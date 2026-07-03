"""PostgreSQL backups to Google Drive, plus restore/preview helpers.

A scheduled job (registered in api.py) runs `pg_dump` of the 317_SMS database,
gzips it, and uploads it to a Shared Drive folder using the existing Google
service-account credentials. The owner-only /backups endpoints reuse the same
helpers to list, restore and preview those backups.

Dumps are plain SQL with `--clean --if-exists` so any backup can be restored
straight over the live database with psql.
"""

import gzip
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import psycopg2
from googleapiclient.discovery import build as google_build
from googleapiclient.http import MediaFileUpload

from core.config import (
    DB_BACKUP_DRIVE_FOLDER_ID,
    DB_BACKUP_RETENTION,
)
from core.security import _service_account_creds

# Drive scope to list, upload, download and delete backups (and see manually
# uploaded copies) on the Shared Drive. The service account uses its OWN
# identity here (no domain-wide delegation) — it must be added as a member of
# the backup Shared Drive. That bounds this scope to that one Shared Drive
# instead of the impersonated admin's entire Drive, so `drive` no longer needs
# to be granted in the Workspace DWD config.
_DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]

_NAME_PREFIX = "317_SMS"
_GZIP_MIME = "application/gzip"


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
            print(f"[db_backup] pruned old backup {f['name']}", flush=True)
        except Exception as e:  # pragma: no cover - best effort
            print(f"[db_backup] failed to prune {f['name']}: {e}", flush=True)


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
    name = f"{_NAME_PREFIX}_{env}_{stamp}.sql.gz"

    tmp_dir = tempfile.mkdtemp(prefix="dbbackup_")
    local_path = os.path.join(tmp_dir, name)
    try:
        print(f"[db_backup] dumping database -> {name}", flush=True)
        _dump_to_file(local_path)

        drive = _drive_client()
        media = MediaFileUpload(local_path, mimetype=_GZIP_MIME, resumable=True)
        created = drive.files().create(
            body={"name": name, "parents": [DB_BACKUP_DRIVE_FOLDER_ID]},
            media_body=media,
            fields="id, name, size, createdTime",
            supportsAllDrives=True,
        ).execute()
        print(f"[db_backup] uploaded {name} ({created.get('size')} bytes)", flush=True)

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
    """Restore a gzipped SQL dump into `url` in a single transaction."""
    proc = subprocess.Popen(
        ["psql", "-v", "ON_ERROR_STOP=1", "--single-transaction", "-d", url],
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
    path = os.path.join(tmp_dir, "backup.sql.gz")
    try:
        name = _download_backup(file_id, path)
        print(f"[db_backup] restoring {name} over live database", flush=True)
        _run_psql(_pg_url(), path)
        print(f"[db_backup] restore of {name} complete", flush=True)
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
    path = os.path.join(tmp_dir, "backup.sql.gz")

    admin = psycopg2.connect(admin_url)
    admin.autocommit = True
    try:
        name = _download_backup(file_id, path)
        with admin.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{scratch}"')
        try:
            _run_psql(_pg_url(scratch), path)
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
