"""ActivityRecommendation model: a persisted record of what the Activity
Engine recommended for a user at a point in time."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.enums import ActivityTypeEnum
from app.models.types import GUID


class ActivityRecommendation(Base):
    __tablename__ = "activity_recommendations"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    activity_type: Mapped[ActivityTypeEnum] = mapped_column(
        SAEnum(ActivityTypeEnum, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    # Template-generated; must never echo raw stored health field values.
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user = relationship("User", backref="activity_recommendations")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ActivityRecommendation id={self.id} user_id={self.user_id}>"
