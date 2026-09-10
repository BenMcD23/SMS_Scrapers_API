"""SQLAlchemy engine and session factory.

``DATABASE_URL`` points at PostgreSQL everywhere the app is deployed. Without
it (a bare local checkout) the app falls back to a SQLite file under ``data/``
so it still boots; tests set ``sqlite:///:memory:`` explicitly.
"""

import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()

Base = declarative_base()

DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    db_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
    os.makedirs(db_dir, exist_ok=True)
    DATABASE_URL = f"sqlite:///{db_dir}/317_SMS.db"

_is_sqlite = DATABASE_URL.startswith("sqlite")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    # Drop connections the server has closed (failover, idle timeout) instead
    # of handing them to a request.
    pool_pre_ping=True,
    # Pool sizing only applies to PostgreSQL; SQLite ignores it.
    **({} if _is_sqlite else {
        "pool_size": int(os.getenv("DB_POOL_SIZE", "5")),
        "max_overflow": int(os.getenv("DB_MAX_OVERFLOW", "10")),
        "pool_recycle": int(os.getenv("DB_POOL_RECYCLE_S", "1800")),
    }),
)
SessionLocal = sessionmaker(bind=engine)
