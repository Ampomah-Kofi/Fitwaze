"""health_profiles table (encrypted sensitive columns)

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-09

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# NOTE: height_cm, weight_kg, diabetes_status, mobility_limitations,
# walking_ability, and cycling_ability are stored as `Text` here because the
# application encrypts them (AES-256-GCM) before they ever reach the
# database — there is no plaintext numeric/enum representation at the SQL
# level to constrain with a CHECK/enum type.

sex_enum = postgresql.ENUM(
    "male", "female", "other", "prefer_not_to_say", name="sex_enum"
)
goal_enum = postgresql.ENUM(
    "general_fitness",
    "weight_management",
    "manage_prediabetes",
    "manage_type2",
    name="goal_enum",
)
preferred_activity_enum = postgresql.ENUM(
    "walk", "cycle", "no_preference", name="preferred_activity_enum"
)


def upgrade() -> None:
    bind = op.get_bind()
    sex_enum.create(bind, checkfirst=True)
    goal_enum.create(bind, checkfirst=True)
    preferred_activity_enum.create(bind, checkfirst=True)

    op.create_table(
        "health_profiles",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("age", sa.Integer(), nullable=False),
        sa.Column("sex", sex_enum, nullable=False),
        sa.Column("goal", goal_enum, nullable=False),
        sa.Column("preferred_activity", preferred_activity_enum, nullable=False),
        sa.Column("current_weekly_minutes", sa.Integer(), nullable=False),
        sa.Column("current_frequency", sa.Integer(), nullable=False),
        sa.Column("height_cm", sa.Text(), nullable=False),
        sa.Column("weight_kg", sa.Text(), nullable=False),
        sa.Column("diabetes_status", sa.Text(), nullable=False),
        sa.Column("mobility_limitations", sa.Text(), nullable=False),
        sa.Column("walking_ability", sa.Text(), nullable=False),
        sa.Column("cycling_ability", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("health_profiles")
    sex_enum.drop(op.get_bind(), checkfirst=True)
    goal_enum.drop(op.get_bind(), checkfirst=True)
    preferred_activity_enum.drop(op.get_bind(), checkfirst=True)
