"""Self-check: every cadet/user portal route scopes its query to the caller.

Run: PYTHONPATH=app venv/bin/python app/test_portal_ownership.py

The portal is the one place where a non-staff caller reaches the database, so
its safety rests entirely on each handler filtering by the identity resolved
from the verified token — `cadet.cin` for get_current_cadet routes, `user.id`
for get_current_user ones. Miss that filter on a new route and any cadet can
read or delete another cadet's orders by guessing an id.

This is the cheap stand-in for database row-level security: rather than push
identity into Postgres, assert statically that the filter is present. Parsing
the source means no database, no token and no running app are needed.
"""

import ast
import os

PORTAL = os.path.join(os.path.dirname(__file__), "routers", "portal.py")

# Which caller dependency implies which ownership expression.
EXPECTED = {
    "get_current_cadet": "cadet.cin",
    "get_current_user": "user.id",
}


def _route_paths(func):
    """The route paths this function is registered on, if any."""
    paths = []
    for dec in func.decorator_list:
        if not isinstance(dec, ast.Call) or not dec.args:
            continue
        target = dec.func
        if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) \
                and target.value.id == "router":
            first = dec.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                paths.append(first.value)
    return paths


def _caller_dep(func):
    """Which get_current_* dependency this handler takes, if any."""
    for default in list(func.args.defaults) + [d for d in func.args.kw_defaults if d]:
        if isinstance(default, ast.Call) and isinstance(default.func, ast.Name) \
                and default.func.id == "Depends" and default.args:
            dep = default.args[0]
            if isinstance(dep, ast.Name) and dep.id in EXPECTED:
                return dep.id
    return None


def _attribute_refs(func):
    """Every `x.y` reference in the body, as "x.y" strings."""
    refs = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            refs.add(f"{node.value.id}.{node.attr}")
    return refs


def _touches_db(func):
    return any(
        isinstance(n, ast.Attribute) and n.attr == "query"
        for n in ast.walk(func)
    )


def check(source, label):
    """Return a list of failure strings for one module's source."""
    tree = ast.parse(source)
    failures = []
    checked = 0
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        paths = _route_paths(node)
        if not paths:
            continue
        dep = _caller_dep(node)
        if dep is None:
            continue
        checked += 1
        # A handler that never queries can't leak another caller's rows.
        if not _touches_db(node):
            continue
        expected = EXPECTED[dep]
        if expected not in _attribute_refs(node):
            failures.append(
                f"{label}: {node.name} {paths} takes {dep} but never filters on {expected}"
            )
    return failures, checked


# ── The real check ────────────────────────────────────────────────────────────

with open(PORTAL) as f:
    failures, checked = check(f.read(), "portal.py")

assert checked > 0, "found no portal routes to check — did the router change shape?"
assert not failures, "unscoped portal route(s):\n  " + "\n  ".join(failures)

# ── Prove the check can actually fail ─────────────────────────────────────────
# A green test that cannot go red is worthless, so run the same checker over a
# handler with its ownership filter removed and assert it is caught.

BROKEN = '''
@router.delete("/cadets/me/orders/{order_id}")
def cadet_delete_order(order_id: int, db=Depends(get_db), cadet=Depends(get_current_cadet)):
    order = db.query(StoresOrder).filter(StoresOrder.id == order_id).first()
    db.delete(order)
'''
broken_failures, broken_checked = check(BROKEN, "synthetic")
assert broken_checked == 1, "self-test handler was not picked up by the checker"
assert broken_failures, "checker failed to catch a route missing its ownership filter"

print(f"OK — {checked} portal routes all scope to the caller; "
      f"checker verified to catch a missing filter")
