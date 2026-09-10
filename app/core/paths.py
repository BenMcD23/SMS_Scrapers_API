"""Filesystem locations for bundled assets.

Everything the API ships alongside its code (PDF marking sheets, Word/Excel
templates, staff signatures, figures) lives under ``app/assets``. Resolve
paths through here rather than walking ``__file__`` in each module, so moving
the assets directory is a one-line change.
"""

from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = APP_DIR / "assets"
ASSESSMENT_SHEETS_DIR = ASSETS_DIR / "assessment_sheets"
SIGNATURES_DIR = ASSETS_DIR / "signatures"
TEMPLATES_DIR = ASSETS_DIR / "templates"
