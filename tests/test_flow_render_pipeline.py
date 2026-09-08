"""
Tests for FlowRenderPipeline and skip_first_frame logic.
"""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock

import pytest

from services.flow_render_pipeline import (
    ClipAlignmentStrategy,
    FlowRenderPipeline,
    _process_clip_segment,
)


def _get_frame_count(path: str) -> int:
    """Return number of video frames in a file using ffprobe."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-count_packets",
        "-show_entries",
        "stream=nb_read_packets",
        "-of",
        "json",
        path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)
    return int(data["streams"][0]["nb_read_packets"])


class TestSkipFirstFrameProcessing:
    """Test _process_clip_segment with skip_first_frame enabled and disabled."""

    @pytest.fixture
    def sample_video(self, tmp_path):
        """Create a 1-second 30fps test video (30 frames)."""
        video_path = str(tmp_path / "sample.mp4")
        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=1:size=320x240:rate=30",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            video_path,
        ]
        subprocess.run(cmd, capture_output=True, check=True)
        return video_path

    def test_skip_first_frame_true_drops_one_frame(self, sample_video, tmp_path):
        out_path = str(tmp_path / "out_skipped.mp4")
        # 1-second clip at 30fps = 30 frames
        _process_clip_segment(
            clip_path=sample_video,
            output_path=out_path,
            window_duration=1.0,
            clip_duration=1.0,
            strategy=ClipAlignmentStrategy.SPEED_LONG_SLOW_SHORT,
            width=640,
            height=360,
            fps=30,
            skip_first_frame=True,
        )
        frames = _get_frame_count(out_path)
        # Should drop 1 frame (29 frames)
        assert frames == 29

    def test_skip_first_frame_false_preserves_all_frames(self, sample_video, tmp_path):
        out_path = str(tmp_path / "out_kept.mp4")
        _process_clip_segment(
            clip_path=sample_video,
            output_path=out_path,
            window_duration=1.0,
            clip_duration=1.0,
            strategy=ClipAlignmentStrategy.SPEED_LONG_SLOW_SHORT,
            width=640,
            height=360,
            fps=30,
            skip_first_frame=False,
        )
        frames = _get_frame_count(out_path)
        # Should keep all 30 frames
        assert frames == 30


class TestFlowRenderPipelineJobParsing:
    """Test argument parsing for skip_first_frame in process_job."""

    def test_default_skip_first_frame_is_true(self, monkeypatch):
        mock_s3 = MagicMock()
        pipeline = FlowRenderPipeline(s3_client=mock_s3)

        captured_skip = None

        def fake_render_locale(**kwargs):
            nonlocal captured_skip
            captured_skip = kwargs.get("skip_first_frame")
            return "/tmp/fake.mp4"

        monkeypatch.setattr(pipeline, "_resolve_file", lambda *a, **kw: "/tmp/fake")
        monkeypatch.setattr(pipeline, "_download_clips", lambda *a, **kw: ["/tmp/c1"])
        monkeypatch.setattr(pipeline, "_render_locale", fake_render_locale)

        job_data = {
            "jobId": "test-123",
            "clipS3Keys": ["flow/c1.mp4"],
            "audioEnPath": "a.mp3",
            "audioZhCnPath": "b.mp3",
            "audioZhTwPath": "c.mp3",
            "alignEnPath": "a.json",
            "alignZhCnPath": "b.json",
            "alignZhTwPath": "c.json",
        }

        pipeline.process_job(job_data)
        assert captured_skip is True

    def test_explicit_skip_first_frame_false(self, monkeypatch):
        mock_s3 = MagicMock()
        pipeline = FlowRenderPipeline(s3_client=mock_s3)

        captured_skip = None

        def fake_render_locale(**kwargs):
            nonlocal captured_skip
            captured_skip = kwargs.get("skip_first_frame")
            return "/tmp/fake.mp4"

        monkeypatch.setattr(pipeline, "_resolve_file", lambda *a, **kw: "/tmp/fake")
        monkeypatch.setattr(pipeline, "_download_clips", lambda *a, **kw: ["/tmp/c1"])
        monkeypatch.setattr(pipeline, "_render_locale", fake_render_locale)

        job_data = {
            "jobId": "test-456",
            "clipS3Keys": ["flow/c1.mp4"],
            "audioEnPath": "a.mp3",
            "audioZhCnPath": "b.mp3",
            "audioZhTwPath": "c.mp3",
            "alignEnPath": "a.json",
            "alignZhCnPath": "b.json",
            "alignZhTwPath": "c.json",
            "skipFirstFrame": False,
        }

        pipeline.process_job(job_data)
        assert captured_skip is False
