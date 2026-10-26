"""Timestamps are stored without a zone (no column is timezone-aware), and the
sites read a zoneless ISO string as the browser's time, which is UK time. So
every timestamp the API writes has to be UK local time: the image runs in
Europe/London, and nothing writes UTC alongside it.
"""

import re
from pathlib import Path

APP = Path(__file__).parent
DOCKERFILE = APP.parent / "Dockerfile"


def test_the_image_runs_in_uk_time_with_the_zone_data_installed():
    text = DOCKERFILE.read_text()
    assert re.search(r"^\s*TZ=Europe/London\b", text, re.M), "Dockerfile must set TZ=Europe/London"
    # Without the system zone files glibc ignores TZ and silently stays on UTC.
    assert re.search(r"\btzdata\b", text), "Dockerfile must install tzdata"


def test_nothing_writes_utc_next_to_local_time():
    # One utcnow() among the now()s stores that row an hour off all summer.
    offenders = [
        f"{path.relative_to(APP)}:{n}"
        for path in APP.rglob("*.py")
        if "venv" not in path.parts and path.name != Path(__file__).name
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if "utcnow(" in line
    ]
    assert offenders == []
