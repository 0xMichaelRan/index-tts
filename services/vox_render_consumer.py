"""
Vox render consumer.

Runs as a daemon thread inside IndexTTSWorker on Linux/Windows
(skipped on macOS where there is no GPU for renders).

Consumes from ``vox_jobs`` (declared with DLX by this worker) and
publishes rendering results to ``vox_results``.

Usage (from tts_worker.py)::

    consumer = VoxRenderConsumer(
        rabbitmq_url=config.rabbitmq_url,
        ffmpeg_path=config.vox_render_ffmpeg_path,
    )
    consumer.start_in_thread()   # non-blocking, daemon thread
    ...
    consumer.stop()              # graceful shutdown
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any
from urllib.parse import urlparse

import pika

from services.logging_config import get_logger
from services.s3_config import S3Client
from services.vox_render_pipeline import VoxRenderPipeline

logger = get_logger(__name__)

# Queue names
_INPUT_QUEUE = "vox_jobs"
_OUTPUT_QUEUE = "vox_results"

# Reconnect settings (mirror tts_worker pattern)
_INITIAL_RECONNECT_DELAY = 5  # seconds
_MAX_RECONNECT_DELAY = 300  # 5 minutes


class VoxRenderConsumer:
    """
    RabbitMQ consumer for vox_jobs.

    Runs in a background daemon thread alongside the main TTS consumer.
    Uses its own blocking pika connection (separate from the TTS worker's
    connection) so the two consumers don't interfere.

    Args:
        rabbitmq_url: AMQP connection URL.
        ffmpeg_path: Path to ffmpeg binary.
        local_tts_output_dir: Directory where the synthesis pipeline writes
            output (checked before S3 download for audio/alignment files).
    """

    def __init__(
        self,
        rabbitmq_url: str,
        ffmpeg_path: str = "ffmpeg",
        local_tts_output_dir: str = "outputs/tts_output",
    ) -> None:
        self.rabbitmq_url = rabbitmq_url
        self.ffmpeg_path = ffmpeg_path
        self.local_tts_output_dir = local_tts_output_dir

        self._shutdown_requested = False
        self._thread: threading.Thread | None = None

        # RabbitMQ connection state
        self._connection: pika.BlockingConnection | None = None
        self._channel: pika.adapters.blocking_connection.BlockingChannel | None = None
        self._reconnect_delay = _INITIAL_RECONNECT_DELAY

        # Pipeline (initialised lazily in _run)
        self._s3_client: S3Client | None = None
        self._pipeline: VoxRenderPipeline | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_in_thread(self) -> threading.Thread:
        """Start the consumer in a daemon background thread."""
        self._thread = threading.Thread(
            target=self._run,
            name="VoxRenderConsumer",
            daemon=True,
        )
        self._thread.start()
        logger.info("VoxRenderConsumer: started in background thread")
        return self._thread

    def stop(self) -> None:
        """Signal shutdown and unblock the consuming loop."""
        logger.info("VoxRenderConsumer: shutdown requested")
        self._shutdown_requested = True
        try:
            if self._channel and self._channel.is_open:
                self._channel.stop_consuming()
        except Exception as e:
            logger.warning(f"VoxRenderConsumer: error stopping consumption: {e}")

    def is_alive(self) -> bool:
        """Return True if the consumer thread is running."""
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------------
    # Internal run loop
    # ------------------------------------------------------------------

    def _run(self) -> None:
        """Main loop: connect, consume, reconnect on failure."""
        logger.info("VoxRenderConsumer: initialising S3 client and pipeline")
        try:
            self._s3_client = S3Client()
            self._pipeline = VoxRenderPipeline(
                s3_client=self._s3_client,
                ffmpeg_path=self.ffmpeg_path,
                local_tts_output_dir=self.local_tts_output_dir,
            )
            logger.success("VoxRenderConsumer: pipeline ready")
        except Exception as exc:
            logger.error(
                f"VoxRenderConsumer: failed to initialise pipeline, "
                f"consumer will not start: {exc}"
            )
            return

        while not self._shutdown_requested:
            try:
                self._connect()
                self._consume()  # blocks until channel stops
            except Exception as exc:
                if self._shutdown_requested:
                    break
                logger.error(
                    f"VoxRenderConsumer: connection lost — {exc}. "
                    f"Retrying in {self._reconnect_delay}s..."
                )
                time.sleep(self._reconnect_delay)
                self._reconnect_delay = min(
                    self._reconnect_delay * 2, _MAX_RECONNECT_DELAY
                )
            finally:
                self._disconnect()

        logger.info("VoxRenderConsumer: shutdown complete")

    def _connect(self) -> None:
        """Establish a pika blocking connection and declare queues with DLX."""
        parsed = urlparse(self.rabbitmq_url)
        credentials = pika.PlainCredentials(
            username=parsed.username or "guest",
            password=parsed.password or "guest",
        )
        params = pika.ConnectionParameters(
            host=parsed.hostname or "localhost",
            port=parsed.port or 5672,
            virtual_host=parsed.path.lstrip("/") or "/",
            credentials=credentials,
            connection_attempts=3,
            retry_delay=2,
            heartbeat=600,
            blocked_connection_timeout=300,
        )
        self._connection = pika.BlockingConnection([params])
        self._channel = self._connection.channel()

        # --- vox_jobs (input queue, owned by this worker) ---
        self._channel.exchange_declare(
            exchange=f"{_INPUT_QUEUE}.dlx",
            exchange_type="fanout",
            durable=True,
        )
        self._channel.queue_declare(
            queue=f"{_INPUT_QUEUE}_failed",
            durable=True,
            arguments={
                "x-message-ttl": 604800000,  # 7 days
                "x-max-length": 5000,
            },
        )
        self._channel.queue_bind(
            queue=f"{_INPUT_QUEUE}_failed",
            exchange=f"{_INPUT_QUEUE}.dlx",
            routing_key="",
        )
        self._channel.queue_declare(
            queue=_INPUT_QUEUE,
            durable=True,
            arguments={
                "x-dead-letter-exchange": f"{_INPUT_QUEUE}.dlx",
                "x-dead-letter-routing-key": f"{_INPUT_QUEUE}_failed",
                "x-message-ttl": 604800000,  # 7 days
                "x-max-length": 10000,
            },
        )

        # --- vox_results (output queue, owned by this worker) ---
        self._channel.exchange_declare(
            exchange=f"{_OUTPUT_QUEUE}.dlx",
            exchange_type="fanout",
            durable=True,
        )
        self._channel.queue_declare(
            queue=f"{_OUTPUT_QUEUE}_failed",
            durable=True,
            arguments={
                "x-message-ttl": 604800000,
                "x-max-length": 5000,
            },
        )
        self._channel.queue_bind(
            queue=f"{_OUTPUT_QUEUE}_failed",
            exchange=f"{_OUTPUT_QUEUE}.dlx",
            routing_key="",
        )
        self._channel.queue_declare(
            queue=_OUTPUT_QUEUE,
            durable=True,
            arguments={
                "x-dead-letter-exchange": f"{_OUTPUT_QUEUE}.dlx",
                "x-dead-letter-routing-key": f"{_OUTPUT_QUEUE}_failed",
                "x-message-ttl": 604800000,
                "x-max-length": 10000,
            },
        )

        # Reset reconnect delay after successful connect
        self._reconnect_delay = _INITIAL_RECONNECT_DELAY
        logger.success(
            f"VoxRenderConsumer: connected to RabbitMQ, listening on '{_INPUT_QUEUE}'"
        )

    def _consume(self) -> None:
        """Start blocking message consumption (one message at a time)."""
        if not self._channel:
            raise RuntimeError("Channel not initialised")

        self._channel.basic_qos(prefetch_count=1)
        self._channel.basic_consume(
            queue=_INPUT_QUEUE,
            on_message_callback=self._handle_message,
            auto_ack=False,
        )
        self._channel.start_consuming()

    def _disconnect(self) -> None:
        """Safely close the pika connection."""
        try:
            if self._connection and not self._connection.is_closed:
                self._connection.close()
        except Exception:
            pass
        self._connection = None
        self._channel = None

    # ------------------------------------------------------------------
    # Message handler
    # ------------------------------------------------------------------

    def _handle_message(
        self,
        ch: pika.adapters.blocking_connection.BlockingChannel,
        method: pika.spec.Basic.Deliver,
        properties: pika.spec.BasicProperties,
        body: bytes,
    ) -> None:
        """Process a single vox_jobs message."""
        if self._shutdown_requested:
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
            return

        job_id = "unknown"
        try:
            job_data: dict[str, Any] = json.loads(body)
            job_id = str(job_data.get("jobId", "unknown"))
            logger.info(f"[VOX {job_id}] Received vox job from queue")

            if self._pipeline is None:
                raise RuntimeError("Pipeline not initialised")

            result = self._pipeline.process_job(job_data)

            # Publish result to vox_results
            self._publish_result(result)

            ch.basic_ack(delivery_tag=method.delivery_tag)
            logger.info(f"[VOX {job_id}] Acknowledged")

        except json.JSONDecodeError as exc:
            logger.error(f"VoxRenderConsumer: invalid JSON — {exc}")
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

        except Exception as exc:
            logger.error(f"[VOX {job_id}] Unexpected error — sending to DLQ: {exc!s}")
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

    def _publish_result(self, result: dict[str, Any]) -> None:
        """Publish result dict to vox_results queue (with retry)."""
        job_id = result.get("jobId", "unknown")
        max_retries = 3

        for attempt in range(1, max_retries + 1):
            try:
                if not self._channel or self._channel.is_closed:
                    raise RuntimeError("Channel closed before publish")

                self._channel.basic_publish(
                    exchange="",
                    routing_key=_OUTPUT_QUEUE,
                    body=json.dumps(result),
                    properties=pika.BasicProperties(
                        delivery_mode=pika.DeliveryMode.Persistent,
                        content_type="application/json",
                    ),
                )
                logger.info(
                    f"[VOX {job_id}] Result published to '{_OUTPUT_QUEUE}' "
                    f"(status={result.get('status')})"
                )
                return

            except Exception as exc:
                if attempt == max_retries:
                    logger.error(
                        f"[VOX {job_id}] Failed to publish result after "
                        f"{max_retries} attempts: {exc}"
                    )
                    raise
                delay = 2**attempt
                logger.warning(
                    f"[VOX {job_id}] Publish attempt {attempt} failed: {exc}. "
                    f"Retrying in {delay}s..."
                )
                time.sleep(delay)
