"""Unit tests for the Route Engine's scoring math (`app/engines/route_engine/scoring.py`)
and the mock provider's deterministic route generation."""
from __future__ import annotations

import math

from app.config import get_settings
from app.engines.route_engine.base import RawRoute, RouteProvider
from app.engines.route_engine.cache import clear_route_cache, get_candidate_routes_cached
from app.engines.route_engine.mock_provider import MockRouteProvider
from app.engines.route_engine.scoring import (
    CYCLE_WEIGHTS,
    MOBILITY_EMPHASIS,
    OLDER_ADULT_EMPHASIS,
    WALK_WEIGHTS,
    personalised_weights,
    score_and_rank_routes,
    score_route,
    terrain_emphasis,
)
from app.models.enums import ActivityTypeEnum, MobilityLimitationEnum


def test_walk_weights_sum_to_one():
    assert math.isclose(sum(WALK_WEIGHTS.values()), 1.0, abs_tol=1e-9)


def test_cycle_weights_sum_to_one():
    assert math.isclose(sum(CYCLE_WEIGHTS.values()), 1.0, abs_tol=1e-9)


def _perfect_walk_route(estimated_minutes: float) -> RawRoute:
    return RawRoute(
        geometry=[(0.0, 0.0)],
        distance_m=1000.0,
        estimated_minutes=estimated_minutes,
        sidewalk_score=1.0,
        traffic_exposure=0.0,
        major_crossings=0.0,
        slope=0.0,
        stairs=0.0,
        trail_bonus=1.0,
        safety_score=1.0,
    )


def _worst_walk_route(estimated_minutes: float) -> RawRoute:
    return RawRoute(
        geometry=[(0.0, 0.0)],
        distance_m=1000.0,
        estimated_minutes=estimated_minutes,
        sidewalk_score=0.0,
        traffic_exposure=1.0,
        major_crossings=1.0,
        slope=1.0,
        stairs=1.0,
        trail_bonus=0.0,
        safety_score=0.0,
    )


def test_perfect_walk_route_with_matching_duration_scores_100():
    route = _perfect_walk_route(estimated_minutes=30)
    result = score_route(route, ActivityTypeEnum.walk, target_duration_min=30)
    assert result.score == 100.0


def test_worst_walk_route_with_completely_mismatched_duration_scores_0():
    # estimated_minutes=60 vs target=30 -> duration_match diff_ratio = 1.0 -> 0
    route = _worst_walk_route(estimated_minutes=60)
    result = score_route(route, ActivityTypeEnum.walk, target_duration_min=30)
    assert result.score == 0.0


def test_worst_walk_route_with_matching_duration_scores_duration_match_weight_only():
    # Duration matches perfectly (duration_match=1.0) but every other factor
    # is worst-case (0.0), so the score should equal exactly the
    # duration_match weight's contribution (25% -> 25.0).
    route = _worst_walk_route(estimated_minutes=30)
    result = score_route(route, ActivityTypeEnum.walk, target_duration_min=30)
    assert result.score == WALK_WEIGHTS["duration_match"] * 100


def test_duration_mismatch_reduces_score():
    on_target = score_route(_perfect_walk_route(30), ActivityTypeEnum.walk, target_duration_min=30)
    off_target = score_route(_perfect_walk_route(60), ActivityTypeEnum.walk, target_duration_min=30)
    assert off_target.score < on_target.score


def test_score_breakdown_contains_expected_walk_factors():
    route = _perfect_walk_route(30)
    result = score_route(route, ActivityTypeEnum.walk, target_duration_min=30)
    assert set(result.breakdown.keys()) == set(WALK_WEIGHTS.keys())


def test_score_breakdown_contains_expected_cycle_factors():
    route = RawRoute(
        geometry=[(0.0, 0.0)],
        distance_m=5000.0,
        estimated_minutes=20,
        bike_lane_score=0.9,
        traffic_stress=0.1,
        intersection_complexity=0.1,
        slope=0.1,
        continuity_score=0.9,
    )
    result = score_route(route, ActivityTypeEnum.cycle, target_duration_min=20)
    assert set(result.breakdown.keys()) == set(CYCLE_WEIGHTS.keys())


def test_perfect_cycle_route_scores_100():
    route = RawRoute(
        geometry=[(0.0, 0.0)],
        distance_m=5000.0,
        estimated_minutes=20,
        bike_lane_score=1.0,
        traffic_stress=0.0,
        intersection_complexity=0.0,
        slope=0.0,
        continuity_score=1.0,
    )
    result = score_route(route, ActivityTypeEnum.cycle, target_duration_min=20)
    assert result.score == 100.0


def test_explanation_mentions_trail_for_high_trail_bonus_route():
    route = _perfect_walk_route(30)
    result = score_route(route, ActivityTypeEnum.walk, target_duration_min=30)
    assert "trail" in result.explanation.lower() or "scores well" in result.explanation.lower()


def test_explanation_includes_caveat_for_weak_factor():
    route = RawRoute(
        geometry=[(0.0, 0.0)],
        distance_m=1000.0,
        estimated_minutes=30,
        sidewalk_score=0.9,
        traffic_exposure=0.05,
        major_crossings=0.05,
        slope=0.05,
        trail_bonus=0.9,
        safety_score=0.05,  # weakest factor
    )
    result = score_route(route, ActivityTypeEnum.walk, target_duration_min=30)
    assert "tradeoff" in result.explanation.lower()


def test_score_and_rank_routes_orders_descending_and_limits_to_top_n():
    routes = [
        _worst_walk_route(30),
        _perfect_walk_route(30),
        RawRoute(
            geometry=[(0.0, 0.0)],
            distance_m=1000,
            estimated_minutes=30,
            sidewalk_score=0.5,
            traffic_exposure=0.5,
            major_crossings=0.5,
            slope=0.5,
            trail_bonus=0.5,
            safety_score=0.5,
        ),
    ]
    ranked = score_and_rank_routes(routes, ActivityTypeEnum.walk, target_duration_min=30, top_n=2)
    assert len(ranked) == 2
    assert ranked[0][1].score >= ranked[1][1].score
    assert ranked[0][1].score == 100.0


# --- MockRouteProvider determinism ------------------------------------------


def test_mock_provider_is_deterministic_for_same_coordinates():
    provider = MockRouteProvider()
    first = provider.get_candidate_routes(40.7128, -74.0060, ActivityTypeEnum.walk, 30)
    second = provider.get_candidate_routes(40.7128, -74.0060, ActivityTypeEnum.walk, 30)

    assert [r.label for r in first] == [r.label for r in second]
    assert [r.distance_m for r in first] == [r.distance_m for r in second]
    assert [r.geometry for r in first] == [r.geometry for r in second]


def test_mock_provider_returns_three_distinct_shapes():
    provider = MockRouteProvider()
    routes = provider.get_candidate_routes(40.7128, -74.0060, ActivityTypeEnum.cycle, 45)
    labels = {r.label for r in routes}
    assert labels == {"out_and_back", "small_loop", "large_loop"}


def test_mock_provider_routes_roughly_match_target_duration():
    provider = MockRouteProvider()
    target_minutes = 30
    routes = provider.get_candidate_routes(40.7128, -74.0060, ActivityTypeEnum.walk, target_minutes)
    for route in routes:
        assert 0.5 * target_minutes <= route.estimated_minutes <= 1.5 * target_minutes


# --- Candidate route cache -------------------------------------------------


class _CountingProvider(RouteProvider):
    """Records how many times the provider was actually asked for routes."""

    def __init__(self) -> None:
        self.calls = 0
        self._inner = MockRouteProvider()

    def get_candidate_routes(self, start_lat, start_lon, activity_type, target_duration_min):
        self.calls += 1
        return self._inner.get_candidate_routes(
            start_lat=start_lat,
            start_lon=start_lon,
            activity_type=activity_type,
            target_duration_min=target_duration_min,
        )


def _call(provider, lat=40.7128, lon=-74.0060, duration=30):
    return get_candidate_routes_cached(
        provider,
        start_lat=lat,
        start_lon=lon,
        activity_type=ActivityTypeEnum.walk,
        target_duration_min=duration,
    )


def test_cache_reuses_one_provider_call_for_a_repeated_request():
    clear_route_cache()
    provider = _CountingProvider()

    first = _call(provider)
    second = _call(provider)

    assert provider.calls == 1
    assert [r.label for r in first] == [r.label for r in second]


def test_cache_treats_nearby_start_points_within_11m_as_the_same():
    clear_route_cache()
    provider = _CountingProvider()

    _call(provider, lat=40.71280, lon=-74.00600)
    _call(provider, lat=40.712801, lon=-74.006002)  # ~0.2 m away

    assert provider.calls == 1


def test_cache_separates_different_start_points_and_durations():
    clear_route_cache()
    provider = _CountingProvider()

    _call(provider)
    _call(provider, lat=51.5074, lon=-0.1278)  # different city
    _call(provider, duration=45)  # different target duration

    assert provider.calls == 3


def test_cache_can_be_disabled_by_setting_ttl_to_zero(monkeypatch):
    clear_route_cache()
    provider = _CountingProvider()

    settings = get_settings()
    monkeypatch.setattr(settings, "route_cache_ttl_seconds", 0)

    _call(provider)
    _call(provider)

    assert provider.calls == 2


# --- Personalised scoring --------------------------------------------------


def _profile(**overrides):
    from app.schemas.profile import HealthProfileData

    payload = {
        "age": 34, "sex": "female", "goal": "general_fitness",
        "preferred_activity": "walk", "current_weekly_minutes": 45,
        "current_frequency": 2, "height_cm": 165.5, "weight_kg": 68.2,
        "diabetes_status": "none", "mobility_limitations": "none",
        "walking_ability": "full", "cycling_ability": "full",
    }
    payload.update(overrides)
    return HealthProfileData(**payload)


def _stepped_route():
    """Short, pleasant, but full of stairs and hills."""
    return RawRoute(
        geometry=[(0.0, 0.0)], distance_m=1000.0, estimated_minutes=30,
        sidewalk_score=1.0, traffic_exposure=0.0, major_crossings=0.0,
        slope=0.9, stairs=1.0, trail_bonus=1.0, safety_score=1.0,
    )


def _flat_route():
    """Duller and slightly off-target on duration, but step-free and flat."""
    return RawRoute(
        geometry=[(0.0, 0.0)], distance_m=1000.0, estimated_minutes=36,
        sidewalk_score=0.8, traffic_exposure=0.2, major_crossings=0.2,
        slope=0.05, stairs=0.0, trail_bonus=0.0, safety_score=0.8,
    )


def test_personalised_weights_still_sum_to_one():
    for limitation in ("none", "mild", "moderate", "severe", "prefer_not_to_say"):
        weights = personalised_weights(
            ActivityTypeEnum.walk, _profile(mobility_limitations=limitation)
        )
        assert math.isclose(sum(weights.values()), 1.0, abs_tol=1e-9), limitation


def test_terrain_weight_rises_with_mobility_limitation():
    unrestricted = personalised_weights(ActivityTypeEnum.walk, _profile())
    restricted = personalised_weights(
        ActivityTypeEnum.walk, _profile(mobility_limitations="severe")
    )
    assert restricted["step_free"] > unrestricted["step_free"]
    assert restricted["slope_inv"] > unrestricted["slope_inv"]
    # Emphasis is relative: non-terrain factors give way rather than the score
    # simply inflating.
    assert restricted["duration_match"] < unrestricted["duration_match"]


def test_a_stepped_route_outranks_a_flat_one_only_for_the_unrestricted_walker():
    routes = [_stepped_route(), _flat_route()]

    unrestricted = score_and_rank_routes(
        routes, ActivityTypeEnum.walk, 30, profile=_profile()
    )
    assert unrestricted[0][0].stairs == 1.0  # duration match and trail win out

    restricted = score_and_rank_routes(
        routes, ActivityTypeEnum.walk, 30, profile=_profile(mobility_limitations="moderate")
    )
    assert restricted[0][0].stairs == 0.0  # the step-free route now wins


def test_limited_walking_ability_emphasises_terrain_even_without_a_limitation():
    baseline = personalised_weights(ActivityTypeEnum.walk, _profile())
    limited = personalised_weights(
        ActivityTypeEnum.walk, _profile(walking_ability="limited")
    )
    assert limited["step_free"] > baseline["step_free"]


def test_cycling_ability_does_not_change_a_walking_score():
    baseline = personalised_weights(ActivityTypeEnum.walk, _profile())
    limited_cyclist = personalised_weights(
        ActivityTypeEnum.walk, _profile(cycling_ability="limited")
    )
    assert limited_cyclist == baseline


def test_older_walkers_get_some_terrain_emphasis():
    assert terrain_emphasis(_profile(age=70), ActivityTypeEnum.walk) > 1.0
    assert terrain_emphasis(_profile(age=40), ActivityTypeEnum.walk) == 1.0


def test_emphasis_takes_the_strongest_signal_rather_than_compounding():
    """An older walker who is also mildly limited must not be scored as though
    they were severely limited."""
    combined = terrain_emphasis(
        _profile(age=70, mobility_limitations="mild"), ActivityTypeEnum.walk
    )
    assert combined == max(MOBILITY_EMPHASIS[MobilityLimitationEnum.mild], OLDER_ADULT_EMPHASIS)
    assert combined < MOBILITY_EMPHASIS[MobilityLimitationEnum.severe]


def test_an_undisclosed_limitation_is_treated_cautiously():
    """Choosing not to disclose must not silently mean "no limitation"."""
    assert terrain_emphasis(
        _profile(mobility_limitations="prefer_not_to_say"), ActivityTypeEnum.walk
    ) > 1.0


def test_explanation_mentions_terrain_for_a_restricted_walker():
    result = score_route(
        _flat_route(), ActivityTypeEnum.walk, 30,
        profile=_profile(mobility_limitations="moderate"),
    )
    assert "mobility needs" in result.explanation
    assert "step-free" in result.explanation

    # ...and says nothing of the sort for someone who reported no limitation.
    generic = score_route(_flat_route(), ActivityTypeEnum.walk, 30, profile=_profile())
    assert "mobility needs" not in generic.explanation


def test_explanation_never_echoes_a_stored_health_value():
    """Rationale text is shoulder-surfable; it must not read health data back."""
    result = score_route(
        _stepped_route(), ActivityTypeEnum.walk, 30,
        profile=_profile(mobility_limitations="severe", age=71, weight_kg=94.0),
    )
    for leaked in ("severe", "71", "94"):
        assert leaked not in result.explanation


def test_scoring_without_a_profile_uses_the_generic_weights():
    assert personalised_weights(ActivityTypeEnum.walk, None) == WALK_WEIGHTS
