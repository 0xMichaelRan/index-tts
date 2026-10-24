"""
Unit tests for SynthesisPipeline cache-hit time-stretching and eviction triggers.

Updated for Phase 3 4-tuple lookup() return value:
    (cache_hit, cached_path_or_s3_key, cache_key, cached_alignment_s3_path)

Tests:
- Fix #1: Time-stretching is applied on cache hit when speed_ratio != 1.0
- Fix #1: Cached base audio is copied without time-stretching when speed_ratio == 1.0
- Fix #2: maybe_evict() is invoked after alignment is backfilled (not from _run_synthesis)
- Phase 3: L0 S3 key causes download_s3_to_local instead of direct copy
- Phase 3: Whisper is skipped when cached_alignment_s3_path is present on cache hit
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
    mock_storage_manager.download_s3_to_local.return_value = (
        "/tmp/mock_out/downloaded.wav"
    )

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
            alignment_service=MagicMock(),
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
    """When speed_ratio is 1.0 and cache hits (L1 local), audio is copied without time-stretching."""
    pipeline = mock_pipeline
    cached_path = "/tmp/cache/hello_world.wav"
    # 4-tuple: (hit, path, key, alignment_s3_path)
    pipeline.cache_manager.lookup.return_value = (
        True,
        cached_path,
        "cache-key-123",
        None,
    )
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
    pipeline.cache_manager.lookup.return_value = (
        True,
        cached_path,
        "cache-key-123",
        None,
    )
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


def test_cache_hit_s3_key_downloads_base_audio(mock_pipeline):
    """When cache returns an S3 key (L0), download_s3_to_local is called."""
    pipeline = mock_pipeline
    s3_key = "cache/tts/ab12/ab12deadbeef.wav"
    pipeline.cache_manager.lookup.return_value = (True, s3_key, "cache-key-123", None)
    pipeline.storage_manager.download_s3_to_local.return_value = (
        "/tmp/mock_out/job-003_base.wav"
    )
    pipeline.audio_processor.copy_audio_file.return_value = "/tmp/mock_out/job-003.wav"
    pipeline.audio_processor.get_audio_duration.return_value = 2.0

    job_data = {
        "jobId": "job-003",
        "job_type": "studio",
        "text": "S3 cache test",
        "audioPromptPath": "audio-prompts/voice.wav",
        "speedRatio": 1.0,
        "language": "en",
    }

    result = pipeline.process_job(job_data)

    assert result["status"] == "completed"
    assert result["cacheHit"] is True

    # S3 download was triggered
    pipeline.storage_manager.download_s3_to_local.assert_any_call(
        s3_key, "/tmp/mock_out", "job-003_base.wav"
    )
    # copy_audio_file called on the downloaded file (ratio=1.0)
    pipeline.audio_processor.copy_audio_file.assert_called_once_with(
        "/tmp/mock_out/job-003_base.wav", "job-003", "/tmp/mock_out"
    )


def test_cache_hit_with_alignment_skips_whisper(mock_pipeline):
    """When cache hit includes cached_alignment_s3_path, _run_alignment is NOT called."""
    pipeline = mock_pipeline
    cached_path = "/tmp/cache/hello_world.wav"
    cached_alignment = "cache/tts/ab12/ab12deadbeef_alignment.json"
    pipeline.cache_manager.lookup.return_value = (
        True,
        cached_path,
        "cache-key-123",
        cached_alignment,
    )
    pipeline.audio_processor.copy_audio_file.return_value = "/tmp/mock_out/job-004.wav"
    pipeline.audio_processor.get_audio_duration.return_value = 2.5
    pipeline.storage_manager.download_s3_to_local.return_value = (
        "/tmp/mock_out/job-004_alignment.json"
    )

    job_data = {
        "jobId": "job-004",
        "job_type": "studio",
        "text": "Alignment cache test",
        "audioPromptPath": "audio-prompts/voice.wav",
        "speedRatio": 1.0,
        "language": "en",
    }

    result = pipeline.process_job(job_data)

    assert result["status"] == "completed"
    assert result["cacheHit"] is True

    # Whisper NOT called
    pipeline._run_alignment.assert_not_called()

    # Alignment downloaded from S3
    pipeline.storage_manager.download_s3_to_local.assert_any_call(
        cached_alignment, "/tmp/mock_out", "job-004_alignment.json"
    )

    # _run_alignment_upload NOT called (reused from cache)
    pipeline._run_alignment_upload.assert_not_called()


def test_cache_miss_stores_and_calls_maybe_evict(mock_pipeline):
    """When cache misses, _run_synthesis runs and maybe_evict() is triggered after alignment backfill."""
    pipeline = mock_pipeline
    pipeline.cache_manager.lookup.return_value = (False, None, None, None)
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
        "jobId": "job-005",
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

    # maybe_evict called from process_job (not from _run_synthesis)
    pipeline.cache_manager.maybe_evict.assert_called_once_with("job-005")
    # alignment backfilled
    pipeline.cache_manager.update_alignment_s3_path.assert_called_once_with(
        "job-005", "new-cache-key-456", "s3://bucket/align.json"
    )


def test_run_synthesis_calls_store_not_evict(mock_pipeline):
    """_run_synthesis stores the base audio in cache but does NOT call maybe_evict (moved to process_job)."""
    pipeline = mock_pipeline
    pipeline._download_audio_prompt = MagicMock(return_value="/tmp/prompt.wav")
    pipeline._synthesize_audio = MagicMock(return_value="/tmp/mock_out/base.wav")
    pipeline.audio_processor.get_audio_duration.return_value = 3.2
    pipeline.audio_processor.apply_ratio_to_audio.return_value = "/tmp/mock_out/out.wav"
    pipeline.cache_manager.store.return_value = "cache-key-789"

    job_data = {"speedRatio": 1.5}

    prompt, output, key = pipeline._run_synthesis(
        job_id="job-006",
        text="Store and evict test",
        audio_prompt_path="audio-prompts/voice.wav",
        language="en",
        speed_ratio=1.5,
        job_data=job_data,
    )

    pipeline.cache_manager.store.assert_called_once_with(
        "job-006",
        "Store and evict test",
        "audio-prompts/voice.wav",
        "/tmp/mock_out/base.wav",
        3.2,
        pytest.approx(0.0, abs=5.0),
        "en",
    )
    # maybe_evict NOT called from _run_synthesis anymore
    pipeline.cache_manager.maybe_evict.assert_not_called()
