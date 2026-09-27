"""Calorie estimates from METs."""
from __future__ import annotations

import pytest

from app.engines.calories import estimate_calories, met_for
from app.models.enums import ActivityTypeEnum
from tests.conftest import auth_headers, register_and_login, valid_profile_payload

WALK, CYCLE = ActivityTypeEnum.walk, ActivityTypeEnum.cycle


@pytest.mark.parametrize("mph, met", [(2.0, 2.8), (3.0, 3.5), (4.0, 4.3)])
def test_walking_mets_by_pace(mph, met):
    assert met_for(WALK, mph) == met


def test_worked_example():
    # 180 lb (81.6 kg), 20 minutes at 3 mph (1609 m): 3.5 x 81.6 x 1/3 = 95 kcal.
    assert estimate_calories(WALK, 20, 81.6, 1609.3) == 95


def test_cycling_burns_more_per_minute_than_walking():
    assert estimate_calories(CYCLE, 20, 80, 5500) > estimate_calories(WALK, 20, 80, 1600)


def test_no_weight_no_estimate():
    assert estimate_calories(WALK, 20, None) is None


def test_progress_reports_calories(client):
    headers = auth_headers(register_and_login(client)["access_token"])
    client.put("/profile", json=valid_profile_payload(weight_kg=90), headers=headers)
    rec = client.post("/activity/recommendation", headers=headers).json()
    start = {"activity_recommendation_id": rec["id"], "latitude": 33.5186, "longitude": -86.8104}
    option = client.post("/route/options", json=start, headers=headers).json()["options"][0]
    session = client.post("/route/select", json={**start, "candidate_label": option["label"],
                                                 "candidate_revision": option["candidate_revision"]}, headers=headers).json()
    client.patch(f"/route/sessions/{session['id']}", json={"status": "completed"}, headers=headers)
    body = client.get("/progress", headers=headers).json()
    assert body["total_calories"] > 0
    assert body["recent_sessions"][0]["calories"] == body["total_calories"]


def test_progress_prefers_what_gps_measured(client):
    headers = auth_headers(register_and_login(client)["access_token"])
    client.put("/profile", json=valid_profile_payload(weight_kg=80), headers=headers)
    rec = client.post("/activity/recommendation", headers=headers).json()
    start = {"activity_recommendation_id": rec["id"], "latitude": 33.5186, "longitude": -86.8104}
    option = client.post("/route/options", json=start, headers=headers).json()["options"][0]
    session = client.post("/route/select", json={**start, "candidate_label": option["label"],
                                                 "candidate_revision": option["candidate_revision"]}, headers=headers).json()
    done = client.patch(f"/route/sessions/{session['id']}", headers=headers,
                        json={"status": "completed", "measured_minutes": 27.4, "measured_distance_m": 2100})
    assert done.json()["measured_minutes"] == 27.4
    body = client.get("/progress", headers=headers).json()
    assert body["total_active_minutes"] == 27
    assert body["total_distance_m"] == 2100
    assert body["recent_sessions"][0]["measured"] is True


def test_implausible_measurements_are_rejected(client):
    from app.schemas.route import RouteSessionUpdateRequest
    with pytest.raises(ValueError):
        RouteSessionUpdateRequest(status="completed", measured_minutes=-3)
