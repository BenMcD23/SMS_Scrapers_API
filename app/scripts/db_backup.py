"""PostgreSQL backups to Google Drive, plus restore/preview helpers.

A scheduled job (registered in api.py) runs `pg_dump` of the 317_SMS database,
gzips it, and uploads it to a Shared Drive folder using the existing Google
service-account credentials. The owner-only /backups endpoints reuse the same
helpers to list, restore and preview those backups.

Dumps are plain SQL with `--clean --if-exists` so any backup can be restored
straight over the live database with psql.

Every dump holds the whole database, cadets' medical details included, so it is
encrypted before it leaves the cluster: Drive only ever sees ciphertext. The key
is derived from ENCRYPTION_KEY, so keep a copy of that secret somewhere other
than the cluster, or the backups can't be read when the cluster is what's lost.
To decrypt one by hand: `python -m scripts.db_backup decrypt in.enc out.sql.gz`.
"""

import base64
import gzip
import logging
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import psycopg2
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from googleapiclient.discovery import build as google_build
from googleapiclient.http import MediaFileUpload

from core.config import (
    DB_BACKUP_DRIVE_FOLDER_ID,
    DB_BACKUP_RETENTION,
)
from core.security import _service_account_creds

logger = logging.getLogger(__name__)

# Drive scope to list, upload, download and delete backups (and see manually
# uploaded copies) on the Shared Drive. The service account uses its OWN
# identity here (no domain-wide delegation) — it must be added as a member of
# the backup Shared Drive. That bounds this scope to that one Shared Drive
# instead of the impersonated admin's entire Drive, so `drive` no longer needs
# to be granted in the Workspace DWD config.
_DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]

_NAME_PREFIX = "317_SMS"
_ENC_MIME = "application/octet-stream"


# ── encryption ────────────────────────────────────────────────────────────────
# File layout: MAGIC, an 8-byte random nonce prefix, then chunks of
# [1-byte final flag][4-byte ciphertext length][AES-256-GCM ciphertext]. Each
# chunk's nonce is the prefix plus its index, and the flag is authenticated
# data, so reordered, dropped or truncated chunks all fail to decrypt rather
# than restoring a silently incomplete database. A plain gzip dump from before
# encryption starts with the gzip magic instead, which is how restore tells the
# two apart while those older backups age out.

_MAGIC = b"317SMSBK1"
_CHUNK = 1 << 20
_GZIP_MAGIC = b"\x1f\x8b"


def _backup_key() -> bytes:
    """A key used only for backups, derived from ENCRYPTION_KEY so there is no
    second secret to lose. Missing key is an error, never a plaintext upload."""
    raw = os.environ.get("ENCRYPTION_KEY")
    if not raw:
        raise RuntimeError("ENCRYPTION_KEY is not set; refusing to write an unencrypted backup")
    return HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None, info=b"317-sms-db-backup-v1",
    ).derive(base64.urlsafe_b64decode(raw))


def _nonce(prefix: bytes, index: int) -> bytes:
    return prefix + struct.pack(">I", index)


def encrypt_file(src: str, dst: str) -> None:
    aes = AESGCM(_backup_key())
    prefix = os.urandom(8)
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        fout.write(_MAGIC + prefix)
        index = 0
        chunk = fin.read(_CHUNK)
        while True:
            nxt = fin.read(_CHUNK)
            # Always end on a flagged chunk (empty for an empty file) so a
            # truncated download can't pass as complete.
            flag = b"\x01" if not nxt else b"\x00"
            ct = aes.encrypt(_nonce(prefix, index), chunk, flag)
            fout.write(flag + struct.pack(">I", len(ct)) + ct)
            if not nxt:
                return
            chunk, index = nxt, index + 1


def decrypt_file(src: str, dst: str) -> None:
    aes = AESGCM(_backup_key())
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        if fin.read(len(_MAGIC)) != _MAGIC:
            raise RuntimeError("not an encrypted 317 SMS backup")
        prefix = fin.read(8)
        index = 0
        while True:
            head = fin.read(5)
            if len(head) < 5:
                raise RuntimeError("backup is truncated")
            flag, size = head[:1], struct.unpack(">I", head[1:])[0]
            ct = fin.read(size)
            if len(ct) < size:
                raise RuntimeError("backup is truncated")
            try:
                fout.write(aes.decrypt(_nonce(prefix, index), ct, flag))
            except InvalidTag:
                raise RuntimeError("backup could not be decrypted: wrong ENCRYPTION_KEY or a damaged file") from None
            if flag == b"\x01":
                if fin.read(1):
                    raise RuntimeError("unexpected data after the end of the backup")
                return
            index += 1


def _plain_dump(path: str) -> str:
    """Path to a gzipped SQL dump for `path`, decrypting next to it if needed."""
    with open(path, "rb") as f:
        head = f.read(len(_MAGIC))
    if head.startswith(_GZIP_MAGIC):
        return path
    plain = path + ".sql.gz"
    decrypt_file(path, plain)
    return plain


# ── connection helpers ────────────────────────────────────────────────────────

def _pg_url(dbname: str | None = None) -> str:
    """The DATABASE_URL as a libpq-compatible URL (drops the +psycopg2 driver
    suffix), optionally pointing at a different database on the same server."""
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        raise RuntimeError("DATABASE_URL is not set; backups require PostgreSQL")
    url = raw.replace("postgresql+psycopg2://", "postgresql://", 1)
    if dbname is None:
        return url
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{dbname}", "", ""))


def _current_dbname() -> str:
    return urlsplit(_pg_url()).path.lstrip("/") or "postgres"


# ── Drive helpers ─────────────────────────────────────────────────────────────

def _drive_client():
    creds = _service_account_creds(_DRIVE_SCOPES)
    return google_build("drive", "v3", credentials=creds, cache_discovery=False)


def list_backups() -> list[dict]:
    """Backup files in the configured Drive folder, newest first."""
    if not DB_BACKUP_DRIVE_FOLDER_ID:
        raise RuntimeError("DB_BACKUP_DRIVE_FOLDER_ID is not configured")
    drive = _drive_client()
    files: list[dict] = []
    page_token = None
    while True:
        resp = drive.files().list(
            q=(f"'{DB_BACKUP_DRIVE_FOLDER_ID}' in parents and trashed = false"),
            orderBy="createdTime desc",
            fields="nextPageToken, files(id, name, size, createdTime)",
            pageSize=200,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            pageToken=page_token,
        ).execute()
        files.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return [
        {
            "id": f["id"],
            "name": f["name"],
            "size": int(f["size"]) if f.get("size") else None,
            "created_at": f.get("createdTime"),
        }
        for f in files
    ]


def _download_backup(file_id: str, dest_path: str) -> str:
    """Download a Drive file to dest_path and return the file's name."""
    drive = _drive_client()
    meta = drive.files().get(
        fileId=file_id, fields="name", supportsAllDrives=True
    ).execute()
    request = drive.files().get_media(fileId=file_id, supportsAllDrives=True)
    from googleapiclient.http import MediaIoBaseDownload

    with open(dest_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request, chunksize=4 * 1024 * 1024)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    return meta["name"]


def _enforce_retention(drive) -> None:
    backups = list_backups()
    for f in backups[DB_BACKUP_RETENTION:]:
        try:
            drive.files().delete(fileId=f["id"], supportsAllDrives=True).execute()
            logger.info(f"pruned old backup {f['name']}")
        except Exception as e:  # pragma: no cover - best effort
            logger.error(f"failed to prune {f['name']}: {e}")


# ── dump / upload ─────────────────────────────────────────────────────────────

def _dump_to_file(path: str) -> None:
    """Stream a gzipped plain-SQL pg_dump of the live DB to `path`."""
    cmd = [
        "pg_dump",
        "--clean",
        "--if-exists",
        "--no-owner",
        "--no-privileges",
        _pg_url(),
    ]
    with gzip.open(path, "wb") as out:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert proc.stdout is not None
        for chunk in iter(lambda: proc.stdout.read(1 << 16), b""):
            out.write(chunk)
        _, err = proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"pg_dump failed: {err.decode(errors='replace').strip()}")


def run_db_backup() -> dict:
    """Dump the database, upload it to Drive, and prune old backups.

    Returns the uploaded file's metadata. Safe to call from the scheduler or
    the owner-only /backups/run endpoint.
    """
    if not DB_BACKUP_DRIVE_FOLDER_ID:
        raise RuntimeError("DB_BACKUP_DRIVE_FOLDER_ID is not configured")

    env = os.environ.get("BACKUP_ENV", os.environ.get("ENV", "prod"))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    name = f"{_NAME_PREFIX}_{env}_{stamp}.sql.gz.enc"

    tmp_dir = tempfile.mkdtemp(prefix="dbbackup_")
    dump_path = os.path.join(tmp_dir, "dump.sql.gz")
    local_path = os.path.join(tmp_dir, name)
    try:
        logger.info(f"dumping database -> {name}")
        _dump_to_file(dump_path)
        encrypt_file(dump_path, local_path)
        os.remove(dump_path)

        drive = _drive_client()
        media = MediaFileUpload(local_path, mimetype=_ENC_MIME, resumable=True)
        created = drive.files().create(
            body={"name": name, "parents": [DB_BACKUP_DRIVE_FOLDER_ID]},
            media_body=media,
            fields="id, name, size, createdTime",
            supportsAllDrives=True,
        ).execute()
        logger.info(f"uploaded {name} ({created.get('size')} bytes)")

        _enforce_retention(drive)
        return {
            "id": created["id"],
            "name": created["name"],
            "size": int(created["size"]) if created.get("size") else None,
            "created_at": created.get("createdTime"),
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ── restore / preview ─────────────────────────────────────────────────────────

def _run_psql(url: str, sql_path: str) -> None:
    """Restore a gzipped SQL dump into `url` in a single transaction.

    --quiet matters: stdout is only read once the whole dump has been written,
    so psql echoing a line per statement could fill the pipe and deadlock a
    large restore. Errors still come through."""
    proc = subprocess.Popen(
        ["psql", "--quiet", "-v", "ON_ERROR_STOP=1", "--single-transaction", "-d", url],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert proc.stdin is not None
    with gzip.open(sql_path, "rb") as f:
        shutil.copyfileobj(f, proc.stdin, length=1 << 16)
    proc.stdin.close()
    out = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"psql restore failed: {out.strip()[-2000:]}")


def restore_backup(file_id: str) -> dict:
    """Download `file_id` and restore it over the live database."""
    tmp_dir = tempfile.mkdtemp(prefix="dbrestore_")
    path = os.path.join(tmp_dir, "backup")
    try:
        name = _download_backup(file_id, path)
        # Decrypt before touching the live database, so a bad key or damaged
        # file fails here instead of half-way through a restore.
        plain = _plain_dump(path)
        logger.info(f"restoring {name} over live database")
        _run_psql(_pg_url(), plain)
        logger.info(f"restore of {name} complete")
        return {"restored": name}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _table_counts(url: str) -> dict[str, int]:
    conn = psycopg2.connect(url)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
            )
            tables = [r[0] for r in cur.fetchall()]
            counts: dict[str, int] = {}
            for t in tables:
                cur.execute(f'SELECT count(*) FROM public."{t}"')
                counts[t] = cur.fetchone()[0]
            return counts
    finally:
        conn.close()


def preview_backup(file_id: str) -> dict:
    """Restore a backup into a throwaway database and diff per-table row counts
    against the live database. The scratch DB is always dropped afterwards."""
    scratch = f"sms_preview_{int(time.time())}"
    admin_url = _pg_url("postgres")
    tmp_dir = tempfile.mkdtemp(prefix="dbpreview_")
    path = os.path.join(tmp_dir, "backup")

    admin = psycopg2.connect(admin_url)
    admin.autocommit = True
    try:
        name = _download_backup(file_id, path)
        plain = _plain_dump(path)
        with admin.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{scratch}"')
        try:
            _run_psql(_pg_url(scratch), plain)
            current = _table_counts(_pg_url())
            backup = _table_counts(_pg_url(scratch))
        finally:
            with admin.cursor() as cur:
                cur.execute(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)')

        rows = []
        for table in sorted(set(current) | set(backup)):
            cur_n = current.get(table)
            bak_n = backup.get(table)
            rows.append(
                {
                    "table": table,
                    "current_rows": cur_n,
                    "backup_rows": bak_n,
                    "delta": (bak_n or 0) - (cur_n or 0),
                }
            )
        return {"file": name, "current_db": _current_dbname(), "tables": rows}
    finally:
        admin.close()
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main(argv: list[str]) -> int:
    """Disaster-recovery entry point: decrypt a backup downloaded from Drive
    when the API (and its /backups restore) isn't running to do it."""
    if len(argv) != 3 or argv[0] != "decrypt":
        print("usage: ENCRYPTION_KEY=... python -m scripts.db_backup decrypt <in.enc> <out.sql.gz>",
              file=sys.stderr)
        return 2
    decrypt_file(argv[1], argv[2])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
