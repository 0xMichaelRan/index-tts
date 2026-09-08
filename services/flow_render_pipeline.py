"""
Flow render pipeline.

Consumes a flow_render_jobs message and produces 3 locale MP4 files:

1. For each locale (en, zh-CN, zh-TW):
   a. Resolve audio + alignment files (local cache first, then S3 download).
   b. Parse word-level alignment JSON.
   c. Segment audio into 10 time windows by character-count proportion.
   d. For each video clip k:
      - speed_factor = clip_natural_duration / window_duration_k  (clamped [0.25, 4])
      - ffmpeg: setpts=PTS/speed_factor  (video stream)
      - ffmpeg: atempo chain             (audio stream, each filter in [0.5, 2.0])
   e. Concat 10 adjusted clips, replace audio with full locale MP3.
   f. Output: flow/{YYYYMMDD}/{job_id}/{locale}.mp4

2. Upload 3 MP4s to S3 output bucket.
3. Return result dict for publishing to flow_render_results.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.logging_config import get_logger
from services.s3_config import S3Client

logger = get_logger(__name__)

# Speed factor bounds per the spec
_MIN_SPEED = 0.25
_MAX_SPEED = 4.0
# atempo filter only accepts [0.5, 2.0] — chain filters for wider range
_ATEMPO_MIN = 0.5
_ATEMPO_MAX = 2.0

LOCALES = ["en", "zh-CN", "zh-TW"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _atempo_chain(speed: float) -> list[str]:
    """
    Build a list of atempo filter strings whose product equals `speed`.

    Each individual atempo value is clamped to [0.5, 2.0] as required by ffmpeg.
    Multiple filters are chained when the target speed falls outside that range.

    Examples:
        _atempo_chain(1.5)  → ["atempo=1.5"]
        _atempo_chain(0.25) → ["atempo=0.5", "atempo=0.5"]
        _atempo_chain(3.0)  → ["atempo=2.0", "atempo=1.5"]
    """
    filters: list[str] = []
    remaining = speed
    while remaining > _ATEMPO_MAX + 1e-6:
        filters.append(f"atempo={_ATEMPO_MAX}")
        remaining /= _ATEMPO_MAX
    while remaining < _ATEMPO_MIN - 1e-6:
        filters.append(f"atempo={_ATEMPO_MIN}")
        remaining /= _ATEMPO_MIN
    # Clamp final value
    remaining = max(_ATEMPO_MIN, min(_ATEMPO_MAX, remaining))
    filters.append(f"atempo={remaining:.6f}")
    return filters


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


def _segment_audio_by_chars(
    words: list[dict],
    num_clips: int,
) -> list[tuple[float, float]]:
    """
    Divide alignment word list into `num_clips` time windows by character proportion.

    Algorithm:
        1. Total chars across all words.
        2. chars_per_clip = total / num_clips.
        3. Walk words, accumulate chars; when crossing k * chars_per_clip, start clip k+1.

    Returns:
        List of (start_sec, end_sec) tuples, length == num_clips.
        The last window always extends to the final word's end timestamp.
    """
    if not words:
        raise ValueError("Empty word list — cannot segment audio")

    total_chars = sum(len(w.get("word", "")) for w in words)
    if total_chars == 0:
        raise ValueError("Zero total characters in alignment — cannot segment")

    chars_per_clip = total_chars / num_clips

    windows: list[tuple[float, float]] = []
    accumulated = 0
    clip_start = words[0].get("start", 0.0)
    next_boundary = chars_per_clip

    for word in words:
        word_len = len(word.get("word", ""))
        accumulated += word_len
        word_end = word.get("end", 0.0)

        if accumulated >= next_boundary and len(windows) < num_clips - 1:
            windows.append((clip_start, word_end))
            clip_start = word_end
            next_boundary += chars_per_clip

    # Final window covers everything to the last word's end
    windows.append((clip_start, words[-1].get("end", clip_start + 1.0)))

    # Pad or trim to exactly num_clips (edge cases)
    while len(windows) < num_clips:
        last_end = windows[-1][1] if windows else 0.0
        windows.append((last_end, last_end + 0.01))
    windows = windows[:num_clips]

    return windows


def _speed_adjust_clip(
    clip_path: str,
    output_path: str,
    speed_factor: float,
    ffmpeg_path: str = "ffmpeg",
) -> None:
    """
    Speed-adjust a single video clip using ffmpeg.

    - Video: setpts=PTS/speed_factor
    - Audio: atempo chain (each filter in [0.5, 2.0])
    """
    speed_factor = max(_MIN_SPEED, min(_MAX_SPEED, speed_factor))
    atempo_filters = _atempo_chain(speed_factor)
    audio_filter = ",".join(atempo_filters)
    video_filter = f"setpts={1.0 / speed_factor:.6f}*PTS"

    cmd = [
        ffmpeg_path,
        "-y",
        "-i",
        clip_path,
        "-filter:v",
        video_filter,
        "-filter:a",
        audio_filter,
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-c:a",
        "aac",
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
    """Replace audio track of video with locale MP3."""
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
            resolution, ratioFormat, createdAt

        Returns:
            Result dict suitable for publishing to flow_render_results.
        """
        job_id = str(job_data.get("jobId", "unknown"))
        start_time = time.time()

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
    ) -> str:
        """
        Render a single locale video.

        Returns local path to the final MP4.
        """
        locale_dir = os.path.join(work_dir, locale.replace("-", "_"))
        os.makedirs(locale_dir, exist_ok=True)

        # Load alignment words
        with open(align_path, encoding="utf-8") as fh:
            alignment = json.load(fh)
        words: list[dict] = alignment.get("words", [])
        if not words:
            raise ValueError(
                f"[FLOW {job_id}] No words in alignment for locale {locale}"
            )

        # Segment into 10 windows
        num_clips = len(clips)
        windows = _segment_audio_by_chars(words, num_clips)

        # Speed-adjust each clip
        adjusted_clips: list[str] = []
        for i, (clip_path, (win_start, win_end)) in enumerate(zip(clips, windows)):
            win_duration = max(win_end - win_start, 0.01)  # guard zero-length
            try:
                clip_natural_duration = _get_video_duration(clip_path, self.ffmpeg_path)
            except Exception as e:
                logger.warning(
                    f"[FLOW {job_id}] Could not get clip {i} duration: {e}. "
                    "Using window duration as fallback."
                )
                clip_natural_duration = win_duration

            speed_factor = clip_natural_duration / win_duration
            speed_factor = max(_MIN_SPEED, min(_MAX_SPEED, speed_factor))

            adjusted_path = os.path.join(locale_dir, f"adj_{i:02d}.mp4")
            logger.debug(
                f"[FLOW {job_id}] {locale} clip {i}: "
                f"speed={speed_factor:.3f} "
                f"(clip={clip_natural_duration:.2f}s, window={win_duration:.2f}s)"
            )
            _speed_adjust_clip(
                clip_path=clip_path,
                output_path=adjusted_path,
                speed_factor=speed_factor,
                ffmpeg_path=self.ffmpeg_path,
            )
            adjusted_clips.append(adjusted_path)

        # Concatenate adjusted clips (video only, temp)
        concat_path = os.path.join(locale_dir, "concat.mp4")
        _concat_clips(adjusted_clips, concat_path, self.ffmpeg_path)

        # Overlay locale audio
        final_path = os.path.join(locale_dir, f"{locale}.mp4")
        _overlay_audio(
            video_path=concat_path,
            audio_path=audio_path,
            output_path=final_path,
            ffmpeg_path=self.ffmpeg_path,
        )

        return final_path
