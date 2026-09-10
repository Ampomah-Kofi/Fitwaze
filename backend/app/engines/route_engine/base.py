"""RouteProvider interface.

This is the plug point a Geography-department team (or any future data
source) implements to swap in real GIS/routing data: implement
`get_candidate_routes` returning a list of `RawRoute` objects and register
your provider in `app/engines/route_engine/__init__.py` /
`app/routers/route.py`'s provider-selection logic (see `ROUTE_PROVIDER` env
var, currently "mock" or "ors").
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.models.enums import ActivityTypeEnum


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
        the given activity type, each roughly matching target_duration_min."""
        raise NotImplementedError
