"""The morning check-in: today's condition shapes today's recommendation.

Covers the safety holds (warning symptoms, feeling unwell, low or very high
blood sugar, a foot problem with no alternative to walking), the softer
adjustments, unit handling, and that the rationale never echoes the reading.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.engines.activity_engine import (
    ActivityUnavailableError,
    CheckInHoldError,
    HIGH_GLUCOSE_MAX_MINUTES,
    recommend_activity,
)
from app.models.enums import AbilityEnum, ActivityTypeEnum, FeelingEnum, GlucoseUnitEnum
from app.schemas.activity import DailyCheckIn
from tests.conftest import auth_headers, register_and_login, valid_profile_payload
from tests.test_activity_engine import make_profile


def checkin(**overrides) -> DailyCheckIn:
    base = dict(feeling=FeelingEnum.good)
    base.update(overrides)
    return DailyCheckIn(**base)


def test_no_checkin_leaves_the_recommendation_unchanged():
    profile = make_profile()
    assert recommend_activity(profile).duration_minutes == recommend_activity(profile, checkin=None).duration_minutes


def test_good_morning_in_range_keeps_the_profile_duration_and_says_so():
    profile = make_profile()
    baseline = recommend_activity(profile).duration_minutes
    result = recommend_activity(profile, checkin=checkin(glucose_value=6.5))
    assert result.duration_minutes == baseline
    assert "good range" in result.rationale


@pytest.mark.parametrize("today", [
    dict(warning_symptoms=True),
    dict(feeling=FeelingEnum.unwell),
    dict(glucose_value=3.2),
    dict(glucose_value=55, glucose_unit=GlucoseUnitEnum.mg_dl),
    dict(glucose_value=18.0),
    dict(glucose_value=320, glucose_unit=GlucoseUnitEnum.mg_dl),
])
def test_unsafe_mornings_hold_activity(today):
    with pytest.raises(CheckInHoldError):
        recommend_activity(make_profile(), checkin=checkin(**today))


def test_high_glucose_caps_the_session_short():
    profile = make_profile(current_weekly_minutes=300)
    result = recommend_activity(profile, checkin=checkin(glucose_value=14.5))
    assert result.duration_minutes <= HIGH_GLUCOSE_MAX_MINUTES
    assert "drink water" in result.rationale


def test_low_normal_glucose_advises_a_snack_without_shortening():
    profile = make_profile()
    baseline = recommend_activity(profile).duration_minutes
    result = recommend_activity(profile, checkin=checkin(glucose_value=4.8))
    assert result.duration_minutes == baseline
    assert "snack" in result.rationale


def test_tired_shortens_the_session():
    profile = make_profile(current_weekly_minutes=300)
    baseline = recommend_activity(profile).duration_minutes
    assert recommend_activity(profile, checkin=checkin(feeling=FeelingEnum.tired)).duration_minutes < baseline


def test_foot_problem_switches_to_cycling():
    result = recommend_activity(make_profile(), checkin=checkin(foot_problem=True))
    assert result.activity_type == ActivityTypeEnum.cycle
    assert "foot" in result.rationale


def test_foot_problem_rejects_an_explicit_walk_choice():
    with pytest.raises(ActivityUnavailableError):
        recommend_activity(make_profile(), ActivityTypeEnum.walk, checkin(foot_problem=True))


def test_foot_problem_without_cycling_is_a_rest_day():
    with pytest.raises(CheckInHoldError):
        recommend_activity(make_profile(cycling_ability=AbilityEnum.unable), checkin=checkin(foot_problem=True))


def test_units_are_equivalent():
    assert checkin(glucose_value=180, glucose_unit=GlucoseUnitEnum.mg_dl).glucose_mmol_l == pytest.approx(10.0)


@pytest.mark.parametrize("value, unit", [(120, GlucoseUnitEnum.mmol_l), (5, GlucoseUnitEnum.mg_dl)])
def test_implausible_readings_are_rejected(value, unit):
    """120 mmol/L is almost certainly 120 mg/dL entered with the wrong unit."""
    with pytest.raises(ValidationError):
        checkin(glucose_value=value, glucose_unit=unit)


@pytest.mark.parametrize("value", [3.5, 4.8, 7.4, 14.2, 19.0])
def test_rationale_never_repeats_the_reading(value):
    try:
        result = recommend_activity(make_profile(), checkin=checkin(glucose_value=value))
        text = result.rationale
    except CheckInHoldError as exc:
        text = str(exc)
    assert str(value) not in text


# --- API --------------------------------------------------------------------


def _user_with_profile(client):
    user = register_and_login(client)
    headers = auth_headers(user["access_token"])
    client.put("/profile", json=valid_profile_payload(), headers=headers)
    return headers


def test_api_accepts_a_checkin(client):
    headers = _user_with_profile(client)
    response = client.post(
        "/activity/recommendation",
        json={"checkin": {"glucose_value": 7.2, "glucose_unit": "mmol/L", "feeling": "okay"}},
        headers=headers,
    )
    assert response.status_code == 200
    assert "good range" in response.json()["rationale"]


def test_api_holds_on_low_glucose_and_says_what_to_do(client):
    headers = _user_with_profile(client)
    response = client.post(
        "/activity/recommendation",
        json={"checkin": {"glucose_value": 60, "glucose_unit": "mg/dL", "feeling": "good"}},
        headers=headers,
    )
    assert response.status_code == 409
    assert "fast sugar" in response.json()["detail"]


def test_api_rejects_a_wrong_unit_reading(client):
    headers = _user_with_profile(client)
    response = client.post(
        "/activity/recommendation",
        json={"checkin": {"glucose_value": 140, "glucose_unit": "mmol/L", "feeling": "good"}},
        headers=headers,
    )
    assert response.status_code == 422
