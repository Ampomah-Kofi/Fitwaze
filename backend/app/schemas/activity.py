import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import ActivityTypeEnum, FeelingEnum, GlucoseUnitEnum

MG_DL_PER_MMOL_L = 18.0

# Plausible meter readings. Anything outside is a typo (or the wrong unit),
# and acting on it would be worse than asking again.
_MIN_GLUCOSE_MMOL_L = 1.0
_MAX_GLUCOSE_MMOL_L = 35.0


class DailyCheckIn(BaseModel):
    """How the person is today, entered when they wake up.

    The saved profile says who someone is; this says how they are this
    morning. It shapes one recommendation. Only the blood sugar reading is
    kept (encrypted, with the recommendation) so the person can compare it
    with their reading after the walk; the rest is not stored.
    """

    # Optional: not everyone owns a meter, and a check-in without a reading
    # must still work.
    glucose_value: float | None = Field(default=None, gt=0, le=1000)
    glucose_unit: GlucoseUnitEnum = GlucoseUnitEnum.mmol_l
    feeling: FeelingEnum
    # Chest pain or tightness, unusual shortness of breath, dizziness or
    # fainting, confusion.
    warning_symptoms: bool = False
    # A new sore, blister, cut or swelling on the feet. Diabetic foot wounds
    # heal slowly and walking on them makes them worse.
    foot_problem: bool = False

    @model_validator(mode="after")
    def _glucose_in_plausible_range(self) -> "DailyCheckIn":
        mmol = self.glucose_mmol_l
        if mmol is not None and not (_MIN_GLUCOSE_MMOL_L <= mmol <= _MAX_GLUCOSE_MMOL_L):
            raise ValueError(
                "That blood glucose reading looks out of range. Check the number and the unit (mmol/L or mg/dL)."
            )
        return self

    @property
    def glucose_mmol_l(self) -> float | None:
        if self.glucose_value is None:
            return None
        if self.glucose_unit == GlucoseUnitEnum.mg_dl:
            return self.glucose_value / MG_DL_PER_MMOL_L
        return self.glucose_value


class ActivityRecommendationRequest(BaseModel):
    activity_type: ActivityTypeEnum | None = None
    checkin: DailyCheckIn | None = None
    # How long the person wants to go today, instead of the suggested length.
    preferred_minutes: int | None = Field(default=None, ge=10, le=60)


class ActivityRecommendationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    activity_type: ActivityTypeEnum
    duration_minutes: int
    rationale: str
    disclaimer: str
    created_at: datetime
