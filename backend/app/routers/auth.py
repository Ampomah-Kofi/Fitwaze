"""Authentication endpoints: register, login, refresh, logout.

Security behaviors implemented here:
- Argon2id password verification with account lockout after 5 consecutive
  failed logins (15 minute lockout window).
- Access token (15 min JWT) returned in the JSON body; refresh token (7 day,
  opaque, hashed at rest) delivered as an httpOnly/SameSite=Strict cookie.
- Refresh token rotation with reuse detection (see `app/security/tokens.py`).
- Strict rate limiting (5/minute per IP) on register + login.
- No user-enumeration: login failures for unknown email vs wrong password
  return the same generic error message/status code.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models.user import User
from app.schemas.auth import AccessTokenResponse, LoginRequest, RegisterRequest, UserPublic
from app.security.deps import get_current_user
from app.security.passwords import hash_password, verify_password
from app.security.rate_limit import AUTH_RATE_LIMIT, limiter
from app.security.tokens import (
    InvalidTokenError,
    RefreshTokenReuseError,
    create_access_token,
    issue_refresh_token,
    revoke_refresh_token,
    rotate_refresh_token,
)

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()

REFRESH_COOKIE_NAME = "fitwaze_refresh_token"

FAILED_LOGIN_LOCKOUT_THRESHOLD = 5
FAILED_LOGIN_LOCKOUT_MINUTES = 15

_GENERIC_LOGIN_ERROR = "Invalid email or password"


def _cookie_secure() -> bool:
    # Secure cookies require HTTPS transport. We disable the flag outside of
    # hosted environments so local/dev/test HTTP traffic can still exercise
    # the refresh flow end-to-end; hosted deployments (the field pilot and
    # production) always sit behind TLS.
    return settings.environment.lower() in ("production", "pilot")


def _set_refresh_cookie(response: Response, raw_refresh_token: str) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=raw_refresh_token,
        httponly=True,
        secure=_cookie_secure(),
        samesite="strict",
        max_age=settings.refresh_token_expire_days * 24 * 60 * 60,
        path="/",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(key=REFRESH_COOKIE_NAME, path="/")


def _refresh_error(detail: str) -> JSONResponse:
    # Raising HTTPException discards headers on the injected response.
    response = JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": detail})
    _clear_refresh_cookie(response)
    return response


def _issue_tokens(db: Session, user: User, response: Response) -> AccessTokenResponse:
    access_token = create_access_token(user.id)
    issued = issue_refresh_token(db, user.id)
    _set_refresh_cookie(response, issued.raw_token)
    return AccessTokenResponse(
        access_token=access_token,
        expires_in_minutes=settings.access_token_expire_minutes,
        user=UserPublic.model_validate(user),
    )


@router.post("/register", response_model=AccessTokenResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit(AUTH_RATE_LIMIT)
def register(
    request: Request,
    payload: RegisterRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> AccessTokenResponse:
    existing = db.execute(select(User).where(User.email == payload.email)).scalar_one_or_none()
    if existing is not None:
        # Generic error to avoid confirming which emails are registered.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Registration failed")

    user = User(email=payload.email, password_hash=hash_password(payload.password))
    db.add(user)
    db.commit()
    db.refresh(user)

    result = _issue_tokens(db, user, response)
    db.commit()
    return result


@router.post("/login", response_model=AccessTokenResponse)
@limiter.limit(AUTH_RATE_LIMIT)
def login(
    request: Request,
    payload: LoginRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> AccessTokenResponse:
    user = db.execute(select(User).where(User.email == payload.email)).scalar_one_or_none()

    now = datetime.now(timezone.utc)

    if user is not None and user.locked_until is not None:
        locked_until = user.locked_until
        if locked_until.tzinfo is None:
            locked_until = locked_until.replace(tzinfo=timezone.utc)
        if locked_until > now:
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail="Account temporarily locked due to repeated failed login attempts",
            )

    valid = user is not None and verify_password(payload.password, user.password_hash)

    if not valid:
        if user is not None:
            user.failed_login_count = (user.failed_login_count or 0) + 1
            if user.failed_login_count >= FAILED_LOGIN_LOCKOUT_THRESHOLD:
                user.locked_until = now + timedelta(minutes=FAILED_LOGIN_LOCKOUT_MINUTES)
            db.add(user)
            db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_GENERIC_LOGIN_ERROR)

    user.failed_login_count = 0
    user.locked_until = None
    db.add(user)

    result = _issue_tokens(db, user, response)
    db.commit()
    return result


@router.post("/refresh", response_model=AccessTokenResponse)
def refresh(request: Request, response: Response, db: Session = Depends(get_db)) -> AccessTokenResponse | Response:
    raw_refresh_token = request.cookies.get(REFRESH_COOKIE_NAME)
    if not raw_refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing refresh token")

    try:
        issued = rotate_refresh_token(db, raw_refresh_token)
    except RefreshTokenReuseError:
        db.commit()  # persist the family-wide revocation
        return _refresh_error("Refresh token reuse detected; all sessions revoked")
    except InvalidTokenError:
        return _refresh_error("Invalid refresh token")

    user = db.get(User, issued.record.user_id)
    if user is None:  # pragma: no cover - defensive, FK guarantees existence
        return _refresh_error("Invalid refresh token")

    access_token = create_access_token(user.id)
    _set_refresh_cookie(response, issued.raw_token)
    db.commit()

    return AccessTokenResponse(
        access_token=access_token,
        expires_in_minutes=settings.access_token_expire_minutes,
        user=UserPublic.model_validate(user),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> Response:
    raw_refresh_token = request.cookies.get(REFRESH_COOKIE_NAME)
    if raw_refresh_token:
        revoke_refresh_token(db, raw_refresh_token)
        db.commit()
    _clear_refresh_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=UserPublic)
def read_me(current_user: User = Depends(get_current_user)) -> UserPublic:
    return UserPublic.model_validate(current_user)
