"""
Tests for video render pipeline and skip_first_frame logic.
"""

from __future__ import annotations

import json
import subprocess
from enum import Enum
from typing import Any
from unittest.mock import MagicMock

import pytest

from services.vox.pipeline import VoxRenderPipeline
from services.vox.video_utils import _adapt_clip_speed_up


class ClipAlignmentStrategy(str, Enum):
    SPEED_LONG_SLOW_SHORT = "speed_long_slow_short"


# Backwards-compatibility aliases for flow -> vox migration
FlowRenderPipeline = VoxRenderPipeline


def _process_clip_segment(
    clip_path: str,
    output_path: str,
    window_duration: float,
    clip_duration: float,
    strategy: Any = None,
    width: int = 640,
    height: int = 360,
    fps: int = 30,
    ffmpeg_path: str = "ffmpeg",
    skip_first_frame: bool = True,
) -> None:
    """Adapter for testing clip adaptation with skip_first_frame."""
    _adapt_clip_speed_up(
        clip_path=clip_path,
        output_path=output_path,
        window_duration=window_duration,
        clip_duration=clip_duration,
        width=width,
        height=height,
        fps=fps,
        ffmpeg_path=ffmpeg_path,
        skip_first_frame=skip_first_frame,
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
    """Test _adapt_clip_speed_up with skip_first_frame enabled and disabled."""

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

    def test_skip_first_frame_true_drops_two_frames(self, sample_video, tmp_path):
        out_path = str(tmp_path / "out_skipped.mp4")
        # 1-second clip at 30fps = 30 frames
        _adapt_clip_speed_up(
            clip_path=sample_video,
            output_path=out_path,
            window_duration=1.0,
            clip_duration=1.0,
            width=640,
            height=360,
            fps=30,
            skip_first_frame=True,
        )
        frames = _get_frame_count(out_path)
        # Should drop 2 frames (28 frames)
        assert frames == 28

    def test_skip_first_frame_false_preserves_all_frames(self, sample_video, tmp_path):
        out_path = str(tmp_path / "out_kept.mp4")
        _adapt_clip_speed_up(
            clip_path=sample_video,
            output_path=out_path,
            window_duration=1.0,
            clip_duration=1.0,
            width=640,
            height=360,
            fps=30,
            skip_first_frame=False,
        )
        frames = _get_frame_count(out_path)
        # Should keep all 30 frames
        assert frames == 30


class TestFlowRenderPipelineJobParsing:
    """Test argument passing for skip_first_frame in VoxRenderPipeline._adapt_clips."""

    def test_default_skip_first_frame_is_true(self, monkeypatch):
        mock_s3 = MagicMock()
        pipeline = VoxRenderPipeline(s3_client=mock_s3)

        captured_skip = None

        def fake_adapt_speed_up(**kwargs):
            nonlocal captured_skip
            captured_skip = kwargs.get("skip_first_frame")

        monkeypatch.setattr(
            "services.vox.pipeline._get_video_duration", lambda *a, **kw: 4.0
        )
        monkeypatch.setattr(
            "services.vox.pipeline._adapt_clip_speed_up", fake_adapt_speed_up
        )

        pipeline._adapt_clips(
            job_id="test-123",
            clips=["/tmp/c1.mp4"],
            windows=[(0.0, 2.0)],
            adapted_dir="/tmp",
            width=640,
            height=360,
        )
        assert captured_skip is True

    def test_explicit_skip_first_frame_false(self, monkeypatch):
        mock_s3 = MagicMock()
        pipeline = VoxRenderPipeline(s3_client=mock_s3)

        captured_skip = None

        def fake_adapt_speed_up(**kwargs):
            nonlocal captured_skip
            captured_skip = kwargs.get("skip_first_frame")

        monkeypatch.setattr(
            "services.vox.pipeline._get_video_duration", lambda *a, **kw: 4.0
        )
        monkeypatch.setattr(
            "services.vox.pipeline._adapt_clip_speed_up", fake_adapt_speed_up
        )

        pipeline._adapt_clips(
            job_id="test-456",
            clips=["/tmp/c1.mp4"],
            windows=[(0.0, 2.0)],
            adapted_dir="/tmp",
            width=640,
            height=360,
            skip_first_frame=False,
        )
        assert captured_skip is False


class TestVoxRenderPipelineClipsDownload:
    """Test video clip downloading uses the video bucket."""

    def test_download_clips_uses_video_bucket(self, tmp_path):
        mock_s3 = MagicMock()
        pipeline = VoxRenderPipeline(s3_client=mock_s3)

        clips_dir = str(tmp_path / "clips")
        clip_keys = [
            "projects/23142/clips/beat_01.mp4",
            "projects/23142/clips/beat_02.mp4",
        ]

        local_clips = pipeline._download_clips(
            job_id="test_proj_1",
            clip_s3_keys=clip_keys,
            clips_dir=clips_dir,
        )

        assert len(local_clips) == 2
        assert mock_s3.download_file.call_count == 2

        for call in mock_s3.download_file.call_args_list:
            assert call.kwargs["bucket_type"] == "video"
