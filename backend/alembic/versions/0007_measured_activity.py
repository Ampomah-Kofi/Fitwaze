"""Measured activity: GPS minutes and distance recorded on completion

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-27
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("activity_sessions", sa.Column("measured_minutes", sa.Float(), nullable=True))
    op.add_column("activity_sessions", sa.Column("measured_distance_m", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("activity_sessions", "measured_distance_m")
    op.drop_column("activity_sessions", "measured_minutes")
