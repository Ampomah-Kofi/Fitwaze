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
    FeelingEnum,
    GoalEnum,
    MobilityLimitationEnum,
    PreferredActivityEnum,
)
from app.schemas.activity import DailyCheckIn
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


# --- Daily check-in thresholds (mmol/L) -------------------------------------
# Based on widely used patient guidance for exercising with diabetes (e.g. the
# ADA's 2016 position statement on physical activity and diabetes). They are
# product safety rules for a wellness app, deliberately named and module-level
# so a clinician can review and adjust them; they are not a treatment plan.
HYPO_BELOW_MMOL_L = 3.9  # 70 mg/dL: treat the low first, do not start
LOW_NORMAL_BELOW_MMOL_L = 5.6  # 100 mg/dL: fine, but have a snack first
HIGH_FROM_MMOL_L = 13.9  # 250 mg/dL: keep it short and gentle
VERY_HIGH_FROM_MMOL_L = 16.7  # 300 mg/dL: skip until it comes down

# People on insulin or a sulfonylurea can go low during or after exercise, so
# the "have a snack first" band starts higher for them (110 mg/dL).
MEDS_LOW_NORMAL_BELOW_MMOL_L = 6.1

HIGH_GLUCOSE_MAX_MINUTES = 15
TIRED_DURATION_FACTOR = 0.75


# --- Adapting to recent walks -----------------------------------------------
# Small, gradual steps: the usual advice is to build activity up slowly.
PROGRESSION_STEP_FRACTION = 0.10   # add about 10% after comfortable walks...
PROGRESSION_MIN_STEP = 2           # ...and at least 2 minutes
PROGRESSION_HEADROOM = 10          # never more than 10 min above the profile band
MAX_SESSION_MINUTES = 60
HARD_REDUCTION_FACTOR = 0.85


@dataclass
class RecentActivity:
    """One recent finished or abandoned session, newest first."""

    status: str                 # "completed" or "abandoned"
    effort: str | None = None   # "easy", "just_right", "hard" or None


class ActivityUnavailableError(ValueError):
    """No requested walking/cycling activity is supported by this profile."""


class CheckInHoldError(ActivityUnavailableError):
    """Today's check-in says this is not a day to start exercising.

    Subclasses ActivityUnavailableError so callers that already handle "no
    activity today" handle this too; the message tells the person what to do
    instead.
    """


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

    if profile.walking_ability == AbilityEnum.unable:
        if profile.cycling_ability == AbilityEnum.unable:
            raise ActivityUnavailableError("Your current profile does not support a walking or cycling recommendation. Review your abilities before continuing.")
        return ActivityTypeEnum.cycle, "Based on the abilities you shared, we're recommending cycling rather than walking."

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


def _check_in_hold(checkin: DailyCheckIn) -> str | None:
    """Why today's check-in rules out starting any activity, or None."""
    if checkin.warning_symptoms:
        return (
            "The symptoms you reported need attention before exercise. Please rest and contact "
            "your doctor or nurse today. If they are severe or getting worse, seek emergency help."
        )
    if checkin.feeling == FeelingEnum.unwell:
        return (
            "You told us you feel unwell, so today is a rest day. Check your blood sugar more "
            "often while you are ill, and contact your care team if you are not improving."
        )
    glucose = checkin.glucose_mmol_l
    if glucose is not None and glucose < HYPO_BELOW_MMOL_L:
        return (
            "Your blood sugar is low. Treat it first with 15 g of fast sugar (such as half a glass "
            "of juice or 3-4 glucose tablets), recheck after 15 minutes, and check in again once "
            "it is back in range."
        )
    if glucose is not None and glucose >= VERY_HIGH_FROM_MMOL_L:
        return (
            "Your blood sugar is very high for exercise right now. Drink water, take your usual "
            "medication as prescribed, and check in again when it has come down. Contact your "
            "care team if it stays this high."
        )
    return None


def _apply_check_in(
    duration_minutes: int, checkin: DailyCheckIn, takes_glucose_lowering_medication: bool = False
) -> tuple[int, list[str]]:
    """Adjust today's duration for how the person is this morning.

    Like the other rules, notes describe which band applied and never repeat
    the reading itself.
    """
    notes: list[str] = []
    glucose = checkin.glucose_mmol_l

    if checkin.feeling == FeelingEnum.tired:
        duration_minutes = max(_MIN_DURATION_MINUTES, round(duration_minutes * TIRED_DURATION_FACTOR))
        notes.append("You said you feel tired this morning, so we've shortened today's session.")

    snack_below = MEDS_LOW_NORMAL_BELOW_MMOL_L if takes_glucose_lowering_medication else LOW_NORMAL_BELOW_MMOL_L
    if glucose is not None:
        if glucose < snack_below:
            notes.append(
                "Your blood sugar is on the low side for exercise: have a small carbohydrate snack "
                "before you start and carry fast sugar with you."
            )
        elif glucose >= HIGH_FROM_MMOL_L:
            duration_minutes = min(duration_minutes, HIGH_GLUCOSE_MAX_MINUTES)
            notes.append(
                "Your blood sugar is high, so keep today's session short and gentle, drink water "
                "before and after, and stop if you feel unwell."
            )
        else:
            notes.append("Your blood sugar is in a good range for activity this morning.")

    return duration_minutes, notes


def _apply_recent_activity(
    duration_minutes: int, band_high: int, recent: list[RecentActivity]
) -> tuple[int, str | None]:
    """Nudge today's length from how the last few sessions went."""
    if not recent:
        return duration_minutes, None
    last = recent[0]
    if last.status == "completed" and last.effort == "hard":
        return max(_MIN_DURATION_MINUTES, round(duration_minutes * HARD_REDUCTION_FACTOR)), (
            "Your last walk felt hard, so today's is a little shorter."
        )
    if last.status == "abandoned":
        return duration_minutes, "Your last session ended early, so we're keeping today's the same length."
    comfortable = [r for r in recent[:2] if r.status == "completed" and r.effort in ("easy", "just_right")]
    if len(comfortable) == 2:
        step = max(PROGRESSION_MIN_STEP, round(duration_minutes * PROGRESSION_STEP_FRACTION))
        increased = min(duration_minutes + step, band_high + PROGRESSION_HEADROOM, MAX_SESSION_MINUTES)
        if increased > duration_minutes:
            return increased, "You've finished your last walks comfortably, so we've added a few minutes."
    return duration_minutes, None


def recommend_activity(
    profile: HealthProfileData,
    activity_type: ActivityTypeEnum | None = None,
    checkin: DailyCheckIn | None = None,
    recent: list[RecentActivity] | None = None,
) -> ActivityRecommendationResult:
    foot_note = None
    if checkin is not None:
        hold = _check_in_hold(checkin)
        if hold:
            raise CheckInHoldError(hold)
        if checkin.foot_problem:
            # Walking on a foot wound is how a small diabetic foot problem
            # becomes a serious one: take walking off the table for today.
            if profile.cycling_ability == AbilityEnum.unable:
                raise CheckInHoldError(
                    "With a sore, blister or swelling on your feet, please don't walk on it today. "
                    "Keep it clean and covered, and have it checked by your doctor or nurse."
                )
            if activity_type == ActivityTypeEnum.walk:
                raise ActivityUnavailableError(
                    "With a sore, blister or swelling on your feet, please don't walk on it today. "
                    "Choose cycling for this session instead, or rest."
                )
            activity_type = ActivityTypeEnum.cycle
            foot_note = (
                "Because of the foot problem you reported, we're suggesting cycling today to keep "
                "weight off your feet. Have it checked if it is not healing."
            )

    if foot_note:
        activity_reason = foot_note
    elif activity_type is None:
        activity_type, activity_reason = _pick_activity_type(profile)
    else:
        ability = profile.walking_ability if activity_type == ActivityTypeEnum.walk else profile.cycling_ability
        if ability == AbilityEnum.unable:
            raise ActivityUnavailableError("The activity you chose is unavailable based on your saved abilities. Choose another activity or review your profile.")
        activity_reason = "You chose " + ("walking" if activity_type == ActivityTypeEnum.walk else "cycling") + " for this session. The duration is based on your saved profile."

    band_low, band_high, band_reason = _baseline_band(profile.current_weekly_minutes)
    duration, goal_note = _apply_goal_adjustment(band_low, band_high, profile.goal)
    duration, diabetes_note = _apply_diabetes_gradual_pacing(
        duration, band_low, band_high, profile.diabetes_status
    )
    duration, mobility_note = _apply_mobility_reduction(duration, profile.mobility_limitations)
    duration, recent_note = _apply_recent_activity(duration, band_high, recent or [])

    notes = [activity_reason, band_reason]
    for note in (goal_note, diabetes_note, mobility_note, recent_note):
        if note:
            notes.append(note)

    if checkin is not None:
        duration, checkin_notes = _apply_check_in(duration, checkin, profile.takes_glucose_lowering_medication)
        notes.extend(checkin_notes)
    if profile.takes_glucose_lowering_medication:
        notes.append(
            "Because you take insulin or a sulfonylurea, carry fast sugar (glucose tablets or "
            "juice) on every walk, and stop if you feel shaky, sweaty or dizzy."
        )

    rationale = " ".join(notes)

    return ActivityRecommendationResult(
        activity_type=activity_type,
        duration_minutes=duration,
        rationale=rationale,
        disclaimer=DISCLAIMER,
        rationale_notes=notes,
    )


def after_walk_advice(
    glucose_mmol_l: float | None, effort: str | None, takes_glucose_lowering_medication: bool = False
) -> str:
    """What to tell someone after they finish, from how it felt and their
    blood sugar afterwards. Like the other rules, it never repeats the reading."""
    parts: list[str] = []
    if glucose_mmol_l is not None:
        if glucose_mmol_l < HYPO_BELOW_MMOL_L:
            parts.append(
                "Your blood sugar is low. Take 15 g of fast sugar now (half a glass of juice or "
                "3-4 glucose tablets), sit down, and recheck in 15 minutes."
            )
        elif glucose_mmol_l < LOW_NORMAL_BELOW_MMOL_L or (
            takes_glucose_lowering_medication and glucose_mmol_l < MEDS_LOW_NORMAL_BELOW_MMOL_L
        ):
            parts.append(
                "Your blood sugar is on the low side after exercise. Have a small snack and "
                "check again in an hour."
            )
        elif glucose_mmol_l >= VERY_HIGH_FROM_MMOL_L:
            parts.append(
                "Your blood sugar is high. Drink water, and contact your care team if it stays high."
            )
        else:
            parts.append("Your blood sugar is in a good range after your activity.")
    if takes_glucose_lowering_medication:
        parts.append("Lows can happen hours after exercise, so check again before bed.")
    if effort == "hard":
        parts.append("It felt hard, so take it easy today. Tell your care team if walks keep feeling this hard.")
    elif effort == "easy":
        parts.append("It felt easy: great progress.")
    parts.append("Well done for getting out today.")
    return " ".join(parts)
