"""Route generation endpoint.

POST /route/options generates and scores 2-3 candidate routes for one of the
CALLING user's own activity recommendations (ownership is checked — a
client can never request options against another user's recommendation id).
Nothing is persisted here by design (data minimization): candidates are
computed on the fly and only ever written to the database once one is
selected via POST /route/select (added alongside activity_sessions).
"""
from __future__ import annotations

import logging
import uuid
import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.engines.activity_engine import after_walk_advice
from app.services.weather import heat_check
from app.engines.route_engine import get_route_provider
from app.engines.route_engine.base import RawRoute, RouteProviderError, _metres_between
from app.engines.route_engine.scoring import RouteSelection
from app.engines.route_engine.cache import get_candidate_routes_cached
from app.engines.route_engine.scoring import RouteScoreResult, feasibility_problem, select_routes
from app.models.activity import ActivityRecommendation
from app.models.enums import SessionStatusEnum
from app.models.profile import HealthProfile, profile_to_data
from app.models.session import ActivitySession
from app.models.user import User
from app.schemas.route import (
    AfterWalkCheckIn,
    ExcludedRouteSchema,
    PastRouteSchema,
    PastRoutesRequest,
    RouteRepeatRequest,
    RouteOptionSchema,
    RouteOptionsRequest,
    RouteOptionsResponse,
    RouteSelectRequest,
    RouteSessionResponse,
    RouteSessionUpdateRequest,
    WeatherSchema,
)
from app.security.deps import get_current_user

router = APIRouter(prefix="/route", tags=["route"])
logger = logging.getLogger(__name__)

# Persisted route geometry is rounded to 4 decimal places (~11 m). That is
# precise enough to redraw the route on a map, while deliberately storing a
# coarser record of where a user actually goes than the provider returns.
_STORED_COORD_PRECISION = 4

# Measured surroundings a provider may report, passed through to the client.
_ENVIRONMENT_FACTS = ("ascent_m", "green_pct", "paths_pct", "busy_road_pct", "steps_pct",
                      "sidewalk_pct", "bike_lane_pct", "lit_pct")


def _candidate_revision(route: RawRoute) -> str:
    data = asdict(route)
    data["unknown_attributes"] = sorted(route.unknown_attributes)
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _load_candidates(**kwargs) -> list[RawRoute]:
    try:
        return get_candidate_routes_cached(get_route_provider(), **kwargs)
    except RouteProviderError as exc:
        raise HTTPException(status_code=503, detail="Routing is temporarily unavailable for this start point. Try another location or try again later.") from exc


def _get_own_recommendation(
    db: Session, recommendation_id, user_id
) -> ActivityRecommendation:
    recommendation = db.get(ActivityRecommendation, recommendation_id)
    if recommendation is None or recommendation.user_id != user_id:
        # 404 (not 403) so we don't confirm to a caller whether a given id
        # exists at all when it belongs to someone else.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recommendation not found")
    return recommendation


@router.post("/options", response_model=RouteOptionsResponse)
def get_route_options(
    payload: RouteOptionsRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RouteOptionsResponse:
    recommendation = _get_own_recommendation(
        db, payload.activity_recommendation_id, current_user.id
    )

    raw_routes = _load_candidates(
        start_lat=payload.latitude,
        start_lon=payload.longitude,
        activity_type=recommendation.activity_type,
        target_duration_min=recommendation.duration_minutes,
    )

    selection = select_routes(
        raw_routes,
        recommendation.activity_type,
        recommendation.duration_minutes,
        profile=_profile_for(db, current_user.id),
    )

    options = [
        RouteOptionSchema(
            candidate_revision=_candidate_revision(route),
            label=route.label,
            distance_m=route.distance_m,
            estimated_minutes=route.estimated_minutes,
            score=result.score,
            score_breakdown=result.breakdown,
            explanation=result.explanation,
            unverified=sorted(route.unknown_attributes),
            environment={
                key: value for key, value in route.raw_attributes.items()
                if key in _ENVIRONMENT_FACTS and isinstance(value, (int, float))
            },
            geometry=route.geometry,
        )
        for route, result in selection.ranked
    ]

    logger.info(
        "route_options_generated user_id=%s activity_recommendation_id=%s count=%d",
        current_user.id,
        recommendation.id,
        len(options),
    )

    return RouteOptionsResponse(
        activity_recommendation_id=recommendation.id,
        activity_type=recommendation.activity_type.value,
        target_duration_minutes=recommendation.duration_minutes,
        provider=get_settings().route_provider.lower(),
        options=options,
        excluded=[
            ExcludedRouteSchema(label=item.label, reason=item.reason)
            for item in selection.excluded
        ],
        weather=WeatherSchema(**asdict(weather)) if (weather := heat_check(payload.latitude, payload.longitude)) else None,
    )


def _get_own_session(db: Session, session_id: uuid.UUID, user_id) -> ActivitySession:
    session_row = db.get(ActivitySession, session_id)
    if session_row is None or session_row.user_id != user_id:
        # 404 for someone else's session id, for the same reason as above.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return session_row


def _profile_for(db: Session, user_id):
    """The caller's own health profile, used to personalise route scoring.

    Scoring falls back to the generic weights when there is no profile row,
    rather than failing: a recommendation cannot exist without a profile, but
    the engine should not depend on that invariant holding.
    """
    profile = db.get(HealthProfile, user_id)
    return profile_to_data(profile) if profile is not None else None


def _candidates_for(
    payload: RouteSelectRequest,
    recommendation: ActivityRecommendation,
    profile,
) -> "RouteSelection":
    raw_routes = _load_candidates(
        start_lat=payload.latitude,
        start_lon=payload.longitude,
        activity_type=recommendation.activity_type,
        target_duration_min=recommendation.duration_minutes,
    )
    return select_routes(
        raw_routes,
        recommendation.activity_type,
        recommendation.duration_minutes,
        profile=profile,
    )


@router.post("/select", response_model=RouteSessionResponse, status_code=status.HTTP_201_CREATED)
def select_route(
    payload: RouteSelectRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RouteSessionResponse:
    """Persist the candidate the user picked as an activity session.

    The candidates are regenerated from the same (recommendation, start point)
    inputs rather than being taken from the request, so the stored distance,
    geometry and score breakdown are always the engine's own output.
    """
    recommendation = _get_own_recommendation(
        db, payload.activity_recommendation_id, current_user.id
    )

    profile = _profile_for(db, current_user.id)
    selection = _candidates_for(payload, recommendation, profile)

    # A route the gate withheld must not become selectable just because a
    # client asked for it by name: refuse it, and say why.
    withheld = next(
        (item for item in selection.excluded if item.label == payload.candidate_label), None
    )
    if withheld is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"That route {withheld.reason}, so it is not offered to you",
        )

    chosen = next(
        (
            (route, result)
            for route, result in selection.ranked
            if route.label == payload.candidate_label
        ),
        None,
    )
    if chosen is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That route option is not available for this recommendation and start point",
        )
    route, result = chosen
    if payload.candidate_revision is not None and payload.candidate_revision != _candidate_revision(route):
        raise HTTPException(status_code=409, detail="This route option has changed. Find routes again before selecting it.")

    session_row = ActivitySession(
        user_id=current_user.id,
        activity_recommendation_id=recommendation.id,
        distance_m=route.distance_m,
        estimated_minutes=round(route.estimated_minutes),
        score_breakdown=result.breakdown,
        status=SessionStatusEnum.selected,
        route_geometry=[
            [round(lat, _STORED_COORD_PRECISION), round(lon, _STORED_COORD_PRECISION)]
            for lat, lon in route.geometry
        ],
    )
    db.add(session_row)
    db.commit()
    db.refresh(session_row)

    logger.info(
        "route_session_selected user_id=%s session_id=%s label=%s",
        current_user.id,
        session_row.id,
        route.label,
    )

    return RouteSessionResponse.model_validate(session_row)


# --- Walking a route again ------------------------------------------------------
# Past routes are offered only near where the person is starting now, and
# only if they fit in today's plan: if the check-in shortened today (tired,
# high blood sugar), a longer favourite is not a shortcut around that.
PAST_ROUTE_START_RADIUS_M = 300.0
PAST_ROUTE_MINUTES_SLACK = 1.25
PAST_ROUTE_LIMIT = 3
PAST_ROUTE_SCAN = 50


def _fits_today(session_row: ActivitySession, recommendation: ActivityRecommendation) -> bool:
    return session_row.estimated_minutes <= recommendation.duration_minutes * PAST_ROUTE_MINUTES_SLACK


def _same_route_key(session_row: ActivitySession) -> tuple:
    # The same loop walked twice has the same stored geometry.
    geometry = session_row.route_geometry or []
    return (round(session_row.distance_m / 25), len(geometry), tuple(geometry[len(geometry) // 2]) if geometry else None)


@router.post("/past", response_model=list[PastRouteSchema])
def past_routes(
    payload: PastRoutesRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[PastRouteSchema]:
    """Routes the caller finished before, starting near this start point,
    that fit today's plan: newest first, each listed once."""
    recommendation = _get_own_recommendation(db, payload.activity_recommendation_id, current_user.id)
    rows = db.scalars(
        select(ActivitySession)
        .join(ActivityRecommendation, ActivitySession.activity_recommendation_id == ActivityRecommendation.id)
        .where(ActivitySession.user_id == current_user.id)
        .where(ActivitySession.status == SessionStatusEnum.completed)
        .where(ActivityRecommendation.activity_type == recommendation.activity_type)
        .order_by(ActivitySession.completed_at.desc())
        .limit(PAST_ROUTE_SCAN)
    )
    start = (payload.latitude, payload.longitude)
    found: dict[tuple, PastRouteSchema] = {}
    for row in rows:
        geometry = row.route_geometry or []
        if not geometry or _metres_between(start, tuple(geometry[0])) > PAST_ROUTE_START_RADIUS_M:
            continue
        key = _same_route_key(row)
        if key in found:
            found[key].times_done += 1
            continue
        if not _fits_today(row, recommendation) or len(found) >= PAST_ROUTE_LIMIT:
            continue
        found[key] = PastRouteSchema(
            session_id=row.id, distance_m=row.distance_m, estimated_minutes=row.estimated_minutes,
            completed_at=row.completed_at, times_done=1, effort=row.effort,
            geometry=[(lat, lon) for lat, lon in geometry],
        )
    return list(found.values())


@router.post("/repeat", response_model=RouteSessionResponse, status_code=status.HTTP_201_CREATED)
def repeat_route(
    payload: RouteRepeatRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RouteSessionResponse:
    """Start a route the caller finished before, under today's plan."""
    recommendation = _get_own_recommendation(db, payload.activity_recommendation_id, current_user.id)
    past = _get_own_session(db, payload.session_id, current_user.id)
    if past.status != SessionStatusEnum.completed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only a route you finished can be walked again")
    if past.activity_recommendation.activity_type != recommendation.activity_type:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="That route was for a different activity than today's plan")
    if not _fits_today(past, recommendation):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="That route is longer than today's plan. Choose a shorter one today.")
    session_row = ActivitySession(
        user_id=current_user.id,
        activity_recommendation_id=recommendation.id,
        distance_m=past.distance_m,
        estimated_minutes=past.estimated_minutes,
        score_breakdown=past.score_breakdown,
        status=SessionStatusEnum.selected,
        route_geometry=past.route_geometry,
    )
    db.add(session_row)
    db.commit()
    db.refresh(session_row)
    logger.info("route_session_repeated user_id=%s session_id=%s from=%s", current_user.id, session_row.id, past.id)
    return RouteSessionResponse.model_validate(session_row)


@router.get("/sessions/{session_id}", response_model=RouteSessionResponse)
def get_route_session(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RouteSessionResponse:
    return RouteSessionResponse.model_validate(
        _get_own_session(db, session_id, current_user.id)
    )


def _record_checkin(db: Session, session_row: ActivitySession, checkin: AfterWalkCheckIn, user_id) -> str:
    session_row.effort = checkin.effort
    glucose = checkin.post_glucose_mmol_l
    session_row.post_glucose_mmol_l = f"{glucose:.2f}" if glucose is not None else None
    profile = _profile_for(db, user_id)
    return after_walk_advice(glucose, checkin.effort, bool(profile and profile.takes_glucose_lowering_medication))


@router.patch("/sessions/{session_id}", response_model=RouteSessionResponse)
def update_route_session(
    session_id: uuid.UUID,
    payload: RouteSessionUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RouteSessionResponse:
    """Mark a session completed or abandoned. Terminal states are final, so a
    replayed or duplicated request cannot inflate a user's progress totals."""
    session_row = _get_own_session(db, session_id, current_user.id)

    if session_row.status in (SessionStatusEnum.completed, SessionStatusEnum.abandoned):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session is already {session_row.status.value}",
        )

    new_status = SessionStatusEnum(payload.status)
    session_row.status = new_status
    advice = None
    if new_status == SessionStatusEnum.completed:
        session_row.completed_at = datetime.now(timezone.utc)
        if payload.measured_minutes is not None:
            session_row.measured_minutes = round(payload.measured_minutes, 1)
            session_row.measured_distance_m = (
                round(payload.measured_distance_m, 1) if payload.measured_distance_m is not None else None
            )
        if payload.effort is not None or payload.post_glucose_value is not None:
            advice = _record_checkin(db, session_row, payload, current_user.id)
    db.commit()
    db.refresh(session_row)

    logger.info(
        "route_session_updated user_id=%s session_id=%s status=%s",
        current_user.id,
        session_row.id,
        new_status.value,
    )

    response = RouteSessionResponse.model_validate(session_row)
    response.after_walk_advice = advice
    return response


def _session_to_gpx(session_row: ActivitySession, activity_type: str) -> str:
    """Render a stored session as a GPX 1.1 track.

    GPX is what walking/cycling apps read (OsmAnd, Komoot, Strava, Garmin) and
    what GIS tooling expects, so it is the export that preserves the route
    exactly. Handing a route to Apple or Google Maps can only approximate it:
    those apps navigate between points and cannot be given a path to follow.
    """
    name = escape(f"FitWaze {activity_type} - {session_row.estimated_minutes} min")

    created = session_row.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    created_utc = created.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    points = [
        f'      <trkpt lat="{lat:.6f}" lon="{lon:.6f}" />'
        for lat, lon in session_row.route_geometry
    ]

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="FitWaze" xmlns="http://www.topografix.com/GPX/1/1">',
        "  <metadata>",
        f"    <name>{name}</name>",
        f"    <time>{created_utc}</time>",
        "  </metadata>",
        "  <trk>",
        f"    <name>{name}</name>",
        "    <trkseg>",
        *points,
        "    </trkseg>",
        "  </trk>",
        "</gpx>",
        "",
    ]
    return "\n".join(lines)


@router.get("/sessions/{session_id}/gpx")
def download_route_gpx(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Export one of the caller's own sessions as a GPX track file."""
    session_row = _get_own_session(db, session_id, current_user.id)
    activity_type = session_row.activity_recommendation.activity_type.value

    return Response(
        content=_session_to_gpx(session_row, activity_type),
        media_type="application/gpx+xml",
        headers={
            "Content-Disposition": f'attachment; filename="fitwaze-{session_row.id}.gpx"'
        },
    )


@router.post("/sessions/{session_id}/checkin", response_model=RouteSessionResponse)
def add_after_walk_checkin(
    session_id: uuid.UUID,
    payload: AfterWalkCheckIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RouteSessionResponse:
    """Add how it felt and blood sugar afterwards to a walk already saved as
    completed. The walk is recorded the moment it is finished; this optional
    step comes after, once, so the record cannot be rewritten later."""
    session_row = _get_own_session(db, session_id, current_user.id)
    if session_row.status != SessionStatusEnum.completed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only a finished walk can have a check-in")
    if session_row.effort is not None or session_row.post_glucose_mmol_l is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This walk already has a check-in")
    if payload.effort is None and payload.post_glucose_value is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Nothing to add")
    advice = _record_checkin(db, session_row, payload, current_user.id)
    db.commit()
    db.refresh(session_row)
    logger.info("route_session_checkin user_id=%s session_id=%s", current_user.id, session_row.id)
    response = RouteSessionResponse.model_validate(session_row)
    response.after_walk_advice = advice
    return response
