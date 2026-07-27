#!/usr/bin/env python
"""
Simple entry point to run the TTS worker.
Use: uv run worker.py
"""

from services.tts_worker import IndexTTSWorker
import os

if __name__ == "__main__":
    worker = IndexTTSWorker(
        rabbitmq_host=os.getenv("RABBITMQ_HOST", "localhost"),
        rabbitmq_port=int(os.getenv("RABBITMQ_PORT", 5672)),
        rabbitmq_user=os.getenv("RABBITMQ_USER", "guest"),
        rabbitmq_password=os.getenv("RABBITMQ_PASSWORD", "guest"),
        s3_bucket=os.getenv("S3_BUCKET", "tts-output"),
        s3_region=os.getenv("AWS_REGION", "us-east-1"),
    )
    worker.start()
