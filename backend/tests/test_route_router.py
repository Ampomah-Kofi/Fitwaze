"""Integration tests for POST /route/options."""
from __future__ import annotations

from tests.conftest import auth_headers, register_and_login, valid_profile_payload


def _create_recommendation(client, token: str) -> str:
    client.put("/profile", json=valid_profile_payload(), headers=auth_headers(token))
    resp = client.post("/activity/recommendation", headers=auth_headers(token))
    return resp.json()["id"]


def test_route_options_returns_up_to_three_scored_candidates(client):
    user = register_and_login(client)
    recommendation_id = _create_recommendation(client, user["access_token"])

    resp = client.post(
        "/route/options",
        json={
            "activity_recommendation_id": recommendation_id,
            "latitude": 40.7128,
            "longitude": -74.0060,
        },
        headers=auth_headers(user["access_token"]),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert 2 <= len(body["options"]) <= 3
    for option in body["options"]:
        assert 0 <= option["score"] <= 100
        assert option["geometry"]
        assert option["explanation"]


def test_route_options_404_for_nonexistent_recommendation(client):
    user = register_and_login(client)
    fake_id = "00000000-0000-0000-0000-000000000000"
    resp = client.post(
        "/route/options",
        json={"activity_recommendation_id": fake_id, "latitude": 40.0, "longitude": -74.0},
        headers=auth_headers(user["access_token"]),
    )
    assert resp.status_code == 404


def test_route_options_requires_authentication(client):
    resp = client.post(
        "/route/options",
        json={
            "activity_recommendation_id": "00000000-0000-0000-0000-000000000000",
            "latitude": 40.0,
            "longitude": -74.0,
        },
    )
    assert resp.status_code == 401
