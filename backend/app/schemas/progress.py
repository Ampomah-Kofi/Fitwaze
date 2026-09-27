"""Pydantic schemas for the progress summary.

Everything here is derived from the CALLING user's own activity sessions;
no schema in this module carries another user's data or any raw health
profile field.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import ActivityTypeEnum, SessionStatusEnum


class ProgressSessionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    activity_type: ActivityTypeEnum
    distance_m: float
    estimated_minutes: int
    status: SessionStatusEnum
    created_at: datetime
    completed_at: datetime | None = None
    # Estimated from METs and the saved body weight; None without a profile.
    calories: int | None = None
    # True when minutes/distance are what GPS measured, not the plan.
    measured: bool = False


class GlucoseWalk(BaseModel):
    """Blood sugar at the morning check-in and after the walk that followed."""
    session_id: uuid.UUID
    completed_at: datetime
    activity_type: ActivityTypeEnum
    before_mg_dl: int
    after_mg_dl: int


class ProgressResponse(BaseModel):
    sessions_selected: int
    sessions_completed: int
    sessions_abandoned: int
    completion_rate: float  # completed / total, 0-1
    total_distance_m: float  # completed sessions only
    total_active_minutes: int  # completed sessions only
    last_7_days_minutes: int
    current_streak_days: int
    total_calories: int | None = None
    recent_sessions: list[ProgressSessionSummary]
    # Walks with a reading before and after, oldest first (the latest few).
    glucose_walks: list[GlucoseWalk] = []
    # Average of (after - before) over glucose_walks; negative means lower.
    average_glucose_change_mg_dl: int | None = None
