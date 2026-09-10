"""activity_sessions table

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

session_status_enum = postgresql.ENUM(
    "offered", "selected", "completed", "abandoned", name="session_status_enum"
)


def upgrade() -> None:
    session_status_enum.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "activity_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "activity_recommendation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("activity_recommendations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("distance_m", sa.Float(), nullable=False),
        sa.Column("estimated_minutes", sa.Integer(), nullable=False),
        sa.Column("score_breakdown", postgresql.JSONB(), nullable=False),
        sa.Column("status", session_status_enum, nullable=False),
        sa.Column("route_geometry", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_activity_sessions_user_id", "activity_sessions", ["user_id"])
    op.create_index(
        "ix_activity_sessions_activity_recommendation_id",
        "activity_sessions",
        ["activity_recommendation_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_activity_sessions_activity_recommendation_id", table_name="activity_sessions"
    )
    op.drop_index("ix_activity_sessions_user_id", table_name="activity_sessions")
    op.drop_table("activity_sessions")
    session_status_enum.drop(op.get_bind(), checkfirst=True)