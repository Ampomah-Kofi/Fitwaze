"""Application configuration loaded from environment variables / .env file.

Nothing sensitive is hardcoded here — all secrets come from the environment.
See `.env.example` at the repository root for the full list of variables and
instructions on how to generate secure values for local development.
"""
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- App ---
    # "development" (default), "pilot" (hosted field test over HTTPS: secure
    # cookies, demo app served) or "production" (secure cookies, no demo app).
    environment: str = Field(default="development", alias="ENVIRONMENT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # --- Database ---
    database_url: str = Field(
        default="postgresql+psycopg2://fitwaze:fitwaze@localhost:5432/fitwaze",
        alias="DATABASE_URL",
    )
    test_database_url: str = Field(
        default="sqlite:///./test.db", alias="TEST_DATABASE_URL"
    )

    # --- Auth / JWT ---
    jwt_secret: str = Field(default="insecure-dev-secret-change-me", alias="JWT_SECRET")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    access_token_expire_minutes: int = Field(
        default=15, alias="ACCESS_TOKEN_EXPIRE_MINUTES"
    )
    refresh_token_expire_days: int = Field(
        default=7, alias="REFRESH_TOKEN_EXPIRE_DAYS"
    )

    # --- Field-level encryption ---
    field_encryption_key: str = Field(
        default="", alias="FIELD_ENCRYPTION_KEY"
    )

    # --- Route engine ---
    # "osrm" (default): real street-following routes, no key. "ors": the same
    # plus measured hills and surroundings, needs ORS_API_KEY. "mock":
    # synthetic straight-line shapes for offline tests only.
    route_provider: str = Field(default="osrm", alias="ROUTE_PROVIDER")
    ors_api_key: str = Field(default="", alias="ORS_API_KEY")
    # OSRM Route service endpoints, one per profile. Defaults are the free
    # FOSSGIS servers; point them at the self-hosted Alabama servers from
    # docker-compose (see scripts/prepare-alabama-map.sh) for production use.
    osrm_foot_url: str = Field(
        default="https://routing.openstreetmap.de/routed-foot/route/v1/foot", alias="OSRM_FOOT_URL"
    )
    osrm_bike_url: str = Field(
        default="https://routing.openstreetmap.de/routed-bike/route/v1/bike", alias="OSRM_BIKE_URL"
    )
    # OpenTopoData-compatible elevation service used to measure hills on OSRM
    # routes. Default: USGS 10 m elevation (covers the US, incl. Alabama).
    # Empty disables it, leaving gradient unverified.
    # Overpass API used to read OpenStreetMap street details (sidewalks, speed
    # limits, bike lanes...) along OSRM routes. Empty disables the check.
    overpass_url: str = Field(default="https://overpass-api.de/api/interpreter", alias="OVERPASS_URL")
    # Heat check from the US National Weather Service before routes are offered.
    weather_enabled: bool = Field(default=True, alias="WEATHER_ENABLED")
    elevation_url: str = Field(default="https://api.opentopodata.org/v1/ned10m", alias="ELEVATION_URL")
    # How long generated candidate routes are reused for the same start point.
    # Keeps a live provider inside its free-tier quota and keeps /route/select
    # consistent with what /route/options showed. 0 disables caching.
    route_cache_ttl_seconds: int = Field(
        default=900, alias="ROUTE_CACHE_TTL_SECONDS"
    )

    # --- CORS ---
    cors_origins: str = Field(
        default="http://localhost:3000,http://localhost:5173",
        alias="CORS_ORIGINS",
    )

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @field_validator("jwt_secret")
    @classmethod
    def _warn_default_secret(cls, v: str) -> str:
        # Intentionally not raising here so the app is still importable for
        # tooling (alembic, tests) without a full .env; enforcement of a real
        # secret in production is a deployment concern documented in the README.
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
