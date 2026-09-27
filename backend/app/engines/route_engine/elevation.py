"""Topography: how hilly a route is, from elevation along its line.

`measure_topography` turns a line of [lon, lat, elevation] points into a
normalised slope (steepest sustained gradient) and total climb. Providers
that return elevation themselves (OpenRouteService) call it directly; for
those that do not (OSRM), `ElevationClient` looks elevations up from an
OpenTopoData-compatible service. The default dataset, `ned10m`, is the USGS
National Elevation Dataset at 10 m resolution, covering all of Alabama.
"""
from __future__ import annotations

import logging
import math

import httpx

from app.engines.route_engine.base import _metres_between

logger = logging.getLogger(__name__)

# Gradient is judged over stretches at least this long, so a kerb ramp or a
# noisy elevation sample does not make a flat park path look like a hill.
_GRADE_WINDOW_M = 50.0
# The sustained gradient that counts as fully steep (slope = 1.0). 12% is a
# hill most people notice; wheelchair ramps are capped at about 8%.
_FULL_SLOPE_GRADE = 0.12


def measure_topography(coordinates: list) -> tuple[float, float] | None:
    """(normalised slope 0-1, total ascent in metres) for an ORS [lon, lat, ele]
    line, or None when elevation is missing for any point."""
    if len(coordinates) < 2 or any(len(point) < 3 for point in coordinates):
        return None
    try:
        elevations = [float(point[2]) for point in coordinates]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(e) for e in elevations):
        return None

    cumulative = [0.0]
    for a, b in zip(coordinates, coordinates[1:]):
        cumulative.append(cumulative[-1] + _metres_between((a[1], a[0]), (b[1], b[0])))
    if cumulative[-1] <= 0:
        return None

    ascent = sum(max(0.0, b - a) for a, b in zip(elevations, elevations[1:]))

    window = min(_GRADE_WINDOW_M, cumulative[-1])
    steepest = 0.0
    j = 0
    for i in range(len(coordinates)):
        j = max(j, i + 1)
        while j < len(coordinates) and cumulative[j] - cumulative[i] < window:
            j += 1
        if j >= len(coordinates):
            break
        run = cumulative[j] - cumulative[i]
        steepest = max(steepest, abs(elevations[j] - elevations[i]) / run)

    return min(1.0, steepest / _FULL_SLOPE_GRADE), ascent



# OpenTopoData accepts at most 100 locations per request, and the public
# instance allows one request per second, so all candidates share one call.
MAX_LOCATIONS_PER_REQUEST = 100


def resample(geometry: list[tuple[float, float]], count: int) -> list[tuple[float, float]]:
    """`count` points spaced evenly by distance along a (lat, lon) line."""
    if len(geometry) < 2 or count < 2:
        return list(geometry[:count])
    cumulative = [0.0]
    for a, b in zip(geometry, geometry[1:]):
        cumulative.append(cumulative[-1] + _metres_between(a, b))
    total = cumulative[-1]
    if total <= 0:
        return [geometry[0]] * count
    points, j = [], 0
    for i in range(count):
        target = total * i / (count - 1)
        while j < len(geometry) - 2 and cumulative[j + 1] < target:
            j += 1
        span = cumulative[j + 1] - cumulative[j] or 1.0
        f = min(1.0, max(0.0, (target - cumulative[j]) / span))
        a, b = geometry[j], geometry[j + 1]
        points.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
    return points


class ElevationClient:
    """Looks up ground elevation for points from an OpenTopoData-style API."""

    def __init__(self, url: str, client: httpx.Client) -> None:
        self.url = url.rstrip("/")
        self.client = client

    def topography_for(self, lines: list[list[tuple[float, float]]]) -> list[tuple[float, float] | None]:
        """(slope 0-1, ascent m) for each (lat, lon) line, or None per line
        whose elevation could not be measured. One request for all lines."""
        if not lines:
            return []
        per_line = max(2, MAX_LOCATIONS_PER_REQUEST // len(lines))
        samples = [resample(line, per_line) for line in lines]
        flat = [point for line in samples for point in line]
        try:
            response = self.client.get(
                self.url, params={"locations": "|".join(f"{lat:.6f},{lon:.6f}" for lat, lon in flat)}
            )
            response.raise_for_status()
            data = response.json()
            if data.get("status") != "OK":
                raise ValueError(f"status {data.get('status')!r}")
            elevations = [result.get("elevation") for result in data["results"]]
            if len(elevations) != len(flat):
                raise ValueError("result count mismatch")
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            logger.warning("elevation_lookup_failed error=%s", type(exc).__name__)
            return [None] * len(lines)

        results: list[tuple[float, float] | None] = []
        index = 0
        for line in samples:
            chunk = elevations[index:index + len(line)]
            index += len(line)
            if any(value is None for value in chunk):  # outside the dataset's coverage
                results.append(None)
                continue
            results.append(measure_topography([[lon, lat, ele] for (lat, lon), ele in zip(line, chunk)]))
        return results
