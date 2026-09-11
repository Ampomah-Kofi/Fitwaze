"""Health profile endpoints: GET/PUT /profile, GET /profile/export,
DELETE /profile/delete.

Every handler here is scoped to `current_user.id` from the verified JWT
(`get_current_user`) — a client can never read or write another user's
profile by supplying a different id, because no id is ever accepted from the
client for these routes in the first place.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.profile import HealthProfile, apply_profile_data, profile_to_data
from app.models.user import User
from app.models.activity import ActivityRecommendation
from app.models.session import ActivitySession
from app.schemas.profile import HealthProfileResponse, HealthProfileUpsertRequest
from app.security.deps import get_current_user

router = APIRouter(prefix="/profile", tags=["profile"])
logger = logging.getLogger(__name__)


def _get_own_profile(db: Session, user_id) -> HealthProfile | None:
    return db.get(HealthProfile, user_id)


def _to_response(profile: HealthProfile) -> HealthProfileResponse:
    data = profile_to_data(profile)
    return HealthProfileResponse(user_id=str(profile.user_id), updated_at=profile.updated_at, **data.model_dump())


@router.get("", response_model=HealthProfileResponse)
def get_profile(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HealthProfileResponse:
    profile = _get_own_profile(db, current_user.id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
    return _to_response(profile)


@router.put("", response_model=HealthProfileResponse)
def upsert_profile(
    payload: HealthProfileUpsertRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HealthProfileResponse:
    profile = _get_own_profile(db, current_user.id)
    created = profile is None
    if profile is None:
        profile = HealthProfile(user_id=current_user.id)

    apply_profile_data(profile, payload)
    db.add(profile)
    db.commit()
    db.refresh(profile)

    logger.info(
        "profile_%s user_id=%s", "created" if created else "updated", current_user.id
    )
    return _to_response(profile)


@router.get("/export")
def export_profile(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """GDPR-style data export: returns the full decrypted profile (and, once
    those tables exist, activity/route history) as JSON to the authenticated
    owner only. No other user's data can ever be included since everything
    is scoped by `current_user.id`."""
    profile = _get_own_profile(db, current_user.id)

    logger.info("profile_export user_id=%s", current_user.id)

    return {
        "user": {
            "id": str(current_user.id),
            "email": current_user.email,
            "created_at": current_user.created_at.isoformat(),
        },
        "health_profile": _to_response(profile).model_dump(mode="json") if profile else None,
    }


@router.delete("/delete", status_code=status.HTTP_204_NO_CONTENT)
def delete_profile(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """GDPR-style right-to-erasure: deletes the user's account and all
    associated data (health profile, activity recommendations, activity
    sessions, refresh tokens, progress summaries). Rows are deleted
    explicitly (rather than relying solely on DB-level ON DELETE CASCADE) so
    behavior is identical and deterministic on both PostgreSQL and the
    SQLite test database."""
    profile = _get_own_profile(db, current_user.id)
    if profile is not None:
        db.delete(profile)

    # Remove dependent rows before the user; ORM backrefs otherwise attempt to
    # null their non-nullable user_id instead of honoring database cascades.
    db.execute(delete(ActivitySession).where(ActivitySession.user_id == current_user.id))
    db.execute(delete(ActivityRecommendation).where(ActivityRecommendation.user_id == current_user.id))
    user = db.get(User, current_user.id)
    if user is not None:
        db.delete(user)  # cascades refresh_tokens via ORM relationship

    db.commit()
    logger.info("profile_deleted user_id=%s", current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
