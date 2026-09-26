"""HealthProfile model (1:1 with User).

Sensitivity tiers, per the approved design:
- Low sensitivity, stored in plaintext: age, sex, goal, preferred_activity,
  current_weekly_minutes, current_frequency.
- High sensitivity, ENCRYPTED AT REST (AES-256-GCM via
  `app.models.types.EncryptedString`): height_cm, weight_kg,
  diabetes_status, mobility_limitations, walking_ability, cycling_ability.
  (The build plan's data-model section lists all six of these fields as
  needing encryption; the security-requirements section calls out four by
  example. We encrypt the full set of six as the more conservative,
  privacy-protective superset — see docs/data-retention-policy.md.)

Encrypted columns are stored as `Text` holding the enum's `.value` (or the
numeric value's string form for height/weight); decryption only happens when
building an API response for the authenticated owner, or in-process for the
Activity Engine.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, Integer, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.enums import GoalEnum, PreferredActivityEnum, SexEnum
from app.models.types import EncryptedString, GUID


class HealthProfile(Base):
    __tablename__ = "health_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )

    # --- Low sensitivity: plaintext ---
    age: Mapped[int] = mapped_column(Integer, nullable=False)
    sex: Mapped[SexEnum] = mapped_column(
        SAEnum(SexEnum, values_callable=lambda e: [m.value for m in e]), nullable=False
    )
    goal: Mapped[GoalEnum] = mapped_column(
        SAEnum(GoalEnum, values_callable=lambda e: [m.value for m in e]), nullable=False
    )
    preferred_activity: Mapped[PreferredActivityEnum] = mapped_column(
        SAEnum(PreferredActivityEnum, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    current_weekly_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    current_frequency: Mapped[int] = mapped_column(Integer, nullable=False)

    # --- High sensitivity: encrypted at rest (AES-256-GCM, see
    # `app.models.types.EncryptedString`). SQLAlchemy transparently decrypts
    # these on read and encrypts on write, so the Python-side attribute
    # always holds the plaintext string form (numeric fields as strings,
    # enum fields as their `.value`) once loaded from the DB.
    height_cm: Mapped[str] = mapped_column(EncryptedString(), nullable=False)
    weight_kg: Mapped[str] = mapped_column(EncryptedString(), nullable=False)
    diabetes_status: Mapped[str] = mapped_column(EncryptedString(), nullable=False)
    mobility_limitations: Mapped[str] = mapped_column(EncryptedString(), nullable=False)
    walking_ability: Mapped[str] = mapped_column(EncryptedString(), nullable=False)
    cycling_ability: Mapped[str] = mapped_column(EncryptedString(), nullable=False)

    # Where the person lives, so every route starts and ends at home. A home
    # address is as sensitive as anything here, so it is encrypted too, and it
    # is optional: routes can always start from wherever the person is instead.
    home_latitude: Mapped[str | None] = mapped_column(EncryptedString(), nullable=True)
    home_longitude: Mapped[str | None] = mapped_column(EncryptedString(), nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    user = relationship("User", backref="health_profile", uselist=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<HealthProfile user_id={self.user_id}>"


def apply_profile_data(profile: "HealthProfile", data) -> None:
    """Populate a HealthProfile ORM instance from a HealthProfileData-shaped
    Pydantic object. Encryption of the sensitive fields happens transparently
    at flush time via the `EncryptedString` column type."""
    profile.age = data.age
    profile.sex = data.sex
    profile.goal = data.goal
    profile.preferred_activity = data.preferred_activity
    profile.current_weekly_minutes = data.current_weekly_minutes
    profile.current_frequency = data.current_frequency
    profile.height_cm = str(data.height_cm)
    profile.weight_kg = str(data.weight_kg)
    profile.diabetes_status = data.diabetes_status.value
    profile.mobility_limitations = data.mobility_limitations.value
    profile.walking_ability = data.walking_ability.value
    profile.cycling_ability = data.cycling_ability.value


def profile_to_data(profile: "HealthProfile"):
    """Build a `HealthProfileData` from an ORM instance, decrypting the
    sensitive fields (already decrypted transparently by SQLAlchemy on
    attribute access) and casting them back to their proper types/enums."""
    from app.models.enums import AbilityEnum, DiabetesStatusEnum, MobilityLimitationEnum
    from app.schemas.profile import HealthProfileData

    return HealthProfileData(
        age=profile.age,
        sex=profile.sex,
        goal=profile.goal,
        preferred_activity=profile.preferred_activity,
        current_weekly_minutes=profile.current_weekly_minutes,
        current_frequency=profile.current_frequency,
        height_cm=float(profile.height_cm),
        weight_kg=float(profile.weight_kg),
        diabetes_status=DiabetesStatusEnum(profile.diabetes_status),
        mobility_limitations=MobilityLimitationEnum(profile.mobility_limitations),
        walking_ability=AbilityEnum(profile.walking_ability),
        cycling_ability=AbilityEnum(profile.cycling_ability),
    )
