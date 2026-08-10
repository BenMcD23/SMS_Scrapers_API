"""Every endpoint the inspection sheet calls admits staff and SNCOs.

The sheet builds its flight tabs from GET /cadets, so when that one endpoint was
staff-only an SNCO saw "No cadets found for this flight" — the page looked
broken rather than forbidden. Asserting the whole set together stops one of them
drifting back out of step with the others.

No DB or network — this only inspects the routes' declared dependencies.
"""

import pytest

from api import app
from core.security import (
    require_staff, require_staff_or_snco, require_staff_or_nco, require_user,
)

# Who each dependency lets through.
ADMITS = {
    require_user.__name__: {"staff", "snco", "nco"},
    require_staff_or_nco.__name__: {"staff", "snco", "nco"},
    require_staff_or_snco.__name__: {"staff", "snco"},
    require_staff.__name__: {"staff"},
}

# Paths the inspection sheet hits: the roster it renders flights from, the
# absences it prefills, and the submit.
INSPECTION_PATHS = ["/absences", "/cadets", "/inspections"]


def guards(path: str) -> set[str]:
    """Roles admitted by every security dependency declared on `path`."""
    roles = {"staff", "snco", "nco"}
    found = False
    for route in app.routes:
        if getattr(route, "path", None) != path:
            continue
        for dep in route.dependant.dependencies:
            admits = ADMITS.get(dep.call.__name__)
            if admits is not None:
                roles &= admits
                found = True
    assert found, f"{path}: no role guard found — is the path still right?"
    return roles


@pytest.mark.parametrize("path", INSPECTION_PATHS)
@pytest.mark.parametrize("role", ["staff", "snco"])
def test_inspection_endpoint_admits(path, role):
    assert role in guards(path), f"{path} shuts out {role}s: admits {guards(path)}"


def test_cadet_roster_stays_off_ncos():
    # The roster is the one that regressed, and it must not swing the other way
    # either — NCOs get /cadets/search, not the full list.
    assert guards("/cadets") == {"staff", "snco"}


def test_admits_table_still_reflects_reality():
    # Guards against ADMITS going stale: a real staff-only endpoint must still
    # read as staff-only.
    assert guards("/users") == {"staff"}
