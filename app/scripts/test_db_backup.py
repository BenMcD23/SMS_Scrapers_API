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
    monkeypatch.setattr(bk, "_dump_to_file", lambda path: (dumped.append(path), open(path, "wb").close()))
    monkeypatch.setattr(bk, "MediaFileUpload", lambda path, mimetype, resumable: path)
    monkeypatch.setattr(bk, "DB_BACKUP_RETENTION", 2)
    monkeypatch.setenv("BACKUP_ENV", "dev")
    files = [{"id": f"f{i}", "name": f"F{i}"} for i in range(4)]
    drive["drive"] = FakeDrive([{"files": files}])

    res = bk.run_db_backup()
    assert res == {"id": "new", "name": res["name"], "size": 123, "created_at": "t"}
    assert res["name"].startswith("317_SMS_dev_") and res["name"].endswith(".sql.gz")
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
    monkeypatch.setattr(bk, "_download_backup", lambda fid, path: "317_SMS_prod_x.sql.gz")
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
    monkeypatch.setattr(bk, "_download_backup", lambda fid, path: "b.sql.gz")
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
