import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
import sys
Base = declarative_base()

# Build absolute path to the database file
db_path = os.path.join(os.path.abspath(os.path.dirname(sys.argv[0])), "scraper_data.db")

# Create the SQLite engine using the absolute path
engine = create_engine(f"sqlite:///{db_path}")

# Create a session factory
SessionLocal = sessionmaker(bind=engine)
