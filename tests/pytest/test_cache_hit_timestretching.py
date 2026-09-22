"""
Unit tests for SynthesisPipeline cache-hit time-stretching and eviction triggers.

Tests Phase 1 fixes:
- Fix #1: Time-stretching is applied on cache hit when speed_ratio != 1.0
- Fix #1: Cached base audio is copied without time-stretching when speed_ratio == 1.0
- Fix #2: maybe_evict() is invoked after successful cache store
"""

from unittest.mock import MagicMock, patch
import pytest

from services.tts.synthesis_pipeline import SynthesisPipeline


@pytest.fixture
def mock_pipeline():
    """Create a SynthesisPipeline with mocked dependencies."""
    mock_tts_engine = MagicMock()
    mock_storage_manager = MagicMock()
    mock_storage_manager.create_output_dir.return_value = "/tmp/mock_out"
    mock_storage_manager.build_s3_output_path.return_value = "tts/test/out.wav"

    mock_cache_manager = MagicMock()
    mock_cache_manager.enabled = True

    mock_job_service = MagicMock()
    mock_job_service.create_job_record.return_value = "test-job-uuid"

    with patch("services.tts.synthesis_pipeline.AudioProcessor"):
        pipeline = SynthesisPipeline(
            tts_engine=mock_tts_engine,
            storage_manager=mock_storage_manager,
            cache_manager=mock_cache_manager,
            job_service=mock_job_service,
        )
        # Mock run_alignment and uploads so we focus on cache & time-stretching
        pipeline._run_alignment = MagicMock(
            return_value=("/tmp/raw.json", "/tmp/sub.srt", "/tmp/align.json")
        )
        pipeline._extract_detected_language = MagicMock(return_value="en")
        pipeline._run_audio_upload = MagicMock(return_value="s3://bucket/audio.wav")
        pipeline._run_alignment_upload = MagicMock(
            return_value=("s3://bucket/align.json", 0.5)
        )
        yield pipeline


def test_cache_hit_ratio_1_copies_file(mock_pipeline):
    """When speed_ratio is 1.0 and cache hits, audio is copied without time-stretching."""
    pipeline = mock_pipeline
    cached_path = "/tmp/cache/hello_world.wav"
    pipeline.cache_manager.lookup.return_value = (True, cached_path, "cache-key-123")
    pipeline.audio_processor.copy_audio_file.return_value = "/tmp/mock_out/test_job.wav"
    pipeline.audio_processor.get_audio_duration.return_value = 2.5

    job_data = {
        "jobId": "job-001",
        "job_type": "studio",
        "text": "Hello world",
        "audioPromptPath": "audio-prompts/voice.wav",
        "speedRatio": 1.0,
        "language": "en",
    }

    result = pipeline.process_job(job_data)

    assert result["status"] == "completed"
    assert result["cacheHit"] is True

    # lookup was invoked
    pipeline.cache_manager.lookup.assert_called_once_with(
        "job-001", "Hello world", "audio-prompts/voice.wav", 1.0
    )

    # copy_audio_file called, apply_ratio_to_audio NOT called
    pipeline.audio_processor.copy_audio_file.assert_called_once_with(
        cached_path, "job-001", "/tmp/mock_out"
    )
    pipeline.audio_processor.apply_ratio_to_audio.assert_not_called()

    # DB update verified
    pipeline.job_service.update_job_status.assert_called_once()
    kwargs = pipeline.job_service.update_job_status.call_args[1]
    assert kwargs["cache_hit"] is True
    assert kwargs["time_stretched"] is False
    assert kwargs["source_ratio"] == 1.0
    assert kwargs["target_ratio"] == 1.0


def test_cache_hit_ratio_not_1_applies_timestretch(mock_pipeline):
    """When speed_ratio != 1.0 and cache hits, apply_ratio_to_audio is called on cached base audio."""
    pipeline = mock_pipeline
    cached_path = "/tmp/cache/hello_world.wav"
    pipeline.cache_manager.lookup.return_value = (True, cached_path, "cache-key-123")
    pipeline.audio_processor.apply_ratio_to_audio.return_value = (
        "/tmp/mock_out/test_job_stretched.wav"
    )
    pipeline.audio_processor.get_audio_duration.return_value = 1.8

    job_data = {
        "jobId": "job-002",
        "job_type": "studio",
        "text": "Hello world",
        "audioPromptPath": "audio-prompts/voice.wav",
        "speedRatio": 1.25,
        "language": "en",
    }

    result = pipeline.process_job(job_data)

    assert result["status"] == "completed"
    assert result["cacheHit"] is True

    # apply_ratio_to_audio called with speed_ratio=1.25
    pipeline.audio_processor.apply_ratio_to_audio.assert_called_once_with(
        cached_path, 1.25, "job-002", "/tmp/mock_out"
    )
    pipeline.audio_processor.copy_audio_file.assert_not_called()

    # DB update verified
    pipeline.job_service.update_job_status.assert_called_once()
    kwargs = pipeline.job_service.update_job_status.call_args[1]
    assert kwargs["cache_hit"] is True
    assert kwargs["time_stretched"] is True
    assert kwargs["source_ratio"] == 1.0
    assert kwargs["target_ratio"] == 1.25


def test_cache_miss_stores_and_calls_maybe_evict(mock_pipeline):
    """When cache misses, _run_synthesis runs, store() is called, and maybe_evict() is triggered."""
    pipeline = mock_pipeline
    pipeline.cache_manager.lookup.return_value = (False, None, None)
    pipeline.cache_manager.store.return_value = "new-cache-key-456"

    # Mock _run_synthesis
    pipeline._run_synthesis = MagicMock(
        return_value=(
            "/tmp/prompt.wav",
            "/tmp/mock_out/synthesized.wav",
            "new-cache-key-456",
        )
    )
    pipeline.audio_processor.get_audio_duration.return_value = 3.0

    job_data = {
        "jobId": "job-003",
        "job_type": "studio",
        "text": "Cache miss test",
        "audioPromptPath": "audio-prompts/voice.wav",
        "speedRatio": 1.0,
        "language": "en",
    }

    result = pipeline.process_job(job_data)

    assert result["status"] == "completed"
    assert result["cacheHit"] is False
    pipeline._run_synthesis.assert_called_once()


def test_run_synthesis_calls_store_and_maybe_evict(mock_pipeline):
    """_run_synthesis stores the base audio in cache and invokes maybe_evict."""
    pipeline = mock_pipeline
    pipeline._download_audio_prompt = MagicMock(return_value="/tmp/prompt.wav")
    pipeline._synthesize_audio = MagicMock(return_value="/tmp/mock_out/base.wav")
    pipeline.audio_processor.get_audio_duration.return_value = 3.2
    pipeline.audio_processor.apply_ratio_to_audio.return_value = "/tmp/mock_out/out.wav"
    pipeline.cache_manager.store.return_value = "cache-key-789"

    job_data = {"speedRatio": 1.5}

    prompt, output, key = pipeline._run_synthesis(
        job_id="job-004",
        text="Store and evict test",
        audio_prompt_path="audio-prompts/voice.wav",
        language="en",
        speed_ratio=1.5,
        job_data=job_data,
    )

    pipeline.cache_manager.store.assert_called_once_with(
        "job-004",
        "Store and evict test",
        "audio-prompts/voice.wav",
        "/tmp/mock_out/base.wav",
        3.2,
        pytest.approx(0.0, abs=5.0),
        "en",
    )
    pipeline.cache_manager.maybe_evict.assert_called_once_with("job-004")
