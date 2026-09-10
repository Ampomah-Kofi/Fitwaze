"""Progress summary endpoint (filled in during the route selection/progress step)."""
from fastapi import APIRouter

router = APIRouter(prefix="/progress", tags=["progress"])
