#!/usr/bin/env python
"""
Simple entry point to run the TTS worker.
Use: uv run worker.py

Environment variables are loaded from .env file in project root.
See .env.example for configuration template.
"""

from services.tts_worker import IndexTTSWorker
from services.worker_config import WorkerConfig

if __name__ == "__main__":
    config = WorkerConfig.from_env()
    config.validate()  # raises ValueError if RABBITMQ_URL is missing

    worker = IndexTTSWorker(config=config)
    worker.start()
