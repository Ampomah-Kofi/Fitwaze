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

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.engines.route_engine import get_route_provider
from app.engines.route_engine.scoring import score_and_rank_routes
from app.models.activity import ActivityRecommendation
from app.models.user import User
from app.schemas.route import RouteOptionSchema, RouteOptionsRequest, RouteOptionsResponse
from app.security.deps import get_current_user

router = APIRouter(prefix="/route", tags=["route"])
logger = logging.getLogger(__name__)


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
