"""Is this route really walkable (or cyclable)? Street details from OpenStreetMap.

OSRM tells us exactly which OpenStreetMap nodes a route passes through. For
those nodes we ask an Overpass server (free, no key) for the streets they
belong to and read what OpenStreetMap records about each one: the kind of
way (main road, residential street, footpath, cycle path, steps), the speed
limit, sidewalks, bike lanes, street lighting, and whether walking or
cycling is banned. Every stretch of the route is matched to its street, so
the result is measured along the route itself, weighted by distance.

What it produces for each route:
- scoring attributes the route engine already uses (sidewalk coverage,
  traffic exposure, paths, steps, bike lanes, traffic stress), now measured
  instead of placeholders;
- facts for the route card (e.g. "Sidewalks 80%", "Busy roads 10%");
- a hazard: a real stretch along a fast road with no sidewalk (or, cycling,
  no bike lane), or on a road where walking/cycling is not allowed. Routes
  with one are not offered to anyone.

US speed limits are in mph; OpenStreetMap values without a unit are km/h.
Where a road has no speed limit tag, the usual US default for its type is
assumed. If too little of a route can be matched (patchy map data), nothing
is claimed and the attributes stay unverified.
"""
from __future__ import annotations

import logging
import re

import httpx

from app.models.enums import ActivityTypeEnum

logger = logging.getLogger(__name__)

USER_AGENT = "FitWaze pilot (github.com/Ampomah-Kofi/Fitwaze)"

# Usual US speeds (mph) by road type when no maxspeed is tagged.
_DEFAULT_MPH = {
    "motorway": 65, "motorway_link": 45, "trunk": 55, "trunk_link": 45, "primary": 45, "primary_link": 35,
    "secondary": 40, "secondary_link": 30, "tertiary": 35, "tertiary_link": 25, "unclassified": 30,
    "residential": 25, "service": 15, "living_street": 10, "road": 30,
}
_PATHS = {"footway", "path", "pedestrian", "track", "cycleway", "bridleway", "steps"}
_BUSY_TYPES = {"trunk", "trunk_link", "primary", "primary_link", "secondary", "secondary_link", "tertiary", "tertiary_link"}
_QUIET_TYPES = {"residential", "living_street", "service"}
_SIDEWALK_YES = {"both", "left", "right", "yes", "separate"}

BUSY_MPH = 35           # roads this fast count as busy for someone on foot
FAST_NO_SIDEWALK_MPH = 45   # walking beside traffic this fast with no sidewalk is unsafe
FAST_NO_BIKE_LANE_MPH = 50  # riding in traffic this fast with no bike lane is unsafe
HIGH_STRESS_MPH = 40
# A hazard counts once it is more than a short crossing or corner.
HAZARD_SHARE = 0.05
HAZARD_MIN_M = 80.0
# Below this share of the route matched to streets, make no claims.
MIN_COVERAGE = 0.6


def speed_mph(tags: dict) -> float:
    value = tags.get("maxspeed", "")
    match = re.match(r"\s*(\d+(?:\.\d+)?)\s*(mph)?", value)
    if match:
        number = float(match.group(1))
        return number if match.group(2) else number * 0.621371
    return float(_DEFAULT_MPH.get(tags.get("highway", ""), 25))


def _has_sidewalk(tags: dict) -> bool:
    if tags.get("sidewalk") in _SIDEWALK_YES:
        return True
    return any(tags.get(f"sidewalk:{side}") in ("yes", "separate") for side in ("both", "left", "right"))


def _has_bike_lane(tags: dict) -> bool:
    if tags.get("highway") == "cycleway" or tags.get("bicycle") == "designated":
        return True
    keys = ("cycleway", "cycleway:both", "cycleway:left", "cycleway:right")
    return any(tags.get(key) in ("lane", "track", "separate", "opposite_lane", "opposite_track") for key in keys)


def classify_walk(tags: dict) -> dict:
    highway = tags.get("highway", "")
    mph = speed_mph(tags)
    dedicated = highway in _PATHS or highway == "living_street"
    sidewalk = _has_sidewalk(tags)
    forbidden = tags.get("foot") == "no" or highway in ("motorway", "motorway_link")
    no_sidewalk_fast = not dedicated and not sidewalk and mph >= FAST_NO_SIDEWALK_MPH
    return {
        "walkable_side": dedicated or sidewalk or (highway in _QUIET_TYPES and mph <= 25),
        "busy": not dedicated and (highway in _BUSY_TYPES or mph >= BUSY_MPH),
        "path": highway in _PATHS and highway != "steps",
        "steps": highway == "steps",
        "sidewalk": sidewalk,
        "lit": tags.get("lit") == "yes",
        "hazard": "is on a road where walking is not allowed" if forbidden
        else "runs along a fast road with no sidewalk" if no_sidewalk_fast else None,
    }


def classify_cycle(tags: dict) -> dict:
    highway = tags.get("highway", "")
    mph = speed_mph(tags)
    lane = _has_bike_lane(tags)
    off_road = highway in ("path", "track", "cycleway") and tags.get("bicycle") != "no"
    forbidden = tags.get("bicycle") == "no" or highway in ("motorway", "motorway_link")
    fast_no_lane = not lane and not off_road and mph >= FAST_NO_BIKE_LANE_MPH
    return {
        "bike_lane": lane or off_road,
        "stress": not lane and not off_road and (highway in ("trunk", "trunk_link", "primary", "primary_link") or mph >= HIGH_STRESS_MPH),
        "steps": highway == "steps",
        "lit": tags.get("lit") == "yes",
        "hazard": "is on a road where cycling is not allowed" if forbidden
        else "uses a fast road with no bike lane" if fast_no_lane else None,
    }


class StreetInfoClient:
    """Fetches OpenStreetMap street tags for route segments from Overpass."""

    def __init__(self, url: str, client: httpx.Client) -> None:
        self.url = url
        self.client = client

    def tags_by_segment(self, node_ids: set[int]) -> dict[frozenset, dict] | None:
        """{frozenset({node_a, node_b}): tags} for every street segment that
        touches the given nodes, or None if the lookup failed."""
        if not node_ids:
            return {}
        query = (
            "[out:json][timeout:20];"
            f"node(id:{','.join(str(n) for n in sorted(node_ids))});"
            "way(bn)[highway];out body;"
        )
        try:
            response = self.client.post(self.url, data={"data": query},
                                        headers={"User-Agent": USER_AGENT}, timeout=20.0)
            response.raise_for_status()
            elements = response.json()["elements"]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            logger.warning("street_lookup_failed error=%s", type(exc).__name__)
            return None

        pairs: dict[frozenset, dict] = {}
        for element in elements:
            if element.get("type") != "way":
                continue
            nodes, tags = element.get("nodes") or [], element.get("tags") or {}
            for a, b in zip(nodes, nodes[1:]):
                pairs[frozenset((a, b))] = tags
        return pairs


def assess(segments: list[tuple[int, int, float]], pairs: dict[frozenset, dict],
           activity_type: ActivityTypeEnum) -> dict | None:
    """Measure one route from its (node, node, metres) segments.

    Returns {"attributes": {...}, "facts": {...}, "hazard": reason | None},
    or None when too little of the route matched known streets.
    """
    total = sum(metres for _, _, metres in segments)
    matched: list[tuple[dict, float]] = []
    for a, b, metres in segments:
        tags = pairs.get(frozenset((a, b)))
        if tags is not None:
            matched.append((tags, metres))
    covered = sum(metres for _, metres in matched)
    if total <= 0 or covered / total < MIN_COVERAGE:
        return None

    classify = classify_walk if activity_type == ActivityTypeEnum.walk else classify_cycle
    rows = [(classify(tags), metres) for tags, metres in matched]

    def share(flag: str) -> float:
        return sum(metres for row, metres in rows if row[flag]) / covered

    hazards: dict[str, float] = {}
    for row, metres in rows:
        if row["hazard"]:
            hazards[row["hazard"]] = hazards.get(row["hazard"], 0.0) + metres
    hazard = None
    if hazards:
        reason, metres = max(hazards.items(), key=lambda item: item[1])
        if metres >= HAZARD_MIN_M or metres / covered >= HAZARD_SHARE:
            hazard = reason

    steps = share("steps")
    facts = {"steps_pct": round(steps * 100, 1), "lit_pct": round(share("lit") * 100, 1)}
    if activity_type == ActivityTypeEnum.walk:
        attributes = {
            "sidewalk_score": share("walkable_side"),
            "traffic_exposure": share("busy"),
            "trail_bonus": share("path"),
            "stairs": min(1.0, steps / 0.10),
        }
        facts.update(sidewalk_pct=round(share("walkable_side") * 100, 1),
                     busy_road_pct=round(share("busy") * 100, 1),
                     paths_pct=round(share("path") * 100, 1))
    else:
        attributes = {
            "bike_lane_score": share("bike_lane"),
            "traffic_stress": share("stress"),
            "stairs": min(1.0, steps / 0.10),
        }
        facts.update(bike_lane_pct=round(share("bike_lane") * 100, 1),
                     busy_road_pct=round(share("stress") * 100, 1))
    return {"attributes": attributes, "facts": facts, "hazard": hazard}
