#!/usr/bin/env python3
"""Quick test to verify RabbitMQ connection."""
import sys
import os
from pathlib import Path
from urllib.parse import urlparse

# Add services to path
sys.path.insert(0, str(Path(__file__).parent))

# Load environment
from dotenv import load_dotenv
env_file = Path(__file__).parent / ".env"
if env_file.exists():
    load_dotenv(str(env_file))

import pika

rabbitmq_url = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
print(f"RABBITMQ_URL: {rabbitmq_url[:50]}...")

parsed = urlparse(rabbitmq_url)
print(f"\nParsed connection details:")
print(f"  Host: {parsed.hostname}")
print(f"  Port: {parsed.port}")
print(f"  VHost: {parsed.path.lstrip('/') or '/'}")
print(f"  User: {parsed.username}")

try:
    credentials = pika.PlainCredentials(
        username=parsed.username or "guest",
        password=parsed.password or "guest",
    )
    
    connection_params = pika.ConnectionParameters(
        host=parsed.hostname or "localhost",
        port=parsed.port or 5672,
        virtual_host=parsed.path.lstrip("/") or "/",
        credentials=credentials,
        connection_attempts=1,
        retry_delay=1,
    )

    print("\n🔗 Attempting to connect...")
    connection = pika.BlockingConnection([connection_params])
    channel = connection.channel()
    
    print("✅ Successfully connected to RabbitMQ!")
    print(f"✅ Channel opened: {channel}")
    
    # Check if queues exist
    channel.queue_declare(queue="tts_jobs", durable=True, passive=False)
    print("✅ Queue 'tts_jobs' is ready")
    
    channel.queue_declare(queue="tts_results", durable=True, passive=False)
    print("✅ Queue 'tts_results' is ready")
    
    connection.close()
    print("\n✅ Connection closed gracefully")
    
except Exception as e:
    print(f"\n❌ Connection failed: {e}")
    sys.exit(1)
