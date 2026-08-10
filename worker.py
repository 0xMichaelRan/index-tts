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
    # RabbitMQ configuration via RABBITMQ_URL
    rabbitmq_url = os.getenv("RABBITMQ_URL")
    if not rabbitmq_url:
        raise ValueError(
            "RABBITMQ_URL environment variable is required. "
            "See .env.example for configuration template."
        )

    worker = IndexTTSWorker(rabbitmq_url=rabbitmq_url)
    worker.start()
