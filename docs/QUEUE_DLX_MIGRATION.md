# Queue DLX Migration Guide

## Problem

The `tts_results` queue (and `tts_jobs`) was declared with **incompatible DLX configurations** between indexTTS-worker and studio-backend, causing a `PRECONDITION_FAILED` error:

```
PRECONDITION_FAILED - inequivalent arg 'x-dead-letter-exchange' for queue 'tts_results' 
in vhost 'jtdiqgdu': received the value 'tts_results.dlx' of type 'longstr' but current is none
```

### Root Cause

**indexTTS-worker** (OLD pattern):
- DLX via default exchange: `x-dead-letter-exchange: ""`
- DLQ name: `tts_results_dlq`

**studio-backend** (NEW standardized pattern):
- DLX via named fanout exchange: `x-dead-letter-exchange: "tts_results.dlx"`
- DLQ name: `tts_results_failed`

RabbitMQ doesn't allow changing queue arguments after creation, so the queue must be deleted and recreated.

## Solution

### 1. Update indexTTS-worker Configuration

**File**: `services/rabbitmq_config.py`

**Changes**:
- Updated `QUEUE_CONFIGS` to use new DLX pattern
- Added `declare_dlx_exchanges()` helper to create fanout exchanges
- Added `bind_dlq_to_dlx()` helper to bind DLQs to DLX exchanges
- Updated `configure_queues()` to follow 4-step setup process
- Renamed DLQ queues: `*_dlq` → `*_failed`

### 2. Queue Structure Comparison

#### OLD Pattern (indexTTS-worker before fix)

```
tts_jobs (queue)
  └─ x-dead-letter-exchange: ""
  └─ x-dead-letter-routing-key: "tts_jobs_dlq"
       └─> (default exchange routes to tts_jobs_dlq)

tts_results (queue)
  └─ x-dead-letter-exchange: ""
  └─ x-dead-letter-routing-key: "tts_results_dlq"
       └─> (default exchange routes to tts_results_dlq)
```

#### NEW Pattern (standardized across all repos)

```
tts_jobs.dlx (fanout exchange)
  └─> tts_jobs_failed (queue)

tts_jobs (queue)
  └─ x-dead-letter-exchange: "tts_jobs.dlx"
  └─ x-dead-letter-routing-key: "tts_jobs_failed"
       └─> (routes to tts_jobs.dlx → tts_jobs_failed)

tts_results.dlx (fanout exchange)
  └─> tts_results_failed (queue)

tts_results (queue)
  └─ x-dead-letter-exchange: "tts_results.dlx"
  └─ x-dead-letter-routing-key: "tts_results_failed"
       └─> (routes to tts_results.dlx → tts_results_failed)
```

### 3. Migration Process

#### Option A: Automated Script (Recommended)

```bash
# From indexTTS-worker directory
cd /path/to/indexTTS-worker

# Run migration script (will prompt for confirmation)
python scripts/fix_queue_dlx_migration.py

# Or with custom RabbitMQ URL:
python scripts/fix_queue_dlx_migration.py amqp://user:pass@host:5672/vhost
```

The script will:
1. Delete old queues: `tts_jobs`, `tts_results`, `tts_jobs_dlq`, `tts_results_dlq`
2. Recreate them with new DLX pattern using `rabbitmq_config.py`

#### Option B: Manual Steps

1. **Access RabbitMQ Management UI**
   - CloudAMQP: https://customer.cloudamqp.com/instance
   - Local: http://localhost:15672

2. **Delete old queues** (Queues tab):
   - `tts_jobs`
   - `tts_results`
   - `tts_jobs_dlq`
   - `tts_results_dlq`

3. **Recreate with new pattern**:
   ```bash
   cd /path/to/indexTTS-worker
   python -m services.rabbitmq_config
   ```

#### Option C: Using RabbitMQ CLI

```bash
# Delete old queues
rabbitmqadmin delete queue name=tts_jobs --vhost=your_vhost
rabbitmqadmin delete queue name=tts_results --vhost=your_vhost
rabbitmqadmin delete queue name=tts_jobs_dlq --vhost=your_vhost
rabbitmqadmin delete queue name=tts_results_dlq --vhost=your_vhost

# Recreate with new pattern
cd /path/to/indexTTS-worker
python -m services.rabbitmq_config
```

### 4. Post-Migration Steps

1. **Restart workers**:
   ```bash
   # Studio backend
   cd /path/to/studio-backend
   python -m app.services.background_worker

   # IndexTTS worker
   cd /path/to/indexTTS-worker
   python -m services.tts_worker
   ```

2. **Verify queue structure** in RabbitMQ Management UI:
   - Main queues: `tts_jobs`, `tts_results`
   - DLX exchanges: `tts_jobs.dlx`, `tts_results.dlx`
   - DLQs: `tts_jobs_failed`, `tts_results_failed`

3. **Test TTS job**:
   ```bash
   # From studio-backend
   python scripts/submit_test_job.py
   ```

## Benefits of New Pattern

1. **Consistency**: Same pattern across all workers (studio-backend, indexTTS-worker, remotion worker)
2. **Clarity**: Named exchanges make DLX routing explicit
3. **Standardization**: Follows `{queue_name}.dlx` → `{queue_name}_failed` convention
4. **Debugging**: Easier to trace failed messages through named exchanges

## Files Changed

### indexTTS-worker

- `services/rabbitmq_config.py` - Updated DLX configuration
- `scripts/fix_queue_dlx_migration.py` - New migration script
- `docs/QUEUE_DLX_MIGRATION.md` - This document

### studio-backend (no changes needed)

Already uses standardized pattern:
- `app/services/rabbitmq.py` - `declare_queue_with_dlx()` method

## Troubleshooting

### Error: "PRECONDITION_FAILED" still occurring

**Cause**: Old queues still exist in RabbitMQ

**Solution**: Run migration script or manually delete queues via management UI

### Error: "Queue not found" when consuming

**Cause**: Queues not created yet

**Solution**: Run `python -m services.rabbitmq_config` to create queues

### Workers can't publish/consume after migration

**Cause**: Workers still connected to old queue definitions

**Solution**: Restart all workers (studio-backend background_worker + indexTTS-worker)

## References

- Studio-backend DLX standard: `docs/RABBITMQ_DLX_STANDARD.md`
- Studio-backend standardization: `docs/RABBITMQ_STANDARDIZATION_SUMMARY.md`
- Studio-backend AGENTS.md: RabbitMQ Configuration section
