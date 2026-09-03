"""
IndexTTS RabbitMQ Worker - Refactored
24/7 background worker for TTS synthesis from RabbitMQ queue.
Orchestrates modular components for synthesis, alignment, and upload.
"""

import json
import logging
import os
import platform
import signal
from pathlib import Path

from dotenv import load_dotenv

from indextts.infer import create_tts_engine
from services.cache_manager import CacheManager
from services.circuit_breaker import get_all_circuit_breaker_stats
from services.logging_config import (
    configure_logging,
    get_logger,
    log_shutdown_summary,
    log_startup_summary,
)
from services.rabbitmq_manager import RabbitMQManager
from services.storage_manager import StorageManager
from services.synthesis_pipeline import SynthesisPipeline

# Load environment variables
env_file = Path(__file__).parent.parent / ".env"
if env_file.exists():
    load_dotenv(str(env_file))


def _env_bool(name: str, default: bool) -> bool:
    """Parse boolean environment variable."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in ("true", "1", "yes")


def _env_int(name: str, default: int) -> int:
    """Parse integer environment variable."""
    value = os.getenv(name)
    if value is None:
        return default
    return int(value)


def _parse_log_level(level_name: str) -> int:
    """Parse log level from string."""
    level = getattr(logging, level_name.upper(), None)
    if isinstance(level, int):
        return level
    return logging.INFO


# Configure logging
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
    """
    Main TTS worker orchestrator.

    Coordinates RabbitMQ consumption with synthesis pipeline.
    Small, focused class that delegates to specialized components.
    """

    def __init__(self, rabbitmq_url: str):
        """
        Initialize the TTS worker.

        Args:
            rabbitmq_url: RabbitMQ connection URL (amqp://user:pass@host:5672/)
        """
        if not rabbitmq_url:
            raise ValueError("RABBITMQ_URL is required")

        self.platform = platform.system()
        self._shutdown_requested = False
        self._processed_jobs = set()

        # Log startup info
        logger.section("STARTUP")
        logger.info(f"Platform:         {self.platform}")
        logger.info(f"Log level:        {logging.getLevelName(_log_level)}")
        if _log_file_enabled:
            logger.info(f"Log file:         {_log_file_path}")

        # Initialize TTS engine
        self.tts_engine = self._init_tts_engine()
        logger.success("TTS engine initialized")

        # Initialize storage manager
        try:
            self.storage_manager = StorageManager()
            logger.success("S3 client initialized")
        except Exception as e:
            logger.warning_icon(
                f"S3 client initialization failed: {e}. Will retry on first use."
            )
            self.storage_manager = None

        # Initialize cache manager
        cache_enabled = os.getenv("TTS_CACHE_ENABLED", "true").lower() == "true"
        cache_max_entries = int(os.getenv("TTS_CACHE_MAX_ENTRIES", "10000"))
        cache_eviction_threshold = int(
            os.getenv("TTS_CACHE_EVICTION_THRESHOLD", "9000")
        )
        cache_dir = os.getenv("TTS_CACHE_LOCAL_DIR", "outputs/tts_cache")

        if cache_enabled:
            self.cache_manager = CacheManager(
                cache_dir=cache_dir,
                max_entries=cache_max_entries,
                eviction_threshold=cache_eviction_threshold,
            )
        else:
            self.cache_manager = CacheManager(cache_dir=cache_dir)
            logger.warning("TTS synthesis cache: DISABLED")

        # Initialize synthesis pipeline
        use_fast_inference = (
            os.getenv("TTS_USE_FAST_INFERENCE", "true").lower() == "true"
        )
        if self.platform != "Darwin":
            inference_method = "infer_fast()" if use_fast_inference else "infer()"
            logger.info(f"TTS inference method: {inference_method}")
        else:
            logger.info("TTS inference method: infer() (macOS native)")

        normalization_enabled = (
            os.getenv("TTS_NORMALIZATION_ENABLED", "true").lower() == "true"
        )
        normalization_target_lufs = float(
            os.getenv("TTS_NORMALIZATION_TARGET_LUFS", "-16.0")
        )

        if normalization_enabled:
            logger.info(
                f"Audio normalization: ENABLED (target: {normalization_target_lufs:.1f} LUFS)"
            )
        else:
            logger.info("Audio normalization: DISABLED")

        self.synthesis_pipeline = SynthesisPipeline(
            tts_engine=self.tts_engine,
            storage_manager=self.storage_manager,
            cache_manager=self.cache_manager,
            use_fast_inference=use_fast_inference,
            normalization_enabled=normalization_enabled,
            normalization_target_lufs=normalization_target_lufs,
        )
        logger.success("Synthesis pipeline initialized")

        # Initialize RabbitMQ manager
        self.rabbitmq_manager = RabbitMQManager(rabbitmq_url)

        # Setup signal handlers
        self._setup_signal_handlers()

    def _init_tts_engine(self):
        """Initialize TTS engine based on platform."""
        if self.platform == "Darwin":
            logger.info("Initializing macOS native TTS engine (language: en-US)")
            return create_tts_engine(use_native_macos=True, language="en-US")
        else:
            logger.info("Initializing IndexTTS GPU inference engine")
            return create_tts_engine(
                use_native_macos=False,
                cfg_path="checkpoints/config.yaml",
                model_dir="checkpoints",
                is_fp16=True,
                use_cuda_kernel=False,
            )

    def _setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown."""

        def signal_handler(signum, frame):
            """Handle shutdown signals."""
            signal_name = signal.Signals(signum).name
            logger.info(f"\n{signal_name} received, initiating graceful shutdown...")
            self._shutdown_requested = True
            self.rabbitmq_manager.request_shutdown()

            # Immediately stop consuming to unblock start_consuming()
            if (
                self.rabbitmq_manager.channel
                and not self.rabbitmq_manager.channel.is_closed
            ):
                try:
                    self.rabbitmq_manager.channel.stop_consuming()
                    logger.info("Message consumption stopped")
                except Exception as e:
                    logger.warning_icon(f"Error stopping consumption: {e}")

        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)
        logger.success("Signal handlers registered (SIGTERM, SIGINT)")

    def start(self):
        """
        Start the worker and begin consuming jobs from RabbitMQ.
        """
        # Initial connection
        try:
            self.rabbitmq_manager.connect()
        except Exception as e:
            logger.failure(f"Initial connection failed: {e}")
            logger.info("Will attempt to reconnect...")
            if not self.rabbitmq_manager.reconnect_with_backoff():
                logger.failure("Failed to establish initial connection, exiting")
                return

        # Log connection summary
        logger.subsection("CONNECTIONS")
        logger.info(f"S3 Misc Bucket:      {self.storage_manager.s3_misc_bucket}")
        logger.info(f"R2 Voice Bucket:     {self.storage_manager.r2_voice_bucket}")
        logger.info("")

        # Log circuit breaker status
        cb_stats = get_all_circuit_breaker_stats()
        log_startup_summary(
            logger,
            platform=self.platform,
            s3_misc_bucket=self.storage_manager.s3_misc_bucket,
            r2_voice_bucket=self.storage_manager.r2_voice_bucket,
            rabbitmq_host=self.rabbitmq_manager.rabbitmq_host,
            stats_dict=cb_stats,
        )

        def message_callback(ch, method, properties, body):
            """Handle incoming job message."""
            if self._shutdown_requested:
                logger.info("Shutdown requested, rejecting new message")
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
                return

            job_data = None
            try:
                job_data = json.loads(body)
                job_id = (
                    job_data.get("jobId")
                    if job_data.get("jobId") is not None
                    else job_data.get("job_id")
                )
                logger.info(f"[JOB {job_id}] Received from queue")

                # Process job through pipeline
                result = self.synthesis_pipeline.process_job(job_data)

                # Publish result
                self.rabbitmq_manager.publish_result(result)
                if result.get("ttsId"):
                    logger.info(
                        f"[JOB {job_id}] Result published with ttsId={result.get('ttsId')}"
                    )

                # Acknowledge message
                self.rabbitmq_manager.acknowledge_message(method.delivery_tag)
                logger.info(f"[JOB {job_id}] Acknowledged")

                # Track processed jobs
                self._processed_jobs.add(job_id)

            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON in message: {e!s}")
                self.rabbitmq_manager.reject_message(method.delivery_tag, requeue=False)

            except Exception as e:
                logger.error(f"Error processing job: {e!s}")
                if job_data:
                    job_id = (
                        job_data.get("jobId")
                        if job_data.get("jobId") is not None
                        else job_data.get("job_id")
                    )
                    logger.error(f"[JOB {job_id}] Processing failed, sending to DLQ")
                self.rabbitmq_manager.reject_message(method.delivery_tag, requeue=False)

        # Main consumption loop
        while not self._shutdown_requested:
            try:
                # Ensure connection is healthy
                if not self.rabbitmq_manager.is_connected():
                    logger.warning("Connection is not open, attempting to reconnect...")
                    if not self.rabbitmq_manager.reconnect_with_backoff():
                        break

                # Start consuming (blocking call - will exit when stop_consuming() is called)
                self.rabbitmq_manager.consume_messages(
                    callback=message_callback,
                    prefetch_count=1,
                )

            except KeyboardInterrupt:
                logger.info("\nShutting down worker (KeyboardInterrupt)...")
                self._shutdown_requested = True
                # Stop consuming to unblock start_consuming()
                if self.rabbitmq_manager.channel:
                    self.rabbitmq_manager.channel.stop_consuming()
                break

            except Exception as e:
                logger.error(f"Connection lost or error occurred: {e!s}")
                if not self._shutdown_requested:
                    logger.info("Attempting to reconnect...")
                    if not self.rabbitmq_manager.reconnect_with_backoff():
                        break

        # Graceful shutdown
        cb_stats = get_all_circuit_breaker_stats()
        log_shutdown_summary(
            logger,
            processed_count=len(self._processed_jobs),
            stats_dict=cb_stats,
        )

        self.rabbitmq_manager.disconnect()


if __name__ == "__main__":
    # Read RabbitMQ URL from environment
    rabbitmq_url = os.getenv("RABBITMQ_URL")
    if not rabbitmq_url:
        raise ValueError(
            "RABBITMQ_URL environment variable is required. "
            "See .env.example for configuration template."
        )

    worker = IndexTTSWorker(rabbitmq_url=rabbitmq_url)
    worker.start()
