"""Every endpoint the inspection sheet calls admits staff and SNCOs.

The sheet builds its flight tabs from GET /cadets, so when that one endpoint was
staff-only an SNCO saw "No cadets found for this flight" — the page looked
broken rather than forbidden. Asserting the whole set together stops one of them
drifting back out of step with the others.

Rather than reading the route table (FastAPI 0.142 stores included routers as
private lazy wrappers, so there is no stable public way to walk their
dependencies), this calls each endpoint as every role and checks whether the
real guard let it through. A request with no body or params is rejected with a
422 only *after* the role guards have run, so 401/403 means the guard said no.
"""

import pytest

from api import app

ROLES = ["staff", "snco", "nco"]
METHODS = ["get", "post", "put", "patch", "delete"]

# Paths the inspection sheet hits: the roster it renders flights from, the
# absences it prefills, and the submit.
INSPECTION_PATHS = ["/absences", "/cadets", "/inspections"]


def guards(api, path: str) -> set[str]:
    """Roles that get past the role guard on every method `path` declares."""
    declared = app.openapi()["paths"].get(path)
    assert declared, f"{path}: no such endpoint — is the path still right?"
    admitted = set(ROLES)
    for method in METHODS:
        if method not in declared:
            continue
        for role in ROLES:
            resp = api.request(method.upper(), path, headers=api.as_(role))
            if resp.status_code in (401, 403):
                admitted.discard(role)
    return admitted


@pytest.mark.parametrize("path", INSPECTION_PATHS)
@pytest.mark.parametrize("role", ["staff", "snco"])
def test_inspection_endpoint_admits(api, path, role):
    admitted = guards(api, path)
    assert role in admitted, f"{path} shuts out {role}s: admits {admitted}"


def test_cadet_roster_stays_off_ncos(api):
    # The roster is the one that regressed, and it must not swing the other way
    # either — NCOs get /cadets/search, not the full list.
    assert guards(api, "/cadets") == {"staff", "snco"}


def test_admits_table_still_reflects_reality(api):
    # Guards against the probe going blind: a real staff-only endpoint must still
    # read as staff-only.
    assert guards(api, "/users") == {"staff"}
