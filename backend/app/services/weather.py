"""Heat check for the start of a walk, from the US National Weather Service.

Alabama summers are a real risk for older people with diabetes: heat and
humidity raise the chance of dehydration and heat illness, and some
diabetes medicines make it worse. Before routes are offered we look up the
current hour's forecast at the start point (api.weather.gov: free, no key,
US only), work out the heat index, and return plain advice.

Failures (outside the US, service down, slow) return None: routes are still
offered, just without a heat note. Nothing personal is sent: only a start
point rounded to about a kilometre.
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

NWS_BASE_URL = "https://api.weather.gov"
# NWS asks every client to identify itself.
USER_AGENT = "FitWaze pilot (github.com/Ampomah-Kofi/Fitwaze)"
_CACHE_SECONDS = 30 * 60
_cache: dict[tuple[float, float], tuple[float, "HeatCheck | None"]] = {}

# NWS heat index bands (degrees F).
CAUTION_F = 80
EXTREME_CAUTION_F = 90
DANGER_F = 103
COLD_BELOW_F = 40

ADVICE = {
    "ok": "Comfortable conditions for a walk.",
    "caution": "It's warm. Drink water before you go and take it at an easy pace.",
    "extreme_caution": (
        "It's hot and humid. Walk in the early morning or evening if you can, choose "
        "the shorter route, carry water, and stop if you feel dizzy or sick."
    ),
    "danger": (
        "It's dangerously hot right now. Please don't walk outside: walk indoors (a mall "
        "or gym), or wait for a cooler time of day."
    ),
    "cold": "It's cold. Wear warm layers and good shoes, and take it slowly.",
}


@dataclass
class HeatCheck:
    temperature_f: float
    relative_humidity: float | None
    heat_index_f: float
    level: str
    advice: str


def heat_index_f(temperature_f: float, relative_humidity: float) -> float:
    """The NWS heat index (Rothfusz regression with its published adjustments)."""
    t, rh = temperature_f, relative_humidity
    simple = 0.5 * (t + 61.0 + (t - 68.0) * 1.2 + rh * 0.094)
    if (simple + t) / 2 < 80:
        return simple
    hi = (-42.379 + 2.04901523 * t + 10.14333127 * rh - 0.22475541 * t * rh
          - 0.00683783 * t * t - 0.05481717 * rh * rh + 0.00122874 * t * t * rh
          + 0.00085282 * t * rh * rh - 0.00000199 * t * t * rh * rh)
    if rh < 13 and 80 <= t <= 112:
        hi -= ((13 - rh) / 4) * math.sqrt((17 - abs(t - 95)) / 17)
    elif rh > 85 and 80 <= t <= 87:
        hi += ((rh - 85) / 10) * ((87 - t) / 5)
    return hi


def classify(temperature_f: float, relative_humidity: float | None) -> HeatCheck:
    index = heat_index_f(temperature_f, relative_humidity) if relative_humidity is not None else temperature_f
    if index >= DANGER_F:
        level = "danger"
    elif index >= EXTREME_CAUTION_F:
        level = "extreme_caution"
    elif index >= CAUTION_F:
        level = "caution"
    elif temperature_f < COLD_BELOW_F:
        level = "cold"
    else:
        level = "ok"
    return HeatCheck(round(temperature_f, 1), relative_humidity, round(index, 1), level, ADVICE[level])


def heat_check(lat: float, lon: float, transport: httpx.BaseTransport | None = None) -> HeatCheck | None:
    """Current heat advice at (lat, lon), or None when unavailable."""
    if not get_settings().weather_enabled:
        return None
    key = (round(lat, 2), round(lon, 2))
    cached = _cache.get(key)
    if cached and time.monotonic() - cached[0] < _CACHE_SECONDS:
        return cached[1]

    result: HeatCheck | None = None
    try:
        with httpx.Client(timeout=4.0, transport=transport, headers={"User-Agent": USER_AGENT,
                                                                   "Accept": "application/geo+json"}) as client:
            points = client.get(f"{NWS_BASE_URL}/points/{key[0]:.2f},{key[1]:.2f}")
            points.raise_for_status()
            hourly_url = points.json()["properties"]["forecastHourly"]
            hourly = client.get(hourly_url)
            hourly.raise_for_status()
            period = hourly.json()["properties"]["periods"][0]
            temperature = float(period["temperature"])
            if period.get("temperatureUnit") == "C":
                temperature = temperature * 9 / 5 + 32
            humidity = (period.get("relativeHumidity") or {}).get("value")
            result = classify(temperature, float(humidity) if humidity is not None else None)
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        logger.warning("heat_check_failed error=%s", type(exc).__name__)
        result = None

    _cache[key] = (time.monotonic(), result)
    return result


def clear_weather_cache() -> None:
    _cache.clear()
