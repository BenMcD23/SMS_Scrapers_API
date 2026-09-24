"""Two processes, one leader lock: the second only runs jobs once the first dies.

Needs a real Postgres (advisory locks), so it skips unless TEST_POSTGRES_URL
is set, e.g. through a port-forward to the dev database:

    kubectl -n sms-dev port-forward svc/sms-db-rw 5433:5432 &
    TEST_POSTGRES_URL=postgresql://app:<password>@localhost:5433/app pytest app/core/test_leader.py
"""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

URL = os.environ.get("TEST_POSTGRES_URL")

# A process running core.leader with a scheduler that just reports what it's told.
CHILD = """
import sys, time
from core import leader
class Fake:
    def start(self, paused=False): print("PAUSED" if paused else "RESUMED", flush=True)
    def resume(self): print("RESUMED", flush=True)
    def pause(self): print("PAUSED", flush=True)
leader.POLL_SECONDS = 1
leader.start(Fake(), on_acquire=lambda: print("ACQUIRED", flush=True))
time.sleep(60)
"""


def _spawn():
    env = {**os.environ, "DATABASE_URL": URL, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    proc = subprocess.Popen([sys.executable, "-c", CHILD], env=env, stdout=subprocess.PIPE, text=True)
    proc.lines = []
    threading.Thread(target=lambda: proc.lines.extend(iter(proc.stdout.readline, "")), daemon=True).start()
    return proc


def _wait_for(proc, word, seconds, after=0):
    """True once `word` appears in the process's output (past line `after`)."""
    end = time.time() + seconds
    while time.time() < end:
        if any(word in line for line in proc.lines[after:]):
            return True
        time.sleep(0.2)
    return False


@pytest.mark.skipif(not URL, reason="needs TEST_POSTGRES_URL (a real Postgres)")
def test_only_one_process_runs_jobs():
    a = _spawn()
    b = None
    try:
        assert _wait_for(a, "RESUMED", 15), "first process never took the lock"
        b = _spawn()
        assert _wait_for(b, "PAUSED", 15)
        assert not _wait_for(b, "RESUMED", 5), "second process ran jobs while the first held the lock"
        a.kill()  # no clean shutdown: Postgres must release the lock with the connection
        assert _wait_for(b, "ACQUIRED", 15), "second process didn't take over"
        assert _wait_for(b, "RESUMED", 5, after=1)
    finally:
        for p in (a, b):
            if p:
                p.kill()
