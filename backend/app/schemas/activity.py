import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import ActivityTypeEnum


class ActivityRecommendationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    activity_type: ActivityTypeEnum
    duration_minutes: int
    rationale: str
    disclaimer: str
    created_at: datetime
