"""
Unit tests for TTSJob model, TTSJobService, and ttsId handling.
"""

from datetime import datetime, timezone
from unittest.mock import patch

from app.models import TTSJob
from services.tts_job_service import TTSJobService
from services.synthesis_pipeline import SynthesisPipeline


def test_tts_job_model_attributes():
    """Test TTSJob model structure and properties."""
    job = TTSJob(
        job_id=12345,
        job_type="rem",
        status="processing",
        text="Testing three ID system",
        audio_prompt_path="prompts/voice.wav",
        language="en",
        ratio=1.2,
        started_at=datetime.now(timezone.utc),
    )
    assert job.job_id == 12345
    assert job.job_type == "rem"
    assert job.status == "processing"
    assert job.ratio == 1.2
    assert "12345" in repr(job)


def test_tts_job_service_test_job_skips_db():
    """Test that isTest=True skips database operations."""
    service = TTSJobService()
    job_data = {
        "jobId": 99999,
        "isTest": True,
        "text": "Hello test",
    }
    tts_id = service.create_job_record(job_data)
    assert tts_id is None


def test_tts_job_service_create_and_update():
    """Test creating and updating a TTS job record."""
    service = TTSJobService()
    service.enabled = True

    with (
        patch("services.tts_job_service.DatabaseSession"),
        patch("services.tts_job_service._run_coroutine") as mock_run,
    ):
        # Mock _run_coroutine to simulate return of tts_id
        def mock_runner(coro, timeout=10.0):
            coro.close()
            return 42

        mock_run.side_effect = mock_runner

        job_data = {
            "jobId": 1001,
            "jobType": "studio",
            "text": "Hello world",
            "audioPromptPath": "voice/1.wav",
            "speedRatio": 1.0,
            "spokenLang": "en",
        }

        tts_id = service.create_job_record(job_data)
        assert tts_id == 42
        mock_run.assert_called_once()


def test_synthesis_pipeline_build_results_includes_tts_id():
    """Test that _build_success_result and _build_failure_result include ttsId when present."""
    job_started_at = datetime.now()
    job_data = {
        "jobId": 8888,
        "ttsId": 321,
        "jobType": "rem",
        "remotionStyle": "cyber-kinetic",
        "resolution": "720p",
        "aspectRatio": "16x9",
        "spokenLang": "en",
    }

    success_result = SynthesisPipeline._build_success_result(
        job_type="rem",
        job_id="8888",
        audio_path="rem/20260903/8888/audio.mp3",
        audio_duration=5.5,
        alignment_s3_path="rem/20260903/8888/audio.json",
        alignment_duration_seconds=1.2,
        job_started_at=job_started_at,
        cache_hit=False,
        retry_count=0,
        job_data=job_data,
    )

    assert success_result["jobId"] == "8888"
    assert success_result["ttsId"] == 321
    assert success_result["jobType"] == "rem"
    assert success_result["remotionStyle"] == "cyber-kinetic"
    assert success_result["status"] == "completed"

    failure_result = SynthesisPipeline._build_failure_result(
        job_type="rem",
        job_id="8888",
        error_code="TEST_ERROR",
        error_message="Failure description",
        retry_count=1,
        job_started_at=job_started_at,
        job_data=job_data,
    )

    assert failure_result["jobId"] == "8888"
    assert failure_result["ttsId"] == 321
    assert failure_result["status"] == "failed"
    assert failure_result["errorCode"] == "TEST_ERROR"
