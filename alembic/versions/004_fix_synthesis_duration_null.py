"""Fix NULL synthesis_duration_seconds values

Revision ID: 004_fix_synthesis_duration_null
Revises: 003
Create Date: 2026-09-03 13:00:00.000000

This migration sets a default value of 0.0 for all existing NULL values in
synthesis_duration_seconds to match existing behavior where this metric
was not being tracked.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "004_fix_synthesis_duration_null"
down_revision: Union[str, None] = "003_add_is_test_column"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Set default value for NULL synthesis_duration_seconds."""
    # Update existing NULL values to 0.0
    op.execute(
        sa.text(
            "UPDATE tts_jobs SET synthesis_duration_seconds = 0.0 WHERE synthesis_duration_seconds IS NULL"
        )
    )
    
    # Change column to NOT NULL with a default
    op.alter_column(
        "tts_jobs",
        "synthesis_duration_seconds",
        existing_type=sa.Numeric(precision=10, scale=2),
        nullable=False,
        server_default="0.0",
    )


def downgrade() -> None:
    """Revert the column back to nullable."""
    op.alter_column(
        "tts_jobs",
        "synthesis_duration_seconds",
        existing_type=sa.Numeric(precision=10, scale=2),
        nullable=True,
        server_default=None,
    )
