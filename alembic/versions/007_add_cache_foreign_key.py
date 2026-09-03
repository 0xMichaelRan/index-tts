"""Add foreign key constraint on tts_jobs.cache_key

Revision ID: 007_add_cache_foreign_key
Revises: 006_add_observability_fields
Create Date: 2026-09-03 20:32:00.000000

This migration cleans up any orphaned cache_key references in tts_jobs and adds
a FOREIGN KEY constraint referencing tts_synthesis_cache(cache_key) with
ON DELETE SET NULL.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "007_add_cache_foreign_key"
down_revision: Union[str, None] = "006_add_observability_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Clean up orphaned cache_key values and add foreign key constraint."""
    # Clean up orphaned cache_key references prior to adding the constraint
    op.execute(
        sa.text(
            """
            UPDATE tts_jobs
            SET cache_key = NULL
            WHERE cache_key IS NOT NULL
              AND cache_key NOT IN (SELECT cache_key FROM tts_synthesis_cache)
            """
        )
    )

    # Add foreign key constraint
    op.create_foreign_key(
        "fk_tts_jobs_cache_key",
        "tts_jobs",
        "tts_synthesis_cache",
        ["cache_key"],
        ["cache_key"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    """Drop the foreign key constraint."""
    op.drop_constraint("fk_tts_jobs_cache_key", "tts_jobs", type_="foreignkey")
