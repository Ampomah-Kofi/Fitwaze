"""FastAPI application entrypoint: app setup, middleware, and router wiring."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.config import get_settings
from app.logging_config import configure_logging
from app.security.rate_limit import limiter

configure_logging()
logger = logging.getLogger(__name__)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("fitwaze_backend_startup environment=%s", settings.environment)
    yield


app = FastAPI(
    title="FitWaze API",
    description="PERSON + PLACE = PERSONALIZED PHYSICAL ACTIVITY",
    version="0.1.0",
    lifespan=lifespan,
)

# --- Rate limiting ---
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# --- CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["health"])
def health_check() -> dict:
    """Liveness/readiness probe. Returns no sensitive information."""
    return {"status": "ok", "service": "fitwaze-backend"}


# --- Routers (added incrementally as each vertical slice is built) ---
from app.routers import auth as auth_router  # noqa: E402
from app.routers import profile as profile_router  # noqa: E402
from app.routers import activity as activity_router  # noqa: E402
from app.routers import route as route_router  # noqa: E402
from app.routers import progress as progress_router  # noqa: E402

app.include_router(auth_router.router)
app.include_router(profile_router.router)
app.include_router(activity_router.router)
app.include_router(route_router.router)
app.include_router(progress_router.router)
