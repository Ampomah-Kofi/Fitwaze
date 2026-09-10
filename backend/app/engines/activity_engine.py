"""Activity Engine: pure-function recommendation logic.

`recommend_activity(profile)` has no DB/network/FastAPI dependency — it's a
deterministic function of a `HealthProfileData` value, which makes it easy
to unit test every rule branch in isolation (see
`backend/tests/test_activity_engine.py`).

Security/privacy note: rationale strings are built ONLY from template
sentences describing *which rule fired* (e.g. "you're averaging under an
hour a week currently"). Raw stored health values (exact diabetes_status,
weight, etc.) are never interpolated into the rationale text — this keeps
the recommendation UI from becoming an unintentional health-data leak
surface (e.g. in shared screenshots, browser history, or logs that happen to
capture response bodies).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.models.enums import (
    AbilityEnum,
    ActivityTypeEnum,
    DiabetesStatusEnum,
    GoalEnum,
    MobilityLimitationEnum,
    PreferredActivityEnum,
)
from app.schemas.profile import HealthProfileData

DISCLAIMER = "This is general wellness guidance, not medical advice"

_GOAL_HIGH_END_BAND = {
    GoalEnum.weight_management,
    GoalEnum.manage_prediabetes,
    GoalEnum.manage_type2,
}

_RECOMMENDED_FREQUENCY_NOTE = "5x/week"

_MOBILITY_DURATION_FACTOR = 0.8
_MIN_DURATION_MINUTES = 10


@dataclass
class ActivityRecommendationResult:
    activity_type: ActivityTypeEnum
    duration_minutes: int
    rationale: str
    disclaimer: str = DISCLAIMER
    rationale_notes: list[str] = field(default_factory=list)


def _pick_activity_type(profile: HealthProfileData) -> tuple[ActivityTypeEnum, str]:
    """Rule 1: honor `preferred_activity` if the corresponding ability isn't
    "unable"; else if mobility_limitations impede walking but cycling_ability
    is fine, pick cycle; else default to walk."""
    preferred = profile.preferred_activity

    if preferred == PreferredActivityEnum.walk and profile.walking_ability != AbilityEnum.unable:
        return ActivityTypeEnum.walk, (
            "You told us you prefer walking, and walking is a good fit for you, "
            "so we're recommending a walking session."
        )

    if preferred == PreferredActivityEnum.cycle and profile.cycling_ability != AbilityEnum.unable:
        return ActivityTypeEnum.cycle, (
            "You told us you prefer cycling, and cycling is a good fit for you, "
            "so we're recommending a cycling session."
        )

    walking_impeded = (
        profile.mobility_limitations != MobilityLimitationEnum.none
        and profile.walking_ability != AbilityEnum.full
    )
    if walking_impeded and profile.cycling_ability != AbilityEnum.unable:
        return ActivityTypeEnum.cycle, (
            "Walking may currently be more difficult for you, and cycling is "
            "available as a comfortable alternative, so we're recommending cycling."
        )

    return ActivityTypeEnum.walk, (
        "We're recommending a walking session as a safe, accessible default."
    )


def _baseline_band(current_weekly_minutes: int) -> tuple[int, int, str]:
    """Rule 2: baseline duration band from current_weekly_minutes."""
    if current_weekly_minutes < 60:
        return 10, 15, (
            "Because you're averaging under an hour a week of activity currently, "
            "we're starting with a short, manageable session."
        )
    if current_weekly_minutes <= 150:
        return 20, 30, (
            "Because you're already averaging a moderate amount of weekly activity, "
            "we're recommending a moderate-length session."
        )
    return 30, 45, (
        "Because you're already averaging a high amount of weekly activity, "
        "we're recommending a longer session."
    )


def _apply_goal_adjustment(
    band_low: int, band_high: int, goal: GoalEnum
) -> tuple[int, str | None]:
    """Rule 3: bias toward the higher end of the band for weight-management /
    diabetes-management goals (with a recommended frequency note); otherwise
    use the band midpoint for general fitness."""
    if goal in _GOAL_HIGH_END_BAND:
        duration = band_high
        note = (
            "Since your goal involves weight management or blood-sugar management, "
            f"we're leaning toward the higher end of that range, aiming for about "
            f"{_RECOMMENDED_FREQUENCY_NOTE} for the best benefit."
        )
        return duration, note

    duration = round((band_low + band_high) / 2)
    return duration, None


def _apply_diabetes_gradual_pacing(
    duration_minutes: int, band_low: int, band_high: int, diabetes_status: DiabetesStatusEnum
) -> tuple[int, str | None]:
    """Rule 4: if diabetes_status != none, keep session duration increases
    gradual/small rather than jumping to the top of the band."""
    if diabetes_status == DiabetesStatusEnum.none:
        return duration_minutes, None

    gradual_duration = round((band_low + band_high) / 2)
    duration_minutes = min(duration_minutes, gradual_duration)
    note = (
        "We're keeping increases in activity gradual and consistent rather than "
        "jumping in duration all at once, which tends to be a more sustainable approach."
    )
    return duration_minutes, note


def _apply_mobility_reduction(
    duration_minutes: int, mobility_limitations: MobilityLimitationEnum
) -> tuple[int, str | None]:
    """Rule 5: if mobility_limitations present, reduce duration and flag
    low-impact pacing."""
    if mobility_limitations == MobilityLimitationEnum.none:
        return duration_minutes, None

    reduced = max(_MIN_DURATION_MINUTES, round(duration_minutes * _MOBILITY_DURATION_FACTOR))
    note = (
        "Given the mobility considerations you shared, we're using a shorter, "
        "low-impact pacing to keep the session comfortable and sustainable."
    )
    return reduced, note


def recommend_activity(profile: HealthProfileData) -> ActivityRecommendationResult:
    activity_type, activity_reason = _pick_activity_type(profile)

    band_low, band_high, band_reason = _baseline_band(profile.current_weekly_minutes)
    duration, goal_note = _apply_goal_adjustment(band_low, band_high, profile.goal)
    duration, diabetes_note = _apply_diabetes_gradual_pacing(
        duration, band_low, band_high, profile.diabetes_status
    )
    duration, mobility_note = _apply_mobility_reduction(duration, profile.mobility_limitations)

    notes = [activity_reason, band_reason]
    for note in (goal_note, diabetes_note, mobility_note):
        if note:
            notes.append(note)

    rationale = " ".join(notes)

    return ActivityRecommendationResult(
        activity_type=activity_type,
        duration_minutes=duration,
        rationale=rationale,
        disclaimer=DISCLAIMER,
        rationale_notes=notes,
    )
