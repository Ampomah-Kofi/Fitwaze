"""Tests for health profile CRUD, encryption at rest, export, and delete."""
from __future__ import annotations

from sqlalchemy import inspect, text

from tests.conftest import (
    TestingSessionLocal,
    auth_headers,
    register_and_login,
    valid_profile_payload,
)


def test_get_profile_404_when_not_created(client):
    user = register_and_login(client)
    resp = client.get("/profile", headers=auth_headers(user["access_token"]))
    assert resp.status_code == 404


def test_put_profile_creates_and_returns_profile(client):
    user = register_and_login(client)
    resp = client.put(
        "/profile", json=valid_profile_payload(), headers=auth_headers(user["access_token"])
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["age"] == 34
    assert body["height_cm"] == 165.5
    assert body["weight_kg"] == 68.2
    assert body["diabetes_status"] == "none"
    assert body["user_id"] == user["user_id"]


def test_put_profile_is_idempotent_upsert(client):
    user = register_and_login(client)
    client.put("/profile", json=valid_profile_payload(), headers=auth_headers(user["access_token"]))
    resp = client.put(
        "/profile",
        json=valid_profile_payload(age=40, current_weekly_minutes=90),
        headers=auth_headers(user["access_token"]),
    )
    assert resp.status_code == 200
    assert resp.json()["age"] == 40
    assert resp.json()["current_weekly_minutes"] == 90

    get_resp = client.get("/profile", headers=auth_headers(user["access_token"]))
    assert get_resp.json()["age"] == 40


def test_sensitive_fields_are_ciphertext_in_the_database(client):
    user = register_and_login(client)
    client.put(
        "/profile",
        json=valid_profile_payload(diabetes_status="type2", height_cm=180.0),
        headers=auth_headers(user["access_token"]),
    )

    db = TestingSessionLocal()
    try:
        row = db.execute(
            text(
                "SELECT height_cm, weight_kg, diabetes_status, mobility_limitations, "
                "walking_ability, cycling_ability FROM health_profiles WHERE user_id = :uid"
            ),
            {"uid": user["user_id"]},
        ).fetchone()
        assert row is not None
        raw_height_cm, raw_weight_kg, raw_diabetes, raw_mobility, raw_walk, raw_cycle = row

        # None of the raw stored values should equal (or even contain) the
        # plaintext we sent — they must be opaque base64 ciphertext.
        assert "180" not in raw_height_cm
        assert raw_diabetes != "type2"
        assert "type2" not in raw_diabetes
        assert raw_mobility != "none" or len(raw_mobility) > len("none")
        for raw_value in (raw_height_cm, raw_weight_kg, raw_diabetes, raw_mobility, raw_walk, raw_cycle):
            # Ciphertext is base64; plaintext values here are short alnum
            # strings, so a much longer, base64-charset string is a good
            # proxy check that encryption actually happened.
            assert len(raw_value) > 20
    finally:
        db.close()


def test_profile_export_returns_decrypted_data_to_owner(client):
    user = register_and_login(client)
    client.put("/profile", json=valid_profile_payload(), headers=auth_headers(user["access_token"]))

    resp = client.get("/profile/export", headers=auth_headers(user["access_token"]))
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["id"] == user["user_id"]
    assert body["health_profile"]["diabetes_status"] == "none"
    assert body["health_profile"]["height_cm"] == 165.5


def test_delete_profile_removes_user_and_profile(client):
    user = register_and_login(client)
    client.put("/profile", json=valid_profile_payload(), headers=auth_headers(user["access_token"]))

    del_resp = client.delete("/profile/delete", headers=auth_headers(user["access_token"]))
    assert del_resp.status_code == 204

    # The access token's underlying user no longer exists -> 401 on reuse.
    me_resp = client.get("/auth/me", headers=auth_headers(user["access_token"]))
    assert me_resp.status_code == 401

    db = TestingSessionLocal()
    try:
        row = db.execute(
            text("SELECT 1 FROM health_profiles WHERE user_id = :uid"),
            {"uid": user["user_id"]},
        ).fetchone()
        assert row is None
        user_row = db.execute(
            text("SELECT 1 FROM users WHERE id = :uid"), {"uid": user["user_id"]}
        ).fetchone()
        assert user_row is None
    finally:
        db.close()


def test_profile_requires_authentication(client):
    resp = client.get("/profile")
    assert resp.status_code == 401
    resp2 = client.put("/profile", json=valid_profile_payload())
    assert resp2.status_code == 401
    resp3 = client.get("/profile/export")
    assert resp3.status_code == 401
    resp4 = client.delete("/profile/delete")
    assert resp4.status_code == 401
