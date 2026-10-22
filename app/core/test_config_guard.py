"""Startup-time safety rails in core.config and api.

These run in a subprocess because they are about what happens at import time,
from a clean environment — reloading modules in the test process would leak
into every other test.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1]


def _run(code: str, **env) -> subprocess.CompletedProcess:
    base = {k: v for k, v in os.environ.items() if k not in ("ENV", "DEV_FAKE_AUTH")}
    base.update(
        ENCRYPTION_KEY="5-6ZgtVJdBRAJfCPIhLdyUFqGgb0ChZfBGB4fL0jTOo=",
        DATABASE_URL="sqlite:///:memory:",
        SCHEDULER_ENABLED="false",
    )
    base.update(env)
    return subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, {str(APP)!r})\n{code}"], capture_output=True, text=True, env=base, cwd=APP.parent)


@pytest.mark.parametrize("env", [{}, {"ENV": "prod"}, {"ENV": "production"}, {"ENV": "dve"}, {"ENV": ""}])
def test_the_bypass_refuses_to_start_unless_env_is_exactly_dev(env):
    # An unset or misspelt ENV counts as prod, so a config slip fails closed.
    res = _run("import core.config", DEV_FAKE_AUTH="1", **env)
    assert res.returncode != 0 and "Refusing to start" in res.stderr


def test_the_bypass_starts_under_env_dev():
    assert _run("import core.config", DEV_FAKE_AUTH="1", ENV="dev").returncode == 0


def test_a_normal_prod_boot_is_unaffected():
    assert _run("import core.config", ENV="prod").returncode == 0
    assert _run("import core.config").returncode == 0


def test_env_is_case_insensitive():
    assert _run("import core.config", DEV_FAKE_AUTH="1", ENV="DEV").returncode == 0


_DOCS = "import api; print(api.app.docs_url, api.app.redoc_url, api.app.openapi_url)"


@pytest.mark.parametrize("env", [{}, {"ENV": "prod"}, {"ENV": "staging"}])
def test_interactive_docs_are_off_outside_dev(env):
    res = _run(_DOCS, **env)
    assert res.returncode == 0, res.stderr
    assert res.stdout.split()[-3:] == ["None", "None", "None"]


def test_interactive_docs_are_on_in_dev():
    res = _run(_DOCS, ENV="dev")
    assert res.returncode == 0, res.stderr
    assert res.stdout.split()[-3:] == ["/docs", "/redoc", "/openapi.json"]


def test_the_test_client_serves_no_docs_in_prod(api):
    # The suite itself runs as prod (ENV unset), so this is the real default.
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert api.get(path).status_code == 404
    assert api.get("/ping").status_code == 200
