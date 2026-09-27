"""Tests for ORSRouteProvider against a simulated OpenRouteService.

These do not touch the network: an `httpx.MockTransport` stands in for the
real service, so the request shape we send and the parsing of the documented
response shape are both exercised without an API key. They are *not* a
substitute for a live smoke test with a real key — a schema change at ORS
would not show up here — but they do catch the errors that would otherwise
only surface during a demo (wrong coordinate order, wrong profile, mishandled
error responses).
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.engines.route_engine.ors_provider import ORSProviderError, ORSRouteProvider
from app.models.enums import ActivityTypeEnum

# One ORS GeoJSON feature, shaped per their Directions API docs. ORS returns
# coordinates as [longitude, latitude], the reverse of what FitWaze uses.
_ORS_FEATURE = {
    "geometry": {
        "coordinates": [[-74.0060, 40.7128], [-74.0050, 40.7135], [-74.0060, 40.7128]],
        "type": "LineString",
    },
    "properties": {
        "segments": [{"distance": 2512.3, "duration": 1800.0}],
    },
}


def _ors_response(status_code: int = 200, payload: dict | None = None):
    body = payload if payload is not None else {"features": [_ORS_FEATURE], "type": "FeatureCollection"}
    return httpx.Response(status_code, json=body)


def test_ors_provider_parses_a_documented_response():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _ors_response()

    provider = ORSRouteProvider(api_key="test-key", transport=httpx.MockTransport(handler))
    routes = provider.get_candidate_routes(
        start_lat=40.7128,
        start_lon=-74.0060,
        activity_type=ActivityTypeEnum.walk,
        target_duration_min=30,
    )

    assert [r.label for r in routes] == ["out_and_back", "small_loop", "large_loop"]
    assert routes[0].distance_m == 2512.3
    assert routes[0].estimated_minutes == 30.0  # 1800 s -> minutes
    # ORS gives [lon, lat]; RawRoute geometry must be (lat, lon).
    assert routes[0].geometry[0] == (40.7128, -74.0060)

    # One request per candidate shape — the number the free-tier quota maths
    # in the README is based on.
    assert len(requests) == 3


def test_ors_provider_sends_the_expected_request():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _ors_response()

    provider = ORSRouteProvider(api_key="test-key", transport=httpx.MockTransport(handler))
    provider.get_candidate_routes(
        start_lat=40.7128,
        start_lon=-74.0060,
        activity_type=ActivityTypeEnum.cycle,
        target_duration_min=40,
    )

    request = captured[0]
    assert request.url.path.endswith("/cycling-regular/geojson")
    assert request.headers["Authorization"] == "test-key"

    body = json.loads(request.content)
    assert body["coordinates"] == [[-74.0060, 40.7128]]  # [lon, lat] for ORS
    round_trip = body["options"]["round_trip"]
    # 40 min at ~15 km/h -> ~10 km for the full-length candidate.
    assert round_trip["length"] == pytest.approx(10000.0, rel=1e-6)
    assert round_trip["seed"] == 1


def test_ors_provider_skips_failed_candidates_but_keeps_the_rest():
    responses = iter([_ors_response(429, {"error": "quota exceeded"}), _ors_response(), _ors_response()])

    provider = ORSRouteProvider(
        api_key="test-key",
        transport=httpx.MockTransport(lambda request: next(responses)),
    )
    routes = provider.get_candidate_routes(
        start_lat=40.7128,
        start_lon=-74.0060,
        activity_type=ActivityTypeEnum.walk,
        target_duration_min=30,
    )

    assert [r.label for r in routes] == ["small_loop", "large_loop"]


def test_ors_provider_raises_when_every_candidate_fails():
    provider = ORSRouteProvider(
        api_key="test-key",
        transport=httpx.MockTransport(lambda request: _ors_response(500, {"error": "boom"})),
    )

    with pytest.raises(ORSProviderError):
        provider.get_candidate_routes(
            start_lat=40.7128,
            start_lon=-74.0060,
            activity_type=ActivityTypeEnum.walk,
            target_duration_min=30,
        )


def test_ors_provider_raises_on_an_unexpected_response_shape():
    provider = ORSRouteProvider(
        api_key="test-key",
        transport=httpx.MockTransport(lambda request: _ors_response(200, {"features": []})),
    )

    with pytest.raises(ORSProviderError):
        provider.get_candidate_routes(
            start_lat=40.7128,
            start_lon=-74.0060,
            activity_type=ActivityTypeEnum.walk,
            target_duration_min=30,
        )


def test_ors_provider_refuses_to_start_without_an_api_key():
    with pytest.raises(ORSProviderError):
        ORSRouteProvider(api_key="")


@pytest.mark.parametrize("broken", [
    {"features": [{}]},
    {"features": [{"geometry": {"type": "LineString", "coordinates": []}, "properties": {"segments": []}}]},
    {"features": None},
    None,
])
def test_malformed_ors_candidate_does_not_discard_other_candidates(broken):
    responses = iter([httpx.Response(200, json=broken), _ors_response(), _ors_response()])
    provider = ORSRouteProvider(api_key="test", transport=httpx.MockTransport(lambda request: next(responses)))
    routes = provider.get_candidate_routes(40.7128, -74.006, ActivityTypeEnum.walk, 30)
    assert len(routes) == 2


def test_ors_accepts_elevation_coordinate_and_rejects_non_finite_distance():
    from copy import deepcopy
    elevated = deepcopy(_ORS_FEATURE)
    for point in elevated["geometry"]["coordinates"]:
        point.append(10)
    malformed = deepcopy(_ORS_FEATURE)
    malformed["properties"]["segments"][0]["distance"] = "nan"
    responses = iter([_ors_response(payload={"features": [malformed]}),
                      _ors_response(payload={"features": [elevated]}), _ors_response()])
    provider = ORSRouteProvider(api_key="test", transport=httpx.MockTransport(lambda request: next(responses)))
    routes = provider.get_candidate_routes(40.7128, -74.006, ActivityTypeEnum.walk, 30)
    assert len(routes) == 2
    assert routes[0].geometry[0] == (40.7128, -74.006)


def _elevated_feature(elevations: list[float]) -> dict:
    """A round trip heading north ~111 m per point, then back, with the given
    elevation at each point."""
    count = len(elevations)
    half = count // 2
    lats = [40.0 + 0.001 * i for i in range(half + 1)] + [40.0 + 0.001 * i for i in range(half - 1, -1, -1)]
    coordinates = [[-74.0, lat, ele] for lat, ele in zip(lats, elevations)]
    return {
        "geometry": {"type": "LineString", "coordinates": coordinates},
        "properties": {"segments": [{"distance": 1100.0, "duration": 780.0}]},
    }


def test_ors_requests_elevation():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _ors_response()

    ORSRouteProvider(api_key="k", transport=httpx.MockTransport(handler)).get_candidate_routes(
        40.7128, -74.006, ActivityTypeEnum.walk, 30
    )
    assert json.loads(captured[0].content)["elevation"] is True


def test_ors_measures_slope_from_elevation():
    flat = _elevated_feature([10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10])
    steep = _elevated_feature([10, 25, 40, 55, 70, 85, 70, 55, 40, 25, 10])  # ~13.5% grade
    no_elevation = _elevated_feature([0] * 11)
    no_elevation["geometry"]["coordinates"] = [p[:2] for p in no_elevation["geometry"]["coordinates"]]
    responses = iter([
        _ors_response(payload={"features": [flat]}),
        _ors_response(payload={"features": [steep]}),
        _ors_response(payload={"features": [no_elevation]}),  # slope stays unverified
    ])
    provider = ORSRouteProvider(api_key="k", transport=httpx.MockTransport(lambda request: next(responses)))
    flat_route, steep_route, unknown_route = provider.get_candidate_routes(40.0, -74.0, ActivityTypeEnum.walk, 13)

    assert flat_route.slope == 0.0
    assert "slope" not in flat_route.unknown_attributes
    assert flat_route.raw_attributes["ascent_m"] == 0.0

    assert steep_route.slope == 1.0
    assert steep_route.raw_attributes["ascent_m"] == pytest.approx(75.0)

    assert "slope" in unknown_route.unknown_attributes
    assert "ascent_m" not in unknown_route.raw_attributes


def test_measured_hills_are_withheld_from_someone_who_cannot_manage_them():
    from app.engines.route_engine.scoring import select_routes
    from app.models.enums import MobilityLimitationEnum
    from tests.test_activity_engine import make_profile

    steep = _elevated_feature([10, 25, 40, 55, 70, 85, 70, 55, 40, 25, 10])
    provider = ORSRouteProvider(
        api_key="k",
        transport=httpx.MockTransport(lambda request: _ors_response(payload={"features": [steep]})),
    )
    routes = provider.get_candidate_routes(40.0, -74.0, ActivityTypeEnum.walk, 13)
    selection = select_routes(
        routes, ActivityTypeEnum.walk, 13, profile=make_profile(mobility_limitations=MobilityLimitationEnum.moderate)
    )
    assert selection.ranked == []
    assert all("steeper" in item.reason for item in selection.excluded)


def test_ors_measures_environment_from_extra_info():
    feature = _elevated_feature([10] * 11)
    feature["properties"]["extras"] = {
        "waytypes": {"summary": [
            {"value": 7.0, "distance": 550.0, "amount": 50.0},   # footway
            {"value": 2.0, "distance": 330.0, "amount": 30.0},   # road
            {"value": 8.0, "distance": 22.0, "amount": 2.0},     # steps
            {"value": 3.0, "distance": 198.0, "amount": 18.0},   # street
        ]},
        "green": {"summary": [
            {"value": 9.0, "distance": 550.0, "amount": 50.0},
            {"value": 2.0, "distance": 550.0, "amount": 50.0},
        ]},
    }
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return _ors_response(payload={"features": [feature]})

    provider = ORSRouteProvider(api_key="k", transport=httpx.MockTransport(handler))
    route = provider.get_candidate_routes(40.0, -74.0, ActivityTypeEnum.walk, 13)[0]

    assert json.loads(captured[0].content)["extra_info"] == ["waytype", "green"]
    assert route.traffic_exposure == pytest.approx(0.30)
    assert route.stairs == pytest.approx(0.20)
    assert route.trail_bonus == pytest.approx(0.55)  # greenness 5.5/10 beats 50% footway
    assert {"traffic_exposure", "stairs", "trail_bonus", "slope"}.isdisjoint(route.unknown_attributes)
    assert "sidewalk_score" in route.unknown_attributes
    assert route.raw_attributes["green_pct"] == 55.0
    assert route.raw_attributes["steps_pct"] == 2.0


def test_ors_cycling_measures_cycleways_and_busy_roads():
    feature = _elevated_feature([10] * 11)
    feature["properties"]["extras"] = {"waytypes": {"summary": [
        {"value": 6.0, "distance": 600.0, "amount": 60.0},   # cycleway
        {"value": 1.0, "distance": 400.0, "amount": 40.0},   # state road
    ]}}
    provider = ORSRouteProvider(
        api_key="k", transport=httpx.MockTransport(lambda request: _ors_response(payload={"features": [feature]}))
    )
    route = provider.get_candidate_routes(40.0, -74.0, ActivityTypeEnum.cycle, 4)[0]
    assert route.bike_lane_score == pytest.approx(0.60)
    assert route.traffic_stress == pytest.approx(0.40)
    assert "bike_lane_score" not in route.unknown_attributes


def test_ors_without_extras_keeps_everything_unverified():
    provider = ORSRouteProvider(api_key="k", transport=httpx.MockTransport(lambda request: _ors_response()))
    route = provider.get_candidate_routes(40.7128, -74.006, ActivityTypeEnum.walk, 30)[0]
    assert "stairs" in route.unknown_attributes and "trail_bonus" in route.unknown_attributes
