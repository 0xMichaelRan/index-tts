"""
Tests for Dead-Letter Queue (DLQ) Monitoring

Tests cover:
- DLQ statistics collection
- Alert threshold detection
- Message retrieval and processing
- Alert callbacks
- Connection management
- Message parsing and serialization
"""

import pytest
import time
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from services.dlq_monitor import (
    DLQMonitor,
    DLQStats,
    DLQMessage,
)


class TestDLQStats:
    """Test DLQ statistics data class."""

    def test_stats_creation(self):
        """DLQStats should store queue information."""
        stats = DLQStats(
            queue_name="test_queue",
            message_count=42,
            consumer_count=2,
        )

        assert stats.queue_name == "test_queue"
        assert stats.message_count == 42
        assert stats.consumer_count == 2

    def test_stats_to_dict(self):
        """DLQStats should serialize to dictionary."""
        stats = DLQStats(
            queue_name="test_queue",
            message_count=42,
            consumer_count=2,
        )

        stats_dict = stats.to_dict()

        assert isinstance(stats_dict, dict)
        assert stats_dict["queue_name"] == "test_queue"
        assert stats_dict["message_count"] == 42
        assert stats_dict["consumer_count"] == 2
        assert "last_check" in stats_dict

    def test_should_alert_on_message_threshold(self):
        """DLQStats should alert when message count exceeds threshold."""
        stats = DLQStats(
            queue_name="test_queue",
            message_count=150,
            consumer_count=0,
        )

        # Should alert when exceeding threshold of 100
        assert stats.should_alert(message_threshold=100)

        # Should not alert when below threshold
        assert not stats.should_alert(message_threshold=200)

    def test_should_alert_on_age_threshold(self):
        """DLQStats should alert when oldest message exceeds age threshold."""
        stats = DLQStats(
            queue_name="test_queue",
            message_count=10,
            consumer_count=0,
            oldest_message_age_seconds=100000,  # ~28 hours
        )

        # Should alert when older than 24 hours
        assert stats.should_alert(message_threshold=1000, age_threshold_hours=24)

        # Should not alert when younger
        stats.oldest_message_age_seconds = 1000
        assert not stats.should_alert(message_threshold=1000, age_threshold_hours=24)


class TestDLQMessage:
    """Test DLQ message representation."""

    def test_message_creation(self):
        """DLQMessage should store message data."""
        properties = Mock()
        properties.content_type = "application/json"
        properties.delivery_mode = 2

        message = DLQMessage(
            delivery_tag=123,
            body=b'{"job_id": "test-job"}',
            properties=properties,
            queue_name="tts_jobs_dlq",
        )

        assert message.delivery_tag == 123
        assert message.queue_name == "tts_jobs_dlq"

    def test_message_json_parsing(self):
        """DLQMessage should parse JSON body."""
        properties = Mock()
        body = b'{"job_id": "test-job", "status": "failed"}'

        message = DLQMessage(
            delivery_tag=1,
            body=body,
            properties=properties,
            queue_name="test_queue",
        )

        assert message.parsed_body is not None
        assert message.parsed_body["job_id"] == "test-job"
        assert message.parsed_body["status"] == "failed"

    def test_message_invalid_json(self):
        """DLQMessage should handle invalid JSON gracefully."""
        properties = Mock()
        body = b"not valid json"

        message = DLQMessage(
            delivery_tag=1,
            body=body,
            properties=properties,
            queue_name="test_queue",
        )

        assert message.parsed_body is None

    def test_message_to_dict(self):
        """DLQMessage should serialize to dictionary."""
        properties = Mock()
        properties.content_type = "application/json"
        properties.delivery_mode = 2
        properties.headers = {}

        message = DLQMessage(
            delivery_tag=123,
            body=b'{"job_id": "test"}',
            properties=properties,
            queue_name="test_queue",
        )

        msg_dict = message.to_dict()

        assert isinstance(msg_dict, dict)
        assert msg_dict["delivery_tag"] == 123
        assert msg_dict["queue_name"] == "test_queue"
        assert "body" in msg_dict


class TestDLQMonitorInit:
    """Test DLQ monitor initialization."""

    def test_init_with_env_var(self):
        """DLQMonitor should read URL from environment."""
        with patch.dict(
            "os.environ", {"RABBITMQ_URL": "amqp://guest:guest@localhost/"}
        ):
            monitor = DLQMonitor()
            assert monitor.rabbitmq_url == "amqp://guest:guest@localhost/"

    def test_init_missing_url_raises_error(self):
        """DLQMonitor should raise error when URL is missing."""
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="RabbitMQ URL not provided"):
                DLQMonitor()

    def test_init_with_parameters(self):
        """DLQMonitor should accept initialization parameters."""
        monitor = DLQMonitor(
            rabbitmq_url="amqp://guest:guest@localhost/",
            check_interval=300,
        )

        assert monitor.rabbitmq_url == "amqp://guest:guest@localhost/"
        assert monitor.check_interval == 300

    def test_init_with_custom_thresholds(self):
        """DLQMonitor should accept custom alert thresholds."""
        thresholds = {"tts_jobs_dlq": 50, "tts_results_dlq": 25}
        monitor = DLQMonitor(
            rabbitmq_url="amqp://guest:guest@localhost/",
            thresholds=thresholds,
        )

        assert monitor.thresholds == thresholds

    def test_init_with_callback(self):
        """DLQMonitor should accept custom alert callback."""

        def my_alert(queue_name, stats):
            pass

        monitor = DLQMonitor(
            rabbitmq_url="amqp://guest:guest@localhost/",
            alert_callback=my_alert,
        )

        assert monitor.alert_callback == my_alert


class TestDLQMonitorStats:
    """Test DLQ statistics retrieval."""

    @patch("services.dlq_monitor.pika.BlockingConnection")
    def test_get_queue_stats(self, mock_connection_class):
        """DLQMonitor should retrieve queue statistics."""
        # Setup mock
        mock_channel = Mock()
        mock_method = Mock()
        mock_method.message_count = 42
        mock_method.consumer_count = 2
        mock_result = Mock()
        mock_result.method = mock_method
        mock_channel.queue_declare.return_value = mock_result

        mock_connection = Mock()
        mock_connection.channel.return_value = mock_channel
        mock_connection.is_closed = False
        mock_connection_class.return_value = mock_connection

        monitor = DLQMonitor(rabbitmq_url="amqp://guest:guest@localhost/")

        stats = monitor.get_queue_stats("test_queue")

        assert stats.queue_name == "test_queue"
        assert stats.message_count == 42
        assert stats.consumer_count == 2

    @patch("services.dlq_monitor.pika.BlockingConnection")
    def test_get_all_stats(self, mock_connection_class):
        """DLQMonitor should retrieve statistics for all queues."""
        # Setup mock
        mock_channel = Mock()

        def queue_declare_side_effect(queue, passive):
            mock_method = Mock()
            if queue == "tts_jobs_dlq":
                mock_method.message_count = 100
            else:
                mock_method.message_count = 50
            mock_method.consumer_count = 1
            mock_result = Mock()
            mock_result.method = mock_method
            return mock_result

        mock_channel.queue_declare.side_effect = queue_declare_side_effect

        mock_connection = Mock()
        mock_connection.channel.return_value = mock_channel
        mock_connection.is_closed = False
        mock_connection_class.return_value = mock_connection

        monitor = DLQMonitor(rabbitmq_url="amqp://guest:guest@localhost/")

        all_stats = monitor.get_all_stats()

        assert len(all_stats) == 2
        assert "tts_jobs_dlq" in all_stats
        assert "tts_results_dlq" in all_stats


class TestDLQMonitorAlerts:
    """Test alert triggering."""

    @patch("services.dlq_monitor.pika.BlockingConnection")
    def test_default_alert_handler(self, mock_connection_class):
        """DLQMonitor should use default alert handler."""
        mock_channel = Mock()
        mock_method = Mock()
        mock_method.message_count = 150
        mock_method.consumer_count = 0
        mock_result = Mock()
        mock_result.method = mock_method
        mock_channel.queue_declare.return_value = mock_result

        mock_connection = Mock()
        mock_connection.channel.return_value = mock_channel
        mock_connection.is_closed = False
        mock_connection_class.return_value = mock_connection

        monitor = DLQMonitor(rabbitmq_url="amqp://guest:guest@localhost/")

        stats = monitor.get_queue_stats("tts_jobs_dlq")

        # Should not raise exception when calling default handler
        monitor._default_alert_handler("tts_jobs_dlq", stats)

    def test_custom_alert_callback(self):
        """DLQMonitor should call custom alert callback."""
        alerts = []

        def custom_alert(queue_name, stats):
            alerts.append((queue_name, stats.message_count))

        monitor = DLQMonitor(
            rabbitmq_url="amqp://guest:guest@localhost/",
            alert_callback=custom_alert,
        )

        stats = DLQStats(queue_name="test", message_count=150, consumer_count=0)
        monitor.alert_callback("test", stats)

        assert len(alerts) == 1
        assert alerts[0] == ("test", 150)


class TestDLQMonitorMessages:
    """Test DLQ message operations."""

    @patch("services.dlq_monitor.pika.BlockingConnection")
    def test_get_dlq_messages(self, mock_connection_class):
        """DLQMonitor should retrieve DLQ messages."""
        # Setup mock
        mock_channel = Mock()

        # Setup message sequence
        messages_to_return = [
            (
                Mock(delivery_tag=1),
                Mock(content_type="application/json"),
                b'{"job_id": "1"}',
            ),
            (
                Mock(delivery_tag=2),
                Mock(content_type="application/json"),
                b'{"job_id": "2"}',
            ),
            (None, None, None),  # End of messages
        ]

        mock_channel.basic_get.side_effect = messages_to_return
        mock_channel.basic_nack = Mock()

        mock_connection = Mock()
        mock_connection.channel.return_value = mock_channel
        mock_connection.is_closed = False
        mock_connection_class.return_value = mock_connection

        monitor = DLQMonitor(rabbitmq_url="amqp://guest:guest@localhost/")

        messages = monitor.get_dlq_messages("tts_jobs_dlq", limit=5, consume=False)

        assert len(messages) == 2
        assert messages[0].delivery_tag == 1
        assert messages[1].delivery_tag == 2

    @patch("services.dlq_monitor.pika.BlockingConnection")
    def test_process_dlq_messages(self, mock_connection_class):
        """DLQMonitor should process DLQ messages with custom processor."""
        # Setup mock
        mock_channel = Mock()

        messages_to_return = [
            (
                Mock(delivery_tag=1),
                Mock(content_type="application/json"),
                b'{"job_id": "1"}',
            ),
            (
                Mock(delivery_tag=2),
                Mock(content_type="application/json"),
                b'{"job_id": "2"}',
            ),
            (None, None, None),
        ]

        mock_channel.basic_get.side_effect = messages_to_return
        mock_channel.basic_ack = Mock()
        mock_channel.basic_nack = Mock()

        mock_connection = Mock()
        mock_connection.channel.return_value = mock_channel
        mock_connection.is_closed = False
        mock_connection_class.return_value = mock_connection

        # Processor that succeeds on first message, fails on second
        def processor(message):
            return message.delivery_tag == 1

        monitor = DLQMonitor(rabbitmq_url="amqp://guest:guest@localhost/")

        stats = monitor.process_dlq_messages("tts_jobs_dlq", processor, limit=5)

        assert stats["processed"] == 1
        assert stats["skipped"] == 1
        assert stats["failed"] == 0


class TestDLQMonitorConnectivity:
    """Test DLQ monitor connection management."""

    @patch("services.dlq_monitor.pika.BlockingConnection")
    def test_connect_establishes_connection(self, mock_connection_class):
        """DLQMonitor should establish RabbitMQ connection."""
        mock_connection = Mock()
        mock_connection.is_closed = False
        mock_connection.channel.return_value = Mock()
        mock_connection_class.return_value = mock_connection

        monitor = DLQMonitor(rabbitmq_url="amqp://guest:guest@localhost/")
        monitor._connect()

        assert monitor._connection is not None
        mock_connection_class.assert_called_once()

    def test_disconnect_closes_connection(self):
        """DLQMonitor should safely close connection."""
        monitor = DLQMonitor(rabbitmq_url="amqp://guest:guest@localhost/")

        # Mock connection
        mock_connection = Mock()
        mock_connection.is_closed = False
        monitor._connection = mock_connection

        monitor._disconnect()

        mock_connection.close.assert_called_once()


class TestDLQMonitorBackground:
    """Test DLQ monitor background monitoring."""

    @patch("services.dlq_monitor.pika.BlockingConnection")
    def test_start_and_stop_monitoring(self, mock_connection_class):
        """DLQMonitor should start and stop background monitoring."""
        mock_connection = Mock()
        mock_connection.is_closed = False
        mock_channel = Mock()
        mock_method = Mock()
        mock_method.message_count = 10
        mock_method.consumer_count = 1
        mock_result = Mock()
        mock_result.method = mock_method
        mock_channel.queue_declare.return_value = mock_result
        mock_connection.channel.return_value = mock_channel
        mock_connection_class.return_value = mock_connection

        monitor = DLQMonitor(
            rabbitmq_url="amqp://guest:guest@localhost/",
            check_interval=0.1,
        )

        # Start monitoring
        monitor.start()
        assert monitor._running is True

        # Let it run for a bit
        time.sleep(0.3)

        # Stop monitoring
        monitor.stop()
        assert monitor._running is False

    def test_double_start_warning(self):
        """DLQMonitor should warn on double start."""
        monitor = DLQMonitor(
            rabbitmq_url="amqp://guest:guest@localhost/",
            check_interval=1,
        )

        monitor._running = True
        monitor.start()  # Should log warning

        # Cleanup
        monitor._running = False


class TestDLQMonitorPurge:
    """Test DLQ purge functionality."""

    @patch("services.dlq_monitor.pika.BlockingConnection")
    def test_purge_dlq(self, mock_connection_class):
        """DLQMonitor should purge all messages from DLQ."""
        mock_channel = Mock()
        mock_method = Mock()
        mock_method.message_count = 42
        mock_result = Mock()
        mock_result.method = mock_method
        mock_channel.queue_purge.return_value = mock_result

        mock_connection = Mock()
        mock_connection.channel.return_value = mock_channel
        mock_connection.is_closed = False
        mock_connection_class.return_value = mock_connection

        monitor = DLQMonitor(rabbitmq_url="amqp://guest:guest@localhost/")

        purged_count = monitor.purge_dlq("tts_jobs_dlq")

        assert purged_count == 42
        mock_channel.queue_purge.assert_called_once_with(queue="tts_jobs_dlq")


class TestDLQMonitorStatsHistory:
    """Test DLQ monitor statistics history."""

    def test_stats_history_storage(self):
        """DLQMonitor should store statistics history."""
        monitor = DLQMonitor(rabbitmq_url="amqp://guest:guest@localhost/")

        # Manually add stats to history
        stats = {"tts_jobs_dlq": DLQStats("tts_jobs_dlq", 10, 1)}
        monitor._stats_history.append(
            {
                "timestamp": datetime.now(),
                "stats": stats,
            }
        )

        history = monitor.get_stats_history(hours=24)

        assert len(history) > 0

    def test_stats_history_respects_time_window(self):
        """DLQMonitor should only return history within time window."""
        monitor = DLQMonitor(rabbitmq_url="amqp://guest:guest@localhost/")

        # Add old entry (older than 24 hours)
        old_timestamp = datetime.now() - timedelta(hours=48)
        stats = {"tts_jobs_dlq": DLQStats("tts_jobs_dlq", 10, 1)}
        monitor._stats_history.append(
            {
                "timestamp": old_timestamp,
                "stats": stats,
            }
        )

        # Add recent entry
        recent_timestamp = datetime.now() - timedelta(hours=1)
        monitor._stats_history.append(
            {
                "timestamp": recent_timestamp,
                "stats": stats,
            }
        )

        history = monitor.get_stats_history(hours=24)

        # Should only return recent entry
        assert len(history) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
