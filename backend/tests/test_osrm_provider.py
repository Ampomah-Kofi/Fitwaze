"""OSRMRouteProvider against a simulated OSRM server.

The simulated server snaps each requested stop onto a small street grid and
joins them with right-angled street segments, the way a real router follows
streets instead of cutting straight across blocks.
"""
from __future__ import annotations

import math

import httpx
import pytest

from app.engines.route_engine.base import _metres_between, returns_to_start
from app.engines.route_engine.osrm_provider import (
    OSRMProviderError,
    OSRMRouteProvider,
    loop_waypoints,
)
from app.models.enums import ActivityTypeEnum

START = (33.5186, -86.8104)  # Birmingham, Alabama
BLOCK = 0.001  # grid spacing in degrees (~100 m)


def _snap(value: float, origin: float) -> float:
    return origin + round((value - origin) / BLOCK) * BLOCK


def _street_server(requests: list[httpx.Request], stretch: float = 1.0, snap_offset: float = 0.0003):
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        stops = [tuple(map(float, pair.split(","))) for pair in request.url.path.rsplit("/", 1)[1].split(";")]
        path: list[list[float]] = []
        for lon, lat in stops:
            # Every stop lands on a street a little way from where it was asked.
            snapped = [_snap(lon, START[1]) + snap_offset, _snap(lat, START[0])]
            if path:
                corner = [snapped[0], path[-1][1]]  # go along one street, then turn
                path.append(corner)
            path.append(snapped)
        distance = sum(
            _metres_between((a[1], a[0]), (b[1], b[0])) for a, b in zip(path, path[1:])
        ) * stretch
        return httpx.Response(200, json={
            "code": "Ok",
            "routes": [{"geometry": {"type": "LineString", "coordinates": path},
                        "distance": distance, "duration": distance / (5000 / 3600)}],
        })
    return handler


def _provider(handler, elevation_url: str = "") -> OSRMRouteProvider:
    return OSRMRouteProvider(
        foot_url="https://osrm.test/route/v1/foot",
        bike_url="https://osrm.test/route/v1/bike",
        elevation_url=elevation_url,
        transport=httpx.MockTransport(handler),
    )


def test_routes_follow_streets_and_return_home():
    requests: list[httpx.Request] = []
    routes = _provider(_street_server(requests)).get_candidate_routes(*START, ActivityTypeEnum.walk, 20)

    assert {r.label for r in routes} == {"out_and_back", "small_loop", "large_loop"}
    for route in routes:
        assert returns_to_start(route, *START)
        assert route.geometry[0] == START and route.geometry[-1] == START
        # Street segments only: after the short walk from the door to the
        # street, every step runs along one axis of the grid, never diagonally
        # across a block.
        for a, b in zip(route.geometry[1:-2], route.geometry[2:-1]):
            assert math.isclose(a[0], b[0], abs_tol=1e-9) or math.isclose(a[1], b[1], abs_tol=1e-9)
        assert route.unknown_attributes  # environment not measured by OSRM
        assert route.raw_attributes["source"] == "osrm"


def test_requests_use_lon_lat_order_and_the_activity_profile():
    requests: list[httpx.Request] = []
    _provider(_street_server(requests)).get_candidate_routes(*START, ActivityTypeEnum.cycle, 15)
    first = requests[0]
    assert first.url.path.startswith("/route/v1/bike/")
    assert first.url.path.rsplit("/", 1)[1].split(";")[0] == f"{START[1]:.6f},{START[0]:.6f}"
    assert first.url.params["geometries"] == "geojson"
    assert first.url.params["overview"] == "full"


def test_a_badly_sized_route_is_resized_once():
    requests: list[httpx.Request] = []
    routes = _provider(_street_server(requests, stretch=2.0)).get_candidate_routes(*START, ActivityTypeEnum.walk, 20)
    assert len(routes) == 3
    assert len(requests) == 6  # one retry per candidate, never more


def test_start_far_from_any_street_is_refused():
    requests: list[httpx.Request] = []
    with pytest.raises(OSRMProviderError):
        _provider(_street_server(requests, snap_offset=0.01)).get_candidate_routes(*START, ActivityTypeEnum.walk, 20)


def test_server_errors_fail_cleanly():
    provider = _provider(lambda request: httpx.Response(200, json={"code": "NoRoute", "routes": []}))
    with pytest.raises(OSRMProviderError):
        provider.get_candidate_routes(*START, ActivityTypeEnum.walk, 20)
    provider = _provider(lambda request: httpx.Response(503))
    with pytest.raises(OSRMProviderError):
        provider.get_candidate_routes(*START, ActivityTypeEnum.walk, 20)


def test_loop_waypoints_sit_on_a_circle_through_the_start():
    points = loop_waypoints(*START, 2600, 90)
    radius = 2600 / 1.3 / (2 * math.pi)
    assert len(points) == 3
    assert all(0 < _metres_between(START, p) <= 2 * radius + 1 for p in points)


def test_osrm_is_the_default_provider(monkeypatch):
    from app.config import get_settings
    from app.engines.route_engine import get_route_provider

    monkeypatch.setattr(get_settings(), "route_provider", "osrm")
    assert isinstance(get_route_provider(), OSRMRouteProvider)


def _with_elevation(street_handler, elevation_of, calls: list):
    """Street server plus an OpenTopoData stand-in on another host."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "elevation.test":
            calls.append(request)
            locations = request.url.params["locations"].split("|")
            return httpx.Response(200, json={"status": "OK", "results": [
                {"elevation": elevation_of(*map(float, loc.split(","))), "location": {}} for loc in locations
            ]})
        return street_handler(request)
    return handler


def test_hills_are_measured_in_one_elevation_request():
    calls: list[httpx.Request] = []
    # Ground rising 30 m per block northwards: steep everywhere it climbs.
    rising = lambda lat, lon: (lat - START[0]) / BLOCK * 30.0
    provider = _provider(_with_elevation(_street_server([]), rising, calls), elevation_url="https://elevation.test/v1/ned10m")
    routes = provider.get_candidate_routes(*START, ActivityTypeEnum.walk, 20)

    assert len(calls) == 1, "one lookup for all candidates (public API allows 1 request/second)"
    assert len(calls[0].url.params["locations"].split("|")) <= 100
    measured = [r for r in routes if "slope" not in r.unknown_attributes]
    assert measured, "gradient measured"
    assert any(r.slope > 0.5 and r.raw_attributes["ascent_m"] > 0 for r in measured)


def test_flat_ground_measures_flat():
    calls: list[httpx.Request] = []
    provider = _provider(_with_elevation(_street_server([]), lambda lat, lon: 150.0, calls),
                         elevation_url="https://elevation.test/v1/ned10m")
    for route in provider.get_candidate_routes(*START, ActivityTypeEnum.walk, 20):
        assert route.slope == 0.0 and "slope" not in route.unknown_attributes


def test_elevation_outage_leaves_gradient_unverified_but_keeps_routes():
    def handler(request):
        if request.url.host == "elevation.test":
            return httpx.Response(429)
        return _street_server([])(request)
    routes = _provider(handler, elevation_url="https://elevation.test/v1/ned10m").get_candidate_routes(
        *START, ActivityTypeEnum.walk, 20)
    assert len(routes) == 3
    assert all("slope" in r.unknown_attributes for r in routes)


def test_points_outside_coverage_stay_unverified():
    calls: list[httpx.Request] = []
    provider = _provider(_with_elevation(_street_server([]), lambda lat, lon: None, calls),
                         elevation_url="https://elevation.test/v1/ned10m")
    assert all("slope" in r.unknown_attributes for r in provider.get_candidate_routes(*START, ActivityTypeEnum.walk, 20))
