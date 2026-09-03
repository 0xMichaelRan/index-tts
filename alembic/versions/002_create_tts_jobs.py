"""Create tts_jobs table

Revision ID: 002_create_tts_jobs
Revises: 001
Create Date: 2026-09-03 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "002_create_tts_jobs"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create tts_jobs table with indexes and constraints."""
    op.create_table(
        "tts_jobs",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("job_type", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
        # TTS parameters (for debugging/analytics)
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("audio_prompt_path", sa.Text(), nullable=True),
        sa.Column("language", sa.String(length=10), nullable=True),
        sa.Column("ratio", sa.Numeric(precision=3, scale=1), nullable=True),
        # Results
        sa.Column("cache_key", sa.String(length=64), nullable=True),
        sa.Column("audio_path", sa.Text(), nullable=True),
        sa.Column("alignment_path", sa.Text(), nullable=True),
        sa.Column("audio_duration_seconds", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("synthesis_duration_seconds", sa.Numeric(precision=10, scale=2), nullable=True),
        # Error tracking
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        # Timestamps
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'completed', 'failed')",
            name="ck_tts_jobs_status",
        ),
        sa.CheckConstraint(
            "job_type IN ('studio', 'playground', 'rem')",
            name="ck_tts_jobs_job_type",
        ),
    )

    # Indexes
    op.create_index("idx_tts_jobs_job_id", "tts_jobs", ["job_id"])
    op.create_index("idx_tts_jobs_status", "tts_jobs", ["status"])
    op.create_index("idx_tts_jobs_created_at", "tts_jobs", ["created_at"])
    op.create_index("idx_tts_jobs_cache_key", "tts_jobs", ["cache_key"])


def downgrade() -> None:
    """Drop tts_jobs table and indexes."""
    op.drop_index("idx_tts_jobs_cache_key", table_name="tts_jobs")
    op.drop_index("idx_tts_jobs_created_at", table_name="tts_jobs")
    op.drop_index("idx_tts_jobs_status", table_name="tts_jobs")
    op.drop_index("idx_tts_jobs_job_id", table_name="tts_jobs")
    op.drop_table("tts_jobs")
