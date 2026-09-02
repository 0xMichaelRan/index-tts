"""
RabbitMQ Queue Configuration with Dead-Letter Queues

This module provides idempotent RabbitMQ queue configuration for the TTS service,
including main queues and dead-letter queues (DLQ) for failed message handling.

Queue Architecture (Standardized DLX Pattern):
    Main Queues (Durable):
    ├── tts_jobs (TTL: 24h) → DLX: tts_jobs.dlx → DLQ: tts_jobs_failed
    └── tts_results (TTL: 7d) → DLX: tts_results.dlx → DLQ: tts_results_failed

    Dead-Letter Exchanges (Fanout):
    ├── tts_jobs.dlx → routes to tts_jobs_failed
    └── tts_results.dlx → routes to tts_results_failed

    Dead-Letter Queues (Durable):
    ├── tts_jobs_failed (TTL: 7 days) - Messages rejected after 3 retries
    └── tts_results_failed (TTL: 7 days) - Failed result processing

Usage:
    from services.rabbitmq_config import configure_queues

    # Configure all queues (idempotent)
    configure_queues(
        rabbitmq_url="amqp://guest:guest@localhost:5672/"
    )
"""

import os
import logging
import time
from typing import Optional, Dict, Any
from urllib.parse import urlparse

try:
    import pika

    PIKA_AVAILABLE = True
except ImportError:
    PIKA_AVAILABLE = False
    logging.warning("pika is not installed. Install with: pip install pika")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# Queue Configuration Constants
# NOTE: Using standardized DLX pattern (consistent with studio-backend)
# Pattern: {queue_name}.dlx (fanout exchange) → {queue_name}_failed (DLQ)
QUEUE_CONFIGS = {
    "tts_jobs": {
        "durable": True,
        "arguments": {
            "x-dead-letter-exchange": "tts_jobs.dlx",  # Named fanout exchange
            "x-dead-letter-routing-key": "tts_jobs_failed",  # DLQ name
            "x-message-ttl": 86400000,  # 24 hours in milliseconds
            "x-max-length": 10000,  # Prevent unlimited queue buildup
            "x-overflow": "reject-publish",  # Reject new messages when full
        },
    },
    "tts_results": {
        "durable": True,
        "arguments": {
            "x-dead-letter-exchange": "tts_results.dlx",  # Named fanout exchange
            "x-dead-letter-routing-key": "tts_results_failed",  # DLQ name
            "x-message-ttl": 604800000,  # 7 days in milliseconds
            "x-max-length": 10000,
        },
    },
    "tts_jobs_failed": {  # Renamed from tts_jobs_dlq
        "durable": True,
        "arguments": {
            "x-message-ttl": 604800000,  # 7 days in milliseconds
            "x-max-length": 5000,
        },
    },
    "tts_results_failed": {  # Renamed from tts_results_dlq
        "durable": True,
        "arguments": {
            "x-message-ttl": 604800000,  # 7 days in milliseconds
            "x-max-length": 5000,
        },
    },
}


class RabbitMQConnectionError(Exception):
    """Raised when RabbitMQ connection fails."""

    pass


def _parse_rabbitmq_url(url: str) -> Dict[str, Any]:
    """
    Parse RabbitMQ connection URL.

    Args:
        url: RabbitMQ URL (e.g., "amqp://user:pass@host:port/vhost")

    Returns:
        Connection parameters dictionary

    Raises:
        ValueError: If URL format is invalid
    """
    try:
        parsed = urlparse(url)

        # Default values
        host = parsed.hostname or "localhost"
        port = parsed.port or 5672
        username = parsed.username or "guest"
        password = parsed.password or "guest"
        vhost = parsed.path.lstrip("/") or "/"

        return {
            "host": host,
            "port": port,
            "credentials": pika.PlainCredentials(username, password),
            "virtual_host": vhost,
        }
    except Exception as e:
        raise ValueError(f"Invalid RabbitMQ URL: {url}") from e


def _connect_with_retry(
    connection_params: pika.ConnectionParameters,
    max_retries: int = 3,
    retry_delay: int = 2,
) -> pika.BlockingConnection:
    """
    Connect to RabbitMQ with exponential backoff retry.

    Args:
        connection_params: Pika connection parameters
        max_retries: Maximum number of retry attempts
        retry_delay: Initial retry delay in seconds

    Returns:
        Active RabbitMQ connection

    Raises:
        RabbitMQConnectionError: If connection fails after all retries
    """
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"Connecting to RabbitMQ (attempt {attempt}/{max_retries})...")
            connection = pika.BlockingConnection(connection_params)
            logger.info("Successfully connected to RabbitMQ")
            return connection
        except pika.exceptions.AMQPConnectionError as e:
            if attempt == max_retries:
                raise RabbitMQConnectionError(
                    f"Failed to connect to RabbitMQ after {max_retries} attempts: {str(e)}"
                ) from e

            delay = retry_delay * (2 ** (attempt - 1))  # Exponential backoff
            logger.warning(
                f"Connection failed: {str(e)}. Retrying in {delay} seconds..."
            )
            time.sleep(delay)


def configure_queue(
    channel: pika.channel.Channel,
    queue_name: str,
    config: Dict[str, Any],
) -> None:
    """
    Configure a single queue with the specified parameters.

    This function is idempotent - it can be safely called multiple times.

    Args:
        channel: RabbitMQ channel
        queue_name: Name of the queue to configure
        config: Queue configuration dictionary with 'durable' and 'arguments'
    """
    try:
        channel.queue_declare(
            queue=queue_name,
            durable=config["durable"],
            arguments=config.get("arguments", {}),
        )
        logger.info(f"✓ Queue '{queue_name}' configured successfully")

        # Log queue arguments for debugging
        if config.get("arguments"):
            for key, value in config["arguments"].items():
                logger.debug(f"  {key}: {value}")

    except Exception as e:
        logger.error(f"✗ Failed to configure queue '{queue_name}': {str(e)}")
        raise


def declare_dlx_exchanges(channel: pika.channel.Channel) -> None:
    """
    Declare DLX (Dead Letter Exchange) fanout exchanges for all queues.

    Creates the following exchanges:
    - tts_jobs.dlx (fanout, durable)
    - tts_results.dlx (fanout, durable)

    Args:
        channel: RabbitMQ channel
    """
    dlx_exchanges = ["tts_jobs.dlx", "tts_results.dlx"]
    
    for exchange_name in dlx_exchanges:
        try:
            channel.exchange_declare(
                exchange=exchange_name,
                exchange_type="fanout",
                durable=True,
            )
            logger.info(f"✓ DLX exchange '{exchange_name}' declared successfully")
        except Exception as e:
            logger.error(f"✗ Failed to declare DLX exchange '{exchange_name}': {str(e)}")
            raise


def bind_dlq_to_dlx(channel: pika.channel.Channel) -> None:
    """
    Bind dead-letter queues to their respective DLX exchanges.

    Bindings:
    - tts_jobs_failed → tts_jobs.dlx
    - tts_results_failed → tts_results.dlx

    Args:
        channel: RabbitMQ channel
    """
    bindings = [
        ("tts_jobs_failed", "tts_jobs.dlx"),
        ("tts_results_failed", "tts_results.dlx"),
    ]
    
    for queue_name, exchange_name in bindings:
        try:
            channel.queue_bind(
                queue=queue_name,
                exchange=exchange_name,
                routing_key="",  # Empty routing key for fanout
            )
            logger.info(f"✓ Bound queue '{queue_name}' to exchange '{exchange_name}'")
        except Exception as e:
            logger.error(f"✗ Failed to bind queue '{queue_name}' to exchange '{exchange_name}': {str(e)}")
            raise


def configure_queues(
    rabbitmq_url: Optional[str] = None,
    max_retries: int = 3,
    retry_delay: int = 2,
) -> None:
    """
    Configure all RabbitMQ queues for the TTS service.

    This function is idempotent and can be safely run multiple times.
    It will create or update the following:
    
    1. DLX Exchanges (fanout):
       - tts_jobs.dlx
       - tts_results.dlx
    
    2. Dead-Letter Queues:
       - tts_jobs_failed
       - tts_results_failed
    
    3. Main Queues (with DLX routing):
       - tts_jobs → routes failed messages to tts_jobs.dlx → tts_jobs_failed
       - tts_results → routes failed messages to tts_results.dlx → tts_results_failed

    Args:
        rabbitmq_url: RabbitMQ connection URL (default: from RABBITMQ_URL env var)
        max_retries: Maximum connection retry attempts
        retry_delay: Initial retry delay in seconds

    Raises:
        ImportError: If pika is not installed
        RabbitMQConnectionError: If connection fails after retries

    Example:
        >>> configure_queues("amqp://guest:guest@localhost:5672/")
        >>> # Or use environment variable
        >>> os.environ["RABBITMQ_URL"] = "amqp://guest:guest@localhost:5672/"
        >>> configure_queues()
    """
    if not PIKA_AVAILABLE:
        raise ImportError(
            "pika is required for RabbitMQ configuration. "
            "Install it with: pip install pika"
        )

    # Get RabbitMQ URL from parameter or environment
    url = rabbitmq_url or os.getenv("RABBITMQ_URL")
    if not url:
        raise ValueError(
            "RabbitMQ URL not provided. Set RABBITMQ_URL environment variable "
            "or pass rabbitmq_url parameter."
        )

    logger.info("=" * 70)
    logger.info("Starting RabbitMQ Queue Configuration (Standardized DLX Pattern)")
    logger.info("=" * 70)

    connection = None
    try:
        # Parse URL and create connection parameters
        conn_params_dict = _parse_rabbitmq_url(url)
        connection_params = pika.ConnectionParameters(**conn_params_dict)

        logger.info(
            f"RabbitMQ Host: {conn_params_dict['host']}:{conn_params_dict['port']}"
        )
        logger.info(f"Virtual Host: {conn_params_dict['virtual_host']}")

        # Connect with retry logic
        connection = _connect_with_retry(connection_params, max_retries, retry_delay)
        channel = connection.channel()

        logger.info("\nConfiguring DLX pattern...")
        logger.info("-" * 70)
        
        # Step 1: Declare DLX exchanges
        logger.info("Step 1: Declaring DLX exchanges...")
        declare_dlx_exchanges(channel)
        
        # Step 2: Declare DLQ queues (must exist before binding)
        logger.info("\nStep 2: Declaring DLQ queues...")
        for queue_name in ["tts_jobs_failed", "tts_results_failed"]:
            configure_queue(channel, queue_name, QUEUE_CONFIGS[queue_name])
        
        # Step 3: Bind DLQs to DLX exchanges
        logger.info("\nStep 3: Binding DLQs to DLX exchanges...")
        bind_dlq_to_dlx(channel)
        
        # Step 4: Declare main queues with DLX routing
        logger.info("\nStep 4: Declaring main queues with DLX routing...")
        for queue_name in ["tts_jobs", "tts_results"]:
            configure_queue(channel, queue_name, QUEUE_CONFIGS[queue_name])

        logger.info("-" * 70)
        logger.info("✓ All queues configured successfully")
        logger.info("=" * 70)

    except RabbitMQConnectionError:
        logger.error("Failed to connect to RabbitMQ")
        raise
    except Exception as e:
        logger.error(f"Error during queue configuration: {str(e)}")
        raise
    finally:
        if connection and not connection.is_closed:
            connection.close()
            logger.info("Connection closed")


def get_queue_info(rabbitmq_url: Optional[str] = None) -> Dict[str, Any]:
    """
    Get information about configured queues.

    Args:
        rabbitmq_url: RabbitMQ connection URL (default: from RABBITMQ_URL env var)

    Returns:
        Dictionary with queue information

    Raises:
        ImportError: If pika is not installed
        RabbitMQConnectionError: If connection fails
    """
    if not PIKA_AVAILABLE:
        raise ImportError("pika is required. Install it with: pip install pika")

    url = rabbitmq_url or os.getenv("RABBITMQ_URL")
    if not url:
        raise ValueError("RabbitMQ URL not provided")

    connection = None
    queue_info = {}

    try:
        conn_params_dict = _parse_rabbitmq_url(url)
        connection_params = pika.ConnectionParameters(**conn_params_dict)
        connection = pika.BlockingConnection(connection_params)
        channel = connection.channel()

        for queue_name in QUEUE_CONFIGS.keys():
            try:
                # Passive declare to get queue info without creating it
                result = channel.queue_declare(queue=queue_name, passive=True)
                queue_info[queue_name] = {
                    "message_count": result.method.message_count,
                    "consumer_count": result.method.consumer_count,
                }
            except pika.exceptions.ChannelClosedByBroker:
                queue_info[queue_name] = {"exists": False}
                # Reopen channel after error
                channel = connection.channel()

        return queue_info

    finally:
        if connection and not connection.is_closed:
            connection.close()


def main():
    """CLI entry point for queue configuration."""
    import sys

    # Parse command line arguments
    rabbitmq_url = sys.argv[1] if len(sys.argv) > 1 else None

    try:
        configure_queues(rabbitmq_url)
        print("\n✓ Queue configuration completed successfully")
        return 0
    except Exception as e:
        print(f"\n✗ Queue configuration failed: {str(e)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    exit(main())
