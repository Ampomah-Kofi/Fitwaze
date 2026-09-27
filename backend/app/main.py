"""FastAPI application entrypoint: app setup, middleware, and router wiring."""
import logging
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
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


# --- Demo client -----------------------------------------------------------
# A single-file Leaflet page that drives the whole flow (register -> profile ->
# recommendation -> route options -> session -> progress) against this API.
# Served from the API's own origin so the browser needs no CORS exemption, and
# deliberately not exposed in production: it is a demonstration/QA aid, not a
# product surface.
DEMO_PAGE = Path(__file__).parent / "static" / "demo.html"


# --- Install to the home screen ----------------------------------------------
# A web app manifest and icons let people add FitWaze to their home screen,
# where it opens full screen like an installed app (no browser bars).
STATIC_DIR = Path(__file__).parent / "static"
APP_ICONS = {"icon-180.png", "icon-192.png", "icon-512.png"}

MANIFEST = {
    "name": "FitWaze",
    "short_name": "FitWaze",
    "description": "Personalized walks and rides near home.",
    "start_url": "/",
    "scope": "/",
    "display": "standalone",
    "orientation": "portrait",
    "background_color": "#f2f2f7",
    "theme_color": "#246347",
    "icons": [
        {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
        {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
    ],
}


@app.get("/manifest.webmanifest", include_in_schema=False)
def web_manifest() -> JSONResponse:
    if settings.environment.lower() == "production":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return JSONResponse(MANIFEST, media_type="application/manifest+json")


def _icon_route(name: str):
    # One explicit route per icon: nothing else in the folder is reachable.
    def serve_icon() -> FileResponse:
        if settings.environment.lower() == "production":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        return FileResponse(STATIC_DIR / name, media_type="image/png",
                            headers={"Cache-Control": "public, max-age=86400"})
    app.add_api_route(f"/{name}", serve_icon, methods=["GET"], include_in_schema=False)


for _icon in sorted(APP_ICONS):
    _icon_route(_icon)


@app.get("/", include_in_schema=False)
@app.get("/demo", include_in_schema=False)
@app.get("/mobile", include_in_schema=False)
def demo_page() -> FileResponse:
    if settings.environment.lower() == "production" or not DEMO_PAGE.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    # Always check for a newer page: without this, phones keep showing the
    # version from before a deploy.
    return FileResponse(DEMO_PAGE, media_type="text/html", headers={"Cache-Control": "no-cache"})
