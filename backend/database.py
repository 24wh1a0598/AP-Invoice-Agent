from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Local SQLite
SQLALCHEMY_DATABASE_URL = "sqlite:///./ap_agent.db"

# Create the engine object
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)

# Session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for models
Base = declarative_base()

# Dependency function
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()