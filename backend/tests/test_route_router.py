"""Integration tests for the /route endpoints."""
from __future__ import annotations

import pytest

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
    # The response says which provider produced the candidates, so a client can
    # tell real routes from the synthetic ones the mock provider generates.
    assert body["provider"] == "mock"
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


def _select_route(client, token: str, recommendation_id: str, label: str = "large_loop"):
    return client.post(
        "/route/select",
        json={
            "activity_recommendation_id": recommendation_id,
            "latitude": 40.7128,
            "longitude": -74.0060,
            "candidate_label": label,
        },
        headers=auth_headers(token),
    )


def test_route_select_persists_the_named_candidate(client):
    user = register_and_login(client)
    recommendation_id = _create_recommendation(client, user["access_token"])

    options = client.post(
        "/route/options",
        json={
            "activity_recommendation_id": recommendation_id,
            "latitude": 40.7128,
            "longitude": -74.0060,
        },
        headers=auth_headers(user["access_token"]),
    ).json()["options"]
    offered = {option["label"]: option for option in options}
    label = next(iter(offered))

    resp = _select_route(client, user["access_token"], recommendation_id, label)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "selected"
    assert body["completed_at"] is None
    # Distance and score breakdown come from the engine, not the request.
    assert body["distance_m"] == offered[label]["distance_m"]
    assert body["score_breakdown"] == offered[label]["score_breakdown"]

    fetched = client.get(
        f"/route/sessions/{body['id']}", headers=auth_headers(user["access_token"])
    )
    assert fetched.status_code == 200
    assert fetched.json()["id"] == body["id"]


def test_route_select_stores_coarsened_geometry(client):
    user = register_and_login(client)
    recommendation_id = _create_recommendation(client, user["access_token"])

    body = _select_route(client, user["access_token"], recommendation_id).json()
    geometry = body["route_geometry"]
    assert geometry
    for lat, lon in geometry:
        assert round(lat, 4) == lat
        assert round(lon, 4) == lon


def test_route_select_rejects_unknown_candidate_label(client):
    user = register_and_login(client)
    recommendation_id = _create_recommendation(client, user["access_token"])

    resp = _select_route(client, user["access_token"], recommendation_id, "teleport")
    assert resp.status_code == 422


def test_route_select_404_for_nonexistent_recommendation(client):
    user = register_and_login(client)
    resp = _select_route(
        client, user["access_token"], "00000000-0000-0000-0000-000000000000"
    )
    assert resp.status_code == 404


def test_session_can_be_completed_once(client):
    user = register_and_login(client)
    recommendation_id = _create_recommendation(client, user["access_token"])
    session_id = _select_route(client, user["access_token"], recommendation_id).json()["id"]

    resp = client.patch(
        f"/route/sessions/{session_id}",
        json={"status": "completed"},
        headers=auth_headers(user["access_token"]),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"
    assert resp.json()["completed_at"] is not None

    # Terminal states are final: a replayed request must not double-count.
    again = client.patch(
        f"/route/sessions/{session_id}",
        json={"status": "completed"},
        headers=auth_headers(user["access_token"]),
    )
    assert again.status_code == 409


def test_session_can_be_abandoned(client):
    user = register_and_login(client)
    recommendation_id = _create_recommendation(client, user["access_token"])
    session_id = _select_route(client, user["access_token"], recommendation_id).json()["id"]

    resp = client.patch(
        f"/route/sessions/{session_id}",
        json={"status": "abandoned"},
        headers=auth_headers(user["access_token"]),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "abandoned"
    assert resp.json()["completed_at"] is None


def test_session_status_must_be_a_terminal_state(client):
    user = register_and_login(client)
    recommendation_id = _create_recommendation(client, user["access_token"])
    session_id = _select_route(client, user["access_token"], recommendation_id).json()["id"]

    resp = client.patch(
        f"/route/sessions/{session_id}",
        json={"status": "selected"},
        headers=auth_headers(user["access_token"]),
    )
    assert resp.status_code == 422


def test_route_select_requires_authentication(client):
    resp = client.post(
        "/route/select",
        json={
            "activity_recommendation_id": "00000000-0000-0000-0000-000000000000",
            "latitude": 40.0,
            "longitude": -74.0,
            "candidate_label": "small_loop",
        },
    )
    assert resp.status_code == 401


def test_gpx_export_returns_the_stored_route(client):
    user = register_and_login(client)
    recommendation_id = _create_recommendation(client, user["access_token"])
    body = _select_route(client, user["access_token"], recommendation_id).json()

    resp = client.get(
        f"/route/sessions/{body['id']}/gpx", headers=auth_headers(user["access_token"])
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/gpx+xml")
    assert "attachment" in resp.headers["content-disposition"]

    gpx = resp.text
    assert gpx.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    # One <trkpt> per stored geometry point, and it must parse as real XML.
    assert gpx.count("<trkpt") == len(body["route_geometry"])

    import xml.etree.ElementTree as ET

    root = ET.fromstring(gpx)
    points = root.findall(".//{http://www.topografix.com/GPX/1/1}trkpt")
    assert len(points) == len(body["route_geometry"])
    first_lat, first_lon = body["route_geometry"][0]
    assert float(points[0].get("lat")) == pytest.approx(first_lat, abs=1e-6)
    assert float(points[0].get("lon")) == pytest.approx(first_lon, abs=1e-6)


def test_gpx_export_is_scoped_to_the_owner(client):
    owner = register_and_login(client)
    other = register_and_login(client)
    recommendation_id = _create_recommendation(client, owner["access_token"])
    session_id = _select_route(client, owner["access_token"], recommendation_id).json()["id"]

    resp = client.get(
        f"/route/sessions/{session_id}/gpx", headers=auth_headers(other["access_token"])
    )
    assert resp.status_code == 404


def test_gpx_export_requires_authentication(client):
    resp = client.get("/route/sessions/00000000-0000-0000-0000-000000000000/gpx")
    assert resp.status_code == 401


def test_unsuitable_routes_are_withheld_with_a_reason(client):
    """A walker who cannot manage steps is not offered the stair-heavy loops the
    mock provider generates, and is told why they are missing."""
    user = register_and_login(client)
    client.put(
        "/profile",
        json=valid_profile_payload(mobility_limitations="severe", walking_ability="limited"),
        headers=auth_headers(user["access_token"]),
    )
    recommendation_id = client.post(
        "/activity/recommendation", headers=auth_headers(user["access_token"])
    ).json()["id"]

    body = client.post(
        "/route/options",
        json={
            "activity_recommendation_id": recommendation_id,
            "latitude": 40.7128,
            "longitude": -74.0060,
        },
        headers=auth_headers(user["access_token"]),
    ).json()

    offered = {option["label"] for option in body["options"]}
    withheld = {item["label"] for item in body["excluded"]}
    assert withheld, "expected the stair-heavy candidates to be withheld"
    assert offered.isdisjoint(withheld)
    for item in body["excluded"]:
        assert item["reason"]

    # Asking for a withheld route by name must be refused, not quietly honoured.
    refused = client.post(
        "/route/select",
        json={
            "activity_recommendation_id": recommendation_id,
            "latitude": 40.7128,
            "longitude": -74.0060,
            "candidate_label": next(iter(withheld)),
        },
        headers=auth_headers(user["access_token"]),
    )
    assert refused.status_code == 409
    assert "not offered" in refused.json()["detail"]


def test_an_unrestricted_walker_is_offered_everything(client):
    user = register_and_login(client)
    recommendation_id = _create_recommendation(client, user["access_token"])
    body = client.post(
        "/route/options",
        json={
            "activity_recommendation_id": recommendation_id,
            "latitude": 40.7128,
            "longitude": -74.0060,
        },
        headers=auth_headers(user["access_token"]),
    ).json()
    assert body["excluded"] == []
    assert len(body["options"]) == 3
