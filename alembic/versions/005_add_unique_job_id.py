"""Add unique constraint on job_id

Revision ID: 005_add_unique_job_id
Revises: 004_fix_synthesis_duration_null
Create Date: 2026-09-03 20:30:00.000000

This migration cleans up any duplicate records on tts_jobs.job_id (keeping the
earliest record) and adds a UNIQUE constraint to ensure idempotency across worker
restarts and message redelivery.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "005_add_unique_job_id"
down_revision: Union[str, None] = "004_fix_synthesis_duration_null"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Clean up existing duplicates and add UNIQUE constraint on job_id."""
    # Clean up any existing duplicates, preserving the earliest created record
    op.execute(
        sa.text(
            """
            DELETE FROM tts_jobs
            WHERE id NOT IN (
                SELECT MIN(id)
                FROM tts_jobs
                GROUP BY job_id
            )
            """
        )
    )

    # Add unique constraint on job_id
    op.create_unique_constraint(
        "uq_tts_jobs_job_id",
        "tts_jobs",
        ["job_id"],
    )


def downgrade() -> None:
    """Drop the UNIQUE constraint on job_id."""
    op.drop_constraint("uq_tts_jobs_job_id", "tts_jobs", type_="unique")
