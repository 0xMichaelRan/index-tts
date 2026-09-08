"""
Flow render pipeline.

Consumes a flow_render_jobs message and produces 3 locale MP4 files:

1. For each locale (en, zh-CN, zh-TW):
   a. Resolve audio + alignment files (local cache first, then S3 download).
   b. Parse word-level alignment JSON.
   c. Segment audio into 10 time windows by WORD-COUNT proportion (not sentences).
   d. For each video clip k, apply clip_alignment_strategy:
      - speed_long_slow_short (default / Option 2):
          speed_factor = clip_natural_duration / window_duration_k  (clamped [0.25, 4])
          ffmpeg: setpts=pts_factor*PTS  (video only, -an)
      - trim_long_slow_short (Option 1):
          Long clip: trim from front (ss = clip_dur - window_dur), keep ending at 1x speed
          Short clip: slow down via setpts (same as Option 2)
   e. Concat 10 adjusted (video-only) clips, overlay full locale MP3 untouched.
   f. Output: flow/{YYYYMMDD}/{job_id}/{locale}.mp4

2. Upload 3 MP4s to S3 output bucket.
3. Return result dict for publishing to flow_render_results.

Strict audio invariant: narration audio is NEVER trimmed, sped up, or slowed down.
All temporal adjustments are performed exclusively on the video clips.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from services.logging_config import get_logger
from services.s3_config import S3Client

logger = get_logger(__name__)

# Speed factor bounds per the spec
_MIN_SPEED = 0.25
_MAX_SPEED = 4.0

LOCALES = ["en", "zh-CN", "zh-TW"]


# ---------------------------------------------------------------------------
# Strategy enum
# ---------------------------------------------------------------------------


class ClipAlignmentStrategy(str, Enum):
    """
    Controls how each video clip is adjusted to match its audio window.

    speed_long_slow_short (Default / Option 2):
        Long clip  → speed up via setpts (all frames preserved, faster playback)
        Short clip → slow down via setpts (all frames preserved, slower playback)

    trim_long_slow_short (Option 1):
        Long clip  → trim from front, keep climax/ending at natural 1× speed
        Short clip → slow down via setpts (same as Option 2)
    """

    SPEED_LONG_SLOW_SHORT = "speed_long_slow_short"
    TRIM_LONG_SLOW_SHORT = "trim_long_slow_short"


# ---------------------------------------------------------------------------
# Resolution helpers
# ---------------------------------------------------------------------------

# Maps (resolution_label, ratio_format) → (width, height)
_RESOLUTION_MAP: dict[tuple[str, str], tuple[int, int]] = {
    ("720p", "16x9"): (1280, 720),
    ("720p", "9x16"): (720, 1280),
    ("1080p", "16x9"): (1920, 1080),
    ("1080p", "9x16"): (1080, 1920),
    ("480p", "16x9"): (854, 480),
    ("480p", "9x16"): (480, 854),
    ("4k", "16x9"): (3840, 2160),
    ("4k", "9x16"): (2160, 3840),
}
_DEFAULT_RESOLUTION = (1280, 720)


def _resolve_dimensions(resolution: str, ratio_format: str) -> tuple[int, int]:
    """Return (width, height) for the given resolution label and ratio format."""
    key = (resolution.lower(), ratio_format.lower())
    return _RESOLUTION_MAP.get(key, _DEFAULT_RESOLUTION)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_video_duration(path: str, ffmpeg_path: str = "ffmpeg") -> float:
    """Return duration of a video file in seconds using ffprobe."""
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


def _segment_audio_by_words(
    words: list[dict],
    num_clips: int,
) -> list[tuple[float, float]]:
    """
    Divide stable-whisper word-level alignment into ``num_clips`` time windows
    using word count as the proportional weight.

    The input text is NOT assumed to have exactly ``num_clips`` sentences.
    Break points are derived purely from word counts so each clip gets a
    roughly equal share of narration words (≈ equal spoken duration).

    Algorithm:
        1. Total word count.
        2. words_per_clip = total / num_clips.
        3. Walk words; when cumulative count crosses k * words_per_clip, close
           the current window and open the next.

    Returns:
        List of (start_sec, end_sec) tuples, length == num_clips.
        The last window always extends to the final word's end timestamp.
    """
    if not words:
        raise ValueError("Empty word list — cannot segment audio")

    total_words = len(words)
    words_per_clip = total_words / num_clips

    windows: list[tuple[float, float]] = []
    clip_start = words[0].get("start", 0.0)
    next_boundary = words_per_clip

    for idx, word in enumerate(words):
        word_end = word.get("end", 0.0)
        words_processed = idx + 1  # 1-indexed count

        if words_processed >= next_boundary and len(windows) < num_clips - 1:
            windows.append((clip_start, word_end))
            clip_start = word_end
            next_boundary += words_per_clip

    # Final window covers everything to the last word's end
    windows.append((clip_start, words[-1].get("end", clip_start + 1.0)))

    # Pad or trim to exactly num_clips (edge cases: very few words)
    while len(windows) < num_clips:
        last_end = windows[-1][1] if windows else 0.0
        windows.append((last_end, last_end + 0.01))
    windows = windows[:num_clips]

    return windows


def _build_scale_pad_filter(width: int, height: int, fps: int = 30) -> str:
    """
    Return a vf filter string that scales, pads to target frame, and sets fps.

    Uses force_original_aspect_ratio=decrease so the clip is letterboxed /
    pillarboxed into the target frame without cropping.
    """
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
        f"fps={fps}"
    )


def _process_clip_segment(
    clip_path: str,
    output_path: str,
    window_duration: float,
    clip_duration: float,
    strategy: ClipAlignmentStrategy,
    width: int,
    height: int,
    fps: int = 30,
    ffmpeg_path: str = "ffmpeg",
) -> None:
    """
    Process a single video clip to fit ``window_duration`` using the given strategy.

    Audio is stripped (-an) because the full locale narration MP3 is overlaid
    onto the concatenated video at the final step (strict audio invariant).

    Args:
        clip_path:       Local path to the raw downloaded clip.
        output_path:     Local path for the processed segment.
        window_duration: Target duration (seconds) from alignment windows.
        clip_duration:   Natural duration of the input clip (seconds).
        strategy:        Alignment strategy to apply.
        width, height:   Target output dimensions.
        fps:             Target output frame rate.
        ffmpeg_path:     Path to the ffmpeg binary.
    """
    scale_pad = _build_scale_pad_filter(width, height, fps)

    if (
        strategy == ClipAlignmentStrategy.TRIM_LONG_SLOW_SHORT
        and clip_duration > window_duration
    ):
        # Option 1 — Long clip: trim from front, keep the ending at 1× speed.
        start_time = clip_duration - window_duration
        cmd = [
            ffmpeg_path, "-y",
            "-ss", f"{start_time:.3f}",
            "-t", f"{window_duration:.3f}",
            "-i", clip_path,
            "-vf", f"{scale_pad},setpts=PTS-STARTPTS",
            "-an",
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            output_path,
        ]
    else:
        # Option 2 (default) or short clip in Option 1: adjust via setpts.
        # pts_factor > 1 slows down, < 1 speeds up.
        pts_factor = window_duration / max(clip_duration, 0.001)
        # Clamp to inverse of speed bounds
        pts_factor = max(1.0 / _MAX_SPEED, min(1.0 / _MIN_SPEED, pts_factor))
        cmd = [
            ffmpeg_path, "-y",
            "-i", clip_path,
            "-vf", f"{scale_pad},setpts={pts_factor:.6f}*PTS",
            "-an",
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            output_path,
        ]

    subprocess.run(cmd, check=True, capture_output=True)


def _concat_clips(
    adjusted_clip_paths: list[str],
    output_path: str,
    ffmpeg_path: str = "ffmpeg",
) -> None:
    """Concatenate adjusted clips using ffmpeg concat demuxer."""
    # Write concat list file
    list_path = output_path + "_concat_list.txt"
    try:
        with open(list_path, "w") as fh:
            for p in adjusted_clip_paths:
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
    Replace audio track of concatenated video with locale MP3.

    The audio stream is copied verbatim — no speed, pitch, or duration
    adjustments are applied (strict audio invariant).
    """
    cmd = [
        ffmpeg_path,
        "-y",
        "-i", video_path,
        "-i", audio_path,
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        output_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)


# ---------------------------------------------------------------------------
# Main pipeline class
# ---------------------------------------------------------------------------


class FlowRenderPipeline:
    """
    Renders 3 locale MP4 files from a flow_render_jobs message.

    Responsibilities:
    - Resolve audio + alignment files (local cache first, S3 fallback)
    - Download video clips from S3
    - Segment audio by character count → 10 time windows per locale
    - Speed-adjust + concat clips via ffmpeg
    - Overlay locale audio
    - Upload 3 MP4s to S3 output bucket
    - Return result dict

    Args:
        s3_client: S3Client instance (dual-bucket aware)
        ffmpeg_path: Path to ffmpeg binary (default: "ffmpeg")
        local_tts_output_dir: Directory where synthesis pipeline stores its outputs
            (used to locate files before falling back to S3 download)
    """

    def __init__(
        self,
        s3_client: S3Client,
        ffmpeg_path: str = "ffmpeg",
        local_tts_output_dir: str = "outputs/tts_output",
    ) -> None:
        self.s3_client = s3_client
        self.ffmpeg_path = ffmpeg_path
        self.local_tts_output_dir = local_tts_output_dir

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def process_job(self, job_data: dict[str, Any]) -> dict[str, Any]:
        """
        Process a flow_render_jobs message end-to-end.

        Expected job_data keys (camelCase from RabbitMQ):
            jobId, jobType,
            audioEnPath, audioZhCnPath, audioZhTwPath,
            alignEnPath, alignZhCnPath, alignZhTwPath,
            clipS3Keys  (list of 10 S3 keys),
            resolution, ratioFormat,
            clipAlignmentStrategy  ("speed_long_slow_short" | "trim_long_slow_short"),
            createdAt

        Returns:
            Result dict suitable for publishing to flow_render_results.
        """
        job_id = str(job_data.get("jobId", "unknown"))
        start_time = time.time()

        # Parse clip alignment strategy (default: speed_long_slow_short)
        raw_strategy = job_data.get(
            "clipAlignmentStrategy",
            ClipAlignmentStrategy.SPEED_LONG_SLOW_SHORT.value,
        )
        try:
            strategy = ClipAlignmentStrategy(raw_strategy)
        except ValueError:
            logger.warning(
                f"[FLOW {job_id}] Unknown clipAlignmentStrategy '{raw_strategy}', "
                f"falling back to 'speed_long_slow_short'"
            )
            strategy = ClipAlignmentStrategy.SPEED_LONG_SLOW_SHORT

        logger.info(f"[FLOW {job_id}] Clip alignment strategy: {strategy.value}")

        # Parse resolution / ratio
        resolution = job_data.get("resolution", "720p")
        ratio_format = job_data.get("ratioFormat", "16x9")
        width, height = _resolve_dimensions(resolution, ratio_format)
        logger.info(
            f"[FLOW {job_id}] Output resolution: {width}x{height} "
            f"({resolution}, {ratio_format})"
        )

        logger.info(f"[FLOW {job_id}] Starting render pipeline")

        work_dir = tempfile.mkdtemp(prefix=f"flow_{job_id}_")
        try:
            # --- 1. Resolve S3 paths per locale ---
            locale_audio: dict[str, str] = {
                "en": job_data["audioEnPath"],
                "zh-CN": job_data["audioZhCnPath"],
                "zh-TW": job_data["audioZhTwPath"],
            }
            locale_align: dict[str, str] = {
                "en": job_data["alignEnPath"],
                "zh-CN": job_data["alignZhCnPath"],
                "zh-TW": job_data["alignZhTwPath"],
            }
            clip_s3_keys: list[str] = job_data["clipS3Keys"]

            if len(clip_s3_keys) != 10:
                raise ValueError(f"Expected 10 clip S3 keys, got {len(clip_s3_keys)}")

            # --- 2. Resolve audio + alignment files (local cache → S3) ---
            logger.info(f"[FLOW {job_id}] Resolving audio and alignment files")
            local_audio: dict[str, str] = {}
            local_align: dict[str, str] = {}
            for locale in LOCALES:
                local_audio[locale] = self._resolve_file(
                    job_id, locale_audio[locale], work_dir, bucket_type="output"
                )
                local_align[locale] = self._resolve_file(
                    job_id, locale_align[locale], work_dir, bucket_type="output"
                )

            # --- 3. Download video clips from S3 ---
            logger.info(f"[FLOW {job_id}] Downloading {len(clip_s3_keys)} video clips")
            clips_dir = os.path.join(work_dir, "clips")
            os.makedirs(clips_dir, exist_ok=True)
            local_clips: list[str] = self._download_clips(
                job_id, clip_s3_keys, clips_dir
            )

            # --- 4. Render each locale ---
            date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
            video_paths: dict[str, str] = {}

            for locale in LOCALES:
                logger.info(f"[FLOW {job_id}] Rendering locale: {locale}")
                s3_video_path = f"flow/{date_str}/{job_id}/{locale}.mp4"
                local_video = self._render_locale(
                    job_id=job_id,
                    locale=locale,
                    audio_path=local_audio[locale],
                    align_path=local_align[locale],
                    clips=local_clips,
                    work_dir=work_dir,
                    strategy=strategy,
                    width=width,
                    height=height,
                )

                # Upload
                logger.info(
                    f"[FLOW {job_id}] Uploading {locale} video → {s3_video_path}"
                )
                self.s3_client.upload_file(
                    local_path=local_video,
                    remote_path=s3_video_path,
                    bucket_type="output",
                )
                video_paths[locale] = s3_video_path
                logger.success(
                    f"[FLOW {job_id}] {locale} video uploaded: {s3_video_path}"
                )

            total_duration = time.time() - start_time
            logger.success(f"[FLOW {job_id}] Render complete in {total_duration:.1f}s")

            return {
                "jobId": job_id,
                "jobType": "flow",
                "status": "completed",
                "videoEnPath": video_paths["en"],
                "videoZhCnPath": video_paths["zh-CN"],
                "videoZhTwPath": video_paths["zh-TW"],
                "renderDurationSeconds": total_duration,
                "completedAt": datetime.now(timezone.utc).isoformat(),
            }

        except Exception as exc:
            total_duration = time.time() - start_time
            logger.error(
                f"[FLOW {job_id}] Render failed after {total_duration:.1f}s: {exc!s}"
            )
            return {
                "jobId": job_id,
                "jobType": "flow",
                "status": "failed",
                "errorCode": type(exc).__name__,
                "errorMessage": str(exc),
                "renderDurationSeconds": total_duration,
                "completedAt": datetime.now(timezone.utc).isoformat(),
            }

        finally:
            # Clean up temp working directory
            try:
                shutil.rmtree(work_dir, ignore_errors=True)
            except Exception as cleanup_err:
                logger.warning(
                    f"[FLOW {job_id}] Failed to clean work dir: {cleanup_err}"
                )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _resolve_file(
        self,
        job_id: str,
        s3_key: str,
        work_dir: str,
        bucket_type: str = "output",
    ) -> str:
        """
        Return a local path for `s3_key`.

        Search order:
        1. Local TTS output dir (synthesis pipeline may have left the file on disk)
        2. Download from S3
        """
        filename = os.path.basename(s3_key)

        # Check local TTS output dir first
        candidate = os.path.join(self.local_tts_output_dir, job_id, filename)
        if os.path.exists(candidate):
            logger.debug(f"[FLOW {job_id}] Cache hit for {filename} at {candidate}")
            return candidate

        # Fall back to S3 download
        local_path = os.path.join(work_dir, filename)
        logger.debug(f"[FLOW {job_id}] Downloading {s3_key} from S3 ({bucket_type})")
        self.s3_client.download_file(
            remote_path=s3_key,
            local_path=local_path,
            bucket_type=bucket_type,
            max_retries=3,
        )
        return local_path

    def _download_clips(
        self,
        job_id: str,
        clip_s3_keys: list[str],
        clips_dir: str,
    ) -> list[str]:
        """Download video clips from the storage (misc) bucket."""
        local_clips: list[str] = []
        for i, s3_key in enumerate(clip_s3_keys):
            ext = Path(s3_key).suffix or ".mp4"
            local_path = os.path.join(clips_dir, f"clip_{i:02d}{ext}")
            self.s3_client.download_file(
                remote_path=s3_key,
                local_path=local_path,
                bucket_type="storage",
                max_retries=3,
            )
            local_clips.append(local_path)
            logger.debug(f"[FLOW {job_id}] Downloaded clip {i}: {s3_key}")
        return local_clips

    def _render_locale(
        self,
        job_id: str,
        locale: str,
        audio_path: str,
        align_path: str,
        clips: list[str],
        work_dir: str,
        strategy: ClipAlignmentStrategy,
        width: int,
        height: int,
        fps: int = 30,
    ) -> str:
        """
        Render a single locale video.

        Steps:
        1. Parse word-level alignment JSON → derive N time windows by word count.
        2. For each clip apply the alignment strategy to fit its window.
        3. Concatenate adjusted (video-only) clips.
        4. Overlay the locale narration audio WITHOUT modification.

        Returns local path to the final MP4.
        """
        locale_dir = os.path.join(work_dir, locale.replace("-", "_"))
        os.makedirs(locale_dir, exist_ok=True)

        # Load alignment words from stable-whisper output
        with open(align_path, encoding="utf-8") as fh:
            alignment = json.load(fh)
        words: list[dict] = alignment.get("words", [])
        if not words:
            raise ValueError(
                f"[FLOW {job_id}] No words in alignment for locale {locale}"
            )

        # Derive N time windows from word count (NOT sentence boundaries)
        num_clips = len(clips)
        windows = _segment_audio_by_words(words, num_clips)

        logger.info(
            f"[FLOW {job_id}] [{locale}] {len(words)} words → "
            f"{num_clips} windows via word-count segmentation"
        )

        # Process each clip to fit its window
        adjusted_clips: list[str] = []
        for i, (clip_path, (win_start, win_end)) in enumerate(zip(clips, windows)):
            win_duration = max(win_end - win_start, 0.01)  # guard zero-length
            try:
                clip_natural_duration = _get_video_duration(
                    clip_path, self.ffmpeg_path
                )
            except Exception as e:
                logger.warning(
                    f"[FLOW {job_id}] [{locale}] Could not probe clip {i} duration: "
                    f"{e}. Falling back to window duration."
                )
                clip_natural_duration = win_duration

            adjusted_path = os.path.join(locale_dir, f"adj_{i:02d}.mp4")

            # Log the effective action for this clip
            if (
                strategy == ClipAlignmentStrategy.TRIM_LONG_SLOW_SHORT
                and clip_natural_duration > win_duration
            ):
                action = (
                    f"trim-front ({clip_natural_duration - win_duration:.2f}s cut, "
                    f"keep last {win_duration:.2f}s at 1×)"
                )
            else:
                pts = win_duration / max(clip_natural_duration, 0.001)
                pts = max(1.0 / _MAX_SPEED, min(1.0 / _MIN_SPEED, pts))
                speed = 1.0 / pts
                action = f"setpts×{pts:.3f} (speed={speed:.3f}×)"

            logger.debug(
                f"[FLOW {job_id}] [{locale}] clip {i:02d}: "
                f"clip={clip_natural_duration:.2f}s window={win_duration:.2f}s → {action}"
            )

            _process_clip_segment(
                clip_path=clip_path,
                output_path=adjusted_path,
                window_duration=win_duration,
                clip_duration=clip_natural_duration,
                strategy=strategy,
                width=width,
                height=height,
                fps=fps,
                ffmpeg_path=self.ffmpeg_path,
            )
            adjusted_clips.append(adjusted_path)

        # Concatenate adjusted (video-only) clips
        concat_path = os.path.join(locale_dir, "concat.mp4")
        _concat_clips(adjusted_clips, concat_path, self.ffmpeg_path)

        # Overlay locale narration audio (STRICT: audio is never modified)
        final_path = os.path.join(locale_dir, f"{locale}.mp4")
        _overlay_audio(
            video_path=concat_path,
            audio_path=audio_path,
            output_path=final_path,
            ffmpeg_path=self.ffmpeg_path,
        )

        return final_path
