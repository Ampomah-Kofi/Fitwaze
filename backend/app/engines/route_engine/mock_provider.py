"""MockRouteProvider: deterministic, offline synthetic route generator.

No network calls. Given a start point, deterministically (seeded by rounding
the start coordinates) produces 3 candidate routes shaped like an
out-and-back, a small loop, and a larger loop, with plausible synthetic
scoring attributes. This is the default provider (`ROUTE_PROVIDER=mock`) and
is what the automated test suite exercises; a Geography-department team can
later swap in a real GIS/routing data source via the `RouteProvider`
interface (see `base.py`).
"""
from __future__ import annotations

import hashlib
import math
import random

from app.engines.route_engine.base import RawRoute, RouteProvider
from app.models.enums import ActivityTypeEnum

_EARTH_RADIUS_M = 6371000.0

# Rough average speeds used to translate a target duration into a target
# distance for synthetic route generation.
_WALK_SPEED_M_PER_MIN = 5000.0 / 60.0  # ~5 km/h
_CYCLE_SPEED_M_PER_MIN = 15000.0 / 60.0  # ~15 km/h


def _seed_from_coords(lat: float, lon: float) -> int:
    """Deterministic seed derived from the start point, rounded to ~11m
    precision (4 decimal places) so nearby-but-not-identical requests still
    produce stable, reproducible synthetic routes."""
    key = f"{round(lat, 4):.4f},{round(lon, 4):.4f}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


def _offset_point(lat: float, lon: float, bearing_deg: float, distance_m: float) -> tuple[float, float]:
    """Move `distance_m` meters from (lat, lon) along `bearing_deg` (0 = north,
    clockwise), using a simple planar/equirectangular approximation — plenty
    accurate for short synthetic recreational routes."""
    bearing_rad = math.radians(bearing_deg)
    delta_lat = (distance_m * math.cos(bearing_rad)) / _EARTH_RADIUS_M
    delta_lon = (distance_m * math.sin(bearing_rad)) / (
        _EARTH_RADIUS_M * math.cos(math.radians(lat)) or 1e-9
    )
    return lat + math.degrees(delta_lat), lon + math.degrees(delta_lon)


def _out_and_back_geometry(lat: float, lon: float, distance_m: float, bearing_deg: float) -> list[tuple[float, float]]:
    half = distance_m / 2
    steps = 5
    points = [(lat, lon)]
    for i in range(1, steps + 1):
        points.append(_offset_point(lat, lon, bearing_deg, half * i / steps))
    # ...and back the same way.
    for i in range(steps - 1, -1, -1):
        points.append(_offset_point(lat, lon, bearing_deg, half * i / steps))
    return points


def _loop_geometry(lat: float, lon: float, distance_m: float, start_bearing_deg: float) -> list[tuple[float, float]]:
    circumference = distance_m
    radius = circumference / (2 * math.pi)
    steps = 12
    points = []
    for i in range(steps + 1):
        angle = start_bearing_deg + (360.0 * i / steps)
        points.append(_offset_point(lat, lon, angle, radius))
    return points


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, value))


class MockRouteProvider(RouteProvider):
    def get_candidate_routes(
        self,
        start_lat: float,
        start_lon: float,
        activity_type: ActivityTypeEnum,
        target_duration_min: int,
    ) -> list[RawRoute]:
        seed = _seed_from_coords(start_lat, start_lon)
        rng = random.Random(seed)

        speed = (
            _WALK_SPEED_M_PER_MIN
            if activity_type == ActivityTypeEnum.walk
            else _CYCLE_SPEED_M_PER_MIN
        )
        target_distance_m = speed * target_duration_min

        # Three shapes, each a slightly different fraction of the target
        # distance so scoring's duration_match sub-score meaningfully
        # differentiates them.
        shape_specs = [
            ("out_and_back", 1.00, rng.uniform(0, 360)),
            ("small_loop", 0.85, rng.uniform(0, 360)),
            ("large_loop", 1.15, rng.uniform(0, 360)),
        ]

        # Baseline attribute presets per shape (illustrative, deterministic
        # given the seed via the jitter below) — a real GIS provider would
        # replace all of this with actual sidewalk/bike-lane/traffic data.
        presets = {
            "out_and_back": dict(
                sidewalk_score=0.75,
                traffic_exposure=0.35,
                major_crossings=0.30,
                slope=0.25,
                stairs=0.05,
                trail_bonus=0.10,
                safety_score=0.75,
                bike_lane_score=0.55,
                traffic_stress=0.40,
                intersection_complexity=0.30,
                continuity_score=0.70,
            ),
            "small_loop": dict(
                sidewalk_score=0.65,
                traffic_exposure=0.45,
                major_crossings=0.45,
                slope=0.20,
                stairs=0.35,
                trail_bonus=0.05,
                safety_score=0.65,
                bike_lane_score=0.45,
                traffic_stress=0.50,
                intersection_complexity=0.50,
                continuity_score=0.55,
            ),
            "large_loop": dict(
                sidewalk_score=0.60,
                traffic_exposure=0.25,
                major_crossings=0.20,
                slope=0.35,
                stairs=0.60,
                trail_bonus=0.40,
                safety_score=0.80,
                bike_lane_score=0.70,
                traffic_stress=0.25,
                intersection_complexity=0.20,
                continuity_score=0.85,
            ),
        }

        routes: list[RawRoute] = []
        for label, distance_fraction, bearing in shape_specs:
            distance_m = target_distance_m * distance_fraction
            estimated_minutes = distance_m / speed

            if label == "out_and_back":
                geometry = _out_and_back_geometry(start_lat, start_lon, distance_m, bearing)
            else:
                geometry = _loop_geometry(start_lat, start_lon, distance_m, bearing)

            attrs = {
                key: _clip01(value + rng.uniform(-0.05, 0.05))
                for key, value in presets[label].items()
            }

            routes.append(
                RawRoute(
                    geometry=geometry,
                    distance_m=round(distance_m, 1),
                    estimated_minutes=round(estimated_minutes, 1),
                    label=label,
                    **attrs,
                )
            )

        return routes
