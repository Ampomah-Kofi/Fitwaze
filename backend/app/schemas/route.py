"""Pydantic schemas for route generation/selection.

Coordinates are strictly validated (`latitude: Field(ge=-90, le=90)`, etc.)
so malformed input never reaches the route engine or the database.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import SessionStatusEnum

CandidateLabel = Literal["out_and_back", "small_loop", "large_loop"]


class RouteOptionsRequest(BaseModel):
    activity_recommendation_id: uuid.UUID
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class RouteOptionSchema(BaseModel):
    candidate_revision: str
    label: CandidateLabel
    distance_m: float
    estimated_minutes: float
    score: float
    score_breakdown: dict[str, float]
    explanation: str
    # Attributes the provider could not measure for this route, so the caller
    # can say what is known rather than implying every number is surveyed.
    unverified: list[str] = []
    # Measured facts about the surroundings, present only when the provider
    # measured them: ascent_m (total climb), green_pct, paths_pct,
    # busy_road_pct, steps_pct. A missing key means unknown, not zero.
    environment: dict[str, float] = {}
    geometry: list[tuple[float, float]]  # (lat, lon) points, full precision (not persisted)


class WeatherSchema(BaseModel):
    temperature_f: float
    relative_humidity: float | None = None
    heat_index_f: float
    # ok | caution | extreme_caution | danger | cold
    level: str
    advice: str


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
    # Heat/cold advice at the start point right now, when available (US only).
    weather: WeatherSchema | None = None


class RouteSelectRequest(BaseModel):
    # New clients echo this to detect routes changed after cache expiry/restart.
    candidate_revision: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
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
    effort: str | None = None
    # Set on the response to a completion that included an after-walk check-in.
    after_walk_advice: str | None = None


class RouteSessionUpdateRequest(BaseModel):
    """Terminal transition for a selected session. Only `completed` and
    `abandoned` are accepted — a client can never move a session back to
    `offered`/`selected`, and Pydantic rejects anything else with a 422."""

    status: Literal["completed", "abandoned"]
    # Optional after-walk check-in, sent with the completion.
    effort: Literal["easy", "just_right", "hard"] | None = None
    post_glucose_value: float | None = Field(default=None, gt=0, le=1000)
    post_glucose_unit: Literal["mmol/L", "mg/dL"] = "mg/dL"

    @model_validator(mode="after")
    def _plausible_reading(self) -> "RouteSessionUpdateRequest":
        mmol = self.post_glucose_mmol_l
        if mmol is not None and not (1.0 <= mmol <= 35.0):
            raise ValueError("That blood glucose reading looks out of range. Check the number and the unit.")
        return self

    @property
    def post_glucose_mmol_l(self) -> float | None:
        if self.post_glucose_value is None:
            return None
        return self.post_glucose_value / 18.0 if self.post_glucose_unit == "mg/dL" else self.post_glucose_value
