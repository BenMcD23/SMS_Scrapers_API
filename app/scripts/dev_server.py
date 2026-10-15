"""Run the API locally on a seeded SQLite database: no Postgres, no Google, no
cluster access. For previewing the 317 SMS site (or the cadet portal) with
realistic data, e.g. from a cloud Claude session that can't reach the cluster.

    python app/scripts/dev_server.py            # http://localhost:8000
    python app/scripts/dev_server.py --reset    # throw the database away first
    python app/scripts/dev_server.py --port 8001 --db /tmp/other.db

The database (data/dev.db by default, gitignored) is seeded on first run and
kept after that, so anything clicked into the UI survives a restart. Sign in
with the site's dev buttons (AUTH_DEV_BYPASS=1): "Staff" is the owner account,
"SNCO" and "NCO" are cadets on the seeded roster.

The seed is deterministic, so a screenshot taken today matches one taken next
week. It never touches a database that already has cadets in it.
"""

import argparse
import os
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "data" / "dev.db"

# Everything that makes this local and offline. Applied before the app is
# imported, because config and the database engine are read at import time.
# Without Google service-account keys the group lookup returns None rather than
# calling out, and the dev token (DEV_FAKE_AUTH) decides the role instead.
DEV_ENV = {
    "DEV_FAKE_AUTH": "1",
    "CORS_ALLOW_LOCALHOST": "true",
    "SCHEDULER_ENABLED": "false",
    "EMAIL_DISABLED": "true",
    # The OC dashboard (leaving process, parent emails) is OC-only; make the dev
    # owner login the OC, and give "request removal" somewhere to point.
    "OC_EMAIL": "ci.mcdonald@317atc.co.uk",
    "ADJUTANT_EMAIL": "adjutant@example.com",
    "DB_BACKUP_ENABLED": "false",
    # A throwaway key: the seed stores nothing secret, it just has to import.
    "ENCRYPTION_KEY": "5-6ZgtVJdBRAJfCPIhLdyUFqGgb0ChZfBGB4fL0jTOo=",
}

FIRST_NAMES = [
    "Amelia", "Oliver", "Isla", "Noah", "Ava", "Leo", "Mia", "Jack", "Freya", "Harry",
    "Zara", "Aoife", "Sienna", "Kai", "Ruby", "Theo", "Lily", "Finn", "Grace", "Arlo",
    "Chloé", "Mohammed", "Priya", "Josh", "Zoë", "Ollie", "Maya", "Reuben",
]
LAST_NAMES = [
    "Smith", "Jones", "Taylor", "Brown", "Williams", "Wilson", "Patel", "Khan",
    "O'Neill", "Murphy", "Evans", "Walker", "Wright", "Hughes", "Nowak", "Ó Briain",
]
# Weighted like a real squadron: mostly cadets, a handful of each NCO rank.
RANKS = ["Cadet"] * 10 + ["Cpl"] * 3 + ["Sgt"] * 2 + ["FS", "CWO"]
FLIGHTS = ["A", "B", "C"]
UNIT = "317 (Failsworth) Sqn"
CADET_COUNT = 60
FIRST_CIN = 2_100_000
PARADE_NIGHTS = 16
# Indexes of cadets who haven't paraded for LAPSED_WEEKS+ weeks (so the leaving
# process lists them).
LAPSED_CADETS = (20, 21, 22)
LAPSED_WEEKS = 9
# Days from now each first aid expiry lands, taken in turn: one expired, two in
# the record page's 60-day warning, the rest fine.
FIRST_AID_EXPIRY_DAYS = [-20, 25, 50, 150, 300, 420]


def _qual_name(level) -> str:
    """A name this rung counts — its first audit pattern. Not the Bader dropdown
    text: some of those ("Methods of Instruction(Training)") aren't what the
    scraper reads back, and wouldn't count toward the dashboard badge."""
    return level.patterns[0]


def _parade_nights(today: datetime, count: int) -> list[datetime]:
    """The last `count` Mondays before today, oldest first."""
    last = today - timedelta(days=(today.weekday() or 7))
    return [last - timedelta(weeks=w) for w in reversed(range(count))]


def seed(db, now: datetime | None = None) -> bool:
    """Fill an empty database with a squadron's worth of roster data. Returns
    False, changing nothing, if there are already cadets — a dev database
    someone has been clicking around in is never overwritten."""
    # Imported here, not at the top: the models pull in the engine, which must
    # only be built after main() has pointed DATABASE_URL at the dev file.
    import core.security as security
    from core.qualifications import BADGE_TYPES
    from core.theory_lessons import CLASSIFICATION_ORDER
    from database.models import (
        Cadet,
        CadetAttendance,
        CadetDietary,
        CadetMedical,
        CadetQualification,
        Staff,
    )

    if db.query(Cadet).count():
        return False

    rng = random.Random(317)
    now = now or datetime.now()
    nights = _parade_nights(now, PARADE_NIGHTS)

    expiry_days = iter(FIRST_AID_EXPIRY_DAYS * CADET_COUNT)
    cadets = []
    for i in range(CADET_COUNT):
        age = rng.randint(12, 18)
        cadets.append(Cadet(
            cin=FIRST_CIN + i,
            first_name=rng.choice(FIRST_NAMES),
            last_name=rng.choice(LAST_NAMES),
            email=f"cadet{i}@example.com",
            # Ofcom's drama range: real-looking, never anyone's phone.
            phone_number=f"07700900{i:03d}",
            # Every third cadet has none, so "missing" shows; 9 and 10 are
            # siblings sharing one address, so the BCC de-duplicates.
            parent_emails=None if i % 3 == 2 else (
                "shared.parent@example.com" if i in (9, 10) else f"parent{i}@example.com"),
            date_of_birth=datetime(now.year - age, rng.randint(1, 12), rng.randint(1, 28)),
            rank=rng.choice(RANKS),
            flight=rng.choice(FLIGHTS),
            classification=rng.choice([None, *CLASSIFICATION_ORDER[1:]]),
        ))
    # The dev SNCO/NCO logins are cadets on the roster, as real ones are, so
    # pages that look the signed-in NCO up by email find them.
    cadets[0].rank, cadets[0].email = "Sgt", security._dev_fake_email("snco")
    cadets[1].rank, cadets[1].email = "Cpl", security._dev_fake_email("nco")
    cadets[7].banned = True
    db.add_all(cadets)

    # Cadets who stopped coming: no parade in the last LAPSED_WEEKS weeks, so
    # the OC dashboard's leaving process has someone to list.
    lapsed = {cadets[i].cin for i in LAPSED_CADETS}
    for c in cadets:
        for night in nights:
            if c.cin in lapsed and night > nights[-1] - timedelta(weeks=LAPSED_WEEKS):
                continue
            status = rng.choice(["Present Correctly Dressed"] * 6 + ["Present Incorrectly Dressed", "Absent"])
            db.add(CadetAttendance(cadet_id=c.cin, date=night, register_type="Parade Night",
                                   status=status, unit=UNIT))
        for badge in rng.sample(BADGE_TYPES, k=rng.randint(0, 4)):
            level = rng.choice(badge.levels)
            achieved = now - timedelta(days=rng.randint(30, 700))
            # First aid lapses, so the record page and the stats "expiring"
            # list get expired, soon and fine examples.
            expires = now + timedelta(days=next(expiry_days)) if badge.key == "first_aid" else None
            db.add(CadetQualification(cadet_id=c.cin, qual_type=_qual_name(level), status="true",
                                      date_achieved=achieved, date_expires=expires, has_attachment=True))

    seed_portal(db, cadets, rng, now)
    seed_flying_paperwork(db, cadets, now)

    db.add(CadetMedical(cadet_id=cadets[3].cin, allergy_name="Peanuts", auto_injector="Yes",
                        severity="Severe", details="Carries two pens"))
    db.add(CadetMedical(cadet_id=cadets[9].cin, allergy_name="Asthma", details="Blue inhaler"))
    db.add(CadetDietary(cadet_id=cadets[5].cin, name="Vegetarian"))
    db.add(CadetDietary(cadet_id=cadets[12].cin, name="Halal"))

    staff = [
        # The dev "Staff" login is the owner, so it gets a staff record too —
        # otherwise Settings says the account isn't linked to the roster.
        ("Dev", "Owner", "CI", security.OWNER_EMAIL),
        ("Sarah", "Hughes", "Sgt", "s.hughes@example.com"),
        ("Tom", "Clark", "Fg Off", "t.clark@example.com"),
        ("Jane", "Doe", "CI", "j.doe@example.com"),
        ("Priya", "Shah", "Flt Lt", "p.shah@example.com"),
    ]
    for i, (first, last, rank, email) in enumerate(staff):
        db.add(Staff(cin=3_000 + i, first_name=first, last_name=last, rank=rank, email=email,
                     phone_number=f"07700901{i:03d}",
                     attendance={n.strftime("%Y-%m"): rng.randint(1, 4) for n in nights}))

    db.commit()
    return True


def seed_flying_paperwork(db, cadets, now: datetime) -> None:
    """What the event scraper would have tracked: an upcoming AEF day part
    ticked off, a gliding day nobody's started, and a finished one kept open
    because it has ticks."""
    from database.models import FlyingEvent, FlyingPaperwork
    from scripts.flying_paperwork import FLYING_CATEGORIES

    aef, gliding = FLYING_CATEGORIES
    events = [
        ("90001", "11 AEF Flying Day", aef, 12, True, cadets[2:8]),
        ("90002", "632 VGS Gliding Induction", gliding, 26, True, cadets[10:14]),
        ("90003", "11 AEF Flying Day (September)", aef, -20, False, cadets[20:23]),
    ]
    for i, (event_id, title, category, days, on_bader, on_event) in enumerate(events, start=1):
        start = (now + timedelta(days=days)).replace(hour=8, minute=0, second=0, microsecond=0)
        fe = FlyingEvent(bader_event_id=event_id, title=title, category=category, date_from=start,
                         date_to=start.replace(hour=17), on_bader=on_bader,
                         created_at=now - timedelta(days=30), last_seen_at=now if on_bader else start)
        for n, cadet in enumerate(on_event):
            started = i != 2  # the gliding day is untouched
            fe.cadets.append(FlyingPaperwork(cadet_id=cadet.cin, on_event=not (i == 1 and n == 0),
                                             tg21=started and n % 2 == 0, tg23=started and n % 3 == 0,
                                             av_med=started and n < 2))
        db.add(fe)


# What a Volunteer Portal sync would have filled in: join and classification
# dates, flights (so Blue Flying has every stage state), passed exams.
FLYING_MIX = ["none", "none", "ptt", "flight", "both", "both"]


def seed_portal(db, cadets, rng, now: datetime) -> None:
    from core.theory_lessons import CLASSIFICATION_ORDER, LEADING, SENIOR_MASTER, THEORY_LESSONS
    from database.models import CadetFlight, CadetQualification, CadetTheoryProgress

    today = now.date()
    for c in cadets:
        c.joined_on = today - timedelta(days=rng.randint(20, 1500))
        # Classification dates climb from joining, a few months a step.
        reached = CLASSIFICATION_ORDER.index(c.classification) if c.classification else 0
        when, dates = c.joined_on, {}
        for name in CLASSIFICATION_ORDER[1:reached + 1]:
            when = min(when + timedelta(days=rng.randint(60, 300)), today)
            dates[name] = when.isoformat()
        c.classification_dates = dates or None
        if not reached:
            continue

        # Blue aviation comes with First Class training.
        db.add(CadetQualification(cadet_id=c.cin, qual_type="Blue ATP Ground School", status="true",
                                  date_achieved=datetime.fromisoformat(dates["First Class Cadet"])))
        mix = rng.choice(FLYING_MIX)
        if mix in ("ptt", "both"):
            db.add(CadetFlight(cadet_id=c.cin, date=today - timedelta(days=rng.randint(30, 600)),
                               activity="simulator", aircraft="PTT", category="PTT", duty="Blue ATP", unit="AEF"))
        if mix in ("flight", "both"):
            glider = rng.random() < 0.4
            db.add(CadetFlight(
                cadet_id=c.cin, date=today - timedelta(days=rng.randint(10, 700)),
                activity="gliding" if glider else "powered", aircraft="Viking" if glider else "Tutor",
                category="VGS" if glider else "AEF", duty="GIF" if glider else "Blue ATP",
                sortie="Gliding Induction Flight" if glider else "Sortie 1 - AEF",
                unit="645 VGS (RAF Topcliffe)" if glider else "11 AEF (RAF Leeming)",
                minutes=rng.randint(10, 30)))

        # All of a set of exams passed once the classification they lead to is
        # reached (Leading's at Leading, Senior/Master's at Master); about half
        # for the cadets still working through them.
        # A third of those still working through them have had their next lesson
        # but not sat its exam, so the classification day planner has exams to give.
        taught_next = rng.random() < 0.33
        for lesson in THEORY_LESSONS:
            working, done = {LEADING: ((1,), 2), SENIOR_MASTER: ((2, 3), 4)}.get(lesson.category, ((), 99))
            if reached >= done or (reached in working and rng.random() < 0.5):
                db.add(CadetTheoryProgress(cadet_id=c.cin, lesson_key=lesson.key,
                                           completed_at=now - timedelta(days=rng.randint(5, 300)),
                                           recorded_by="Volunteer Portal"))
            elif reached in working and taught_next:
                taught_next = False
                db.add(CadetTheoryProgress(cadet_id=c.cin, lesson_key=lesson.key,
                                           completed_at=now - timedelta(days=rng.randint(5, 30)),
                                           recorded_by="staff@example.com"))


def seed_stores(client, headers: dict) -> None:
    """Shelves, stock and a few open orders, added through the real endpoints so
    they always pass the API's own validation, sized from /reference so they
    never name an item or size the site doesn't offer."""
    ref = client.get("/reference", headers=headers).json()["uniform"]
    sized = [t for t in ref["itemTypes"] if t not in ref["noSizeItems"] and ref["sizes"].get(t)]

    def post(path, body):
        res = client.post(path, json=body, headers=headers)
        assert res.status_code < 300, f"{path}: {res.status_code} {res.text}"

    for box in ("A", "B", "C"):
        post("/stores/structure", {"action": "add-box", "box": box})
        for section in ("1", "2"):
            post("/stores/structure", {"action": "add-section", "box": box, "section": section})

    for i, item in enumerate(sized):
        sizes = ref["sizes"][item]
        for size in sizes[len(sizes) // 3: len(sizes) // 3 + 2]:
            post("/stores/stock", {"itemType": item, "size": size, "box": "ABC"[i % 3],
                                   "section": str(i % 2 + 1), "quantity": 2 + i % 4})

    for n, cin in enumerate((FIRST_CIN + 4, FIRST_CIN + 11, FIRST_CIN + 18, FIRST_CIN + 25)):
        item = sized[n % len(sized)]
        post("/stores/orders", {"cadetCin": cin, "items": [
            {"itemType": item, "size": ref["sizes"][item][0]},
            {"itemType": ref["noSizeItems"][0], "size": ""},
            {"itemType": sized[(n + 1) % len(sized)], "needSizing": True, "sizingDetails": "Growing fast"},
        ]})
    post("/stores/badges/orders", {"cadetCin": FIRST_CIN + 4, "items": [{"badgeName": "ATC"}]})

    # What each of those cadets was last given, so the orders page's Sizes
    # popover and the record's Uniform tab have history to show.
    for n, cin in enumerate((FIRST_CIN + 4, FIRST_CIN + 11, FIRST_CIN + 18)):
        given = []
        for item in sized[n::2]:
            sizes = ref["sizes"][item]
            given.append({"itemType": item, "sizeGiven": sizes[min(n + 2, len(sizes) - 1)],
                          "lastGiven": f"2025-{n + 3:02d}-14"})
        post(f"/stores/issuances/{cin}", {"givenBy": "Dev Owner", "items": given})


def seed_stores_history(client, headers: dict, db, now: datetime | None = None) -> None:
    """A year and a bit of handed-out uniform, so the stores insights page has
    demand to rank. Orders go through the real endpoint; only their dates and
    "given" stamps are then written directly, because the API always stamps now.
    Sizes cluster around the middle of each range, as real cadets' do."""
    from database.models import StoresOrder

    now = now or datetime.now()
    rng = random.Random(317)
    ref = client.get("/reference", headers=headers).json()["uniform"]
    # Shirts and berets wear out or are outgrown far more than belts.
    weights = {"Beret": 5, "Jumper": 4, "Wedgewood Male": 4, "Wedgewood Female": 3, "Trousers": 4,
               "Slacks": 3, "Working Blue Male": 2, "Working Blue Female": 1, "Tie": 3, "Belt": 1,
               "Brassard": 1, "Skirts": 1}
    types = [t for t in ref["itemTypes"] if t in weights]
    for n in range(70):
        picked = rng.sample(types, k=rng.randint(1, 3), counts=[weights[t] for t in types])
        items = []
        for item in dict.fromkeys(picked):
            sizes = ref["sizes"].get(item) or [""]
            centre = len(sizes) / 2
            idx = min(len(sizes) - 1, max(0, int(rng.triangular(0, len(sizes), centre))))
            items.append({"itemType": item, "size": "" if item in ref["noSizeItems"] else sizes[idx]})
        res = client.post("/stores/orders", json={"cadetCin": FIRST_CIN + n % 50, "items": items},
                          headers=headers)
        assert res.status_code == 201, res.text
        order = db.get(StoresOrder, int(res.json()["id"]))
        order.created_at = now - timedelta(days=rng.randint(20, 450))
        order.completed = True
        for oi in order.order_items:
            oi.given_at = order.created_at + timedelta(days=rng.randint(0, 21))
            oi.given_by = "Dev Owner"
        # Commit per order: the next API call writes through its own
        # connection, and SQLite won't let it while this one holds the lock.
        db.commit()


INSPECTION_NIGHTS = 8
# Each flight gets a pattern for the inspection audit to find: B's shoes are a
# persistent fault that gets worse, A's berets get better, C's hair is spread
# thin. The texts vary on purpose, as real inspectors' do, so the AI has
# variants to merge.
INSPECTION_PATTERNS = {
    "A": ("Beret / Headdress", ["badge not over left eye", "beret not shaped", "Beret badge off centre"],
          lambda night: 0.6 if night < INSPECTION_NIGHTS // 2 else 0.1),
    "B": ("Shoes", ["not bulled", "dull toecaps", "shoes need polishing", "Scuffed heels"],
          lambda night: 0.3 if night < INSPECTION_NIGHTS // 2 else 0.6),
    "C": ("Hair / Face", ["hair over collar", "hair not in a bun", "loose hair"], lambda night: 0.25),
}
OCCASIONAL_FAULTS = [
    ("Trousers", "creases not sharp"), ("Jumper / Shirt / Tie", "tie knot loose"),
    ("Jumper / Shirt / Tie", "shirt not ironed"), ("Trousers", "fluff on trousers"),
]
INSPECTION_POSITIVES = [("Shoes", "excellent bulling"), ("Beret / Headdress", "well shaped beret"),
                        ("Trousers", "sharp creases")]


def seed_inspections(client, headers: dict, now: datetime | None = None) -> None:
    """Weekly inspection sheets for the last INSPECTION_NIGHTS parade nights,
    through the real submit endpoint so AWOLs and flight scores are worked out
    the way the site's own submissions are. Every fourth night is MTP."""
    rng = random.Random(1317)
    nights = _parade_nights(now or datetime.now(), PARADE_NIGHTS)[-INSPECTION_NIGHTS:]
    left = FIRST_CIN + 7  # the banned cadet
    lapsed = {FIRST_CIN + i for i in LAPSED_CADETS}
    roster = [c for c in client.get("/cadets", headers=headers).json() if c["cin"] != left]

    for n, night in enumerate(nights):
        mtp = n % 4 == 3
        marks = []
        for c in roster:
            if c["cin"] in lapsed or rng.random() < 0.08:
                marks.append({"cin": c["cin"], "absent": True})
                continue
            comments = []
            region, texts, chance = INSPECTION_PATTERNS[c["flight"]]
            if mtp and region == "Shoes":
                region = "Boots"
            if rng.random() < chance(n):
                comments.append({"type": "fault", "region": region, "text": rng.choice(texts)})
            if not mtp and rng.random() < 0.15:
                where, text = rng.choice(OCCASIONAL_FAULTS)
                comments.append({"type": "fault", "region": where, "text": text})
            if rng.random() < 0.12:
                where, text = rng.choice(INSPECTION_POSITIVES)
                comments.append({"type": "positive", "region": where, "text": text})
            faults = sum(1 for x in comments if x["type"] == "fault")
            score = max(3.0, min(10.0, 8.5 - 1.5 * faults + rng.choice([-0.5, 0, 0.5, 1])))
            marks.append({"cin": c["cin"], "score": score, "comments": comments})
        res = client.post("/inspections", headers=headers, json={
            "date": night.date().isoformat(), "uniform": "mtp" if mtp else "blues", "marks": marks,
        })
        assert res.status_code < 300, f"/inspections: {res.status_code} {res.text}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite file (default: data/dev.db)")
    ap.add_argument("--reset", action="store_true", help="delete the database first")
    args = ap.parse_args()

    db_path = args.db.resolve()
    if args.reset:
        db_path.unlink(missing_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    os.environ.update(DEV_ENV)
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    sys.path.insert(0, str(ROOT / "app"))

    import uvicorn
    from fastapi.testclient import TestClient

    from api import app
    from database.database import Base, SessionLocal, engine

    # create_all, not Alembic: the migrations are written for PostgreSQL, and a
    # throwaway preview database has no history to migrate.
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        fresh = seed(db)
    if fresh:
        client, headers = TestClient(app), {"Authorization": "Bearer dev-fake-token"}
        seed_stores(client, headers)
        with SessionLocal() as db:
            seed_stores_history(client, headers, db)
        seed_inspections(client, headers)
        print(f"seeded {db_path}")
    else:
        print(f"using existing {db_path} (--reset to start over)")

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
