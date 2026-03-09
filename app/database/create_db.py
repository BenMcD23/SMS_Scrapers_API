from app.database.database import Base, engine

def init_db():
    # Because we imported 'models' above, Base now knows about 'Users'
    Base.metadata.create_all(bind=engine)
    print("Database tables synchronized.")