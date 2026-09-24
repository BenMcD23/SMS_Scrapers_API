from datetime import date, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from api import app
from core.badge_quals import (
    EXPIRED,
    HELD,
    MISSING,
    UNKNOWN,
    badge_qual_status,
    badge_qual_statuses,
)
from core.catalogue import BADGE_CATEGORIES, BADGE_CATEGORY_QUAL_TYPE, badge_names
from core.qualifications import BADGE_TYPE_BY_KEY

client = TestClient(app)

TODAY = date(2026, 9, 22)


def qual(name, achieved="2024-01-01", expires=None):
    """A stand-in for a CadetQualification row — raw Bader text, as scraped."""
    return SimpleNamespace(
        qual_type=name,
        date_achieved=datetime.fromisoformat(achieved),
        date_expires=datetime.fromisoformat(expires) if expires else None,
    )


# Trimmed from a real cadet's qualifications table, names exactly as SMS shows them.
SAMPLE_QUALS = [
    qual("Blue Air Cadet Foundation Leadership", "2022-03-30"),
    qual("Bronze Air Cadet Foundation Leadership", "2024-04-28"),
    qual("Silver Air Cadet Foundation Leadership", "2024-10-27"),
    qual("Blue Road Marching", "2024-03-18"),
    qual("Blue Shot (Air Rifle)", "2023-04-12"),
    qual("Blue Shot (L98A2)", "2025-05-04"),
    qual("Cyber - Bronze Award", "2025-02-16"),
    qual("Cyber - Silver Award", "2025-06-01"),
    qual("Blue Space Studies", "2025-04-02"),
    qual("Bronze Space Studies", "2026-02-21"),
    qual("Bronze Duke Of Edinburgh Award", "2022-08-16"),
    qual("Silver Duke Of Edinburgh Award", "2024-12-10"),
    qual("Gold Duke Of Edinburgh Award", "2026-04-27"),
    qual("Rafac Aviation Training Package Blue Training Badge", "2023-10-29"),
    qual("Rafac Aviation Training Package Bronze Training Badge", "2024-09-07"),
    qual("Musician (Blue) - Drum", "2023-11-12"),
    qual("Wing Musician (Bronze) - Drums", "2024-06-18"),
    qual("Regional Musician (Silver) - Drums", "2025-03-02"),
    qual("Radio - Basic Operator (Blue)", "2023-12-08"),
    qual("St John Activity First Aid", "2024-12-01", "2027-12-01"),
    qual("St John Essential First Aid", "2022-06-24", "2025-06-25"),
    qual("St John Youth First Aid", "2024-02-11", "2027-02-11"),
]


def status(name, quals=SAMPLE_QUALS, classification="Leading Cadet"):
    return badge_qual_status(name, quals, classification, TODAY)


# ── The mapping itself ────────────────────────────────────────────────────────

def test_every_mapped_category_points_at_a_real_badge_type():
    ids = {c["id"] for c in BADGE_CATEGORIES}
    for cat_id, qual_key in BADGE_CATEGORY_QUAL_TYPE.items():
        assert cat_id in ids, cat_id
        assert qual_key in BADGE_TYPE_BY_KEY, qual_key


def test_every_catalogue_badge_gets_a_verdict():
    statuses = badge_qual_statuses(SAMPLE_QUALS, "Leading Cadet", today=TODAY)
    assert set(statuses) == set(badge_names())
    assert all(s["status"] in {HELD, EXPIRED, MISSING, UNKNOWN} for s in statuses.values())


# ── Leveled badges ────────────────────────────────────────────────────────────

def test_the_exact_qualification_reads_back_with_its_award_date():
    res = status("Leadership – Silver")
    assert res["status"] == HELD
    assert res["qualName"] == "Silver Air Cadet Foundation Leadership"
    assert res["dateAchieved"] == "2024-10-27"
    assert "27 Oct 2024" in res["reason"]


def test_a_level_above_the_one_ordered_still_evidences_it():
    # Every rung is on record here, so the Blue one is what should be reported.
    res = status("Leadership – Blue")
    assert res["status"] == HELD
    assert res["levelHeld"] == "blue"

    # Only Bronze on record: ordering Blue is still evidenced, by the higher rung.
    res = status("Leadership – Blue", [qual("Bronze Leadership", "2025-01-06")])
    assert res["status"] == HELD
    assert res["levelHeld"] == "bronze"
    assert "sits above it" in res["reason"]


def test_a_level_above_the_highest_held_is_missing_and_names_what_is_held():
    res = status("Leadership – Gold")
    assert res["status"] == MISSING
    assert res["highestHeld"] == "silver"
    assert "highest recorded is Silver" in res["reason"]


def test_nothing_of_that_badge_at_all_is_missing():
    res = status("Radio – Silver")
    assert res["status"] == MISSING
    assert res["highestHeld"] == "blue"

    res = status("Radio – Blue", [])
    assert res["status"] == MISSING
    assert res["highestHeld"] is None
    assert res["reason"] == "No Radio qualification is recorded on SMS."


def test_the_weapon_and_instrument_variants_all_count():
    assert status("Shooting – Blue")["status"] == HELD
    assert status("Shooting – Bronze")["status"] == MISSING
    assert status("Music – Silver")["qualName"] == "Regional Musician (Silver) - Drums"


def test_road_marching_gold_accepts_either_the_gold_or_nijmegen_rung():
    res = status("Road Marching – Gold (Nijmegen)", [qual("Nijmegen Road Marching", "2025-07-20")])
    assert res["status"] == HELD
    assert res["levelHeld"] == "nijmegen"
    assert status("Road Marching – Gold (Nijmegen)")["status"] == MISSING


def test_old_space_module_names_still_evidence_the_badge():
    res = status("Space – Silver", [qual("Ou Life On Mars (Silver)", "2023-05-02")])
    assert res["status"] == HELD


# ── Expiry ────────────────────────────────────────────────────────────────────

def test_a_lapsed_qualification_is_flagged_rather_than_counted():
    res = status("First Aid – Blue", [qual("St John Essential First Aid", "2022-06-24", "2025-06-25")])
    assert res["status"] == EXPIRED
    assert res["dateExpires"] == "2025-06-25"
    assert "expired on 25 Jun 2025" in res["reason"]


def test_a_current_qualification_higher_up_covers_a_lapsed_one():
    # Essential (Blue) has lapsed, but Youth (Bronze) is current, so the Blue
    # badge is still evidenced — and by the nearest rung above, not the highest.
    res = status("First Aid – Blue")
    assert res["status"] == HELD
    assert res["qualName"] == "St John Youth First Aid"


def test_a_qualification_expiring_later_today_still_counts():
    res = status("First Aid – Bronze", [qual("St John Youth First Aid", "2023-09-22", TODAY.isoformat())])
    assert res["status"] == HELD


# ── Classification and core badges ────────────────────────────────────────────

def test_classification_badges_check_the_classification_ladder():
    assert status("Leading")["status"] == HELD
    assert status("First Class")["status"] == HELD  # a lower rung than the one held
    assert status("Senior")["status"] == MISSING
    assert status("Senior")["reason"] == "SMS shows Leading Cadet, not Senior Cadet."


def test_no_classification_recorded_means_nothing_above_junior():
    res = status("First Class", classification=None)
    assert res["status"] == MISSING
    assert "Junior Cadet" in res["reason"]


def test_core_badges_are_never_flagged():
    for name in ("Squadron Num", "ATC", "TRF"):
        assert status(name)["status"] == UNKNOWN


def test_a_level_with_no_award_behind_it_is_not_reported_as_missing():
    # Cyber starts at Bronze on Bader, so a Cyber Blue badge has nothing to
    # check against and must not be flagged as if the cadet were making it up.
    assert status("Cyber – Blue")["status"] == UNKNOWN


def test_a_badge_name_that_is_not_in_the_catalogue_is_not_flagged():
    assert status("Some Retired Badge – Purple")["status"] == UNKNOWN


# ── Wiring: the verdict rides along on every order item ───────────────────────

def test_order_items_carry_a_live_verdict():
    from routers.badges import badge_order_to_dict

    order = SimpleNamespace(
        id=7,
        created_at=datetime(2026, 9, 20, 19, 30),
        completed=False,
        cadet=SimpleNamespace(
            cin=3103170021025,
            first_name="Matthew",
            last_name="Beverley",
            classification="Leading Cadet",
            qualifications=SAMPLE_QUALS,
        ),
        order_items=[
            SimpleNamespace(
                id=1, badge_name="Leadership – Silver", replacement=False, qm_notes="[]",
                given_at=None, given_by=None, ready_to_collect=None, stock_events="[]",
                gained_where=None, gained_where_detail=None,
                gained_date_from=None, gained_date_to=None,
            ),
            SimpleNamespace(
                id=2, badge_name="Leadership – Gold", replacement=False, qm_notes="[]",
                given_at=None, given_by=None, ready_to_collect=None, stock_events="[]",
                gained_where=None, gained_where_detail=None,
                gained_date_from=None, gained_date_to=None,
            ),
        ],
    )

    items = badge_order_to_dict(order)["items"]
    assert items[0]["qualStatus"]["status"] == HELD
    assert items[1]["qualStatus"]["status"] == MISSING


# ── The endpoints ─────────────────────────────────────────────────────────────

def test_both_endpoints_return_the_catalogue_verdicts():
    """The staff and portal routes over a real session — mostly to prove
    /cadets/{cin}/badge-qual-check isn't swallowed by /cadets/{cin}."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from core.db import get_current_cadet, get_db
    from core.security import require_staff
    from database.database import Base
    from database.models import Cadet, CadetQualification

    # TestClient runs the request on a worker thread, so the usual in-memory
    # SQLite has to be told to allow the connection across threads.
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()

    cadet = Cadet(cin=3103170021025, first_name="Matthew", last_name="Beverley",
                  classification="Leading Cadet")
    db.add(cadet)
    for q in SAMPLE_QUALS:
        db.add(CadetQualification(cadet_id=cadet.cin, qual_type=q.qual_type, status="true",
                                  date_achieved=q.date_achieved, date_expires=q.date_expires))
    db.commit()

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_staff] = lambda: {"email": "qm@317atc.co.uk"}
    app.dependency_overrides[get_current_cadet] = lambda: cadet
    try:
        staff = client.get(f"/cadets/{cadet.cin}/badge-qual-check")
        mine = client.get("/cadets/me/badge-qual-check")
        missing = client.get("/cadets/1/badge-qual-check")
    finally:
        app.dependency_overrides.clear()

    assert staff.status_code == 200
    assert staff.json() == mine.json()
    assert staff.json()["Leadership – Silver"]["status"] == HELD
    assert staff.json()["Leadership – Gold"]["status"] == MISSING
    assert missing.status_code == 404
