"""
Vox render pipeline.

Consumes a vox_jobs message and produces a single-locale MP4 file:

1. Resolve audio + alignment files (local cache first, then S3 download).
2. Parse word-level alignment JSON produced by stable-whisper.
3. Use ScriptGuidedAligner to derive exactly N time windows from beatNarrations:
   - Word-count boundary slicing (not Whisper sentence segments).
   - Midpoint pause cut allocation for fully contiguous windows.
4. For each video clip k, apply one of two adaptation strategies:
   - Speed-up  (window_duration ≤ 4.0s): setpts factor < 1.0  (faster playback)
   - Living-Poster Hold (window_duration > 4.0s): play at 1×, tpad clone-hold the
     final frame for the excess duration.
5. Concat N adapted (video-only) clips.
6. Mux original narration audio untouched (strict audio invariant).
7. Upload final MP4 to ``output_s3_key``.

Audio invariant: narration audio is NEVER trimmed, sped up, or slowed down.
All temporal adjustments are applied exclusively to video frames.

Resolution and aspectRatio are MANDATORY — no defaults are permitted.
"""

from __future__ import annotations

import json
import os
import re
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

# Speed factor bounds
_MIN_SPEED = 0.25
_MAX_SPEED = 4.0

# Oracle animation clip natural duration (seconds)
_CLIP_NATURAL_DURATION = 4.0

# Oracle clip composition: assembly phase duration before the living-poster hold
_CLIP_ASSEMBLY_DURATION = 3.0  # 0.0s – 3.0s at natural 1× speed


# ---------------------------------------------------------------------------
# Resolution helpers
# ---------------------------------------------------------------------------

# Maps (resolution_label, aspect_ratio) → (width, height)
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


def _resolve_dimensions(resolution: str, aspect_ratio: str) -> tuple[int, int]:
    """
    Return (width, height) for the given resolution label and aspect ratio.

    Raises ValueError if the combination is not recognised.
    """
    key = (resolution.lower(), aspect_ratio.lower())
    if key not in _RESOLUTION_MAP:
        known = ", ".join(f"{r}/{a}" for r, a in _RESOLUTION_MAP)
        raise ValueError(
            f"Unknown resolution/aspectRatio combination '{resolution}/{aspect_ratio}'. "
            f"Known combinations: {known}"
        )
    return _RESOLUTION_MAP[key]


# ---------------------------------------------------------------------------
# FFmpeg helpers
# ---------------------------------------------------------------------------


def _get_video_duration(path: str, ffmpeg_path: str = "ffmpeg") -> float:
    """Return duration of a video/audio file in seconds using ffprobe."""
    ffprobe = ffmpeg_path.replace("ffmpeg", "ffprobe")
    cmd = [
        ffprobe,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json",
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
            ffmpeg_path, "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", list_path,
            "-c", "copy",
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
        ffmpeg_path, "-y",
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
# Script-Guided Aligner
# ---------------------------------------------------------------------------


class ScriptGuidedAligner:
    """
    Derives exactly N time windows from a list of beat narrations and the
    word-level alignment output of stable-whisper.

    Algorithm
    ---------
    Step A — Word-count boundary slicing:
        For each beat narration, count its tokens (whitespace-split words for
        Latin scripts; character count for CJK scripts).  Sequentially consume
        that many words from the stable-whisper ``words`` array.  This gives
        speech_start_k / speech_end_k for each beat k without any Whisper
        segmentation heuristics.

    Step B — Midpoint pause cut allocation:
        Between consecutive beats, the acoustic pause is split at its midpoint.
        Window boundaries are guaranteed monotonically non-decreasing and
        sum(window_durations) == total_audio_duration (zero drift).
    """

    # Threshold for CJK character counting vs whitespace-split word counting
    _CJK_RATIO_THRESHOLD = 0.4

    def __init__(self, beat_narrations: list[str], words: list[dict]) -> None:
        """
        Args:
            beat_narrations: Ordered list of narration strings, one per beat.
            words: Word-level alignment dicts from stable-whisper with keys
                   ``word``, ``start`` (float), ``end`` (float).

        Raises:
            ValueError: If narrations are empty or word list is empty.
        """
        if not beat_narrations:
            raise ValueError("beat_narrations must not be empty")
        if not words:
            raise ValueError("Alignment word list must not be empty")
        self.beat_narrations = beat_narrations
        self.words = words

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def build_windows(
        self,
        total_audio_duration: float | None = None,
    ) -> list[tuple[float, float]]:
        """
        Return exactly ``len(beat_narrations)`` contiguous (start, end) windows.

        Args:
            total_audio_duration: Duration of the full audio file.  If provided,
                the last window extends to this value (covers any trailing silence).

        Returns:
            List of (start_sec, end_sec) tuples, length == len(beat_narrations).
        """
        n = len(self.beat_narrations)
        token_counts = [self._token_count(narr) for narr in self.beat_narrations]
        total_tokens = sum(token_counts)
        available_words = len(self.words)

        logger.debug(
            f"ScriptGuidedAligner: {n} beats, {total_tokens} tokens, "
            f"{available_words} alignment words"
        )

        if total_tokens > available_words:
            # Proportional downscale — prevents index overflow
            logger.warning(
                f"ScriptGuidedAligner: total tokens ({total_tokens}) exceeds "
                f"alignment words ({available_words}); scaling proportionally"
            )
            scale = available_words / total_tokens
            token_counts = [max(1, round(c * scale)) for c in token_counts]
            # Re-clamp to not exceed total
            while sum(token_counts) > available_words:
                token_counts[-1] = max(1, token_counts[-1] - 1)

        # Step A: Slice words per beat → {speech_start, speech_end}
        segments: list[dict] = []
        cursor = 0
        for k, count in enumerate(token_counts):
            end_cursor = min(cursor + count, available_words)
            beat_words = self.words[cursor:end_cursor]
            if not beat_words:
                # Degenerate: reuse previous segment boundary
                prev_end = segments[-1]["end"] if segments else 0.0
                segments.append({"start": prev_end, "end": prev_end})
            else:
                segments.append(
                    {
                        "start": float(beat_words[0]["start"]),
                        "end": float(beat_words[-1]["end"]),
                    }
                )
            cursor = end_cursor

        # Step B: Midpoint pause cut allocation
        return self._build_time_windows(segments, total_audio_duration)

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    @staticmethod
    def _is_cjk(text: str) -> bool:
        """Return True if the majority of non-whitespace chars are CJK."""
        non_ws = [c for c in text if not c.isspace()]
        if not non_ws:
            return False
        cjk_count = sum(
            1 for c in non_ws
            if "\u4e00" <= c <= "\u9fff"
            or "\u3400" <= c <= "\u4dbf"
            or "\uac00" <= c <= "\ud7a3"
            or "\u3040" <= c <= "\u30ff"
        )
        return cjk_count / len(non_ws) >= ScriptGuidedAligner._CJK_RATIO_THRESHOLD

    @staticmethod
    def _token_count(narration: str) -> int:
        """
        Count tokens in a narration string.

        Uses character count for CJK scripts (each character is a token),
        and whitespace-split word count for Latin/other scripts.
        """
        text = narration.strip()
        if not text:
            return 1
        if ScriptGuidedAligner._is_cjk(text):
            # Count non-whitespace characters for CJK
            return sum(1 for c in text if not c.isspace())
        else:
            return len(re.findall(r"\S+", text))

    @staticmethod
    def _build_time_windows(
        segments: list[dict],
        total_audio_duration: float | None,
    ) -> list[tuple[float, float]]:
        """
        Convert per-beat speech start/end pairs into fully-contiguous windows.

        The acoustic pause between consecutive beats is split at its midpoint.
        Window[0].start = 0.0, Window[-1].end = total_audio_duration (or last
        speech end if duration not provided).  This guarantees zero-gap coverage
        with sum(window_durations) == total_audio_duration.
        """
        n = len(segments)
        seg_starts = [float(s["start"]) for s in segments]
        seg_ends = [float(s["end"]) for s in segments]

        total_end = (
            max(float(total_audio_duration), seg_ends[-1])
            if total_audio_duration is not None
            else seg_ends[-1]
        )

        # Build boundary list: boundary[0] = 0.0, boundary[i] = midpoint of
        # inter-beat pause, boundary[n] = total_end
        boundaries: list[float] = [0.0]
        for i in range(n - 1):
            midpoint = (seg_ends[i] + seg_starts[i + 1]) / 2.0
            # Enforce monotonicity
            midpoint = max(boundaries[-1], midpoint)
            boundaries.append(midpoint)
        boundaries.append(max(boundaries[-1], total_end))

        return [(boundaries[i], boundaries[i + 1]) for i in range(n)]


# ---------------------------------------------------------------------------
# Clip adaptation
# ---------------------------------------------------------------------------


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
        ffmpeg_path, "-y",
        "-i", clip_path,
        "-vf", f"{skip_filter}{scale_pad},setpts={pts_factor:.6f}*PTS",
        "-an",
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
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
            ffmpeg_path, "-y",
            "-ss", f"{start_offset:.3f}",
            "-i", clip_path,
            "-vf", (
                f"setpts=PTS-STARTPTS,{scale_pad},"
                f"tpad=stop_mode=clone:stop_duration={extra_secs:.3f}"
            ),
            "-an",
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            output_path,
        ]
    else:
        cmd = [
            ffmpeg_path, "-y",
            "-i", clip_path,
            "-vf", (
                f"{scale_pad},"
                f"tpad=stop_mode=clone:stop_duration={extra_secs:.3f}"
            ),
            "-an",
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            output_path,
        ]
    subprocess.run(cmd, check=True, capture_output=True)


# ---------------------------------------------------------------------------
# Main pipeline class
# ---------------------------------------------------------------------------


class VoxRenderPipeline:
    """
    Renders a single-locale MP4 file from a vox_jobs message.

    Responsibilities:
    - Validate mandatory resolution and aspectRatio fields.
    - Resolve audio + alignment files (local cache → S3).
    - Download video clips from the video S3 bucket.
    - Apply ScriptGuidedAligner to derive N time windows from beatNarrations.
    - Adapt each clip to its time window (speed-up or Living-Poster Hold).
    - Concatenate adapted (video-only) clips and mux original audio untouched.
    - Upload the final MP4 to output_s3_key in the video bucket.
    - Return a result dict for publishing to vox_results.

    Args:
        s3_client: Registry-backed S3Client.
        ffmpeg_path: Path to ffmpeg binary (default: "ffmpeg").
        local_tts_output_dir: Directory where the TTS synthesis pipeline
            stores its outputs, checked before falling back to S3 download.
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
        Process a vox_jobs message end-to-end.

        Expected job_data keys (camelCase from RabbitMQ):
            jobId, jobType,
            projectId, voiceId, language,
            resolution (MANDATORY), aspectRatio (MANDATORY),
            audioPath, alignmentPath,
            clipS3Keys  (ordered list of clip S3 keys),
            beatNarrations (ordered list of narration strings),
            outputS3Key,
            createdAt

        Returns:
            Result dict suitable for publishing to vox_results.
        """
        job_id = str(job_data.get("jobId", "unknown"))
        start_time = time.time()

        logger.info(f"[VOX {job_id}] Starting vox render pipeline")

        # --- Validate mandatory fields ---
        resolution = job_data.get("resolution") or job_data.get("resolution")
        aspect_ratio = job_data.get("aspectRatio") or job_data.get("aspect_ratio")
        if not resolution:
            raise ValueError(
                f"[VOX {job_id}] 'resolution' is a mandatory field — not provided"
            )
        if not aspect_ratio:
            raise ValueError(
                f"[VOX {job_id}] 'aspectRatio' is a mandatory field — not provided"
            )

        width, height = _resolve_dimensions(resolution, aspect_ratio)
        logger.info(
            f"[VOX {job_id}] Output: {width}x{height} "
            f"({resolution}, {aspect_ratio})"
        )

        clip_s3_keys: list[str] = (
            job_data.get("clipS3Keys") or job_data.get("clip_s3_keys") or []
        )
        beat_narrations: list[str] = (
            job_data.get("beatNarrations") or job_data.get("beat_narrations") or []
        )
        audio_path_s3: str = (
            job_data.get("audioPath") or job_data.get("audio_path", "")
        )
        alignment_path_s3: str = (
            job_data.get("alignmentPath") or job_data.get("alignment_path", "")
        )
        output_s3_key: str = (
            job_data.get("outputS3Key") or job_data.get("output_s3_key", "")
        )
        language: str = job_data.get("language", "en")
        project_id = job_data.get("projectId") or job_data.get("project_id")

        if not clip_s3_keys:
            raise ValueError(f"[VOX {job_id}] No clip S3 keys provided")
        if not beat_narrations:
            raise ValueError(f"[VOX {job_id}] No beat narrations provided")
        if len(clip_s3_keys) != len(beat_narrations):
            raise ValueError(
                f"[VOX {job_id}] Mismatch: {len(clip_s3_keys)} clips vs "
                f"{len(beat_narrations)} narrations"
            )
        if not audio_path_s3:
            raise ValueError(f"[VOX {job_id}] audioPath is required")
        if not alignment_path_s3:
            raise ValueError(f"[VOX {job_id}] alignmentPath is required")
        if not output_s3_key:
            raise ValueError(f"[VOX {job_id}] outputS3Key is required")

        logger.info(
            f"[VOX {job_id}] {len(clip_s3_keys)} clips, "
            f"{len(beat_narrations)} narrations, "
            f"language={language}"
        )

        work_dir = tempfile.mkdtemp(prefix=f"vox_{job_id}_")
        try:
            # 1. Resolve audio and alignment files (audio bucket)
            logger.info(f"[VOX {job_id}] Resolving audio and alignment files")
            local_audio = self._resolve_file(
                job_id, audio_path_s3, work_dir, bucket_type="audio"
            )
            local_align = self._resolve_file(
                job_id, alignment_path_s3, work_dir, bucket_type="audio"
            )

            # 2. Download video clips
            logger.info(f"[VOX {job_id}] Downloading {len(clip_s3_keys)} video clips")
            clips_dir = os.path.join(work_dir, "clips")
            os.makedirs(clips_dir, exist_ok=True)
            local_clips = self._download_clips(job_id, clip_s3_keys, clips_dir)

            # 3. Load alignment and run script-guided aligner
            with open(local_align, encoding="utf-8") as fh:
                alignment_data = json.load(fh)

            words: list[dict] = alignment_data.get("words", [])
            if not words:
                raise ValueError(
                    f"[VOX {job_id}] Alignment JSON has no 'words' — "
                    "cannot perform script-guided alignment"
                )

            try:
                total_audio_duration = _get_video_duration(
                    local_audio, self.ffmpeg_path
                )
            except Exception as probe_err:
                logger.warning(
                    f"[VOX {job_id}] Could not probe audio duration: {probe_err}"
                )
                total_audio_duration = None

            aligner = ScriptGuidedAligner(
                beat_narrations=beat_narrations,
                words=words,
            )
            windows = aligner.build_windows(
                total_audio_duration=total_audio_duration
            )

            logger.info(
                f"[VOX {job_id}] Alignment: {len(words)} words → "
                f"{len(windows)} windows "
                f"(audio_dur={total_audio_duration:.2f}s)"
                if total_audio_duration
                else f"[VOX {job_id}] Alignment: {len(words)} words → "
                f"{len(windows)} windows"
            )

            # 4. Adapt each clip to its window
            adapted_dir = os.path.join(work_dir, "adapted")
            os.makedirs(adapted_dir, exist_ok=True)
            adapted_clips = self._adapt_clips(
                job_id, local_clips, windows, adapted_dir, width, height
            )

            # 5. Concatenate (video-only) adapted clips
            concat_path = os.path.join(work_dir, "concat.mp4")
            _concat_clips(adapted_clips, concat_path, self.ffmpeg_path)
            logger.info(f"[VOX {job_id}] Clips concatenated")

            # 6. Mux original audio (strict audio invariant)
            final_path = os.path.join(work_dir, "final.mp4")
            _overlay_audio(
                video_path=concat_path,
                audio_path=local_audio,
                output_path=final_path,
                ffmpeg_path=self.ffmpeg_path,
            )
            logger.info(f"[VOX {job_id}] Audio muxed")

            # 7. Upload to S3 (video bucket)
            self.s3_client.upload_file(
                local_path=final_path,
                remote_path=output_s3_key,
                bucket_type="video",
            )
            logger.success(f"[VOX {job_id}] Uploaded → {output_s3_key}")

            # Measure final video duration
            try:
                video_duration = _get_video_duration(final_path, self.ffmpeg_path)
            except Exception:
                video_duration = total_audio_duration or 0.0

            render_duration = time.time() - start_time
            logger.success(
                f"[VOX {job_id}] Render complete in {render_duration:.1f}s "
                f"(video_dur={video_duration:.1f}s)"
            )

            return {
                "jobId": job_id,
                "jobType": "vox",
                "projectId": project_id,
                "status": "completed",
                "videoPath": output_s3_key,
                "videoDurationSeconds": video_duration,
                "renderDurationSeconds": render_duration,
                "completedAt": datetime.now(timezone.utc).isoformat(),
            }

        except Exception as exc:
            render_duration = time.time() - start_time
            logger.error(
                f"[VOX {job_id}] Render failed after {render_duration:.1f}s: {exc!s}"
            )
            return {
                "jobId": job_id,
                "jobType": "vox",
                "projectId": project_id,
                "status": "failed",
                "errorCode": type(exc).__name__,
                "errorMessage": str(exc),
                "renderDurationSeconds": render_duration,
                "completedAt": datetime.now(timezone.utc).isoformat(),
            }

        finally:
            try:
                shutil.rmtree(work_dir, ignore_errors=True)
            except Exception as cleanup_err:
                logger.warning(
                    f"[VOX {job_id}] Failed to clean work dir: {cleanup_err}"
                )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _resolve_file(
        self,
        job_id: str,
        s3_key: str,
        work_dir: str,
        bucket_type: str = "audio",
    ) -> str:
        """
        Return a local path for s3_key.

        Search order:
        1. Local TTS output dir (synthesis pipeline may have left the file on disk).
        2. Download from S3.
        """
        filename = os.path.basename(s3_key)

        # Check local TTS output dir first (cache hit)
        candidate = os.path.join(self.local_tts_output_dir, job_id, filename)
        if os.path.exists(candidate):
            logger.debug(f"[VOX {job_id}] Local cache hit: {filename}")
            return candidate

        local_path = os.path.join(work_dir, filename)
        logger.debug(f"[VOX {job_id}] Downloading {s3_key} ({bucket_type})")
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
        """Download ordered video clips from the video bucket."""
        local_clips: list[str] = []
        for i, s3_key in enumerate(clip_s3_keys):
            ext = Path(s3_key).suffix or ".mp4"
            local_path = os.path.join(clips_dir, f"clip_{i:02d}{ext}")
            self.s3_client.download_file(
                remote_path=s3_key,
                local_path=local_path,
                bucket_type="video",
                max_retries=3,
            )
            local_clips.append(local_path)
            logger.debug(f"[VOX {job_id}] Downloaded clip {i:02d}: {s3_key}")
        return local_clips

    def _adapt_clips(
        self,
        job_id: str,
        clips: list[str],
        windows: list[tuple[float, float]],
        adapted_dir: str,
        width: int,
        height: int,
        fps: int = 30,
        skip_first_frame: bool = True,
    ) -> list[str]:
        """
        Adapt each clip to its time window.

        Selects speed-up or Living-Poster Hold per clip based on window duration.
        """
        adapted: list[str] = []
        for i, (clip_path, (win_start, win_end)) in enumerate(zip(clips, windows)):
            win_dur = max(win_end - win_start, 0.01)

            try:
                clip_dur = _get_video_duration(clip_path, self.ffmpeg_path)
            except Exception as e:
                logger.warning(
                    f"[VOX {job_id}] Could not probe clip {i:02d} duration: {e}. "
                    f"Using natural duration {_CLIP_NATURAL_DURATION}s."
                )
                clip_dur = _CLIP_NATURAL_DURATION

            out_path = os.path.join(adapted_dir, f"adj_{i:02d}.mp4")

            if win_dur <= clip_dur:
                # Speed-up: compress clip to fit within window
                action = f"speed-up (clip={clip_dur:.2f}s → window={win_dur:.2f}s)"
                _adapt_clip_speed_up(
                    clip_path=clip_path,
                    output_path=out_path,
                    window_duration=win_dur,
                    clip_duration=clip_dur,
                    width=width,
                    height=height,
                    fps=fps,
                    ffmpeg_path=self.ffmpeg_path,
                    skip_first_frame=skip_first_frame,
                )
            else:
                # Living-Poster Hold: play at 1× then freeze the final frame
                extra = win_dur - clip_dur
                action = (
                    f"living-poster (clip={clip_dur:.2f}s, "
                    f"hold +{extra:.2f}s → window={win_dur:.2f}s)"
                )
                _adapt_clip_living_poster(
                    clip_path=clip_path,
                    output_path=out_path,
                    window_duration=win_dur,
                    clip_duration=clip_dur,
                    width=width,
                    height=height,
                    fps=fps,
                    ffmpeg_path=self.ffmpeg_path,
                    skip_first_frame=skip_first_frame,
                )

            logger.debug(f"[VOX {job_id}] clip {i:02d}: {action}")
            adapted.append(out_path)

        return adapted
