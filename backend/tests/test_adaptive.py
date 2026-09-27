"""Today's plan adapts to how recent walks went."""
from __future__ import annotations

from app.engines.activity_engine import RecentActivity, recommend_activity
from tests.conftest import auth_headers, register_and_login, valid_profile_payload
from tests.test_activity_engine import make_profile

EASY = RecentActivity("completed", "easy")
RIGHT = RecentActivity("completed", "just_right")
HARD = RecentActivity("completed", "hard")
QUIT = RecentActivity("abandoned")


def _minutes(recent, **profile):
    return recommend_activity(make_profile(**profile), recent=recent).duration_minutes


def test_two_comfortable_walks_add_a_little():
    base = _minutes([])
    assert _minutes([EASY, RIGHT]) == base + max(2, round(base * 0.1))


def test_one_comfortable_walk_is_not_enough_to_increase():
    assert _minutes([EASY]) == _minutes([])


def test_a_hard_walk_shortens_the_next():
    assert _minutes([HARD, EASY]) < _minutes([])


def test_an_abandoned_walk_holds_steady_and_says_so():
    result = recommend_activity(make_profile(), recent=[QUIT, EASY])
    assert result.duration_minutes == _minutes([])
    assert "ended early" in result.rationale


def test_increases_are_capped():
    assert _minutes([EASY, EASY], current_weekly_minutes=300) <= 60


def _walk(client, headers, effort):
    rec = client.post("/activity/recommendation", headers=headers).json()
    start = {"activity_recommendation_id": rec["id"], "latitude": 33.5186, "longitude": -86.8104}
    option = client.post("/route/options", json=start, headers=headers).json()["options"][0]
    session = client.post("/route/select", json={**start, "candidate_label": option["label"],
                                                 "candidate_revision": option["candidate_revision"]}, headers=headers).json()
    client.patch(f"/route/sessions/{session['id']}", json={"status": "completed", "effort": effort}, headers=headers)
    return rec["duration_minutes"]


def test_the_api_uses_the_callers_own_history(client):
    headers = auth_headers(register_and_login(client)["access_token"])
    client.put("/profile", json=valid_profile_payload(), headers=headers)
    first = _walk(client, headers, "easy")
    _walk(client, headers, "just_right")
    after = client.post("/activity/recommendation", headers=headers).json()
    assert after["duration_minutes"] > first
    assert "added a few minutes" in after["rationale"]

    other = auth_headers(register_and_login(client)["access_token"])
    client.put("/profile", json=valid_profile_payload(), headers=other)
    assert client.post("/activity/recommendation", headers=other).json()["duration_minutes"] == first
