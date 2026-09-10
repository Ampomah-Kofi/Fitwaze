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
