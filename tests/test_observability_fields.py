"""
Test for new observability fields in migrations 005, 006, 007.

Tests:
- Migration 005: Unique constraint on tts_jobs.job_id
- Migration 006: Observability fields (cache_hit, alignment_duration_seconds, time_stretched, source_ratio, target_ratio)
- Migration 007: Foreign key constraint on tts_jobs.cache_key
"""

import pytest
from datetime import datetime, timezone
from sqlalchemy.exc import IntegrityError
from app.models import TTSJob, TTSSynthesisCache


class TestJobIdUniqueConstraint:
    """Test migration 005: unique constraint on job_id."""

    def test_job_id_is_unique_in_model(self):
        """TTSJob model should have unique=True on job_id column."""
        job_id_column = TTSJob.__table__.columns.get("job_id")
        assert job_id_column is not None
        assert job_id_column.unique is True, "job_id should have unique=True"


class TestObservabilityFields:
    """Test migration 006: observability fields."""

    def test_cache_hit_field_exists(self):
        """TTSJob model should have cache_hit field."""
        assert hasattr(TTSJob, "cache_hit"), "cache_hit field should exist"

        # Test default value (Python-level default applies on object creation)
        job = TTSJob(job_id=1001, job_type="studio", status="processing")
        # Python default should be False
        assert job.cache_hit is False, "cache_hit should default to False"

        # Test explicit set
        job_with_hit = TTSJob(
            job_id=1002, job_type="studio", status="processing", cache_hit=True
        )
        assert job_with_hit.cache_hit is True

    def test_alignment_duration_seconds_field_exists(self):
        """TTSJob model should have alignment_duration_seconds field."""
        assert hasattr(TTSJob, "alignment_duration_seconds"), (
            "alignment_duration_seconds field should exist"
        )

        # Test nullable
        job = TTSJob(job_id=1002, job_type="studio", status="processing")
        assert job.alignment_duration_seconds is None, (
            "alignment_duration_seconds should be nullable"
        )

    def test_time_stretched_field_exists(self):
        """TTSJob model should have time_stretched field."""
        assert hasattr(TTSJob, "time_stretched"), "time_stretched field should exist"

        # Test default value
        job = TTSJob(job_id=1003, job_type="studio", status="processing")
        assert job.time_stretched is False, "time_stretched should default to False"

    def test_source_ratio_field_exists(self):
        """TTSJob model should have source_ratio field."""
        assert hasattr(TTSJob, "source_ratio"), "source_ratio field should exist"

        # Test nullable
        job = TTSJob(job_id=1004, job_type="studio", status="processing")
        assert job.source_ratio is None, "source_ratio should be nullable"

    def test_target_ratio_field_exists(self):
        """TTSJob model should have target_ratio field."""
        assert hasattr(TTSJob, "target_ratio"), "target_ratio field should exist"

        # Test nullable
        job = TTSJob(job_id=1005, job_type="studio", status="processing")
        assert job.target_ratio is None, "target_ratio should be nullable"

    def test_observability_fields_can_be_set(self):
        """Test that all observability fields can be set."""
        job = TTSJob(
            job_id=1006,
            job_type="studio",
            status="completed",
            cache_hit=True,
            alignment_duration_seconds=1.23,
            time_stretched=True,
            source_ratio=1.0,
            target_ratio=1.5,
        )

        assert job.cache_hit is True
        assert job.alignment_duration_seconds == 1.23
        assert job.time_stretched is True
        assert job.source_ratio == 1.0
        assert job.target_ratio == 1.5


class TestCacheForeignKey:
    """Test migration 007: foreign key constraint on cache_key."""

    def test_cache_key_has_foreign_key(self):
        """TTSJob.cache_key should have foreign key to tts_synthesis_cache."""
        cache_key_column = TTSJob.__table__.columns.get("cache_key")
        assert cache_key_column is not None

        # Check if foreign key constraint exists
        foreign_keys = list(cache_key_column.foreign_keys)
        assert len(foreign_keys) > 0, "cache_key should have a foreign key"

        # Verify it points to tts_synthesis_cache.cache_key
        fk = foreign_keys[0]
        assert fk.column.table.name == "tts_synthesis_cache", (
            "Foreign key should reference tts_synthesis_cache"
        )
        assert fk.column.name == "cache_key", (
            "Foreign key should reference cache_key column"
        )

    def test_cache_key_nullable(self):
        """cache_key should be nullable."""
        cache_key_column = TTSJob.__table__.columns.get("cache_key")
        assert cache_key_column.nullable is True, "cache_key should be nullable"


class TestModelIntegration:
    """Integration tests for the complete model structure."""

    def test_complete_job_with_all_fields(self):
        """Test creating a TTSJob with all new observability fields."""
        job = TTSJob(
            job_id=9999,
            job_type="rem",
            status="completed",
            is_test=False,
            text="Hello world",
            audio_prompt_path="audio-prompts/voice_123.wav",
            language="en",
            ratio=1.2,
            cache_key="a1b2c3d4e5f6" + "0" * 52,  # 64 char hash
            audio_path="tts-audio/rem/20260903/9999/audio.mp3",
            alignment_path="tts-audio/rem/20260903/9999/alignment.json",
            audio_duration_seconds=5.5,
            synthesis_duration_seconds=2.3,
            cache_hit=True,
            alignment_duration_seconds=1.1,
            time_stretched=True,
            source_ratio=1.0,
            target_ratio=1.2,
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
        )

        # Verify all fields are set correctly
        assert job.job_id == 9999
        assert job.cache_hit is True
        assert job.alignment_duration_seconds == 1.1
        assert job.time_stretched is True
        assert job.source_ratio == 1.0
        assert job.target_ratio == 1.2
        assert job.cache_key.startswith("a1b2c3")

    def test_repr_still_works(self):
        """Test that __repr__ still works with new fields."""
        job = TTSJob(
            job_id=8888,
            job_type="studio",
            status="processing",
            cache_hit=True,
            time_stretched=False,
        )

        repr_str = repr(job)
        assert "8888" in repr_str
        assert "processing" in repr_str
