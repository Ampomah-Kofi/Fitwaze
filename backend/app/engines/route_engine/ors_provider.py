"""ORSRouteProvider: real OpenRouteService-backed implementation.

Only instantiated when `ROUTE_PROVIDER=ors` is set (default is "mock" — see
`app/routers/route.py`). Implemented per ORS's documented request/response
shape for the Directions API's "round trip" option, which is the natural fit
for "give me N candidate loop/out-and-back routes starting near here":
https://openrouteservice.org/dev/#/api-docs/v2/directions/{profile}/geojson/post

IMPORTANT: this has never run against the live ORS API in this environment
(no network access / API key during development). `tests/test_ors_provider.py`
exercises the request shape and response parsing against a simulated service
(`httpx.MockTransport`), which catches coordinate-order and error-handling
mistakes — but it cannot catch a change at ORS's end. A real API key and a
live smoke test are still needed before relying on this in production; see the
README's "Map & route data" section.

Topography is measured: every request asks ORS for elevation, and the
steepest sustained gradient along the returned line becomes the route's
`slope`, which the feasibility gate and scoring then use for real. ORS does
not return sidewalk/bike-lane/safety-style attributes, so those scoring
inputs are left at a neutral placeholder value here. Enriching them
with real GIS layers (sidewalks, bike lanes, crash data, slope, etc.) is
exactly the kind of data the Geography department's integration is expected
to plug in — either by extending this provider or by adding a new
`RouteProvider` implementation.
"""
from __future__ import annotations

import logging
import math

import httpx

from app.config import get_settings
from app.engines.route_engine.base import (
    RawRoute,
    RouteProvider,
    RouteProviderError,
    returns_to_start,
)
from app.engines.route_engine.elevation import measure_topography
from app.models.enums import ActivityTypeEnum

logger = logging.getLogger(__name__)

ORS_BASE_URL = "https://api.openrouteservice.org/v2/directions"

_PROFILE_BY_ACTIVITY = {
    ActivityTypeEnum.walk: "foot-walking",
    ActivityTypeEnum.cycle: "cycling-regular",
}

_WALK_SPEED_M_PER_MIN = 5000.0 / 60.0
_CYCLE_SPEED_M_PER_MIN = 15000.0 / 60.0

# Neutral placeholder for attributes ORS doesn't provide out of the box.
_NEUTRAL_ATTRIBUTE_VALUE = 0.5

# Every attribute below is a placeholder, not a measurement. Declaring them as
# unknown keeps the feasibility gate from excluding real routes on the strength
# of an invented number — the routes ORS returns are walkable/cyclable by
# construction (that is what the foot-walking and cycling-regular profiles
# mean), we simply cannot yet say how many steps or how steep they are.
_UNMEASURED = frozenset({
    "sidewalk_score", "traffic_exposure", "major_crossings", "slope", "stairs",
    "trail_bonus", "safety_score", "bike_lane_score", "traffic_stress",
    "intersection_complexity", "continuity_score",
})


# ORS "waytype" codes (extra_info=waytype), per the ORS documentation.
_WAYTYPE_STATE_ROAD = 1
_WAYTYPE_ROAD = 2
_WAYTYPE_PATH = 4
_WAYTYPE_TRACK = 5
_WAYTYPE_CYCLEWAY = 6
_WAYTYPE_FOOTWAY = 7
_WAYTYPE_STEPS = 8
_BUSY_WAYTYPES = {_WAYTYPE_STATE_ROAD, _WAYTYPE_ROAD}
_OFFROAD_WAYTYPES = {_WAYTYPE_PATH, _WAYTYPE_TRACK, _WAYTYPE_FOOTWAY, _WAYTYPE_CYCLEWAY}
# Share of a route on steps that counts as fully "stairs" (1.0). Even 1% of a
# kilometre is a flight of ten metres, which the severe-mobility limit
# (0.10) already refuses.
_FULL_STAIRS_SHARE = 0.10

# Environment layers requested from ORS. Greenness (0-10, how much vegetation
# lines the way) is only offered for foot profiles.
_EXTRA_INFO = {
    ActivityTypeEnum.walk: ["waytype", "green"],
    ActivityTypeEnum.cycle: ["waytype"],
}


def _summary(extras: dict, *names: str) -> list[dict]:
    """ORS reports each layer as {"summary": [{"value", "distance", "amount"}]},
    `amount` being the percentage of the route. Accept the singular and plural
    key spellings, since the response keys are not the request names."""
    for name in names:
        layer = extras.get(name)
        if isinstance(layer, dict) and isinstance(layer.get("summary"), list):
            return [
                item for item in layer["summary"]
                if isinstance(item, dict) and isinstance(item.get("value"), (int, float))
                and isinstance(item.get("amount"), (int, float))
            ]
    return []


def measure_environment(properties: dict, activity_type: ActivityTypeEnum) -> tuple[dict, dict]:
    """(scoring attributes, display facts) measured from ORS extra_info.

    Only attributes backed by data in this response are returned, so anything
    missing stays marked unverified rather than silently becoming a guess.
    """
    extras = properties.get("extras") if isinstance(properties, dict) else None
    if not isinstance(extras, dict):
        return {}, {}

    attributes: dict[str, float] = {}
    facts: dict[str, float] = {}

    waytypes = _summary(extras, "waytypes", "waytype")
    total = sum(item["amount"] for item in waytypes)
    if total > 0:
        def share(codes: set[int]) -> float:
            return sum(item["amount"] for item in waytypes if int(item["value"]) in codes) / total

        steps = share({_WAYTYPE_STEPS})
        busy = share(_BUSY_WAYTYPES)
        offroad = share(_OFFROAD_WAYTYPES)
        attributes["stairs"] = min(1.0, steps / _FULL_STAIRS_SHARE)
        facts["steps_pct"] = round(steps * 100, 1)
        facts["busy_road_pct"] = round(busy * 100, 1)
        facts["paths_pct"] = round(offroad * 100, 1)
        if activity_type == ActivityTypeEnum.walk:
            attributes["traffic_exposure"] = busy
            attributes["trail_bonus"] = offroad
        else:
            attributes["traffic_stress"] = busy
            attributes["bike_lane_score"] = min(1.0, share({_WAYTYPE_CYCLEWAY}) + 0.5 * share(
                {_WAYTYPE_PATH, _WAYTYPE_TRACK}
            ))

    green = _summary(extras, "green")
    green_total = sum(item["amount"] for item in green)
    if green_total > 0:
        greenness = sum(item["value"] * item["amount"] for item in green) / green_total / 10.0
        facts["green_pct"] = round(min(1.0, max(0.0, greenness)) * 100, 1)
        # A park path counts toward the walking "trail" factor as much as a
        # marked footway does: both take the walker away from the road.
        attributes["trail_bonus"] = max(attributes.get("trail_bonus", 0.0), min(1.0, greenness))

    return attributes, facts


class ORSProviderError(RouteProviderError):
    """Raised when the ORS API call fails or returns an unexpected shape."""


class ORSRouteProvider(RouteProvider):
    def __init__(
        self,
        api_key: str | None = None,
        timeout_seconds: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        settings = get_settings()
        self.api_key = api_key or settings.ors_api_key
        self.timeout_seconds = timeout_seconds
        # Injectable so the test suite can exercise the request/response
        # handling against a simulated ORS service without a key or network.
        self.transport = transport
        if not self.api_key:
            raise ORSProviderError(
                "ORS_API_KEY is not configured; set it in .env to use ROUTE_PROVIDER=ors"
            )

    def get_candidate_routes(
        self,
        start_lat: float,
        start_lon: float,
        activity_type: ActivityTypeEnum,
        target_duration_min: int,
    ) -> list[RawRoute]:
        profile = _PROFILE_BY_ACTIVITY[activity_type]
        speed = (
            _WALK_SPEED_M_PER_MIN
            if activity_type == ActivityTypeEnum.walk
            else _CYCLE_SPEED_M_PER_MIN
        )
        target_distance_m = speed * target_duration_min

        shape_specs = [
            ("out_and_back", 1.00, 1),
            ("small_loop", 0.85, 2),
            ("large_loop", 1.15, 3),
        ]

        routes: list[RawRoute] = []
        with httpx.Client(timeout=self.timeout_seconds, transport=self.transport) as client:
            for label, distance_fraction, seed in shape_specs:
                length_m = max(200.0, target_distance_m * distance_fraction)
                try:
                    feature = self._request_round_trip(
                        client, profile, start_lat, start_lon, length_m, seed,
                        _EXTRA_INFO[activity_type],
                    )
                    if feature["geometry"]["type"] != "LineString":
                        raise ORSProviderError("Expected a LineString")
                    coordinates = feature["geometry"]["coordinates"]
                    geometry = [(float(point[1]), float(point[0])) for point in coordinates]
                    segment = feature["properties"]["segments"][0]
                    distance_m = float(segment["distance"])
                    estimated_minutes = float(segment["duration"]) / 60.0
                    candidate = RawRoute(geometry, distance_m, estimated_minutes)
                    if not (math.isfinite(distance_m) and distance_m > 0 and
                            math.isfinite(estimated_minutes) and estimated_minutes > 0 and
                            returns_to_start(candidate, start_lat, start_lon)):
                        raise ORSProviderError("Invalid round-trip geometry or metrics")
                except (httpx.HTTPError, ORSProviderError, KeyError, IndexError, TypeError, ValueError) as exc:
                    logger.warning("ors_request_failed label=%s error=%s", label, type(exc).__name__)
                    continue

                attributes = dict.fromkeys(_UNMEASURED, _NEUTRAL_ATTRIBUTE_VALUE)
                raw_attributes: dict = {"source": "ors", "profile": profile}

                topography = measure_topography(coordinates)
                if topography:
                    attributes["slope"] = topography[0]
                    raw_attributes["ascent_m"] = round(topography[1], 1)

                environment, facts = measure_environment(feature.get("properties", {}), activity_type)
                attributes.update(environment)
                raw_attributes.update(facts)

                measured = set(environment) | ({"slope"} if topography else set())

                routes.append(
                    RawRoute(
                        geometry=geometry,
                        distance_m=round(distance_m, 1),
                        estimated_minutes=round(estimated_minutes, 1),
                        label=label,
                        **{name: round(value, 3) for name, value in attributes.items()},
                        unknown_attributes=_UNMEASURED - measured,
                        raw_attributes=raw_attributes,
                    )
                )

        if not routes:
            raise ORSProviderError("OpenRouteService returned no usable routes")

        return routes

    def _request_round_trip(
        self,
        client: httpx.Client,
        profile: str,
        start_lat: float,
        start_lon: float,
        length_m: float,
        seed: int,
        extra_info: list[str],
    ) -> dict:
        url = f"{ORS_BASE_URL}/{profile}/geojson"
        headers = {
            "Authorization": self.api_key,
            "Content-Type": "application/json",
        }
        body = {
            "coordinates": [[start_lon, start_lat]],
            # Returns [lon, lat, elevation] points, which is what lets the
            # engine see hills instead of guessing.
            "elevation": True,
            # Per-segment environment layers: road type (busy roads, paths,
            # steps) and, on foot, how green the surroundings are.
            "extra_info": extra_info,
            "options": {
                "round_trip": {
                    "length": length_m,
                    "points": 3,
                    "seed": seed,
                }
            },
        }

        response = client.post(url, headers=headers, json=body)
        response.raise_for_status()
        data = response.json()

        try:
            return data["features"][0]
        except (KeyError, IndexError) as exc:
            raise ORSProviderError("Unexpected ORS response shape") from exc
