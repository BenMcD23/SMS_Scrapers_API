"""The ban-notification ledger must live in the mounted data volume."""

import os

import scripts.get_banned as gb


def test_the_ledger_path_is_absolute_and_not_next_to_the_source():
    # It used to be CWD-relative, which resolved inside the container image and
    # was wiped on every deploy, so people were notified again.
    assert os.path.isabs(gb.NOTIFIED_FILE)
    assert os.path.basename(os.path.dirname(gb.NOTIFIED_FILE)) == "data"
    assert os.path.dirname(gb.NOTIFIED_FILE) != os.path.dirname(gb.__file__)


def test_the_ledger_round_trips_and_creates_its_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(gb, "_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(gb, "NOTIFIED_FILE", str(tmp_path / "data" / "notifications.json"))
    assert gb.load_notified() == set()
    gb.save_notified({"B::event", "A::event"})
    assert gb.load_notified() == {"A::event", "B::event"}
