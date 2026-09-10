"""Unit tests for the pure Activity Engine function `recommend_activity`.

Each test targets one rule branch from the approved spec:
1. activity_type selection (preference/ability/mobility fallback)
2. baseline duration band from current_weekly_minutes
3. goal-based adjustment (high end + frequency note vs. midpoint)
4. gradual pacing when diabetes_status != none
5. reduced, low-impact duration when mobility_limitations present
"""
from __future__ import annotations

from app.engines.activity_engine import DISCLAIMER, recommend_activity
from app.models.enums import (
    AbilityEnum,
    ActivityTypeEnum,
    DiabetesStatusEnum,
    GoalEnum,
    MobilityLimitationEnum,
    PreferredActivityEnum,
    SexEnum,
)
from app.schemas.profile import HealthProfileData


def make_profile(**overrides) -> HealthProfileData:
    base = dict(
        age=35,
        sex=SexEnum.female,
        goal=GoalEnum.general_fitness,
        preferred_activity=PreferredActivityEnum.walk,
        current_weekly_minutes=90,
        current_frequency=3,
        height_cm=170.0,
        weight_kg=70.0,
        diabetes_status=DiabetesStatusEnum.none,
        mobility_limitations=MobilityLimitationEnum.none,
        walking_ability=AbilityEnum.full,
        cycling_ability=AbilityEnum.full,
    )
    base.update(overrides)
    return HealthProfileData(**base)


# --- Rule 1: activity_type selection ---------------------------------------


def test_honors_preferred_walk_when_walking_ability_not_unable():
    profile = make_profile(preferred_activity=PreferredActivityEnum.walk, walking_ability=AbilityEnum.limited)
    result = recommend_activity(profile)
    assert result.activity_type == ActivityTypeEnum.walk


def test_honors_preferred_cycle_when_cycling_ability_not_unable():
    profile = make_profile(preferred_activity=PreferredActivityEnum.cycle, cycling_ability=AbilityEnum.limited)
    result = recommend_activity(profile)
    assert result.activity_type == ActivityTypeEnum.cycle


def test_falls_back_to_cycle_when_walking_impeded_and_cycling_fine():
    profile = make_profile(
        preferred_activity=PreferredActivityEnum.walk,
        walking_ability=AbilityEnum.unable,
        mobility_limitations=MobilityLimitationEnum.moderate,
        cycling_ability=AbilityEnum.full,
    )
    result = recommend_activity(profile)
    assert result.activity_type == ActivityTypeEnum.cycle


def test_defaults_to_walk_when_no_preference_and_no_mobility_issue():
    profile = make_profile(preferred_activity=PreferredActivityEnum.no_preference)
    result = recommend_activity(profile)
    assert result.activity_type == ActivityTypeEnum.walk


def test_defaults_to_walk_when_preferred_unable_and_cycling_also_unable():
    profile = make_profile(
        preferred_activity=PreferredActivityEnum.cycle,
        cycling_ability=AbilityEnum.unable,
        walking_ability=AbilityEnum.full,
        mobility_limitations=MobilityLimitationEnum.none,
    )
    result = recommend_activity(profile)
    assert result.activity_type == ActivityTypeEnum.walk


# --- Rule 2: baseline duration bands ----------------------------------------


def test_band_under_60_minutes_gives_short_session():
    profile = make_profile(current_weekly_minutes=30, goal=GoalEnum.general_fitness)
    result = recommend_activity(profile)
    assert 10 <= result.duration_minutes <= 15


def test_band_60_to_150_minutes_gives_moderate_session():
    profile = make_profile(current_weekly_minutes=100, goal=GoalEnum.general_fitness)
    result = recommend_activity(profile)
    assert 20 <= result.duration_minutes <= 30


def test_band_over_150_minutes_gives_longer_session():
    profile = make_profile(current_weekly_minutes=200, goal=GoalEnum.general_fitness)
    result = recommend_activity(profile)
    assert 30 <= result.duration_minutes <= 45


# --- Rule 3: goal adjustment -------------------------------------------------


def test_general_fitness_uses_band_midpoint():
    profile = make_profile(current_weekly_minutes=100, goal=GoalEnum.general_fitness)
    result = recommend_activity(profile)
    assert result.duration_minutes == 25  # midpoint of 20-30


def test_weight_management_biases_to_band_high_end_and_notes_frequency():
    profile = make_profile(current_weekly_minutes=100, goal=GoalEnum.weight_management)
    result = recommend_activity(profile)
    assert result.duration_minutes == 30  # top of 20-30 band
    assert "5x/week" in result.rationale


def test_manage_type2_biases_to_band_high_end():
    profile = make_profile(current_weekly_minutes=30, goal=GoalEnum.manage_type2, diabetes_status=DiabetesStatusEnum.none)
    result = recommend_activity(profile)
    assert result.duration_minutes == 15  # top of 10-15 band


# --- Rule 4: diabetes gradual pacing -----------------------------------------


def test_diabetes_status_caps_duration_to_gradual_midpoint_even_for_weight_goal():
    with_diabetes = make_profile(
        current_weekly_minutes=100,
        goal=GoalEnum.weight_management,
        diabetes_status=DiabetesStatusEnum.type2,
    )
    without_diabetes = make_profile(
        current_weekly_minutes=100,
        goal=GoalEnum.weight_management,
        diabetes_status=DiabetesStatusEnum.none,
    )
    result_with = recommend_activity(with_diabetes)
    result_without = recommend_activity(without_diabetes)

    assert result_with.duration_minutes < result_without.duration_minutes
    assert result_with.duration_minutes == 25  # midpoint, not band-high (30)
    assert "gradual" in result_with.rationale.lower()


def test_disclaimer_always_present_and_not_medical_advice():
    profile = make_profile(diabetes_status=DiabetesStatusEnum.prediabetes)
    result = recommend_activity(profile)
    assert result.disclaimer == DISCLAIMER
    assert "not medical advice" in result.disclaimer.lower()

    # Disclaimer is present even with no diabetes history.
    profile_none = make_profile(diabetes_status=DiabetesStatusEnum.none)
    assert recommend_activity(profile_none).disclaimer == DISCLAIMER


def test_rationale_never_echoes_raw_diabetes_status_value():
    profile = make_profile(diabetes_status=DiabetesStatusEnum.type2)
    result = recommend_activity(profile)
    assert "type2" not in result.rationale
    assert "prediabetes" not in result.rationale


# --- Rule 5: mobility limitations reduce duration ---------------------------


def test_mobility_limitations_reduce_duration_and_flag_low_impact_pacing():
    with_limitation = make_profile(
        current_weekly_minutes=100,
        goal=GoalEnum.general_fitness,
        mobility_limitations=MobilityLimitationEnum.moderate,
        walking_ability=AbilityEnum.limited,
    )
    without_limitation = make_profile(current_weekly_minutes=100, goal=GoalEnum.general_fitness)

    result_with = recommend_activity(with_limitation)
    result_without = recommend_activity(without_limitation)

    assert result_with.duration_minutes < result_without.duration_minutes
    assert "low-impact pacing" in result_with.rationale


def test_mobility_reduction_never_drops_below_minimum_floor():
    profile = make_profile(
        current_weekly_minutes=10,
        goal=GoalEnum.general_fitness,
        mobility_limitations=MobilityLimitationEnum.severe,
        walking_ability=AbilityEnum.limited,
    )
    result = recommend_activity(profile)
    assert result.duration_minutes >= 10


def test_rationale_never_echoes_raw_mobility_value():
    profile = make_profile(mobility_limitations=MobilityLimitationEnum.severe, walking_ability=AbilityEnum.limited)
    result = recommend_activity(profile)
    assert "severe" not in result.rationale
