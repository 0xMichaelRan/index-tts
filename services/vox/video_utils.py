"""
FFmpeg helper functions for Vox video rendering.

Pure utility functions for video manipulation — no domain state.
All I/O is delegated to subprocess (ffmpeg/ffprobe).
"""

from __future__ import annotations

import json
import os
import subprocess

from services.common.logging_config import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Speed factor bounds
# ---------------------------------------------------------------------------

_MIN_SPEED = 0.25
_MAX_SPEED = 4.0

# Oracle animation clip natural duration (seconds)
_CLIP_NATURAL_DURATION = 4.0

# ---------------------------------------------------------------------------
# Resolution helpers
# ---------------------------------------------------------------------------

# Maps (resolution_label, aspect_ratio) → (width, height)
_RESOLUTION_MAP: dict[tuple[str, str], tuple[int, int]] = {
    ("640p", "16x9"): (1138, 640),
    ("640p", "9x16"): (640, 1138),
    ("640p", "1x1"): (640, 640),
    ("720p", "16x9"): (1280, 720),
    ("720p", "9x16"): (720, 1280),
    ("720p", "1x1"): (720, 720),
    ("1080p", "16x9"): (1920, 1080),
    ("1080p", "9x16"): (1080, 1920),
    ("1080p", "1x1"): (1080, 1080),
    ("2k", "16x9"): (2560, 1440),
    ("2k", "9x16"): (1440, 2560),
    ("2k", "1x1"): (1440, 1440),
}

_BASE_RESOLUTIONS: dict[str, int] = {
    "640p": 640,
    "720p": 720,
    "1080p": 1080,
    "2k": 1440,
}


def _resolve_dimensions(resolution: str, aspect_ratio: str) -> tuple[int, int]:
    """
    Return (width, height) for the given resolution label and aspect ratio.

    Supported resolutions: '640p', '720p', '1080p', '2k'.
    Supported aspect ratios: '16x9', '9x16', '1x1' (and arbitrary WxH ratios).

    Raises ValueError if the combination is not recognised.
    """
    res_lower = resolution.lower()
    norm_aspect = aspect_ratio.lower().replace(":", "x")
    key = (res_lower, norm_aspect)
    if key in _RESOLUTION_MAP:
        return _RESOLUTION_MAP[key]

    if res_lower in _BASE_RESOLUTIONS and "x" in norm_aspect:
        try:
            parts = norm_aspect.split("x")
            rw, rh = int(parts[0]), int(parts[1])
            if rw > 0 and rh > 0:
                base = _BASE_RESOLUTIONS[res_lower]
                if rw >= rh:
                    h = base
                    w = int(round(h * rw / rh))
                else:
                    w = base
                    h = int(round(w * rh / rw))
                w = w + (w % 2)
                h = h + (h % 2)
                return (w, h)
        except (ValueError, IndexError):
            pass

    known = ", ".join(f"{r}/{a}" for r, a in _RESOLUTION_MAP)
    raise ValueError(
        f"Unknown resolution/aspectRatio combination '{resolution}/{aspect_ratio}'. "
        f"Known combinations: {known}"
    )


# ---------------------------------------------------------------------------
# FFmpeg helpers
# ---------------------------------------------------------------------------


def _get_video_duration(path: str, ffmpeg_path: str = "ffmpeg") -> float:
    """Return duration of a video/audio file in seconds using ffprobe."""
    ffprobe = ffmpeg_path.replace("ffmpeg", "ffprobe")
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


def _build_scale_pad_filter(width: int, height: int, fps: int = 30) -> str:
    """Return a vf filter string that scales, letterboxes, and sets fps."""
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
        f"fps={fps}"
    )


def _concat_clips(
    clip_paths: list[str],
    output_path: str,
    ffmpeg_path: str = "ffmpeg",
) -> None:
    """Concatenate video clips using the ffmpeg concat demuxer."""
    list_path = output_path + "_concat_list.txt"
    try:
        with open(list_path, "w") as fh:
            for p in clip_paths:
                fh.write(f"file '{os.path.abspath(p)}'\n")

        cmd = [
            ffmpeg_path,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            list_path,
            "-c",
            "copy",
            output_path,
        ]
        subprocess.run(cmd, check=True, capture_output=True)
    finally:
        if os.path.exists(list_path):
            os.remove(list_path)


def _overlay_audio(
    video_path: str,
    audio_path: str,
    output_path: str,
    ffmpeg_path: str = "ffmpeg",
) -> None:
    """
    Mux the narration audio onto the concatenated video.

    Audio is copied verbatim — no speed, pitch, or duration adjustments
    (strict audio invariant).
    """
    cmd = [
        ffmpeg_path,
        "-y",
        "-i",
        video_path,
        "-i",
        audio_path,
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        output_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _adapt_clip_speed_up(
    clip_path: str,
    output_path: str,
    window_duration: float,
    clip_duration: float,
    width: int,
    height: int,
    fps: int = 30,
    ffmpeg_path: str = "ffmpeg",
    skip_first_frame: bool = True,
) -> None:
    """
    Speed-up strategy: window_duration ≤ clip_duration.

    Compresses the clip to fit within window_duration via setpts.
    """
    scale_pad = _build_scale_pad_filter(width, height, fps)
    frame_duration = 2.0 / fps
    eff_clip_dur = (
        max(clip_duration - frame_duration, 0.001)
        if skip_first_frame
        else clip_duration
    )

    skip_filter = "select='gte(n\\,2)',setpts=PTS-STARTPTS," if skip_first_frame else ""
    pts_factor = window_duration / max(eff_clip_dur, 0.001)
    # Clamp to reciprocal of speed bounds
    pts_factor = max(1.0 / _MAX_SPEED, min(1.0 / _MIN_SPEED, pts_factor))

    cmd = [
        ffmpeg_path,
        "-y",
        "-i",
        clip_path,
        "-vf",
        f"{skip_filter}{scale_pad},setpts={pts_factor:.6f}*PTS",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "22",
        output_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _adapt_clip_living_poster(
    clip_path: str,
    output_path: str,
    window_duration: float,
    clip_duration: float,
    width: int,
    height: int,
    fps: int = 30,
    ffmpeg_path: str = "ffmpeg",
    skip_first_frame: bool = True,
) -> None:
    """
    Living-Poster Hold strategy: window_duration > clip_duration.

    Oracle animation clips follow this rhythm:
        0.0s – 3.0s  Assembly phase (paper elements animate at 1× speed).
        3.0s – 4.0s  Living-poster hold (final frame, subtle breathing).

    Since window_duration > 4.0s we play the entire clip at 1× speed and then
    freeze-hold the last frame for the excess duration via ffmpeg tpad.
    """
    extra_secs = max(window_duration - clip_duration, 0.0)
    scale_pad = _build_scale_pad_filter(width, height, fps)

    frame_duration = 2.0 / fps
    start_offset = frame_duration if skip_first_frame else 0.0

    if skip_first_frame:
        cmd = [
            ffmpeg_path,
            "-y",
            "-ss",
            f"{start_offset:.3f}",
            "-i",
            clip_path,
            "-vf",
            (
                f"setpts=PTS-STARTPTS,{scale_pad},"
                f"tpad=stop_mode=clone:stop_duration={extra_secs:.3f}"
            ),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "22",
            output_path,
        ]
    else:
        cmd = [
            ffmpeg_path,
            "-y",
            "-i",
            clip_path,
            "-vf",
            (f"{scale_pad},tpad=stop_mode=clone:stop_duration={extra_secs:.3f}"),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "22",
            output_path,
        ]
    subprocess.run(cmd, check=True, capture_output=True)
