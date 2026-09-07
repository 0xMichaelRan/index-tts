"""Add priority field to tts_jobs

Revision ID: 009_add_priority_field
Revises: 008_add_word_count
Create Date: 2026-09-07 04:47:00.000000

Adds job priority tracking to tts_jobs:
- priority: INTEGER NOT NULL DEFAULT 5 (0=lowest, 10=highest, 5=normal)

Priority reflects the AMQP message priority (x-max-priority=10 queues)
set by the producer (studio-backend) or JSON field fallback.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "009_add_priority_field"
down_revision: Union[str, None] = "008_add_word_count"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add priority column to tts_jobs."""
    op.add_column(
        "tts_jobs",
        sa.Column(
            "priority",
            sa.Integer(),
            nullable=False,
            server_default="5",
            comment="Job priority (0=lowest, 10=highest, default=5=normal)",
        ),
    )
    op.create_index("idx_tts_jobs_priority", "tts_jobs", ["priority"])


def downgrade() -> None:
    """Drop priority column from tts_jobs."""
    op.drop_index("idx_tts_jobs_priority", table_name="tts_jobs")
    op.drop_column("tts_jobs", "priority")
