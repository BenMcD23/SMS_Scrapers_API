import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

Base = declarative_base()

DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    # Local dev — store DB in project root /data folder
    db_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../data"))
    os.makedirs(db_dir, exist_ok=True)
    DATABASE_URL = f"sqlite:///{db_dir}/317_SMS.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()