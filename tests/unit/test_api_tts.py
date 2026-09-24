from __future__ import annotations

import os
import wave
from unittest.mock import MagicMock
import pytest
from fastapi.testclient import TestClient

from services.api.app import app
from services.api.deps import get_tts_engine, get_tts_pipeline


def _create_dummy_wav(path: str):
    """Create a minimal valid WAV file at the given path."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 160)


@pytest.fixture
def client():
    app.dependency_overrides.clear()
    yield TestClient(app)
    app.dependency_overrides.clear()


class TestTTSSynthesizeEndpoint:
    def test_synthesize_success(self, client):
        mock_pipeline = MagicMock()
        mock_pipeline.process_job.return_value = {
            "jobId": "test-job-1",
            "jobType": "studio",
            "status": "completed",
            "audioPath": "s3://klatu-tts/test-job-1/audio.wav",
            "audioDurationSeconds": 2.5,
            "synthesisDurationSeconds": 1.1,
            "alignmentPath": "s3://klatu-tts/test-job-1/alignment.json",
            "alignmentDurationSeconds": 0.4,
            "startedAt": "2026-09-24T12:00:00Z",
            "completedAt": "2026-09-24T12:00:02Z",
            "cacheHit": False,
            "retryCount": 0,
        }
        app.dependency_overrides[get_tts_pipeline] = lambda: mock_pipeline

        payload = {
            "jobId": "test-job-1",
            "text": "Hello world from test",
            "spokenLang": "en",
            "jobType": "studio",
            "speedRatio": 1.25,
        }
        response = client.post("/api/v1/tts/synthesize", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["jobId"] == "test-job-1"
        assert data["status"] == "completed"
        assert data["audioPath"] == "s3://klatu-tts/test-job-1/audio.wav"
        assert data["audioDurationSeconds"] == 2.5

        # Verify mock pipeline received camelCase dictionary
        mock_pipeline.process_job.assert_called_once()
        called_args = mock_pipeline.process_job.call_args[0][0]
        assert called_args["jobId"] == "test-job-1"
        assert called_args["text"] == "Hello world from test"
        assert called_args["spokenLang"] == "en"
        assert called_args["speedRatio"] == 1.25

    def test_synthesize_pipeline_failure(self, client):
        mock_pipeline = MagicMock()
        mock_pipeline.process_job.return_value = {
            "jobId": "test-job-fail",
            "jobType": "studio",
            "status": "failed",
            "errorCode": "SYNTHESIS_FAILED",
            "errorMessage": "Out of memory",
        }
        app.dependency_overrides[get_tts_pipeline] = lambda: mock_pipeline

        payload = {
            "jobId": "test-job-fail",
            "text": "Fail test",
        }
        response = client.post("/api/v1/tts/synthesize", json=payload)
        assert response.status_code == 500
        data = response.json()
        assert data["status"] == "failed"
        assert data["errorCode"] == "SYNTHESIS_FAILED"

    def test_synthesize_pipeline_exception(self, client):
        mock_pipeline = MagicMock()
        mock_pipeline.process_job.side_effect = RuntimeError("Crash in pipeline")
        app.dependency_overrides[get_tts_pipeline] = lambda: mock_pipeline

        payload = {
            "jobId": "test-job-exc",
            "text": "Crash test",
        }
        response = client.post("/api/v1/tts/synthesize", json=payload)
        assert response.status_code == 500
        data = response.json()
        assert data["status"] == "failed"
        assert data["errorCode"] == "RuntimeError"
        assert "Crash in pipeline" in data["errorMessage"]

    def test_synthesize_validation_empty_text(self, client):
        payload = {
            "jobId": "test-job-invalid",
            "text": "",
        }
        response = client.post("/api/v1/tts/synthesize", json=payload)
        assert response.status_code == 422

    def test_synthesize_validation_invalid_job_type(self, client):
        payload = {
            "jobId": "test-job-invalid",
            "text": "Valid text",
            "jobType": "invalid_type",
        }
        response = client.post("/api/v1/tts/synthesize", json=payload)
        assert response.status_code == 422


class TestTTSDirectEndpoint:
    def test_direct_inference(self, client, monkeypatch):
        mock_engine = MagicMock()

        def fake_infer(**kwargs):
            out = kwargs.get("output_path")
            _create_dummy_wav(out)

        mock_engine.infer.side_effect = fake_infer
        app.dependency_overrides[get_tts_engine] = lambda: mock_engine
        monkeypatch.setattr("platform.system", lambda: "Darwin")

        response = client.post(
            "/api/v1/tts/direct",
            data={"text": "Direct test speech", "speed_ratio": 1.2},
        )
        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/wav"
        assert len(response.content) > 0
        mock_engine.infer.assert_called_once()
        assert mock_engine.infer.call_args[1]["ratio"] == 1.2

    def test_direct_inference_ratio_alias(self, client, monkeypatch):
        mock_engine = MagicMock()

        def fake_infer(**kwargs):
            out = kwargs.get("output_path")
            _create_dummy_wav(out)

        mock_engine.infer.side_effect = fake_infer
        app.dependency_overrides[get_tts_engine] = lambda: mock_engine
        monkeypatch.setattr("platform.system", lambda: "Darwin")

        response = client.post(
            "/api/v1/tts/direct",
            data={"text": "Ratio alias test", "ratio": 1.5},
        )
        assert response.status_code == 200
        assert mock_engine.infer.call_args[1]["ratio"] == 1.5


class TestLegacyInferEndpoint:
    def test_legacy_infer_headers_and_response(self, client, monkeypatch):
        mock_engine = MagicMock()

        def fake_infer(**kwargs):
            out = kwargs.get("output_path")
            _create_dummy_wav(out)

        mock_engine.infer.side_effect = fake_infer
        app.dependency_overrides[get_tts_engine] = lambda: mock_engine
        monkeypatch.setattr("platform.system", lambda: "Darwin")

        response = client.post(
            "/infer/",
            data={"text": "Legacy test", "ratio": 1.0},
        )
        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/wav"
        assert "deprecation-warning" in response.headers
        assert "/infer/ is deprecated" in response.headers["deprecation-warning"]
