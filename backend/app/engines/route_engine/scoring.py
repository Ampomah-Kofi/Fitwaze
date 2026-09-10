"""Route scoring: weighted 0-100 score + a plain-language explanation.

Weights are module-level dicts (so they're easy to review/tune) and each
sums to 1.0. `score_route` is a pure function of a `RawRoute` + activity
type + target duration, which makes the scoring math straightforward to
unit test (see `backend/tests/test_route_engine.py`).
"""
from __future__ import annotations

from dataclasses import dataclass

from app.engines.route_engine.base import RawRoute
from app.models.enums import ActivityTypeEnum

WALK_WEIGHTS: dict[str, float] = {
    "duration_match": 0.25,
    "sidewalk_score": 0.20,
    "traffic_exposure_inv": 0.15,
    "major_crossings_inv": 0.10,
    "slope_inv": 0.10,
    "trail_bonus": 0.10,
    "safety_score": 0.10,
}

CYCLE_WEIGHTS: dict[str, float] = {
    "duration_match": 0.25,
    "bike_lane_score": 0.25,
    "traffic_stress_inv": 0.20,
    "intersection_complexity_inv": 0.10,
    "slope_inv": 0.10,
    "continuity_score": 0.10,
}

assert abs(sum(WALK_WEIGHTS.values()) - 1.0) < 1e-9
assert abs(sum(CYCLE_WEIGHTS.values()) - 1.0) < 1e-9

_WALK_FACTOR_DESCRIPTIONS = {
    "duration_match": "closely matches your target session length",
    "sidewalk_score": "has good sidewalk coverage",
    "traffic_exposure_inv": "avoids heavy traffic exposure",
    "major_crossings_inv": "avoids major road crossings",
    "slope_inv": "stays relatively flat",
    "trail_bonus": "uses a marked trail",
    "safety_score": "feels like a safe, well-used route",
}

_CYCLE_FACTOR_DESCRIPTIONS = {
    "duration_match": "closely matches your target session length",
    "bike_lane_score": "stays on marked bike lanes",
    "traffic_stress_inv": "avoids high-stress traffic",
    "intersection_complexity_inv": "avoids complex intersections",
    "slope_inv": "stays relatively flat",
    "continuity_score": "has a smooth, continuous path with few interruptions",
}

_WALK_FACTOR_CAVEATS = {
    "sidewalk_score": "has limited sidewalk coverage in places",
    "traffic_exposure_inv": "runs alongside busier traffic than ideal",
    "major_crossings_inv": "involves a few major road crossings",
    "slope_inv": "has some noticeable hills",
    "trail_bonus": "doesn't use a dedicated trail",
    "safety_score": "has a lower safety score than the other options",
}

_CYCLE_FACTOR_CAVEATS = {
    "bike_lane_score": "has limited bike lane coverage",
    "traffic_stress_inv": "involves higher-stress traffic segments",
    "intersection_complexity_inv": "crosses a few complex intersections",
    "slope_inv": "has some noticeable hills",
    "continuity_score": "has a few interruptions in the path",
}


@dataclass
class RouteScoreResult:
    score: float  # 0-100
    breakdown: dict[str, float]  # factor name -> 0-1 normalized value
    explanation: str


def _duration_match(estimated_minutes: float, target_duration_min: float) -> float:
    if target_duration_min <= 0:
        return 0.0
    diff_ratio = abs(estimated_minutes - target_duration_min) / target_duration_min
    return max(0.0, 1.0 - diff_ratio)


def _factor_values(route: RawRoute, activity_type: ActivityTypeEnum, target_duration_min: float) -> dict[str, float]:
    duration_match = _duration_match(route.estimated_minutes, target_duration_min)

    if activity_type == ActivityTypeEnum.walk:
        return {
            "duration_match": duration_match,
            "sidewalk_score": route.sidewalk_score,
            "traffic_exposure_inv": 1.0 - route.traffic_exposure,
            "major_crossings_inv": 1.0 - route.major_crossings,
            "slope_inv": 1.0 - route.slope,
            "trail_bonus": route.trail_bonus,
            "safety_score": route.safety_score,
        }

    return {
        "duration_match": duration_match,
        "bike_lane_score": route.bike_lane_score,
        "traffic_stress_inv": 1.0 - route.traffic_stress,
        "intersection_complexity_inv": 1.0 - route.intersection_complexity,
        "slope_inv": 1.0 - route.slope,
        "continuity_score": route.continuity_score,
    }


def _weights_for(activity_type: ActivityTypeEnum) -> dict[str, float]:
    return WALK_WEIGHTS if activity_type == ActivityTypeEnum.walk else CYCLE_WEIGHTS


def _generate_explanation(
    activity_type: ActivityTypeEnum, factor_values: dict[str, float], weights: dict[str, float]
) -> str:
    descriptions = _WALK_FACTOR_DESCRIPTIONS if activity_type == ActivityTypeEnum.walk else _CYCLE_FACTOR_DESCRIPTIONS
    caveats = _WALK_FACTOR_CAVEATS if activity_type == ActivityTypeEnum.walk else _CYCLE_FACTOR_CAVEATS

    # Rank factors (excluding duration_match, which is about the request
    # rather than the route's built environment) by their weighted
    # contribution to highlight what actually drove the score.
    contributions = {
        name: value * weights[name]
        for name, value in factor_values.items()
        if name != "duration_match"
    }
    ranked = sorted(contributions.items(), key=lambda kv: kv[1], reverse=True)

    top_positives = [name for name, _ in ranked[:2] if factor_values[name] >= 0.5]
    sentence_parts = [descriptions[name] for name in top_positives]

    if sentence_parts:
        positive_sentence = "This route scores well because it " + " and ".join(sentence_parts) + "."
    else:
        positive_sentence = "This route is a reasonable option based on the available route data."

    weakest_name, weakest_value = min(factor_values.items(), key=lambda kv: kv[1] if kv[0] != "duration_match" else 1.0)
    caveat_sentence = ""
    if weakest_name in caveats and weakest_value < 0.4:
        caveat_sentence = " One tradeoff: it " + caveats[weakest_name] + "."

    return positive_sentence + caveat_sentence


def score_route(
    route: RawRoute, activity_type: ActivityTypeEnum, target_duration_min: float
) -> RouteScoreResult:
    weights = _weights_for(activity_type)
    factor_values = _factor_values(route, activity_type, target_duration_min)

    weighted_score = sum(factor_values[name] * weight for name, weight in weights.items())
    score = round(weighted_score * 100, 1)

    explanation = _generate_explanation(activity_type, factor_values, weights)

    return RouteScoreResult(score=score, breakdown=factor_values, explanation=explanation)


def score_and_rank_routes(
    routes: list[RawRoute], activity_type: ActivityTypeEnum, target_duration_min: float, top_n: int = 3
) -> list[tuple[RawRoute, RouteScoreResult]]:
    scored = [(route, score_route(route, activity_type, target_duration_min)) for route in routes]
    scored.sort(key=lambda pair: pair[1].score, reverse=True)
    return scored[:top_n]
