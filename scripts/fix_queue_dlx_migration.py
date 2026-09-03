#!/usr/bin/env python3
"""
Queue DLX Migration Script

Migrates from old DLX pattern to new standardized pattern and clears all queues:
- OLD: Uses default exchange with routing keys (tts_results_dlq)
- NEW: Uses named fanout exchanges (tts_results.dlx → tts_results_failed)

This script:
1. Deletes all queues (TTS, video, thumbnail, Agnes, credit warning queues)
2. Recreates TTS queues with the new standardized DLX pattern

WARNING: This will delete all messages in ALL queues!
Make sure no critical jobs are in the queues before running.

Queues affected:
- agnes_jobs
- credit_warnings
- thumbnail_jobs, thumbnail_jobs_failed
- tts_jobs, tts_jobs_failed, tts_results, tts_results_failed
- video_jobs, video_jobs_failed, video_results, video_results_failed

Usage:
    python scripts/fix_queue_dlx_migration.py

    # Or with custom RabbitMQ URL:
    python scripts/fix_queue_dlx_migration.py amqp://user:pass@host:5672/vhost
"""

import os
import sys
import logging

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import pika
    from services.rabbitmq_config import configure_queues, _parse_rabbitmq_url
except ImportError as e:
    print(f"Error: Missing required dependency: {e}")
    print("Install with: pip install pika")
    sys.exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def delete_queue_safe(channel: pika.channel.Channel, queue_name: str) -> bool:
    """
    Delete a queue if it exists (ignores NOT_FOUND errors).

    Returns:
        True if queue was deleted, False if it didn't exist
    """
    try:
        channel.queue_delete(queue=queue_name)
        logger.info(f"✓ Deleted queue '{queue_name}'")
        return True
    except pika.exceptions.ChannelClosedByBroker as e:
        if "NOT_FOUND" in str(e):
            logger.info(f"  Queue '{queue_name}' doesn't exist (skipping)")
            return False
        else:
            logger.error(f"✗ Failed to delete queue '{queue_name}': {e}")
            raise
    except Exception as e:
        logger.error(f"✗ Failed to delete queue '{queue_name}': {e}")
        raise


def delete_exchange_safe(channel: pika.channel.Channel, exchange_name: str) -> bool:
    """
    Delete an exchange if it exists (ignores NOT_FOUND errors).

    Returns:
        True if exchange was deleted, False if it didn't exist
    """
    try:
        channel.exchange_delete(exchange=exchange_name)
        logger.info(f"✓ Deleted exchange '{exchange_name}'")
        return True
    except pika.exceptions.ChannelClosedByBroker as e:
        if "NOT_FOUND" in str(e):
            logger.info(f"  Exchange '{exchange_name}' doesn't exist (skipping)")
            return False
        else:
            logger.error(f"✗ Failed to delete exchange '{exchange_name}': {e}")
            raise
    except Exception as e:
        logger.error(f"✗ Failed to delete exchange '{exchange_name}': {e}")
        raise


def migrate_queues(rabbitmq_url: str) -> bool:
    """
    Migrate queues from old DLX pattern to new standardized pattern.

    Returns:
        True if migration succeeded, False otherwise
    """
    logger.info("=" * 70)
    logger.info("Queue DLX Migration: OLD → NEW Pattern")
    logger.info("=" * 70)

    connection = None
    try:
        # Parse URL and connect
        conn_params_dict = _parse_rabbitmq_url(rabbitmq_url)
        connection_params = pika.ConnectionParameters(**conn_params_dict)

        logger.info(f"Connecting to RabbitMQ...")
        logger.info(f"  Host: {conn_params_dict['host']}:{conn_params_dict['port']}")
        logger.info(f"  VHost: {conn_params_dict['virtual_host']}")

        connection = pika.BlockingConnection(connection_params)
        channel = connection.channel()
        logger.info("✓ Connected to RabbitMQ\n")

        # Step 1: Delete old queues (both old and new naming conventions)
        logger.info("Step 1: Deleting all queues...")
        logger.info("-" * 70)

        # All queues to clear (includes TTS, video, thumbnail, agnes, and credit warning queues)
        old_queues = [
            "agnes_jobs",
            "credit_warnings",
            "thumbnail_jobs",
            "thumbnail_jobs_failed",
            "tts_jobs",
            "tts_jobs_failed",
            "tts_results",
            "tts_results_failed",
            "video_jobs",
            "video_jobs_failed",
            "video_results",
            "video_results_failed",
        ]
        deleted_count = 0

        for queue_name in old_queues:
            # Create a new channel for each delete (in case previous one closed)
            try:
                if delete_queue_safe(channel, queue_name):
                    deleted_count += 1
            except pika.exceptions.ChannelClosedByBroker:
                # Reopen channel and retry
                channel = connection.channel()
                if delete_queue_safe(channel, queue_name):
                    deleted_count += 1

        logger.info(f"\n✓ Deleted {deleted_count} queue(s)\n")

        # Step 2: Delete old DLX exchanges (if they exist)
        logger.info("Step 2: Cleaning up old DLX exchanges...")
        logger.info("-" * 70)

        # Note: Old pattern used default exchange (""), so no exchanges to delete
        logger.info("  Old pattern used default exchange (nothing to clean)\n")

        # Close connection before reconfiguring
        if connection and not connection.is_closed:
            connection.close()
            connection = None

        # Step 3: Recreate with new pattern
        logger.info("Step 3: Recreating queues with new DLX pattern...")
        logger.info("-" * 70)

        configure_queues(rabbitmq_url)

        logger.info("\n" + "=" * 70)
        logger.info("✓ Migration completed successfully!")
        logger.info("=" * 70)
        logger.info("\nNew queue structure:")
        logger.info("  Main queues:")
        logger.info("    • tts_jobs → tts_jobs.dlx → tts_jobs_failed")
        logger.info("    • tts_results → tts_results.dlx → tts_results_failed")
        logger.info("\n  DLX exchanges (fanout):")
        logger.info("    • tts_jobs.dlx")
        logger.info("    • tts_results.dlx")
        logger.info("\n  Dead-letter queues:")
        logger.info("    • tts_jobs_failed")
        logger.info("    • tts_results_failed")
        logger.info("=" * 70)

        return True

    except Exception as e:
        logger.error(f"\n✗ Migration failed: {e}", exc_info=True)
        return False

    finally:
        if connection and not connection.is_closed:
            connection.close()
            logger.info("Connection closed")


def main():
    """CLI entry point."""
    # Get RabbitMQ URL from argument or environment
    rabbitmq_url = sys.argv[1] if len(sys.argv) > 1 else os.getenv("RABBITMQ_URL")

    if not rabbitmq_url:
        print("Error: RabbitMQ URL not provided")
        print("\nUsage:")
        print("  python scripts/fix_queue_dlx_migration.py <rabbitmq_url>")
        print("  or set RABBITMQ_URL environment variable")
        print("\nExample:")
        print(
            "  python scripts/fix_queue_dlx_migration.py amqp://guest:guest@localhost:5672/"
        )
        return 1

    # Confirm before proceeding
    print("\n⚠️  WARNING: This will delete all messages in the following queues:")
    print("    • agnes_jobs")
    print("    • credit_warnings")
    print("    • thumbnail_jobs")
    print("    • thumbnail_jobs_failed")
    print("    • tts_jobs")
    print("    • tts_jobs_failed")
    print("    • tts_results")
    print("    • tts_results_failed")
    print("    • video_jobs")
    print("    • video_jobs_failed")
    print("    • video_results")
    print("    • video_results_failed")
    print("\nMake sure no critical jobs are in the queues before proceeding.")

    response = input("\nProceed with migration? (yes/no): ").strip().lower()
    if response not in ["yes", "y"]:
        print("Migration cancelled.")
        return 0

    print()  # Empty line for readability

    # Run migration
    success = migrate_queues(rabbitmq_url)

    if success:
        print("\n✓ Migration completed successfully!")
        print("\nNext steps:")
        print("  1. Restart studio-backend background worker")
        print("  2. Restart indexTTS-worker")
        print("  3. Verify queues in RabbitMQ management UI")
        return 0
    else:
        print("\n✗ Migration failed. Check logs above for details.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
