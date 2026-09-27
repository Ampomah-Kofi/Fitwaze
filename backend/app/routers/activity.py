"""Activity recommendation endpoint.

POST /activity/recommendation reads the CALLING user's own health profile
(never a client-supplied id), combines it with the optional morning check-in
(today's glucose reading, how they feel, warning signs), runs the pure
`recommend_activity` engine function, persists the result, and returns it.
The check-in itself is not stored.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.engines.activity_engine import DISCLAIMER, ActivityUnavailableError, RecentActivity, recommend_activity
from app.models.enums import SessionStatusEnum
from app.models.session import ActivitySession
from app.models.activity import ActivityRecommendation
from app.models.profile import HealthProfile, profile_to_data
from app.models.user import User
from app.schemas.activity import ActivityRecommendationRequest, ActivityRecommendationResponse
from app.security.deps import get_current_user

router = APIRouter(prefix="/activity", tags=["activity"])
logger = logging.getLogger(__name__)


RECENT_WINDOW_DAYS = 14


def _recent_activity(db: Session, user_id) -> list[RecentActivity]:
    """The caller's last few finished or abandoned sessions, newest first,
    from the past two weeks: an older walk says little about today."""
    since = datetime.now(timezone.utc) - timedelta(days=RECENT_WINDOW_DAYS)
    rows = db.scalars(
        select(ActivitySession)
        .where(ActivitySession.user_id == user_id)
        .where(ActivitySession.status.in_([SessionStatusEnum.completed, SessionStatusEnum.abandoned]))
        .order_by(ActivitySession.created_at.desc())
        .limit(3)
    )
    recent = []
    for row in rows:
        created = row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=timezone.utc)
        if created >= since:
            recent.append(RecentActivity(status=row.status.value, effort=row.effort))
    return recent


@router.post("/recommendation", response_model=ActivityRecommendationResponse)
def create_activity_recommendation(
    payload: ActivityRecommendationRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ActivityRecommendationResponse:
    profile = db.get(HealthProfile, current_user.id)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Complete your health profile before requesting a recommendation",
        )

    profile_data = profile_to_data(profile)
    try:
        result = recommend_activity(
            profile_data,
            payload.activity_type if payload else None,
            payload.checkin if payload else None,
            _recent_activity(db, current_user.id),
            payload.preferred_minutes if payload else None,
        )
    except ActivityUnavailableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    record = ActivityRecommendation(
        user_id=current_user.id,
        activity_type=result.activity_type,
        duration_minutes=result.duration_minutes,
        rationale=result.rationale,
    )
    reading = payload.checkin.glucose_mmol_l if payload and payload.checkin else None
    if reading is not None:
        record.pre_glucose_mmol_l = f"{reading:.2f}"
    db.add(record)
    db.commit()
    db.refresh(record)

    logger.info("activity_recommendation_created user_id=%s", current_user.id)

    return ActivityRecommendationResponse(
        id=record.id,
        activity_type=record.activity_type,
        duration_minutes=record.duration_minutes,
        rationale=record.rationale,
        disclaimer=DISCLAIMER,
        created_at=record.created_at,
    )
