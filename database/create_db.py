import os
from database.database import Base, engine, SessionLocal
from sqlalchemy import inspect

Base.metadata.create_all(bind=engine)

def init_db():
    db_dir = os.path.dirname(os.path.abspath("app/database/317_SMS.db"))
    os.makedirs(db_dir, exist_ok=True)  # create folder if it doesn’t exist

    inspector = inspect(engine)
    if not inspector.get_table_names():
        print("Database not found or empty — creating tables...")
        Base.metadata.create_all(bind=engine)
    else:
        print("Database already initialized.")