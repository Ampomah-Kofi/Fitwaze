"""Tests for the auth vertical slice: register/login/refresh/logout, Argon2id
hashing, account lockout, and refresh token rotation + reuse detection."""
from __future__ import annotations

from app.models.user import User
from app.security.rate_limit import limiter
from tests.conftest import STRONG_PASSWORD, TestingSessionLocal, auth_headers, unique_email


def test_health_check(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_register_returns_access_token_and_sets_refresh_cookie(client):
    email = unique_email()
    resp = client.post("/auth/register", json={"email": email, "password": STRONG_PASSWORD})
    assert resp.status_code == 201
    body = resp.json()
    assert "access_token" in body
    assert body["user"]["email"] == email
    assert "password" not in body["user"]
    assert "password_hash" not in body["user"]
    assert "fitwaze_refresh_token" in resp.cookies


def test_password_is_hashed_with_argon2id(client):
    email = unique_email()
    client.post("/auth/register", json={"email": email, "password": STRONG_PASSWORD})

    db = TestingSessionLocal()
    try:
        user = db.query(User).filter(User.email == email).one()
        assert user.password_hash.startswith("$argon2id$")
        assert user.password_hash != STRONG_PASSWORD
    finally:
        db.close()


def test_duplicate_registration_rejected(client):
    email = unique_email()
    first = client.post("/auth/register", json={"email": email, "password": STRONG_PASSWORD})
    assert first.status_code == 201

    second = client.post("/auth/register", json={"email": email, "password": STRONG_PASSWORD})
    assert second.status_code == 409


def test_login_success(client):
    email = unique_email()
    client.post("/auth/register", json={"email": email, "password": STRONG_PASSWORD})

    resp = client.post("/auth/login", json={"email": email, "password": STRONG_PASSWORD})
    assert resp.status_code == 200
    assert "access_token" in resp.json()


def test_login_wrong_password_rejected(client):
    email = unique_email()
    client.post("/auth/register", json={"email": email, "password": STRONG_PASSWORD})

    resp = client.post("/auth/login", json={"email": email, "password": "totally-wrong-pw"})
    assert resp.status_code == 401


def test_login_unknown_email_same_error_as_wrong_password(client):
    resp = client.post(
        "/auth/login", json={"email": unique_email(), "password": "whatever-1234"}
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid email or password"


def test_account_locks_after_five_failed_logins(client):
    email = unique_email()
    client.post("/auth/register", json={"email": email, "password": STRONG_PASSWORD})

    for _ in range(5):
        resp = client.post("/auth/login", json={"email": email, "password": "wrong-password"})
        assert resp.status_code == 401

    # Reset the (separately-tested) IP rate limiter so this assertion is
    # isolated to the account-lockout control, not the rate limiter — both
    # fire on the 5th/6th request from the same client in real usage.
    limiter.reset()

    # Next attempt (even with the CORRECT password) should now be locked.
    locked_resp = client.post("/auth/login", json={"email": email, "password": STRONG_PASSWORD})
    assert locked_resp.status_code == 423


def test_get_me_requires_valid_token(client):
    resp = client.get("/auth/me")
    assert resp.status_code == 401

    resp_bad = client.get("/auth/me", headers=auth_headers("not-a-real-token"))
    assert resp_bad.status_code == 401


def test_get_me_returns_current_user(client):
    email = unique_email()
    register_resp = client.post("/auth/register", json={"email": email, "password": STRONG_PASSWORD})
    token = register_resp.json()["access_token"]

    resp = client.get("/auth/me", headers=auth_headers(token))
    assert resp.status_code == 200
    assert resp.json()["email"] == email


def test_refresh_rotates_token_and_issues_new_access_token(client):
    email = unique_email()
    client.post("/auth/register", json={"email": email, "password": STRONG_PASSWORD})

    old_refresh_cookie = client.cookies.get("fitwaze_refresh_token")
    assert old_refresh_cookie is not None

    refresh_resp = client.post("/auth/refresh")
    assert refresh_resp.status_code == 200
    assert "access_token" in refresh_resp.json()

    new_refresh_cookie = client.cookies.get("fitwaze_refresh_token")
    assert new_refresh_cookie is not None
    assert new_refresh_cookie != old_refresh_cookie


def test_refresh_without_cookie_rejected(client):
    resp = client.post("/auth/refresh")
    assert resp.status_code == 401


def test_reused_refresh_token_revokes_family(client):
    email = unique_email()
    client.post("/auth/register", json={"email": email, "password": STRONG_PASSWORD})

    old_token = client.cookies.get("fitwaze_refresh_token")

    # First rotation succeeds.
    first = client.post("/auth/refresh")
    assert first.status_code == 200
    new_token = client.cookies.get("fitwaze_refresh_token")
    assert new_token != old_token

    # Replaying the OLD (now-revoked) token should be detected as reuse and
    # rejected, and should also revoke the newly-issued token in the family.
    client.cookies.set("fitwaze_refresh_token", old_token)
    replay_resp = client.post("/auth/refresh")
    assert replay_resp.status_code == 401

    # The legitimately-rotated token must now ALSO be revoked (whole family).
    client.cookies.set("fitwaze_refresh_token", new_token)
    after_reuse_resp = client.post("/auth/refresh")
    assert after_reuse_resp.status_code == 401


def test_logout_revokes_refresh_token(client):
    email = unique_email()
    client.post("/auth/register", json={"email": email, "password": STRONG_PASSWORD})

    logout_resp = client.post("/auth/logout")
    assert logout_resp.status_code == 204

    # Cookie should be cleared / refresh should now fail.
    refresh_resp = client.post("/auth/refresh")
    assert refresh_resp.status_code == 401


def test_auth_login_rate_limited_after_threshold(client):
    email = unique_email()
    client.post("/auth/register", json={"email": email, "password": STRONG_PASSWORD})

    statuses = []
    for _ in range(7):
        resp = client.post("/auth/login", json={"email": email, "password": "wrong-password"})
        statuses.append(resp.status_code)

    assert 429 in statuses
