"""Authentication endpoints (filled in during the auth vertical-slice step)."""
from fastapi import APIRouter

router = APIRouter(prefix="/auth", tags=["auth"])
