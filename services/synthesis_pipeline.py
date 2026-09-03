"""
TTS synthesis pipeline orchestration.
Handles synthesis, forced alignment, and result upload.
"""

import json
import logging
import os
import platform
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from services.audio_processor import AudioProcessor
from services.cache_manager import CacheManager
from services.circuit_breaker import CircuitBreakerError, get_circuit_breaker
from services.logging_config import get_logger
from services.s3_config import S3ConfigError
from services.storage_manager import StorageManager
from services.tts_job_service import TTSJobService

logger = get_logger(__name__)

# Import alignment service
try:
    from services.alignment import AlignmentService

    ALIGNMENT_AVAILABLE = True
except ImportError as e:
    logging.warning("AlignmentService not available: %s", e)
    AlignmentService = None  # type: ignore[assignment,misc]
    ALIGNMENT_AVAILABLE = False


class SynthesisPipeline:
    """Orchestrates the complete TTS synthesis, alignment, and upload pipeline."""

    def __init__(
        self,
        tts_engine: Any,
        storage_manager: StorageManager,
        cache_manager: CacheManager,
        use_fast_inference: bool = True,
        normalization_enabled: bool = True,
        normalization_target_lufs: float = -16.0,
        job_service: Optional[TTSJobService] = None,
    ):
        """
        Initialize synthesis pipeline.

        Args:
            tts_engine: TTS engine instance (native macOS or IndexTTS)
            storage_manager: StorageManager instance
            cache_manager: CacheManager instance
            use_fast_inference: Use fast inference method (Windows/Linux)
            normalization_enabled: Enable audio loudness normalization
            normalization_target_lufs: Target LUFS for normalization
            job_service: Optional TTSJobService instance for job tracking
        """
        self.tts_engine = tts_engine
        self.storage_manager = storage_manager
        self.cache_manager = cache_manager
        self.job_service = job_service or TTSJobService()
        self.audio_processor = AudioProcessor()

        self.use_fast_inference = use_fast_inference
        self.normalization_enabled = normalization_enabled
        self.normalization_target_lufs = normalization_target_lufs

        self.platform = platform.system()

        # Initialize alignment service (mandatory)
        alignment_model = os.getenv("TTS_ALIGNMENT_MODEL", "small")
        alignment_device = os.getenv("TTS_ALIGNMENT_DEVICE", "cpu")

        if ALIGNMENT_AVAILABLE and AlignmentService is not None:
            self.alignment_service = AlignmentService(
                model_name=alignment_model,
                device=alignment_device,
            )
            try:
                t_align_load = time.time()
                self.alignment_service.load_model()
                logger.success(
                    f"Alignment: stable-whisper {alignment_model} on {alignment_device} "
                    f"(mandatory, loaded in {time.time() - t_align_load:.1f}s)"
                )
            except Exception as e:
                logger.failure(f"Alignment model failed to load: {e}")
                raise
        else:
            self.alignment_service = None
            logger.failure("AlignmentService unavailable — pipeline cannot start")
            raise RuntimeError("AlignmentService is required but not available")

        # Initialize circuit breakers
        self.tts_breaker = get_circuit_breaker(
            name="IndexTTS",
            failure_threshold=int(
                os.getenv("CIRCUIT_BREAKER_TTS_FAILURE_THRESHOLD", "3")
            ),
            reset_timeout=int(os.getenv("CIRCUIT_BREAKER_TTS_RESET_TIMEOUT", "30")),
            half_open_max_calls=2,
            success_threshold=2,
        )

        self.alignment_breaker = get_circuit_breaker(
            name="Alignment",
            failure_threshold=int(
                os.getenv("CIRCUIT_BREAKER_ALIGNMENT_FAILURE_THRESHOLD", "3")
            ),
            reset_timeout=int(
                os.getenv("CIRCUIT_BREAKER_ALIGNMENT_RESET_TIMEOUT", "60")
            ),
            half_open_max_calls=2,
            success_threshold=2,
        )

    def process_job(self, job_data: dict[str, Any]) -> dict[str, Any]:
        """
        Process a single TTS job through the complete pipeline.

        Pipeline stages:
        1. Cache lookup (skip synthesis if hit)
        2. Synthesis (at ratio=1.0 for caching)
        3. Forced alignment (stable-whisper)
        4. Audio upload to S3
        5. Alignment JSON upload
        6. Cleanup temp files

        Args:
            job_data: Job message from RabbitMQ

        Returns:
            Result dictionary with status, paths, and metadata
        """
        # Extract job parameters
        job_id = (
            job_data.get("jobId")
            if job_data.get("jobId") is not None
            else job_data.get("job_id")
        )
        if job_id is not None:
            job_id = str(job_id)

        text = job_data.get("text", "")
        audio_prompt_path = job_data.get("audioPromptPath")
        language = job_data.get("spokenLang", "en")
        job_type = job_data.get("jobType", "studio")

        # Validate job_type
        if job_type not in ("studio", "playground", "rem"):
            logger.error(
                f"[JOB {job_id}] Invalid job_type: {job_type}, defaulting to 'studio'"
            )
            job_type = "studio"

        speed_ratio = job_data.get("speedRatio", 1.0)
        environment = job_data.get("environment", "prod")
        voice_id = job_data.get("voiceId", 0)

        retry_count = 0
        max_retries = 3

        logger.info(
            f"[JOB {job_id}] Processing TTS request "
            f"(type: {job_type}, language: {language}, speedRatio: {speed_ratio})"
        )

        job_started_at = datetime.now()
        job_start_time = time.time()
        local_audio_prompt = None
        local_output = None
        local_alignment_json = None
        cache_hit = False

        # Create tts_jobs database record and generate ttsId
        tts_id = None
        try:
            tts_id = self.job_service.create_job_record(job_data)
            if tts_id is not None:
                job_data["ttsId"] = tts_id
        except Exception as e:
            logger.error(f"[JOB {job_id}] Failed to create TTS job record: {e}")
            return self._build_failure_result(
                job_type,
                job_id,
                "DATABASE_CONNECTION_FAILED",
                str(e),
                0,
                job_started_at,
                job_data,
            )

        # Retry loop for transient failures
        while retry_count < max_retries:
            try:
                # Stage 1: Cache lookup
                if self.cache_manager.enabled:
                    cache_hit, cached_audio_path = self.cache_manager.lookup(
                        job_id, text, audio_prompt_path, speed_ratio
                    )
                    if cache_hit and cached_audio_path:
                        local_output = cached_audio_path

                # Stage 2: Synthesis (if no cache hit)
                if not cache_hit:
                    local_audio_prompt, local_output = self._run_synthesis(
                        job_id,
                        text,
                        audio_prompt_path,
                        language,
                        speed_ratio,
                        job_data,
                    )

                # Stage 3: Forced alignment (mandatory)
                output_dir = self.storage_manager.create_output_dir(job_id)
                _, _, local_alignment_json = self._run_alignment(
                    job_id, local_output, text, language, output_dir
                )

                # Extract detected language
                detected_language = self._extract_detected_language(
                    local_alignment_json, language
                )

                # Stage 4: Build S3 paths
                file_extension = Path(local_output).suffix.lstrip(".")
                output_s3_path = self.storage_manager.build_s3_output_path(
                    job_type=job_type,
                    job_id=job_id,
                    language=detected_language,
                    ratio=speed_ratio,
                    environment=environment,
                    voice_id=voice_id,
                    file_extension=file_extension,
                )

                # Stage 5: Upload audio
                audio_path = self._run_audio_upload(
                    job_id, local_output, output_s3_path
                )

                # Stage 6: Upload alignment JSON
                alignment_s3_path, alignment_duration_seconds = (
                    self._run_alignment_upload(
                        job_id, local_alignment_json, output_s3_path
                    )
                )

                # Stage 7: Calculate metrics
                audio_duration = self.audio_processor.get_audio_duration(local_output)
                total_duration = time.time() - job_start_time

                # Update tts_jobs status in database
                if tts_id is not None:
                    self.job_service.update_job_status(
                        tts_id,
                        "completed",
                        audio_path=audio_path,
                        alignment_path=alignment_s3_path,
                        audio_duration_seconds=audio_duration,
                        synthesis_duration_seconds=total_duration,
                        retry_count=retry_count,
                    )

                # Build success result
                result = self._build_success_result(
                    job_type,
                    job_id,
                    audio_path,
                    audio_duration,
                    alignment_s3_path,
                    alignment_duration_seconds,
                    job_started_at,
                    cache_hit,
                    retry_count,
                    job_data,
                    synthesis_duration_seconds=total_duration,
                )

                cache_status = "cache HIT" if cache_hit else "full synthesis"
                logger.success(
                    f"[JOB {job_id}] Completed in {total_duration:.2f}s ({cache_status})"
                )
                return result

            except (S3ConfigError, OSError) as e:
                # Retryable errors
                retry_count += 1
                if retry_count < max_retries:
                    delay = 2**retry_count
                    logger.warning(
                        f"[JOB {job_id}] Attempt {retry_count}/{max_retries} failed: {e!s}. "
                        f"Retrying in {delay}s..."
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        f"[JOB {job_id}] All {max_retries} attempts failed: {e!s}"
                    )
                    if tts_id is not None:
                        self.job_service.update_job_status(
                            tts_id,
                            "failed",
                            error_code="RETRYABLE_ERROR_EXHAUSTED",
                            error_message=str(e),
                            retry_count=retry_count,
                        )
                    return self._build_failure_result(
                        job_type,
                        job_id,
                        "RETRYABLE_ERROR_EXHAUSTED",
                        str(e),
                        retry_count,
                        job_started_at,
                        job_data,
                    )

            except Exception as e:
                logger.error(f"[JOB {job_id}] Non-retryable error: {e!s}")
                if tts_id is not None:
                    self.job_service.update_job_status(
                        tts_id,
                        "failed",
                        error_code="NON_RETRYABLE_ERROR",
                        error_message=str(e),
                        retry_count=retry_count,
                    )
                return self._build_failure_result(
                    job_type,
                    job_id,
                    "NON_RETRYABLE_ERROR",
                    str(e),
                    retry_count,
                    job_started_at,
                    job_data,
                )

            finally:
                # Cleanup temp files
                if local_audio_prompt and os.path.exists(local_audio_prompt):
                    self.storage_manager.cleanup_local_files(local_audio_prompt)
                if local_output and not local_output.startswith(
                    str(Path(self.cache_manager.cache_dir))
                ):
                    self.storage_manager.cleanup_local_files(local_output)
                if local_alignment_json and os.path.exists(local_alignment_json):
                    self.storage_manager.cleanup_local_files(local_alignment_json)

        if tts_id is not None:
            self.job_service.update_job_status(
                tts_id,
                "failed",
                error_code="MAX_RETRIES_EXHAUSTED",
                error_message="Max retries exhausted",
                retry_count=retry_count,
            )
        return self._build_failure_result(
            job_type,
            job_id,
            "MAX_RETRIES_EXHAUSTED",
            "Max retries exhausted",
            retry_count,
            job_started_at,
            job_data,
        )

    def _run_synthesis(
        self,
        job_id: str,
        text: str,
        audio_prompt_path: str,
        language: str,
        speed_ratio: float,
        job_data: dict[str, Any],
    ) -> tuple[str, str]:
        """
        Execute synthesis stage: download prompt, synthesize, apply cache/time-stretch.

        Returns:
            (local_audio_prompt_path, local_output_path) tuple
        """
        synthesis_start = time.time()

        # Stage 2a: Download audio prompt
        logger.info(f"[JOB {job_id}] Downloading audio prompt from S3...")
        try:
            from services.circuit_breaker import get_circuit_breaker

            s3_breaker = get_circuit_breaker(
                "S3Download", failure_threshold=5, reset_timeout=60
            )
            with s3_breaker:
                local_audio_prompt = self.storage_manager.download_audio_prompt(
                    job_id, audio_prompt_path
                )
        except CircuitBreakerError:
            error_msg = "S3 circuit breaker is open - service unavailable"
            logger.error(f"[JOB {job_id}] {error_msg}")
            raise RuntimeError(error_msg)

        # Stage 2b: Synthesize at ratio=1.0 (for caching)
        logger.info(f"[JOB {job_id}] Synthesizing audio...")
        try:
            with self.tts_breaker:
                base_audio_path = self._synthesize_audio(
                    job_id, text, local_audio_prompt, audio_prompt_path, language
                )
        except CircuitBreakerError:
            error_msg = "TTS circuit breaker is open - service unavailable"
            logger.error(f"[JOB {job_id}] {error_msg}")
            raise RuntimeError(error_msg)

        # Stage 2c: Store in cache (if enabled)
        if self.cache_manager.enabled:
            audio_duration = self.audio_processor.get_audio_duration(base_audio_path)
            synthesis_duration = time.time() - synthesis_start
            self.cache_manager.store(
                job_id,
                text,
                audio_prompt_path,
                base_audio_path,
                audio_duration,
                synthesis_duration,
                language,
            )

        # Stage 2d: Apply time-stretching if needed
        output_dir = self.storage_manager.create_output_dir(job_id)
        if speed_ratio != 1.0:
            local_output = self.audio_processor.apply_ratio_to_audio(
                base_audio_path, speed_ratio, job_id, output_dir
            )
        else:
            local_output = self.audio_processor.copy_audio_file(
                base_audio_path, job_id, output_dir
            )

        return local_audio_prompt, local_output

    def _synthesize_audio(
        self,
        job_id: str,
        text: str,
        audio_prompt: str,
        audio_prompt_s3_path: str,
        language: str,
    ) -> str:
        """Execute TTS synthesis with voice caching."""
        output_dir = self.storage_manager.create_output_dir(job_id)
        timestamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
        output_path = os.path.join(output_dir, f"{job_id}_{timestamp}.wav")

        logger.info(f"[JOB {job_id}] Synthesizing to {output_path} (ratio: 1.0)")

        if self.platform == "Darwin":
            # macOS native TTS
            self.tts_engine.infer(
                audio_prompt=None,
                text=text,
                output_path=output_path,
                ratio=1.0,
                language=language,
            )
        else:
            # IndexTTS GPU inference with voice caching
            is_same_voice = self.tts_engine.cache_audio_prompt == audio_prompt_s3_path

            if not is_same_voice:
                logger.info(
                    f"[JOB {job_id}] Loading new voice (S3: {audio_prompt_s3_path})"
                )
                self.tts_engine.cache_audio_prompt = None
                self.tts_engine.cache_cond_mel = None
            else:
                logger.info(
                    f"[JOB {job_id}] Reusing cached voice (S3: {audio_prompt_s3_path})"
                )
                self.tts_engine.cache_audio_prompt = audio_prompt

            # Run inference
            if self.use_fast_inference:
                self.tts_engine.infer_fast(
                    audio_prompt=audio_prompt,
                    text=text,
                    output_path=output_path,
                    ratio=1.0,
                    enable_normalization=self.normalization_enabled,
                    target_lufs=self.normalization_target_lufs,
                )
            else:
                self.tts_engine.infer(
                    audio_prompt=audio_prompt,
                    text=text,
                    output_path=output_path,
                    ratio=1.0,
                )

            # Store S3 path as cache key for next job
            self.tts_engine.cache_audio_prompt = audio_prompt_s3_path

        logger.info(f"[JOB {job_id}] Synthesis complete: {output_path}")
        return output_path

    def _run_alignment(
        self,
        job_id: str,
        local_output: str,
        text: str,
        language: str,
        output_dir: str,
    ) -> tuple[str, str, str]:
        """Execute forced alignment (stable-whisper)."""
        try:
            with self.alignment_breaker:
                raw_json_path, srt_path, parsed_json_path = (
                    self.alignment_service.align_to_files(
                        job_id=job_id,
                        audio_path=local_output,
                        text=text,
                        language_hint=language,
                        output_dir=output_dir,
                    )
                )
                return raw_json_path, srt_path, parsed_json_path
        except CircuitBreakerError:
            error_msg = "Alignment circuit breaker is open"
            logger.error(f"[JOB {job_id}] {error_msg}")
            raise RuntimeError(error_msg)
        except Exception as e:
            error_msg = f"Forced alignment generation failed: {e!s}"
            logger.error(f"[JOB {job_id}] {error_msg}")
            raise RuntimeError(error_msg)

    def _extract_detected_language(
        self, local_alignment_json: str, fallback_language: str
    ) -> str:
        """Extract language_strategy from alignment JSON."""
        if not local_alignment_json or not os.path.exists(local_alignment_json):
            return fallback_language

        try:
            with open(local_alignment_json, encoding="utf-8") as fh:
                alignment_data = json.load(fh)
            return alignment_data.get("language_strategy", fallback_language)
        except Exception as e:
            logger.warning(f"Could not extract language from alignment: {e}")
            return fallback_language

    def _run_audio_upload(self, job_id: str, local_path: str, remote_path: str) -> str:
        """Upload audio to S3."""
        logger.info(f"[JOB {job_id}] Uploading to S3...")

        try:
            from services.circuit_breaker import get_circuit_breaker

            s3_breaker = get_circuit_breaker(
                "S3Download", failure_threshold=5, reset_timeout=60
            )
            with s3_breaker:
                audio_path = self.storage_manager.upload_audio(
                    job_id, local_path, remote_path
                )
        except CircuitBreakerError:
            error_msg = "S3 circuit breaker is open during audio upload"
            logger.error(f"[JOB {job_id}] {error_msg}")
            raise RuntimeError(error_msg)

        return audio_path

    def _run_alignment_upload(
        self, job_id: str, local_parsed_json: str, output_s3_path: str
    ) -> tuple[str, Optional[float]]:
        """Upload alignment JSON to S3 and extract duration."""
        if not local_parsed_json or not os.path.exists(local_parsed_json):
            error_msg = "Alignment JSON file missing prior to upload"
            logger.error(f"[JOB {job_id}] {error_msg}")
            raise RuntimeError(error_msg)

        try:
            from services.circuit_breaker import get_circuit_breaker

            s3_breaker = get_circuit_breaker(
                "S3Download", failure_threshold=5, reset_timeout=60
            )
            with s3_breaker:
                alignment_s3_path = self.storage_manager.upload_alignment_json(
                    job_id, local_parsed_json, output_s3_path
                )

            # Read alignment_duration_seconds
            alignment_duration_seconds = None
            try:
                with open(local_parsed_json, encoding="utf-8") as fh:
                    aj = json.load(fh)
                alignment_duration_seconds = aj.get("alignment_duration_seconds")
            except Exception:
                pass

            return alignment_s3_path, alignment_duration_seconds

        except CircuitBreakerError:
            error_msg = "S3 circuit breaker open during alignment upload"
            logger.error(f"[JOB {job_id}] {error_msg}")
            raise RuntimeError(error_msg)
        except Exception as e:
            error_msg = f"Failed to upload alignment JSON: {e!s}"
            logger.error(f"[JOB {job_id}] {error_msg}")
            raise RuntimeError(error_msg)

    @staticmethod
    def _build_success_result(
        job_type: str,
        job_id: str,
        audio_path: str,
        audio_duration: float,
        alignment_s3_path: str,
        alignment_duration_seconds: Optional[float],
        job_started_at: datetime,
        cache_hit: bool,
        retry_count: int,
        job_data: dict[str, Any],
        synthesis_duration_seconds: float = 0.0,
    ) -> dict[str, Any]:
        """Build success result dictionary."""
        result = {
            "jobType": job_type,
            "jobId": job_id,
            "status": "completed",
            "audioPath": audio_path,
            "audioDurationSeconds": audio_duration,
            "synthesisDurationSeconds": synthesis_duration_seconds,
            "startedAt": job_started_at.isoformat(),
            "completedAt": datetime.now().isoformat(),
            "cacheHit": cache_hit,
            "retryCount": retry_count,
            "alignmentPath": alignment_s3_path,
            "alignmentDurationSeconds": alignment_duration_seconds,
        }

        # Include ttsId if present
        if "ttsId" in job_data and job_data["ttsId"] is not None:
            result["ttsId"] = job_data["ttsId"]

        # Preserve test flag
        if job_data.get("isTest"):
            result["isTest"] = True

        # Echo Remotion parameters for chaining
        if job_type == "rem":
            for key in ["remotionStyle", "resolution", "aspectRatio", "spokenLang"]:
                if key in job_data:
                    result[key] = job_data[key]

        return result

    @staticmethod
    def _build_failure_result(
        job_type: str,
        job_id: str,
        error_code: str,
        error_message: str,
        retry_count: int,
        job_started_at: datetime,
        job_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Build failure result dictionary."""
        result = {
            "jobType": job_type,
            "jobId": job_id,
            "status": "failed",
            "errorCode": error_code,
            "errorMessage": error_message,
            "retryCount": retry_count,
            "startedAt": job_started_at.isoformat(),
            "completedAt": datetime.now().isoformat(),
            "isTest": job_data.get("isTest", False),
        }

        # Include ttsId if present
        if "ttsId" in job_data and job_data["ttsId"] is not None:
            result["ttsId"] = job_data["ttsId"]

        # Echo Remotion parameters even on failure
        if job_type == "rem":
            for key in ["remotionStyle", "resolution", "aspectRatio", "spokenLang"]:
                if key in job_data:
                    result[key] = job_data[key]

        return result
