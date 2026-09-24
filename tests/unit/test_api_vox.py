from __future__ import annotations

from unittest.mock import MagicMock
import pytest
from fastapi.testclient import TestClient

from services.api.app import app
from services.api.deps import get_vox_pipeline


@pytest.fixture
def client():
    app.dependency_overrides.clear()
    yield TestClient(app)
    app.dependency_overrides.clear()


class TestVoxRenderEndpoint:
    def test_vox_render_success(self, client):
        mock_pipeline = MagicMock()
        mock_pipeline.process_job.return_value = {
            "jobId": "vox-job-123",
            "jobType": "vox",
            "projectId": "proj-abc",
            "status": "completed",
            "videoPath": "outputs/videos/vox-job-123.mp4",
            "videoDurationSeconds": 8.0,
            "renderDurationSeconds": 3.2,
            "completedAt": "2026-09-24T12:05:00Z",
        }
        app.dependency_overrides[get_vox_pipeline] = lambda: mock_pipeline

        payload = {
            "jobId": "vox-job-123",
            "resolution": "1080p",
            "aspectRatio": "16:9",
            "clipS3Keys": ["clips/c1.mp4", "clips/c2.mp4"],
            "beatNarrations": ["First beat narration", "Second beat narration"],
            "audioPath": "tts/audio.wav",
            "alignmentPath": "tts/alignment.json",
            "outputS3Key": "outputs/videos/vox-job-123.mp4",
            "projectId": "proj-abc",
            "language": "en",
            "burnSubtitles": True,
        }
        response = client.post("/api/v1/vox/render", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["jobId"] == "vox-job-123"
        assert data["status"] == "completed"
        assert data["videoPath"] == "outputs/videos/vox-job-123.mp4"
        assert data["videoDurationSeconds"] == 8.0
        assert data["renderDurationSeconds"] == 3.2

        # Verify mock pipeline received camelCase dictionary
        mock_pipeline.process_job.assert_called_once()
        called_args = mock_pipeline.process_job.call_args[0][0]
        assert called_args["jobId"] == "vox-job-123"
        assert called_args["resolution"] == "1080p"
        assert called_args["aspectRatio"] == "16:9"
        assert len(called_args["clipS3Keys"]) == 2
        assert len(called_args["beatNarrations"]) == 2
        assert called_args["outputS3Key"] == "outputs/videos/vox-job-123.mp4"

    def test_vox_render_pipeline_failure(self, client):
        mock_pipeline = MagicMock()
        mock_pipeline.process_job.return_value = {
            "jobId": "vox-job-fail",
            "jobType": "vox",
            "status": "failed",
            "errorCode": "FFMPEG_ERROR",
            "errorMessage": "Non-zero exit status 1",
            "renderDurationSeconds": 1.5,
        }
        app.dependency_overrides[get_vox_pipeline] = lambda: mock_pipeline

        payload = {
            "jobId": "vox-job-fail",
            "resolution": "720p",
            "aspectRatio": "9:16",
            "clipS3Keys": ["clips/c1.mp4"],
            "beatNarrations": ["Single narration"],
            "audioPath": "tts/audio.wav",
            "alignmentPath": "tts/alignment.json",
            "outputS3Key": "outputs/vox-job-fail.mp4",
        }
        response = client.post("/api/v1/vox/render", json=payload)
        assert response.status_code == 500
        data = response.json()
        assert data["status"] == "failed"
        assert data["errorCode"] == "FFMPEG_ERROR"

    def test_vox_render_pipeline_exception(self, client):
        mock_pipeline = MagicMock()
        mock_pipeline.process_job.side_effect = RuntimeError("Fatal GPU error")
        app.dependency_overrides[get_vox_pipeline] = lambda: mock_pipeline

        payload = {
            "jobId": "vox-job-exc",
            "resolution": "720p",
            "aspectRatio": "9:16",
            "clipS3Keys": ["clips/c1.mp4"],
            "beatNarrations": ["Single narration"],
            "audioPath": "tts/audio.wav",
            "alignmentPath": "tts/alignment.json",
            "outputS3Key": "outputs/vox-job-exc.mp4",
        }
        response = client.post("/api/v1/vox/render", json=payload)
        assert response.status_code == 500
        data = response.json()
        assert data["status"] == "failed"
        assert data["errorCode"] == "RuntimeError"

    def test_vox_render_missing_resolution(self, client):
        payload = {
            "jobId": "vox-job-invalid",
            "aspectRatio": "16:9",
            "clipS3Keys": ["clips/c1.mp4"],
            "beatNarrations": ["Single narration"],
            "audioPath": "tts/audio.wav",
            "alignmentPath": "tts/alignment.json",
            "outputS3Key": "outputs/out.mp4",
        }
        response = client.post("/api/v1/vox/render", json=payload)
        assert response.status_code == 422

    def test_vox_render_missing_aspect_ratio(self, client):
        payload = {
            "jobId": "vox-job-invalid",
            "resolution": "1080p",
            "clipS3Keys": ["clips/c1.mp4"],
            "beatNarrations": ["Single narration"],
            "audioPath": "tts/audio.wav",
            "alignmentPath": "tts/alignment.json",
            "outputS3Key": "outputs/out.mp4",
        }
        response = client.post("/api/v1/vox/render", json=payload)
        assert response.status_code == 422

    def test_vox_render_clips_and_narrations_count_mismatch(self, client):
        payload = {
            "jobId": "vox-job-mismatch",
            "resolution": "1080p",
            "aspectRatio": "16:9",
            "clipS3Keys": ["clips/c1.mp4", "clips/c2.mp4"],
            "beatNarrations": ["Only one narration"],
            "audioPath": "tts/audio.wav",
            "alignmentPath": "tts/alignment.json",
            "outputS3Key": "outputs/out.mp4",
        }
        response = client.post("/api/v1/vox/render", json=payload)
        assert response.status_code == 422
