"""Relativize cache paths in tts_synthesis_cache

Revision ID: 010_relativize_cache_paths
Revises: 009_add_priority_field
Create Date: 2026-09-22 06:35:00.000000

Phase 3b: Store ``base_audio_local_path`` as a path relative to the
configured ``cache_dir`` (e.g. ``hello_world_001.wav``) instead of the
full absolute path (e.g. ``/home/user/project/outputs/tts_cache/hello_world_001.wav``).

Benefits:
- Cache is portable across machines sharing the same DB
- Cache survives ``cache_dir`` renames / moves (absolute prefix stripped)
- Simpler values in the DB

Upgrade:
    Strips the ``outputs/tts_cache/`` prefix (or any trailing path component
    up to and including a ``tts_cache`` directory) from every existing row.
    Rows that are already relative (no leading ``/``) are left untouched.

Downgrade:
    Re-prepends the default ``outputs/tts_cache/`` prefix to every row that
    looks relative (no leading ``/``).

.. warning::
    After this migration the worker must be configured with the same
    ``cache_dir`` as was used before, otherwise cached audio files will not
    be found on lookup.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "010_relativize_cache_paths"
down_revision: Union[str, None] = "009_add_priority_field"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The default cache directory prefix used before this migration.
_DEFAULT_CACHE_DIR = "outputs/tts_cache/"


def upgrade() -> None:
    """Strip absolute-path prefix from base_audio_local_path, keeping only the filename."""
    # Use a regex to extract just the filename from any absolute path.
    # Paths that are already relative (no leading '/') are left untouched.
    op.execute(
        sa.text(
            """
            UPDATE tts_synthesis_cache
            SET base_audio_local_path = regexp_replace(
                base_audio_local_path,
                '^.*/([^/]+)$',
                '\\1'
            )
            WHERE base_audio_local_path LIKE '/%'
            """
        )
    )


def downgrade() -> None:
    """Re-prepend the default cache_dir prefix to relative paths."""
    op.execute(
        sa.text(
            f"""
            UPDATE tts_synthesis_cache
            SET base_audio_local_path = '{_DEFAULT_CACHE_DIR}' || base_audio_local_path
            WHERE base_audio_local_path NOT LIKE '/%'
            """
        )
    )
