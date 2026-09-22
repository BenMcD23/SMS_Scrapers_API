"""Does a cadet actually hold the qualification a badge is being ordered for?

Both badge order forms — the cadet portal's and the staff stores page's — let
anyone pick any badge in the catalogue, so a badge ordered for a qualification
the cadet never gained was only ever caught by the QM recognising the name.
This module joins the two catalogues that already exist — ``core.catalogue``'s
badge names (what the forms offer) and ``core.qualifications``' Bader patterns
(what the quali scraper records) — and returns a verdict per badge name.

Verdicts are recomputed from the cadet's current qualifications every time they
are asked for, never snapshotted onto the order: SMS trails reality by days, so
an order flagged tonight clears itself once the qual lands without anyone
having to remember to re-check it.

A verdict is advice, not a gate. ``missing`` usually means SMS hasn't caught up
rather than that the cadet is trying it on, so the forms warn and ask the cadet
to confirm; staff are shown the same warning and are never blocked.

Levels are a ladder: a cadet can't hold Silver without having passed through
Blue, so any rung at or above the one being ordered evidences the badge. The
rung actually being ordered is preferred when reporting which record matched,
so the usual case reads back the obvious qualification rather than the highest.
"""

from __future__ import annotations

from datetime import date

from core.catalogue import (
    BADGE_CATEGORIES,
    BADGE_CATEGORY_QUAL_TYPE,
    BADGE_CLASSIFICATION,
    BADGE_LEVEL_QUAL_LEVELS,
    BADGE_NAME_SEPARATOR,
    badge_names,
)
from core.qualifications import BADGE_TYPE_BY_KEY, held_level, qual_is_expired
from core.theory_lessons import class_rank

# ── Verdicts ──────────────────────────────────────────────────────────────────

HELD = "held"          # a matching qualification is on record and current
EXPIRED = "expired"    # matched, but the qualification has lapsed
MISSING = "missing"    # nothing on record evidences it
UNKNOWN = "unknown"    # not tied to a qualification we can check


def _pretty(d: date | None) -> str:
    """"30 Mar 2022" — platform-independent, unlike strftime("%-d")."""
    return f"{d.day} {d:%b %Y}" if d else "an unknown date"


def _as_date(value) -> date | None:
    return value.date() if value is not None else None


def _verdict(status: str, reason: str, **extra) -> dict:
    """One badge's verdict. ``reason`` is written here rather than in the two
    frontends so they can't word the same finding differently."""
    return {
        "status": status,
        "reason": reason,
        "qualName": None,
        "dateAchieved": None,
        "dateExpires": None,
        "levelHeld": None,
        "highestHeld": None,
        **extra,
    }


# ── Badge name → what would evidence it ───────────────────────────────────────

def _category_for(badge_name: str) -> tuple[dict | None, str | None]:
    """The catalogue category a badge name came from, and the level label if it
    has one. Names that aren't in the catalogue at all (renamed entries still
    sitting on old orders) resolve to ``(None, None)``."""
    prefix, sep, level = badge_name.partition(BADGE_NAME_SEPARATOR)
    for cat in BADGE_CATEGORIES:
        if sep and cat.get("prefix") == prefix and level in cat.get("levels", []):
            return cat, level
        if not sep and badge_name in cat.get("items", []):
            return cat, None
    return None, None


# ── Checks ────────────────────────────────────────────────────────────────────

def _matching_quals(level, quals) -> list:
    """The cadet's qualifications whose raw Bader text matches this rung."""
    return [
        q for q in quals
        if any(p.casefold() in (q.qual_type or "").casefold() for p in level.patterns)
    ]


def _leveled_verdict(badge, level_label: str, quals, today: date) -> dict:
    wanted = BADGE_LEVEL_QUAL_LEVELS.get(level_label, ())
    # Levels run highest-first, so the ordered badge's rung is the *last* index
    # among the ones that would evidence it; everything before it sits above.
    rung = max((i for i, lv in enumerate(badge.levels) if lv.level in wanted), default=-1)
    if rung < 0:
        # A catalogue level with no rung behind it — Cyber has no Blue award, so
        # there is nothing to check rather than nothing to find.
        return _verdict(UNKNOWN, f"{badge.name} isn't recorded at {level_label} on SMS, so this can't be checked.")

    current = [q for q in quals if not qual_is_expired(q, today)]
    highest = held_level(badge, [q.qual_type for q in current])

    # Walk out from the rung being ordered towards the top of the ladder, so the
    # record reported back is the most direct evidence available.
    lapsed_hit = None
    for i in range(rung, -1, -1):
        for q in _matching_quals(badge.levels[i], quals):
            if qual_is_expired(q, today):
                lapsed_hit = lapsed_hit or (badge.levels[i], q)
                continue
            achieved = _as_date(q.date_achieved)
            reason = (
                f"SMS shows {q.qual_type}, awarded {_pretty(achieved)}."
                if i == rung else
                f"Not recorded at {level_label}, but SMS shows {q.qual_type} "
                f"({_pretty(achieved)}), which sits above it."
            )
            return _verdict(
                HELD, reason,
                qualName=q.qual_type,
                dateAchieved=achieved.isoformat() if achieved else None,
                dateExpires=None,
                levelHeld=badge.levels[i].level,
                highestHeld=highest,
            )

    if lapsed_hit:
        level, q = lapsed_hit
        expires = _as_date(q.date_expires)
        return _verdict(
            EXPIRED,
            f"{q.qual_type} is on SMS but expired on {_pretty(expires)}.",
            qualName=q.qual_type,
            dateAchieved=_as_date(q.date_achieved).isoformat() if q.date_achieved else None,
            dateExpires=expires.isoformat() if expires else None,
            levelHeld=level.level,
            highestHeld=highest,
        )

    reason = (
        f"No {level_label} {badge.name} on SMS — the highest recorded is {highest.title()}."
        if highest else
        f"No {badge.name} qualification is recorded on SMS."
    )
    return _verdict(MISSING, reason, highestHeld=highest)


def _classification_verdict(item: str, classification: str | None) -> dict:
    """Classification badges are earned by passing the classification exams, so
    they're checked against the cadet's classification rather than a qual."""
    target = BADGE_CLASSIFICATION[item]
    held = classification or "Junior Cadet"  # no classification recorded = not past First Class
    if class_rank(classification) >= class_rank(target):
        return _verdict(HELD, f"SMS shows {held}.", levelHeld=target, highestHeld=held)
    return _verdict(
        MISSING,
        f"SMS shows {held}, not {target}.",
        highestHeld=held,
    )


# ── Public API ────────────────────────────────────────────────────────────────

def badge_qual_status(badge_name: str, quals, classification: str | None,
                      today: date | None = None) -> dict:
    """Whether ``quals`` evidence the badge ``badge_name``.

    ``quals`` are the cadet's ``CadetQualification`` rows (raw Bader text in
    ``qual_type``); ``classification`` their highest classification.
    """
    today = today or date.today()
    cat, level_label = _category_for(badge_name)
    if cat is None:
        return _verdict(UNKNOWN, "This badge isn't in the catalogue, so it can't be checked.")
    if cat["id"] == "classification":
        return _classification_verdict(badge_name, classification)

    qual_key = BADGE_CATEGORY_QUAL_TYPE.get(cat["id"])
    badge = BADGE_TYPE_BY_KEY.get(qual_key) if qual_key else None
    if badge is None or level_label is None:
        # Core badges — issued to everyone, not earned, so nothing to check.
        return _verdict(UNKNOWN, "This badge isn't earned through a qualification.")
    return _leveled_verdict(badge, level_label, list(quals), today)


def badge_qual_statuses(quals, classification: str | None,
                        names: list[str] | None = None,
                        today: date | None = None) -> dict[str, dict]:
    """A verdict per badge name, keyed by the exact name the forms build.

    Defaults to the whole catalogue so a form can fetch once and look up every
    selection the cadet makes without another round trip.
    """
    today = today or date.today()
    quals = list(quals)
    return {
        name: badge_qual_status(name, quals, classification, today)
        for name in (names if names is not None else badge_names())
    }
