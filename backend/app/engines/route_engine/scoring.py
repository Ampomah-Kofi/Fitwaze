"""Route scoring: weighted 0-100 score + a plain-language explanation.

Weights are module-level dicts (so they're easy to review/tune) and each
sums to 1.0. `score_route` is a pure function of a `RawRoute` + activity
type + target duration, which makes the scoring math straightforward to
unit test (see `backend/tests/test_route_engine.py`).
"""
from __future__ import annotations

from dataclasses import dataclass

from typing import TYPE_CHECKING

from app.engines.route_engine.base import RawRoute
from app.models.enums import AbilityEnum, ActivityTypeEnum, MobilityLimitationEnum

if TYPE_CHECKING:  # pragma: no cover - import only for type checking
    from app.schemas.profile import HealthProfileData

WALK_WEIGHTS: dict[str, float] = {
    "duration_match": 0.22,
    "sidewalk_score": 0.18,
    "traffic_exposure_inv": 0.14,
    "major_crossings_inv": 0.09,
    "slope_inv": 0.09,
    "step_free": 0.09,
    "trail_bonus": 0.10,
    "safety_score": 0.09,
}

CYCLE_WEIGHTS: dict[str, float] = {
    "duration_match": 0.23,
    "bike_lane_score": 0.23,
    "traffic_stress_inv": 0.18,
    "intersection_complexity_inv": 0.09,
    "slope_inv": 0.09,
    "step_free": 0.08,
    "continuity_score": 0.10,
}

assert abs(sum(WALK_WEIGHTS.values()) - 1.0) < 1e-9
assert abs(sum(CYCLE_WEIGHTS.values()) - 1.0) < 1e-9

# --- Personalisation -------------------------------------------------------
# The same street is not the same route to two different people: a flight of
# steps is a minor annoyance to one walker and a wall to another. These are the
# factors whose weight rises when the person's profile says terrain matters
# more to them, expressed as multipliers applied before renormalising so the
# weights still sum to 1.0.
TERRAIN_FACTORS = ("slope_inv", "step_free", "major_crossings_inv", "intersection_complexity_inv")

# How strongly terrain is emphasised, by how constrained the person is. These
# are product judgements, not clinical thresholds — they are deliberately
# module-level and named so a physiotherapist or supervisor can argue with them.
MOBILITY_EMPHASIS: dict[MobilityLimitationEnum, float] = {
    MobilityLimitationEnum.none: 1.0,
    MobilityLimitationEnum.mild: 1.6,
    MobilityLimitationEnum.moderate: 2.4,
    MobilityLimitationEnum.severe: 3.2,
    # An unstated limitation is treated as a mild one: the cautious reading
    # costs a fit walker very little and protects someone who chose not to say.
    MobilityLimitationEnum.prefer_not_to_say: 1.6,
}

LIMITED_ABILITY_EMPHASIS = 1.8
OLDER_ADULT_AGE = 65
OLDER_ADULT_EMPHASIS = 1.3

# --- Feasibility -----------------------------------------------------------
# Emphasis alone is not enough. Weighting steps more heavily still lets a
# stair-ridden route come top when the alternatives are worse, and "best of a
# bad set" is the wrong answer when the obstacle is one this person cannot
# cross. These are hard limits: a route above them is not offered at all.
MAX_STAIRS: dict[MobilityLimitationEnum, float] = {
    MobilityLimitationEnum.none: 1.0,
    MobilityLimitationEnum.mild: 0.60,
    MobilityLimitationEnum.moderate: 0.30,
    MobilityLimitationEnum.severe: 0.10,
    MobilityLimitationEnum.prefer_not_to_say: 0.60,
}

MAX_SLOPE: dict[MobilityLimitationEnum, float] = {
    MobilityLimitationEnum.none: 1.0,
    MobilityLimitationEnum.mild: 0.80,
    MobilityLimitationEnum.moderate: 0.50,
    MobilityLimitationEnum.severe: 0.30,
    MobilityLimitationEnum.prefer_not_to_say: 0.80,
}

# Someone who reports limited ability for the activity gets at least the
# moderate limits, whatever they said about mobility limitations generally.
LIMITED_ABILITY_FLOOR = MobilityLimitationEnum.moderate

_WALK_FACTOR_DESCRIPTIONS = {
    "duration_match": "closely matches your target session length",
    "sidewalk_score": "has good sidewalk coverage",
    "traffic_exposure_inv": "avoids heavy traffic exposure",
    "major_crossings_inv": "avoids major road crossings",
    "slope_inv": "stays relatively flat",
    "step_free": "avoids steps and stairs",
    "trail_bonus": "uses paths, trails or green space",
    "safety_score": "feels like a safe, well-used route",
}

_CYCLE_FACTOR_DESCRIPTIONS = {
    "step_free": "avoids steps and stairs",
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
    "trail_bonus": "stays mostly on streets rather than paths or green space",
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
            "step_free": 1.0 - route.stairs,
            "trail_bonus": route.trail_bonus,
            "safety_score": route.safety_score,
        }

    return {
        "duration_match": duration_match,
        "bike_lane_score": route.bike_lane_score,
        "traffic_stress_inv": 1.0 - route.traffic_stress,
        "intersection_complexity_inv": 1.0 - route.intersection_complexity,
        "slope_inv": 1.0 - route.slope,
        "step_free": 1.0 - route.stairs,
        "continuity_score": route.continuity_score,
    }


def _weights_for(activity_type: ActivityTypeEnum) -> dict[str, float]:
    return WALK_WEIGHTS if activity_type == ActivityTypeEnum.walk else CYCLE_WEIGHTS


def terrain_emphasis(profile: "HealthProfileData | None", activity_type: ActivityTypeEnum) -> float:
    """How much more this person's terrain factors should count, as a multiplier.

    Driven by the mobility limitation they reported, the ability relevant to the
    activity they are about to do, and age. The strongest applicable signal
    wins rather than the factors multiplying together, so someone who is both
    older and mildly limited is not scored as if they were severely limited.
    """
    if profile is None:
        return 1.0

    ability = (
        profile.walking_ability
        if activity_type == ActivityTypeEnum.walk
        else profile.cycling_ability
    )

    candidates = [MOBILITY_EMPHASIS.get(profile.mobility_limitations, 1.0)]
    if ability == AbilityEnum.limited:
        candidates.append(LIMITED_ABILITY_EMPHASIS)
    if profile.age >= OLDER_ADULT_AGE:
        candidates.append(OLDER_ADULT_EMPHASIS)

    return max(candidates)


def personalised_weights(
    activity_type: ActivityTypeEnum, profile: "HealthProfileData | None" = None
) -> dict[str, float]:
    """Base weights with terrain factors emphasised for this person.

    Renormalised to sum to 1.0, so raising terrain weight lowers everything
    else proportionally rather than inflating the score: two people comparing
    the same route see numbers on the same 0-100 scale.
    """
    base = _weights_for(activity_type)
    emphasis = terrain_emphasis(profile, activity_type)
    if emphasis == 1.0:
        return dict(base)

    adjusted = {
        name: weight * (emphasis if name in TERRAIN_FACTORS else 1.0)
        for name, weight in base.items()
    }
    total = sum(adjusted.values())
    return {name: weight / total for name, weight in adjusted.items()}


def _generate_explanation(
    activity_type: ActivityTypeEnum, factor_values: dict[str, float], weights: dict[str, float],
    unknown_factors: frozenset[str] = frozenset(),
) -> str:
    descriptions = _WALK_FACTOR_DESCRIPTIONS if activity_type == ActivityTypeEnum.walk else _CYCLE_FACTOR_DESCRIPTIONS
    caveats = _WALK_FACTOR_CAVEATS if activity_type == ActivityTypeEnum.walk else _CYCLE_FACTOR_CAVEATS

    # Rank factors (excluding duration_match, which is about the request
    # rather than the route's built environment) by their weighted
    # contribution to highlight what actually drove the score.
    # A factor with no weight or no wording is a bug in this module, but it must
    # not become a 500 for someone asking for a walk: an unweighted factor
    # simply contributes nothing, and one with no wording goes unmentioned.
    # `test_route_engine.py` asserts the dicts agree, so the bug is caught there
    # rather than in production.
    contributions = {
        name: value * weights.get(name, 0.0)
        for name, value in factor_values.items()
        if name != "duration_match" and name not in unknown_factors
    }
    ranked = sorted(contributions.items(), key=lambda kv: kv[1], reverse=True)

    top_positives = [
        name for name, _ in ranked[:2]
        if factor_values[name] >= 0.5 and name in descriptions
    ]
    sentence_parts = [descriptions[name] for name in top_positives]

    if sentence_parts:
        positive_sentence = "This route scores well because it " + " and ".join(sentence_parts) + "."
    else:
        positive_sentence = "This route is a reasonable option based on the available route data."

    known = {name: value for name, value in factor_values.items()
             if name != "duration_match" and name not in unknown_factors}
    weakest_name, weakest_value = min(known.items(), key=lambda kv: kv[1], default=("", 1.0))
    caveat_sentence = ""
    if weakest_name in caveats and weakest_value < 0.4:
        caveat_sentence = " One tradeoff: it " + caveats[weakest_name] + "."

    return positive_sentence + caveat_sentence


def score_route(
    route: RawRoute,
    activity_type: ActivityTypeEnum,
    target_duration_min: float,
    profile: "HealthProfileData | None" = None,
) -> RouteScoreResult:
    """Score one route 0-100 for one person.

    Passing `profile` is what makes this PERSON + PLACE rather than just PLACE:
    the same street scores differently for a walker who cannot manage steps.
    """
    weights = personalised_weights(activity_type, profile)
    factor_values = _factor_values(route, activity_type, target_duration_min)

    weighted_score = sum(factor_values[name] * weight for name, weight in weights.items())
    score = round(weighted_score * 100, 1)

    unknown_factors = frozenset(
        "step_free" if name == "stairs" else
        name + "_inv" if name in {"traffic_exposure", "major_crossings", "slope", "traffic_stress", "intersection_complexity"}
        else name for name in route.unknown_attributes
    )
    explanation = _generate_explanation(activity_type, factor_values, weights, unknown_factors)
    if unknown_factors:
        explanation += " Some route attributes are unverified; the score includes neutral placeholders, not measured accessibility."
    if profile is not None and terrain_emphasis(profile, activity_type) > 1.0:
        if route.unknown_attributes & {"stairs", "slope"}:
            explanation += " Steps or gradient are unverified, so suitability for your mobility needs cannot be confirmed."
        else:
            explanation += _terrain_note(factor_values)

    return RouteScoreResult(score=score, breakdown=factor_values, explanation=explanation)


def _terrain_note(factor_values: dict[str, float]) -> str:
    """One sentence saying how this route treats the terrain the person told us
    matters to them — stated plainly, and never repeating a stored health value
    back at them."""
    steps_clear = factor_values.get("step_free", 1.0) == 1.0
    gentle = factor_values.get("slope_inv", 1.0) >= 0.7

    if steps_clear and gentle:
        return " Ranked with your mobility needs in mind: it is step-free and stays gentle underfoot."
    if steps_clear:
        return " Ranked with your mobility needs in mind: step-free, though it does have some gradient."
    if gentle:
        return " Ranked with your mobility needs in mind: gentle underfoot, but it does include steps."
    return " Heads up: this one has both steps and noticeable gradient, which may not suit you."


def score_and_rank_routes(
    routes: list[RawRoute],
    activity_type: ActivityTypeEnum,
    target_duration_min: float,
    top_n: int = 3,
    profile: "HealthProfileData | None" = None,
) -> list[tuple[RawRoute, RouteScoreResult]]:
    scored = [
        (route, score_route(route, activity_type, target_duration_min, profile))
        for route in routes
    ]
    scored.sort(key=lambda pair: pair[1].score, reverse=True)
    return scored[:top_n]


@dataclass
class ExcludedRoute:
    label: str
    reason: str


@dataclass
class RouteSelection:
    """What the engine is willing to offer this person, and what it held back."""

    ranked: list[tuple[RawRoute, RouteScoreResult]]
    excluded: list[ExcludedRoute]


def _limits_for(profile: "HealthProfileData", activity_type: ActivityTypeEnum) -> tuple[float, float]:
    limitation = profile.mobility_limitations
    ability = (
        profile.walking_ability
        if activity_type == ActivityTypeEnum.walk
        else profile.cycling_ability
    )

    max_stairs = MAX_STAIRS.get(limitation, 1.0)
    max_slope = MAX_SLOPE.get(limitation, 1.0)
    if ability == AbilityEnum.limited:
        max_stairs = min(max_stairs, MAX_STAIRS[LIMITED_ABILITY_FLOOR])
        max_slope = min(max_slope, MAX_SLOPE[LIMITED_ABILITY_FLOOR])
    return max_stairs, max_slope


def feasibility_problem(
    route: RawRoute, activity_type: ActivityTypeEnum, profile: "HealthProfileData | None"
) -> str | None:
    """Why this route should not be offered to this person, or None if it is fine.

    Attributes the provider could not measure are never grounds for exclusion:
    withholding a route because of a placeholder value would quietly hide most
    of the map whenever the data is thin.
    """
    if profile is None:
        return None

    ability = profile.walking_ability if activity_type == ActivityTypeEnum.walk else profile.cycling_ability
    if ability == AbilityEnum.unable:
        return "requires an activity you reported being unable to do; update your profile and request a new recommendation"

    max_stairs, max_slope = _limits_for(profile, activity_type)

    if "stairs" not in route.unknown_attributes and route.stairs > max_stairs:
        return "includes steps beyond what you told us you can manage"
    if "slope" not in route.unknown_attributes and route.slope > max_slope:
        return "is steeper than suits you"
    return None


def select_routes(
    routes: list[RawRoute],
    activity_type: ActivityTypeEnum,
    target_duration_min: float,
    profile: "HealthProfileData | None" = None,
    top_n: int = 3,
) -> RouteSelection:
    """Score, rank, and withhold anything this person should not be sent along.

    Returning the reasons rather than silently dropping routes matters: a user
    who can see three loops on the map and is offered two deserves to know why,
    and a researcher reading the output needs to see what was filtered.
    """
    offerable: list[RawRoute] = []
    excluded: list[ExcludedRoute] = []

    for route in routes:
        problem = feasibility_problem(route, activity_type, profile)
        if problem is None:
            offerable.append(route)
        else:
            excluded.append(ExcludedRoute(label=route.label, reason=problem))

    ranked = score_and_rank_routes(
        offerable, activity_type, target_duration_min, top_n, profile
    )
    return RouteSelection(ranked=ranked, excluded=excluded)
