"""SQLAlchemy engine/session setup and the `get_db` FastAPI dependency.

Production/dev uses PostgreSQL + PostGIS (see docker-compose.yml). The test
suite uses SQLite by default (see backend/tests/conftest.py) so contributors
can run `pytest` without a running Postgres instance. Model-level types
(`app/models/types.py`) are dialect-aware so the same model definitions work
against either backend.
"""
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()


def _engine_kwargs(url: str) -> dict:
    if url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    return {"pool_pre_ping": True}


engine = create_engine(settings.database_url, **_engine_kwargs(settings.database_url))
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
