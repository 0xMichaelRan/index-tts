# RabbitMQ Setup Guide

This guide explains how to configure RabbitMQ queues for the TTS service with dead-letter queue (DLQ) support for reliable message processing.

## Table of Contents

1. [Overview](#overview)
2. [Queue Architecture](#queue-architecture)
3. [Prerequisites](#prerequisites)
4. [Installation](#installation)
5. [Configuration](#configuration)
6. [Usage](#usage)
7. [Monitoring](#monitoring)
8. [Troubleshooting](#troubleshooting)

## Overview

The TTS service uses RabbitMQ for asynchronous job processing with the following features:

- **Durable queues**: Messages survive broker restarts
- **Dead-letter queues**: Failed messages are routed to DLQs for investigation
- **Message TTL**: Automatic cleanup of old messages
- **Queue limits**: Prevent unlimited queue growth
- **Circuit breaker**: Handle failures gracefully

## Queue Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Main Queues (Durable)                   │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  tts_jobs                                                   │
│  ├── TTL: 24 hours                                          │
│  ├── Max Length: 10,000 messages                           │
│  ├── Overflow: reject-publish                              │
│  └── Dead-letter → tts_jobs_dlq                            │
│                                                             │
│  tts_results                                                │
│  ├── TTL: 7 days                                           │
│  ├── Max Length: 10,000 messages                           │
│  └── Dead-letter → tts_results_dlq                         │
│                                                             │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                Dead-Letter Queues (Durable)                 │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  tts_jobs_dlq                                               │
│  ├── TTL: 7 days                                           │
│  └── Max Length: 5,000 messages                            │
│                                                             │
│  tts_results_dlq                                            │
│  ├── TTL: 7 days                                           │
│  └── Max Length: 5,000 messages                            │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Queue Details

#### Main Queues

**tts_jobs**
- **Purpose**: TTS synthesis job requests from studio-backend
- **TTL**: 24 hours (jobs expire after 1 day)
- **Max Length**: 10,000 messages
- **Overflow Behavior**: Reject new messages when full
- **Dead-letter**: Messages rejected after 3 retries → `tts_jobs_dlq`

**tts_results**
- **Purpose**: TTS synthesis results sent back to studio-backend
- **TTL**: 7 days (results retained for a week)
- **Max Length**: 10,000 messages
- **Dead-letter**: Failed result processing → `tts_results_dlq`

#### Dead-Letter Queues

**tts_jobs_dlq**
- **Purpose**: Failed job messages requiring manual investigation
- **TTL**: 7 days
- **Max Length**: 5,000 messages

**tts_results_dlq**
- **Purpose**: Failed result messages
- **TTL**: 7 days
- **Max Length**: 5,000 messages

## Prerequisites

### 1. Install RabbitMQ

**Docker (Recommended for Development)**:
```bash
docker run -d \
  --name rabbitmq \
  -p 5672:5672 \
  -p 15672:15672 \
  -e RABBITMQ_DEFAULT_USER=admin \
  -e RABBITMQ_DEFAULT_PASS=admin123 \
  rabbitmq:3-management
```

**Ubuntu/Debian**:
```bash
sudo apt-get update
sudo apt-get install rabbitmq-server
sudo systemctl start rabbitmq-server
sudo systemctl enable rabbitmq-server
```

**Windows**:
Download from [RabbitMQ official website](https://www.rabbitmq.com/download.html)

### 2. Install Python Dependencies

```bash
# Install pika for RabbitMQ client
pip install pika

# Or install with RabbitMQ extra
pip install -e ".[rabbitmq]"
```

### 3. Verify RabbitMQ is Running

```bash
# Check service status
sudo systemctl status rabbitmq-server  # Linux
docker ps | grep rabbitmq              # Docker

# Access management UI
http://localhost:15672                 # Default: guest/guest
```

## Configuration

### Environment Variables

Create or update your `.env` file in the `services/` directory:

```bash
# RabbitMQ Connection
RABBITMQ_URL=amqp://guest:guest@localhost:5672/

# Alternative: Individual parameters (legacy)
RABBITMQ_HOST=localhost
RABBITMQ_PORT=5672
RABBITMQ_USER=guest
RABBITMQ_PASSWORD=guest
RABBITMQ_VHOST=/

# Worker Configuration
WORKER_LOG_LEVEL=INFO
```

### URL Format

```
amqp://[username]:[password]@[host]:[port]/[vhost]
```

Examples:
```bash
# Local development
RABBITMQ_URL=amqp://guest:guest@localhost:5672/

# CloudAMQP (production)
RABBITMQ_URL=amqps://user:pass@squid.rmq.cloudamqp.com/user

# Custom vhost
RABBITMQ_URL=amqp://admin:secret@rabbitmq.example.com:5672/tts-prod
```

## Usage

### Method 1: Command Line Script

```bash
# Using environment variable
export RABBITMQ_URL="amqp://guest:guest@localhost:5672/"
python -m services.rabbitmq_config

# Or pass URL directly
python -m services.rabbitmq_config "amqp://guest:guest@localhost:5672/"
```

### Method 2: Python Code

```python
from services.rabbitmq_config import configure_queues

# Configure all queues
configure_queues(
    rabbitmq_url="amqp://guest:guest@localhost:5672/"
)

# Or use environment variable
import os
os.environ["RABBITMQ_URL"] = "amqp://guest:guest@localhost:5672/"
configure_queues()
```

### Method 3: Integration with Worker

Add queue configuration to your worker startup:

```python
from services.rabbitmq_config import configure_queues
from services.tts_worker import IndexTTSWorker

# Configure queues on startup
try:
    configure_queues()
    print("✓ RabbitMQ queues configured")
except Exception as e:
    print(f"✗ Queue configuration failed: {e}")
    # Continue anyway - queues may already exist

# Start worker
worker = IndexTTSWorker(...)
worker.start()
```

### Configuration is Idempotent

The configuration script can be safely run multiple times. It will:
- Create queues if they don't exist
- Update existing queues with new parameters
- Not affect messages already in queues

### Expected Output

```
======================================================================
Starting RabbitMQ Queue Configuration
======================================================================
RabbitMQ Host: localhost:5672
Virtual Host: /
Connecting to RabbitMQ (attempt 1/3)...
Successfully connected to RabbitMQ

Configuring queues...
----------------------------------------------------------------------
✓ Queue 'tts_jobs' configured successfully
✓ Queue 'tts_results' configured successfully
✓ Queue 'tts_jobs_dlq' configured successfully
✓ Queue 'tts_results_dlq' configured successfully
----------------------------------------------------------------------
✓ All queues configured successfully
======================================================================
Connection closed
```

## Monitoring

### 1. RabbitMQ Management UI

Access the web interface at http://localhost:15672 (default credentials: guest/guest)

**What to Monitor**:
- Queue message counts
- Consumer connections
- Message rates (publish/deliver)
- Dead-letter queue depths

### 2. Get Queue Information via CLI

```bash
# List all queues
rabbitmqctl list_queues name messages consumers

# Get detailed queue info
rabbitmqctl list_queues name messages consumers \
  memory message_bytes_persistent
```

### 3. Python Monitoring

```python
from services.rabbitmq_config import get_queue_info

# Get queue statistics
info = get_queue_info()

for queue_name, stats in info.items():
    print(f"{queue_name}:")
    print(f"  Messages: {stats.get('message_count', 'N/A')}")
    print(f"  Consumers: {stats.get('consumer_count', 'N/A')}")
```

### 4. Alerting

Set up alerts when:
- `tts_jobs_dlq` exceeds 100 messages (job failures)
- `tts_results_dlq` exceeds 50 messages (result processing failures)
- Any message remains in DLQ for >24 hours
- Main queues reach 80% capacity (8,000 messages)

## Troubleshooting

### Connection Refused

**Symptom**: `pika.exceptions.AMQPConnectionError: Connection refused`

**Solutions**:
1. Check if RabbitMQ is running:
   ```bash
   sudo systemctl status rabbitmq-server  # Linux
   docker ps                              # Docker
   ```

2. Verify connection parameters in `.env`
3. Check firewall rules (port 5672 must be open)
4. Test connection:
   ```bash
   telnet localhost 5672
   ```

### Authentication Failed

**Symptom**: `ProbableAuthenticationError`

**Solutions**:
1. Verify credentials in `RABBITMQ_URL`
2. Create user if needed:
   ```bash
   rabbitmqctl add_user myuser mypassword
   rabbitmqctl set_user_tags myuser administrator
   rabbitmqctl set_permissions -p / myuser ".*" ".*" ".*"
   ```

### Queue Full (reject-publish)

**Symptom**: Messages rejected when publishing

**Solutions**:
1. Increase `x-max-length` in `QUEUE_CONFIGS`
2. Scale up consumers to process messages faster
3. Investigate why messages are accumulating

### Messages in Dead-Letter Queue

**Symptom**: `tts_jobs_dlq` or `tts_results_dlq` has messages

**Investigation Steps**:

1. **Inspect DLQ Messages** (via Management UI):
   - Go to Queues → `tts_jobs_dlq`
   - Click "Get Messages"
   - Review message headers for error details

2. **Check Message Headers**:
   ```python
   x-death: [
     {
       "reason": "rejected",
       "count": 3,
       "queue": "tts_jobs",
       "time": "2024-12-25T10:00:00Z"
     }
   ]
   ```

3. **Common Causes**:
   - Invalid message format
   - Worker errors (check worker logs)
   - Network issues during processing
   - S3 upload failures

4. **Recovery**:
   ```bash
   # Move messages back to main queue (use shovel plugin)
   rabbitmqctl shovel tts_jobs_dlq tts_jobs
   
   # Or consume DLQ messages manually
   python scripts/process_dlq.py
   ```

### Slow Message Processing

**Symptom**: Messages accumulating in `tts_jobs`

**Solutions**:
1. Scale workers horizontally (run multiple instances)
2. Check worker logs for bottlenecks
3. Monitor IndexTTS synthesis time
4. Verify S3 upload speed

### Queue Configuration Not Applied

**Symptom**: Changes to `QUEUE_CONFIGS` not reflected

**Solutions**:
1. Delete and recreate queues (⚠️ will lose messages):
   ```bash
   rabbitmqctl delete_queue tts_jobs
   python -m services.rabbitmq_config
   ```

2. Or use RabbitMQ policies (non-destructive)

## Advanced Configuration

### Custom Queue Parameters

Edit `QUEUE_CONFIGS` in `services/rabbitmq_config.py`:

```python
QUEUE_CONFIGS = {
    "tts_jobs": {
        "durable": True,
        "arguments": {
            "x-dead-letter-exchange": "",
            "x-dead-letter-routing-key": "tts_jobs_dlq",
            "x-message-ttl": 86400000,  # 24 hours
            "x-max-length": 10000,
            "x-overflow": "reject-publish",
            
            # Optional: Add priority support
            "x-max-priority": 10,
            
            # Optional: Add lazy queues for disk storage
            "x-queue-mode": "lazy",
        },
    },
}
```

### Connection Pooling

For high-throughput production:

```python
import pika
from pika import connection

# Use connection pool
pool = connection.ConnectionPool(
    connection_parameters=pika.URLParameters("amqp://..."),
    max_size=10,
)

with pool.acquire() as conn:
    channel = conn.channel()
    # Use channel
```

## Production Recommendations

1. **Use CloudAMQP or managed RabbitMQ** for production
2. **Enable TLS** for encrypted connections (amqps://)
3. **Set up monitoring** with Prometheus + Grafana
4. **Configure alerts** for DLQ depths
5. **Regular backups** of queue definitions
6. **Test failover** and recovery procedures
7. **Document runbooks** for common issues

## Related Documentation

- [TTS Worker Guide](./TTS_WORKER.md)
- [Architecture Overview](./ARCHITECTURE.md)
- [API Documentation](./API.md)
- [RabbitMQ Official Docs](https://www.rabbitmq.com/documentation.html)

## Support

For issues or questions:
1. Check [FAQ](./FAQ.md)
2. Review worker logs: `tail -f logs/worker.log`
3. Check RabbitMQ logs: `tail -f /var/log/rabbitmq/rabbit@*.log`
4. Open an issue on GitHub
