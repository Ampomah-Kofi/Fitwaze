"""Pydantic schemas for route generation/selection.

Coordinates are strictly validated (`latitude: Field(ge=-90, le=90)`, etc.)
so malformed input never reaches the route engine or the database.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import SessionStatusEnum

CandidateLabel = Literal["out_and_back", "small_loop", "large_loop"]


class RouteOptionsRequest(BaseModel):
    activity_recommendation_id: uuid.UUID
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class RouteOptionSchema(BaseModel):
    label: CandidateLabel
    distance_m: float
    estimated_minutes: float
    score: float
    score_breakdown: dict[str, float]
    explanation: str
    # Attributes the provider could not measure for this route, so the caller
    # can say what is known rather than implying every number is surveyed.
    unverified: list[str] = []
    geometry: list[tuple[float, float]]  # (lat, lon) points, full precision (not persisted)


class ExcludedRouteSchema(BaseModel):
    label: str
    reason: str


class RouteOptionsResponse(BaseModel):
    activity_recommendation_id: uuid.UUID
    activity_type: str
    target_duration_minutes: int
    # Which RouteProvider produced these candidates ("mock" = synthetic,
    # "ors" = real OpenStreetMap-derived routes). Clients surface this so a
    # demo can never pass synthetic geometry off as real streets.
    provider: str
    options: list[RouteOptionSchema]
    # Candidates withheld because they are not suitable for this person — shown
    # with their reason rather than dropped silently.
    excluded: list[ExcludedRouteSchema] = []


class RouteSelectRequest(BaseModel):
    activity_recommendation_id: uuid.UUID
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    candidate_label: CandidateLabel


class RouteSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    activity_recommendation_id: uuid.UUID
    distance_m: float
    estimated_minutes: int
    score_breakdown: dict[str, float]
    status: SessionStatusEnum
    created_at: datetime
    completed_at: datetime | None = None
    route_geometry: list[tuple[float, float]] | None = None


class RouteSessionUpdateRequest(BaseModel):
    """Terminal transition for a selected session. Only `completed` and
    `abandoned` are accepted — a client can never move a session back to
    `offered`/`selected`, and Pydantic rejects anything else with a 422."""

    status: Literal["completed", "abandoned"]
