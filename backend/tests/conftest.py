"""Shared pytest fixtures.

Test database: this suite runs against SQLite (a temp file recreated for each
test function) rather than PostgreSQL/PostGIS. This keeps `pytest` runnable
without a running Postgres instance. Model-level types
(`app/models/types.py`) are dialect-aware so the same model definitions work
against SQLite in tests and PostgreSQL+PostGIS in dev/production; geometry
columns fall back to plain WKT text under SQLite. If you want to run the
suite against a real Postgres+PostGIS instance instead, set
`TEST_DATABASE_URL` to a postgresql+psycopg2 URL before running pytest and
adjust `conftest.py`'s engine creation accordingly (see README).
"""
from __future__ import annotations

import base64
import os
import uuid

# --- Environment must be configured before importing any `app.*` module ---
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("JWT_SECRET", "test-only-secret-do-not-use-in-prod")
os.environ.setdefault(
    "FIELD_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode()
)
os.environ.setdefault("DATABASE_URL", "sqlite:///./_unused_in_tests.db")
os.environ.setdefault("ROUTE_PROVIDER", "mock")
os.environ.setdefault("WEATHER_ENABLED", "false")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base, get_db
from app.engines.route_engine.cache import clear_route_cache
from app.main import app
from app.security.rate_limit import limiter

TEST_DB_PATH = os.path.join(os.path.dirname(__file__), "_test_fitwaze.db")
TEST_DATABASE_URL = f"sqlite:///{TEST_DB_PATH}"

engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_get_db


@pytest.fixture(autouse=True)
def _fresh_database():
    """Recreate all tables before each test and reset the rate limiter and
    route cache so tests don't leak state (or 429s) into one another."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    limiter.reset()
    clear_route_cache()
    yield
    Base.metadata.drop_all(bind=engine)
    engine.dispose()
    if os.path.exists(TEST_DB_PATH):
        try:
            os.remove(TEST_DB_PATH)
        except PermissionError:
            pass


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def unique_email() -> str:
    return f"user_{uuid.uuid4().hex[:12]}@example.com"


STRONG_PASSWORD = "Correct-Horse-Battery-Staple-9"


def register_and_login(client: TestClient, email: str | None = None, password: str = STRONG_PASSWORD) -> dict:
    """Register a fresh user and return {"email", "password", "access_token",
    "user_id", "client"} — registration also logs the user in and sets the
    refresh cookie on the given client instance."""
    email = email or unique_email()
    resp = client.post("/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 201, resp.text
    data = resp.json()
    return {
        "email": email,
        "password": password,
        "access_token": data["access_token"],
        "user_id": data["user"]["id"],
    }


def auth_headers(access_token: str) -> dict:
    return {"Authorization": f"Bearer {access_token}"}


def valid_profile_payload(**overrides) -> dict:
    payload = {
        "age": 34,
        "sex": "female",
        "goal": "general_fitness",
        "preferred_activity": "walk",
        "current_weekly_minutes": 45,
        "current_frequency": 2,
        "height_cm": 165.5,
        "weight_kg": 68.2,
        "diabetes_status": "none",
        "mobility_limitations": "none",
        "walking_ability": "full",
        "cycling_ability": "full",
    }
    payload.update(overrides)
    return payload
