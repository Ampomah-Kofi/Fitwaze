"""Pydantic schemas for the health profile: request validation is strict
(explicit numeric bounds + enum types) per the security requirements —
no arbitrary strings are accepted for categorical health fields."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    AbilityEnum,
    DiabetesStatusEnum,
    GoalEnum,
    MobilityLimitationEnum,
    PreferredActivityEnum,
    SexEnum,
)


class HealthProfileData(BaseModel):
    """Plain profile data shape consumed by the Activity Engine
    (`app.engines.activity_engine.recommend_activity`). Decoupled from the
    ORM model / request-response schemas so the engine stays a pure function
    with no DB or FastAPI dependency."""

    model_config = ConfigDict(from_attributes=True)

    age: int = Field(ge=13, le=120)
    sex: SexEnum
    goal: GoalEnum
    preferred_activity: PreferredActivityEnum
    current_weekly_minutes: int = Field(ge=0, le=3000)
    current_frequency: int = Field(ge=0, le=21)
    height_cm: float = Field(ge=50, le=272)
    weight_kg: float = Field(ge=20, le=400)
    diabetes_status: DiabetesStatusEnum
    mobility_limitations: MobilityLimitationEnum
    walking_ability: AbilityEnum
    cycling_ability: AbilityEnum

    # Insulin or a sulfonylurea (e.g. glipizide, glimepiride, glyburide):
    # the medicines that can make blood sugar fall too low during exercise.
    takes_glucose_lowering_medication: bool = False
    # Who to call if something goes wrong on a walk. Optional.
    emergency_contact_name: str | None = Field(default=None, max_length=80)
    emergency_contact_phone: str | None = Field(default=None, pattern=r"^\+?[0-9 ().-]{7,20}$")


class HealthProfileUpsertRequest(HealthProfileData):
    """Same validated shape as HealthProfileData; used for PUT /profile."""


class HealthProfileResponse(HealthProfileData):
    user_id: str
    updated_at: datetime


class HomeLocation(BaseModel):
    """The saved start point for this person's routes: where they live."""

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
