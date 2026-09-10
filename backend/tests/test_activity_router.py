"""Integration tests for POST /activity/recommendation."""
from __future__ import annotations

from tests.conftest import auth_headers, register_and_login, valid_profile_payload


def test_recommendation_requires_profile_first(client):
    user = register_and_login(client)
    resp = client.post("/activity/recommendation", headers=auth_headers(user["access_token"]))
    assert resp.status_code == 404


def test_recommendation_created_after_profile_exists(client):
    user = register_and_login(client)
    client.put("/profile", json=valid_profile_payload(), headers=auth_headers(user["access_token"]))

    resp = client.post("/activity/recommendation", headers=auth_headers(user["access_token"]))
    assert resp.status_code == 200
    body = resp.json()
    assert body["activity_type"] in ("walk", "cycle")
    assert body["duration_minutes"] > 0
    assert "not medical advice" in body["disclaimer"].lower()


def test_recommendation_requires_authentication(client):
    resp = client.post("/activity/recommendation")
    assert resp.status_code == 401
