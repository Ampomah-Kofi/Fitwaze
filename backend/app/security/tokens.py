"""JWT access tokens + opaque, rotating refresh tokens.

- Access tokens: short-lived (15 min default) JWTs, HS256, signed with
  `JWT_SECRET`. Returned in the JSON response body for the frontend to hold
  in memory (never persisted server-side).
- Refresh tokens: random opaque strings (not JWTs). Only a SHA-256 hash of
  the token is ever stored in the `refresh_tokens` table; the raw token is
  delivered to the client exactly once, as an httpOnly/Secure/SameSite=Strict
  cookie (set by the auth router).
- Rotation: every successful `/auth/refresh` call revokes the presented
  token and issues a new one in the same "family". If a token that has
  already been revoked is presented again (replay of a stolen/rotated-out
  token), the entire family is revoked as a reuse-detection response.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.user import RefreshToken

settings = get_settings()

ACCESS_TOKEN_TYPE = "access"


class TokenError(Exception):
    """Base class for token-related failures."""


class InvalidTokenError(TokenError):
    pass


class RefreshTokenReuseError(TokenError):
    """Raised when a previously-revoked refresh token is presented again.

    Indicates possible token theft; caller should treat this as a security
    event (the whole token family has already been revoked by the time this
    is raised).
    """


# --- Access tokens (JWT) ---------------------------------------------------


def create_access_token(user_id: uuid.UUID | str) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {
        "sub": str(user_id),
        "type": ACCESS_TOKEN_TYPE,
        "iat": int(now.timestamp()),
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    """Decode and verify a JWT access token. Raises InvalidTokenError on any
    failure (expired, bad signature, wrong type, malformed)."""
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except JWTError as exc:
        raise InvalidTokenError("invalid or expired access token") from exc

    if payload.get("type") != ACCESS_TOKEN_TYPE:
        raise InvalidTokenError("wrong token type")
    if "sub" not in payload:
        raise InvalidTokenError("missing subject claim")
    return payload


# --- Refresh tokens (opaque, hashed at rest) --------------------------------


def _generate_raw_refresh_token() -> str:
    return secrets.token_urlsafe(64)


def _hash_refresh_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


@dataclass
class IssuedRefreshToken:
    raw_token: str
    record: RefreshToken


def issue_refresh_token(
    db: Session, user_id: uuid.UUID, family_id: uuid.UUID | None = None
) -> IssuedRefreshToken:
    """Create and persist a new refresh token (hashed) for a user.

    If `family_id` is omitted, a new family is started (used at login). When
    rotating an existing token, pass the existing family_id forward so reuse
    detection can revoke the whole chain if needed.
    """
    raw_token = _generate_raw_refresh_token()
    now = datetime.now(timezone.utc)
    record = RefreshToken(
        id=uuid.uuid4(),
        user_id=user_id,
        token_hash=_hash_refresh_token(raw_token),
        family_id=family_id or uuid.uuid4(),
        issued_at=now,
        expires_at=now + timedelta(days=settings.refresh_token_expire_days),
        revoked_at=None,
    )
    db.add(record)
    db.flush()
    return IssuedRefreshToken(raw_token=raw_token, record=record)


def rotate_refresh_token(db: Session, raw_token: str) -> IssuedRefreshToken:
    """Validate a presented refresh token and rotate it.

    Returns the newly issued refresh token on success. Raises:
    - InvalidTokenError: token not found, expired.
    - RefreshTokenReuseError: token was already revoked (replay detected) —
      the entire family has been revoked as a side effect before raising.
    """
    token_hash = _hash_refresh_token(raw_token)
    record = db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    ).scalar_one_or_none()

    if record is None:
        raise InvalidTokenError("refresh token not recognized")

    now = datetime.now(timezone.utc)
    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if record.revoked_at is not None:
        # Reuse of an already-rotated-out token: revoke the whole family.
        _revoke_family(db, record.family_id)
        raise RefreshTokenReuseError("refresh token reuse detected")

    if expires_at < now:
        raise InvalidTokenError("refresh token expired")

    record.revoked_at = now
    db.add(record)

    new_issued = issue_refresh_token(db, record.user_id, family_id=record.family_id)
    db.flush()
    return new_issued


def revoke_refresh_token(db: Session, raw_token: str) -> None:
    """Revoke a single refresh token (used on logout). No-op if not found."""
    token_hash = _hash_refresh_token(raw_token)
    record = db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    ).scalar_one_or_none()
    if record is not None and record.revoked_at is None:
        record.revoked_at = datetime.now(timezone.utc)
        db.add(record)
        db.flush()


def _revoke_family(db: Session, family_id: uuid.UUID) -> None:
    now = datetime.now(timezone.utc)
    records = db.execute(
        select(RefreshToken).where(
            RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None)
        )
    ).scalars()
    for record in records:
        record.revoked_at = now
        db.add(record)
    db.flush()
