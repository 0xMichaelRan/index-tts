#!/usr/bin/env python
"""
Worker Status Monitor Entry Point

Monitor RabbitMQ queues, circuit breaker status, and worker performance.
Consolidated into services.messaging.dlq_monitor.

Usage:
    uv run monitor.py
    uv run monitor.py --once
    uv run monitor.py --interval 10
"""

import sys
from services.messaging.dlq_monitor import WorkerMonitor, main_monitor

__all__ = ["WorkerMonitor", "main_monitor"]

if __name__ == "__main__":
    sys.exit(main_monitor())
