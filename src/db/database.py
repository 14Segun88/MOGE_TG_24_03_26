import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Для MVP используем SQLite (файл будет сохранен локально)
# В продакшене достаточно заменить эту строку на "postgresql://user:pass@localhost/dbname"
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./hitl_database.db")

# Параметр check_same_thread нужен только для SQLite
engine_kwargs = {}
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, **engine_kwargs)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    """
    Dependency для FastAPI (или скриптов),
    чтобы получать и безопасно закрывать сессию к БД.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
