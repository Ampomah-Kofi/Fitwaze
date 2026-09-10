"""Route provider selection.

`get_route_provider()` returns the configured `RouteProvider` implementation
based on the `ROUTE_PROVIDER` env var ("mock", default, or "ors"). This is
the single place that needs to change to plug in a new data source.
"""
from __future__ import annotations

from app.config import get_settings
from app.engines.route_engine.base import RawRoute, RouteProvider
from app.engines.route_engine.mock_provider import MockRouteProvider

__all__ = ["RawRoute", "RouteProvider", "get_route_provider"]


def get_route_provider() -> RouteProvider:
    settings = get_settings()
    if settings.route_provider.lower() == "ors":
        from app.engines.route_engine.ors_provider import ORSRouteProvider

        return ORSRouteProvider()
    return MockRouteProvider()
