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
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.engines.route_engine import get_route_provider
from app.engines.route_engine.base import RawRoute
from app.engines.route_engine.scoring import RouteScoreResult, score_and_rank_routes
from app.models.activity import ActivityRecommendation
from app.models.enums import SessionStatusEnum
from app.models.session import ActivitySession
from app.models.user import User
from app.schemas.route import (
    RouteOptionSchema,
    RouteOptionsRequest,
    RouteOptionsResponse,
    RouteSelectRequest,
    RouteSessionResponse,
    RouteSessionUpdateRequest,
)
from app.security.deps import get_current_user

router = APIRouter(prefix="/route", tags=["route"])
logger = logging.getLogger(__name__)

# Persisted route geometry is rounded to 4 decimal places (~11 m). That is
# precise enough to redraw the route on a map, while deliberately storing a
# coarser record of where a user actually goes than the provider returns.
_STORED_COORD_PRECISION = 4


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

    provider = get_route_provider()
    raw_routes = provider.get_candidate_routes(
        start_lat=payload.latitude,
        start_lon=payload.longitude,
        activity_type=recommendation.activity_type,
        target_duration_min=recommendation.duration_minutes,
    )

    ranked = score_and_rank_routes(
        raw_routes, recommendation.activity_type, recommendation.duration_minutes
    )

    options = [
        RouteOptionSchema(
            label=route.label,
            distance_m=route.distance_m,
            estimated_minutes=route.estimated_minutes,
            score=result.score,
            score_breakdown=result.breakdown,
            explanation=result.explanation,
            geometry=route.geometry,
        )
        for route, result in ranked
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
        options=options,
    )


def _get_own_session(db: Session, session_id: uuid.UUID, user_id) -> ActivitySession:
    session_row = db.get(ActivitySession, session_id)
    if session_row is None or session_row.user_id != user_id:
        # 404 for someone else's session id, for the same reason as above.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return session_row


def _candidates_for(
    payload: RouteSelectRequest, recommendation: ActivityRecommendation
) -> list[tuple[RawRoute, RouteScoreResult]]:
    provider = get_route_provider()
    raw_routes = provider.get_candidate_routes(
        start_lat=payload.latitude,
        start_lon=payload.longitude,
        activity_type=recommendation.activity_type,
        target_duration_min=recommendation.duration_minutes,
    )
    return score_and_rank_routes(
        raw_routes, recommendation.activity_type, recommendation.duration_minutes
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

    chosen = next(
        (
            (route, result)
            for route, result in _candidates_for(payload, recommendation)
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


@router.get("/sessions/{session_id}", response_model=RouteSessionResponse)
def get_route_session(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RouteSessionResponse:
    return RouteSessionResponse.model_validate(
        _get_own_session(db, session_id, current_user.id)
    )


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
    if new_status == SessionStatusEnum.completed:
        session_row.completed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(session_row)

    logger.info(
        "route_session_updated user_id=%s session_id=%s status=%s",
        current_user.id,
        session_row.id,
        new_status.value,
    )

    return RouteSessionResponse.model_validate(session_row)
