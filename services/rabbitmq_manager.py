"""
RabbitMQ connection management with automatic reconnection and DLX support.
"""

import json
import logging
import time
from typing import Any, Callable
from urllib.parse import urlparse

import pika

from services.logging_config import get_logger

logger = get_logger(__name__)


class RabbitMQManager:
    """Manages RabbitMQ connection lifecycle, reconnection, and queue setup."""

    def __init__(self, rabbitmq_url: str):
        """
        Initialize RabbitMQ manager.

        Args:
            rabbitmq_url: RabbitMQ connection URL (amqp://user:pass@host:5672/)
        """
        if not rabbitmq_url:
            raise ValueError("RABBITMQ_URL is required")

        self.rabbitmq_url = rabbitmq_url
        self.connection = None
        self.channel = None

        # Parse URL for logging
        try:
            parsed = urlparse(rabbitmq_url)
            self.rabbitmq_host = parsed.hostname or "localhost"
            self.host_display = (
                rabbitmq_url.split("@")[1]
                if "@" in rabbitmq_url
                else self.rabbitmq_host
            )
        except Exception:
            self.rabbitmq_host = "localhost"
            self.host_display = "localhost"

        # Reconnection tracking
        self._reconnect_delay = 5  # Initial delay in seconds
        self._max_reconnect_delay = 300  # Maximum delay (5 minutes)
        self._reconnect_attempts = 0
        self._shutdown_requested = False

    def connect(self) -> None:
        """
        Connect to RabbitMQ and setup queues with DLX pattern.

        Raises:
            Exception: If connection fails
        """
        try:
            logger.subsection(f"Connecting to RabbitMQ ({self.host_display})")

            # Parse connection parameters
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

            # Setup DLX infrastructure
            self._setup_dlx_queues()

            logger.success("Connected to RabbitMQ")
            logger.info("  DLX: tts_jobs.dlx → tts_jobs_failed (dead letter queue)")

            # Reset reconnection tracking on success
            self._reconnect_attempts = 0
            self._reconnect_delay = 5

        except Exception as e:
            logger.failure(f"Failed to connect to RabbitMQ: {e!s}")
            raise

    def _setup_dlx_queues(self) -> None:
        """Setup Dead Letter Exchange (DLX) pattern for job queues."""
        if not self.channel:
            raise RuntimeError("Channel not initialized")

        # Declare DLX exchange (fanout)
        self.channel.exchange_declare(
            exchange="tts_jobs.dlx",
            exchange_type="fanout",
            durable=True,
        )

        # Declare DLQ (dead letter queue)
        self.channel.queue_declare(
            queue="tts_jobs_failed",
            durable=True,
            arguments={
                "x-message-ttl": 604800000,  # 7 days TTL
                "x-max-length": 5000,
            },
        )

        # Bind DLQ to DLX
        self.channel.queue_bind(
            queue="tts_jobs_failed",
            exchange="tts_jobs.dlx",
            routing_key="",
        )

        # Declare main queue with DLX routing
        self.channel.queue_declare(
            queue="tts_jobs",
            durable=True,
            arguments={
                "x-dead-letter-exchange": "tts_jobs.dlx",
                "x-dead-letter-routing-key": "tts_jobs_failed",
                "x-message-ttl": 86400000,  # 24 hours TTL
                "x-max-length": 10000,
                "x-overflow": "reject-publish",
            },
        )

    def is_connected(self) -> bool:
        """Check if connection is open and healthy."""
        try:
            return (
                self.connection is not None
                and self.connection.is_open
                and self.channel is not None
                and self.channel.is_open
            )
        except Exception:
            return False

    def disconnect(self) -> None:
        """Safely close RabbitMQ connection."""
        try:
            if self.connection and not self.connection.is_closed:
                self.connection.close()
                logger.success("Disconnected from RabbitMQ")
        except Exception as e:
            logger.warning_icon(f"Error disconnecting from RabbitMQ: {e!s}")

    def reconnect_with_backoff(self) -> bool:
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
                self.disconnect()

                # Attempt new connection
                self.connect()

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

    def request_shutdown(self) -> None:
        """Signal that shutdown has been requested."""
        self._shutdown_requested = True

    def consume_messages(
        self,
        callback: Callable[
            [
                pika.adapters.blocking_connection.BlockingChannel,
                pika.spec.Basic.Deliver,
                pika.spec.BasicProperties,
                bytes,
            ],
            None,
        ],
        prefetch_count: int = 1,
    ) -> None:
        """
        Start consuming messages from tts_jobs queue.

        Args:
            callback: Function to handle each message
            prefetch_count: Number of messages to prefetch (default: 1 for sequential)
        """
        if not self.is_connected():
            raise RuntimeError("Not connected to RabbitMQ")

        # Set QoS
        self.channel.basic_qos(prefetch_count=prefetch_count)

        # Setup consumer
        self.channel.basic_consume(
            queue="tts_jobs",
            on_message_callback=callback,
            auto_ack=False,
        )

        logger.info("Starting message consumption...")
        self.channel.start_consuming()

    def publish_result(self, result: dict[str, Any], max_retries: int = 3) -> None:
        """
        Publish job result to tts_results queue with retry.

        Args:
            result: Job result dictionary
            max_retries: Maximum retry attempts

        Raises:
            Exception: If all retries exhausted
        """
        retry_count = 0
        job_id = result.get("jobId") or result.get("job_id")

        while retry_count < max_retries:
            try:
                # Check connection before publishing
                if not self.is_connected():
                    logger.warning(
                        f"[JOB {job_id}] Connection closed, attempting to reconnect before publishing..."
                    )
                    if not self.reconnect_with_backoff():
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
                        f"[JOB {job_id}] Connection error publishing result "
                        f"(attempt {retry_count}/{max_retries}): {e}. "
                        f"Retrying in {delay}s..."
                    )
                    time.sleep(delay)
                    # Try to reconnect
                    try:
                        if not self.reconnect_with_backoff():
                            raise Exception("Reconnection failed")
                    except Exception as reconnect_error:
                        logger.error(
                            f"[JOB {job_id}] Reconnection failed: {reconnect_error}"
                        )
                else:
                    logger.critical(
                        f"[JOB {job_id}] ✗ Failed to publish result after {max_retries} attempts: {e}"
                    )
                    raise

            except Exception as e:
                retry_count += 1
                if retry_count < max_retries:
                    delay = 2**retry_count
                    logger.warning(
                        f"[JOB {job_id}] Failed to publish result "
                        f"(attempt {retry_count}/{max_retries}): {e}. "
                        f"Retrying in {delay}s..."
                    )
                    time.sleep(delay)
                else:
                    logger.critical(
                        f"[JOB {job_id}] ✗ Failed to publish result after {max_retries} attempts: {e}"
                    )
                    raise

    def acknowledge_message(self, delivery_tag: int) -> None:
        """Acknowledge message successful processing."""
        if self.channel and not self.channel.is_closed:
            self.channel.basic_ack(delivery_tag=delivery_tag)

    def reject_message(self, delivery_tag: int, requeue: bool = False) -> None:
        """Reject message and optionally requeue."""
        if self.channel and not self.channel.is_closed:
            self.channel.basic_nack(delivery_tag=delivery_tag, requeue=requeue)
