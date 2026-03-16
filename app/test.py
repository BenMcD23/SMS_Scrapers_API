from database.database import SessionLocal
from database.models import CadetQualification
db = SessionLocal()
names = db.query(CadetQualification.qual_type).distinct().all()
for n in sorted(names):
    print(repr(n[0]))
db.close()
