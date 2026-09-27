"""activity_recommendations table

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-09

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

activity_type_enum = postgresql.ENUM("walk", "cycle", name="activity_type_enum", create_type=False)


def upgrade() -> None:
    activity_type_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "activity_recommendations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("activity_type", activity_type_enum, nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_activity_recommendations_user_id", "activity_recommendations", ["user_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_activity_recommendations_user_id", table_name="activity_recommendations")
    op.drop_table("activity_recommendations")
    activity_type_enum.drop(op.get_bind(), checkfirst=True)
