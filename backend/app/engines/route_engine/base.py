"""RouteProvider interface.

This is the plug point a Geography-department team (or any future data
source) implements to swap in real GIS/routing data: implement
`get_candidate_routes` returning a list of `RawRoute` objects and register
your provider in `app/engines/route_engine/__init__.py` /
`app/routers/route.py`'s provider-selection logic (see `ROUTE_PROVIDER` env
var, currently "mock" or "ors").
"""
from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.models.enums import ActivityTypeEnum

# How far a route's ends may sit from the requested start before it stops being
# a round trip. Generous enough for a routing engine snapping to the nearest
# path, tight enough that a walker is not left with a real walk home.
ROUND_TRIP_TOLERANCE_M = 60.0


@dataclass
class RawRoute:
    """A single candidate route with geometry + normalized (0-1) scoring
    attributes. Not every attribute is meaningful for every activity type —
    `scoring.py` only reads the subset relevant to `activity_type`."""

    geometry: list[tuple[float, float]]  # ordered (lat, lon) points
    distance_m: float
    estimated_minutes: float
    label: str = ""  # e.g. "out_and_back", "small_loop", "large_loop"

    # --- walking-relevant attributes (0-1 normalized) ---
    sidewalk_score: float = 0.0
    traffic_exposure: float = 0.0
    major_crossings: float = 0.0
    slope: float = 0.0
    trail_bonus: float = 0.0
    safety_score: float = 0.0
    # Presence of steps/stairs along the route (OSM `highway=steps` and the
    # like). Kept separate from `slope` because they are a different kind of
    # obstacle: a hill is tiring, a flight of stairs can be impassable. 0.0
    # means step-free, which is why it defaults there.
    stairs: float = 0.0

    # --- cycling-relevant attributes (0-1 normalized) ---
    bike_lane_score: float = 0.0
    traffic_stress: float = 0.0
    intersection_complexity: float = 0.0
    continuity_score: float = 0.0

    # Attributes this provider could not actually measure and has filled with a
    # neutral placeholder. Scoring still uses the placeholder value, but the
    # feasibility gate must never exclude a route on the strength of a number
    # nobody measured — "unknown" is not the same as "bad".
    unknown_attributes: frozenset[str] = frozenset()

    raw_attributes: dict = field(default_factory=dict)


class RouteProvider(ABC):
    """Abstract base class every route data source must implement."""

    @abstractmethod
    def get_candidate_routes(
        self,
        start_lat: float,
        start_lon: float,
        activity_type: ActivityTypeEnum,
        target_duration_min: int,
    ) -> list[RawRoute]:
        """Return 2-3 candidate routes starting at (start_lat, start_lon) for
        the given activity type, each roughly matching target_duration_min.

        REQUIRED: every route is a **round trip**. Its geometry must begin and
        end at the given start point, because FitWaze recommends recreational
        activity from wherever the user happens to be — they need to get home
        again, and a one-way route would leave them stranded at the far end with
        a journey nobody has accounted for. `returns_to_start()` below checks
        this, and the API logs a warning for any provider that violates it.
        """
        raise NotImplementedError


def _metres_between(a: tuple[float, float], b: tuple[float, float]) -> float:
    radius_m = 6371000.0
    d_lat = math.radians(b[0] - a[0])
    d_lon = math.radians(b[1] - a[1])
    h = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(a[0])) * math.cos(math.radians(b[0])) * math.sin(d_lon / 2) ** 2
    )
    return 2 * radius_m * math.asin(math.sqrt(h))


def returns_to_start(
    route: RawRoute,
    start_lat: float,
    start_lon: float,
    tolerance_m: float = ROUND_TRIP_TOLERANCE_M,
) -> bool:
    """Whether a route both begins and ends at the requested start point."""
    if not route.geometry:
        return False
    start = (start_lat, start_lon)
    return (
        _metres_between(start, route.geometry[0]) <= tolerance_m
        and _metres_between(start, route.geometry[-1]) <= tolerance_m
    )
