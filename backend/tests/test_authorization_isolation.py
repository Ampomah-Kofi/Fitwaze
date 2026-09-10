"""Cross-user authorization isolation tests.

CRITICAL security test file: creates two independent users (A and B) and
proves that user B can never read/write/delete user A's data through any
endpoint that scopes data by the authenticated user. Every protected router
handler must filter queries by `current_user.id` from the verified JWT —
never by a client-supplied user id — so every case here should resolve to
401/403/404, never 200.

This file is scaffolded now (auth only) and extended in later steps as the
profile, activity, and route/progress resources are built, per the approved
build plan.
"""
from __future__ import annotations

import pytest

from tests.conftest import auth_headers, register_and_login, valid_profile_payload


@pytest.fixture
def two_users(client):
    user_a = register_and_login(client)
    # Use a second TestClient-independent call on the same app/db but track
    # tokens separately per user; the shared `client` fixture's cookie jar is
    # fine here because we only exercise Authorization-header-based auth for
    # cross-user checks (refresh-cookie flows are covered in test_auth.py).
    user_b = register_and_login(client)
    return user_a, user_b


def test_users_get_distinct_ids_and_tokens(two_users):
    user_a, user_b = two_users
    assert user_a["user_id"] != user_b["user_id"]
    assert user_a["access_token"] != user_b["access_token"]


def test_user_b_cannot_read_user_a_profile_via_me_endpoint(client, two_users):
    """Sanity check that /auth/me is scoped to the caller's own token, never
    to any other identity — the foundation every other isolation test
    (profile/activity/route, added in later steps) builds on."""
    user_a, user_b = two_users

    resp_a = client.get("/auth/me", headers=auth_headers(user_a["access_token"]))
    resp_b = client.get("/auth/me", headers=auth_headers(user_b["access_token"]))

    assert resp_a.status_code == 200
    assert resp_b.status_code == 200
    assert resp_a.json()["id"] == user_a["user_id"]
    assert resp_b.json()["id"] == user_b["user_id"]
    assert resp_a.json()["id"] != resp_b.json()["id"]


def test_user_a_token_cannot_be_used_after_tampering(client, two_users):
    """A token for user A, tampered with, must not resolve to user B (or
    anyone) — guards against naive "trust the payload" implementations."""
    user_a, _user_b = two_users
    tampered_token = user_a["access_token"][:-1] + (
        "A" if user_a["access_token"][-1] != "A" else "B"
    )
    resp = client.get("/auth/me", headers=auth_headers(tampered_token))
    assert resp.status_code == 401


def test_profile_endpoints_are_scoped_to_the_caller_not_a_client_supplied_id(client, two_users):
    """/profile has no id in its path at all -- it is always resolved from
    the verified JWT. This proves user B's requests against /profile only
    ever touch user B's own row, even after user A has created a profile."""
    user_a, user_b = two_users

    create_a = client.put(
        "/profile", json=valid_profile_payload(age=50), headers=auth_headers(user_a["access_token"])
    )
    assert create_a.status_code == 200
    assert create_a.json()["user_id"] == user_a["user_id"]

    # User B has no profile yet -- GET must 404, never leak user A's profile.
    get_b = client.get("/profile", headers=auth_headers(user_b["access_token"]))
    assert get_b.status_code == 404

    # User B creates their own profile with different data.
    create_b = client.put(
        "/profile", json=valid_profile_payload(age=22), headers=auth_headers(user_b["access_token"])
    )
    assert create_b.status_code == 200
    assert create_b.json()["user_id"] == user_b["user_id"]

    # Each user's GET must return only their own data.
    get_a_again = client.get("/profile", headers=auth_headers(user_a["access_token"]))
    assert get_a_again.json()["age"] == 50
    get_b_again = client.get("/profile", headers=auth_headers(user_b["access_token"]))
    assert get_b_again.json()["age"] == 22


def test_profile_export_never_includes_other_users_data(client, two_users):
    user_a, user_b = two_users
    client.put(
        "/profile",
        json=valid_profile_payload(diabetes_status="type2"),
        headers=auth_headers(user_a["access_token"]),
    )

    export_b = client.get("/profile/export", headers=auth_headers(user_b["access_token"]))
    assert export_b.status_code == 200
    assert export_b.json()["user"]["id"] == user_b["user_id"]
    assert export_b.json()["health_profile"] is None


def test_user_b_deleting_own_account_does_not_affect_user_a(client, two_users):
    user_a, user_b = two_users
    client.put("/profile", json=valid_profile_payload(), headers=auth_headers(user_a["access_token"]))
    client.put("/profile", json=valid_profile_payload(), headers=auth_headers(user_b["access_token"]))

    del_resp = client.delete("/profile/delete", headers=auth_headers(user_b["access_token"]))
    assert del_resp.status_code == 204

    # User A is completely unaffected.
    still_there = client.get("/profile", headers=auth_headers(user_a["access_token"]))
    assert still_there.status_code == 200


def test_user_b_cannot_get_route_options_for_user_as_recommendation(client, two_users):
    """User B must not be able to generate route options against a
    recommendation id that belongs to user A."""
    user_a, user_b = two_users

    client.put("/profile", json=valid_profile_payload(), headers=auth_headers(user_a["access_token"]))
    rec_resp = client.post("/activity/recommendation", headers=auth_headers(user_a["access_token"]))
    assert rec_resp.status_code == 200
    recommendation_id = rec_resp.json()["id"]

    resp = client.post(
        "/route/options",
        json={
            "activity_recommendation_id": recommendation_id,
            "latitude": 40.0,
            "longitude": -74.0,
        },
        headers=auth_headers(user_b["access_token"]),
    )
    assert resp.status_code in (403, 404)

    # The rightful owner can still use their own recommendation.
    own_resp = client.post(
        "/route/options",
        json={
            "activity_recommendation_id": recommendation_id,
            "latitude": 40.0,
            "longitude": -74.0,
        },
        headers=auth_headers(user_a["access_token"]),
    )
    assert own_resp.status_code == 200


# NOTE: additional cases covering
#   - POST /route/select
#   - GET /progress
# using user A's known resource IDs from user B's session are added in a
# later step of the build plan once those routers/resources exist.
