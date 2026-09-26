"""Calories burned, estimated from METs.

kcal = MET x body weight (kg) x hours, with MET values from the Compendium of
Physical Activities (Ainsworth et al., 2011 update) chosen by speed:

- walking: under 2.5 mph 2.8 METs; 2.5-3.4 mph 3.5; 3.5 mph and faster 4.3
- cycling (leisure): under 10 mph 4.0 METs; 10-11.9 mph 6.8; 12 mph+ 8.0

It is an estimate: real burn varies with fitness, terrain and body
composition, and the app says "about". Speed comes from the route's distance
and time, so a slow walk is not credited like a brisk one.
"""
from __future__ import annotations

from app.models.enums import ActivityTypeEnum

_MPH_PER_KMH = 0.621371

WALK_METS = ((2.5, 2.8), (3.5, 3.5), (float("inf"), 4.3))   # (below mph, MET)
CYCLE_METS = ((10.0, 4.0), (12.0, 6.8), (float("inf"), 8.0))


def met_for(activity_type: ActivityTypeEnum, speed_mph: float | None) -> float:
    table = WALK_METS if activity_type == ActivityTypeEnum.walk else CYCLE_METS
    if speed_mph is None:
        return table[1][1]  # typical pace
    return next(met for below, met in table if speed_mph < below)


def estimate_calories(activity_type: ActivityTypeEnum, minutes: float, weight_kg: float | None,
                      distance_m: float | None = None) -> int | None:
    """Rounded kcal, or None without a weight or time to base it on."""
    if not weight_kg or not minutes or minutes <= 0:
        return None
    speed = None
    if distance_m:
        speed = (distance_m / 1000) / (minutes / 60) * _MPH_PER_KMH
    return round(met_for(activity_type, speed) * weight_kg * minutes / 60)
