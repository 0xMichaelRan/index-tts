"""
Integration tests for PostgreSQL connectivity, schema integrity, and the
TTS synthesis cache service (TTSCacheServiceSync) against a live database.

Requires DATABASE_URL in .env pointing to a real PostgreSQL instance with
migrations applied. If DATABASE_URL is absent, all tests are skipped.

Run:
    uv run pytest tests/pytest/test_db_integration.py -v
"""

from __future__ import annotations

import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Guard
# ---------------------------------------------------------------------------

_DB_CONFIGURED = os.getenv("DATABASE_URL") is not None


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def sync_session_factory():
    """Return SyncSessionLocal (sessionmaker), skipping if DB not configured."""
    if not _DB_CONFIGURED:
        pytest.skip("DATABASE_URL not set")
    from app.database import SyncSessionLocal

    if SyncSessionLocal is None:
        pytest.skip("SyncSessionLocal could not be initialised (check DATABASE_URL)")
    return SyncSessionLocal


@pytest.fixture(scope="function")
def db(sync_session_factory):
    """Function-scoped DB session; always rolls back so tests don't persist data."""
    session = sync_session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


# ---------------------------------------------------------------------------
# 1. Connectivity
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _DB_CONFIGURED, reason="DATABASE_URL not set")
class TestDatabaseConnectivity:
    """Basic connectivity checks against the live PostgreSQL instance."""

    def test_select_1(self, db) -> None:
        """SELECT 1 must execute cleanly within reasonable time."""
        result = db.execute(text("SELECT 1")).scalar()
        assert result == 1

    def test_multiple_connections(self, sync_session_factory) -> None:
        """Open 3 independent sessions and execute SELECT 1 on each."""
        sessions = [sync_session_factory() for _ in range(3)]
        try:
            for session in sessions:
                assert session.execute(text("SELECT 1")).scalar() == 1
        finally:
            for session in sessions:
                session.rollback()
                session.close()

    def test_database_url_uses_postgresql_driver(self) -> None:
        """DATABASE_URL must reference PostgreSQL (not SQLite or other)."""
        database_url = os.getenv("DATABASE_URL", "")
        assert "postgresql" in database_url, (
            f"DATABASE_URL should use PostgreSQL driver, got: {database_url!r}"
        )


# ---------------------------------------------------------------------------
# 2. Schema Integrity
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _DB_CONFIGURED, reason="DATABASE_URL not set")
class TestDatabaseSchema:
    """Verify that Alembic migrations have been applied to the live DB."""

    def test_tts_synthesis_cache_table_exists(self, db) -> None:
        """tts_synthesis_cache table must exist (migrations applied)."""
        db.execute(text("SELECT 1 FROM tts_synthesis_cache LIMIT 1"))

    def test_tts_jobs_table_exists(self, db) -> None:
        """tts_jobs table must exist (migrations applied)."""
        db.execute(text("SELECT 1 FROM tts_jobs LIMIT 1"))

    def test_tts_synthesis_cache_expected_columns(self, db) -> None:
        """All expected columns must be present in tts_synthesis_cache."""
        expected_columns = {
            "cache_key",
            "text",
            "audio_prompt_path",
            "text_hash",
            "base_audio_local_path",
            "audio_duration_seconds",
            "synthesis_duration_ms",
            "hit_count",
            "created_at",
            "last_accessed_at",
            "language",
            "word_count",
            "tts_engine",
        }
        rows = db.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'tts_synthesis_cache'
                """
            )
        ).fetchall()
        actual_columns = {row[0] for row in rows}
        missing = expected_columns - actual_columns
        assert not missing, (
            f"Missing columns in tts_synthesis_cache: {missing}"
        )


# ---------------------------------------------------------------------------
# 3. Cache Service Integration
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def cache_audio_file() -> str:
    """Real temporary WAV-like file that satisfies store()'s FileNotFoundError guard."""
    with tempfile.NamedTemporaryFile(
        mode="wb", suffix=".wav", delete=False
    ) as fh:
        # Write a minimal WAV header so file_size_bytes > 0
        fh.write(b"RIFF\x24\x00\x00\x00WAVEfmt ")
        path = fh.name
    yield path
    if os.path.exists(path):
        os.remove(path)


@pytest.fixture(scope="function")
def unique_text() -> str:
    """Unique text per test to avoid PK collisions with existing cache entries."""
    return f"Integration test synthesis text {uuid.uuid4()}"


@pytest.fixture(scope="function")
def unique_voice() -> str:
    """Unique voice path per test."""
    return f"voice-recordings/integration-test/{uuid.uuid4()}.wav"


@pytest.fixture(scope="function")
def cache_svc(db, cache_audio_file):
    """TTSCacheServiceSync pointed at a temp cache directory."""
    if not _DB_CONFIGURED:
        pytest.skip("DATABASE_URL not set")
    from app.cache_service import TTSCacheServiceSync

    with tempfile.TemporaryDirectory() as tmpdir:
        svc = TTSCacheServiceSync(db, cache_dir=tmpdir)
        # Make the audio file live inside the cache_dir so store() can relativise
        dest = Path(tmpdir) / Path(cache_audio_file).name
        dest.write_bytes(Path(cache_audio_file).read_bytes())
        # Expose the in-cache path so tests can pass it directly
        svc._test_audio_path = str(dest)
        yield svc


@pytest.mark.skipif(not _DB_CONFIGURED, reason="DATABASE_URL not set")
class TestCacheServiceIntegration:
    """End-to-end tests for TTSCacheServiceSync against a live PostgreSQL DB.

    Each test uses a unique (text, voice) pair, and the function-scoped `db`
    fixture rolls back after each test — so nothing is left in the DB.
    """

    def test_lookup_miss(self, cache_svc, unique_text, unique_voice) -> None:
        """Fresh (text, voice) must return None (cache miss)."""
        result = cache_svc.lookup(unique_text, unique_voice)
        assert result is None

    def test_store_and_lookup_hit(
        self, cache_svc, unique_text, unique_voice
    ) -> None:
        """store() followed by lookup() must return the same cache_key."""
        entry = cache_svc.store(
            text=unique_text,
            audio_prompt_path=unique_voice,
            base_audio_local_path=cache_svc._test_audio_path,
            audio_duration_seconds=2.5,
            synthesis_duration_ms=1500,
            language="en",
        )
        assert entry is not None
        expected_key = cache_svc.generate_cache_key(unique_text, unique_voice)
        assert entry.cache_key == expected_key

        hit = cache_svc.lookup(unique_text, unique_voice)
        assert hit is not None
        assert hit.cache_key == expected_key

    def test_hit_count_increments(
        self, cache_svc, unique_text, unique_voice
    ) -> None:
        """Each lookup() after store() must increment hit_count by 1."""
        cache_svc.store(
            text=unique_text,
            audio_prompt_path=unique_voice,
            base_audio_local_path=cache_svc._test_audio_path,
            audio_duration_seconds=1.0,
            synthesis_duration_ms=800,
        )
        hit1 = cache_svc.lookup(unique_text, unique_voice)
        hit2 = cache_svc.lookup(unique_text, unique_voice)
        assert hit1.hit_count == 1
        assert hit2.hit_count == 2

    def test_delete_entry(self, cache_svc, unique_text, unique_voice) -> None:
        """delete_entry() must remove the row; subsequent lookup() returns None."""
        entry = cache_svc.store(
            text=unique_text,
            audio_prompt_path=unique_voice,
            base_audio_local_path=cache_svc._test_audio_path,
            audio_duration_seconds=1.0,
            synthesis_duration_ms=500,
        )
        deleted = cache_svc.delete_entry(entry.cache_key)
        assert deleted is True
        assert cache_svc.lookup(unique_text, unique_voice) is None

    def test_evict_old_entries(self, db, cache_svc, unique_text, unique_voice) -> None:
        """An entry backdated past max_age_days must be removed by evict_old_entries()."""
        from app.models import TTSSynthesisCache

        entry = cache_svc.store(
            text=unique_text,
            audio_prompt_path=unique_voice,
            base_audio_local_path=cache_svc._test_audio_path,
            audio_duration_seconds=1.0,
            synthesis_duration_ms=500,
        )

        # Backdate last_accessed_at by 30 days via raw SQL so we bypass ORM guards
        cache_key = entry.cache_key
        old_ts = datetime.now(timezone.utc) - timedelta(days=30)
        db.execute(
            text(
                "UPDATE tts_synthesis_cache "
                "SET last_accessed_at = :ts "
                "WHERE cache_key = :key"
            ),
            {"ts": old_ts, "key": cache_key},
        )
        db.commit()

        # Trigger eviction: set max_entries=0 so ANY entry count exceeds the limit
        evicted = cache_svc.evict_old_entries(max_entries=0, evict_count=1000)
        assert evicted >= 1
        assert cache_svc.lookup(unique_text, unique_voice) is None

    def test_get_cache_stats_returns_dict(self, cache_svc) -> None:
        """get_cache_stats() must return a dict with expected keys."""
        stats = cache_svc.get_cache_stats()
        assert isinstance(stats, dict)
        for key in ("total_entries", "total_size_mb"):
            assert key in stats, f"Missing key in cache stats: {key!r}"

    def test_invalidate_voice_cache(
        self, cache_svc, unique_voice
    ) -> None:
        """invalidate_voice_cache() must remove all entries sharing the same voice."""
        texts = [f"text {uuid.uuid4()}" for _ in range(2)]
        for t in texts:
            cache_svc.store(
                text=t,
                audio_prompt_path=unique_voice,
                base_audio_local_path=cache_svc._test_audio_path,
                audio_duration_seconds=1.0,
                synthesis_duration_ms=400,
            )
        removed = cache_svc.invalidate_voice_cache(unique_voice)
        assert removed >= 2
        for t in texts:
            assert cache_svc.lookup(t, unique_voice) is None
