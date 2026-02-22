# database/database.py
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# The ONE and ONLY Base
Base = declarative_base()

# Use a consistent path
DB_PATH = os.path.join(os.path.dirname(__file__), "317_SMS.db")
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()