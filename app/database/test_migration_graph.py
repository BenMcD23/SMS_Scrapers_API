"""The migration history is one line the deploy's `alembic upgrade head` can follow."""

import re
from collections import Counter
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

HERE = Path(__file__).parent
VERSIONS = HERE / "alembic/versions"


def test_every_revision_id_is_unique():
    # Alembic only warns on a reused id and silently drops one of the files,
    # so this broke the prod migrate job (v1.0.642) without failing CI.
    ids = [
        re.search(r"^revision\s*(?::[^=]*)?=\s*['\"](\w+)['\"]", f.read_text(), re.M).group(1)
        for f in VERSIONS.glob("*.py")
    ]
    assert [i for i, n in Counter(ids).items() if n > 1] == []


def test_there_is_a_single_head():
    config = Config(str(HERE / "alembic.ini"))
    config.set_main_option("script_location", str(HERE / "alembic"))
    assert len(ScriptDirectory.from_config(config).get_heads()) == 1
