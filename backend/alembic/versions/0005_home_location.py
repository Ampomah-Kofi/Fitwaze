"""health_profiles.home_latitude / home_longitude

Where the person lives, so routes start and end at home. Stored as Text
because the values are AES-256-GCM ciphertext (see EncryptedString).

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("health_profiles", sa.Column("home_latitude", sa.Text(), nullable=True))
    op.add_column("health_profiles", sa.Column("home_longitude", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("health_profiles", "home_longitude")
    op.drop_column("health_profiles", "home_latitude")
