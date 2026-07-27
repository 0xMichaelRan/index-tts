"""
Dead-Letter Queue (DLQ) Monitoring and Alerting

Monitors dead-letter queues for failed TTS jobs and results processing.
Provides alerting when DLQ depths exceed thresholds and enables
manual intervention for problematic messages.

DLQ Architecture:
    - tts_jobs_dlq: Failed job messages after 3 retries
    - tts_results_dlq: Failed result processing messages

Alert Thresholds:
    - tts_jobs_dlq: Alert when >100 messages
    - tts_results_dlq: Alert when >50 messages
    - Any message in DLQ >24 hours: Critical alert

Usage:
    from services.dlq_monitor import DLQMonitor
    
    # Initialize monitor
    monitor = DLQMonitor(
        rabbitmq_url="amqp://guest:guest@localhost:5672/",
        check_interval=300,  # Check every 5 minutes
    )
    
    # Start monitoring
    monitor.start()
    
    # Get DLQ statistics
    stats = monitor.get_stats()
    
    # Manually process DLQ messages
    monitor.process_dlq_messages("tts_jobs_dlq", limit=10)
"""

import os
import json
import logging
import time
import threading
from typing import Optional, Dict, Any, List, Callable
from datetime import datetime, timedelta
from urllib.parse import urlparse
from dataclasses import dataclass, field

try:
    import pika
    from pika.exceptions import AMQPConnectionError, ChannelClosedByBroker
    PIKA_AVAILABLE = True
except ImportError:
    PIKA_AVAILABLE = False
    logging.warning("pika is not installed. Install with: pip install pika")

logger = logging.getLogger(__name__)


@dataclass
class DLQStats:
    """Statistics for a dead-letter queue."""
    queue_name: str
    message_count: int
    consumer_count: int
    oldest_message_age_seconds: Optional[int] = None
    last_check: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> dict:
        """Convert stats to dictionary."""
        return {
            "queue_name": self.queue_name,
            "message_count": self.message_count,
            "consumer_count": self.consumer_count,
            "oldest_message_age_seconds": self.oldest_message_age_seconds,
            "last_check": self.last_check.isoformat(),
        }
    
    def should_alert(self, message_threshold: int, age_threshold_hours: int = 24) -> bool:
        """Check if this DLQ should trigger an alert."""
        if self.message_count > message_threshold:
            return True
        
        if self.oldest_message_age_seconds:
            age_hours = self.oldest_message_age_seconds / 3600
            if age_hours > age_threshold_hours:
                return True
        
        return False


@dataclass
class DLQMessage:
    """Represents a message in a dead-letter queue."""
    delivery_tag: int
    body: bytes
    properties: Any
    queue_name: str
    parsed_body: Optional[dict] = None
    
    def __post_init__(self):
        """Parse message body as JSON."""
        try:
            self.parsed_body = json.loads(self.body)
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse DLQ message body as JSON: {self.body[:100]}")
            self.parsed_body = None
    
    def to_dict(self) -> dict:
        """Convert message to dictionary."""
        return {
            "delivery_tag": self.delivery_tag,
            "queue_name": self.queue_name,
            "body": self.parsed_body or self.body.decode("utf-8", errors="replace"),
            "properties": {
                "content_type": getattr(self.properties, "content_type", None),
                "delivery_mode": getattr(self.properties, "delivery_mode", None),
                "headers": getattr(self.properties, "headers", {}),
            }
        }


class DLQMonitorError(Exception):
    """Raised when DLQ monitoring encounters an error."""
    pass


class DLQMonitor:
    """
    Monitor dead-letter queues for failed messages and alert when thresholds are exceeded.
    
    Monitors the following DLQs:
    - tts_jobs_dlq: Failed job submissions after 3 retries
    - tts_results_dlq: Failed result processing
    """
    
    # Default alert thresholds
    DEFAULT_THRESHOLDS = {
        "tts_jobs_dlq": 100,
        "tts_results_dlq": 50,
    }
    
    # Age threshold for critical alerts (24 hours)
    AGE_THRESHOLD_HOURS = 24
    
    def __init__(
        self,
        rabbitmq_url: Optional[str] = None,
        check_interval: int = 300,  # 5 minutes
        alert_callback: Optional[Callable[[str, DLQStats], None]] = None,
        thresholds: Optional[Dict[str, int]] = None,
    ):
        """
        Initialize DLQ monitor.
        
        Args:
            rabbitmq_url: RabbitMQ connection URL (default: from RABBITMQ_URL env)
            check_interval: Seconds between DLQ checks
            alert_callback: Function to call when alert is triggered
            thresholds: Custom alert thresholds per queue
        """
        if not PIKA_AVAILABLE:
            raise ImportError("pika is required. Install with: pip install pika")
        
        self.rabbitmq_url = rabbitmq_url or os.getenv("RABBITMQ_URL")
        if not self.rabbitmq_url:
            raise ValueError("RabbitMQ URL not provided")
        
        self.check_interval = check_interval
        self.alert_callback = alert_callback or self._default_alert_handler
        self.thresholds = thresholds or self.DEFAULT_THRESHOLDS
        
        # State
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._connection: Optional[pika.BlockingConnection] = None
        self._channel: Optional[pika.channel.Channel] = None
        self._stats_history: List[Dict[str, DLQStats]] = []
        self._lock = threading.RLock()
        
        logger.info(
            f"DLQ Monitor initialized: check_interval={check_interval}s, "
            f"thresholds={self.thresholds}"
        )
    
    def _parse_rabbitmq_url(self) -> pika.ConnectionParameters:
        """Parse RabbitMQ URL into connection parameters."""
        parsed = urlparse(self.rabbitmq_url)
        
        credentials = pika.PlainCredentials(
            username=parsed.username or "guest",
            password=parsed.password or "guest",
        )
        
        return pika.ConnectionParameters(
            host=parsed.hostname or "localhost",
            port=parsed.port or 5672,
            virtual_host=parsed.path.lstrip("/") or "/",
            credentials=credentials,
            connection_attempts=3,
            retry_delay=2,
            heartbeat=600,
            blocked_connection_timeout=300,
        )
    
    def _connect(self) -> None:
        """Establish connection to RabbitMQ."""
        try:
            if self._connection and not self._connection.is_closed:
                return
            
            params = self._parse_rabbitmq_url()
            self._connection = pika.BlockingConnection(params)
            self._channel = self._connection.channel()
            logger.info("DLQ Monitor connected to RabbitMQ")
            
        except AMQPConnectionError as e:
            raise DLQMonitorError(f"Failed to connect to RabbitMQ: {e}") from e
    
    def _disconnect(self) -> None:
        """Close RabbitMQ connection."""
        try:
            if self._connection and not self._connection.is_closed:
                self._connection.close()
                logger.info("DLQ Monitor disconnected from RabbitMQ")
        except Exception as e:
            logger.warning(f"Error disconnecting from RabbitMQ: {e}")
        finally:
            self._connection = None
            self._channel = None
    
    def get_queue_stats(self, queue_name: str) -> DLQStats:
        """
        Get statistics for a specific queue.
        
        Args:
            queue_name: Name of the queue
            
        Returns:
            Queue statistics
            
        Raises:
            DLQMonitorError: If queue stats cannot be retrieved
        """
        try:
            self._connect()
            
            # Passive declare to get queue info without creating it
            result = self._channel.queue_declare(queue=queue_name, passive=True)
            
            stats = DLQStats(
                queue_name=queue_name,
                message_count=result.method.message_count,
                consumer_count=result.method.consumer_count,
            )
            
            # Try to peek at oldest message timestamp
            # Note: This requires actually consuming a message, so we skip it
            # to avoid interfering with real consumers
            
            return stats
            
        except ChannelClosedByBroker as e:
            raise DLQMonitorError(f"Queue '{queue_name}' does not exist: {e}") from e
        except Exception as e:
            raise DLQMonitorError(f"Failed to get stats for '{queue_name}': {e}") from e
    
    def get_all_stats(self) -> Dict[str, DLQStats]:
        """Get statistics for all monitored DLQs."""
        stats = {}
        for queue_name in self.thresholds.keys():
            try:
                stats[queue_name] = self.get_queue_stats(queue_name)
            except DLQMonitorError as e:
                logger.error(f"Failed to get stats for {queue_name}: {e}")
        
        return stats
    
    def _default_alert_handler(self, queue_name: str, stats: DLQStats) -> None:
        """Default alert handler that logs warnings."""
        threshold = self.thresholds.get(queue_name, 100)
        
        if stats.message_count > threshold:
            logger.warning(
                f"DLQ ALERT: '{queue_name}' has {stats.message_count} messages "
                f"(threshold: {threshold})"
            )
        
        if stats.oldest_message_age_seconds:
            age_hours = stats.oldest_message_age_seconds / 3600
            if age_hours > self.AGE_THRESHOLD_HOURS:
                logger.critical(
                    f"DLQ CRITICAL: '{queue_name}' has messages older than "
                    f"{self.AGE_THRESHOLD_HOURS} hours (oldest: {age_hours:.1f}h)"
                )
    
    def check_and_alert(self) -> Dict[str, DLQStats]:
        """
        Check all DLQs and trigger alerts if thresholds are exceeded.
        
        Returns:
            Dictionary of queue stats
        """
        stats = self.get_all_stats()
        
        with self._lock:
            # Store stats in history
            self._stats_history.append({
                "timestamp": datetime.now(),
                "stats": stats,
            })
            
            # Keep only last 24 hours of history
            cutoff = datetime.now() - timedelta(hours=24)
            self._stats_history = [
                entry for entry in self._stats_history
                if entry["timestamp"] > cutoff
            ]
        
        # Check thresholds and alert
        for queue_name, queue_stats in stats.items():
            threshold = self.thresholds.get(queue_name, 100)
            if queue_stats.should_alert(threshold, self.AGE_THRESHOLD_HOURS):
                try:
                    self.alert_callback(queue_name, queue_stats)
                except Exception as e:
                    logger.error(f"Alert callback failed for {queue_name}: {e}")
        
        return stats
    
    def _monitor_loop(self) -> None:
        """Background monitoring loop."""
        logger.info("DLQ monitoring loop started")
        
        while self._running:
            try:
                stats = self.check_and_alert()
                
                # Log summary
                total_messages = sum(s.message_count for s in stats.values())
                logger.info(
                    f"DLQ Check: {len(stats)} queues, {total_messages} total messages"
                )
                
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
            
            # Sleep until next check
            time.sleep(self.check_interval)
        
        logger.info("DLQ monitoring loop stopped")
    
    def start(self) -> None:
        """Start background monitoring."""
        if self._running:
            logger.warning("DLQ Monitor already running")
            return
        
        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        logger.info("DLQ Monitor started")
    
    def stop(self) -> None:
        """Stop background monitoring."""
        if not self._running:
            return
        
        logger.info("Stopping DLQ Monitor...")
        self._running = False
        
        if self._thread:
            self._thread.join(timeout=10)
        
        self._disconnect()
        logger.info("DLQ Monitor stopped")
    
    def get_dlq_messages(
        self,
        queue_name: str,
        limit: int = 10,
        consume: bool = False,
    ) -> List[DLQMessage]:
        """
        Retrieve messages from a DLQ.
        
        Args:
            queue_name: Name of the DLQ
            limit: Maximum number of messages to retrieve
            consume: If True, messages are removed from queue (acknowledged)
            
        Returns:
            List of DLQ messages
            
        Raises:
            DLQMonitorError: If messages cannot be retrieved
        """
        messages = []
        
        try:
            self._connect()
            
            for _ in range(limit):
                method, properties, body = self._channel.basic_get(queue=queue_name)
                
                if method is None:
                    # No more messages
                    break
                
                message = DLQMessage(
                    delivery_tag=method.delivery_tag,
                    body=body,
                    properties=properties,
                    queue_name=queue_name,
                )
                messages.append(message)
                
                if consume:
                    # Acknowledge and remove from queue
                    self._channel.basic_ack(delivery_tag=method.delivery_tag)
                else:
                    # Reject and requeue
                    self._channel.basic_nack(
                        delivery_tag=method.delivery_tag,
                        requeue=True
                    )
            
            logger.info(
                f"Retrieved {len(messages)} messages from '{queue_name}' "
                f"(consume={consume})"
            )
            
            return messages
            
        except Exception as e:
            raise DLQMonitorError(
                f"Failed to get messages from '{queue_name}': {e}"
            ) from e
    
    def process_dlq_messages(
        self,
        queue_name: str,
        processor: Callable[[DLQMessage], bool],
        limit: int = 10,
    ) -> Dict[str, int]:
        """
        Process messages from a DLQ with a custom processor function.
        
        Args:
            queue_name: Name of the DLQ
            processor: Function that processes a message and returns True if successful
            limit: Maximum number of messages to process
            
        Returns:
            Statistics: {"processed": n, "failed": m, "skipped": k}
            
        Example:
            def my_processor(message: DLQMessage) -> bool:
                # Custom processing logic
                if message.parsed_body:
                    # Retry the operation
                    return True
                return False
            
            stats = monitor.process_dlq_messages(
                "tts_jobs_dlq",
                my_processor,
                limit=50
            )
        """
        stats = {"processed": 0, "failed": 0, "skipped": 0}
        
        try:
            self._connect()
            
            for _ in range(limit):
                method, properties, body = self._channel.basic_get(queue=queue_name)
                
                if method is None:
                    break
                
                message = DLQMessage(
                    delivery_tag=method.delivery_tag,
                    body=body,
                    properties=properties,
                    queue_name=queue_name,
                )
                
                try:
                    if processor(message):
                        # Processing successful, remove from DLQ
                        self._channel.basic_ack(delivery_tag=method.delivery_tag)
                        stats["processed"] += 1
                    else:
                        # Processing failed, requeue
                        self._channel.basic_nack(
                            delivery_tag=method.delivery_tag,
                            requeue=True
                        )
                        stats["skipped"] += 1
                        
                except Exception as e:
                    logger.error(f"Error processing DLQ message: {e}")
                    # Requeue on error
                    self._channel.basic_nack(
                        delivery_tag=method.delivery_tag,
                        requeue=True
                    )
                    stats["failed"] += 1
            
            logger.info(
                f"Processed {stats['processed']} messages from '{queue_name}' "
                f"(failed: {stats['failed']}, skipped: {stats['skipped']})"
            )
            
            return stats
            
        except Exception as e:
            raise DLQMonitorError(
                f"Failed to process messages from '{queue_name}': {e}"
            ) from e
    
    def purge_dlq(self, queue_name: str) -> int:
        """
        Purge all messages from a DLQ.
        
        WARNING: This permanently deletes all messages in the queue.
        
        Args:
            queue_name: Name of the DLQ to purge
            
        Returns:
            Number of messages purged
        """
        try:
            self._connect()
            result = self._channel.queue_purge(queue=queue_name)
            message_count = result.method.message_count
            
            logger.warning(
                f"PURGED {message_count} messages from DLQ '{queue_name}'"
            )
            
            return message_count
            
        except Exception as e:
            raise DLQMonitorError(f"Failed to purge '{queue_name}': {e}") from e
    
    def get_stats_history(self, hours: int = 24) -> List[Dict]:
        """Get historical DLQ statistics."""
        with self._lock:
            cutoff = datetime.now() - timedelta(hours=hours)
            return [
                {
                    "timestamp": entry["timestamp"].isoformat(),
                    "stats": {
                        name: stats.to_dict()
                        for name, stats in entry["stats"].items()
                    }
                }
                for entry in self._stats_history
                if entry["timestamp"] > cutoff
            ]


def main():
    """CLI entry point for DLQ monitoring."""
    import sys
    
    # Parse command line arguments
    command = sys.argv[1] if len(sys.argv) > 1 else "monitor"
    
    try:
        monitor = DLQMonitor()
        
        if command == "monitor":
            # Start continuous monitoring
            monitor.start()
            logger.info("DLQ monitoring started. Press Ctrl+C to stop.")
            
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                logger.info("\nStopping monitor...")
                monitor.stop()
        
        elif command == "check":
            # Single check
            stats = monitor.check_and_alert()
            print("\nDLQ Statistics:")
            print("=" * 70)
            for queue_name, queue_stats in stats.items():
                print(f"\n{queue_name}:")
                print(f"  Messages: {queue_stats.message_count}")
                print(f"  Consumers: {queue_stats.consumer_count}")
                print(f"  Last Check: {queue_stats.last_check.isoformat()}")
        
        elif command == "peek":
            # Peek at DLQ messages
            queue_name = sys.argv[2] if len(sys.argv) > 2 else "tts_jobs_dlq"
            limit = int(sys.argv[3]) if len(sys.argv) > 3 else 5
            
            messages = monitor.get_dlq_messages(queue_name, limit=limit, consume=False)
            print(f"\nPeeking at {len(messages)} messages from '{queue_name}':")
            print("=" * 70)
            for i, msg in enumerate(messages, 1):
                print(f"\nMessage {i}:")
                print(json.dumps(msg.to_dict(), indent=2))
        
        else:
            print(f"Unknown command: {command}")
            print("Usage: python -m services.dlq_monitor [monitor|check|peek [queue_name] [limit]]")
            return 1
        
        return 0
        
    except Exception as e:
        logger.error(f"DLQ monitoring failed: {e}")
        return 1


if __name__ == "__main__":
    exit(main())
