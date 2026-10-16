# Worker & Queue Monitoring

IndexTTS Worker includes a consolidated monitoring system in [`services/messaging/dlq_monitor.py`](file:///Users/aa/git/github_uncgra/indexTTS-worker/services/messaging/dlq_monitor.py) that covers:
1. **Real-Time Worker & Queue Health** ([`WorkerMonitor`](file:///Users/aa/git/github_uncgra/indexTTS-worker/services/messaging/dlq_monitor.py#L578)) — live depth of job queues, result queues, and DLQs with visual load indicators.
2. **Dead-Letter Queue (DLQ) Monitoring & Maintenance** ([`DLQMonitor`](file:///Users/aa/git/github_uncgra/indexTTS-worker/services/messaging/dlq_monitor.py#L143)) — threshold alerting, message age tracking, inspection without consuming, custom reprocessing, and queue purging.
3. **Root CLI Entrypoint** ([`monitor.py`](file:///Users/aa/git/github_uncgra/indexTTS-worker/monitor.py)) — convenient top-level command-line launcher delegating directly to the consolidated monitor.

---

## Quick Start (CLI)

### 1. Real-Time Worker Status Dashboard

Run continuous queue depth monitoring (refreshes every 5 seconds by default):

```bash
# Continuous monitoring
uv run python monitor.py

# Single status check and exit
uv run python monitor.py --once

# Custom polling interval (e.g., 10 seconds)
uv run python monitor.py --interval 10

# Or via the unified service module
uv run python -m services.messaging.dlq_monitor status --once
```

**Example Dashboard Output**:
```
======================================================================
📊 WORKER STATUS MONITOR - 2026-10-17 04:30:00
======================================================================

📋 Queue Status:
   TTS Jobs (pending):       3
   TTS Results (pending):    1
   Failed Jobs (DLQ):        0

📈 Queue Health:
   ⚠️  3 jobs queued (light load)
   ℹ️  1 results pending delivery

⏱️  Metrics:
   Last check: 2026-10-17T04:30:00.123456
```

### 2. Dead-Letter Queue (DLQ) Operations

```bash
# Check current DLQ statistics against alert thresholds
uv run python -m services.messaging.dlq_monitor check

# Start continuous DLQ threshold monitoring in background
uv run python -m services.messaging.dlq_monitor monitor

# Peek at messages in a DLQ without acknowledging/removing them (default limit: 5)
uv run python -m services.messaging.dlq_monitor peek tts_jobs_failed 5

# Purge all messages in a DLQ (caution: permanently deletes messages)
uv run python -m services.messaging.dlq_monitor purge tts_jobs_failed
```

---

## Architecture

### Monitored Queues

| Queue Name | Queue Type | Role |
| :--- | :--- | :--- |
| `tts_jobs` | Main (Priority 0–10) | Pending TTS synthesis jobs waiting for worker consumption |
| `tts_results` | Main (Priority 0–10) | Synthesized audio & alignment results waiting for backend delivery |
| `tts_jobs_failed` | DLQ (`tts_jobs.dlx`) | Jobs rejected after maximum retries (DLX fanout) |
| `tts_results_failed` | DLQ (`tts_results.dlx`) | Results that failed downstream processing |
| `vox_jobs` | Main | Multi-clip video render jobs |
| `vox_results` | Main | Rendered video results |
| `vox_jobs_failed` | DLQ (`vox_jobs.dlx`) | Failed render requests |

### Health Thresholds & Indicators

`WorkerMonitor` categorizes main queue depth into four visual tiers:
- **Idle** (`0` jobs): `✓ TTS Jobs queue is empty (worker is idle)`
- **Light Load** (`1–4` jobs): `⚠️  N jobs queued (light load)`
- **Moderate Load** (`5–19` jobs): `⚡ N jobs queued (moderate load)`
- **High Load** (`≥ 20` jobs): `🔥 N jobs queued (HIGH LOAD)`
- **DLQ Alert** (`> 0` failed): `⚠️  N failed jobs in DLQ (needs investigation)`

`DLQMonitor` triggers alerts based on:
- **Depth threshold**: Default >100 messages for `tts_jobs_dlq` / `tts_jobs_failed`, >50 for `tts_results_dlq` / `tts_results_failed`.
- **Message age**: Any message remaining in DLQ for >24 hours triggers a critical alert.

---

## Python API Usage

### Using `WorkerMonitor`

```python
from services.messaging.dlq_monitor import WorkerMonitor

monitor = WorkerMonitor()

# Single check
status = monitor.run_once()
print("Pending jobs:", status["tts_jobs_pending"])
print("Failed jobs in DLQ:", status["tts_jobs_failed"])

# Programmatic queue check
monitor.connect_rabbitmq()
status = monitor.get_queue_status()
monitor.disconnect()
```

### Using `DLQMonitor`

```python
from services.messaging.dlq_monitor import DLQMonitor, DLQStats

def on_alert(queue_name: str, stats: DLQStats):
    print(f"ALERT on {queue_name}: {stats.message_count} messages!")

monitor = DLQMonitor(
    check_interval=300,  # 5 minutes
    alert_callback=on_alert,
    thresholds={
        "tts_jobs_failed": 50,
        "tts_results_failed": 25,
    },
)

# Start background monitoring thread
monitor.start()

# Check and retrieve stats
all_stats = monitor.get_all_stats()

# Peek messages without ack
messages = monitor.get_dlq_messages("tts_jobs_failed", limit=10, consume=False)
for msg in messages:
    print(msg.parsed_body)

# Stop monitoring
monitor.stop()
```

### Accessing Worker Status via `DLQMonitor`

```python
from services.messaging.dlq_monitor import DLQMonitor

dlq_mon = DLQMonitor()
worker_status = dlq_mon.get_worker_status()
print(worker_status)
```

---

## File Structure

- [`services/messaging/dlq_monitor.py`](file:///Users/aa/git/github_uncgra/indexTTS-worker/services/messaging/dlq_monitor.py) — Core consolidated monitor implementation (`WorkerMonitor`, `DLQMonitor`, `main`, `main_monitor`).
- [`monitor.py`](file:///Users/aa/git/github_uncgra/indexTTS-worker/monitor.py) — Lightweight launcher for convenient CLI execution (`uv run monitor.py`).
- [`tests/unit/test_dlq_monitor.py`](file:///Users/aa/git/github_uncgra/indexTTS-worker/tests/unit/test_dlq_monitor.py) — Complete unit test suite for both `DLQMonitor` and `WorkerMonitor`.
