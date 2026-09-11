"""Short-lived in-process cache of provider candidate routes.

Two reasons, both of which only bite once a real provider is configured:

1. **Free-tier quota.** OpenRouteService's free key allows roughly 2,000
   requests/day and a single POST /route/options costs three of them (one per
   candidate shape). Without caching, a user nudging the map around a few
   times can burn a meaningful slice of the daily budget.
2. **Consistency between /options and /select.** POST /route/select re-derives
   the candidates so it can persist the engine's own output rather than
   trusting the client. The mock provider is deterministic, but a live routing
   service need not be — without a cache a user could be shown one route and
   have a subtly different one saved.

The cache lives in the process (no Redis/extra infrastructure for the MVP), so
it is per-worker and empties on restart — both acceptable, since a miss just
means a normal provider call. Keys hold a coarsened start point and no user
id, so a cache entry cannot be tied back to the person who requested it.
"""
from __future__ import annotations

import logging
import time
import math
from threading import RLock

from app.config import get_settings
from app.engines.route_engine.base import RawRoute, RouteProvider, RouteProviderError, returns_to_start
from app.models.enums import ActivityTypeEnum

logger = logging.getLogger(__name__)

# Start points are rounded to ~11 m before they become part of a cache key, so
# small GPS jitter at the same street corner reuses one provider call.
_KEY_COORD_PRECISION = 4

# Bounded so a long-running process cannot grow without limit; when full, the
# oldest entries are dropped first.
_MAX_ENTRIES = 512

_CacheKey = tuple[str, float, float, str, int]
_cache: dict[_CacheKey, tuple[float, list[RawRoute]]] = {}
_cache_lock = RLock()


def _make_key(
    provider: RouteProvider,
    start_lat: float,
    start_lon: float,
    activity_type: ActivityTypeEnum,
    target_duration_min: int,
) -> _CacheKey:
    return (
        type(provider).__name__,
        round(start_lat, _KEY_COORD_PRECISION),
        round(start_lon, _KEY_COORD_PRECISION),
        activity_type.value,
        target_duration_min,
    )


def _evict_expired_and_overflow(now: float) -> None:
    for key in [key for key, (expires_at, _) in _cache.items() if expires_at <= now]:
        del _cache[key]

    while len(_cache) >= _MAX_ENTRIES:
        del _cache[next(iter(_cache))]


def clear_route_cache() -> None:
    """Drop every cached entry (used by the test suite)."""
    with _cache_lock:
        _cache.clear()


def get_candidate_routes_cached(
    provider: RouteProvider,
    start_lat: float,
    start_lon: float,
    activity_type: ActivityTypeEnum,
    target_duration_min: int,
) -> list[RawRoute]:
    ttl_seconds = get_settings().route_cache_ttl_seconds
    key = _make_key(provider, start_lat, start_lon, activity_type, target_duration_min)
    now = time.monotonic()

    with _cache_lock:
        cached = _cache.get(key) if ttl_seconds > 0 else None
    if cached is not None and cached[0] > now:
        return cached[1]

    routes = provider.get_candidate_routes(
        start_lat=start_lat,
        start_lon=start_lon,
        activity_type=activity_type,
        target_duration_min=target_duration_min,
    )
    routes = _usable_routes(provider, routes, start_lat, start_lon)

    if ttl_seconds > 0:
        with _cache_lock:
            now = time.monotonic()
            _evict_expired_and_overflow(now)
            _cache[key] = (now + ttl_seconds, routes)
            logger.debug("route_cache_store provider=%s entries=%d", key[0], len(_cache))
    return routes


def _usable_routes(
    provider: RouteProvider, routes: list[RawRoute], start_lat: float, start_lon: float
) -> list[RawRoute]:
    """Reject invalid geometry and one-way routes before they can be offered."""
    usable = [route for route in routes
              if returns_to_start(route, start_lat, start_lon)
              and math.isfinite(route.distance_m) and route.distance_m > 0
              and math.isfinite(route.estimated_minutes) and route.estimated_minutes > 0]
    offenders = [
        route.label or "unlabelled"
        for route in routes
        if route not in usable
    ]
    if offenders:
        logger.warning(
            "route_provider_returned_one_way_routes provider=%s labels=%s",
            type(provider).__name__,
            ",".join(offenders),
        )
    if not usable:
        raise RouteProviderError("No usable round-trip routes")
    return usable
