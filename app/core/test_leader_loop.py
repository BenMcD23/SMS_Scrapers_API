"""The scheduler leader lock loop, against a fake Postgres connection.

The real loop runs on its thread, so these check what each process does as
the lock is won, lost and contended: the rolling-deploy guarantee the module
docstring makes. test_leader.py checks the same against a real Postgres,
when one is available; this runs everywhere, CI included.
"""

import threading
import time

import pytest

from core import leader


class FakeScheduler:
    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()

    def _record(self, name):
        with self.lock:
            self.calls.append(name)

    def start(self, paused=False):
        self._record("start(paused)" if paused else "start")

    def resume(self):
        self._record("resume")

    def pause(self):
        self._record("pause")

    def count(self, name):
        with self.lock:
            return self.calls.count(name)


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar(self):
        return self.value


class FakeConn:
    def __init__(self, db):
        self.db = db
        self.closed = False
        self.invalidated = False

    def execution_options(self, **opts):
        assert opts == {"isolation_level": "AUTOCOMMIT"}
        return self

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if self.db.fail_next:
            self.db.fail_next -= 1
            raise ConnectionError("server closed the connection unexpectedly")
        if "pg_try_advisory_lock" in sql:
            assert params == {"id": leader.LOCK_ID}
            self.db.lock_attempts += 1
            return FakeResult(self.db.lock_free)
        assert sql == "SELECT 1"
        self.db.pings += 1
        return FakeResult(1)

    def invalidate(self):
        self.invalidated = True

    def close(self):
        self.closed = True


class FakeDb:
    """Stands in for the engine leader.py creates, and the Postgres behind it."""

    def __init__(self):
        self.lock_free = True
        self.fail_next = 0
        self.connect_fails = 0
        self.lock_attempts = 0
        self.pings = 0
        self.conns = []
        self.disposed = False
        self.engine_kwargs = None

    def create_engine(self, url, **kwargs):
        self.engine_kwargs = kwargs
        return self

    def connect(self):
        if self.connect_fails:
            self.connect_fails -= 1
            raise ConnectionError("could not connect to server")
        conn = FakeConn(self)
        self.conns.append(conn)
        return conn

    def dispose(self):
        self.disposed = True


def wait_for(condition, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.005)
    raise AssertionError("condition never became true")


@pytest.fixture
def db(monkeypatch):
    fake = FakeDb()
    monkeypatch.setattr(leader, "DATABASE_URL", "postgresql://u:p@db/sms")
    monkeypatch.setattr(leader, "create_engine", fake.create_engine)
    monkeypatch.setattr(leader, "POLL_SECONDS", 0.01)
    yield fake
    # Never leave a loop thread running into the next test.
    leader.stop()
    monkeypatch.setattr(leader, "_thread", None)


def test_without_postgres_the_scheduler_just_runs(monkeypatch):
    # SQLite has no advisory locks; the only process runs the jobs itself.
    monkeypatch.setattr(leader, "DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setattr(leader, "_thread", None)
    sched = FakeScheduler()

    leader.start(sched, on_acquire=lambda: pytest.fail("no lock to acquire"))

    assert sched.calls == ["start"]
    assert leader._thread is None


def test_the_process_that_takes_the_lock_rereads_state_then_runs_jobs(db):
    sched = FakeScheduler()
    order = []
    leader.start(sched, on_acquire=lambda: order.append(list(sched.calls)))

    wait_for(lambda: sched.count("resume") == 1)
    # Started paused, and on_acquire ran before resuming, so jobs never run
    # on state another process may have changed.
    assert sched.calls[0] == "start(paused)"
    assert order == [["start(paused)"]]


def test_a_leader_keeps_its_lock_by_pinging_not_by_retaking_it(db):
    sched = FakeScheduler()
    leader.start(sched, on_acquire=lambda: None)

    wait_for(lambda: db.pings >= 3)
    assert db.lock_attempts == 1
    assert sched.count("resume") == 1
    assert sched.count("pause") == 0


def test_a_process_that_cant_get_the_lock_keeps_its_jobs_paused_and_retries(db):
    # The old pod still holds it during a rolling deploy.
    db.lock_free = False
    sched = FakeScheduler()
    leader.start(sched, on_acquire=lambda: pytest.fail("never the leader"))

    wait_for(lambda: db.lock_attempts >= 3)
    assert sched.calls == ["start(paused)"]


def test_the_waiting_process_takes_over_when_the_old_leader_lets_go(db):
    db.lock_free = False
    sched = FakeScheduler()
    acquired = []
    leader.start(sched, on_acquire=lambda: acquired.append(1))
    wait_for(lambda: db.lock_attempts >= 2)

    db.lock_free = True  # the old pod exited and its connection closed

    wait_for(lambda: sched.count("resume") == 1)
    assert acquired == [1]


def test_losing_the_connection_pauses_jobs_at_once_then_takes_the_lock_back(db):
    sched = FakeScheduler()
    acquired = []
    leader.start(sched, on_acquire=lambda: acquired.append(1))
    wait_for(lambda: sched.count("resume") == 1)

    db.fail_next = 1  # the next ping finds the connection dead

    wait_for(lambda: sched.count("resume") == 2)
    # Paused before anything else could run on a lock Postgres has dropped,
    # and the stale connection thrown away rather than reused.
    assert sched.calls == ["start(paused)", "resume", "pause", "resume"]
    assert db.conns[0].invalidated
    assert len(db.conns) == 2
    # Re-reads state on every new term as leader, not only the first.
    assert acquired == [1, 1]


def test_a_database_that_is_down_at_start_is_retried_until_it_answers(db):
    db.connect_fails = 3
    sched = FakeScheduler()
    leader.start(sched, on_acquire=lambda: None)

    wait_for(lambda: sched.count("resume") == 1)
    assert sched.count("pause") == 0  # was never leader, so nothing to pause


def test_a_failing_on_acquire_leaves_the_jobs_paused_and_tries_again(db):
    # If re-reading state fails, running jobs on stale state would be worse
    # than waiting: stay paused, drop the connection (and so the lock), retry.
    sched = FakeScheduler()
    attempts = []

    def on_acquire():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("couldn't load the schedule")

    leader.start(sched, on_acquire=on_acquire)

    wait_for(lambda: sched.count("resume") == 1)
    assert len(attempts) == 2
    assert db.conns[0].invalidated
    assert sched.count("pause") == 0


def test_stopping_the_leader_pauses_its_jobs_and_releases_the_lock(db):
    sched = FakeScheduler()
    leader.start(sched, on_acquire=lambda: None)
    wait_for(lambda: sched.count("resume") == 1)

    leader.stop()

    assert not leader._thread.is_alive()
    assert sched.calls[-1] == "pause"
    # Closing the connection is what releases the advisory lock for the next pod.
    assert db.conns[-1].closed
    assert db.disposed


def test_stopping_a_process_that_never_led_pauses_nothing(db):
    db.lock_free = False
    sched = FakeScheduler()
    leader.start(sched, on_acquire=lambda: None)
    wait_for(lambda: db.lock_attempts >= 1)

    leader.stop()

    assert sched.calls == ["start(paused)"]
    assert db.conns[-1].closed


def test_the_lock_connection_notices_a_silently_dead_peer(db):
    # Keepalives and tcp_user_timeout bound how long a partitioned node can
    # keep believing it is leader; a pool would hand back stale connections.
    leader.start(FakeScheduler(), on_acquire=lambda: None)
    wait_for(lambda: db.engine_kwargs is not None)

    args = db.engine_kwargs["connect_args"]
    assert args["keepalives"] == 1
    assert args["tcp_user_timeout"] <= 30000
    assert db.engine_kwargs["poolclass"].__name__ == "NullPool"
