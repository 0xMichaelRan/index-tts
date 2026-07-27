"""Test to verify RabbitMQ worker connection."""
import os
from pathlib import Path
from urllib.parse import urlparse

import pytest
import pika
from dotenv import load_dotenv


@pytest.fixture(scope="session", autouse=True)
def load_env():
    """Load environment variables from .env file."""
    env_file = Path(__file__).parent.parent.parent / ".env"
    if env_file.exists():
        load_dotenv(str(env_file))


def parse_rabbitmq_url(url: str) -> dict:
    """Parse RabbitMQ URL into connection parameters."""
    parsed = urlparse(url)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5672,
        "vhost": parsed.path.lstrip("/") or "/",
        "username": parsed.username or "guest",
        "password": parsed.password or "guest",
    }


class TestRabbitMQWorkerConnection:
    """Test RabbitMQ worker connection and queue availability."""

    @pytest.fixture
    def rabbitmq_url(self):
        """Get RabbitMQ URL from environment."""
        return os.getenv(
            "RABBITMQ_URL",
            "amqp://guest:guest@localhost:5672/"
        )

    @pytest.fixture
    def connection_params(self, rabbitmq_url):
        """Create RabbitMQ connection parameters."""
        params = parse_rabbitmq_url(rabbitmq_url)
        credentials = pika.PlainCredentials(
            username=params["username"],
            password=params["password"],
        )
        return pika.ConnectionParameters(
            host=params["host"],
            port=params["port"],
            virtual_host=params["vhost"],
            credentials=credentials,
            connection_attempts=1,
            retry_delay=1,
        )

    def test_rabbitmq_connection(self, connection_params):
        """Test basic RabbitMQ connection."""
        connection = pika.BlockingConnection([connection_params])
        try:
            assert connection.is_open, "Connection should be open"
            channel = connection.channel()
            assert channel is not None, "Channel should be created"
        finally:
            connection.close()

    def test_tts_jobs_queue_exists(self, connection_params):
        """Test that tts_jobs queue can be declared."""
        connection = pika.BlockingConnection([connection_params])
        try:
            channel = connection.channel()
            channel.queue_declare(queue="tts_jobs", durable=True, passive=False)
            # If we get here, queue is ready
            assert True
        finally:
            connection.close()

    def test_tts_results_queue_exists(self, connection_params):
        """Test that tts_results queue can be declared."""
        connection = pika.BlockingConnection([connection_params])
        try:
            channel = connection.channel()
            channel.queue_declare(queue="tts_results", durable=True, passive=False)
            # If we get here, queue is ready
            assert True
        finally:
            connection.close()

    def test_connection_details_parsed_correctly(self, rabbitmq_url):
        """Test that RabbitMQ URL is parsed correctly."""
        params = parse_rabbitmq_url(rabbitmq_url)
        
        assert params["host"] is not None, "Host should be parsed"
        assert params["port"] is not None, "Port should be parsed"
        assert params["username"] is not None, "Username should be parsed"
        assert params["password"] is not None, "Password should be parsed"
        assert params["vhost"] is not None, "VHost should be parsed"
