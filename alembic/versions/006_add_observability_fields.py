"""Add observability fields to tts_jobs

Revision ID: 006_add_observability_fields
Revises: 005_add_unique_job_id
Create Date: 2026-09-03 20:31:00.000000

This migration adds dedicated observability fields to tts_jobs:
- cache_hit: Direct tracking of synthesis cache hits
- alignment_duration_seconds: Timing breakdown for forced alignment
- time_stretched: Flag indicating if audio was stretched
- source_ratio and target_ratio: Tracking playback speed adjustments
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "006_add_observability_fields"
down_revision: Union[str, None] = "005_add_unique_job_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add observability columns to tts_jobs."""
    op.add_column(
        "tts_jobs",
        sa.Column(
            "cache_hit",
            sa.Boolean(),
            nullable=False,
            server_default="false",
            comment="Whether synthesis result came from cache",
        ),
    )
    op.add_column(
        "tts_jobs",
        sa.Column(
            "alignment_duration_seconds",
            sa.Numeric(precision=10, scale=2),
            nullable=True,
            comment="Time spent on forced alignment (seconds)",
        ),
    )
    op.add_column(
        "tts_jobs",
        sa.Column(
            "time_stretched",
            sa.Boolean(),
            nullable=False,
            server_default="false",
            comment="Whether audio was time-stretched (ratio != 1.0)",
        ),
    )
    op.add_column(
        "tts_jobs",
        sa.Column(
            "source_ratio",
            sa.Numeric(precision=3, scale=1),
            nullable=True,
            comment="Original synthesis ratio (always 1.0 for cache)",
        ),
    )
    op.add_column(
        "tts_jobs",
        sa.Column(
            "target_ratio",
            sa.Numeric(precision=3, scale=1),
            nullable=True,
            comment="Target playback speed ratio requested",
        ),
    )


def downgrade() -> None:
    """Drop observability columns from tts_jobs."""
    op.drop_column("tts_jobs", "target_ratio")
    op.drop_column("tts_jobs", "source_ratio")
    op.drop_column("tts_jobs", "time_stretched")
    op.drop_column("tts_jobs", "alignment_duration_seconds")
    op.drop_column("tts_jobs", "cache_hit")
