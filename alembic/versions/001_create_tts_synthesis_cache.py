"""create tts synthesis cache table

Revision ID: 001
Revises:
Create Date: 2026-08-09 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create tts_synthesis_cache table with all indexes and constraints."""
    # Create table
    op.create_table(
        "tts_synthesis_cache",
        sa.Column(
            "cache_key",
            sa.String(length=64),
            nullable=False,
            comment="SHA256 hash of text + audio_prompt_path",
        ),
        sa.Column(
            "text", sa.Text(), nullable=False, comment="Full text that was synthesized"
        ),
        sa.Column(
            "audio_prompt_path",
            sa.String(length=512),
            nullable=False,
            comment="S3 path to voice prompt file",
        ),
        sa.Column(
            "text_hash",
            sa.String(length=64),
            nullable=False,
            comment="SHA256 hash of text only (for indexing)",
        ),
        sa.Column(
            "base_audio_local_path",
            sa.String(length=1024),
            nullable=False,
            comment="Local filesystem path to cached WAV file",
        ),
        sa.Column(
            "base_audio_s3_path",
            sa.String(length=1024),
            nullable=True,
            comment="Optional S3 backup path",
        ),
        sa.Column(
            "audio_duration_seconds",
            sa.Float(),
            nullable=False,
            comment="Duration of base audio in seconds",
        ),
        sa.Column(
            "sample_rate",
            sa.Integer(),
            server_default="24000",
            comment="Audio sample rate (Hz)",
        ),
        sa.Column(
            "audio_format",
            sa.String(length=10),
            server_default="wav",
            comment="Audio file format",
        ),
        sa.Column(
            "file_size_bytes",
            sa.BigInteger(),
            nullable=True,
            comment="File size in bytes",
        ),
        sa.Column(
            "synthesis_duration_ms",
            sa.Integer(),
            nullable=False,
            comment="Time taken to synthesize (milliseconds)",
        ),
        sa.Column(
            "hit_count",
            sa.Integer(),
            server_default="0",
            comment="Number of times this cache entry was reused",
        ),
        sa.Column(
            "last_accessed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            comment="Last time this cache entry was accessed",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            comment="When this cache entry was created",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            comment="When this cache entry was last updated",
        ),
        sa.Column(
            "language",
            sa.String(length=10),
            nullable=True,
            comment="Language code (e.g., 'en', 'zh')",
        ),
        sa.Column(
            "tts_engine",
            sa.String(length=50),
            server_default="IndexTTS-1.5",
            comment="TTS engine version",
        ),
        sa.PrimaryKeyConstraint("cache_key"),
        sa.CheckConstraint("audio_duration_seconds > 0", name="valid_duration"),
        sa.CheckConstraint("LENGTH(base_audio_local_path) > 0", name="valid_file_path"),
        sa.CheckConstraint("hit_count >= 0", name="valid_hit_count"),
    )

    # Create indexes for performance
    op.create_index("idx_tts_cache_text_hash", "tts_synthesis_cache", ["text_hash"])
    op.create_index(
        "idx_tts_cache_audio_prompt", "tts_synthesis_cache", ["audio_prompt_path"]
    )
    op.create_index(
        "idx_tts_cache_last_accessed", "tts_synthesis_cache", ["last_accessed_at"]
    )
    op.create_index("idx_tts_cache_created_at", "tts_synthesis_cache", ["created_at"])
    op.create_index("idx_tts_cache_hit_count", "tts_synthesis_cache", ["hit_count"])

    # Composite index for common lookup pattern
    op.create_index(
        "idx_tts_cache_lookup",
        "tts_synthesis_cache",
        ["text_hash", "audio_prompt_path"],
    )


def downgrade() -> None:
    """Drop tts_synthesis_cache table and all indexes."""
    # Drop indexes first
    op.drop_index("idx_tts_cache_lookup", table_name="tts_synthesis_cache")
    op.drop_index("idx_tts_cache_hit_count", table_name="tts_synthesis_cache")
    op.drop_index("idx_tts_cache_created_at", table_name="tts_synthesis_cache")
    op.drop_index("idx_tts_cache_last_accessed", table_name="tts_synthesis_cache")
    op.drop_index("idx_tts_cache_audio_prompt", table_name="tts_synthesis_cache")
    op.drop_index("idx_tts_cache_text_hash", table_name="tts_synthesis_cache")

    # Drop table
    op.drop_table("tts_synthesis_cache")
