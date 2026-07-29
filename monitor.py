#!/usr/bin/env python
"""
Worker Status Monitor
Monitor RabbitMQ queues, circuit breaker status, and worker performance.
Usage: uv run monitor.py
"""

import os
import time
import logging
from datetime import datetime
from typing import Dict, Any
from urllib.parse import urlparse

import pika
from dotenv import load_dotenv
from pathlib import Path

# Load environment variables
env_file = Path(__file__).parent / ".env"
if env_file.exists():
    load_dotenv(str(env_file))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class WorkerMonitor:
    """Monitor TTS worker and RabbitMQ queue status."""

    def __init__(self):
        """Initialize the monitor."""
        self.connection = None
        self.channel = None
        self.stats = {
            "tts_jobs_pending": 0,
            "tts_jobs_total": 0,
            "tts_results_pending": 0,
            "last_check": None,
            "queue_depth_history": [],
        }

    def connect_rabbitmq(self):
        """Connect to RabbitMQ."""
        try:
            rabbitmq_url = os.getenv(
                "RABBITMQ_URL", "amqp://guest:guest@localhost:5672/"
            )
            logger.info("Connecting to RabbitMQ...")

            parsed = urlparse(rabbitmq_url)

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

            # Declare queues (passive mode - don't create if they don't exist)
            try:
                self.channel.queue_declare(queue="tts_jobs", passive=True)
                self.channel.queue_declare(queue="tts_results", passive=True)
                logger.info("✓ Connected to RabbitMQ")
            except pika.exceptions.ChannelClosedByBroker:
                logger.warning("Queues don't exist yet. Declaring them...")
                self.channel = self.connection.channel()
                self.channel.queue_declare(queue="tts_jobs", durable=True)
                self.channel.queue_declare(queue="tts_results", durable=True)
                logger.info("✓ Connected and queues declared")

        except Exception as e:
            logger.error(f"✗ Failed to connect to RabbitMQ: {str(e)}")
            raise

    def get_queue_status(self) -> Dict[str, Any]:
        """Get current queue status from RabbitMQ."""
        try:
            # Get queue stats
            method, properties, body = self.channel.basic_get(
                queue="tts_jobs", auto_ack=False
            )
            if method:
                self.channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

            # Declare passive to get stats without modifying
            tts_jobs_method = self.channel.queue_declare(queue="tts_jobs", passive=True)
            tts_results_method = self.channel.queue_declare(
                queue="tts_results", passive=True
            )

            status = {
                "tts_jobs_pending": tts_jobs_method.method.message_count,
                "tts_results_pending": tts_results_method.method.message_count,
                "timestamp": datetime.now().isoformat(),
            }

            return status

        except Exception as e:
            logger.error(f"Error getting queue status: {e}")
            return {
                "error": str(e),
                "timestamp": datetime.now().isoformat(),
            }

    def print_status(self, status: Dict[str, Any]):
        """Pretty print the status."""
        print("\n" + "=" * 70)
        print(
            f"📊 WORKER STATUS MONITOR - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        print("=" * 70)

        if "error" in status:
            print(f"❌ Error: {status['error']}")
        else:
            tts_jobs = status.get("tts_jobs_pending", 0)
            tts_results = status.get("tts_results_pending", 0)

            # Queue status
            print("\n📋 Queue Status:")
            print(f"   TTS Jobs (pending):   {tts_jobs:>5}")
            print(f"   TTS Results (pending):{tts_results:>5}")

            # Visual indicators
            print("\n📈 Queue Health:")
            if tts_jobs == 0:
                print("   ✓ TTS Jobs queue is empty (worker is idle)")
            elif tts_jobs < 5:
                print(f"   ⚠️  {tts_jobs} jobs queued (light load)")
            elif tts_jobs < 20:
                print(f"   ⚡ {tts_jobs} jobs queued (moderate load)")
            else:
                print(f"   🔥 {tts_jobs} jobs queued (HIGH LOAD)")

            if tts_results > 0:
                print(f"   ℹ️  {tts_results} results pending delivery")

            # Throughput (basic estimate)
            print("\n⏱️  Metrics:")
            print(f"   Last check: {status.get('timestamp', 'N/A')}")

    def disconnect(self):
        """Close RabbitMQ connection."""
        try:
            if self.connection and not self.connection.is_closed:
                self.connection.close()
                logger.info("✓ Disconnected from RabbitMQ")
        except Exception as e:
            logger.error(f"Error disconnecting: {e}")

    def run_continuous(self, interval: int = 5):
        """Run monitor continuously with specified interval."""
        logger.info(f"Starting continuous monitoring (interval: {interval}s)")
        logger.info("Press CTRL+C to stop\n")

        try:
            self.connect_rabbitmq()

            while True:
                status = self.get_queue_status()
                self.print_status(status)
                time.sleep(interval)

        except KeyboardInterrupt:
            print("\n\nShutting down monitor...")
            self.disconnect()

    def run_once(self):
        """Run monitor once and exit."""
        try:
            self.connect_rabbitmq()
            status = self.get_queue_status()
            self.print_status(status)
            self.disconnect()
        except Exception as e:
            logger.error(f"Monitor error: {e}")
            raise


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Worker Status Monitor")
    parser.add_argument(
        "--interval",
        type=int,
        default=5,
        help="Polling interval in seconds (default: 5)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run once and exit (default: continuous)",
    )

    args = parser.parse_args()

    monitor = WorkerMonitor()

    if args.once:
        monitor.run_once()
    else:
        monitor.run_continuous(interval=args.interval)
