"""The scraper watchdog: a stalled run is killed, a slow but working one isn't.

test_scrapers.py switches the watchdog off so its runs don't leave sleeping
threads behind; here it runs for real, with the timings shrunk to milliseconds.
"""

import threading
import time

import pytest

from routers import scrapers as sc


@pytest.fixture(autouse=True)
def fast_watchdog(monkeypatch):
    monkeypatch.setattr(sc, "WATCHDOG_POLL_SECONDS", 0.005)
    monkeypatch.setattr(sc, "SCRAPER_IDLE_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setattr(sc, "SCRAPER_MAX_RUNTIME_SECONDS", 60)


def run_state():
    return {"running": True, "messages": []}


def watch(state, **kwargs):
    killed = threading.Event()
    sc.start_watchdog(state, "test scraper", killed.set, **kwargs)
    return killed


def test_a_run_that_stops_logging_is_killed_and_the_kill_is_logged(caplog):
    killed = watch(run_state())

    assert killed.wait(2)
    # Otherwise the kill only shows in the SSE log, not the API's own logs.
    assert "watchdog killed test scraper" in caplog.text


def test_a_slow_run_that_keeps_logging_is_left_alone():
    state = run_state()
    killed = watch(state)

    # Log a line well inside every idle window for over twice the timeout.
    end = time.monotonic() + 0.3
    while time.monotonic() < end:
        state["messages"].append("still going")
        time.sleep(0.02)

    assert not killed.is_set()
    state["running"] = False


def test_a_run_that_logs_forever_is_still_killed_at_the_runtime_cap(monkeypatch):
    # A scrape stuck in a loop that logs keeps beating the idle check.
    monkeypatch.setattr(sc, "SCRAPER_MAX_RUNTIME_SECONDS", 0.15)
    state = run_state()
    killed = watch(state)

    end = time.monotonic() + 2
    while not killed.is_set() and time.monotonic() < end:
        state["messages"].append("looping")
        time.sleep(0.01)

    assert killed.is_set()


def test_a_finished_run_is_never_killed():
    state = run_state()
    killed = watch(state)

    state["running"] = False

    assert not killed.wait(0.3)


def test_a_previous_runs_watchdog_never_kills_the_current_run():
    # A new run of the same scraper reuses the slot; the old watchdog must stand down.
    current = {"run": 1}
    killed = watch(run_state(), is_current=lambda: current["run"] == 1)

    current["run"] = 2

    assert not killed.wait(0.3)
