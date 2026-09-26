"""Walkability and cyclability from OpenStreetMap street details."""
from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx
import pytest

from app.engines.route_engine.osm_streets import assess, classify_cycle, classify_walk, speed_mph
from app.engines.route_engine.osrm_provider import OSRMRouteProvider
from app.engines.route_engine.scoring import select_routes
from app.models.enums import ActivityTypeEnum

WALK, CYCLE = ActivityTypeEnum.walk, ActivityTypeEnum.cycle

RESIDENTIAL = {"highway": "residential"}
SIDEWALKED_MAIN = {"highway": "primary", "maxspeed": "40 mph", "sidewalk": "both", "lit": "yes"}
HIGHWAY_280 = {"highway": "trunk", "maxspeed": "55 mph", "sidewalk": "no"}
RURAL_ROAD = {"highway": "secondary", "maxspeed": "45 mph"}  # typical Alabama county road: no sidewalk tagged
GREENWAY = {"highway": "footway"}
BIKE_LANE_STREET = {"highway": "tertiary", "maxspeed": "35 mph", "cycleway:right": "lane"}


@pytest.mark.parametrize("tags, mph", [({"maxspeed": "35 mph"}, 35), ({"maxspeed": "50"}, 31.07),
                                       ({"highway": "residential"}, 25), ({"highway": "trunk"}, 55)])
def test_speed_limits(tags, mph):
    assert speed_mph(tags) == pytest.approx(mph, abs=0.05)


def test_walking_classification():
    assert classify_walk(RESIDENTIAL)["walkable_side"] and not classify_walk(RESIDENTIAL)["busy"]
    main = classify_walk(SIDEWALKED_MAIN)
    assert main["busy"] and main["sidewalk"] and main["hazard"] is None
    assert classify_walk(HIGHWAY_280)["hazard"] == "runs along a fast road with no sidewalk"
    assert classify_walk(RURAL_ROAD)["hazard"] == "runs along a fast road with no sidewalk"
    assert classify_walk({"highway": "motorway"})["hazard"] == "is on a road where walking is not allowed"
    assert classify_walk(GREENWAY)["path"]
    assert classify_walk({"highway": "steps"})["steps"]


def test_cycling_classification():
    assert classify_cycle(BIKE_LANE_STREET)["bike_lane"]
    assert classify_cycle(SIDEWALKED_MAIN)["stress"]
    assert classify_cycle({"highway": "primary", "maxspeed": "55 mph"})["hazard"] == "uses a fast road with no bike lane"
    assert classify_cycle({"highway": "residential", "bicycle": "no"})["hazard"] == "is on a road where cycling is not allowed"


def _pairs(*ways):
    """Ways given as (tags, [nodes])."""
    out = {}
    for tags, nodes in ways:
        for a, b in zip(nodes, nodes[1:]):
            out[frozenset((a, b))] = tags
    return out


def test_assess_measures_shares_by_distance():
    pairs = _pairs((RESIDENTIAL, [1, 2, 3]), (SIDEWALKED_MAIN, [3, 4]), (GREENWAY, [4, 5]))
    segments = [(1, 2, 300.0), (2, 3, 200.0), (3, 4, 250.0), (4, 5, 250.0)]
    result = assess(segments, pairs, WALK)
    assert result["hazard"] is None
    assert result["attributes"]["sidewalk_score"] == pytest.approx(1.0)
    assert result["attributes"]["traffic_exposure"] == pytest.approx(0.25)
    assert result["attributes"]["trail_bonus"] == pytest.approx(0.25)
    assert result["facts"]["lit_pct"] == 25.0


def test_a_real_stretch_along_a_fast_road_is_a_hazard_but_a_crossing_is_not():
    pairs = _pairs((RESIDENTIAL, [1, 2]), (RURAL_ROAD, [2, 3]))
    assert assess([(1, 2, 900.0), (2, 3, 40.0)], pairs, WALK)["hazard"] is None   # crossing it
    assert assess([(1, 2, 900.0), (2, 3, 200.0)], pairs, WALK)["hazard"] == "runs along a fast road with no sidewalk"


def test_patchy_map_data_makes_no_claims():
    pairs = _pairs((RESIDENTIAL, [1, 2]))
    assert assess([(1, 2, 100.0), (7, 8, 900.0)], pairs, WALK) is None


# --- end to end through the OSRM provider --------------------------------------

START = (33.5186, -86.8104)


def _servers(way_tags: dict, calls: list):
    """An OSRM stand-in whose routes run over nodes 1..6, and an Overpass
    stand-in that returns one way over those nodes with `way_tags`."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "overpass.test":
            calls.append(parse_qs(request.content.decode())["data"][0])
            return httpx.Response(200, json={"elements": [
                {"type": "way", "id": 99, "nodes": [1, 2, 3, 4, 5, 6], "tags": way_tags}]})
        lat, lon = START
        coords = [[lon, lat], [lon + 0.003, lat], [lon + 0.003, lat + 0.003], [lon, lat + 0.003], [lon, lat]]
        return httpx.Response(200, json={"code": "Ok", "routes": [{
            "geometry": {"type": "LineString", "coordinates": coords}, "distance": 1300.0, "duration": 936.0,
            "legs": [{"annotation": {"nodes": [1, 2, 3, 4, 5, 6], "distance": [260.0] * 5}}]}]})
    return handler


def _routes(way_tags, activity=WALK, minutes=15):
    calls: list = []
    provider = OSRMRouteProvider(foot_url="https://osrm.test/route/v1/foot", bike_url="https://osrm.test/route/v1/bike",
                                 elevation_url="", overpass_url="https://overpass.test/api/interpreter",
                                 transport=httpx.MockTransport(_servers(way_tags, calls)))
    return provider.get_candidate_routes(*START, activity, minutes), calls


def test_street_details_are_measured_in_one_lookup():
    routes, calls = _routes(SIDEWALKED_MAIN)
    assert len(calls) == 1, "one Overpass request for all candidates"
    assert "node(id:1,2,3,4,5,6)" in calls[0]
    route = routes[0]
    assert {"sidewalk_score", "traffic_exposure"}.isdisjoint(route.unknown_attributes)
    assert route.sidewalk_score == 1.0 and route.traffic_exposure == 1.0
    assert route.raw_attributes["sidewalk_pct"] == 100.0
    assert route.hazard_reason == ""


def test_unwalkable_routes_are_withheld_from_everyone():
    routes, _ = _routes(HIGHWAY_280)
    selection = select_routes(routes, WALK, 15, profile=None)
    assert selection.ranked == []
    assert all(item.reason == "runs along a fast road with no sidewalk" for item in selection.excluded)


def test_cycling_routes_get_bike_lane_measures():
    routes, _ = _routes(BIKE_LANE_STREET, CYCLE, 5)
    assert routes[0].bike_lane_score == 1.0 and routes[0].raw_attributes["bike_lane_pct"] == 100.0


def test_overpass_outage_leaves_details_unverified():
    def handler(request):
        if request.url.host == "overpass.test":
            return httpx.Response(504)
        return _servers({}, [])(request)
    provider = OSRMRouteProvider(foot_url="https://osrm.test/route/v1/foot", bike_url="https://osrm.test/route/v1/bike",
                                 elevation_url="", overpass_url="https://overpass.test/api/interpreter",
                                 transport=httpx.MockTransport(handler))
    routes = provider.get_candidate_routes(*START, WALK, 15)
    assert routes and all("sidewalk_score" in r.unknown_attributes and not r.hazard_reason for r in routes)
