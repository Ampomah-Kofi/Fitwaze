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

from tests.conftest import auth_headers, register_and_login


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


# NOTE: additional cases covering
#   - GET/PUT /profile, GET /profile/export, DELETE /profile/delete
#   - POST /route/options, POST /route/select
#   - GET /progress
# using user A's known resource IDs from user B's session are added in
# later steps of the build plan once those routers exist (see
# test_profile.py-equivalent isolation cases inline below, and route/
# progress isolation cases added alongside the route engine work).
