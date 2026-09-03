"""Add isTest column to tts_jobs table

Revision ID: 003_add_is_test_column
Revises: 002_create_tts_jobs
Create Date: 2026-09-03 14:40:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "003_add_is_test_column"
down_revision: Union[str, None] = "002_create_tts_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add isTest column to distinguish test jobs from production jobs."""
    op.add_column(
        "tts_jobs",
        sa.Column(
            "is_test",
            sa.Boolean(),
            nullable=False,
            server_default="false",
            comment="Whether this is a test job (isTest=true in message)",
        ),
    )
    # Create index for filtering test jobs
    op.create_index("idx_tts_jobs_is_test", "tts_jobs", ["is_test"])


def downgrade() -> None:
    """Remove isTest column."""
    op.drop_index("idx_tts_jobs_is_test", table_name="tts_jobs")
    op.drop_column("tts_jobs", "is_test")
