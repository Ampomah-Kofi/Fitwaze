"""Shared enum types for categorical health/activity fields.

Using strict Python enums (rather than free-text strings) for every
categorical health field is a deliberate security choice: Pydantic schemas
built on these enums reject arbitrary input at validation time (422), rather
than accepting attacker-controlled strings that end up in the database or in
rationale-generation templates.
"""
from __future__ import annotations

import enum


class SexEnum(str, enum.Enum):
    male = "male"
    female = "female"
    other = "other"
    prefer_not_to_say = "prefer_not_to_say"


class GoalEnum(str, enum.Enum):
    general_fitness = "general_fitness"
    weight_management = "weight_management"
    manage_prediabetes = "manage_prediabetes"
    manage_type2 = "manage_type2"


class PreferredActivityEnum(str, enum.Enum):
    walk = "walk"
    cycle = "cycle"
    no_preference = "no_preference"


class DiabetesStatusEnum(str, enum.Enum):
    none = "none"
    prediabetes = "prediabetes"
    type2 = "type2"
    prefer_not_to_say = "prefer_not_to_say"


class AbilityEnum(str, enum.Enum):
    """Used for both `walking_ability` and `cycling_ability`."""

    full = "full"
    limited = "limited"
    unable = "unable"


class MobilityLimitationEnum(str, enum.Enum):
    none = "none"
    mild = "mild"
    moderate = "moderate"
    severe = "severe"
    prefer_not_to_say = "prefer_not_to_say"


class ActivityTypeEnum(str, enum.Enum):
    walk = "walk"
    cycle = "cycle"


class SessionStatusEnum(str, enum.Enum):
    offered = "offered"
    selected = "selected"
    completed = "completed"
    abandoned = "abandoned"
