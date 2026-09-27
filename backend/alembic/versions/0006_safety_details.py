"""Safety details: glucose-lowering medication, emergency contact, after-walk check-in

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Text columns hold AES-256-GCM ciphertext (see EncryptedString).
    op.add_column("health_profiles", sa.Column("takes_glucose_lowering_medication", sa.Text(), nullable=True))
    op.add_column("health_profiles", sa.Column("emergency_contact_name", sa.Text(), nullable=True))
    op.add_column("health_profiles", sa.Column("emergency_contact_phone", sa.Text(), nullable=True))
    op.add_column("activity_sessions", sa.Column("effort", sa.String(length=20), nullable=True))
    op.add_column("activity_sessions", sa.Column("post_glucose_mmol_l", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("activity_sessions", "post_glucose_mmol_l")
    op.drop_column("activity_sessions", "effort")
    op.drop_column("health_profiles", "emergency_contact_phone")
    op.drop_column("health_profiles", "emergency_contact_name")
    op.drop_column("health_profiles", "takes_glucose_lowering_medication")
