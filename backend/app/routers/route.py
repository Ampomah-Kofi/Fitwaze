"""Route generation/selection endpoints (filled in during the route engine step)."""
from fastapi import APIRouter

router = APIRouter(prefix="/route", tags=["route"])
