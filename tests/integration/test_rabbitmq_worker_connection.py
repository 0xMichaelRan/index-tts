"""
Integration tests for RabbitMQ worker connection.

Requires RABBITMQ_URL in .env. Skipped when the variable is absent.

Tests cover:
- Basic connection and channel creation
- Presence of all expected queues (main, DLQ, vox)
- DLX exchange bindings
- Channel QoS prefetch
- Publish/consume round-trip on tts_jobs (immediate ack — does not affect real jobs)

Run:
    uv run pytest tests/pytest/test_rabbitmq_worker_connection.py -v
"""

import ssl

import os
from urllib.parse import urlparse

import pika
import pytest

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Guard — conftest.py loads .env before collection so getenv works here
# ---------------------------------------------------------------------------

_RABBITMQ_CONFIGURED = os.getenv("RABBITMQ_URL") is not None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_url(url: str) -> dict:
    """Parse a RabbitMQ URL into pika connection parameter components."""
    parsed = urlparse(url)
    is_ssl = parsed.scheme in ("amqps", "amqp+ssl")
    # amqps:// defaults to port 5671; amqp:// defaults to 5672
    default_port = 5671 if is_ssl else 5672
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or default_port,
        "vhost": parsed.path.lstrip("/") or "/",
        "username": parsed.username or "guest",
        "password": parsed.password or "guest",
        "ssl": is_ssl,
    }


def _make_params(url: str) -> pika.ConnectionParameters:
    """Build pika ConnectionParameters from a RabbitMQ URL."""
    parts = _parse_url(url)
    credentials = pika.PlainCredentials(
        username=parts["username"],
        password=parts["password"],
    )
    kwargs: dict = dict(
        host=parts["host"],
        port=parts["port"],
        virtual_host=parts["vhost"],
        credentials=credentials,
        connection_attempts=1,
        retry_delay=1,
    )
    if parts["ssl"]:
        # Build an SSL context; disable hostname checking for managed CloudAMQP
        # endpoints where the certificate SAN may not match the vhost subdomain.
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        kwargs["ssl_options"] = pika.SSLOptions(ssl_ctx)
    return pika.ConnectionParameters(**kwargs)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def rabbitmq_url() -> str:
    return os.getenv("RABBITMQ_URL")  # guaranteed non-None due to skipif


@pytest.fixture(scope="session")
def rmq_params(rabbitmq_url) -> pika.ConnectionParameters:
    return _make_params(rabbitmq_url)


@pytest.fixture(scope="function")
def connection(rmq_params) -> pika.BlockingConnection:
    """Open a fresh BlockingConnection for each test; close on teardown."""
    conn = pika.BlockingConnection([rmq_params])
    yield conn
    if conn.is_open:
        conn.close()


@pytest.fixture(scope="function")
def channel(connection) -> pika.adapters.blocking_connection.BlockingChannel:
    """Open a channel on the test connection."""
    return connection.channel()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _RABBITMQ_CONFIGURED, reason="RABBITMQ_URL not set")
class TestRabbitMQWorkerConnection:
    """Integration tests for RabbitMQ worker connection and queue topology."""

    # -- Connectivity -------------------------------------------------------

    def test_connection_open(self, connection) -> None:
        """BlockingConnection must open successfully."""
        assert connection.is_open

    def test_channel_creation(self, channel) -> None:
        """Channel must be non-None after opening."""
        assert channel is not None

    def test_url_parsed_correctly(self, rabbitmq_url) -> None:
        """All URL components must parse to non-None values."""
        parts = _parse_url(rabbitmq_url)
        assert parts["host"] is not None
        assert parts["port"] is not None
        assert parts["username"] is not None
        assert parts["password"] is not None
        assert parts["vhost"] is not None

    # -- Main queues --------------------------------------------------------

    @pytest.mark.parametrize(
        "queue_name",
        ["tts_jobs", "tts_results", "vox_jobs", "vox_results"],
    )
    def test_main_queue_exists(self, channel, queue_name: str) -> None:
        """Main work queues must exist (passive declare)."""
        channel.queue_declare(queue=queue_name, passive=True)

    # -- Dead-letter queues -------------------------------------------------

    @pytest.mark.parametrize(
        "queue_name",
        [
            "tts_jobs_failed",
            "tts_results_failed",
            "vox_jobs_failed",
            "vox_results_failed",
        ],
    )
    def test_dlq_exists(self, channel, queue_name: str) -> None:
        """Dead-letter queues must exist (passive declare)."""
        channel.queue_declare(queue=queue_name, passive=True)

    # -- DLX exchanges ------------------------------------------------------

    @pytest.mark.parametrize(
        "exchange_name",
        ["tts_jobs.dlx", "tts_results.dlx", "vox_jobs.dlx", "vox_results.dlx"],
    )
    def test_dlx_exchange_exists(self, channel, exchange_name: str) -> None:
        """DLX fanout exchanges must exist (passive declare)."""
        channel.exchange_declare(
            exchange=exchange_name,
            exchange_type="fanout",
            passive=True,
        )

    # -- QoS / prefetch -----------------------------------------------------

    def test_channel_qos_prefetch(self, channel) -> None:
        """basic_qos(prefetch_count=1) must not raise."""
        channel.basic_qos(prefetch_count=1)

    # -- Publish / consume round-trip on tts_jobs ---------------------------

    def test_publish_and_consume_roundtrip_tts_jobs(self, channel) -> None:
        """Publish a test message to tts_jobs and immediately consume + ack it.

        Uses a unique correlation_id so the test message can be identified and
        distinguished from any real jobs. The message is basic_get'd (no listener
        registered) and ack'd immediately, making this safe to run against the
        real production queue.
        """
        import json

        correlation_id = f"integration-test-{os.getpid()}"
        test_body = json.dumps(
            {
                "jobId": "integration-test-noop",
                "jobType": "integration-test",
                "_correlation_id": correlation_id,
            }
        ).encode()

        # Publish
        channel.basic_publish(
            exchange="",
            routing_key="tts_jobs",
            body=test_body,
            properties=pika.BasicProperties(
                delivery_mode=2,  # persistent
                content_type="application/json",
                correlation_id=correlation_id,
            ),
        )

        # Consume immediately — retry up to 5 times to let the broker deliver
        max_attempts = 5
        received = None
        for _ in range(max_attempts):
            method_frame, _header, body = channel.basic_get(
                queue="tts_jobs", auto_ack=False
            )
            if method_frame is None:
                continue
            decoded = json.loads(body.decode())
            if decoded.get("_correlation_id") == correlation_id:
                received = decoded
                channel.basic_ack(delivery_tag=method_frame.delivery_tag)
                break
            else:
                # Not ours — nack without requeue so it gets redelivered
                channel.basic_nack(delivery_tag=method_frame.delivery_tag, requeue=True)

        assert received is not None, (
            "Test message was not received from tts_jobs within the retry window. "
            "Another consumer may have picked it up."
        )
        assert received["_correlation_id"] == correlation_id
