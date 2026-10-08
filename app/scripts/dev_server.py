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

    for c in cadets:
        for night in nights:
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
        seed_stores(TestClient(app), {"Authorization": "Bearer dev-fake-token"})
        print(f"seeded {db_path}")
    else:
        print(f"using existing {db_path} (--reset to start over)")

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
