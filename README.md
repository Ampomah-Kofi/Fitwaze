# FitWaze

FitWaze = PERSON + PLACE = PERSONALIZED PHYSICAL ACTIVITY.

FitWaze is a health & mobility MVP backend with two engines:

- **Activity Engine** — given a user's health/fitness profile, recommends a walk/cycle activity and duration.
- **Route Engine** — given the recommendation and a location, generates and scores 2-3 candidate routes.

This repository currently contains the backend API only. A Geography department team will
later plug in real GIS/routing data via the `RouteProvider` interface described in
`backend/app/engines/route_engine/base.py`.

> Status: working backend MVP with a browser demo and an automated test suite.
> See Quick start below to run it locally.

The agreed product direction and demo acceptance flow are recorded in
[the product brief](docs/PRODUCT_BRIEF.md).
The latest logic and flow audit is in [the system review](docs/SYSTEM_REVIEW.md).

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
per rounded start point, activity and duration, which reduces repeat requests.
The demo echoes each option's `candidate_revision` when selecting it; if cache
expiry, a restart, or another worker produces a changed candidate, selection
returns 409 and asks the user to fetch routes again. Other clients should also
send this optional field to get the same protection. If you outgrow the free tier, self-hosting ORS,
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

A mobile interface with a full-screen handset layout and bottom navigation
(Today / Route / Progress). Larger screens show the same app at phone width. It
drives the whole flow — register, health profile, activity recommendation, route
options on a map, live tracking, session completion and progress — against a
running API:
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

Use **For this session** to accept the engine's choice or request walking or
cycling. The engine recalculates the recommendation using the saved profile
and refuses activities reported as unavailable. **Enter another starting
location** accepts latitude/longitude when GPS or map clicking is inconvenient.

Each route also offers **Try a simulated journey**. After selecting that mode,
tap **Start simulation**: the marker follows the chosen geometry in roughly
20 seconds without GPS. **Finish** adds a completion to the separate demo
progress area for the current tab. Simulation does not write an activity
session or contribute to real progress totals.

Open [FitWaze](http://localhost:8000/) for the mobile interface. `/demo` and
`/mobile` serve the same app. It uses a compact welcome screen, a collapsible
profile, fixed bottom navigation, and **Map & start point / Routes & activity**
controls. There is no desktop-layout mode. On a phone,
open the server's reachable address rather than `localhost`
(which refers to the phone itself). Location and tracking require HTTPS there.

The options list states which provider produced the routes, so a synthetic
`mock` route is never mistaken for a real street. `POST /route/options` returns
the same information in its `provider` field.

### Personalised route scoring

Route scores are not the same for everyone. `terrain_emphasis()` in
`scoring.py` reads the caller's own health profile — reported mobility
limitation, the ability relevant to the activity, and age — and raises the
weight of the terrain factors (`slope_inv`, `step_free`, crossings and
intersection complexity) accordingly, renormalising so scores stay on the same
0-100 scale. Steps are tracked separately from slope, because they are a
different obstacle: a hill is tiring, a flight of stairs can be impassable.

Worked example — the same two candidates, three different people:

| Profile | Ranking |
| --- | --- |
| No limitation, age 34 | stepped park route (82.9) > flat pavement (75.1) |
| Moderate limitation | flat pavement (79.7) > stepped park route (70.2) |
| Severe limitation | flat pavement (81.3) > stepped park route (65.7) |

The multipliers are module-level constants with names, so a supervisor or
physiotherapist can argue with them. The strongest applicable signal wins rather
than compounding, and a withheld limitation is treated as a mild one rather than
as none.

### Routes are round trips

Every candidate begins and ends at the user's own start point. This is part of
the `RouteProvider` contract, not an accident of the mock: FitWaze recommends
activity from wherever someone happens to be, so a one-way route would strand
them at the far end with a journey home nobody costed. `returns_to_start()`
checks it, and the API removes invalid or one-way candidates before offering
them, even when caching is disabled. If none remain, the API returns an
actionable 503 response. This also covers routing-service failures.

ORS satisfies this through its `round_trip` option. The mock generates an
out-and-back plus two loops, each centred one radius off the start so the start
point lies on the loop itself.

### Is the route actually walkable?

Two separate questions, answered in two places.

**Walkable at all.** With `ROUTE_PROVIDER=ors` this holds by construction: the
`foot-walking` and `cycling-regular` profiles only route over ways tagged as
walkable or cyclable, so a candidate never runs down a motorway. With `mock`
it does not hold at all — that provider draws synthetic geometry and ignores
streets entirely, which is why the UI labels its output as synthetic.

**Walkable by this person.** Weighting steps more heavily is not enough: a
stair-ridden route can still come top when the alternatives are worse, and
"best of a bad set" is the wrong answer when the obstacle is one the walker
cannot cross. `select_routes()` therefore applies hard limits (`MAX_STAIRS`,
`MAX_SLOPE`, tightened further by limited ability) and withholds anything over
them, returning the reason alongside the offers. `POST /route/select` refuses a
withheld route with a 409, so a client cannot select one by asking for it by
name.

Worked example from the running API, same start point:

```
unrestricted walker      offered: out_and_back, large_loop, small_loop
severe limitation,       offered: out_and_back
limited walking         withheld: small_loop, large_loop
                                  "includes steps beyond what you told us you can manage"
```

**Unknown is not the same as bad.** A provider that cannot measure steps fills
in a neutral placeholder and declares the attribute in `unknown_attributes`.
The gate skips those, because excluding routes on the strength of an invented
number would quietly hide most of the map wherever the data is thin. ORS marks
every one of its surface attributes this way today. Explanations do not claim
unmeasured terrain is accessible, and the UI labels unknown steps as
**Steps unverified**, rather than inventing a percentage of step-free distance.
Neutral placeholders still contribute to the numeric score, so it is not an
accessibility certification. A profile updated to report an activity as
unavailable also blocks routes for older recommendations of that activity.

### Live tracking

**Start activity** on a selected session switches the map to follow mode: it
watches position continuously (`watchPosition`, not a one-off fix), moves a dot
as the user moves, draws a dashed red breadcrumb of where they have actually
been, and shows elapsed time, distance moved, pace and progress against the
recommended duration. Panning the map by hand releases follow mode until
**Recentre on me** is pressed.

Starting an activity opens the map automatically. The mobile journey view keeps
tracking and completion controls directly below the map. Live map following is
GPS tracking; turn-by-turn instructions and automatic rerouting are not yet implemented.

Two details worth knowing:

- **Tracked positions never leave the browser.** The API is told which route was
  selected and, later, that the session finished — not a stream of where the
  user is. A continuous location history is not needed to compute progress, so
  the product does not collect one.
- **A web page cannot track in the background.** The Screen Wake Lock API keeps
  the display awake while the page is visible, but locking the phone or
  switching apps suspends updates. Genuine background tracking needs a native
  app; this is a demo of the flow, not a field-logging tool.

Fixes reported as worse than 50 m accurate are drawn but not counted towards
distance, and steps under 1 m or over 200 m are discarded, so GPS jitter while
standing still does not invent a kilometre.

### Handing a route to the phone's map app

A selected session can leave the app two ways:

- **Open in Maps** — Apple Maps on iOS, Google Maps elsewhere. Neither app can
  be handed a path to follow; they route between points of their own choosing.
  Google accepts up to 9 intermediate waypoints, so a loop survives roughly
  intact. Apple's URL scheme takes only an origin and a destination, so the
  handoff aims at the far side of the loop and the return leg is the walker's.
  Apple Maps also has no cycling-directions flag, so a cycle route hands over as
  a walking one. The UI states which approximation you are getting.
- **Download GPX** (`GET /route/sessions/{id}/gpx`, owner-scoped) — the exact
  route, readable by OsmAnd, Komoot, Strava, Garmin and any GIS tool. This is
  the export to use for dissertation analysis.

### Mobile layout

The app always uses a single-column mobile flow, capped at 430px on larger
screens and filling smaller handsets. Map and route options are separate views.
Controls have generous touch targets, inputs
use 16px type to avoid iOS focus zoom, and keyboard focus is visible. Motion
respects the system's reduced-motion preference.

Changing the location, profile, or recommendation clears old route offers;
late responses for previous inputs cannot bring them back. While an activity
is selected, finish or abandon it before changing the start or selecting another.

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

For the existing local SQLite demo, after installing backend dependencies and
configuring `backend/.env`, run this from the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File backend/start-demo.ps1
```

It preserves `backend/demo.db` and starts the API with automatic reload, so
Python API changes stay in sync with the HTML. Stop an existing server on port
8000 first, or pass `-Port 8001`. The PostgreSQL setup follows below.

Requires Python 3.12 and Docker Desktop (or Docker Compose) for PostgreSQL/PostGIS.
Run these commands from the repository root in PowerShell:

```powershell
py -3.12 -m venv backend/.venv
backend/.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
# First setup only: preserve backend/.env if you already have one.
if (!(Test-Path backend/.env)) { Copy-Item .env.example backend/.env }
backend/.venv/Scripts/python.exe -c "import secrets; print(secrets.token_urlsafe(64))"
backend/.venv/Scripts/python.exe -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())"
```

Put the first generated value in `JWT_SECRET` and the second in
`FIELD_ENCRYPTION_KEY` in `backend/.env`. Keep that encryption key stable:
existing encrypted health profiles require the same key to be read.
Leave `ROUTE_PROVIDER=mock` for synthetic routes without an external API key.

To run the whole stack in Docker:

```powershell
docker compose up --build
```

Compose waits for the database, applies migrations, and starts the API.
Open [the demo](http://localhost:8000/demo),
[API documentation](http://localhost:8000/docs), or
[the health endpoint](http://localhost:8000/health).

Alternatively, run only the database in Docker and the API locally:

```powershell
docker compose up -d db
cd backend
.venv/Scripts/python.exe -m alembic upgrade head
.venv/Scripts/python.exe -m uvicorn app.main:app --reload
```

On macOS/Linux, use `python3.12` instead of `py -3.12` and `.venv/bin/python`
instead of `.venv/Scripts/python.exe`; copy the example with `cp` on first setup.

### Tests

From `backend`, run:

```powershell
.venv/Scripts/python.exe -m pytest -q
```

The suite uses a disposable SQLite database and mocked routing, so it needs
neither Docker nor an ORS key. It does not exercise PostgreSQL/PostGIS or the
live routing service. `TEST_DATABASE_URL` currently does not switch the test
database; the fixture explicitly creates SQLite.

Client state and request-handling regression checks use Node's built-in test runner:

```powershell
# From the repository root, with Node installed:
node --test backend/tests/demo_client.test.cjs
```

These checks use a small DOM stub; they do not verify rendered layout,
Leaflet interactions, or real-device geolocation.

### Authentication behavior

Logout revokes the refresh token and removes its browser cookie. Invalid or
replayed refresh tokens also clear that cookie; reuse revokes the token family.
Already-issued access tokens remain valid until their expiry (15 minutes by
default). Clients should discard their in-memory access token on logout.
