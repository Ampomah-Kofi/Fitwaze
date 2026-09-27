"""Integration tests for GET /progress."""
from __future__ import annotations

from tests.conftest import auth_headers, register_and_login, valid_profile_payload


def _selected_session(client, token: str, label: str) -> dict:
    client.put("/profile", json=valid_profile_payload(), headers=auth_headers(token))
    recommendation_id = client.post(
        "/activity/recommendation", headers=auth_headers(token)
    ).json()["id"]
    resp = client.post(
        "/route/select",
        json={
            "activity_recommendation_id": recommendation_id,
            "latitude": 40.7128,
            "longitude": -74.0060,
            "candidate_label": label,
        },
        headers=auth_headers(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_progress_is_empty_for_a_new_user(client):
    user = register_and_login(client)
    resp = client.get("/progress", headers=auth_headers(user["access_token"]))
    assert resp.status_code == 200
    body = resp.json()
    assert body["sessions_selected"] == 0
    assert body["sessions_completed"] == 0
    assert body["completion_rate"] == 0.0
    assert body["current_streak_days"] == 0
    assert body["recent_sessions"] == []


def test_progress_counts_only_completed_sessions_in_totals(client):
    user = register_and_login(client)
    token = user["access_token"]

    completed = _selected_session(client, token, "large_loop")
    _selected_session(client, token, "small_loop")  # left in `selected`
    abandoned = _selected_session(client, token, "out_and_back")

    client.patch(
        f"/route/sessions/{completed['id']}",
        json={"status": "completed"},
        headers=auth_headers(token),
    )
    client.patch(
        f"/route/sessions/{abandoned['id']}",
        json={"status": "abandoned"},
        headers=auth_headers(token),
    )

    body = client.get("/progress", headers=auth_headers(token)).json()
    assert body["sessions_selected"] == 3
    assert body["sessions_completed"] == 1
    assert body["sessions_abandoned"] == 1
    assert body["completion_rate"] == round(1 / 3, 3)
    assert body["total_distance_m"] == completed["distance_m"]
    assert body["total_active_minutes"] == completed["estimated_minutes"]
    assert body["last_7_days_minutes"] == completed["estimated_minutes"]
    assert body["current_streak_days"] == 1
    assert len(body["recent_sessions"]) == 3
    assert {s["activity_type"] for s in body["recent_sessions"]} == {"walk"}


def test_progress_recent_sessions_are_capped_and_newest_first(client):
    user = register_and_login(client)
    token = user["access_token"]
    for _ in range(6):
        _selected_session(client, token, "small_loop")

    body = client.get("/progress", headers=auth_headers(token)).json()
    assert body["sessions_selected"] == 6
    assert len(body["recent_sessions"]) == 5
    created = [s["created_at"] for s in body["recent_sessions"]]
    assert created == sorted(created, reverse=True)


def test_progress_requires_authentication(client):
    assert client.get("/progress").status_code == 401


def test_streak_days_follow_the_callers_time_zone():
    from datetime import date, datetime, timezone
    from app.routers.progress import _current_streak_days, _zone

    # 8:30 pm in Birmingham on Sept 26 is 01:30 UTC on Sept 27.
    evening = datetime(2026, 9, 27, 1, 30, tzinfo=timezone.utc)
    chicago = _zone("America/Chicago")
    assert evening.astimezone(chicago).date() == date(2026, 9, 26)
    assert _current_streak_days({evening.astimezone(chicago).date()}, date(2026, 9, 26)) == 1
    assert _zone("Not/AZone") is timezone.utc and _zone(None) is timezone.utc
