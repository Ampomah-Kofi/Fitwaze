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

ORS does not return sidewalk/bike-lane/safety-style attributes, so those
scoring inputs are left at a neutral placeholder value here. Enriching them
with real GIS layers (sidewalks, bike lanes, crash data, slope, etc.) is
exactly the kind of data the Geography department's integration is expected
to plug in — either by extending this provider or by adding a new
`RouteProvider` implementation.
"""
from __future__ import annotations

import logging

import httpx

from app.config import get_settings
from app.engines.route_engine.base import RawRoute, RouteProvider
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


class ORSProviderError(RuntimeError):
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
                        client, profile, start_lat, start_lon, length_m, seed
                    )
                except (httpx.HTTPError, ORSProviderError) as exc:
                    logger.warning("ors_request_failed label=%s error=%s", label, type(exc).__name__)
                    continue

                geometry = [(lat, lon) for lon, lat in feature["geometry"]["coordinates"]]
                segment = feature["properties"]["segments"][0]
                distance_m = float(segment["distance"])
                estimated_minutes = float(segment["duration"]) / 60.0

                routes.append(
                    RawRoute(
                        geometry=geometry,
                        distance_m=round(distance_m, 1),
                        estimated_minutes=round(estimated_minutes, 1),
                        label=label,
                        sidewalk_score=_NEUTRAL_ATTRIBUTE_VALUE,
                        traffic_exposure=_NEUTRAL_ATTRIBUTE_VALUE,
                        major_crossings=_NEUTRAL_ATTRIBUTE_VALUE,
                        slope=_NEUTRAL_ATTRIBUTE_VALUE,
                        trail_bonus=_NEUTRAL_ATTRIBUTE_VALUE,
                        safety_score=_NEUTRAL_ATTRIBUTE_VALUE,
                        bike_lane_score=_NEUTRAL_ATTRIBUTE_VALUE,
                        traffic_stress=_NEUTRAL_ATTRIBUTE_VALUE,
                        intersection_complexity=_NEUTRAL_ATTRIBUTE_VALUE,
                        continuity_score=_NEUTRAL_ATTRIBUTE_VALUE,
                        raw_attributes={"source": "ors", "profile": profile},
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
    ) -> dict:
        url = f"{ORS_BASE_URL}/{profile}/geojson"
        headers = {
            "Authorization": self.api_key,
            "Content-Type": "application/json",
        }
        body = {
            "coordinates": [[start_lon, start_lat]],
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
            raise ORSProviderError(f"unexpected ORS response shape: {data!r}") from exc
