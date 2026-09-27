"""Saved home location: routes start and end where the person lives."""
from __future__ import annotations

from sqlalchemy import text

from tests.conftest import TestingSessionLocal, auth_headers, register_and_login, valid_profile_payload

HOME = {"latitude": 5.6037, "longitude": -0.187}


def _user(client, with_profile=True):
    headers = auth_headers(register_and_login(client)["access_token"])
    if with_profile:
        client.put("/profile", json=valid_profile_payload(), headers=headers)
    return headers


def test_home_round_trips(client):
    headers = _user(client)
    assert client.get("/profile/home", headers=headers).status_code == 404
    assert client.put("/profile/home", json=HOME, headers=headers).json() == HOME
    assert client.get("/profile/home", headers=headers).json() == HOME


def test_home_survives_a_profile_update(client):
    headers = _user(client)
    client.put("/profile/home", json=HOME, headers=headers)
    client.put("/profile", json=valid_profile_payload(age=50), headers=headers)
    assert client.get("/profile/home", headers=headers).json() == HOME


def test_home_can_be_cleared(client):
    headers = _user(client)
    client.put("/profile/home", json=HOME, headers=headers)
    assert client.delete("/profile/home", headers=headers).status_code == 204
    assert client.get("/profile/home", headers=headers).status_code == 404


def test_home_needs_a_profile(client):
    assert client.put("/profile/home", json=HOME, headers=_user(client, with_profile=False)).status_code == 404


def test_home_is_validated(client):
    headers = _user(client)
    assert client.put("/profile/home", json={"latitude": 91, "longitude": 0}, headers=headers).status_code == 422


def test_home_is_private_to_its_owner(client):
    alice, bob = _user(client), _user(client)
    client.put("/profile/home", json=HOME, headers=alice)
    assert client.get("/profile/home", headers=bob).status_code == 404


def test_home_is_encrypted_at_rest(client):
    headers = _user(client)
    client.put("/profile/home", json=HOME, headers=headers)
    with TestingSessionLocal() as db:
        stored = db.execute(text("SELECT home_latitude, home_longitude FROM health_profiles")).all()
    assert stored and all("5.6037" not in (lat or "") and "-0.187" not in (lon or "") for lat, lon in stored)


def test_home_is_in_the_export(client):
    headers = _user(client)
    client.put("/profile/home", json=HOME, headers=headers)
    assert client.get("/profile/export", headers=headers).json()["home_location"] == HOME
