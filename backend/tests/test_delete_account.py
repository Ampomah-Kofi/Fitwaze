"""Deleting an account erases everything recorded about the person."""
from __future__ import annotations

from sqlalchemy import text

from tests.conftest import STRONG_PASSWORD, TestingSessionLocal, auth_headers, register_and_login
from tests.test_glucose_trend import _walk
from tests.conftest import valid_profile_payload


def _rows(table, user_id=None):
    with TestingSessionLocal() as db:
        return db.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()


def test_deleting_erases_everything_and_signs_out(client):
    keep = auth_headers(register_and_login(client)["access_token"])  # someone else, untouched
    client.put("/profile", json=valid_profile_payload(), headers=keep)
    _walk(client, keep, before=150, after=120)

    user = register_and_login(client)
    headers = auth_headers(user["access_token"])
    client.put("/profile", json=valid_profile_payload(), headers=headers)
    _walk(client, headers, before=150, after=120)

    wrong = client.post("/auth/delete-account", json={"password": "not the password"}, headers=headers)
    assert wrong.status_code == 401
    gone = client.post("/auth/delete-account", json={"password": STRONG_PASSWORD}, headers=headers)
    assert gone.status_code == 204

    for table in ("users", "health_profiles", "activity_recommendations", "activity_sessions"):
        assert _rows(table) == 1, table  # only the other person's row is left
    assert client.post("/auth/login", json={"email": user["email"], "password": STRONG_PASSWORD}).status_code == 401
    assert client.get("/progress", headers=headers).status_code == 401
    assert client.get("/progress", headers=keep).json()["sessions_completed"] == 1


def test_deleting_needs_a_signed_in_person(client):
    assert client.post("/auth/delete-account", json={"password": "x"}).status_code == 401
