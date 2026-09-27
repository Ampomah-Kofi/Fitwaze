"""Blood sugar before the walk, from the morning check-in (encrypted)

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Text: the application encrypts the reading before it is stored.
    op.add_column("activity_recommendations", sa.Column("pre_glucose_mmol_l", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("activity_recommendations", "pre_glucose_mmol_l")
