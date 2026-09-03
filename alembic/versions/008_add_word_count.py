"""Add word_count for TTS synthesize time estimation

Revision ID: 008_add_word_count
Revises: 007_add_cache_foreign_key
Create Date: 2026-09-04 04:30:00.000000

Adds word_count to tts_jobs and tts_synthesis_cache so synthesis duration
can be correlated with text length (CJK chars + Latin tokens).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "008_add_word_count"
down_revision: Union[str, None] = "007_add_cache_foreign_key"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add word_count columns for duration estimation analytics."""
    op.add_column(
        "tts_jobs",
        sa.Column(
            "word_count",
            sa.Integer(),
            nullable=True,
            comment="Speaking units: CJK chars + Latin tokens (for duration estimation)",
        ),
    )
    op.create_index("idx_tts_jobs_word_count", "tts_jobs", ["word_count"])

    op.add_column(
        "tts_synthesis_cache",
        sa.Column(
            "word_count",
            sa.Integer(),
            nullable=True,
            comment="Speaking units: CJK chars + Latin tokens (for duration estimation)",
        ),
    )


def downgrade() -> None:
    """Drop word_count columns."""
    op.drop_column("tts_synthesis_cache", "word_count")
    op.drop_index("idx_tts_jobs_word_count", table_name="tts_jobs")
    op.drop_column("tts_jobs", "word_count")
