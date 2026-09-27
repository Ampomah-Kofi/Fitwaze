"""Progress summary endpoint.

GET /progress aggregates the CALLING user's own activity sessions — totals,
completion rate, last-7-day minutes and a day streak. Every query is scoped
by `user_id == current_user.id`; no client-supplied id is ever used to
select rows.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import csv
import io

from fastapi import APIRouter, Depends, Header
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.enums import SessionStatusEnum
from app.engines.calories import estimate_calories, estimate_steps
from app.models.profile import HealthProfile
from app.models.session import ActivitySession
from app.models.user import User
from app.schemas.progress import Achievement, GlucoseWalk, ProgressResponse, ProgressSessionSummary
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


def _zone(name: str | None):
    """The caller's IANA time zone (sent by the app as X-Timezone), or UTC."""
    if not name or len(name) > 64:
        return timezone.utc
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


def _current_streak_days(completed_dates: set[date], today: date) -> int:
    """Consecutive days, ending today or yesterday, with >=1 completed
    session. Allowing the streak to end yesterday means it isn't broken
    simply because the user hasn't been out yet today.

    Days are the caller's local calendar days (see `_zone`), so an evening
    walk in Alabama counts for that evening, not the next UTC day."""
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


def _longest_streak_days(completed_dates: set[date]) -> int:
    longest = 0
    for day in completed_dates:
        if day - timedelta(days=1) in completed_dates:
            continue  # not the start of a run
        length = 1
        while day + timedelta(days=length) in completed_dates:
            length += 1
        longest = max(longest, length)
    return longest


def _best_week_minutes(minutes_by_day: dict[date, float]) -> float:
    """Most active minutes in one calendar week (Monday to Sunday)."""
    weeks: dict[date, float] = {}
    for day, minutes in minutes_by_day.items():
        monday = day - timedelta(days=day.weekday())
        weeks[monday] = weeks.get(monday, 0.0) + minutes
    return max(weeks.values(), default=0.0)


# (key, title, description, unit measured, goal). Small, reachable steps
# first: the aim is encouragement, not a leaderboard.
ACHIEVEMENTS = (
    ("first_walk", "First step", "Finish your first walk or ride.", "sessions", 1),
    ("streak_3", "Three in a row", "Be active 3 days in a row.", "streak", 3),
    ("sugar_check", "Know your numbers", "Check your blood sugar before and after a walk.", "glucose", 1),
    ("walks_10", "Ten done", "Finish 10 walks or rides.", "sessions", 10),
    ("miles_10", "10 miles", "Cover 10 miles in all.", "miles", 10),
    ("week_150", "150-minute week", "Reach the weekly goal of 150 active minutes.", "week", 150),
    ("streak_7", "Full week", "Be active 7 days in a row.", "streak", 7),
    ("walks_25", "Habit formed", "Finish 25 walks or rides.", "sessions", 25),
    ("miles_50", "50 miles", "Cover 50 miles in all.", "miles", 50),
)


def _achievements(measures: dict[str, float]) -> list[Achievement]:
    return [
        Achievement(key=key, title=title, description=description, earned=measures[unit] >= goal,
                    current=round(min(measures[unit], goal), 1), goal=goal)
        for key, title, description, unit, goal in ACHIEVEMENTS
    ]


MG_DL_PER_MMOL_L = 18.0
# A morning reading only describes "before the walk" if the walk followed
# soon after; a reading from breakfast says little about a walk at dinner.
BEFORE_READING_MAX_AGE = timedelta(hours=4)
GLUCOSE_WALK_LIMIT = 10


def _mg_dl(stored: str | None) -> int | None:
    return round(float(stored) * MG_DL_PER_MMOL_L) if stored else None


def _before_reading(s: ActivitySession) -> int | None:
    """The check-in reading taken shortly before this walk, in mg/dL."""
    recommendation = s.activity_recommendation
    if not recommendation.pre_glucose_mmol_l or s.completed_at is None:
        return None
    if _as_utc(s.completed_at) - _as_utc(recommendation.created_at) > BEFORE_READING_MAX_AGE:
        return None
    return _mg_dl(recommendation.pre_glucose_mmol_l)


@router.get("/export.csv")
def export_activity_csv(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    x_timezone: str | None = Header(default=None),
) -> Response:
    """The caller's own finished activities as a spreadsheet, to share with
    their care team: date, activity, minutes, miles, calories, how it felt
    and blood sugar afterwards. Only their own rows; never anyone else's."""
    zone = _zone(x_timezone)
    profile = db.get(HealthProfile, current_user.id)
    weight_kg = float(profile.weight_kg) if profile is not None else None
    height_cm = float(profile.height_cm) if profile is not None else None
    rows = db.scalars(
        select(ActivitySession)
        .where(ActivitySession.user_id == current_user.id)
        .where(ActivitySession.status == SessionStatusEnum.completed)
        .order_by(ActivitySession.completed_at.desc())
    )
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Date", "Activity", "Minutes", "Miles", "Calories (estimate)", "Steps (estimate)", "Measured by GPS",
                     "How it felt", "Blood sugar before (mg/dL)", "Blood sugar after (mg/dL)"])
    for s in rows:
        minutes = s.measured_minutes if s.measured_minutes is not None else s.estimated_minutes
        metres = s.measured_distance_m if s.measured_distance_m is not None else s.distance_m
        activity = s.activity_recommendation.activity_type
        writer.writerow([
            _as_utc(s.completed_at).astimezone(zone).strftime("%Y-%m-%d %H:%M") if s.completed_at else "",
            "Ride" if activity.value == "cycle" else "Walk",
            round(minutes),
            f"{metres / 1609.344:.2f}",
            estimate_calories(activity, minutes, weight_kg, metres) or "",
            estimate_steps(activity, metres, height_cm) or "",
            "yes" if s.measured_minutes is not None else "no",
            (s.effort or "").replace("_", " "),
            _before_reading(s) or "",
            _mg_dl(s.post_glucose_mmol_l) or "",
        ])
    logger.info("activity_export user_id=%s", current_user.id)
    return Response(
        content=out.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="fitwaze-activity.csv"', "Cache-Control": "no-store"},
    )


@router.get("", response_model=ProgressResponse)
def get_progress(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    x_timezone: str | None = Header(default=None),
) -> ProgressResponse:
    zone = _zone(x_timezone)
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
        round(s.measured_minutes if s.measured_minutes is not None else s.estimated_minutes)
        for s in completed
        if s.id in completed_at_utc and completed_at_utc[s.id] >= week_ago
    )
    streak = _current_streak_days(
        {value.astimezone(zone).date() for value in completed_at_utc.values()}, now.astimezone(zone).date()
    )

    profile = db.get(HealthProfile, current_user.id)
    weight_kg = float(profile.weight_kg) if profile is not None else None
    height_cm = float(profile.height_cm) if profile is not None else None

    # Prefer what the phone measured; fall back to the plan when the walk was
    # not tracked.
    def minutes(s: ActivitySession) -> float:
        return s.measured_minutes if s.measured_minutes is not None else s.estimated_minutes

    def metres(s: ActivitySession) -> float:
        return s.measured_distance_m if s.measured_distance_m is not None else s.distance_m

    def calories(s: ActivitySession) -> int | None:
        return estimate_calories(s.activity_recommendation.activity_type, minutes(s), weight_kg, metres(s))

    burned = [calories(s) for s in completed]

    def steps(s: ActivitySession) -> int | None:
        return estimate_steps(s.activity_recommendation.activity_type, metres(s), height_cm)

    glucose_walks = []
    for s in sorted(completed, key=lambda s: completed_at_utc.get(s.id) or now):
        before, after = _before_reading(s), _mg_dl(s.post_glucose_mmol_l)
        if before is not None and after is not None:
            glucose_walks.append(GlucoseWalk(session_id=s.id, completed_at=s.completed_at,
                                             activity_type=s.activity_recommendation.activity_type,
                                             before_mg_dl=before, after_mg_dl=after))
    glucose_walks = glucose_walks[-GLUCOSE_WALK_LIMIT:]  # the latest, oldest first
    local_minutes: dict[date, float] = {}
    for s in completed:
        if s.id in completed_at_utc:
            day = completed_at_utc[s.id].astimezone(zone).date()
            local_minutes[day] = local_minutes.get(day, 0.0) + minutes(s)
    achievements = _achievements({
        "sessions": len(completed),
        "streak": _longest_streak_days(set(local_minutes)),
        "glucose": len(glucose_walks),
        "miles": sum(metres(s) for s in completed) / 1609.344,
        "week": _best_week_minutes(local_minutes),
    })
    average_change = (round(sum(w.after_mg_dl - w.before_mg_dl for w in glucose_walks) / len(glucose_walks))
                      if glucose_walks else None)
    total = len(sessions)
    return ProgressResponse(
        sessions_selected=total,
        sessions_completed=len(completed),
        sessions_abandoned=len(abandoned),
        completion_rate=round(len(completed) / total, 3) if total else 0.0,
        total_distance_m=round(sum(metres(s) for s in completed), 1),
        total_active_minutes=round(sum(minutes(s) for s in completed)),
        last_7_days_minutes=last_7_days_minutes,
        current_streak_days=streak,
        total_calories=sum(c for c in burned if c) if weight_kg else None,
        total_steps=sum(n for n in (steps(s) for s in completed) if n),
        recent_sessions=[
            ProgressSessionSummary(
                id=s.id,
                activity_type=s.activity_recommendation.activity_type,
                distance_m=metres(s),
                estimated_minutes=round(minutes(s)),
                status=s.status,
                created_at=s.created_at,
                completed_at=s.completed_at,
                calories=calories(s),
                steps=steps(s),
                measured=s.measured_minutes is not None,
            )
            for s in sessions[:RECENT_SESSION_LIMIT]
        ],
        glucose_walks=glucose_walks,
        average_glucose_change_mg_dl=average_change,
        achievements=achievements,
    )
