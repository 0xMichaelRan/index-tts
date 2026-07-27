#!/usr/bin/env python
"""
Simple entry point to run the TTS worker.
Use: uv run worker.py

Environment variables are loaded from .env file in project root.
See .env.example for configuration template.
"""

import os

from services.tts_worker import IndexTTSWorker

if __name__ == "__main__":
    # RabbitMQ configuration
    # Support both RABBITMQ_URL and individual parameters
    rabbitmq_url = os.getenv("RABBITMQ_URL")
    rabbitmq_host = os.getenv("RABBITMQ_HOST", "localhost")
    rabbitmq_port = int(os.getenv("RABBITMQ_PORT", "5672"))
    rabbitmq_user = os.getenv("RABBITMQ_USER", "guest")
    rabbitmq_password = os.getenv("RABBITMQ_PASSWORD", "guest")

    worker = IndexTTSWorker(
        rabbitmq_url=rabbitmq_url,
        rabbitmq_host=rabbitmq_host,
        rabbitmq_port=rabbitmq_port,
        rabbitmq_user=rabbitmq_user,
        rabbitmq_password=rabbitmq_password,
    )
    worker.start()
