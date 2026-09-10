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


class ProgressResponse(BaseModel):
    sessions_selected: int
    sessions_completed: int
    sessions_abandoned: int
    completion_rate: float  # completed / total, 0-1
    total_distance_m: float  # completed sessions only
    total_active_minutes: int  # completed sessions only
    last_7_days_minutes: int
    current_streak_days: int
    recent_sessions: list[ProgressSessionSummary]
