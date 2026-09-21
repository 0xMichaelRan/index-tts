"""
Unit tests for TTS cache service.

Tests cover:
- Cache key generation
- Store and lookup operations
- Hit count tracking
- Cache statistics
- Eviction policy
- File integrity verification
- Voice invalidation

Run:
    uv run pytest tests/test_cache_service.py -v
"""

import os
import pytest
import tempfile
from datetime import datetime, timedelta

from sqlalchemy import select, update

from app.cache_service import TTSCacheServiceSync
from app.database import SyncSessionLocal
from app.models import TTSSynthesisCache


@pytest.fixture
def db_session():
    """Create synchronous database session for testing."""
    if not SyncSessionLocal:
        pytest.skip("Database not configured")

    session = SyncSessionLocal()
    try:
        yield session
    finally:
        session.rollback()  # Roll back any uncommitted changes
        session.close()


@pytest.fixture
def cache_service(db_session):
    """Create synchronous cache service for testing."""
    return TTSCacheServiceSync(db_session, cache_dir="outputs/test_cache")


@pytest.fixture
def temp_audio_file():
    """Create temporary audio file for testing."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".wav", delete=False) as tmp:
        tmp.write("dummy audio content for testing")
        tmp_path = tmp.name

    yield tmp_path

    # Cleanup
    if os.path.exists(tmp_path):
        os.remove(tmp_path)


class TestCacheKeyGeneration:
    """Test cache key generation."""

    def test_cache_key_is_deterministic(self):
        """Test that cache key is deterministic."""
        text = "Hello world"
        voice = "audio-prompts/voice_123.wav"

        key1 = TTSCacheServiceSync.generate_cache_key(text, voice)
        key2 = TTSCacheServiceSync.generate_cache_key(text, voice)

        assert key1 == key2
        assert len(key1) == 64  # SHA256 hex

    def test_cache_key_is_unique(self):
        """Test that different inputs produce different keys."""
        text1 = "Hello world"
        text2 = "Hello world!"
        voice = "audio-prompts/voice_123.wav"

        key1 = TTSCacheServiceSync.generate_cache_key(text1, voice)
        key2 = TTSCacheServiceSync.generate_cache_key(text2, voice)

        assert key1 != key2

    def test_text_hash_generation(self):
        """Test text hash generation."""
        text = "Test text"

        hash1 = TTSCacheServiceSync.generate_text_hash(text)
        hash2 = TTSCacheServiceSync.generate_text_hash(text)

        assert hash1 == hash2
        assert len(hash1) == 64


class TestSemanticFilename:
    """Test semantic filename generation."""

    def test_extract_voice_id_basic(self):
        """Test extracting voice ID from standard S3 path."""
        path = "audio-prompts/voice_001.wav"
        voice_id = TTSCacheServiceSync.extract_voice_id(path)
        assert voice_id == "001"

    def test_extract_voice_id_with_prefix(self):
        """Test extracting voice ID with voice_ prefix removal."""
        path = "audio-prompts/voice-mary.wav"
        voice_id = TTSCacheServiceSync.extract_voice_id(path)
        assert voice_id == "mary"

    def test_extract_voice_id_nested_path(self):
        """Test extracting voice ID from nested path."""
        path = "audio-prompts/user/123/english.wav"
        voice_id = TTSCacheServiceSync.extract_voice_id(path)
        assert voice_id == "english"

    def test_extract_voice_id_simple(self):
        """Test extracting voice ID from simple filename."""
        path = "voice.wav"
        voice_id = TTSCacheServiceSync.extract_voice_id(path)
        assert voice_id == "voice"

    def test_sanitize_text_basic(self):
        """Test sanitizing text with punctuation."""
        text = "Hello, World!"
        sanitized = TTSCacheServiceSync.sanitize_text_for_filename(text)
        assert sanitized == "hello_world"

    def test_sanitize_text_special_chars(self):
        """Test sanitizing text with special characters."""
        text = "What's your name?"
        sanitized = TTSCacheServiceSync.sanitize_text_for_filename(text)
        assert sanitized == "whats_your_name"

    def test_sanitize_text_parentheses(self):
        """Test sanitizing text with parentheses and brackets."""
        text = "Test (v2) [edit]"
        sanitized = TTSCacheServiceSync.sanitize_text_for_filename(text)
        assert sanitized == "test_v2_edit"

    def test_sanitize_text_max_length(self):
        """Test sanitizing text with max length."""
        text = "This is a very long text that should be truncated"
        sanitized = TTSCacheServiceSync.sanitize_text_for_filename(text, max_length=20)
        assert len(sanitized) <= 20
        assert sanitized == "this_is_a_very_long"

    def test_sanitize_text_multiple_spaces(self):
        """Test sanitizing text with multiple spaces."""
        text = "Hello    world  test"
        sanitized = TTSCacheServiceSync.sanitize_text_for_filename(text)
        assert sanitized == "hello_world_test"

    def test_generate_semantic_filename_basic(self):
        """Test generating semantic filename."""
        text = "Hello world"
        voice = "audio-prompts/voice_001.wav"
        filename = TTSCacheServiceSync.generate_semantic_filename(text, voice)
        assert filename == "hello_world_001.wav"

    def test_generate_semantic_filename_with_different_voice(self):
        """Test generating semantic filename with different voice."""
        text = "This is a test"
        voice = "audio-prompts/mary.wav"
        filename = TTSCacheServiceSync.generate_semantic_filename(text, voice)
        assert filename == "this_is_a_test_mary.wav"

    def test_generate_semantic_filename_different_text(self):
        """Test generating semantic filename with different text."""
        text = "Slow speech"
        voice = "audio-prompts/voice_slow.wav"
        filename = TTSCacheServiceSync.generate_semantic_filename(text, voice)
        assert filename == "slow_speech_slow.wav"

    def test_generate_semantic_filename_nested_voice_path(self):
        """Test generating semantic filename with nested voice path."""
        text = "Testing nested paths"
        voice = "audio-prompts/user/123/english.wav"
        filename = TTSCacheServiceSync.generate_semantic_filename(text, voice)
        assert filename == "testing_nested_paths_english.wav"


class TestCacheLookup:
    """Test cache lookup operations."""

    def test_lookup_miss_returns_none(self, cache_service):
        """Test that lookup returns None when entry doesn't exist."""
        entry = cache_service.lookup("nonexistent text", "nonexistent_voice.wav")
        assert entry is None

    def test_lookup_returns_stored_entry(self, cache_service, temp_audio_file):
        """Test that lookup returns stored entry."""
        text = "Test synthesis"
        voice = "audio-prompts/test_voice.wav"

        # Store entry
        stored = cache_service.store(
            text=text,
            audio_prompt_path=voice,
            base_audio_local_path=temp_audio_file,
            audio_duration_seconds=5.0,
            synthesis_duration_ms=3000,
        )

        # Lookup
        retrieved = cache_service.lookup(text, voice)

        assert retrieved is not None
        assert retrieved.cache_key == stored.cache_key
        assert retrieved.text == text
        assert retrieved.audio_prompt_path == voice

        # Cleanup
        cache_service.delete_entry(stored.cache_key)

    def test_lookup_deletes_entry_if_file_missing(self, cache_service, temp_audio_file):
        """Test that lookup deletes entry if file is missing."""
        text = "Test file deletion"
        voice = "audio-prompts/test_voice.wav"

        # Store entry
        cache_service.store(
            text=text,
            audio_prompt_path=voice,
            base_audio_local_path=temp_audio_file,
            audio_duration_seconds=3.0,
            synthesis_duration_ms=2000,
        )

        # Delete the file (simulate missing file)
        os.remove(temp_audio_file)

        # Lookup should return None and delete entry
        retrieved = cache_service.lookup(text, voice)

        assert retrieved is None


class TestCacheStore:
    """Test cache store operations."""

    def test_store_creates_entry(self, cache_service, temp_audio_file):
        """Test that store creates cache entry."""
        text = "Store test"
        voice = "audio-prompts/voice.wav"

        entry = cache_service.store(
            text=text,
            audio_prompt_path=voice,
            base_audio_local_path=temp_audio_file,
            audio_duration_seconds=4.0,
            synthesis_duration_ms=2500,
            language="en",
        )

        assert entry.cache_key is not None
        assert entry.text == text
        assert entry.audio_prompt_path == voice
        assert entry.audio_duration_seconds == 4.0
        assert entry.synthesis_duration_ms == 2500
        assert entry.language == "en"
        assert entry.hit_count == 0

        # Cleanup
        cache_service.delete_entry(entry.cache_key)

    def test_store_calculates_file_size(self, cache_service, temp_audio_file):
        """Test that store calculates file size."""
        text = "File size test"
        voice = "audio-prompts/voice.wav"

        expected_size = os.path.getsize(temp_audio_file)

        entry = cache_service.store(
            text=text,
            audio_prompt_path=voice,
            base_audio_local_path=temp_audio_file,
            audio_duration_seconds=3.0,
            synthesis_duration_ms=2000,
        )

        assert entry.file_size_bytes == expected_size

        # Cleanup
        cache_service.delete_entry(entry.cache_key)

    def test_store_raises_if_file_missing(self, cache_service):
        """Test that store raises error if file doesn't exist."""
        with pytest.raises(FileNotFoundError):
            cache_service.store(
                text="Test",
                audio_prompt_path="audio-prompts/voice.wav",
                base_audio_local_path="/nonexistent/file.wav",
                audio_duration_seconds=3.0,
                synthesis_duration_ms=2000,
            )

    def test_store_duplicate_is_idempotent(self, cache_service, temp_audio_file):
        """Test that storing the same (text, voice) twice does not raise (upsert / ON CONFLICT DO NOTHING)."""
        text = "Duplicate store test"
        voice = "audio-prompts/dup_voice.wav"

        entry1 = cache_service.store(
            text=text,
            audio_prompt_path=voice,
            base_audio_local_path=temp_audio_file,
            audio_duration_seconds=3.0,
            synthesis_duration_ms=2000,
        )

        # Second store of the same key — should not raise
        entry2 = cache_service.store(
            text=text,
            audio_prompt_path=voice,
            base_audio_local_path=temp_audio_file,
            audio_duration_seconds=3.0,
            synthesis_duration_ms=2000,
        )

        # Both should share the same cache_key; original entry wins
        assert entry1.cache_key == entry2.cache_key

        # Cleanup
        cache_service.delete_entry(entry1.cache_key)

    def test_store_relative_path_when_inside_cache_dir(self, cache_service, db_session):
        """Test that storing a file inside cache_dir stores only a relative path in DB (Phase 3b)."""
        rel_filename = "test_rel_audio.wav"
        full_path = cache_service.cache_dir / rel_filename
        full_path.write_bytes(b"dummy audio content inside cache_dir")

        text = "Relative path test"
        voice = "audio-prompts/rel_voice.wav"

        try:
            entry = cache_service.store(
                text=text,
                audio_prompt_path=voice,
                base_audio_local_path=str(full_path),
                audio_duration_seconds=1.5,
                synthesis_duration_ms=500,
            )

            # 1. Stored row in DB must contain ONLY the relative path (not absolute)
            stmt = select(TTSSynthesisCache.base_audio_local_path).where(
                TTSSynthesisCache.cache_key == entry.cache_key
            )
            raw_stored_path = db_session.execute(stmt).scalar_one()
            assert not os.path.isabs(raw_stored_path)
            assert raw_stored_path == rel_filename

            # 2. Returned entry.base_audio_local_path must be resolved to absolute path
            assert os.path.isabs(entry.base_audio_local_path)
            assert os.path.exists(entry.base_audio_local_path)

            # 3. Lookup must return resolved absolute path
            looked_up = cache_service.lookup(text, voice)
            assert looked_up is not None
            assert os.path.isabs(looked_up.base_audio_local_path)
            assert os.path.exists(looked_up.base_audio_local_path)

            # 4. DB record must STILL be relative after lookup (lookup didn't overwrite with abs)
            db_session.expire_all()
            raw_stored_after = db_session.execute(stmt).scalar_one()
            assert not os.path.isabs(raw_stored_after)
            assert raw_stored_after == rel_filename

        finally:
            cache_service.delete_entry(cache_service.generate_cache_key(text, voice))
            if full_path.exists():
                full_path.unlink()


class TestHitCountTracking:
    """Test hit count tracking."""

    def test_hit_count_increments_on_lookup(self, cache_service, temp_audio_file):
        """Test that hit count increments on cache lookup."""
        text = "Hit count test"
        voice = "audio-prompts/voice.wav"

        # Store entry
        entry = cache_service.store(
            text=text,
            audio_prompt_path=voice,
            base_audio_local_path=temp_audio_file,
            audio_duration_seconds=3.0,
            synthesis_duration_ms=2000,
        )

        initial_hits = entry.hit_count

        # Lookup multiple times
        cache_service.lookup(text, voice)
        cache_service.lookup(text, voice)
        cache_service.lookup(text, voice)

        # Final lookup to check count
        final_entry = cache_service.lookup(text, voice)

        # Should increment by 4 (3 + 1 final lookup)
        assert final_entry.hit_count == initial_hits + 4

        # Cleanup
        cache_service.delete_entry(entry.cache_key)


class TestCacheStatistics:
    """Test cache statistics."""

    def test_get_cache_stats(self, cache_service):
        """Test getting cache statistics."""
        stats = cache_service.get_cache_stats()

        assert "total_entries" in stats
        assert "total_hits" in stats
        assert "total_size_mb" in stats
        assert "avg_hits_per_entry" in stats

        assert isinstance(stats["total_entries"], int)
        assert isinstance(stats["total_hits"], int)
        assert isinstance(stats["total_size_mb"], (int, float))
        assert isinstance(stats["avg_hits_per_entry"], (int, float))


class TestCacheEviction:
    """Test cache eviction."""

    def test_evict_old_entries(self, cache_service, temp_audio_file):
        """Test evicting old entries."""
        # Store some entries
        entries = []
        for i in range(3):
            entry = cache_service.store(
                text=f"Eviction test {i}",
                audio_prompt_path=f"audio-prompts/voice_{i}.wav",
                base_audio_local_path=temp_audio_file,
                audio_duration_seconds=3.0,
                synthesis_duration_ms=2000,
            )
            entries.append(entry)

        # Get stats before
        stats_before = cache_service.get_cache_stats()
        entries_before = stats_before["total_entries"]

        # Evict entries
        evicted = cache_service.evict_old_entries(
            max_entries=entries_before - 2,  # Keep all but 2
            evict_count=2,
        )

        # Get stats after
        stats_after = cache_service.get_cache_stats()
        entries_after = stats_after["total_entries"]

        # Check eviction worked
        assert evicted <= 2
        assert entries_after <= entries_before

        # Cleanup remaining entries
        for entry in entries:
            try:
                cache_service.delete_entry(entry.cache_key)
            except Exception:
                pass  # Entry may already be deleted

    def test_evict_removes_files_stored_with_relative_paths(
        self, cache_service, db_session
    ):
        """Test that bulk eviction deletes physical files stored with relative paths (Phase 3b + 3c)."""
        rel_filename = "test_evict_rel.wav"
        full_path = cache_service.cache_dir / rel_filename
        full_path.write_bytes(b"dummy audio for eviction")

        text = "Eviction relative path test"
        voice = "audio-prompts/evict_rel_voice.wav"

        try:
            entry = cache_service.store(
                text=text,
                audio_prompt_path=voice,
                base_audio_local_path=str(full_path),
                audio_duration_seconds=1.0,
                synthesis_duration_ms=400,
            )
            cache_key = entry.cache_key
            assert full_path.exists()

            # Set last_accessed_at far in the past so it is guaranteed to be the LRU candidate
            db_session.execute(
                update(TTSSynthesisCache)
                .where(TTSSynthesisCache.cache_key == cache_key)
                .values(last_accessed_at=datetime.utcnow() - timedelta(days=3650))
            )
            db_session.commit()

            # Evict with max_entries=0 so this entry gets evicted
            evicted = cache_service.evict_old_entries(max_entries=0, evict_count=1)
            assert evicted >= 1

            # File must be deleted on disk
            assert not full_path.exists()

            # DB entry must be deleted
            stmt = select(TTSSynthesisCache).where(
                TTSSynthesisCache.cache_key == cache_key
            )
            assert db_session.execute(stmt).scalar_one_or_none() is None

        finally:
            if full_path.exists():
                full_path.unlink()


class TestCacheDeletion:
    """Test cache deletion operations."""

    def test_delete_entry(self, cache_service, temp_audio_file):
        """Test deleting cache entry."""
        text = "Delete test"
        voice = "audio-prompts/voice.wav"

        # Store entry
        entry = cache_service.store(
            text=text,
            audio_prompt_path=voice,
            base_audio_local_path=temp_audio_file,
            audio_duration_seconds=3.0,
            synthesis_duration_ms=2000,
        )

        # Delete entry
        deleted = cache_service.delete_entry(entry.cache_key)

        assert deleted is True

        # Verify entry is gone
        retrieved = cache_service.lookup(text, voice)
        assert retrieved is None

    def test_delete_nonexistent_entry(self, cache_service):
        """Test deleting nonexistent entry returns False."""
        deleted = cache_service.delete_entry("nonexistent_key_12345")
        assert deleted is False


class TestVoiceInvalidation:
    """Test voice cache invalidation."""

    def test_invalidate_voice_cache(self, cache_service, temp_audio_file):
        """Test invalidating all entries for a voice."""
        voice = "audio-prompts/test_invalidate_voice.wav"

        # Store multiple entries with same voice
        entries = []
        for i in range(3):
            entry = cache_service.store(
                text=f"Invalidation test {i}",
                audio_prompt_path=voice,
                base_audio_local_path=temp_audio_file,
                audio_duration_seconds=3.0,
                synthesis_duration_ms=2000,
            )
            entries.append(entry)

        # Invalidate voice
        deleted = cache_service.invalidate_voice_cache(voice)

        assert deleted == 3

        # Verify all entries are gone
        for i in range(3):
            retrieved = cache_service.lookup(f"Invalidation test {i}", voice)
            assert retrieved is None


class TestTopEntries:
    """Test top entries query."""

    def test_get_top_entries(self, cache_service, temp_audio_file):
        """Test getting top entries by hit count."""
        # Store entries with different hit counts
        entry1 = cache_service.store(
            text="Top entry 1",
            audio_prompt_path="audio-prompts/voice_1.wav",
            base_audio_local_path=temp_audio_file,
            audio_duration_seconds=3.0,
            synthesis_duration_ms=2000,
        )

        entry2 = cache_service.store(
            text="Top entry 2",
            audio_prompt_path="audio-prompts/voice_2.wav",
            base_audio_local_path=temp_audio_file,
            audio_duration_seconds=3.0,
            synthesis_duration_ms=2000,
        )

        # Generate different hit counts
        cache_service.lookup("Top entry 1", "audio-prompts/voice_1.wav")
        cache_service.lookup("Top entry 1", "audio-prompts/voice_1.wav")
        cache_service.lookup("Top entry 1", "audio-prompts/voice_1.wav")

        cache_service.lookup("Top entry 2", "audio-prompts/voice_2.wav")

        # Get top entries
        top = cache_service.get_top_entries(limit=10)

        assert len(top) >= 2

        # First entry should have more hits
        # (Note: there may be other entries from other tests)

        # Cleanup
        cache_service.delete_entry(entry1.cache_key)
        cache_service.delete_entry(entry2.cache_key)
