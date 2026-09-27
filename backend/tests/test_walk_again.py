"""Walking a route again: past routes near the start that fit today's plan."""
from __future__ import annotations

from tests.conftest import auth_headers, register_and_login, valid_profile_payload

HOME = {"latitude": 33.5186, "longitude": -86.8104}
FAR = {"latitude": 33.60, "longitude": -86.70}


def _user(client, **profile):
    headers = auth_headers(register_and_login(client)["access_token"])
    client.put("/profile", json=valid_profile_payload(**profile), headers=headers)
    return headers


def _plan(client, headers, **body):
    return client.post("/activity/recommendation", json=body or None, headers=headers).json()


def _walk(client, headers, rec, where=HOME, status="completed"):
    start = {"activity_recommendation_id": rec["id"], **where}
    option = client.post("/route/options", json=start, headers=headers).json()["options"][0]
    session = client.post("/route/select", json={**start, "candidate_label": option["label"],
                                                 "candidate_revision": option["candidate_revision"]},
                          headers=headers).json()
    client.patch(f"/route/sessions/{session['id']}", json={"status": status}, headers=headers)
    return session


def test_a_finished_route_is_offered_again_near_home_and_can_be_started(client):
    headers = _user(client)
    first = _walk(client, headers, _plan(client, headers))
    _walk(client, headers, _plan(client, headers), status="abandoned")
    today = _plan(client, headers)
    past = client.post("/route/past", json={"activity_recommendation_id": today["id"], **HOME}, headers=headers).json()
    assert [p["session_id"] for p in past] == [first["id"]]
    assert past[0]["times_done"] == 1 and past[0]["geometry"]

    again = client.post("/route/repeat", json={"activity_recommendation_id": today["id"],
                                               "session_id": first["id"]}, headers=headers)
    assert again.status_code == 201
    body = again.json()
    assert body["status"] == "selected" and body["activity_recommendation_id"] == today["id"]
    assert body["route_geometry"] == first["route_geometry"] and body["distance_m"] == first["distance_m"]


def test_the_same_loop_walked_twice_is_listed_once_with_a_count(client):
    headers = _user(client)
    _walk(client, headers, _plan(client, headers))
    _walk(client, headers, _plan(client, headers))
    past = client.post("/route/past", json={"activity_recommendation_id": _plan(client, headers)["id"], **HOME},
                       headers=headers).json()
    assert len(past) == 1 and past[0]["times_done"] == 2


def test_routes_from_somewhere_else_are_not_offered(client):
    headers = _user(client)
    _walk(client, headers, _plan(client, headers), where=FAR)
    past = client.post("/route/past", json={"activity_recommendation_id": _plan(client, headers)["id"], **HOME},
                       headers=headers).json()
    assert past == []


def test_a_route_longer_than_todays_plan_is_not_offered(client):
    headers = _user(client)
    long_walk = _walk(client, headers, _plan(client, headers, preferred_minutes=45))
    short_day = _plan(client, headers, preferred_minutes=10)
    past = client.post("/route/past", json={"activity_recommendation_id": short_day["id"], **HOME},
                       headers=headers).json()
    assert past == []
    refused = client.post("/route/repeat", json={"activity_recommendation_id": short_day["id"],
                                                 "session_id": long_walk["id"]}, headers=headers)
    assert refused.status_code == 409 and "longer than today's plan" in refused.json()["detail"]


def test_a_ride_is_not_offered_for_a_walking_day(client):
    headers = _user(client)
    ride = _walk(client, headers, _plan(client, headers, activity_type="cycle"))
    walk_day = _plan(client, headers, activity_type="walk")
    assert client.post("/route/past", json={"activity_recommendation_id": walk_day["id"], **HOME},
                       headers=headers).json() == []
    assert client.post("/route/repeat", json={"activity_recommendation_id": walk_day["id"],
                                              "session_id": ride["id"]}, headers=headers).status_code == 409


def test_nobody_can_repeat_or_see_another_persons_routes(client):
    owner = _user(client)
    walk = _walk(client, owner, _plan(client, owner))
    other = _user(client)
    other_plan = _plan(client, other)
    assert client.post("/route/past", json={"activity_recommendation_id": other_plan["id"], **HOME},
                       headers=other).json() == []
    assert client.post("/route/repeat", json={"activity_recommendation_id": other_plan["id"],
                                              "session_id": walk["id"]}, headers=other).status_code == 404
