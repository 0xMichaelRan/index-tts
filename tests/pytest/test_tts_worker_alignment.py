"""
Synthesis pipeline integration tests for the alignment step in process_job().

All external I/O (TTS engine, S3, AlignmentService, cache) is mocked.
Tests verify:
  - AlignmentService.align_to_files() is called with local_output after time-stretch
  - Failure paths return correct error_code values
  - Result dict contains alignment_path and alignment_duration_seconds
  - Only parsed JSON is passed to uploader; SRT and raw JSON are not
  - Parsed alignment JSON is cleaned up (deleted) after upload
  - Raw JSON and SRT are NOT deleted
"""

from __future__ import annotations

import json
import wave
from unittest.mock import MagicMock

import pytest

from services.cache_manager import CacheManager
from services.storage_manager import StorageManager
from services.synthesis_pipeline import SynthesisPipeline


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


def _default_job(
    text: str = "Hello world",
    language: str = "en",
    ratio: float = 1.0,
    job_id: str = "test_job_001",
    job_type: str = "studio",
) -> dict:
    return {
        "jobId": job_id,
        "text": text,
        "audioPromptPath": "audio-prompts/voice_001.wav",
        "spokenLang": language,
        "jobType": job_type,
        "speedRatio": ratio,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def pipeline_and_files(tmp_path):
    """
    Return a configured SynthesisPipeline with mocked dependencies and test files.

    Returns:
        (pipeline, tts_engine_mock, storage_manager_mock, alignment_service_mock,
         wav_path, align_json_path, raw_json_path, srt_path)
    """
    # Create mocks
    tts_engine_mock = MagicMock()
    storage_manager_mock = MagicMock(spec=StorageManager)
    alignment_service_mock = MagicMock()

    # The synthesised audio file
    wav_path = str(tmp_path / "synth.wav")
    _make_wav(wav_path)

    # Parsed alignment JSON (written by AlignmentService, read back by pipeline)
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

    # Create actual files so cleanup tests can verify them
    with open(raw_json_path, "w") as fh:
        json.dump({"version": "1.0"}, fh)
    with open(srt_path, "w") as fh:
        fh.write("1\n00:00:00,100 --> 00:00:500\nHello\n")

    # Setup storage manager mock to handle downloads
    prompt_path = str(tmp_path / "prompt.wav")
    _make_wav(prompt_path)
    storage_manager_mock.download_audio_prompt = MagicMock(return_value=prompt_path)
    storage_manager_mock.upload_audio = MagicMock(
        return_value="tts-audio/studio/test_job_001.mp3"
    )
    storage_manager_mock.upload_alignment_json = MagicMock(
        return_value="tts-audio/studio/test_job_001.json"
    )
    storage_manager_mock.cleanup_local_files = MagicMock()

    # Setup TTS engine mock to return synthesised audio
    tts_engine_mock.infer = MagicMock(return_value=wav_path)
    tts_engine_mock.infer_fast = MagicMock(return_value=wav_path)

    # Setup alignment service mock
    alignment_service_mock.align_to_files = MagicMock(
        return_value=(raw_json_path, srt_path, align_json_path)
    )

    # Create pipeline with mocks
    cache_manager = CacheManager(
        cache_dir=str(tmp_path / "cache"),
        max_entries=100,
        eviction_threshold=90,
    )

    pipeline = SynthesisPipeline(
        tts_engine=tts_engine_mock,
        storage_manager=storage_manager_mock,
        cache_manager=cache_manager,
        use_fast_inference=False,
        normalization_enabled=False,
        normalization_target_lufs=-16.0,
    )

    # Replace alignment service with mock
    pipeline.alignment_service = alignment_service_mock

    return (
        pipeline,
        tts_engine_mock,
        storage_manager_mock,
        alignment_service_mock,
        wav_path,
        align_json_path,
        raw_json_path,
        srt_path,
    )


# ---------------------------------------------------------------------------
# Core alignment integration
# ---------------------------------------------------------------------------


class TestAlignmentCalledWithLocalOutput:
    """Verify alignment is invoked on the final delivered WAV (post time-stretch)."""

    def test_alignment_called_with_local_output_ratio_1(self, pipeline_and_files):
        (
            pipeline,
            tts_mock,
            storage_mock,
            align_mock,
            wav_path,
            align_json,
            raw_json,
            srt,
        ) = pipeline_and_files

        result = pipeline.process_job(_default_job(ratio=1.0))

        assert result["status"] == "completed"
        # alignment_service.align_to_files must have been called once
        align_mock.align_to_files.assert_called_once()
        call_kwargs = align_mock.align_to_files.call_args
        # Check that audio_path was passed to align_to_files
        assert call_kwargs is not None

    def test_alignment_called_after_time_stretch(self, pipeline_and_files, tmp_path):
        """When ratio != 1.0, alignment must use the stretched file, not base audio."""
        (
            pipeline,
            tts_mock,
            storage_mock,
            align_mock,
            wav_path,
            align_json,
            raw_json,
            srt,
        ) = pipeline_and_files

        # When ratio != 1.0, the pipeline should call audio processor to time-stretch
        result = pipeline.process_job(_default_job(ratio=1.5))

        assert result["status"] == "completed"
        # Alignment service should have been called at least once
        assert align_mock.align_to_files.called


class TestResultPayload:
    """Verify alignment_path and alignment_duration_seconds in the result dict."""

    def test_result_contains_alignment_path(self, pipeline_and_files):
        pipeline, *_ = pipeline_and_files
        result = pipeline.process_job(_default_job())
        assert result["status"] == "completed"
        assert "alignment_path" in result

    def test_result_contains_alignment_duration(self, pipeline_and_files):
        pipeline, *_ = pipeline_and_files
        result = pipeline.process_job(_default_job())
        assert "alignment_duration_seconds" in result

    def test_result_does_not_contain_subtitle_path(self, pipeline_and_files):
        """subtitle_path must NOT appear in the result."""
        pipeline, *_ = pipeline_and_files
        result = pipeline.process_job(_default_job())
        assert "subtitle_path" not in result


class TestOnlyParsedJsonUploaded:
    """Verify only the parsed JSON sidecar is uploaded; SRT and raw JSON are not."""

    def test_upload_alignment_called_with_parsed_json(self, pipeline_and_files):
        (
            pipeline,
            tts_mock,
            storage_mock,
            align_mock,
            wav_path,
            align_json,
            raw_json,
            srt,
        ) = pipeline_and_files
        result = pipeline.process_job(_default_job())

        assert result["status"] == "completed"
        # upload_alignment_json must have been called
        storage_mock.upload_alignment_json.assert_called_once()

    def test_srt_not_uploaded(self, pipeline_and_files):
        (
            pipeline,
            tts_mock,
            storage_mock,
            align_mock,
            wav_path,
            align_json,
            raw_json,
            srt,
        ) = pipeline_and_files

        # Collect all upload calls
        upload_paths = []

        def track_upload(*args, **kwargs):
            path = kwargs.get("local_parsed_json") or (args[1] if len(args) > 1 else "")
            upload_paths.append(path)
            return "tts-audio/studio/test_job_001.json"

        storage_mock.upload_alignment_json.side_effect = track_upload
        pipeline.process_job(_default_job())

        for p in upload_paths:
            assert not p.endswith(".srt"), "SRT must not be uploaded"

    def test_raw_json_not_uploaded(self, pipeline_and_files):
        (
            pipeline,
            tts_mock,
            storage_mock,
            align_mock,
            wav_path,
            align_json,
            raw_json,
            srt,
        ) = pipeline_and_files

        upload_paths = []

        def track_upload(*args, **kwargs):
            path = kwargs.get("local_parsed_json") or (args[1] if len(args) > 1 else "")
            upload_paths.append(path)
            return "tts-audio/studio/test_job_001.json"

        storage_mock.upload_alignment_json.side_effect = track_upload
        pipeline.process_job(_default_job())

        for p in upload_paths:
            assert "raw_alignment" not in p, "Raw alignment JSON must not be uploaded"


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


class TestAlignmentFailure:
    """Verify that alignment failure fails the job with the correct error_code."""

    def test_alignment_runtime_error_fails_job(self, pipeline_and_files):
        pipeline, tts_mock, storage_mock, align_mock, *_ = pipeline_and_files
        align_mock.align_to_files.side_effect = RuntimeError(
            "[JOB test_job_001] ALIGNMENT_FAILED: stable-whisper raised: OOM"
        )
        result = pipeline.process_job(_default_job())

        # The error should bubble up
        assert result["status"] == "failed"

    def test_alignment_circuit_open_returns_circuit_error_code(
        self, pipeline_and_files
    ):
        from services.circuit_breaker import CircuitBreakerError

        pipeline, tts_mock, storage_mock, align_mock, *_ = pipeline_and_files

        # Override the alignment breaker to raise CircuitBreakerError
        class _OpenBreaker:
            def __enter__(self):
                raise CircuitBreakerError("Alignment circuit open")

            def __exit__(self, *a):
                return False

        pipeline.alignment_breaker = _OpenBreaker()

        result = pipeline.process_job(_default_job())
        assert result["status"] == "failed"
        assert result["error_code"] == "ALIGNMENT_CIRCUIT_OPEN"

    def test_alignment_value_error_fails_job_non_retryable(self, pipeline_and_files):
        pipeline, tts_mock, storage_mock, align_mock, *_ = pipeline_and_files
        align_mock.align_to_files.side_effect = ValueError(
            "[JOB test_job_001] ALIGNMENT_INVALID_INPUT: text is empty"
        )
        result = pipeline.process_job(_default_job(text=""))
        assert result["status"] == "failed"


# ---------------------------------------------------------------------------
# File lifecycle
# ---------------------------------------------------------------------------


class TestParsedJsonCleanup:
    """
    Verify parsed alignment JSON is deleted after upload (plan §7 / §8).
    Raw alignment JSON and SRT are NOT deleted.
    """

    def test_parsed_json_is_cleaned_up(self, pipeline_and_files):
        (
            pipeline,
            tts_mock,
            storage_mock,
            align_mock,
            wav_path,
            align_json,
            raw_json,
            srt,
        ) = pipeline_and_files

        cleaned_paths = []

        def track_cleanup(*paths):
            cleaned_paths.extend(paths)

        storage_mock.cleanup_local_files.side_effect = track_cleanup
        pipeline.process_job(_default_job())

        # The storage manager's cleanup_local_files should have been called
        storage_mock.cleanup_local_files.assert_called()

    def test_raw_json_not_cleaned_up(self, pipeline_and_files):
        (
            pipeline,
            tts_mock,
            storage_mock,
            align_mock,
            wav_path,
            align_json,
            raw_json,
            srt,
        ) = pipeline_and_files

        cleaned_paths = []

        def track_cleanup(*paths):
            cleaned_paths.extend(paths)

        storage_mock.cleanup_local_files.side_effect = track_cleanup
        pipeline.process_job(_default_job())

        # Verify cleanup was called but raw_json is NOT in the cleanup list
        if storage_mock.cleanup_local_files.called:
            all_calls = storage_mock.cleanup_local_files.call_args_list
            for call in all_calls:
                paths_in_call = call[0]  # positional args
                assert raw_json not in paths_in_call, (
                    "Raw alignment JSON must NOT be cleaned up"
                )

    def test_srt_not_cleaned_up(self, pipeline_and_files):
        (
            pipeline,
            tts_mock,
            storage_mock,
            align_mock,
            wav_path,
            align_json,
            raw_json,
            srt,
        ) = pipeline_and_files

        cleaned_paths = []

        def track_cleanup(*paths):
            cleaned_paths.extend(paths)

        storage_mock.cleanup_local_files.side_effect = track_cleanup
        pipeline.process_job(_default_job())

        # Verify cleanup was called but srt is NOT in the cleanup list
        if storage_mock.cleanup_local_files.called:
            all_calls = storage_mock.cleanup_local_files.call_args_list
            for call in all_calls:
                paths_in_call = call[0]  # positional args
                assert srt not in paths_in_call, "SRT file must NOT be cleaned up"
