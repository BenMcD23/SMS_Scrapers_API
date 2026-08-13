import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import NullPool

load_dotenv()

Base = declarative_base()

DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    # Local dev — store DB in project root /data folder, use postregs preferably
    db_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../data"))
    os.makedirs(db_dir, exist_ok=True)
    DATABASE_URL = f"sqlite:///{db_dir}/317_SMS.db"

# Set by the Lambda runtime itself, so nothing has to be configured for it.
IS_LAMBDA = bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))

_is_sqlite = DATABASE_URL.startswith("sqlite")
connect_args = {"check_same_thread": False} if _is_sqlite else {}

if IS_LAMBDA and not _is_sqlite:
    # Neon sits behind PgBouncer, and a Lambda container is frozen between
    # invocations: a SQLAlchemy pool would hand out connections the pooler has
    # long since dropped, and leak the rest every time a container is reaped.
    # NullPool opens one connection per request and closes it — PgBouncer is
    # the pool. pool_pre_ping would only add a round trip to a brand-new
    # connection, so it's dropped here and kept everywhere else (the worker
    # holds connections open for hours between scrapes).
    engine_kwargs = {"poolclass": NullPool}
else:
    engine_kwargs = {"pool_pre_ping": True}

engine = create_engine(DATABASE_URL, connect_args=connect_args, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()