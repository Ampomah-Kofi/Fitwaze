"""slowapi rate limiter configuration.

A single shared `Limiter` instance keyed by client IP. Strict limits are
applied per-route (see `app/routers/auth.py` for `/auth/login` and
`/auth/register`); everything else falls back to the lenient default limit
configured here.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

DEFAULT_RATE_LIMIT = "60/minute"
AUTH_RATE_LIMIT = "5/minute"

limiter = Limiter(key_func=get_remote_address, default_limits=[DEFAULT_RATE_LIMIT])
