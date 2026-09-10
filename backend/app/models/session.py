"""Persisted route sessions selected from generated route options."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, JSON, Integer, Float, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.enums import SessionStatusEnum
from app.models.types import GUID


class ActivitySession(Base):
    __tablename__ = "activity_sessions"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    activity_recommendation_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("activity_recommendations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    distance_m: Mapped[float] = mapped_column(Float, nullable=False)
    estimated_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    score_breakdown: Mapped[dict[str, float]] = mapped_column(JSON, nullable=False)
    status: Mapped[SessionStatusEnum] = mapped_column(
        SAEnum(SessionStatusEnum, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=SessionStatusEnum.selected,
    )
    route_geometry: Mapped[list[list[float]]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user = relationship("User", backref="activity_sessions")
    activity_recommendation = relationship("ActivityRecommendation")