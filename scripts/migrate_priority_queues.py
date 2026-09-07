"""
Queue Priority Migration Script

Recreates tts_jobs and tts_results queues with x-max-priority=10 support.

RabbitMQ does NOT allow changing queue arguments on existing queues — you must
delete and recreate them. This script handles that safely.

WARNING: Deleting tts_jobs will drop any unprocessed messages in the queue.
         Always drain the queue (wait for it to be empty) before running this.

Usage:
    # Check queue status first (safe, read-only)
    python scripts/migrate_priority_queues.py --check

    # Run migration (requires --force to confirm data loss)
    python scripts/migrate_priority_queues.py --force

    # Override RabbitMQ URL
    python scripts/migrate_priority_queues.py --force --url amqp://user:pass@host:5672/
"""

import argparse
import os
import sys
import time
import urllib.parse
import urllib.request
import json
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

try:
    import pika
except ImportError:
    print("ERROR: pika is not installed. Run: uv add pika", file=sys.stderr)
    sys.exit(1)

from services.rabbitmq_config import (  # noqa: E402
    MQ_PRIORITY_MAX,
    QUEUE_CONFIGS,
    bind_dlq_to_dlx,
    configure_queue,
    declare_dlx_exchanges,
)
from services.logging_config import configure_logging, get_logger  # noqa: E402

configure_logging()
logger = get_logger(__name__)

# Queues that need to be recreated (they change arguments)
PRIORITY_QUEUES = ["tts_jobs", "tts_results"]
# Queues that should already exist (DLQs — don't need priority, skip delete)
DLQ_QUEUES = ["tts_jobs_failed", "tts_results_failed"]


def _connect(rabbitmq_url: str) -> tuple[pika.BlockingConnection, pika.channel.Channel]:
    """Connect to RabbitMQ and return (connection, channel)."""
    from urllib.parse import urlparse

    parsed = urlparse(rabbitmq_url)
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
    )
    connection = pika.BlockingConnection(params)
    channel = connection.channel()
    return connection, channel


def _mgmt_url_from_amqp(amqp_url: str) -> str | None:
    """
    Derive the RabbitMQ Management HTTP API base URL from an AMQP URL.

    Maps:  amqp://user:pass@host:5672/vhost  →  http://user:pass@host:15672
           amqps://…:5671/…                  →  https://…:15671

    Returns None if the URL cannot be parsed.
    """
    try:
        parsed = urllib.parse.urlparse(amqp_url)
        scheme = "https" if parsed.scheme in ("amqps", "amqp+ssl") else "http"
        amqp_port = parsed.port or (5671 if scheme == "https" else 5672)
        # Management API is conventionally on amqp_port + 10000
        mgmt_port = amqp_port + 10000
        host = parsed.hostname or "localhost"
        if parsed.username and parsed.password:
            netloc = f"{parsed.username}:{parsed.password}@{host}:{mgmt_port}"
        else:
            netloc = f"{host}:{mgmt_port}"
        return f"{scheme}://{netloc}"
    except Exception:
        return None


def _fetch_queue_args_via_management(
    mgmt_base: str, vhost: str, queue_name: str, timeout: float = 5.0
) -> dict | None:
    """
    Fetch queue arguments from the RabbitMQ Management HTTP API.

    Returns the 'arguments' dict (e.g. {"x-max-priority": 10, ...}) on success,
    or None if the queue does not exist or the API is unreachable.
    """
    encoded_vhost = urllib.parse.quote(vhost, safe="")
    encoded_queue = urllib.parse.quote(queue_name, safe="")
    url = f"{mgmt_base}/api/queues/{encoded_vhost}/{encoded_queue}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
            return data.get("arguments", {})
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None  # Queue does not exist
        return None  # API error — treat as unknown
    except Exception:
        return None  # Management API unreachable


def check_queues(rabbitmq_url: str) -> dict:
    """
    Return info about current queue state (message counts, priority arguments).

    Uses AMQP passive declare for basic stats, then the RabbitMQ Management
    HTTP API to reliably read queue arguments (e.g. x-max-priority), because
    AMQP Queue.DeclareOk does NOT return queue arguments per the protocol spec.
    """
    connection, channel = _connect(rabbitmq_url)
    amqp_info: dict = {}
    try:
        for name in PRIORITY_QUEUES + DLQ_QUEUES:
            try:
                result = channel.queue_declare(queue=name, passive=True)
                amqp_info[name] = {
                    "exists": True,
                    "messages": result.method.message_count,
                    "consumers": result.method.consumer_count,
                }
            except pika.exceptions.ChannelClosedByBroker:
                amqp_info[name] = {"exists": False, "messages": 0, "consumers": 0}
                channel = connection.channel()
    finally:
        connection.close()

    # Now enrich with queue arguments from the Management HTTP API.
    # AMQP passive declare cannot return arguments — this is a protocol limitation.
    parsed = urllib.parse.urlparse(rabbitmq_url)
    vhost = parsed.path.lstrip("/") or "/"
    mgmt_base = _mgmt_url_from_amqp(rabbitmq_url)

    mgmt_available = mgmt_base is not None
    for name, data in amqp_info.items():
        if not data.get("exists"):
            data["arguments"] = None  # Queue absent
            data["mgmt_available"] = mgmt_available
            continue
        if mgmt_base is not None:
            args = _fetch_queue_args_via_management(mgmt_base, vhost, name)
            data["arguments"] = args  # None means API unreachable or queue gone
            data["mgmt_available"] = args is not None
        else:
            data["arguments"] = None
            data["mgmt_available"] = False

    return amqp_info


def delete_queue(channel: pika.channel.Channel, name: str) -> None:
    """Delete a queue unconditionally."""
    try:
        channel.queue_delete(queue=name)
        logger.info(f"  ✓ Deleted queue '{name}'")
    except Exception as e:
        logger.warning(f"  ⚠ Could not delete queue '{name}': {e}")


def migrate(rabbitmq_url: str) -> None:
    """Delete and recreate priority queues with x-max-priority support."""
    connection, channel = _connect(rabbitmq_url)
    logger.info("=" * 60)
    logger.info("Priority Queue Migration")
    logger.info("=" * 60)

    try:
        # Step 1: Declare DLX exchanges (idempotent)
        logger.info("\nStep 1: Ensuring DLX exchanges exist...")
        declare_dlx_exchanges(channel)

        # Step 2: Ensure DLQ queues exist (don't delete them — they may hold dead letters)
        logger.info("\nStep 2: Ensuring DLQ queues exist...")
        for name in DLQ_QUEUES:
            configure_queue(channel, name, QUEUE_CONFIGS[name])

        # Step 3: Bind DLQs to DLX (idempotent)
        logger.info("\nStep 3: Binding DLQs to DLX exchanges...")
        bind_dlq_to_dlx(channel)

        # Step 4: Delete and recreate main queues with priority
        logger.info("\nStep 4: Recreating main queues with x-max-priority support...")
        for name in PRIORITY_QUEUES:
            logger.info(f"  Deleting '{name}'...")
            delete_queue(channel, name)
            time.sleep(0.2)  # Brief pause for broker to settle

        # Reopen channel after deletions (channel may be closed after delete errors)
        channel.close()
        channel = connection.channel()

        for name in PRIORITY_QUEUES:
            configure_queue(channel, name, QUEUE_CONFIGS[name])
            cfg_args = QUEUE_CONFIGS[name]["arguments"]
            logger.info(
                f"    x-max-priority = {cfg_args.get('x-max-priority', 'NOT SET')}"
            )

        logger.info("\n" + "=" * 60)
        logger.info(
            f"✓ Migration complete! Queues now support priority 0–{MQ_PRIORITY_MAX}"
        )
        logger.info("=" * 60)

    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(
        description="Migrate tts_jobs/tts_results queues to support x-max-priority"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check current queue state without making changes",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Actually perform the migration (deletes and recreates queues)",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="RabbitMQ URL (default: RABBITMQ_URL env var)",
    )
    args = parser.parse_args()

    rabbitmq_url = args.url or os.getenv("RABBITMQ_URL")
    if not rabbitmq_url:
        print(
            "ERROR: RABBITMQ_URL not set. Use --url or set the environment variable.",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.check:
        print("\nChecking queue state...\n")
        info = check_queues(rabbitmq_url)
        for name, data in info.items():
            exists = data.get("exists", False)
            msgs = data.get("messages", 0)
            consumers = data.get("consumers", 0)
            state = "EXISTS" if exists else "MISSING"

            if name in PRIORITY_QUEUES and exists:
                arguments = data.get("arguments")
                mgmt_ok = data.get("mgmt_available", False)
                if not mgmt_ok:
                    # Management API unreachable — cannot inspect arguments
                    priority_note = "(priority: unknown — management API unavailable)"
                elif arguments is None:
                    priority_note = "(priority: unknown — management API error)"
                else:
                    actual = arguments.get("x-max-priority")
                    if actual is None:
                        priority_note = (
                            f"(priority: NOT SET — will add priority={MQ_PRIORITY_MAX})"
                        )
                    elif actual == MQ_PRIORITY_MAX:
                        priority_note = f"(priority={actual} ✓)"
                    else:
                        priority_note = f"(priority={actual} ✗ — expected {MQ_PRIORITY_MAX}, will recreate)"
            else:
                priority_note = ""

            status = f"{state:8s} | messages={msgs:5d} | consumers={consumers}" + (
                f" {priority_note}" if priority_note else ""
            )
            print(f"  {name:25s}: {status}")
        print()
        return 0

    if not args.force:
        print(
            "\nERROR: This script will DELETE and recreate tts_jobs and tts_results queues.\n"
            "Any pending messages in those queues will be LOST.\n\n"
            "To confirm, run with --force.\n"
            "To check queue state first (safe), run with --check.\n",
            file=sys.stderr,
        )
        return 1

    print(
        "\n⚠️  WARNING: Deleting tts_jobs and tts_results. Pending messages will be lost!\n"
    )
    time.sleep(2)  # Brief pause to allow Ctrl+C

    migrate(rabbitmq_url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
