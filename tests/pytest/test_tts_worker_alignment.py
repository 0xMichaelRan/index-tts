"""
Worker integration tests for the alignment step in process_job().

All external I/O (TTS engine, S3, RabbitMQ, AlignmentService, cache) is mocked.
Tests verify:
  - AlignmentService.align_to_files() is called with local_output after time-stretch
  - Failure paths return correct error_code values
  - Result dict contains alignment_path and alignment_duration_seconds
  - Only parsed JSON is passed to IdempotentUploader; SRT and raw JSON are not
  - Parsed alignment JSON is cleaned up (deleted) after upload
  - Raw JSON and SRT are NOT deleted
"""

from __future__ import annotations

import json
import os
import tempfile
import wave
from unittest.mock import MagicMock, Mock, patch, call

import pytest

from services.tts_worker import IndexTTSWorker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_wav(tmp_path: str, duration_s: float = 2.0, sample_rate: int = 24000) -> str:
    """Write a minimal WAV file and return its path."""
    n = int(duration_s * sample_rate)
    with wave.open(tmp_path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * n)
    return tmp_path


def _make_worker() -> IndexTTSWorker:
    """
    Build an IndexTTSWorker without triggering real TTS / S3 / alignment init.
    """
    worker = IndexTTSWorker.__new__(IndexTTSWorker)

    # Core state expected by process_job
    worker.platform = "Linux"
    worker.rabbitmq_url = "amqp://guest:guest@localhost:5672/"
    worker.rabbitmq_host = "localhost"
    worker.s3_storage_bucket = "test-storage"
    worker.s3_output_bucket = "test-output"
    worker.cache_dir = "outputs/tts_cache"
    worker.cache_enabled = False
    worker.use_fast_inference = False
    worker.normalization_enabled = False
    worker.normalization_target_lufs = -16.0
    worker._shutdown_requested = False
    worker._processed_jobs = set()
    worker._reconnect_delay = 5
    worker._max_reconnect_delay = 300
    worker._reconnect_attempts = 0

    # Service mocks
    worker.tts = MagicMock()
    worker.s3_client = MagicMock()
    worker.uploader = MagicMock()
    worker.connection = None
    worker.channel = None

    # Circuit breakers (all closed / pass-through)
    def _passthrough_breaker():
        class _CBCtx:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return MagicMock(__enter__=lambda s: s, __exit__=lambda s, *a: False)

    worker.s3_breaker = _passthrough_breaker()
    worker.tts_breaker = _passthrough_breaker()
    worker.alignment_breaker = _passthrough_breaker()

    # Alignment service mock
    worker.alignment_service = MagicMock()

    return worker


def _default_job(
    text: str = "Hello world",
    language: str = "en",
    ratio: float = 1.0,
    job_id: str = "test_job_001",
    job_type: str = "studio",
) -> dict:
    return {
        "job_id": job_id,
        "text": text,
        "audio_prompt_path": "audio-prompts/voice_001.wav",
        "language": language,
        "jobType": job_type,
        "output_path_template": "tts-audio/studio/{job_id}.mp3",
        "ratio": ratio,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def worker(tmp_path):
    """Return a configured mock worker with a real temporary WAV as local_output."""
    w = _make_worker()

    # The synthesised audio file
    wav_path = str(tmp_path / "synth.wav")
    _make_wav(wav_path)

    # Parsed alignment JSON (written by AlignmentService, read back by worker)
    align_json_path = str(tmp_path / "test_job_001_alignment.json")
    align_data = {
        "version": "1.0",
        "job_id": "test_job_001",
        "alignment_duration_seconds": 1.23,
        "words": [{"word": "Hello", "start": 0.1, "end": 0.5, "probability": 0.99}],
    }
    with open(align_json_path, "w") as fh:
        json.dump(align_data, fh)

    # SRT and raw JSON paths (returned by align_to_files but NOT uploaded)
    raw_json_path = str(tmp_path / "test_job_001_raw_alignment.json")
    srt_path = str(tmp_path / "test_job_001_alignment.srt")

    # Patch _synthesize_audio to return the fake WAV
    w._synthesize_audio = MagicMock(return_value=wav_path)

    # Patch _download_audio_prompt
    prompt_path = str(tmp_path / "prompt.wav")
    _make_wav(prompt_path)
    w._download_audio_prompt = MagicMock(return_value=prompt_path)

    # alignment_service.align_to_files returns (raw_json, srt, parsed_json)
    w.alignment_service.align_to_files = MagicMock(
        return_value=(raw_json_path, srt_path, align_json_path)
    )

    # S3 upload mocks
    w._upload_to_s3_idempotent = MagicMock(
        return_value="tts-audio/studio/test_job_001.mp3"
    )
    w._upload_alignment = MagicMock(return_value="tts-audio/studio/test_job_001.json")

    return w, wav_path, align_json_path, raw_json_path, srt_path


# ---------------------------------------------------------------------------
# Core alignment integration
# ---------------------------------------------------------------------------


class TestAlignmentCalledWithLocalOutput:
    """Verify alignment is invoked on the final delivered WAV (post time-stretch)."""

    def test_alignment_called_with_local_output_ratio_1(self, worker, tmp_path):
        w, wav_path, align_json, raw_json, srt = worker

        result = w.process_job(_default_job(ratio=1.0))

        assert result["status"] == "completed"
        # alignment_service.align_to_files must have been called once
        w.alignment_service.align_to_files.assert_called_once()
        call_kwargs = w.alignment_service.align_to_files.call_args
        # First positional or keyword arg is job_id; audio_path must be the WAV
        assert call_kwargs.kwargs.get("audio_path") == wav_path or (
            call_kwargs.args and call_kwargs.args[1] == wav_path
        )

    def test_alignment_called_after_time_stretch(self, worker, tmp_path):
        """When ratio != 1.0, alignment must use the stretched file, not base audio."""
        w, wav_path, align_json, raw_json, srt = worker

        # Simulate time-stretch returning a different path
        stretched_path = str(tmp_path / "stretched.wav")
        _make_wav(stretched_path, duration_s=1.33)  # faster
        w._apply_ratio_to_cached_audio = MagicMock(return_value=stretched_path)
        w._synthesize_audio = MagicMock(return_value=wav_path)

        # Re-assign align_to_files return (still uses align_json_path from fixture)
        w.alignment_service.align_to_files = MagicMock(
            return_value=(raw_json, srt, align_json)
        )
        w._upload_to_s3_idempotent = MagicMock(
            return_value="tts-audio/studio/test_job_001.mp3"
        )
        w._upload_alignment = MagicMock(
            return_value="tts-audio/studio/test_job_001.json"
        )

        result = w.process_job(_default_job(ratio=1.5))

        assert result["status"] == "completed"
        call_kwargs = w.alignment_service.align_to_files.call_args
        # Must be called with the stretched path, not wav_path
        actual_audio = call_kwargs.kwargs.get("audio_path") or call_kwargs.args[1]
        assert actual_audio == stretched_path


class TestResultPayload:
    """Verify alignment_path and alignment_duration_seconds in the result dict."""

    def test_result_contains_alignment_path(self, worker):
        w, *_ = worker
        result = w.process_job(_default_job())
        assert result["status"] == "completed"
        assert "alignment_path" in result
        assert result["alignment_path"] == "tts-audio/studio/test_job_001.json"

    def test_result_contains_alignment_duration(self, worker):
        w, *_ = worker
        result = w.process_job(_default_job())
        assert "alignment_duration_seconds" in result
        assert abs(result["alignment_duration_seconds"] - 1.23) < 0.01

    def test_result_does_not_contain_subtitle_path(self, worker):
        """subtitle_path must NOT appear in the RabbitMQ result (plan §7)."""
        w, *_ = worker
        result = w.process_job(_default_job())
        assert "subtitle_path" not in result


class TestOnlyParsedJsonUploaded:
    """Verify only the parsed JSON sidecar is uploaded; SRT and raw JSON are not."""

    def test_upload_alignment_called_with_parsed_json(self, worker, tmp_path):
        w, wav_path, align_json, raw_json, srt = worker
        result = w.process_job(_default_job())

        assert result["status"] == "completed"
        # _upload_alignment must have been called with the parsed JSON path
        w._upload_alignment.assert_called_once()
        call_kwargs = w._upload_alignment.call_args
        actual_json = call_kwargs.kwargs.get("local_parsed_json") or call_kwargs.args[1]
        assert actual_json == align_json

    def test_srt_not_uploaded(self, worker):
        w, wav_path, align_json, raw_json, srt = worker

        # Collect all upload calls
        upload_paths = []

        def track_upload(*args, **kwargs):
            path = kwargs.get("local_parsed_json") or (args[1] if len(args) > 1 else "")
            upload_paths.append(path)
            return "tts-audio/studio/test_job_001.json"

        w._upload_alignment.side_effect = track_upload
        w.process_job(_default_job())

        for p in upload_paths:
            assert not p.endswith(".srt"), "SRT must not be uploaded"

    def test_raw_json_not_uploaded(self, worker):
        w, wav_path, align_json, raw_json, srt = worker

        upload_paths = []

        def track_upload(*args, **kwargs):
            path = kwargs.get("local_parsed_json") or (args[1] if len(args) > 1 else "")
            upload_paths.append(path)
            return "tts-audio/studio/test_job_001.json"

        w._upload_alignment.side_effect = track_upload
        w.process_job(_default_job())

        for p in upload_paths:
            assert "raw_alignment" not in p, "Raw alignment JSON must not be uploaded"


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


class TestAlignmentFailure:
    """Verify that alignment failure fails the job with the correct error_code."""

    def test_alignment_runtime_error_fails_job(self, worker):
        w, *_ = worker
        w.alignment_service.align_to_files.side_effect = RuntimeError(
            "[JOB test_job_001] ALIGNMENT_FAILED: stable-whisper raised: OOM"
        )
        result = w.process_job(_default_job())

        # The error should bubble up through the non-retryable except clause
        assert result["status"] == "failed"

    def test_alignment_circuit_open_returns_circuit_error_code(self, worker):
        from services.circuit_breaker import CircuitBreakerError

        w, *_ = worker

        # Override the alignment breaker context manager to raise CircuitBreakerError
        class _OpenBreaker:
            def __enter__(self):
                raise CircuitBreakerError("Alignment circuit open")

            def __exit__(self, *a):
                return False

        w.alignment_breaker = _OpenBreaker()

        result = w.process_job(_default_job())
        assert result["status"] == "failed"
        assert result["error_code"] == "ALIGNMENT_CIRCUIT_OPEN"

    def test_alignment_value_error_fails_job_non_retryable(self, worker):
        w, *_ = worker
        w.alignment_service.align_to_files.side_effect = ValueError(
            "[JOB test_job_001] ALIGNMENT_INVALID_INPUT: text is empty"
        )
        result = w.process_job(_default_job(text=""))
        assert result["status"] == "failed"


# ---------------------------------------------------------------------------
# File lifecycle
# ---------------------------------------------------------------------------


class TestParsedJsonCleanup:
    """
    Verify parsed alignment JSON is deleted after upload (plan §7 / §8).
    Raw alignment JSON and SRT are NOT deleted.
    """

    def test_parsed_json_is_cleaned_up(self, worker, tmp_path):
        w, wav_path, align_json, raw_json, srt = worker

        cleaned_paths = []
        original_cleanup = w._cleanup_local_files

        def track_cleanup(*paths):
            cleaned_paths.extend(paths)

        w._cleanup_local_files = track_cleanup
        w.process_job(_default_job())

        assert align_json in cleaned_paths, "Parsed alignment JSON must be cleaned up"

    def test_raw_json_not_cleaned_up(self, worker, tmp_path):
        w, wav_path, align_json, raw_json, srt = worker

        cleaned_paths = []

        def track_cleanup(*paths):
            cleaned_paths.extend(paths)

        w._cleanup_local_files = track_cleanup
        w.process_job(_default_job())

        assert raw_json not in cleaned_paths, (
            "Raw alignment JSON must NOT be cleaned up"
        )

    def test_srt_not_cleaned_up(self, worker, tmp_path):
        w, wav_path, align_json, raw_json, srt = worker

        cleaned_paths = []

        def track_cleanup(*paths):
            cleaned_paths.extend(paths)

        w._cleanup_local_files = track_cleanup
        w.process_job(_default_job())

        assert srt not in cleaned_paths, "SRT file must NOT be cleaned up"
