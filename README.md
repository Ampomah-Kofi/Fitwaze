# FitWaze

FitWaze = PERSON + PLACE = PERSONALIZED PHYSICAL ACTIVITY.

FitWaze is a health & mobility MVP backend with two engines:

- **Activity Engine** — given a user's health/fitness profile, recommends a walk/cycle activity and duration.
- **Route Engine** — given the recommendation and a location, generates and scores 2-3 candidate routes.

This repository currently contains the backend API only. A Geography department team will
later plug in real GIS/routing data via the `RouteProvider` interface described in
`backend/app/engines/route_engine/base.py`.

> Status: work in progress, built incrementally per the approved implementation plan.
> This section will be expanded with full setup instructions and a security verification
> checklist as the build progresses.

## Tech stack

- Python 3.12 + FastAPI, Pydantic v2
- PostgreSQL 16 + PostGIS, SQLAlchemy 2.0 + GeoAlchemy2, Alembic
- Argon2id password hashing
- JWT access tokens (15 min) + rotating refresh tokens (httpOnly cookie, 7 day expiry)
- `pydantic-settings` + `.env` for configuration
- `slowapi` for rate limiting
- Docker Compose for local dev (Postgres+PostGIS, backend)
- `pytest` for testing

## Project layout

```
FITWAZE/
├── docs/                 # architecture, threat model, data retention policy
├── docker-compose.yml    # postgres+postgis + backend services
└── backend/
    ├── app/              # FastAPI application
    └── tests/            # pytest suite
```

## Quick start

Detailed setup instructions (environment variables, running migrations, running the API,
running tests, Docker) are documented at the end of this README once the corresponding
build steps are complete.
