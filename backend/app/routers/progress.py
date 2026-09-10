"""Progress summary endpoint.

GET /progress aggregates the CALLING user's own activity sessions — totals,
completion rate, last-7-day minutes and a day streak. Every query is scoped
by `user_id == current_user.id`; no client-supplied id is ever used to
select rows.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.enums import SessionStatusEnum
from app.models.session import ActivitySession
from app.models.user import User
from app.schemas.progress import ProgressResponse, ProgressSessionSummary
from app.security.deps import get_current_user

router = APIRouter(prefix="/progress", tags=["progress"])
logger = logging.getLogger(__name__)

RECENT_SESSION_LIMIT = 5
STREAK_WINDOW_DAYS = 365


def _as_utc(value: datetime) -> datetime:
    """Timestamps come back naive under SQLite (tests) and tz-aware under
    PostgreSQL; treat naive values as the UTC they were written as."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _current_streak_days(completed_dates: set[date], today: date) -> int:
    """Consecutive days, ending today or yesterday, with >=1 completed
    session. Allowing the streak to end yesterday means it isn't broken
    simply because the user hasn't been out yet today.

    Days are bucketed in UTC, so a session completed late in the evening west
    of Greenwich counts towards the next calendar day. Fixing that properly
    needs the user's timezone, which the profile does not collect."""
    if today in completed_dates:
        cursor = today
    elif (today - timedelta(days=1)) in completed_dates:
        cursor = today - timedelta(days=1)
    else:
        return 0

    streak = 0
    while cursor in completed_dates and streak < STREAK_WINDOW_DAYS:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


@router.get("", response_model=ProgressResponse)
def get_progress(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProgressResponse:
    # Session volume per user is small (one row per selected route), so the
    # aggregates are computed in Python rather than in SQL — this keeps the
    # naive/aware timestamp handling below in one place and dialect-agnostic.
    sessions = list(
        db.scalars(
            select(ActivitySession)
            .where(ActivitySession.user_id == current_user.id)
            .order_by(ActivitySession.created_at.desc())
        )
    )

    completed = [s for s in sessions if s.status == SessionStatusEnum.completed]
    abandoned = [s for s in sessions if s.status == SessionStatusEnum.abandoned]

    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    completed_at_utc = {
        s.id: _as_utc(s.completed_at) for s in completed if s.completed_at is not None
    }

    last_7_days_minutes = sum(
        s.estimated_minutes
        for s in completed
        if s.id in completed_at_utc and completed_at_utc[s.id] >= week_ago
    )
    streak = _current_streak_days(
        {value.date() for value in completed_at_utc.values()}, now.date()
    )

    total = len(sessions)
    return ProgressResponse(
        sessions_selected=total,
        sessions_completed=len(completed),
        sessions_abandoned=len(abandoned),
        completion_rate=round(len(completed) / total, 3) if total else 0.0,
        total_distance_m=round(sum(s.distance_m for s in completed), 1),
        total_active_minutes=sum(s.estimated_minutes for s in completed),
        last_7_days_minutes=last_7_days_minutes,
        current_streak_days=streak,
        recent_sessions=[
            ProgressSessionSummary(
                id=s.id,
                activity_type=s.activity_recommendation.activity_type,
                distance_m=s.distance_m,
                estimated_minutes=s.estimated_minutes,
                status=s.status,
                created_at=s.created_at,
                completed_at=s.completed_at,
            )
            for s in sessions[:RECENT_SESSION_LIMIT]
        ],
    )
