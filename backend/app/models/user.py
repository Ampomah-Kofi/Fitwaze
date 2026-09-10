"""User and RefreshToken models.

Security notes:
- `password_hash` stores an Argon2id hash (see `app/security/passwords.py`).
  It is never serialized in any Pydantic response schema and is excluded
  from log output by `app/logging_config.py`.
- `refresh_tokens.token_hash` stores a SHA-256 hash of the opaque refresh
  token; the raw token is never persisted (see `app/security/tokens.py`).
- All primary keys are UUIDs to avoid ID-enumeration.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.types import GUID


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    # Additional relationships (health_profile, activity_recommendations,
    # activity_sessions, progress_summaries) are added in
    # `app/models/relationships.py` once those models exist, to keep this
    # module importable independent of build order.

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<User id={self.id}>"


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)

    # Token family tracking for reuse-detection: all tokens issued from the
    # same original login/refresh chain share a family_id. If a revoked token
    # is presented again, we revoke the whole family.
    family_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, index=True)

    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped["User"] = relationship(back_populates="refresh_tokens")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<RefreshToken id={self.id} user_id={self.user_id}>"
