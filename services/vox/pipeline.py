"""
VoxRenderPipeline — orchestrator for single-locale MP4 rendering.

Consumes a vox_jobs message and produces a single-locale MP4 file:

1. Resolve audio + alignment files (local cache first, then S3 download).
2. Parse word-level alignment JSON produced by stable-whisper.
3. Use ScriptGuidedAligner to derive exactly N time windows from beatNarrations:
   - Word-count boundary slicing (not Whisper sentence segments).
   - Midpoint pause cut allocation for fully contiguous windows.
4. For each video clip k, apply one of two adaptation strategies:
   - Speed-up  (window_duration <= 4.0s): setpts factor < 1.0  (faster playback)
   - Living-Poster Hold (window_duration > 4.0s): play at 1x, tpad clone-hold the
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
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.common.logging_config import get_logger
from services.storage.s3_config import S3Client
from services.vox.aligner import ScriptGuidedAligner
from services.vox.subtitles import (
    _FONTS_DIR,
    _build_ass_subtitles,
    _has_cjk_chars,
    _mux_audio_with_subtitles,
)
from services.vox.video_utils import (
    _CLIP_NATURAL_DURATION,
    _adapt_clip_living_poster,
    _adapt_clip_speed_up,
    _concat_clips,
    _get_video_duration,
    _overlay_audio,
    _resolve_dimensions,
)

logger = get_logger(__name__)


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
            f"[VOX {job_id}] Output: {width}x{height} ({resolution}, {aspect_ratio})"
        )

        clip_s3_keys: list[str] = (
            job_data.get("clipS3Keys") or job_data.get("clip_s3_keys") or []
        )
        beat_narrations: list[str] = (
            job_data.get("beatNarrations") or job_data.get("beat_narrations") or []
        )
        audio_path_s3: str = job_data.get("audioPath") or job_data.get("audio_path", "")
        alignment_path_s3: str = job_data.get("alignmentPath") or job_data.get(
            "alignment_path", ""
        )
        output_s3_key: str = job_data.get("outputS3Key") or job_data.get(
            "output_s3_key", ""
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
            # 1. Resolve audio and alignment files (tts bucket)
            logger.info(f"[VOX {job_id}] Resolving audio and alignment files")
            local_audio = self._resolve_file(
                job_id, audio_path_s3, work_dir, bucket_type="tts"
            )
            local_align = self._resolve_file(
                job_id, alignment_path_s3, work_dir, bucket_type="tts"
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
            windows = aligner.build_windows(total_audio_duration=total_audio_duration)

            logger.info(
                f"[VOX {job_id}] Alignment: {len(words)} words → "
                f"{len(windows)} windows "
                f"(audio_dur={total_audio_duration:.2f}s)"
                if total_audio_duration
                else f"[VOX {job_id}] Alignment: {len(words)} words → "
                f"{len(windows)} windows"
            )

            # 3b. Build ASS subtitle file (word-level karaoke)
            burn_subtitles: bool = bool(job_data.get("burnSubtitles", True))
            ass_path: str | None = None
            if burn_subtitles:
                ass_path = os.path.join(work_dir, "subtitles.ass")
                # zh-TW uses Traditional Chinese font (Noto Sans CJK TC)
                use_tc = language.lower() in ("zh-tw", "zh_tw", "zhtw", "tc")
                _build_ass_subtitles(
                    words=words,
                    windows=windows,
                    beat_narrations=beat_narrations,
                    output_path=ass_path,
                    aspect_ratio=aspect_ratio,
                    width=width,
                    height=height,
                    use_tc=use_tc,
                )
                logger.info(
                    f"[VOX {job_id}] ASS subtitles built: "
                    f"{len(windows)} lines, lang={language}, "
                    f"font={'TC' if use_tc else 'SC' if _has_cjk_chars(' '.join(beat_narrations)) else 'Inter'}"
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

            # 6. Burn subtitles + mux audio (single pass when subtitles enabled)
            final_path = os.path.join(work_dir, "final.mp4")
            if burn_subtitles and ass_path:
                # Use bundled fonts if the directory exists; fall back to
                # system fonts gracefully so the render never hard-fails.
                fonts_dir: str | None = (
                    _FONTS_DIR if os.path.isdir(_FONTS_DIR) else None
                )
                if fonts_dir:
                    logger.info(f"[VOX {job_id}] Using bundled fonts from: {fonts_dir}")
                else:
                    logger.warning(
                        f"[VOX {job_id}] Bundled fonts directory not found "
                        f"({_FONTS_DIR}); falling back to system fonts"
                    )
                _mux_audio_with_subtitles(
                    video_path=concat_path,
                    audio_path=local_audio,
                    ass_path=ass_path,
                    output_path=final_path,
                    ffmpeg_path=self.ffmpeg_path,
                    fontsdir=fonts_dir,
                )
                logger.info(
                    f"[VOX {job_id}] Subtitles burned + audio muxed (single pass)"
                )
            else:
                _overlay_audio(
                    video_path=concat_path,
                    audio_path=local_audio,
                    output_path=final_path,
                    ffmpeg_path=self.ffmpeg_path,
                )
                logger.info(f"[VOX {job_id}] Audio muxed (subtitles disabled)")

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
        bucket_type: str = "tts",
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
