"""SQLAlchemy models package.

Importing this package registers all mapped models on `Base.metadata`, which
is required for `Base.metadata.create_all()` (used by the test suite) and for
Alembic's env.py to see the full schema.
"""
from app.models.user import User, RefreshToken  # noqa: F401
from app.models.profile import HealthProfile  # noqa: F401

__all__ = [
    "User",
    "RefreshToken",
    "HealthProfile",
]
