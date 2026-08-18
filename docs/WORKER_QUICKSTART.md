# TTS Worker Quick Start

Simple scripts to run and monitor the IndexTTS RabbitMQ worker.

**For installation and setup**, see: **[WORKER_SETUP.md](./WORKER_SETUP.md)**

---

## Running the Worker

### Start the Worker

```bash
uv run worker.py
```

This will:
- Initialize the TTS engine (IndexTTS GPU)
- Connect to RabbitMQ
- Initialize S3 client and circuit breakers
- Start listening for TTS jobs on the `tts_jobs` queue

**In Production:**
```bash
# Run in background with nohup
nohup uv run worker.py > worker.log 2>&1 &

# Or use tmux
tmux new-session -d -s tts-worker "cd /Users/aa/git/github_uncgra/indexTTS-worker && uv run worker.py"

# Or systemd/launchd for persistent service
```

### Stop the Worker

Press `CTRL+C` to gracefully shut down. The worker will:
- Stop accepting new messages
- Wait for current job to complete
- Print shutdown summary with circuit breaker stats
- Clean up resources

## Monitoring the Worker

### Real-time Monitoring

```bash
# Continuous monitoring (polls every 5 seconds)
uv run monitor.py

# Custom polling interval
uv run monitor.py --interval 10

# Check status once and exit
uv run monitor.py --once
```

### What Gets Monitored

- **TTS Jobs Queue**: Pending jobs waiting to be processed
- **TTS Results Queue**: Completed results waiting to be delivered
- **Queue Health**: Visual indicators for load level
  - ✓ Empty (worker is idle)
  - ⚠️ Light load (1-4 jobs)
  - ⚡ Moderate load (5-19 jobs)
  - 🔥 High load (20+ jobs)

### Example Output

```
======================================================================
📊 WORKER STATUS MONITOR - 2026-07-27 19:16:56
======================================================================

📋 Queue Status:
   TTS Jobs (pending):       0
   TTS Results (pending):   11

📈 Queue Health:
   ✓ TTS Jobs queue is empty (worker is idle)
   ℹ️  11 results pending delivery

⏱️  Metrics:
   Last check: 2026-07-27T19:16:56.701518
```

## Environment Variables

Required in `.env`:

```bash
# RabbitMQ
RABBITMQ_URL=amqp://user:pass@host:port/vhost

# S3 / Supabase Storage
S3_ENDPOINT=https://...supabase.co/storage/v1/s3
S3_ACCESS_KEY=...
S3_SECRET_KEY=...
S3_BUCKET_NAME=studio
AWS_REGION=us-east-1

# TTS Model
TTS_MODEL_DIR=checkpoints
```

## Architecture

```
┌─────────────────────────────────────────────────────┐
│ TTS Worker (uv run worker.py)                       │
├─────────────────────────────────────────────────────┤
│                                                     │
│  ┌──────────────────────────────────────────────┐  │
│  │ Message Handler (RabbitMQ Consumer)          │  │
│  │ - Receives TTS job from tts_jobs queue       │  │
│  │ - Deserializes JSON payload                  │  │
│  │ - Routes to process_job                      │  │
│  └──────────────────────────────────────────────┘  │
│                     ↓                               │
│  ┌──────────────────────────────────────────────┐  │
│  │ Job Processor                                │  │
│  │ 1. Download audio prompt from S3             │  │
│  │ 2. Synthesize audio (IndexTTS)                   │  │
│  │ 3. Upload to S3 (idempotent)                 │  │
│  │ 4. Publish result to tts_results queue       │  │
│  └──────────────────────────────────────────────┘  │
│                                                     │
│  ┌──────────────────────────────────────────────┐  │
│  │ Circuit Breakers (Fault Tolerance)           │  │
│  │ - S3Download: 5 failures → open (60s reset)  │  │
│  │ - IndexTTS: 3 failures → open (30s reset)    │  │
│  └──────────────────────────────────────────────┘  │
│                                                     │
│  ┌──────────────────────────────────────────────┐  │
│  │ Resilience Features                          │  │
│  │ - Exponential backoff retry (2s, 4s, 8s)     │  │
│  │ - Idempotent S3 upload (check existing)      │  │
│  │ - Graceful shutdown (SIGTERM/SIGINT)         │  │
│  │ - Partial failure recovery logging           │  │
│  └──────────────────────────────────────────────┘  │
│                                                     │
└─────────────────────────────────────────────────────┘
```

## Job Lifecycle

```
1. QUEUED (tts_jobs queue)
   ↓
2. PROCESSING
   - Download audio prompt from S3
   - Generate TTS audio
   - Upload results to S3
   ↓
3. COMPLETED or FAILED
   - Publish result to tts_results queue
   ↓
4. ACKNOWLEDGED
   - Message removed from queue
```

## Troubleshooting

### Worker won't start
```bash
# Check RabbitMQ connection
uv run monitor.py --once

# Check S3 credentials
cat .env | grep S3

# Check TTS model files
ls -la checkpoints/
```

### High queue depth
```bash
# Monitor in real-time
uv run monitor.py

# If stuck, gracefully restart:
# 1. Stop current worker (CTRL+C)
# 2. Check pending results (uv run monitor.py --once)
# 3. Restart worker (uv run worker.py)
```

### Circuit breaker open
The worker logs will show:
```
ERROR: S3 circuit breaker is open - service unavailable
ERROR: IndexTTS circuit breaker is open - service unavailable
```

This happens after repeated failures. The breaker will automatically reset after the timeout (S3: 60s, TTS: 30s).

### Out of memory
If synthesis crashes:
1. Check available RAM: `free -h`
2. Reduce concurrent jobs (RabbitMQ QoS: prefetch_count=1)
3. Monitor GPU memory if using CUDA

## Performance Tuning

### Throughput
- Increase parallelism: Run multiple worker instances
- Monitor queue depth: `uv run monitor.py`
- Adjust batch size for fast inference mode

### Reliability
- Circuit breaker thresholds in `services/circuit_breaker.py`
- Retry logic in `services/tts_worker.py`
- S3 upload idempotency in `services/idempotent_upload.py`

### Resource Usage
- TTS model: ~2GB GPU memory
- Per-job: ~500MB temporary storage
- Connection pooling: Automatic via pika

## Files

| File | Purpose |
|------|---------|
| `worker.py` | Simple entry point to start the worker |
| `monitor.py` | Monitor RabbitMQ queues and worker health |
| `services/tts_worker.py` | Main worker implementation |
| `services/circuit_breaker.py` | Fault tolerance and resilience |
| `services/s3_config.py` | S3/Supabase storage client |
| `services/idempotent_upload.py` | Idempotent retry for S3 uploads |

## Logs

Worker logs are printed to stdout. For persistent logs:

```bash
# Redirect to file
nohup uv run worker.py > worker.log 2>&1 &

# Tail logs
tail -f worker.log

# Search logs
grep "ERROR" worker.log
grep "\[JOB" worker.log  # Find specific job
```

## Next Steps

1. Start the worker: `uv run worker.py`
2. Monitor it: `uv run monitor.py` (in another terminal)
3. Send a test job to the RabbitMQ queue
4. Watch it get processed in real-time

For more details, see:
- `docs/` - Full documentation
- `services/` - Implementation details
- `.env.example` - Configuration reference
