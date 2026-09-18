"""
IndexTTS RabbitMQ Worker - Refactored
24/7 background worker for TTS synthesis from RabbitMQ queue.
Orchestrates modular components for synthesis, alignment, and upload.
"""

import json
import platform
import signal
from pathlib import Path

from dotenv import load_dotenv

from indextts.infer import create_tts_engine
from services.cache_manager import CacheManager
from services.circuit_breaker import get_all_circuit_breaker_stats
from services.vox_render_consumer import VoxRenderConsumer
from services.logging_config import (
    configure_logging,
    get_logger,
    log_shutdown_summary,
    log_startup_summary,
)
from services.job_utils import extract_job_id
from services.rabbitmq_config import MQ_PRIORITY_DEFAULT, MQ_PRIORITY_MAX
from services.rabbitmq_manager import RabbitMQManager
from services.s3_registry import list_buckets
from services.storage_manager import StorageManager
from services.synthesis_pipeline import SynthesisPipeline
from services.worker_config import WorkerConfig

# Load environment variables
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    load_dotenv(str(_env_file))


class IndexTTSWorker:
    """
    Main TTS worker orchestrator.

    Coordinates RabbitMQ consumption with synthesis pipeline.
    Small, focused class that delegates to specialized components.
    """

    def __init__(self, config: WorkerConfig):
        """
        Initialize the TTS worker.

        Args:
            config: WorkerConfig instance with all runtime settings.
                    Build from environment with ``WorkerConfig.from_env()``,
                    or construct directly in tests.
        """
        config.validate()
        self.config = config
        self.platform = platform.system()
        self._shutdown_requested = False
        self._processed_jobs = set()

        # Configure logging from config (must happen before any logger use)
        configure_logging(
            level=config.log_level,
            use_file=config.log_file_enabled,
            file_path=config.log_file_path,
            use_color=True,
        )

        # Module-level logger is now bound after logging is configured
        global logger
        logger = get_logger(__name__)

        # Log startup info
        logger.section("STARTUP")
        logger.info(f"Platform:         {self.platform}")
        logger.info(f"Log level:        {config.log_level_name}")
        if config.log_file_enabled:
            logger.info(f"Log file:         {config.log_file_path}")

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
        if config.cache_enabled:
            self.cache_manager = CacheManager(
                cache_dir=config.cache_dir,
                max_entries=config.cache_max_entries,
                eviction_threshold=config.cache_eviction_threshold,
            )
        else:
            self.cache_manager = CacheManager(cache_dir=config.cache_dir)
            logger.warning("TTS synthesis cache: DISABLED")

        # Initialize synthesis pipeline
        if self.platform != "Darwin":
            inference_method = (
                "infer_fast()" if config.use_fast_inference else "infer()"
            )
            logger.info(f"TTS inference method: {inference_method}")
        else:
            logger.info("TTS inference method: infer() (macOS native)")

        if config.normalization_enabled:
            logger.info(
                f"Audio normalization: ENABLED "
                f"(target: {config.normalization_target_lufs:.1f} LUFS)"
            )
        else:
            logger.info("Audio normalization: DISABLED")

        self.synthesis_pipeline = SynthesisPipeline(
            tts_engine=self.tts_engine,
            storage_manager=self.storage_manager,
            cache_manager=self.cache_manager,
            use_fast_inference=config.use_fast_inference,
            normalization_enabled=config.normalization_enabled,
            normalization_target_lufs=config.normalization_target_lufs,
        )
        logger.success("Synthesis pipeline initialized")

        # Initialize RabbitMQ manager
        self.rabbitmq_manager = RabbitMQManager(config.rabbitmq_url)

        # Initialize vox render consumer (all platforms, if enabled)
        self.vox_render_consumer: VoxRenderConsumer | None = None
        if config.vox_render_enabled:
            self.vox_render_consumer = VoxRenderConsumer(
                rabbitmq_url=config.rabbitmq_url,
                ffmpeg_path=config.vox_render_ffmpeg_path,
                local_tts_output_dir="outputs/tts_output",
            )
            logger.info(
                f"Vox render consumer: ENABLED "
                f"(ffmpeg: {config.vox_render_ffmpeg_path})"
            )
        else:
            logger.info("Vox render consumer: DISABLED (VOX_RENDER_ENABLED=false)")

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

            # Stop the vox render consumer if running
            if self.vox_render_consumer is not None:
                self.vox_render_consumer.stop()

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

    def _handle_message(self, ch, method, properties, body):
        """Handle a single incoming RabbitMQ job message."""
        if self._shutdown_requested:
            logger.info("Shutdown requested, rejecting new message")
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
            return

        job_data = None
        try:
            job_data = json.loads(body)
            job_id = extract_job_id(job_data)

            # Resolve priority: AMQP header takes precedence over JSON field
            amqp_priority = getattr(properties, "priority", None)
            if amqp_priority is not None:
                priority = int(amqp_priority)
            else:
                priority = int(job_data.get("priority", MQ_PRIORITY_DEFAULT))
            # Clamp to valid range
            priority = max(0, min(priority, MQ_PRIORITY_MAX))

            logger.info(f"[JOB {job_id}] Received from queue (priority={priority})")

            # Process job through pipeline
            result = self.synthesis_pipeline.process_job(job_data)

            # Publish result with same priority as the inbound job
            self.rabbitmq_manager.publish_result(result, priority=priority)
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
                job_id = extract_job_id(job_data)
                logger.error(f"[JOB {job_id}] Processing failed, sending to DLQ")
            self.rabbitmq_manager.reject_message(method.delivery_tag, requeue=False)

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

        # Log S3 registry
        logger.subsection("CONNECTIONS")
        for cfg in list_buckets():
            logger.info(f"S3 [{cfg.type:6s}] {cfg.bucket_name} @ {cfg.endpoint_url}")
        logger.info("")

        # Log circuit breaker status
        cb_stats = get_all_circuit_breaker_stats()
        log_startup_summary(
            logger,
            platform=self.platform,
            s3_buckets=list_buckets(),
            rabbitmq_host=self.rabbitmq_manager.rabbitmq_host,
            stats_dict=cb_stats,
        )

        # Start vox render consumer thread (if enabled)
        if self.vox_render_consumer is not None:
            self.vox_render_consumer.start_in_thread()
            logger.success("VoxRenderConsumer thread started")

        # Main consumption loop
        while not self._shutdown_requested:
            try:
                # Ensure connection is healthy
                if not self.rabbitmq_manager.is_connected():
                    logger.warning("Connection is not open, attempting to reconnect...")
                    if not self.rabbitmq_manager.reconnect_with_backoff():
                        break

                # Start consuming (blocking call)
                self.rabbitmq_manager.consume_messages(
                    callback=self._handle_message,
                    prefetch_count=1,
                )

            except KeyboardInterrupt:
                logger.info("\nShutting down worker (KeyboardInterrupt)...")
                self._shutdown_requested = True
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


# Module-level logger placeholder — real logger is set inside __init__
# after configure_logging() runs.  This allows the module to be imported
# without immediately emitting log output.
logger = get_logger(__name__)


if __name__ == "__main__":
    config = WorkerConfig.from_env()
    config.validate()

    worker = IndexTTSWorker(config=config)
    worker.start()
