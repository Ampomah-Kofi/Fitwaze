"""slowapi rate limiter configuration.

A single shared `Limiter` instance keyed by client IP. Strict limits are
applied per-route (see `app/routers/auth.py` for `/auth/login` and
`/auth/register`); everything else falls back to the lenient default limit
configured here.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

from app.config import get_settings

DEFAULT_RATE_LIMIT = "60/minute"
AUTH_RATE_LIMIT = "5/minute"


def client_ip(request: Request) -> str:
    """The caller's address for rate limiting.

    Behind a hosting platform's proxy every request arrives from the proxy,
    so the real caller is in X-Forwarded-For. Each proxy *appends* the address
    it saw, so with N trusted proxies in front the caller is the Nth entry
    from the end; anything before that was written by the caller and cannot
    be trusted (taking the first entry would let anyone dodge the login limit
    by sending a fake header). With no trusted proxies configured the header
    is ignored entirely.
    """
    hops = get_settings().trusted_proxy_hops
    forwarded = request.headers.get("x-forwarded-for")
    if hops > 0 and forwarded:
        hosts = [item.strip() for item in forwarded.split(",") if item.strip()]
        if len(hosts) >= hops:
            return hosts[-hops]
    return get_remote_address(request)


limiter = Limiter(key_func=client_ip, default_limits=[DEFAULT_RATE_LIMIT])
