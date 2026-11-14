"""What each member of staff has due: DBS, BPSS and the mandatory training the
Volunteer Portal tracks, read from the raw copies the portal sync keeps in
Staff_Portal_Data.

The portal's shapes are undocumented (docs/volunteer-portal-api.md has what
the RAFAC Dashy extension reads), so every parser here is defensive: an
unexpected shape gives "no record", never an exception.

Only dates and names leave this module: the DBS disclosure number is personal
data nobody in 317 SMS needs, so it is never read out of the raw copy.
"""

from __future__ import annotations

from datetime import date, timedelta

from core.portal_data import _day, _rows, _text

# How long before an expiry it shows as due, and the weekly email first says so.
DUE_WINDOW = timedelta(days=60)
# RAFAC renews an enhanced DBS every three years. Used only when the portal
# record carries no expiry date of its own.
DBS_RENEW_YEARS = 3

OK = "ok"
EXPIRING = "expiring"
EXPIRED = "expired"
MISSING = "missing"
# Worst first: a person's overall status is the worst of their items.
SEVERITY = (EXPIRED, MISSING, EXPIRING, OK)

_ISSUED = ("dateValidFrom", "dateOfCheck", "issueDate", "dateIssued", "checkDate", "dateCompleted")
_EXPIRES = ("dateExpires", "expiryDate", "dateValidTo", "validTo", "renewalDate")
_WRAPPERS = ("items", "dbs", "bpss", "records", "results", "data", "value")


def _records(raw) -> list[dict]:
    """The portal returns one record, a list, or a list inside a wrapper."""
    if isinstance(raw, dict):
        for key in _WRAPPERS:
            if isinstance(raw.get(key), list):
                return _rows(raw[key])
        return [raw]
    return _rows(raw)


def _first_day(row: dict, keys) -> date | None:
    for key in keys:
        when = _day(row.get(key))
        if when:
            return when
    return None


def _add_years(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:  # 29 February
        return day.replace(year=day.year + years, day=28)


def status_for(expires: date | None, today: date, held: bool = True) -> str:
    if not held:
        return MISSING
    if expires is None:
        return OK
    if expires < today:
        return EXPIRED
    if expires - today <= DUE_WINDOW:
        return EXPIRING
    return OK


def dbs(raw, today: date) -> dict:
    """The newest DBS check: when it was issued and when it lapses."""
    checks = [(r, _first_day(r, _ISSUED)) for r in _records(raw)]
    checks = [(r, d) for r, d in checks if d]
    if not checks:
        return {"key": "dbs", "name": "DBS", "kind": "vetting", "achieved": None, "expires": None,
                "status": MISSING}
    row, issued = max(checks, key=lambda c: c[1])
    expires = _first_day(row, _EXPIRES) or _add_years(issued, DBS_RENEW_YEARS)
    return {"key": "dbs", "name": "DBS", "kind": "vetting", "achieved": issued, "expires": expires,
            "status": status_for(expires, today)}


def bpss(raw, today: date) -> dict:
    """BPSS is done once, so it is held or missing; an expiry is honoured if the
    portal ever gives one."""
    checks = [(r, _first_day(r, _ISSUED)) for r in _records(raw)]
    checks = [(r, d) for r, d in checks if d]
    if not checks:
        return {"key": "bpss", "name": "BPSS", "kind": "vetting", "achieved": None, "expires": None,
                "status": MISSING}
    row, done = max(checks, key=lambda c: c[1])
    expires = _first_day(row, _EXPIRES)
    return {"key": "bpss", "name": "BPSS", "kind": "vetting", "achieved": done, "expires": expires,
            "status": status_for(expires, today)}


def training(raw, today: date) -> list[dict]:
    """The person's mandatory training from their row of the unit tracker
    (``mandatoryTraining[]``). Optional modules are left out: nobody is chased
    for them. A module with no achieved date is missing."""
    row = raw if isinstance(raw, dict) else {}
    items = []
    for t in _rows(row.get("mandatoryTraining")):
        if t.get("mandatory") is False:
            continue
        name = _text(t, "displayName", "name", "title")
        if not name:
            continue
        criteria = _text(t, "criteriaId") or name
        achieved = _day(t.get("dateAchieved"))
        expires = _day(t.get("dateExpires"))
        items.append({
            "key": f"training:{criteria}",
            "name": name,
            "kind": "training",
            "achieved": achieved,
            "expires": expires,
            "status": status_for(expires, today, held=achieved is not None),
        })
    return sorted(items, key=lambda i: i["name"].lower())


def items_for(stored: dict, today: date) -> list[dict] | None:
    """Every item for one person from their stored data sets, or None if the
    portal sync has never brought anything back for them (so the page can say
    "not synced" rather than "everything missing")."""
    if not stored:
        return None
    out = []
    if "dbs" in stored:
        out.append(dbs(stored["dbs"], today))
    if "bpss" in stored:
        out.append(bpss(stored["bpss"], today))
    if "training" in stored:
        out.extend(training(stored["training"], today))
    return out


def worst(items: list[dict] | None) -> str | None:
    if not items:
        return None
    return min((i["status"] for i in items), key=SEVERITY.index)


def due(items: list[dict] | None) -> list[dict]:
    """Items to chase: expired, missing or expiring, worst first then soonest."""
    chase = [i for i in items or [] if i["status"] != OK]
    return sorted(chase, key=lambda i: (SEVERITY.index(i["status"]), i["expires"] or date.max, i["name"]))
