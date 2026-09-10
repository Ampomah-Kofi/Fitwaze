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

## Map & route data

FitWaze needs map data in two places, and the whole stack below is free and
needs no credit card — which is deliberate, since this build is an MVP/demo.

### 1. Routing data (backend)

This is what feeds `RouteProvider.get_candidate_routes()`. Two providers ship
in the repo, selected with the `ROUTE_PROVIDER` environment variable:

| `ROUTE_PROVIDER` | What it does | Cost |
| --- | --- | --- |
| `mock` (default) | Deterministic synthetic routes, no network calls, no key | Free, works offline |
| `ors` | Real routes from OpenRouteService's round-trip Directions API | Free tier, ~2,000 requests/day |

The `mock` provider is what the test suite exercises and is enough to demo the
full flow offline. To use real routes:

1. Sign up for a free key at <https://openrouteservice.org/dev/#/signup> (no
   payment details required).
2. Set `ROUTE_PROVIDER=ors` and `ORS_API_KEY=<your key>` in `backend/.env`.

**Quota maths.** One `POST /route/options` costs three ORS requests (one per
candidate shape), so a 2,000/day budget is roughly 660 route-option requests per
day across all users. `ROUTE_CACHE_TTL_SECONDS` (default 900) caches candidates
per rounded start point, activity and duration, which cuts repeat requests from
the same spot to zero and also guarantees `POST /route/select` persists exactly
the route the user was shown. If you outgrow the free tier, self-hosting ORS,
GraphHopper or Valhalla against a regional OSM extract removes the limit
entirely at no licence cost.

> **Not yet verified against the live API.** `tests/test_ors_provider.py` runs
> the provider against a simulated ORS service, so the request shape, the
> `[lon, lat]` → `[lat, lon]` conversion and the failure handling are covered.
> Nothing here has touched the real service though (no key or network access
> during development), so smoke-test it with a real key before a live demo.

### 2. Map rendering (frontend)

The API returns geometry as ordered `[latitude, longitude]` pairs, which drop
straight into **Leaflet** (`L.polyline`) or **MapLibre GL JS** — both open
source, no API key, no usage billing. Tiles can come from OpenStreetMap's
standard tile servers (mind their usage policy) or a self-hosted Protomaps
file. **Nominatim** or **Photon** cover address search for the start point.

Google Maps is deliberately *not* used here: its terms forbid persisting route
geometry the way `activity_sessions.route_geometry` does, it has no round-trip
routing, and even its free tier requires a billing account.

### Attribution

Routing data derives from OpenStreetMap, licensed under the
[ODbL](https://www.openstreetmap.org/copyright). Any map view or published
write-up must credit “© OpenStreetMap contributors”, and OpenRouteService asks
to be credited alongside it.

## Demo client

A single-file Leaflet page drives the whole flow — register, health profile,
activity recommendation, route options on a map, session selection, completion
and progress — against a running API:

```bash
cd backend
uvicorn app.main:app --reload
# then open http://localhost:8000/demo
```

Register an account, save the profile, ask for a recommendation, then set a
start point — either **Use my current location** (browser geolocation, shown
with its accuracy radius) or by **clicking anywhere on the map** — and generate
route options. The three candidates are drawn in rank order (green = best) with
their score and explanation; selecting one persists a session you can complete.

The options list states which provider produced the routes, so a synthetic
`mock` route is never mistaken for a real street. `POST /route/options` returns
the same information in its `provider` field.

**Geolocation needs a secure context.** Browsers allow it on HTTPS and on
`localhost` only. Opening the demo over a LAN address (`http://192.168.x.x`) on
a phone will refuse to locate — put an HTTPS tunnel in front of it, or fall back
to clicking the map.

The page is served from the API's own origin (so no CORS exemption is needed)
and is not exposed when `ENVIRONMENT=production` — it is a demonstration and QA
aid, not a product surface. It keeps its access token in a JavaScript variable
for the life of the tab and never writes credentials to browser storage.

With `ROUTE_PROVIDER=mock` this works fully offline, which makes it a safe bet
for a live presentation; only the map tiles need the network.

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
