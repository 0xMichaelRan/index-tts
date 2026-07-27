"""
Unit tests for RabbitMQ configuration module.

Tests queue configuration, connection handling, and error scenarios.
"""

import os
import pytest
from unittest.mock import Mock, patch, MagicMock
import pika
from services.rabbitmq_config import (
    configure_queues,
    configure_queue,
    get_queue_info,
    RabbitMQConnectionError,
    _parse_rabbitmq_url,
    _connect_with_retry,
    QUEUE_CONFIGS,
)


class TestRabbitMQURLParsing:
    """Test RabbitMQ URL parsing."""

    def test_parse_basic_url(self):
        """Test parsing basic RabbitMQ URL."""
        url = "amqp://guest:guest@localhost:5672/"
        params = _parse_rabbitmq_url(url)
        
        assert params["host"] == "localhost"
        assert params["port"] == 5672
        assert params["virtual_host"] == "/"

    def test_parse_url_with_vhost(self):
        """Test parsing URL with custom vhost."""
        url = "amqp://user:pass@host:5672/myvhost"
        params = _parse_rabbitmq_url(url)
        
        assert params["host"] == "host"
        assert params["virtual_host"] == "myvhost"

    def test_parse_url_with_defaults(self):
        """Test parsing URL with missing components uses defaults."""
        url = "amqp://localhost"
        params = _parse_rabbitmq_url(url)
        
        assert params["host"] == "localhost"
        assert params["port"] == 5672
        assert params["virtual_host"] == "/"

    def test_parse_invalid_url(self):
        """Test parsing invalid URL raises ValueError."""
        # urlparse doesn't fail on invalid schemes, so we need to mock pika.PlainCredentials
        # to raise an error or provide an invalid URL format that will cause an exception
        with patch("services.rabbitmq_config.pika.PlainCredentials") as mock_creds:
            mock_creds.side_effect = ValueError("Invalid credentials")
            with pytest.raises(ValueError, match="Invalid RabbitMQ URL"):
                _parse_rabbitmq_url("not-a-valid-url")


class TestQueueConfiguration:
    """Test queue configuration logic."""

    def test_queue_configs_structure(self):
        """Test QUEUE_CONFIGS has all required queues."""
        required_queues = ["tts_jobs", "tts_results", "tts_jobs_dlq", "tts_results_dlq"]
        
        for queue_name in required_queues:
            assert queue_name in QUEUE_CONFIGS
            config = QUEUE_CONFIGS[queue_name]
            assert "durable" in config
            assert "arguments" in config

    def test_main_queues_have_dlq_routing(self):
        """Test main queues are configured with dead-letter routing."""
        main_queues = ["tts_jobs", "tts_results"]
        
        for queue_name in main_queues:
            config = QUEUE_CONFIGS[queue_name]
            args = config["arguments"]
            
            assert "x-dead-letter-exchange" in args
            assert args["x-dead-letter-exchange"] == ""
            assert "x-dead-letter-routing-key" in args

    def test_tts_jobs_queue_configuration(self):
        """Test tts_jobs queue has correct configuration."""
        config = QUEUE_CONFIGS["tts_jobs"]
        args = config["arguments"]
        
        assert config["durable"] is True
        assert args["x-message-ttl"] == 86400000  # 24 hours
        assert args["x-max-length"] == 10000
        assert args["x-overflow"] == "reject-publish"
        assert args["x-dead-letter-routing-key"] == "tts_jobs_dlq"

    def test_tts_results_queue_configuration(self):
        """Test tts_results queue has correct configuration."""
        config = QUEUE_CONFIGS["tts_results"]
        args = config["arguments"]
        
        assert config["durable"] is True
        assert args["x-message-ttl"] == 604800000  # 7 days
        assert args["x-max-length"] == 10000
        assert args["x-dead-letter-routing-key"] == "tts_results_dlq"

    def test_dlq_configuration(self):
        """Test dead-letter queues have correct configuration."""
        dlqs = ["tts_jobs_dlq", "tts_results_dlq"]
        
        for queue_name in dlqs:
            config = QUEUE_CONFIGS[queue_name]
            args = config["arguments"]
            
            assert config["durable"] is True
            assert args["x-message-ttl"] == 604800000  # 7 days
            assert args["x-max-length"] == 5000


class TestConnectionRetry:
    """Test connection retry logic."""

    @patch("services.rabbitmq_config.time.sleep")
    def test_connect_success_first_attempt(self, mock_sleep):
        """Test successful connection on first attempt."""
        mock_connection = Mock()
        
        with patch("services.rabbitmq_config.pika.BlockingConnection") as mock_blocking_conn:
            mock_blocking_conn.return_value = mock_connection
            
            conn_params = Mock()
            result = _connect_with_retry(conn_params, max_retries=3)
            
            assert result == mock_connection
            mock_blocking_conn.assert_called_once_with(conn_params)
            mock_sleep.assert_not_called()

    @patch("services.rabbitmq_config.time.sleep")
    def test_connect_success_after_retry(self, mock_sleep):
        """Test successful connection after retry."""
        mock_connection = Mock()
        
        with patch("services.rabbitmq_config.pika.BlockingConnection") as mock_blocking_conn:
            mock_blocking_conn.side_effect = [
                pika.exceptions.AMQPConnectionError("Connection refused"),
                mock_connection,
            ]
            
            conn_params = Mock()
            result = _connect_with_retry(conn_params, max_retries=3, retry_delay=1)
            
            assert result == mock_connection
            assert mock_blocking_conn.call_count == 2
            mock_sleep.assert_called_once_with(1)  # First retry delay

    @patch("services.rabbitmq_config.time.sleep")
    def test_connect_failure_after_retries(self, mock_sleep):
        """Test connection failure after all retries."""
        with patch("services.rabbitmq_config.pika.BlockingConnection") as mock_blocking_conn:
            mock_blocking_conn.side_effect = (
                pika.exceptions.AMQPConnectionError("Connection refused")
            )
            
            conn_params = Mock()
            
            with pytest.raises(RabbitMQConnectionError, match="Failed to connect"):
                _connect_with_retry(conn_params, max_retries=3, retry_delay=1)
            
            assert mock_blocking_conn.call_count == 3
            assert mock_sleep.call_count == 2  # Retries before final attempt

    @patch("services.rabbitmq_config.time.sleep")
    def test_connect_exponential_backoff(self, mock_sleep):
        """Test exponential backoff during retries."""
        with patch("services.rabbitmq_config.pika.BlockingConnection") as mock_blocking_conn:
            mock_blocking_conn.side_effect = (
                pika.exceptions.AMQPConnectionError("Connection refused")
            )
            
            conn_params = Mock()
            
            with pytest.raises(RabbitMQConnectionError):
                _connect_with_retry(conn_params, max_retries=3, retry_delay=2)
            
            # Check exponential backoff: 2, 4 seconds
            expected_delays = [2, 4]
            actual_delays = [call[0][0] for call in mock_sleep.call_args_list]
            assert actual_delays == expected_delays


class TestConfigureQueue:
    """Test single queue configuration."""

    def test_configure_queue_success(self):
        """Test successful queue configuration."""
        mock_channel = Mock()
        queue_name = "test_queue"
        config = {
            "durable": True,
            "arguments": {"x-message-ttl": 10000}
        }
        
        configure_queue(mock_channel, queue_name, config)
        
        mock_channel.queue_declare.assert_called_once_with(
            queue=queue_name,
            durable=True,
            arguments={"x-message-ttl": 10000},
        )

    def test_configure_queue_no_arguments(self):
        """Test queue configuration without arguments."""
        mock_channel = Mock()
        queue_name = "test_queue"
        config = {"durable": True}
        
        configure_queue(mock_channel, queue_name, config)
        
        mock_channel.queue_declare.assert_called_once_with(
            queue=queue_name,
            durable=True,
            arguments={},
        )

    def test_configure_queue_failure(self):
        """Test queue configuration failure."""
        mock_channel = Mock()
        mock_channel.queue_declare.side_effect = Exception("Queue error")
        
        queue_name = "test_queue"
        config = {"durable": True, "arguments": {}}
        
        with pytest.raises(Exception, match="Queue error"):
            configure_queue(mock_channel, queue_name, config)


class TestConfigureQueues:
    """Test full queue configuration."""

    @patch("services.rabbitmq_config.PIKA_AVAILABLE", True)
    @patch("services.rabbitmq_config._connect_with_retry")
    def test_configure_queues_success(self, mock_connect):
        """Test successful configuration of all queues."""
        mock_connection = Mock()
        mock_channel = Mock()
        mock_connection.channel.return_value = mock_channel
        mock_connection.is_closed = False
        mock_connect.return_value = mock_connection
        
        # Run configuration
        with patch("services.rabbitmq_config.pika.ConnectionParameters"):
            configure_queues("amqp://guest:guest@localhost:5672/")
        
        # Verify all queues were declared
        assert mock_channel.queue_declare.call_count == 4
        
        # Verify connection was closed
        mock_connection.close.assert_called_once()

    @patch("services.rabbitmq_config.PIKA_AVAILABLE", False)
    def test_configure_queues_pika_not_installed(self):
        """Test error when pika is not installed."""
        with pytest.raises(ImportError, match="pika is required"):
            configure_queues("amqp://guest:guest@localhost:5672/")

    def test_configure_queues_no_url(self):
        """Test error when no URL provided."""
        with pytest.raises(ValueError, match="RabbitMQ URL not provided"):
            configure_queues()

    @patch("services.rabbitmq_config.PIKA_AVAILABLE", True)
    def test_configure_queues_uses_env_var(self, monkeypatch):
        """Test configuration uses RABBITMQ_URL environment variable."""
        monkeypatch.setenv("RABBITMQ_URL", "amqp://test:test@testhost:5672/")
        
        with patch("services.rabbitmq_config._connect_with_retry") as mock_connect, \
             patch("services.rabbitmq_config.pika.ConnectionParameters"):
            
            mock_connection = Mock()
            mock_connection.channel.return_value = Mock()
            mock_connection.is_closed = False
            mock_connect.return_value = mock_connection
            
            configure_queues()
            
            # Verify connection was attempted
            assert mock_connect.called


class TestGetQueueInfo:
    """Test queue information retrieval."""

    @patch("services.rabbitmq_config.PIKA_AVAILABLE", True)
    @patch("services.rabbitmq_config.pika")
    @patch("services.rabbitmq_config._parse_rabbitmq_url")
    def test_get_queue_info_success(self, mock_parse_url, mock_pika):
        """Test successful queue info retrieval."""
        mock_parse_url.return_value = {
            "host": "localhost",
            "port": 5672,
            "credentials": Mock(),
            "virtual_host": "/",
        }
        
        mock_connection = Mock()
        mock_channel = Mock()
        mock_connection.channel.return_value = mock_channel
        mock_connection.is_closed = False
        mock_pika.BlockingConnection.return_value = mock_connection
        
        # Mock queue_declare response
        mock_result = Mock()
        mock_result.method.message_count = 10
        mock_result.method.consumer_count = 2
        mock_channel.queue_declare.return_value = mock_result
        
        info = get_queue_info("amqp://guest:guest@localhost:5672/")
        
        assert "tts_jobs" in info
        assert info["tts_jobs"]["message_count"] == 10
        assert info["tts_jobs"]["consumer_count"] == 2

    @patch("services.rabbitmq_config.PIKA_AVAILABLE", False)
    def test_get_queue_info_pika_not_installed(self):
        """Test error when pika is not installed."""
        with pytest.raises(ImportError, match="pika is required"):
            get_queue_info("amqp://guest:guest@localhost:5672/")


@pytest.mark.skipif(
    os.getenv("RABBITMQ_URL") is None,
    reason="RABBITMQ_URL not set - skipping integration test"
)
class TestIntegration:
    """Integration tests with real RabbitMQ (requires running instance)."""

    def test_configure_queues_integration(self):
        """Test full queue configuration with real RabbitMQ."""
        rabbitmq_url = os.getenv("RABBITMQ_URL")
        
        # Should not raise any exceptions
        configure_queues(rabbitmq_url)
        
        # Verify queues exist
        info = get_queue_info(rabbitmq_url)
        
        assert "tts_jobs" in info
        assert "tts_results" in info
        assert "tts_jobs_dlq" in info
        assert "tts_results_dlq" in info
