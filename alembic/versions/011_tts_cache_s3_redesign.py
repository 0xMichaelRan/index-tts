"""TTS synthesis cache S3 redesign: base_alignment_s3_path, nullable local path, drop valid_file_path constraint

Revision ID: 011_tts_cache_s3_redesign
Revises: 010_relativize_cache_paths
Create Date: 2026-10-24 20:00:00.000000

Changes to tts_synthesis_cache:
1. Drop CHECK constraint valid_file_path (was: LENGTH(base_audio_local_path) > 0)
   — base_audio_local_path becomes nullable (L1 hot cache, optional)
2. ALTER COLUMN base_audio_local_path DROP NOT NULL
3. ADD COLUMN base_alignment_s3_path VARCHAR(1024) nullable
   — S3 key for alignment JSON; reused on cache hits to skip Whisper

No data backfill required for existing rows:
  - existing base_audio_local_path values are preserved as-is
  - base_audio_s3_path was already nullable (UNUSED column); no change needed
  - base_alignment_s3_path will be NULL for pre-existing rows and populated
    on the next synthesis of each (text, voice) pair
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "011_tts_cache_s3_redesign"
down_revision: Union[str, None] = "010_relativize_cache_paths"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "tts_synthesis_cache"


def upgrade() -> None:
    # 1. Drop the CHECK constraint that enforced base_audio_local_path non-empty
    #    (it must go before we make the column nullable, otherwise it conflicts).
    op.drop_constraint("valid_file_path", TABLE, type_="check")

    # 2. Make base_audio_local_path nullable (L1 hot cache — may be absent after
    #    container restart on deployments that don't mount a persistent volume).
    op.alter_column(
        TABLE,
        "base_audio_local_path",
        nullable=True,
        existing_type=sa.String(1024),
        existing_nullable=False,
    )

    # 3. Add base_alignment_s3_path column (S3 key for cached alignment JSON).
    op.add_column(
        TABLE,
        sa.Column(
            "base_alignment_s3_path",
            sa.String(1024),
            nullable=True,
            comment="S3 key to parsed alignment JSON for base audio (reused on cache hits to skip Whisper)",
        ),
    )


def downgrade() -> None:
    # Remove new column
    op.drop_column(TABLE, "base_alignment_s3_path")

    # Restore NOT NULL on base_audio_local_path.
    # First set any NULLs to empty string so the ALTER won't fail.
    op.execute(
        sa.text(
            "UPDATE tts_synthesis_cache "
            "SET base_audio_local_path = '' "
            "WHERE base_audio_local_path IS NULL"
        )
    )
    op.alter_column(
        TABLE,
        "base_audio_local_path",
        nullable=False,
        existing_type=sa.String(1024),
        existing_nullable=True,
    )

    # Restore CHECK constraint
    op.create_check_constraint(
        "valid_file_path",
        TABLE,
        "LENGTH(base_audio_local_path) > 0",
    )
