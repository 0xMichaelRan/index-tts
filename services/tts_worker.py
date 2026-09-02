"""
IndexTTS RabbitMQ Worker
24/7 background worker for TTS synthesis from RabbitMQ queue.
- Consumes TTS requests from RabbitMQ
- Synthesizes audio using IndexTTS engine
- Uploads results to S3
- Updates status back to RabbitMQ
"""

import asyncio
import json
import logging
import os
import platform
import shutil
import signal
import threading
import time
import wave
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pika
from dotenv import load_dotenv

from indextts.infer import create_tts_engine
from services.circuit_breaker import (
    CircuitBreakerError,
    get_all_circuit_breaker_stats,
    get_circuit_breaker,
)
from services.idempotent_upload import IdempotentUploader
from services.logging_config import (
    configure_logging,
    get_logger,
    log_shutdown_summary,
    log_startup_summary,
)
from services.s3_config import S3Client, S3ConfigError

# Import cache components
try:
    from app.database import DatabaseSession, check_db_connection
    from app.cache_service import TTSCacheService

    CACHE_AVAILABLE = True
except ImportError as e:
    logging.warning("Cache dependencies not available: %s", e)
    CACHE_AVAILABLE = False

# Import alignment service
try:
    from services.alignment import AlignmentService

    ALIGNMENT_AVAILABLE = True
except ImportError as e:
    logging.warning("AlignmentService not available: %s", e)
    AlignmentService = None  # type: ignore[assignment,misc]
    ALIGNMENT_AVAILABLE = False

# Load environment variables from .env file
env_file = Path(__file__).parent.parent / ".env"
if env_file.exists():
    load_dotenv(str(env_file))


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in ("true", "1", "yes")


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    return int(value)


def _parse_log_level(level_name: str) -> int:
    level = getattr(logging, level_name.upper(), None)
    if isinstance(level, int):
        return level
    return logging.INFO


def _build_s3_output_path(
    job_type: str,
    job_id: str,
    language: str,
    ratio: float,
    environment: str,
    voice_id: int | None,
    file_extension: str,
) -> str:
    """
    Build S3 output path with new structure.

    Format: {job_type}/{YYYYMMDD}/{job_id}/{filename}.{ext}
    Filename: {language}_r{ratio}_{environment}[_voice{voice_id}].{ext}

    Args:
        job_type: "studio", "playground", or "rem"
        job_id: Job identifier
        language: Language code (e.g., "zh", "en", "mixed")
        ratio: Speed ratio (e.g., 1.0, 1.2, 0.8)
        environment: Environment name (e.g., "prod", "dev", "staging")
        voice_id: Voice ID (include in filename if > 0)
        file_extension: File extension without dot (e.g., "mp3", "json")

    Returns:
        S3 path string

    Examples:
        >>> _build_s3_output_path("studio", "abc123", "zh", 1.0, "prod", 42, "mp3")
        'studio/20260902/abc123/zh_r10_prod_voice42.mp3'
        >>> _build_s3_output_path("playground", "xyz", "en", 1.5, "dev", 0, "wav")
        'playground/20260902/xyz/en_r15_dev.wav'
        >>> _build_s3_output_path("studio", "def", "en", 0.7, "prod", 0, "mp3")
        'studio/20260902/def/en_r07_prod.mp3'
    """
    # Get current date in local timezone (YYYYMMDD format)
    date_str = datetime.now().strftime("%Y%m%d")

    # Format ratio: remove decimal point and zero-pad (e.g., 1.0→r10, 1.2→r12, 0.7→r07)
    ratio_int = int(ratio * 10)
    ratio_str = f"r{ratio_int:02d}"

    # Build filename components
    filename_parts = [
        language,
        ratio_str,
        environment,
    ]

    # Add voice_id only if it's greater than 0
    if voice_id and voice_id > 0:
        filename_parts.append(f"voice{voice_id}")

    filename = "_".join(filename_parts) + f".{file_extension}"

    # Build full path: {job_type}/{YYYYMMDD}/{job_id}/{filename}
    return f"{job_type}/{date_str}/{job_id}/{filename}"


# Configure structured logging from environment
_log_level = _parse_log_level(os.getenv("LOG_LEVEL", "INFO"))
_log_file_enabled = _env_bool("LOG_FILE_ENABLED", False)
_log_file_path = os.getenv("LOG_FILE_PATH", "logs/worker.log")

configure_logging(
    level=_log_level,
    use_file=_log_file_enabled,
    file_path=_log_file_path,
    use_color=True,
)
logger = get_logger(__name__)


class IndexTTSWorker:
    """Worker for processing TTS jobs from RabbitMQ queue."""

    def __init__(self, rabbitmq_url: str):
        """
        Initialize the TTS worker.

        Args:
            rabbitmq_url: RabbitMQ connection URL (e.g., amqp://user:pass@host:5672/)

        Note: S3 configuration is read from environment variables by S3Client.
        """
        # RabbitMQ configuration
        if not rabbitmq_url:
            raise ValueError("RABBITMQ_URL is required")

        self.rabbitmq_url = rabbitmq_url

        # Parse URL to extract host for logging
        try:
            parsed = urlparse(rabbitmq_url)
            self.rabbitmq_host = parsed.hostname or "localhost"
        except Exception:
            self.rabbitmq_host = "localhost"

        self.platform = platform.system()

        # S3 bucket names (will be set by S3Client during initialization)
        self.s3_misc_bucket = None
        self.r2_voice_bucket = None

        logger.section("STARTUP")
        logger.info(f"Platform:         {self.platform}")
        logger.info(f"Log level:        {logging.getLevelName(_log_level)}")
        if _log_file_enabled:
            logger.info(f"Log file:         {_log_file_path}")

        # Initialize TTS engine
        self._init_tts_engine()
        logger.success("TTS engine initialized")

        # Initialize alignment service (CPU, Whisper small — mandatory)
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
                raise  # Alignment is mandatory — worker must not start without it
        else:
            self.alignment_service = None
            logger.failure("AlignmentService unavailable — worker cannot start")
            raise RuntimeError("AlignmentService is required but not available")

        # Initialize S3 client (reads config from environment variables)
        try:
            self.s3_client = S3Client()
            # Store bucket names for logging
            self.s3_misc_bucket = self.s3_client.storage_bucket_name
            self.r2_voice_bucket = self.s3_client.output_bucket_name
            logger.success("S3 client initialized")
        except Exception as e:
            logger.warning_icon(
                f"S3 client initialization failed: {e}. Will retry on first use."
            )
            self.s3_client = None
            self.s3_misc_bucket = "N/A"
            self.r2_voice_bucket = "N/A"

        # Initialize idempotent uploader
        self.uploader = None
        try:
            self.uploader = IdempotentUploader(self.s3_client)
            logger.success("Idempotent uploader initialized")
        except Exception as e:
            logger.warning_icon(
                f"Uploader initialization failed: {e}. Will initialize on first use."
            )
            self.uploader = None

        # Initialize circuit breakers (thresholds/timeouts from .env)
        logger.subsection("Initializing Circuit Breakers")
        s3_failure_threshold = _env_int("CIRCUIT_BREAKER_S3_FAILURE_THRESHOLD", 5)
        s3_reset_timeout = _env_int("CIRCUIT_BREAKER_S3_RESET_TIMEOUT", 60)
        tts_failure_threshold = _env_int("CIRCUIT_BREAKER_TTS_FAILURE_THRESHOLD", 3)
        tts_reset_timeout = _env_int("CIRCUIT_BREAKER_TTS_RESET_TIMEOUT", 30)
        alignment_failure_threshold = _env_int(
            "CIRCUIT_BREAKER_ALIGNMENT_FAILURE_THRESHOLD", 3
        )
        alignment_reset_timeout = _env_int(
            "CIRCUIT_BREAKER_ALIGNMENT_RESET_TIMEOUT", 60
        )

        self.s3_breaker = get_circuit_breaker(
            name="S3Download",
            failure_threshold=s3_failure_threshold,
            reset_timeout=s3_reset_timeout,
            half_open_max_calls=3,
            success_threshold=2,
        )

        self.tts_breaker = get_circuit_breaker(
            name="IndexTTS",
            failure_threshold=tts_failure_threshold,
            reset_timeout=tts_reset_timeout,
            half_open_max_calls=2,
            success_threshold=2,
        )

        self.alignment_breaker = get_circuit_breaker(
            name="Alignment",
            failure_threshold=alignment_failure_threshold,
            reset_timeout=alignment_reset_timeout,
            half_open_max_calls=2,
            success_threshold=2,
        )

        # Placeholder for RabbitMQ connection
        self.connection = None
        self.channel = None

        # Tracking for idempotent operations
        self._processed_jobs = set()  # Track completed job IDs for deduplication

        # Cache configuration
        self.cache_enabled = (
            CACHE_AVAILABLE and os.getenv("TTS_CACHE_ENABLED", "true").lower() == "true"
        )
        self.cache_max_entries = int(os.getenv("TTS_CACHE_MAX_ENTRIES", "10000"))
        self.cache_eviction_threshold = int(
            os.getenv("TTS_CACHE_EVICTION_THRESHOLD", "9000")
        )
        self.cache_dir = os.getenv("TTS_CACHE_LOCAL_DIR", "outputs/tts_cache")

        if self.cache_enabled:
            logger.info("TTS synthesis cache: ENABLED")
            logger.info(f"  Max entries: {self.cache_max_entries}")
            logger.info(f"  Eviction threshold: {self.cache_eviction_threshold}")
            logger.info(f"  Cache directory: {self.cache_dir}")
        else:
            logger.warning("TTS synthesis cache: DISABLED")

        # Inference method configuration (Windows/Linux only)
        self.use_fast_inference = (
            os.getenv("TTS_USE_FAST_INFERENCE", "true").lower() == "true"
        )
        if self.platform != "Darwin":
            inference_method = "infer_fast()" if self.use_fast_inference else "infer()"
            logger.info(f"TTS inference method: {inference_method}")
        else:
            logger.info("TTS inference method: infer() (macOS native)")

        # Audio normalization configuration
        self.normalization_enabled = (
            os.getenv("TTS_NORMALIZATION_ENABLED", "true").lower() == "true"
        )
        self.normalization_target_lufs = float(
            os.getenv("TTS_NORMALIZATION_TARGET_LUFS", "-16.0")
        )

        logger.info(
            f"Audio normalization: {'ENABLED' if self.normalization_enabled else 'DISABLED'}"
        )
        if self.normalization_enabled:
            logger.info(f"  Target LUFS: {self.normalization_target_lufs:.1f} dB")

        # Graceful shutdown support
        self._shutdown_requested = False

        # Reconnection tracking
        self._reconnect_delay = 5  # Initial reconnection delay in seconds
        self._max_reconnect_delay = 300  # Maximum delay (5 minutes)
        self._reconnect_attempts = 0

        self._setup_signal_handlers()

    def _init_tts_engine(self):
        """Initialize the appropriate TTS engine based on platform."""
        if self.platform == "Darwin":
            logger.info("Initializing macOS native TTS engine (language: en-US)")
            self.tts = create_tts_engine(use_native_macos=True, language="en-US")
        else:
            logger.info("Initializing IndexTTS GPU inference engine")
            self.tts = create_tts_engine(
                use_native_macos=False,
                cfg_path="checkpoints/config.yaml",
                model_dir="checkpoints",
                is_fp16=True,
                use_cuda_kernel=False,
            )

    def _setup_signal_handlers(self):
        """Set up signal handlers for graceful shutdown."""

        def signal_handler(signum, frame):
            """Handle shutdown signals."""
            signal_name = signal.Signals(signum).name
            logger.info(f"\n{signal_name} received, initiating graceful shutdown...")
            self._shutdown_requested = True

            # Stop consuming new messages
            if self.channel and not self.channel.is_closed:
                try:
                    self.channel.stop_consuming()
                    logger.info("Stopped consuming new messages")
                except Exception as e:
                    logger.warning_icon(f"Error stopping consumer: {e}")

        # Register handlers for SIGTERM and SIGINT
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)
        logger.success("Signal handlers registered (SIGTERM, SIGINT)")

    def connect_rabbitmq(self):
        """
        Connect to RabbitMQ using the configured URL.
        Supports CloudAMQP and standard RabbitMQ URLs.
        Implements automatic reconnection with exponential backoff.
        """
        try:
            host_display = (
                self.rabbitmq_url.split("@")[1]
                if "@" in self.rabbitmq_url
                else self.rabbitmq_host
            )
            logger.subsection(f"Connecting to RabbitMQ ({host_display})")

            # Parse the URL to extract components
            parsed = urlparse(self.rabbitmq_url)

            credentials = pika.PlainCredentials(
                username=parsed.username or "guest",
                password=parsed.password or "guest",
            )

            connection_params = pika.ConnectionParameters(
                host=parsed.hostname or "localhost",
                port=parsed.port or 5672,
                virtual_host=parsed.path.lstrip("/") or "/",
                credentials=credentials,
                connection_attempts=3,
                retry_delay=2,
                heartbeat=600,
                blocked_connection_timeout=300,
            )

            self.connection = pika.BlockingConnection([connection_params])
            self.channel = self.connection.channel()

            # Declare Dead Letter Exchange (DLX) for failed messages
            # Pattern: {queue_name}.dlx (fanout exchange for consistency with other workers)
            self.channel.exchange_declare(
                exchange="tts_jobs.dlx",
                exchange_type="fanout",
                durable=True,
            )

            # Declare Dead Letter Queue (DLQ) to store failed messages
            # Pattern: {queue_name}_failed (standardized suffix)
            self.channel.queue_declare(
                queue="tts_jobs_failed",
                durable=True,
                arguments={
                    "x-message-ttl": 604800000,  # 7 days TTL (must match rabbitmq_config.py)
                    "x-max-length": 5000,
                },
            )

            # Bind DLQ to DLX (fanout exchange doesn't require routing key)
            self.channel.queue_bind(
                queue="tts_jobs_failed",
                exchange="tts_jobs.dlx",
                routing_key="",
            )

            # Declare main queue with DLX arguments
            self.channel.queue_declare(
                queue="tts_jobs",
                durable=True,
                arguments={
                    "x-dead-letter-exchange": "tts_jobs.dlx",
                    "x-dead-letter-routing-key": "tts_jobs_failed",
                    "x-message-ttl": 86400000,  # 24 hours TTL (must match rabbitmq_config.py)
                    "x-max-length": 10000,
                    "x-overflow": "reject-publish",
                },
            )

            logger.success("Connected to RabbitMQ")
            logger.info("  DLX: tts_jobs.dlx → tts_jobs_failed (dead letter queue)")

            # Reset reconnection tracking on successful connection
            self._reconnect_attempts = 0
            self._reconnect_delay = 5

        except Exception as e:
            logger.failure(f"Failed to connect to RabbitMQ: {e!s}")
            raise

    def _is_connection_open(self) -> bool:
        """Check if RabbitMQ connection is open and healthy."""
        try:
            return (
                self.connection is not None
                and self.connection.is_open
                and self.channel is not None
                and self.channel.is_open
            )
        except Exception:
            return False

    def _reconnect_with_backoff(self) -> bool:
        """
        Attempt to reconnect to RabbitMQ with exponential backoff.

        Returns:
            True if reconnection successful, False if shutdown requested
        """
        while not self._shutdown_requested:
            self._reconnect_attempts += 1

            logger.warning(
                f"Attempting to reconnect to RabbitMQ "
                f"(attempt {self._reconnect_attempts}, waiting {self._reconnect_delay}s)..."
            )

            time.sleep(self._reconnect_delay)

            try:
                # Close old connection if it exists
                self.disconnect_rabbitmq()

                # Attempt new connection
                self.connect_rabbitmq()

                logger.success(
                    f"Successfully reconnected to RabbitMQ after {self._reconnect_attempts} attempts"
                )
                return True

            except Exception as e:
                logger.error(
                    f"Reconnection attempt {self._reconnect_attempts} failed: {e}"
                )

                # Exponential backoff with maximum delay
                self._reconnect_delay = min(
                    self._reconnect_delay * 2, self._max_reconnect_delay
                )

        return False

    def disconnect_rabbitmq(self):
        """Safely close RabbitMQ connection."""
        try:
            if self.connection and not self.connection.is_closed:
                self.connection.close()
                logger.success("Disconnected from RabbitMQ")
        except Exception as e:
            logger.warning_icon(f"Error disconnecting from RabbitMQ: {e!s}")

    async def _process_cache_lookup_async(
        self, job_id: str, text: str, audio_prompt_path: str, ratio: float
    ) -> tuple[bool, str | None]:
        """
        Async helper to lookup and process cache hit.

        Returns:
            (cache_hit, cached_audio_path) tuple
        """
        try:
            async with DatabaseSession() as db_session:
                cache_service = TTSCacheService(db_session, self.cache_dir)
                cache_entry = await cache_service.lookup(text, audio_prompt_path)

                if cache_entry:
                    # Cache HIT - reuse base audio
                    logger.success(f"[JOB {job_id}] Cache HIT - reusing base audio")

                    if ratio != 1.0:
                        # Apply time-stretching to cached audio
                        cached_audio_path = self._apply_ratio_to_cached_audio(
                            cache_entry.base_audio_local_path, ratio, job_id
                        )
                    else:
                        # Just copy cached audio
                        cached_audio_path = self._copy_cached_audio(
                            cache_entry.base_audio_local_path, job_id
                        )

                    # Check for eviction (in background, doesn't affect this job)
                    await cache_service.evict_old_entries(
                        max_entries=self.cache_max_entries,
                        evict_count=self.cache_eviction_threshold,
                    )

                    return (True, cached_audio_path)
        except Exception as e:
            logger.warning(
                f"[JOB {job_id}] Cache lookup failed: {e}, falling back to full synthesis"
            )

        return (False, None)

    def _process_cache_lookup(
        self, job_id: str, text: str, audio_prompt_path: str, ratio: float
    ) -> tuple[bool, str | None]:
        """
        Synchronous wrapper for cache lookup using thread + new event loop.
        Avoids event loop attachment issues.
        """
        result_container = {}

        def run_async():
            """Run in separate thread with its own event loop"""
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(
                    self._process_cache_lookup_async(
                        job_id, text, audio_prompt_path, ratio
                    )
                )
                result_container["result"] = result
            except Exception as e:
                result_container["error"] = e
            finally:
                loop.close()

        thread = threading.Thread(target=run_async)
        thread.start()
        thread.join(timeout=10.0)  # 10 second timeout

        if "error" in result_container:
            logger.warning(
                f"[JOB {job_id}] Cache lookup failed: {result_container['error']}"
            )
            return (False, None)

        return result_container.get("result", (False, None))

    async def _process_cache_store_async(
        self,
        job_id: str,
        text: str,
        audio_prompt_path: str,
        base_audio_path: str,
        language: str,
        synthesis_start: float,
    ) -> None:
        """
        Async helper to store synthesis result in cache.

        Note: All cached audio is stored at ratio=1.0 (base speed).
        Time-stretching is applied separately when needed.
        """
        try:
            async with DatabaseSession() as db_session:
                cache_service = TTSCacheService(db_session, self.cache_dir)
                audio_duration = self._get_audio_duration(base_audio_path)
                synthesis_duration = time.time() - synthesis_start

                await cache_service.store(
                    text=text,
                    audio_prompt_path=audio_prompt_path,
                    base_audio_local_path=base_audio_path,
                    audio_duration_seconds=audio_duration,
                    synthesis_duration_ms=int(synthesis_duration * 1000),
                    language=language,
                )

                logger.success(f"[JOB {job_id}] Base audio cached for future reuse")
        except Exception as e:
            logger.warning(f"[JOB {job_id}] Failed to cache synthesis: {e}")

    def _process_cache_store(
        self,
        job_id: str,
        text: str,
        audio_prompt_path: str,
        base_audio_path: str,
        language: str,
        synthesis_start: float,
    ) -> None:
        """
        Synchronous wrapper for cache storage using thread + new event loop.

        Note: All cached audio is stored at ratio=1.0 (base speed).
        Time-stretching is applied separately when needed.
        """

        def run_async():
            """Run in separate thread with its own event loop"""
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(
                    self._process_cache_store_async(
                        job_id,
                        text,
                        audio_prompt_path,
                        base_audio_path,
                        language,
                        synthesis_start,
                    )
                )
            except Exception as e:
                logger.warning(f"[JOB {job_id}] Cache store failed: {e}")
            finally:
                loop.close()

        thread = threading.Thread(target=run_async)
        thread.start()
        thread.join(timeout=10.0)  # 10 second timeout

    def process_job(self, job_data: dict[str, Any]) -> dict[str, Any]:
        """
        Process a single TTS job with synthesis caching and circuit breaker protection.

        Cache Strategy:
        - Lookup: Check if (text, voice) already synthesized
        - Cache Hit: Copy base audio + time-stretch if ratio != 1.0
        - Cache Miss: Full synthesis at ratio=1.0, store in cache, then time-stretch if needed

        Args:
            job_data: Job message containing:
                - job_id: Unique job identifier
                - text: Text to synthesize
                - audio_prompt_path: S3 path to audio prompt file (e.g., "audio-prompts/voice_001.wav")
                - language: Language code
                - job_type: "studio", "playground", or "rem" (Remotion)
                - output_path_template: S3 output path template (e.g., "tts-audio/studio/{job_id}.mp3")
                - ratio: Speech speed ratio (0.5=slow, 1.0=normal, 2.0=fast)

        Returns:
            Result dictionary with:
                - job_id: Original job ID
                - job_type: Job type (included for routing in backend)
                - status: "completed" or "failed"
                - audio_path: S3 path to generated audio
                - alignment_path: S3 path to alignment JSON (mandatory)
                - audio_duration_seconds: Duration of synthesized audio
                - synthesis_duration_seconds: Time taken for TTS synthesis (in seconds)
                - audioPath: S3 path to generated audio
                - alignmentPath: S3 path to alignment JSON (mandatory)
                - audioDurationSeconds: Duration of synthesized audio
                - synthesisDurationSeconds: Time taken for TTS synthesis (in seconds)
                - startedAt: ISO 8601 timestamp when processing started
                - completedAt: ISO 8601 timestamp when processing completed
                - cacheHit: Whether cache was used
                - errorCode: Error code (if failed)
                - errorMessage: Error message (if failed)
                - retryCount: Number of retries attempted
        """
        job_id = job_data.get("jobId") if job_data.get("jobId") is not None else job_data.get("job_id")
        # Ensure job_id is a string (may come as integer from backend)
        if job_id is not None:
            job_id = str(job_id)

        text = job_data.get("text", "")
        audio_prompt_path = job_data.get("audioPromptPath") or job_data.get("audio_prompt_path")
        language = job_data.get("language", "en")
        job_type = job_data.get("jobType") or job_data.get("job_type", "studio")

        # Validate job_type (support studio, playground, rem)
        if job_type not in ("studio", "playground", "rem"):
            logger.error(
                f"[JOB {job_id}] Invalid job_type: {job_type}, defaulting to 'studio'"
            )
            job_type = "studio"

        # Note: output_path_template is legacy and no longer used; paths are built dynamically
        ratio = job_data.get("ratio", 1.0)
        environment = job_data.get("environment", "prod")
        voice_id = job_data.get("voiceId") if job_data.get("voiceId") is not None else job_data.get("voice_id", 0)

        retry_count = 0
        max_retries = 3

        logger.info(
            f"[JOB {job_id}] Processing TTS request (type: {job_type}, language: {language}, ratio: {ratio})"
        )

        # Check for duplicate processing
        if job_id in self._processed_jobs:
            logger.warning(f"[JOB {job_id}] Already processed, skipping")
            return {
                "jobType": job_type,
                "jobId": job_id,
                "status": "completed",
                "note": "duplicate_skipped",
                "completedAt": datetime.now().isoformat(),
            }

        # Track precise timing
        job_started_at = datetime.now()
        job_start_time = time.time()  # For calculating total_duration (logging only)
        synthesis_start = time.time()  # Initialize for both cache hit and miss paths
        local_audio_prompt = None
        local_output = None
        local_alignment_json = None  # parsed JSON — uploaded then deleted
        cache_hit = False

        # Retry loop for transient failures
        while retry_count < max_retries:
            try:
                # Step 1: Check synthesis cache (if enabled)
                cached_audio_path = None
                if self.cache_enabled:
                    cache_hit, cached_audio_path = self._process_cache_lookup(
                        job_id, text, audio_prompt_path, ratio
                    )
                    if cache_hit and cached_audio_path:
                        local_output = cached_audio_path

                # Step 2: If no cache hit, perform full synthesis
                if not cache_hit:
                    # Download audio prompt from S3
                    logger.info(f"[JOB {job_id}] Downloading audio prompt from S3...")
                    try:
                        with self.s3_breaker:
                            local_audio_prompt = self._download_audio_prompt(
                                job_id, audio_prompt_path
                            )
                    except CircuitBreakerError:
                        error_msg = "S3 circuit breaker is open - service unavailable"
                        logger.error(f"[JOB {job_id}] {error_msg}")
                        return {
                            "jobType": job_type,
                            "jobId": job_id,
                            "status": "failed",
                            "errorCode": "S3_CIRCUIT_OPEN",
                            "errorMessage": error_msg,
                            "retryCount": retry_count,
                            "startedAt": job_started_at.isoformat(),
                            "completedAt": datetime.now().isoformat(),
                        }

                    # Synthesize audio at ratio 1.0 (for caching)
                    logger.info(f"[JOB {job_id}] Synthesizing audio...")
                    synthesis_start = time.time()

                    try:
                        with self.tts_breaker:
                            base_audio_path = self._synthesize_audio(
                                job_id=job_id,
                                text=text,
                                audio_prompt=local_audio_prompt,
                                audio_prompt_s3_path=audio_prompt_path,
                                language=language,
                                ratio=1.0,  # ALWAYS synthesize at 1.0 for caching
                            )
                    except CircuitBreakerError:
                        error_msg = "TTS circuit breaker is open - service unavailable"
                        logger.error(f"[JOB {job_id}] {error_msg}")
                        return {
                            "jobType": job_type,
                            "jobId": job_id,
                            "status": "failed",
                            "errorCode": "TTS_CIRCUIT_OPEN",
                            "errorMessage": error_msg,
                            "retryCount": retry_count,
                            "startedAt": job_started_at.isoformat(),
                            "completedAt": datetime.now().isoformat(),
                        }

                    # Store in cache (if enabled)
                    if self.cache_enabled:
                        self._process_cache_store(
                            job_id,
                            text,
                            audio_prompt_path,
                            base_audio_path,
                            language,
                            synthesis_start,
                        )

                    # Apply time-stretching if ratio != 1.0
                    if ratio != 1.0:
                        local_output = self._apply_ratio_to_cached_audio(
                            base_audio_path, ratio, job_id
                        )
                    else:
                        local_output = base_audio_path

                # Step 3: Forced alignment (stable-whisper, CPU) — mandatory for all job types
                output_dir = os.path.join("outputs", "tts_output", job_id)

                try:
                    with self.alignment_breaker:
                        (
                            _local_raw_json,
                            _local_srt,
                            local_alignment_json,
                        ) = self._align_audio(
                            job_id=job_id,
                            local_output=local_output,
                            text=text,
                            language=language,
                            output_dir=output_dir,
                        )
                except CircuitBreakerError:
                    error_msg = "Alignment circuit breaker is open"
                    logger.error(f"[JOB {job_id}] {error_msg}")
                    return {
                        "jobType": job_type,
                        "jobId": job_id,
                        "status": "failed",
                        "errorCode": "ALIGNMENT_CIRCUIT_OPEN",
                        "errorMessage": error_msg,
                        "retryCount": retry_count,
                        "startedAt": job_started_at.isoformat(),
                        "completedAt": datetime.now().isoformat(),
                    }
                except Exception as e:
                    # Alignment generation failure MUST fail the job for ALL job types
                    error_msg = f"Forced alignment generation failed: {e!s}"
                    logger.error(f"[JOB {job_id}] {error_msg}")
                    return {
                        "jobType": job_type,
                        "jobId": job_id,
                        "status": "failed",
                        "errorCode": "FORCED_ALIGNMENT_FAILED",
                        "errorMessage": error_msg,
                        "retryCount": retry_count,
                        "startedAt": job_started_at.isoformat(),
                        "completedAt": datetime.now().isoformat(),
                    }

                # Step 3.5: Extract detected language from alignment JSON
                detected_language = language  # fallback to job language
                if local_alignment_json and os.path.exists(local_alignment_json):
                    try:
                        with open(local_alignment_json, encoding="utf-8") as fh:
                            alignment_data = json.load(fh)
                        detected_language = alignment_data.get(
                            "language_strategy", language
                        )
                    except Exception as e:
                        logger.warning(
                            f"[JOB {job_id}] Could not extract language from alignment: {e}"
                        )

                # Step 3.6: Build S3 output paths using detected language
                file_extension = Path(local_output).suffix.lstrip(
                    "."
                )  # e.g., "mp3" or "wav"
                output_s3_path = _build_s3_output_path(
                    job_type=job_type,
                    job_id=job_id,
                    language=detected_language,
                    ratio=ratio,
                    environment=environment,
                    voice_id=voice_id,
                    file_extension=file_extension,
                )

                # Step 4: Upload audio to S3 with idempotent retry
                logger.info(f"[JOB {job_id}] Uploading to S3...")

                try:
                    with self.s3_breaker:
                        audio_path = self._upload_to_s3_idempotent(
                            job_id, local_output, output_s3_path
                        )
                except CircuitBreakerError:
                    error_msg = "S3 circuit breaker is open during audio upload"
                    logger.error(f"[JOB {job_id}] {error_msg}")
                    return {
                        "jobType": job_type,
                        "jobId": job_id,
                        "status": "failed",
                        "errorCode": "S3_UPLOAD_CIRCUIT_OPEN",
                        "errorMessage": error_msg,
                        "retryCount": retry_count,
                        "startedAt": job_started_at.isoformat(),
                        "completedAt": datetime.now().isoformat(),
                    }

                # Step 5: Upload parsed alignment JSON sidecar to S3 (mandatory for all job types)
                alignment_s3_path = None
                alignment_duration_seconds = None
                if not local_alignment_json or not os.path.exists(local_alignment_json):
                    error_msg = "Alignment JSON file missing prior to upload"
                    logger.error(f"[JOB {job_id}] {error_msg}")
                    return {
                        "jobType": job_type,
                        "jobId": job_id,
                        "status": "failed",
                        "errorCode": "FORCED_ALIGNMENT_FAILED",
                        "errorMessage": error_msg,
                        "retryCount": retry_count,
                        "startedAt": job_started_at.isoformat(),
                        "completedAt": datetime.now().isoformat(),
                    }

                try:
                    with self.s3_breaker:
                        alignment_s3_path = self._upload_alignment(
                            job_id=job_id,
                            local_parsed_json=local_alignment_json,
                            output_s3_path=output_s3_path,
                        )
                    # Read back alignment_duration_seconds from the uploaded JSON
                    try:
                        with open(local_alignment_json, encoding="utf-8") as fh:
                            _aj = json.load(fh)
                        alignment_duration_seconds = _aj.get(
                            "alignment_duration_seconds"
                        )
                    except Exception:
                        pass
                except CircuitBreakerError:
                    error_msg = "S3 circuit breaker open during alignment upload"
                    logger.error(f"[JOB {job_id}] {error_msg}")
                    return {
                        "jobType": job_type,
                        "jobId": job_id,
                        "status": "failed",
                        "errorCode": "ALIGNMENT_UPLOAD_CIRCUIT_OPEN",
                        "errorMessage": error_msg,
                        "retryCount": retry_count,
                        "startedAt": job_started_at.isoformat(),
                        "completedAt": datetime.now().isoformat(),
                    }
                except Exception as e:
                    # Alignment upload failure MUST fail the job for ALL job types
                    error_msg = f"Failed to upload alignment JSON: {e!s}"
                    logger.error(f"[JOB {job_id}] {error_msg}")
                    return {
                        "jobType": job_type,
                        "jobId": job_id,
                        "status": "failed",
                        "errorCode": "FORCED_ALIGNMENT_UPLOAD_FAILED",
                        "errorMessage": error_msg,
                        "retryCount": retry_count,
                        "startedAt": job_started_at.isoformat(),
                        "completedAt": datetime.now().isoformat(),
                    }

                # Step 6: Calculate audio duration
                audio_duration = self._get_audio_duration(local_output)

                # Calculate synthesis duration (TTS only, excluding I/O and upload)
                synthesis_duration = time.time() - synthesis_start

                # Calculate total job duration (for logging)
                total_duration = time.time() - job_start_time

                # Mark as processed
                self._processed_jobs.add(job_id)

                result = {
                    "jobType": job_type,
                    "jobId": job_id,
                    "status": "completed",
                    "audioPath": audio_path,
                    "audioDurationSeconds": audio_duration,
                    "synthesisDurationSeconds": round(synthesis_duration, 2),
                    "startedAt": job_started_at.isoformat(),
                    "completedAt": datetime.now().isoformat(),
                    "cacheHit": cache_hit,
                    "retryCount": retry_count,
                    "alignmentPath": alignment_s3_path,
                    "alignmentDurationSeconds": alignment_duration_seconds,
                }
                
                # Preserve isTest flag if present (for test job chaining)
                if job_data.get("isTest"):
                    result["isTest"] = True

                cache_status = "cache HIT" if cache_hit else "full synthesis"
                logger.success(
                    f"[JOB {job_id}] Completed in {total_duration:.2f}s ({cache_status})"
                )
                return result

            except (S3ConfigError, OSError) as e:
                # Retryable errors
                retry_count += 1
                if retry_count < max_retries:
                    delay = 2**retry_count  # Exponential backoff: 2, 4, 8 seconds
                    logger.warning(
                        f"[JOB {job_id}] Attempt {retry_count}/{max_retries} failed: {e!s}. "
                        f"Retrying in {delay}s..."
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        f"[JOB {job_id}] All {max_retries} attempts failed: {e!s}"
                    )
                    return {
                        "jobType": job_type,
                        "jobId": job_id,
                        "status": "failed",
                        "errorCode": "RETRYABLE_ERROR_EXHAUSTED",
                        "errorMessage": str(e),
                        "retryCount": retry_count,
                        "startedAt": job_started_at.isoformat(),
                        "completedAt": datetime.now().isoformat(),
                        "isTest": job_data.get("isTest", False),  # Preserve test flag
                    }

            except Exception as e:
                # Non-retryable errors
                logger.error(f"[JOB {job_id}] Non-retryable error: {e!s}")
                return {
                    "jobType": job_type,
                    "jobId": job_id,
                    "status": "failed",
                    "errorCode": "NON_RETRYABLE_ERROR",
                    "errorMessage": str(e),
                    "retryCount": retry_count,
                    "startedAt": job_started_at.isoformat(),
                    "completedAt": datetime.now().isoformat(),
                    "isTest": job_data.get("isTest", False),  # Preserve test flag
                }

            finally:
                # Always clean up temporary files (but NOT cached base audio)
                if local_audio_prompt:
                    self._cleanup_local_files(local_audio_prompt)
                # Only clean up output if it's not in cache directory
                if local_output and not local_output.startswith(
                    str(Path(self.cache_dir))
                ):
                    self._cleanup_local_files(local_output)
                # Parsed alignment JSON is uploaded then removed (plan §7 / §8)
                # Raw alignment JSON and SRT are intentionally NOT cleaned up
                if local_alignment_json:
                    self._cleanup_local_files(local_alignment_json)

    def _download_audio_prompt(
        self,
        job_id: str,
        audio_prompt_path: str,
    ) -> str:
        """
        Download audio prompt from S3 storage bucket with retry logic.

        Args:
            job_id: Job identifier
            audio_prompt_path: S3 path to audio prompt (e.g., "audio-prompts/voice_123.wav")
                              Can also be voice_id (int) for backwards compatibility

        Returns:
            Local file path to downloaded audio

        Raises:
            S3ConfigError: If download fails after retries
            ValueError: If audio_prompt_path is invalid
        """
        if not self.s3_client:
            self.s3_client = S3Client()

        # Create temp directory for downloads
        temp_dir = os.path.join("outputs", "temp", job_id)
        os.makedirs(temp_dir, exist_ok=True)

        local_path = os.path.join(temp_dir, os.path.basename(audio_prompt_path))

        # Download from storage bucket (where voices are stored)
        self.s3_client.download_file(
            remote_path=audio_prompt_path,
            local_path=local_path,
            bucket_type="storage",
            max_retries=3,
        )

        logger.info(f"[JOB {job_id}] Downloaded audio prompt to {local_path}")
        return local_path

    def _synthesize_audio(
        self,
        job_id: str,
        text: str,
        audio_prompt: str | None,
        audio_prompt_s3_path: str | None,
        language: str,
        ratio: float = 1.0,
    ) -> str:
        """
        Synthesize audio using TTS engine.

        NOTE: When called from process_job() for caching, ratio is ALWAYS 1.0.
        Time-stretching is applied separately after synthesis.

        Args:
            job_id: Job identifier
            text: Text to synthesize
            audio_prompt: Local path to audio prompt file
            audio_prompt_s3_path: S3 source path (for cache key)
            language: Language code
            ratio: Speech ratio (always 1.0 for caching)

        Returns:
            Local path to synthesized base audio (ratio=1.0)

        Raises:
            Exception: If synthesis fails
        """
        # Create cache directory for base audio (semantic naming for cache)
        if ratio == 1.0 and self.cache_enabled:
            # Store in cache directory with semantic filename
            from app.cache_service import TTSCacheService

            cache_dir = Path(self.cache_dir)
            cache_dir.mkdir(parents=True, exist_ok=True)

            # Generate semantic filename for better debugging
            # Format: {text_preview}_{voice_id}.wav (e.g., "hello_world_001.wav")
            output_filename = TTSCacheService.generate_semantic_filename(
                text, audio_prompt_s3_path or ""
            )
            output_path = str(cache_dir / output_filename)
        else:
            # Non-cacheable output (custom ratio)
            output_dir = os.path.join("outputs", "tts_output", job_id)
            os.makedirs(output_dir, exist_ok=True)

            timestamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
            output_filename = f"{job_id}_{timestamp}.wav"
            output_path = os.path.join(output_dir, output_filename)

        logger.info(f"[JOB {job_id}] Synthesizing to {output_path} (ratio: {ratio})")

        # Platform-specific synthesis
        if platform.system() == "Darwin":
            # macOS native TTS
            self.tts.infer(
                audio_prompt=None,
                text=text,
                output_path=output_path,
                ratio=ratio,
                language=language,
            )
        else:
            # IndexTTS GPU inference — language is auto-detected from text
            # Voice caching strategy:
            # - We track S3 paths at the worker level for cache hit detection
            # - But we let infer/infer_fast use local paths for its internal cache comparison
            # - After each inference, we keep the S3 path for next job's comparison

            if audio_prompt_s3_path:
                # Check if this is the same voice as previous job (by S3 path)
                is_same_voice = self.tts.cache_audio_prompt == audio_prompt_s3_path

                if not is_same_voice:
                    # New voice - need to clear cache and load audio
                    logger.info(
                        f"[JOB {job_id}] Loading new voice (S3: {audio_prompt_s3_path})"
                    )
                    self.tts.cache_audio_prompt = None
                    self.tts.cache_cond_mel = None
                    # Now infer/infer_fast will load the audio and cache it
                else:
                    # Same voice - trick infer/infer_fast into thinking this is the same file
                    # by temporarily setting cache_audio_prompt to the current local path
                    logger.info(
                        f"[JOB {job_id}] Reusing cached voice (S3: {audio_prompt_s3_path})"
                    )
                    # Override the comparison: make infer/infer_fast think the local path matches cache
                    self.tts.cache_audio_prompt = audio_prompt

                # Run inference - it will either load audio (if cache was cleared) or reuse (if paths match)
                # NOTE: ratio parameter is NOT used by IndexTTS - we handle time-stretching separately
                if self.use_fast_inference:
                    self.tts.infer_fast(
                        audio_prompt=audio_prompt,
                        text=text,
                        output_path=output_path,
                        ratio=1.0,  # Always 1.0 for base audio
                        enable_normalization=self.normalization_enabled,
                        target_lufs=self.normalization_target_lufs,
                    )
                else:
                    self.tts.infer(
                        audio_prompt=audio_prompt,
                        text=text,
                        output_path=output_path,
                        ratio=1.0,  # Always 1.0 for base audio
                    )

                # CRITICAL: Store S3 path as cache key for next job comparison
                # This allows the NEXT job to detect if it's using the same voice
                self.tts.cache_audio_prompt = audio_prompt_s3_path
                # cache_cond_mel is already set by infer/infer_fast, we keep it
            else:
                # Fallback for jobs without S3 path metadata
                logger.warning(
                    f"[JOB {job_id}] No S3 path provided, voice caching disabled"
                )
                if self.use_fast_inference:
                    self.tts.infer_fast(
                        audio_prompt=audio_prompt,
                        text=text,
                        output_path=output_path,
                        ratio=1.0,  # Always 1.0 for base audio
                        enable_normalization=self.normalization_enabled,
                        target_lufs=self.normalization_target_lufs,
                    )
                else:
                    self.tts.infer(
                        audio_prompt=audio_prompt,
                        text=text,
                        output_path=output_path,
                        ratio=1.0,  # Always 1.0 for base audio
                    )

        logger.info(f"[JOB {job_id}] Synthesis complete: {output_path}")
        return output_path

    def _copy_cached_audio(self, base_audio_path: str, job_id: str) -> str:
        """
        Copy cached base audio to job-specific location.

        Used when ratio=1.0 (no time-stretching needed).

        Args:
            base_audio_path: Path to cached base audio
            job_id: Job identifier

        Returns:
            Path to copied audio file
        """
        output_dir = os.path.join("outputs", "tts_output", job_id)
        os.makedirs(output_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
        output_path = os.path.join(output_dir, f"{job_id}_{timestamp}.wav")

        shutil.copy(base_audio_path, output_path)
        logger.info(f"[JOB {job_id}] Copied cached audio (ratio=1.0)")

        return output_path

    def _apply_ratio_to_cached_audio(
        self, base_audio_path: str, ratio: float, job_id: str
    ) -> str:
        """
        Apply time-stretching to cached base audio.

        Creates a copy of base audio and applies time-stretching.

        Args:
            base_audio_path: Path to cached base audio
            ratio: Time stretch ratio (0.5=slow, 2.0=fast)
            job_id: Job identifier

        Returns:
            Path to time-stretched audio file
        """
        output_dir = os.path.join("outputs", "tts_output", job_id)
        os.makedirs(output_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
        output_path = os.path.join(output_dir, f"{job_id}_{timestamp}.wav")

        # Copy base file
        shutil.copy(base_audio_path, output_path)

        # Apply time-stretching in-place
        logger.info(
            f"[JOB {job_id}] Applying time stretch to cached audio (ratio={ratio})"
        )
        self._apply_time_stretch_to_file(output_path, ratio, job_id)

        return output_path

    def _apply_time_stretch_to_file(self, audio_path: str, ratio: float, job_id: str):
        """
        Apply time-stretching to audio file in-place.

        Uses librosa's time_stretch to adjust playback speed while preserving pitch.
        This implements the ratio parameter for TTS synthesis:
        - ratio > 1.0: Speed up (e.g., 2.0 = 2x faster, half duration)
        - ratio = 1.0: No change (normal speed)
        - ratio < 1.0: Slow down (e.g., 0.5 = 2x slower, double duration)

        Args:
            audio_path: Path to audio file (WAV format)
            ratio: Time stretch ratio (0.5=slow, 1.0=normal, 2.0=fast)
            job_id: Job identifier for logging

        Raises:
            Exception: If time-stretching fails
        """
        import librosa
        import soundfile as sf

        try:
            # Load audio file
            audio, sr = librosa.load(audio_path, sr=None)

            # Calculate original duration
            original_duration = len(audio) / sr

            # Apply time stretching (ratio > 1.0 speeds up, < 1.0 slows down)
            stretched = librosa.effects.time_stretch(audio, rate=ratio)

            # Calculate new duration
            new_duration = len(stretched) / sr

            # Save back to the same file (in-place modification)
            sf.write(audio_path, stretched, sr)

            logger.info(
                f"[JOB {job_id}] Time stretch applied successfully "
                f"(original: {original_duration:.2f}s → new: {new_duration:.2f}s)"
            )

        except Exception as e:
            logger.error(f"[JOB {job_id}] Time stretching failed: {e}")
            # Don't raise - continue with unstretched audio
            # This ensures the job doesn't fail completely if time-stretching fails
            logger.warning(
                f"[JOB {job_id}] Continuing with original audio (no time stretch)"
            )

    def _align_audio(
        self,
        job_id: str,
        local_output: str,
        text: str,
        language: str,
        output_dir: str,
    ) -> tuple[str, str, str]:
        """
        Run forced alignment on the final delivered audio.

        Must be called **after** time-stretching and **before** S3 upload so that
        the timestamps match the uploaded waveform exactly (plan §3 ordering rule).

        Args:
            job_id:      Job identifier.
            local_output: Path to the final WAV (post time-stretch / post normalization).
            text:        Raw job text as received from RabbitMQ.
            language:    Language hint from the job payload.
            output_dir:  Directory where artefacts are written (e.g. outputs/tts_output/{job_id}).

        Returns:
            ``(raw_json_path, srt_path, parsed_json_path)`` — per plan §7 file lifecycle.

        Raises:
            RuntimeError: If alignment fails (ALIGNMENT_FAILED).
            ValueError:   If text is empty (ALIGNMENT_INVALID_INPUT).
            FileNotFoundError: If local_output missing (ALIGNMENT_AUDIO_NOT_FOUND).
        """
        return self.alignment_service.align_to_files(
            job_id=job_id,
            audio_path=local_output,
            text=text,
            language_hint=language,
            output_dir=output_dir,
        )

    def _upload_alignment(
        self,
        job_id: str,
        local_parsed_json: str,
        output_s3_path: str,
    ) -> str:
        """
        Upload parsed alignment JSON sidecar to the S3 output bucket.

        S3 path is derived from the audio path by replacing the file extension:
            ``studio/20260902/abc123/zh_ratio1-0_prod.mp3``
            → ``studio/20260902/abc123/zh_ratio1-0_prod.json``

        The SRT and raw Whisper JSON are NOT uploaded — they remain on local disk.

        Args:
            job_id:           Job identifier.
            local_parsed_json: Local path to the parsed alignment JSON.
            output_s3_path:   S3 path of the uploaded audio file.

        Returns:
            S3 key of the uploaded alignment JSON.

        Raises:
            S3ConfigError: If upload fails after retries.
        """
        # Replace the file extension with .json
        alignment_s3_path = output_s3_path.rsplit(".", 1)[0] + ".json"

        logger.info(
            f"[JOB {job_id}] Uploading alignment JSON sidecar → {alignment_s3_path}"
        )

        if not self.uploader:
            self.uploader = IdempotentUploader(self.s3_client)

        s3_path = self.uploader.upload_with_retry(
            job_id=job_id,
            local_path=local_parsed_json,
            remote_path=alignment_s3_path,
            verify_integrity=True,
        )

        logger.info(f"[JOB {job_id}] Alignment JSON uploaded: {s3_path}")
        return s3_path

    def _upload_to_s3_idempotent(
        self,
        job_id: str,
        local_path: str,
        remote_path: str,
    ) -> str:
        """
        Upload synthesized audio to S3 with idempotent retry.

        Implements idempotent retry by checking if file already exists
        with matching job_id metadata tag before uploading.

        Features:
        - Check if file already uploaded (idempotent check)
        - Skip upload if file exists with matching job_id
        - Exponential backoff retry on failure (base: 2s, multiplier: 2)
        - Metadata tagging with job_id and status
        - Partial failure recovery support

        Args:
            job_id: Job identifier
            local_path: Local file path
            remote_path: S3 destination path

        Returns:
            S3 path (remote_path)

        Raises:
            S3ConfigError: If upload fails after retries
        """
        if not self.uploader:
            self.uploader = IdempotentUploader(self.s3_client)

        logger.info(f"[JOB {job_id}] Starting idempotent S3 upload")

        try:
            # Use idempotent uploader with retry logic
            s3_path = self.uploader.upload_with_retry(
                job_id=job_id,
                local_path=local_path,
                remote_path=remote_path,
                verify_integrity=True,
            )

            logger.info(f"[JOB {job_id}] Upload completed: {s3_path}")
            return s3_path

        except S3ConfigError as e:
            logger.error(f"[JOB {job_id}] Upload failed: {e}")
            raise

    def _get_audio_duration(self, audio_path: str) -> float:
        """
        Get duration of audio file in seconds.

        Supports WAV files using the wave module.

        Args:
            audio_path: Path to audio file (must be .wav format)

        Returns:
            Duration in seconds

        Raises:
            FileNotFoundError: If audio file doesn't exist
            ValueError: If file is not a valid WAV file
        """
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        try:
            with wave.open(audio_path, "r") as audio_file:
                frames = audio_file.getnframes()
                sample_rate = audio_file.getframerate()
                duration = frames / float(sample_rate)
                return duration
        except wave.Error as e:
            raise ValueError(f"Invalid WAV file {audio_path}: {e}")
        except Exception as e:
            logger.warning(f"Could not read audio duration from {audio_path}: {e}")
            # Fallback: estimate based on file size (very rough approximation)
            # WAV at 24kHz, 16-bit mono: ~48000 bytes/sec
            file_size = os.path.getsize(audio_path)
            estimated_duration = file_size / 48000.0
            logger.info(
                f"Using estimated duration: {estimated_duration:.2f}s based on file size"
            )
            return estimated_duration

    def _cleanup_local_files(self, *paths: str):
        """Remove local temporary files."""
        for path in paths:
            if not path:
                continue
            try:
                if os.path.exists(path):
                    os.remove(path)
                    logger.debug(f"Removed: {path}")
            except Exception as e:
                logger.warning(f"Failed to remove {path}: {e!s}")

    def publish_result(self, result: dict[str, Any]):
        """
        Publish job result back to RabbitMQ tts_results queue with retry.

        Implements exponential backoff retry for RabbitMQ ack failures.
        If all retries fail after S3 upload success, triggers partial failure
        recovery and logs critical error for manual intervention.

        Partial Failure Handling:
        - If S3 upload succeeded but RabbitMQ publish fails
        - File is safely stored in S3
        - Manual intervention required to update database
        - Recovery metadata is logged for debugging

        Args:
            result: Job result dictionary with status and metadata
        """
        max_retries = 3
        retry_count = 0
        job_id = result.get("jobId") or result.get("job_id")

        while retry_count < max_retries:
            try:
                # Check connection health before publishing
                if not self._is_connection_open():
                    logger.warning(
                        f"[JOB {job_id}] Connection closed, attempting to reconnect before publishing..."
                    )
                    if not self._reconnect_with_backoff():
                        raise Exception("Failed to reconnect to RabbitMQ")

                self.channel.basic_publish(
                    exchange="",
                    routing_key="tts_results",
                    body=json.dumps(result),
                    properties=pika.BasicProperties(
                        delivery_mode=pika.DeliveryMode.Persistent,
                        content_type="application/json",
                    ),
                )
                logger.info(f"✓ Published result for job {job_id}")
                return

            except (
                pika.exceptions.ConnectionClosedByBroker,
                pika.exceptions.AMQPConnectionError,
                pika.exceptions.StreamLostError,
            ) as e:
                # Connection errors - attempt reconnect
                retry_count += 1
                if retry_count < max_retries:
                    delay = 2**retry_count
                    logger.warning(
                        f"[JOB {job_id}] Connection error publishing result (attempt {retry_count}/{max_retries}): {e}. "
                        f"Retrying in {delay}s..."
                    )
                    time.sleep(delay)
                    # Try to reconnect
                    try:
                        if not self._reconnect_with_backoff():
                            raise Exception("Reconnection failed")
                    except Exception as reconnect_error:
                        logger.error(
                            f"[JOB {job_id}] Reconnection failed: {reconnect_error}"
                        )
                else:
                    logger.critical(
                        f"[JOB {job_id}] ✗ Failed to publish result after {max_retries} attempts: {e}"
                    )
                    self._handle_publish_failure(job_id, result, e)

            except Exception as e:
                retry_count += 1
                if retry_count < max_retries:
                    delay = 2**retry_count  # Exponential backoff: 2, 4, 8 seconds
                    logger.warning(
                        f"[JOB {job_id}] Failed to publish result (attempt {retry_count}/{max_retries}): {e}. "
                        f"Retrying in {delay}s..."
                    )
                    time.sleep(delay)
                else:
                    logger.critical(
                        f"[JOB {job_id}] ✗ Failed to publish result after {max_retries} attempts: {e}"
                    )
                    self._handle_publish_failure(job_id, result, e)

    def _handle_publish_failure(
        self, job_id: str, result: dict[str, Any], error: Exception
    ):
        """Handle failure to publish result after all retries exhausted."""
        # If result contains audio_path, S3 upload likely succeeded
        if result.get("status") == "completed" and result.get("audio_path"):
            logger.critical(
                f"[JOB {job_id}] PARTIAL FAILURE DETECTED: "
                f"S3 upload succeeded but RabbitMQ publish failed"
            )

            # Trigger recovery for partial failure
            if self.uploader:
                recovery_data = self.uploader.handle_partial_failure(
                    job_id=job_id,
                    remote_path=result.get("audio_path"),
                    error=error,
                )
                logger.critical(f"Recovery data: {json.dumps(recovery_data, indent=2)}")

        # Do not raise - we've done our best, avoid requeueing the original message

    def start(self):
        """
        Start the worker and begin consuming jobs from RabbitMQ.
        Implements automatic reconnection on connection loss.
        """
        # Initial connection
        try:
            self.connect_rabbitmq()
        except Exception as e:
            logger.failure(f"Initial connection failed: {e}")
            logger.info("Will attempt to reconnect...")
            if not self._reconnect_with_backoff():
                logger.failure("Failed to establish initial connection, exiting")
                return

        # Log connection summary
        logger.subsection("CONNECTIONS")
        logger.info(f"S3 Misc Bucket:      {self.s3_misc_bucket}")
        logger.info(f"R2 Voice Bucket:     {self.r2_voice_bucket}")
        logger.info("")

        # Log circuit breaker status
        cb_stats = get_all_circuit_breaker_stats()
        log_startup_summary(
            logger,
            platform=self.platform,
            s3_misc_bucket=self.s3_misc_bucket,
            r2_voice_bucket=self.r2_voice_bucket,
            rabbitmq_host=self.rabbitmq_host,
            stats_dict=cb_stats,
        )

        def callback(ch, method, properties, body):
            """Handle incoming job message."""
            # Check for shutdown request before processing
            if self._shutdown_requested:
                logger.info("Shutdown requested, rejecting new message")
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
                return

            job_data = None
            try:
                job_data = json.loads(body)
                job_id = job_data.get("jobId") if job_data.get("jobId") is not None else job_data.get("job_id")
                logger.info(f"[JOB {job_id}] Received from queue")

                result = self.process_job(job_data)
                self.publish_result(result)

                # Acknowledge message only after successful processing
                ch.basic_ack(delivery_tag=method.delivery_tag)
                logger.info(f"[JOB {job_id}] Acknowledged")

            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON in message: {e!s}")
                # Reject without requeue - invalid messages go to DLQ
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

            except CircuitBreakerError as e:
                logger.error(f"Circuit breaker open: {e!s}")
                # Requeue when circuit breaker is open - might succeed later
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

            except Exception as e:
                logger.error(f"Error processing job: {e!s}")
                if job_data:
                    job_id = job_data.get("jobId") if job_data.get("jobId") is not None else job_data.get("job_id")
                    logger.error(f"[JOB {job_id}] Processing failed, sending to DLQ")
                # Reject without requeue - failed jobs go to DLQ after retries
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

        # Main consumption loop with automatic reconnection
        while not self._shutdown_requested:
            try:
                # Ensure connection is healthy
                if not self._is_connection_open():
                    logger.warning("Connection is not open, attempting to reconnect...")
                    if not self._reconnect_with_backoff():
                        break

                # Set QoS to process one message at a time
                self.channel.basic_qos(prefetch_count=1)

                # Set up consumer
                self.channel.basic_consume(
                    queue="tts_jobs",
                    on_message_callback=callback,
                    auto_ack=False,
                )

                # Start consuming
                logger.info("Starting message consumption...")
                self.channel.start_consuming()

            except KeyboardInterrupt:
                logger.info("\nShutting down worker (KeyboardInterrupt)...")
                self._shutdown_requested = True
                break

            except (
                pika.exceptions.ConnectionClosedByBroker,
                pika.exceptions.AMQPConnectionError,
                pika.exceptions.StreamLostError,
            ) as e:
                # Connection errors - attempt to reconnect
                logger.error(f"Connection lost: {e!s}")
                if not self._shutdown_requested:
                    logger.info("Connection lost, attempting to reconnect...")
                    if not self._reconnect_with_backoff():
                        break

            except Exception as e:
                logger.failure(f"Unexpected error: {e!s}")
                if not self._shutdown_requested:
                    logger.info("Attempting to reconnect after unexpected error...")
                    if not self._reconnect_with_backoff():
                        break

        # Graceful shutdown: Print statistics
        cb_stats = get_all_circuit_breaker_stats()
        log_shutdown_summary(
            logger,
            processed_count=len(self._processed_jobs),
            stats_dict=cb_stats,
        )

        self.disconnect_rabbitmq()


if __name__ == "__main__":
    # Read RabbitMQ URL from environment (supports CloudAMQP URLs)
    rabbitmq_url = os.getenv("RABBITMQ_URL")
    if not rabbitmq_url:
        raise ValueError(
            "RABBITMQ_URL environment variable is required. "
            "See .env.example for configuration template."
        )

    worker = IndexTTSWorker(rabbitmq_url=rabbitmq_url)
    worker.start()
