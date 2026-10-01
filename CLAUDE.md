# SMS Scrapers API conventions

FastAPI + SQLAlchemy backend for the 317 SMS site and the cadet portal. See the
README for environments and deploys; this file is how to work in the code.

## Layout

`app/` is the import root (`pytest.ini` sets `pythonpath = app`): `routers/` are
the endpoints, `core/` shared helpers, `scripts/` the Bader scrapers and jobs,
`texts/` parade-night texts, `assessment_builders/` and `form_generators/` the
PDF/Word/Excel output, `database/` models and Alembic migrations.

Schema changes go through an Alembic migration (`app/database/alembic/versions`),
never `create_all`.

## Tests are part of every change

Every change ships with tests — a new endpoint with tests for what it does, a
bug fix with a test that fails without the fix (run it against the old code once
to prove it). Test the unhappy paths as hard as the happy one:

- **Every role.** No token is 401; each role that mustn't get in is 403; each
  role that should, does. Parametrise over personas rather than testing one.
- **Every rejection.** Validation (422), bad input (400), missing rows (404),
  wrong state (409), someone else's record (403/404).
- **Outside failures.** Google, Gmail, Notify, the LLM chain, GitHub and Bader
  timing out or erroring — the code should degrade the way its docstring says.
- **Data at the edges.** Empty lists, `None` columns, a scrape that returned
  nothing (it must never wipe existing data), duplicate rows, non-ASCII names.

**Running them.** `pytest` from the repo root (no env vars needed — `conftest.py`
sets safe defaults), `ruff check app` for lint. CI runs both. `pytest -k name`
for one test; `pip install pytest-cov && pytest --cov=app` for coverage.

**Where tests live.** Next to the module, named `test_<thing>.py`:
`routers/stores.py` → `routers/test_stores.py`. HTTP-level tests for a router
whose file already holds unit tests go in `test_<thing>_api.py`.

**Use the shared fixtures in `app/conftest.py`** instead of building your own:

- `api` — a `TestClient` on a fresh in-memory database. Send
  `headers=api.as_("staff")` (or `"snco"`, `"nco"`, `"cadet"`, `"owner"`, `"oc"`,
  …). Tokens are faked but the real `require_*` role checks still run, so access
  rules are genuinely tested.
- `db` — a session on that same database, for seeding rows and checking results.
- `outbox` — autouse; every email the code tries to send lands here instead of
  Gmail. Assert on recipients and content.
- `http` / `github` — fake outbound `httpx` calls and a fake GitHub
  contents/git-data API for the publishing endpoints.
- `real_send_email` — the genuine `send_email`, for testing the emailer itself.

**Rules.**

- Never touch the network, Google or a real database. Patch with pytest's
  `monkeypatch` — never assign to a module attribute directly; that leaks into
  every later test.
- Background work (`SessionLocal()`, `Session(engine)`) is already pointed at
  the test database by the fixtures; scraper tests fake the Playwright page
  readers (`scripts/test_scraper_db_sync.py` shows how).
- Name tests after the behaviour ("a failed event save keeps the previous
  events"), and say *why* in a comment when a case isn't obvious.
- Keep a module's self-checks in pytest, not in an `if __name__ == "__main__"`
  block — CI never runs those.
- The frontends pin some of the same numbers (assessment pass marks, text
  limits); change them together.
