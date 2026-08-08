"""Which cadet ranks put someone on the NCO team.

One rule, in one place: it decides who the appraisal page lists and who the
attendance page counts as an NCO. Those have to agree, so nothing re-derives it
locally.
"""

from sqlalchemy.orm import Session

from database.models import Cadet

# Rank is the only marker — flight isn't, NCOs stay posted to the flight they
# run. Staff ranks are never checked against this: a staff sergeant is not a
# cadet NCO.
NCO_RANKS = {"corporal", "sergeant", "flight sergeant"}


def is_nco_rank(rank: str | None) -> bool:
    """True for a cadet rank in the NCO team. Case- and space-insensitive, as
    the rank is scraped verbatim from Bader."""
    return (rank or "").strip().lower() in NCO_RANKS


def nco_team(db: Session) -> list[Cadet]:
    """The NCO team in surname order. Ranks are filtered in Python rather than
    SQL so `is_nco_rank` stays the only place the ranks are named — the squadron
    roster is a couple of hundred rows, so the read costs nothing."""
    cadets = db.query(Cadet).order_by(Cadet.last_name, Cadet.first_name).all()
    return [cadet for cadet in cadets if is_nco_rank(cadet.rank)]
