"""OSRMRouteProvider: real street-following routes with no API key.

Uses an OSRM server's Route service over OpenStreetMap data, one server per
profile (foot, bike). Out of the box it uses the FOSSGIS community servers
(https://routing.openstreetmap.de), which need no key but are for light use.
For the Alabama deployment, run OSRM on the free Alabama extract instead
(docker-compose `alabama` profile, prepared by scripts/prepare-alabama-map.sh)
and set OSRM_FOOT_URL / OSRM_BIKE_URL to it: no limits, no key, and routes
that follow exactly the streets and paths in that map.

OSRM has no round-trip option, so loops are built here: a few waypoints are
placed on a circle that passes through the start, and OSRM joins them along
real streets and paths, start -> waypoints -> start. An out-and-back goes to
one turning point and returns. Every returned line follows the street
network, never a straight line through buildings. If the result comes back
much longer or shorter than wanted (streets are rarely straight), the circle
is resized once and the request repeated.

OSRM reports geometry, distance and duration only, so the environment
attributes (steps, gradient, traffic, greenery) stay neutral placeholders and
are declared unmeasured — the OpenRouteService provider measures those when a
key is configured.

IMPORTANT: like the ORS provider, this has only run against a simulated
server in development (no outbound network there). Smoke-test it once on a
machine with internet access before a live demo.
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
    _metres_between,
    returns_to_start,
)
from app.models.enums import ActivityTypeEnum

logger = logging.getLogger(__name__)

_WALK_SPEED_M_PER_MIN = 5000.0 / 60.0
_CYCLE_SPEED_M_PER_MIN = 15000.0 / 60.0

# Streets wind: a loop along them is longer than the circle its waypoints sit
# on. This first guess is corrected by one resize if it is off.
_DETOUR_FACTOR = 1.3
# Accept a candidate within this fraction of the wanted length.
_LENGTH_TOLERANCE = 0.25
# OSRM snaps the start to the nearest routable way. A home inside a compound
# may be some way from it; beyond this the route is not really "from home".
_MAX_SNAP_M = 250.0

_NEUTRAL_ATTRIBUTE_VALUE = 0.5
_UNMEASURED = frozenset({
    "sidewalk_score", "traffic_exposure", "major_crossings", "slope", "stairs",
    "trail_bonus", "safety_score", "bike_lane_score", "traffic_stress",
    "intersection_complexity", "continuity_score",
})

_EARTH_RADIUS_M = 6371000.0


class OSRMProviderError(RouteProviderError):
    """Raised when the OSRM server fails or returns an unusable route."""


def _offset(lat: float, lon: float, bearing_deg: float, distance_m: float) -> tuple[float, float]:
    """The point `distance_m` from (lat, lon) along `bearing_deg`."""
    lat1, lon1, bearing = math.radians(lat), math.radians(lon), math.radians(bearing_deg)
    angular = distance_m / _EARTH_RADIUS_M
    lat2 = math.asin(math.sin(lat1) * math.cos(angular) + math.cos(lat1) * math.sin(angular) * math.cos(bearing))
    lon2 = lon1 + math.atan2(
        math.sin(bearing) * math.sin(angular) * math.cos(lat1),
        math.cos(angular) - math.sin(lat1) * math.sin(lat2),
    )
    return math.degrees(lat2), (math.degrees(lon2) + 540) % 360 - 180


def loop_waypoints(lat: float, lon: float, length_m: float, bearing_deg: float, points: int = 3) -> list[tuple[float, float]]:
    """Waypoints on a circle through the start whose perimeter is roughly the
    street length wanted, heading off along `bearing_deg`."""
    radius = length_m / _DETOUR_FACTOR / (2 * math.pi)
    centre = _offset(lat, lon, bearing_deg, radius)
    back = (bearing_deg + 180) % 360  # direction from the centre to the start
    step = 360 / (points + 1)
    return [_offset(centre[0], centre[1], back + step * i, radius) for i in range(1, points + 1)]


def out_and_back_waypoints(lat: float, lon: float, length_m: float, bearing_deg: float) -> list[tuple[float, float]]:
    return [_offset(lat, lon, bearing_deg, length_m / 2 / _DETOUR_FACTOR)]


class OSRMRouteProvider(RouteProvider):
    def __init__(
        self,
        foot_url: str | None = None,
        bike_url: str | None = None,
        timeout_seconds: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        settings = get_settings()
        self.urls = {
            ActivityTypeEnum.walk: (foot_url or settings.osrm_foot_url).rstrip("/"),
            ActivityTypeEnum.cycle: (bike_url or settings.osrm_bike_url).rstrip("/"),
        }
        self.timeout_seconds = timeout_seconds
        # Injectable so tests can stand in for the server without a network.
        self.transport = transport

    def get_candidate_routes(
        self,
        start_lat: float,
        start_lon: float,
        activity_type: ActivityTypeEnum,
        target_duration_min: int,
    ) -> list[RawRoute]:
        speed = _WALK_SPEED_M_PER_MIN if activity_type == ActivityTypeEnum.walk else _CYCLE_SPEED_M_PER_MIN
        target_m = speed * target_duration_min

        # Headings are spread so the three candidates explore different parts
        # of the neighbourhood rather than overlapping.
        shape_specs = [
            ("out_and_back", 1.00, 30.0),
            ("small_loop", 0.85, 150.0),
            ("large_loop", 1.15, 270.0),
        ]

        routes: list[RawRoute] = []
        with httpx.Client(timeout=self.timeout_seconds, transport=self.transport) as client:
            for label, fraction, bearing in shape_specs:
                wanted = max(300.0, target_m * fraction)
                try:
                    route = self._candidate(client, activity_type, start_lat, start_lon, label, wanted, bearing)
                except (httpx.HTTPError, OSRMProviderError, KeyError, IndexError, TypeError, ValueError) as exc:
                    logger.warning("osrm_request_failed label=%s error=%s", label, type(exc).__name__)
                    continue
                routes.append(route)

        if not routes:
            raise OSRMProviderError("The routing server returned no usable routes")
        return routes

    def _candidate(self, client, activity_type, lat, lon, label, wanted_m, bearing) -> RawRoute:
        length = wanted_m
        best = None
        for _attempt in range(2):
            waypoints = (
                out_and_back_waypoints(lat, lon, length, bearing)
                if label == "out_and_back"
                else loop_waypoints(lat, lon, length, bearing)
            )
            geometry, distance_m, duration_s = self._route(client, activity_type, lat, lon, waypoints)
            best = (geometry, distance_m, duration_s)
            if abs(distance_m - wanted_m) <= wanted_m * _LENGTH_TOLERANCE:
                break
            # Resize the circle by how far off the streets took us, once.
            length = length * wanted_m / distance_m

        geometry, distance_m, duration_s = best
        route = RawRoute(
            geometry=geometry,
            distance_m=round(distance_m, 1),
            estimated_minutes=round(duration_s / 60.0, 1),
            label=label,
            **dict.fromkeys(_UNMEASURED, _NEUTRAL_ATTRIBUTE_VALUE),
            unknown_attributes=_UNMEASURED,
            raw_attributes={"source": "osrm"},
        )
        if not returns_to_start(route, lat, lon):
            raise OSRMProviderError("Route does not return to the start")
        return route

    def _route(self, client, activity_type, lat, lon, waypoints):
        stops = [(lat, lon), *waypoints, (lat, lon)]
        coordinates = ";".join(f"{p_lon:.6f},{p_lat:.6f}" for p_lat, p_lon in stops)  # OSRM wants lon,lat
        url = f"{self.urls[activity_type]}/{coordinates}"
        response = client.get(url, params={"overview": "full", "geometries": "geojson", "steps": "false"})
        response.raise_for_status()
        data = response.json()
        if data.get("code") != "Ok" or not data.get("routes"):
            raise OSRMProviderError(f"OSRM answered {data.get('code')!r}")

        route = data["routes"][0]
        coords = route["geometry"]["coordinates"]
        if route["geometry"].get("type") != "LineString" or len(coords) < 2:
            raise OSRMProviderError("Expected a LineString")
        geometry = [(float(point[1]), float(point[0])) for point in coords]
        distance_m = float(route["distance"])
        duration_s = float(route["duration"])
        if not (math.isfinite(distance_m) and distance_m > 0 and math.isfinite(duration_s) and duration_s > 0):
            raise OSRMProviderError("Invalid distance or duration")

        # The line starts and ends where OSRM snapped the start onto a street.
        # Join it to the actual start (a front door, a compound gate) with the
        # short final stretch, as long as that stretch really is short.
        start = (lat, lon)
        snap = max(_metres_between(start, geometry[0]), _metres_between(start, geometry[-1]))
        if snap > _MAX_SNAP_M:
            raise OSRMProviderError("Start is too far from any street or path")
        if geometry[0] != start:
            geometry.insert(0, start)
        if geometry[-1] != start:
            geometry.append(start)
        return geometry, distance_m, duration_s
