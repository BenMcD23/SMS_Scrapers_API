"""Backup orchestration with Drive, pg_dump and psql faked out — the real
round trip needs a Postgres, which the leader-lock test already gates on."""

import gzip
import os

import pytest

import scripts.db_backup as bk


class FakeDrive:
    def __init__(self, pages):
        self.pages = list(pages)
        self.deleted, self.created = [], []

    def files(self):
        return self

    def list(self, **kw):
        self._next = self.pages.pop(0)
        return self

    def create(self, body, media_body, fields, supportsAllDrives):
        self.created.append(body)
        self._next = {"id": "new", "name": body["name"], "size": "123", "createdTime": "t"}
        return self

    def delete(self, fileId, supportsAllDrives):
        self.deleted.append(fileId)
        self._next = {}
        return self

    def execute(self):
        return self._next


@pytest.fixture
def drive(monkeypatch):
    holder = {}
    monkeypatch.setattr(bk, "_drive_client", lambda: holder["drive"])
    monkeypatch.setattr(bk, "DB_BACKUP_DRIVE_FOLDER_ID", "folder")
    return holder


@pytest.mark.parametrize("raw,name,expected", [
    ("postgresql+psycopg2://u:p@h:5432/sms", None, "postgresql://u:p@h:5432/sms"),
    ("postgresql://u:p@h/sms", "postgres", "postgresql://u:p@h/postgres"),
    ("postgresql://u:p@h/sms", "sms_preview_1", "postgresql://u:p@h/sms_preview_1"),
])
def test_pg_url(monkeypatch, raw, name, expected):
    monkeypatch.setenv("DATABASE_URL", raw)
    assert bk._pg_url(name) == expected


def test_pg_url_requires_a_database(monkeypatch):
    monkeypatch.delenv("DATABASE_URL")
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        bk._pg_url()


def test_current_dbname(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://h/sms")
    assert bk._current_dbname() == "sms"
    monkeypatch.setenv("DATABASE_URL", "postgresql://h")
    assert bk._current_dbname() == "postgres"


def test_list_backups_follows_pages(drive):
    drive["drive"] = FakeDrive([
        {"files": [{"id": "a", "name": "A", "size": "10", "createdTime": "2"}], "nextPageToken": "p2"},
        {"files": [{"id": "b", "name": "B", "createdTime": "1"}]},
    ])
    assert bk.list_backups() == [
        {"id": "a", "name": "A", "size": 10, "created_at": "2"},
        {"id": "b", "name": "B", "size": None, "created_at": "1"},
    ]


def test_list_backups_needs_a_folder(monkeypatch):
    monkeypatch.setattr(bk, "DB_BACKUP_DRIVE_FOLDER_ID", "")
    with pytest.raises(RuntimeError, match="not configured"):
        bk.list_backups()
    with pytest.raises(RuntimeError, match="not configured"):
        bk.run_db_backup()


def test_run_backup_uploads_then_prunes_beyond_retention(drive, monkeypatch, tmp_path):
    dumped = []
    uploaded = {}

    def dump(path):
        dumped.append(path)
        with gzip.open(path, "wb") as f:
            f.write(b"INSERT INTO \"Cadet_Medical\" VALUES ('Peanuts');")

    def media(path, mimetype, resumable):
        # Read at upload time: the temp dir is gone once run_db_backup returns.
        uploaded["bytes"] = open(path, "rb").read()
        return path

    monkeypatch.setattr(bk, "_dump_to_file", dump)
    monkeypatch.setattr(bk, "MediaFileUpload", media)
    monkeypatch.setattr(bk, "DB_BACKUP_RETENTION", 2)
    monkeypatch.setenv("BACKUP_ENV", "dev")
    files = [{"id": f"f{i}", "name": f"F{i}"} for i in range(4)]
    drive["drive"] = FakeDrive([{"files": files}])

    res = bk.run_db_backup()
    assert res == {"id": "new", "name": res["name"], "size": 123, "created_at": "t"}
    assert res["name"].startswith("317_SMS_dev_") and res["name"].endswith(".sql.gz.enc")
    # Drive only ever gets ciphertext: neither the SQL nor the gzip wrapper shows.
    assert b"Peanuts" not in uploaded["bytes"] and not uploaded["bytes"].startswith(b"\x1f\x8b")
    assert drive["drive"].created[0]["parents"] == ["folder"]
    assert drive["drive"].deleted == ["f2", "f3"]
    # The temporary dump is cleaned up.
    assert not os.path.exists(os.path.dirname(dumped[0]))


def test_failed_dump_uploads_nothing_and_cleans_up(drive, monkeypatch):
    seen = []

    def fail(path):
        seen.append(path)
        raise RuntimeError("pg_dump failed: auth")

    monkeypatch.setattr(bk, "_dump_to_file", fail)
    drive["drive"] = FakeDrive([])
    with pytest.raises(RuntimeError, match="pg_dump failed"):
        bk.run_db_backup()
    assert drive["drive"].created == [] and not os.path.exists(os.path.dirname(seen[0]))


def test_restore_downloads_then_runs_psql(monkeypatch):
    ran = []
    monkeypatch.setenv("DATABASE_URL", "postgresql://h/sms")
    monkeypatch.setattr(bk, "_download_backup", lambda fid, path: (_gzip_dump(path), "317_SMS_prod_x.sql.gz")[1])
    monkeypatch.setattr(bk, "_run_psql", lambda url, path: ran.append(url))
    assert bk.restore_backup("file-1") == {"restored": "317_SMS_prod_x.sql.gz"}
    assert ran == ["postgresql://h/sms"]


def test_run_psql_reports_failures(monkeypatch, tmp_path):
    dump = tmp_path / "d.sql.gz"
    with gzip.open(dump, "wb") as f:
        f.write(b"SELECT 1;")

    class Proc:
        def __init__(self, cmd, **kw):
            self.cmd = cmd
            import io
            self.stdin = io.BytesIO()
            self.stdin.close = lambda: None
            self.stdout = io.BytesIO(b"ERROR: relation exists")
            self.returncode = 3

        def wait(self):
            return self.returncode

    procs = []
    monkeypatch.setattr(bk.subprocess, "Popen", lambda cmd, **kw: procs.append(Proc(cmd)) or procs[-1])
    with pytest.raises(RuntimeError, match="relation exists"):
        bk._run_psql("postgresql://h/x", str(dump))
    assert "--quiet" in procs[0].cmd and "--single-transaction" in procs[0].cmd
    assert procs[0].stdin.getvalue() == b"SELECT 1;"


def test_preview_diffs_counts_and_always_drops_the_scratch_db(monkeypatch):
    executed = []

    class Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def execute(self, sql):
            executed.append(sql)

    class Conn:
        autocommit = False

        def cursor(self):
            return Cur()

        def close(self):
            executed.append("closed")

    monkeypatch.setenv("DATABASE_URL", "postgresql://h/sms")
    monkeypatch.setattr(bk.psycopg2, "connect", lambda url: Conn())
    monkeypatch.setattr(bk, "_download_backup", lambda fid, path: (_gzip_dump(path), "b.sql.gz")[1])
    monkeypatch.setattr(bk, "_run_psql", lambda url, path: None)
    counts = {"postgresql://h/sms": {"Cadets": 10, "Gone": 1}}
    monkeypatch.setattr(bk, "_table_counts", lambda url: counts.get(url, {"Cadets": 8, "New": 2}))

    res = bk.preview_backup("f")
    assert res["file"] == "b.sql.gz" and res["current_db"] == "sms"
    assert res["tables"] == [
        {"table": "Cadets", "current_rows": 10, "backup_rows": 8, "delta": -2},
        {"table": "Gone", "current_rows": 1, "backup_rows": None, "delta": -1},
        {"table": "New", "current_rows": None, "backup_rows": 2, "delta": 2},
    ]
    assert any(s.startswith("CREATE DATABASE") for s in executed)
    assert any(s.startswith("DROP DATABASE") for s in executed)

    executed.clear()

    def bad_restore(url, path):
        raise RuntimeError("psql restore failed")

    monkeypatch.setattr(bk, "_run_psql", bad_restore)
    with pytest.raises(RuntimeError):
        bk.preview_backup("f")
    assert any(s.startswith("DROP DATABASE") for s in executed) and executed[-1] == "closed"


# ── encryption ────────────────────────────────────────────────────────────────

def _roundtrip(tmp_path, data: bytes) -> bytes:
    src, enc, out = tmp_path / "in", tmp_path / "in.enc", tmp_path / "out"
    src.write_bytes(data)
    bk.encrypt_file(str(src), str(enc))
    bk.decrypt_file(str(enc), str(out))
    return out.read_bytes()


@pytest.mark.parametrize("size", [0, 1, bk._CHUNK - 1, bk._CHUNK, bk._CHUNK + 1, 3 * bk._CHUNK])
def test_encrypt_then_decrypt_gives_back_the_same_bytes(tmp_path, size):
    # Sizes either side of a chunk boundary, since that's where an off-by-one
    # would drop or duplicate the tail of a dump.
    data = os.urandom(size)
    assert _roundtrip(tmp_path, data) == data


def test_the_same_dump_encrypts_differently_each_time(tmp_path):
    src = tmp_path / "in"
    src.write_bytes(b"same")
    bk.encrypt_file(str(src), str(tmp_path / "a"))
    bk.encrypt_file(str(src), str(tmp_path / "b"))
    assert (tmp_path / "a").read_bytes() != (tmp_path / "b").read_bytes()


def _encrypted(tmp_path, size=2 * bk._CHUNK + 10) -> bytes:
    src, enc = tmp_path / "in", tmp_path / "in.enc"
    src.write_bytes(os.urandom(size))
    bk.encrypt_file(str(src), str(enc))
    return enc.read_bytes()


def _decrypt_bytes(tmp_path, data: bytes):
    bad = tmp_path / "bad.enc"
    bad.write_bytes(data)
    bk.decrypt_file(str(bad), str(tmp_path / "out"))


def test_a_flipped_byte_fails_to_decrypt(tmp_path):
    data = bytearray(_encrypted(tmp_path))
    data[len(bk._MAGIC) + 8 + 5 + 100] ^= 1
    with pytest.raises(RuntimeError, match="could not be decrypted"):
        _decrypt_bytes(tmp_path, bytes(data))


def test_a_truncated_download_fails_rather_than_restoring_part_of_the_database(tmp_path):
    data = _encrypted(tmp_path)
    first_chunk_end = len(bk._MAGIC) + 8 + 5 + bk._CHUNK + 16
    # Cut at a clean chunk boundary: every remaining chunk is valid, only the
    # final-flagged one is missing.
    with pytest.raises(RuntimeError, match="truncated"):
        _decrypt_bytes(tmp_path, data[:first_chunk_end])
    with pytest.raises(RuntimeError, match="truncated"):
        _decrypt_bytes(tmp_path, data[:-3])


def test_reordered_chunks_fail_to_decrypt(tmp_path):
    data = _encrypted(tmp_path)
    head = len(bk._MAGIC) + 8
    step = 5 + bk._CHUNK + 16
    first, second = data[head:head + step], data[head + step:head + 2 * step]
    swapped = data[:head] + second + first + data[head + 2 * step:]
    with pytest.raises(RuntimeError, match="could not be decrypted"):
        _decrypt_bytes(tmp_path, swapped)


def test_data_after_the_final_chunk_is_rejected(tmp_path):
    with pytest.raises(RuntimeError, match="after the end"):
        _decrypt_bytes(tmp_path, _encrypted(tmp_path, size=10) + b"x")


def test_a_different_encryption_key_cannot_read_the_backup(tmp_path, monkeypatch):
    data = _encrypted(tmp_path, size=10)
    monkeypatch.setenv("ENCRYPTION_KEY", "yzM7cRZtwFXy5u-1MvX1uFkV0NqQ9Pp8Jz2XQ3cXk1Q=")
    with pytest.raises(RuntimeError, match="wrong ENCRYPTION_KEY"):
        _decrypt_bytes(tmp_path, data)


def test_a_file_that_is_not_a_backup_is_rejected(tmp_path):
    with pytest.raises(RuntimeError, match="not an encrypted"):
        _decrypt_bytes(tmp_path, b"hello world, not a backup")


def test_no_encryption_key_means_no_backup_is_uploaded(drive, monkeypatch):
    monkeypatch.delenv("ENCRYPTION_KEY")
    monkeypatch.setattr(bk, "_dump_to_file", lambda path: open(path, "wb").close())
    drive["drive"] = FakeDrive([])
    with pytest.raises(RuntimeError, match="ENCRYPTION_KEY"):
        bk.run_db_backup()
    assert drive["drive"].created == []


def _gzip_dump(path, sql=b"SELECT 1;"):
    with gzip.open(path, "wb") as f:
        f.write(sql)


def test_restore_decrypts_an_encrypted_backup_before_psql(monkeypatch, tmp_path):
    plain = tmp_path / "p.sql.gz"
    _gzip_dump(plain, b"SELECT 'restored';")
    enc = tmp_path / "p.enc"
    bk.encrypt_file(str(plain), str(enc))

    def download(fid, path):
        open(path, "wb").write(enc.read_bytes())
        return "317_SMS_prod_x.sql.gz.enc"

    fed = []
    monkeypatch.setenv("DATABASE_URL", "postgresql://h/sms")
    monkeypatch.setattr(bk, "_download_backup", download)
    monkeypatch.setattr(bk, "_run_psql", lambda url, path: fed.append(gzip.open(path).read()))
    assert bk.restore_backup("f") == {"restored": "317_SMS_prod_x.sql.gz.enc"}
    assert fed == [b"SELECT 'restored';"]


def test_restore_still_reads_the_plain_backups_from_before_encryption(monkeypatch):
    # The 14 days of unencrypted backups already on Drive stay restorable
    # until retention prunes them.
    def download(fid, path):
        _gzip_dump(path, b"SELECT 'old';")
        return "317_SMS_prod_old.sql.gz"

    fed = []
    monkeypatch.setenv("DATABASE_URL", "postgresql://h/sms")
    monkeypatch.setattr(bk, "_download_backup", download)
    monkeypatch.setattr(bk, "_run_psql", lambda url, path: fed.append(gzip.open(path).read()))
    bk.restore_backup("f")
    assert fed == [b"SELECT 'old';"]


def test_a_backup_that_wont_decrypt_never_reaches_the_live_database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://h/sms")
    monkeypatch.setattr(
        bk, "_download_backup",
        lambda fid, path: (open(path, "wb").write(bk._MAGIC + b"\0" * 8 + b"\x01\0\0\0\x20" + b"\0" * 32), "x")[1],
    )
    ran = []
    monkeypatch.setattr(bk, "_run_psql", lambda url, path: ran.append(url))
    with pytest.raises(RuntimeError, match="could not be decrypted"):
        bk.restore_backup("f")
    assert ran == []


def test_command_line_decrypt(tmp_path, capsys):
    src, enc, out = tmp_path / "a", tmp_path / "a.enc", tmp_path / "a.out"
    src.write_bytes(b"dump")
    bk.encrypt_file(str(src), str(enc))
    assert bk.main(["decrypt", str(enc), str(out)]) == 0
    assert out.read_bytes() == b"dump"
    assert bk.main(["oops"]) == 2
    assert "usage" in capsys.readouterr().err
