"""The API must not import Playwright.

This is the invariant the whole cloud/home split rests on: browser automation
runs on the home worker, and the Lambda image (Dockerfile.api) ships without
Chromium or any of its OS libraries. An innocent-looking `from
scripts.scraper_calls import ...` in a router would import playwright at
startup — which is a crash in the deployed image, not a slow build, and only
at runtime.

Checked in a subprocess because by the time the rest of the suite has run,
sys.modules has plenty in it that the API alone would never load.
"""
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_PROBE = """
import sys
import lambda_handler  # the Lambda entrypoint — the whole cloud import graph
banned = sorted({m.split(".")[0] for m in sys.modules} & {"playwright", "psutil"})
print(",".join(banned))
"""


def _run_probe(script: str) -> str:
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(REPO_ROOT / "app"), str(REPO_ROOT)]),
        "DATABASE_URL": "sqlite:///:memory:",
        "ENCRYPTION_KEY": os.environ.get(
            "ENCRYPTION_KEY", "5-6ZgtVJdBRAJfCPIhLdyUFqGgb0ChZfBGB4fL0jTOo="
        ),
    }
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, cwd=REPO_ROOT, env=env, timeout=180,
    )
    assert result.returncode == 0, f"probe failed:\n{result.stderr}"
    return result.stdout.strip()


def test_the_api_does_not_import_playwright():
    leaked = _run_probe(_PROBE)
    assert leaked == "", (
        f"{leaked} reached the API's import graph. Playwright and the RAM guard "
        "belong to the worker (app/worker.py); the Lambda image doesn't ship a "
        "browser, so this would only fail once deployed."
    )


def test_the_worker_does_import_playwright():
    # The mirror image: if this ever passes empty, the seam has been moved
    # rather than kept, and the test above stops meaning anything.
    loaded = _run_probe(_PROBE.replace("import lambda_handler", "import worker"))
    assert "playwright" in loaded
