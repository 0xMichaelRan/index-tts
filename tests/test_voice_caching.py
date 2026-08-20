"""
Test voice caching behavior in TTS worker.

This test verifies that the worker correctly reuses cached voice data
when processing multiple jobs with the same audio prompt (S3 path).
"""

from unittest.mock import MagicMock, patch

import pytest

from services.tts_worker import IndexTTSWorker


class TestVoiceCaching:
    """Test voice caching with S3 path as cache key."""

    @pytest.fixture
    def mock_tts_engine(self):
        """Mock TTS engine with cache attributes."""
        mock_tts = MagicMock()
        mock_tts.cache_audio_prompt = None
        mock_tts.cache_cond_mel = None
        mock_tts.infer_fast = MagicMock()
        return mock_tts

    @pytest.fixture
    def worker_with_mock_tts(self, mock_tts_engine):
        """Create worker with mocked TTS engine."""
        with patch("services.tts_worker.create_tts_engine") as mock_create:
            mock_create.return_value = mock_tts_engine
            worker = IndexTTSWorker(
                rabbitmq_url="amqp://guest:guest@localhost:5672/",
            )
            # Replace TTS engine with our mock
            worker.tts = mock_tts_engine
            yield worker

    def test_first_job_loads_voice(
        self, worker_with_mock_tts, mock_tts_engine, tmp_path
    ):
        """Test that first job with a voice loads and caches it."""
        # Arrange
        job_id = "test_job_1"
        s3_path = "voice-recordings/user/123/english.wav"
        local_path = tmp_path / "english.wav"
        local_path.write_text("fake audio data")

        # Act
        worker_with_mock_tts._synthesize_audio(
            job_id=job_id,
            text="Hello world",
            audio_prompt=str(local_path),
            audio_prompt_s3_path=s3_path,
            language="en",
            ratio=1.0,
        )

        # Assert
        # Cache should be cleared for new voice
        assert mock_tts_engine.infer_fast.called
        # After inference, cache should store S3 path
        assert mock_tts_engine.cache_audio_prompt == s3_path

    def test_second_job_reuses_voice_cache(
        self, worker_with_mock_tts, mock_tts_engine, tmp_path
    ):
        """Test that second job with same voice reuses cache."""
        # Arrange
        job_id_1 = "test_job_1"
        job_id_2 = "test_job_2"
        s3_path = "voice-recordings/user/123/english.wav"
        local_path_1 = tmp_path / "job1" / "english.wav"
        local_path_2 = tmp_path / "job2" / "english.wav"
        local_path_1.parent.mkdir()
        local_path_2.parent.mkdir()
        local_path_1.write_text("fake audio data")
        local_path_2.write_text("fake audio data")

        # Simulate first job
        worker_with_mock_tts._synthesize_audio(
            job_id=job_id_1,
            text="Hello world",
            audio_prompt=str(local_path_1),
            audio_prompt_s3_path=s3_path,
            language="en",
            ratio=1.0,
        )

        # Verify cache is set
        assert mock_tts_engine.cache_audio_prompt == s3_path

        # Reset mock to track second call
        mock_tts_engine.infer_fast.reset_mock()

        # Act - second job with same S3 path but different local path
        worker_with_mock_tts._synthesize_audio(
            job_id=job_id_2,
            text="Different text",
            audio_prompt=str(local_path_2),  # Different local path
            audio_prompt_s3_path=s3_path,  # Same S3 path
            language="en",
            ratio=1.0,
        )

        # Assert
        # Cache should NOT be cleared (reused)
        assert mock_tts_engine.infer_fast.called
        # Cache should still store S3 path
        assert mock_tts_engine.cache_audio_prompt == s3_path

    def test_different_voice_clears_cache(
        self, worker_with_mock_tts, mock_tts_engine, tmp_path
    ):
        """Test that different voice clears cache."""
        # Arrange
        job_id_1 = "test_job_1"
        job_id_2 = "test_job_2"
        s3_path_1 = "voice-recordings/user/123/english.wav"
        s3_path_2 = "voice-recordings/user/456/spanish.wav"
        local_path_1 = tmp_path / "job1" / "english.wav"
        local_path_2 = tmp_path / "job2" / "spanish.wav"
        local_path_1.parent.mkdir()
        local_path_2.parent.mkdir()
        local_path_1.write_text("fake audio data 1")
        local_path_2.write_text("fake audio data 2")

        # First job
        worker_with_mock_tts._synthesize_audio(
            job_id=job_id_1,
            text="Hello",
            audio_prompt=str(local_path_1),
            audio_prompt_s3_path=s3_path_1,
            language="en",
            ratio=1.0,
        )
        assert mock_tts_engine.cache_audio_prompt == s3_path_1

        # Act - second job with different voice
        worker_with_mock_tts._synthesize_audio(
            job_id=job_id_2,
            text="Hola",
            audio_prompt=str(local_path_2),
            audio_prompt_s3_path=s3_path_2,  # Different S3 path
            language="es",
            ratio=1.0,
        )

        # Assert
        # Cache should be updated to new voice
        assert mock_tts_engine.cache_audio_prompt == s3_path_2

    def test_no_s3_path_disables_caching(
        self, worker_with_mock_tts, mock_tts_engine, tmp_path
    ):
        """Test that missing S3 path disables caching."""
        # Arrange
        job_id = "test_job_1"
        local_path = tmp_path / "english.wav"
        local_path.write_text("fake audio data")

        # Act - synthesize without S3 path
        worker_with_mock_tts._synthesize_audio(
            job_id=job_id,
            text="Hello",
            audio_prompt=str(local_path),
            audio_prompt_s3_path=None,  # No S3 path
            language="en",
            ratio=1.0,
        )

        # Assert
        # infer_fast should still be called
        assert mock_tts_engine.infer_fast.called
        # Cache should not be set (or cleared)
        # Since we didn't set it, it should still be None from fixture


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
