# Agent Instructions for IndexTTS Worker

## Project Overview

IndexTTS Worker is a 24/7 background service that processes TTS synthesis jobs from a RabbitMQ queue, generates audio using the IndexTTS engine, and uploads results to S3-compatible storage.

## Environment & Package Management

**Important**: Always use `uv` (not `pip` or `python` directly) for running commands in this project.

- **Package manager**: `uv` (see https://docs.astral.sh/uv/getting-started/installation/)
- **Python version**: 3.12+ (specified in `.python-version`)
- **Lock file**: `uv.lock` (auto-generated, don't edit manually)

### Common Commands

```bash
uv sync              # Install/sync dependencies from pyproject.toml
uv run python file.py     # Run Python scripts
uv run pytest        # Run tests
uv run ruff format .      # Format code
uv run ruff check .       # Lint code
```

Never use:
- `python -m pytest` → use `uv run pytest` instead
- `python script.py` → use `uv run python script.py` instead
- `pip install ...` → use `uv add ...` instead
- `python -m py_compile` → use `uv run python -m py_compile` instead

## Architecture

### Key Components

- **`services/tts_worker.py`** - Main worker process (RabbitMQ consumer, orchestration)
- **`services/circuit_breaker.py`** - Circuit breaker pattern for resilience (S3, TTS)
- **`services/s3_config.py`** - **Dual-bucket S3 client** (independent storage/output configs)
- **`services/idempotent_upload.py`** - Idempotent upload with integrity verification
- **`services/logging_config.py`** - Structured logging with visual hierarchy
- **`indextts/`** - TTS engine (BigVGAN vocoder, FastSpeech2 acoustic model)

### S3 Dual-Bucket Architecture

The worker now supports **completely independent S3 configurations** for storage and output buckets:

- **Storage Bucket** (`S3_STORAGE_*`): Voice recordings, audio prompts (read-only during synthesis)
- **Output Bucket** (`S3_OUTPUT_*`): TTS synthesis results (write-only during synthesis)

Each bucket can have:
- Different S3 endpoints (different providers or regions)
- Separate credentials (different access keys)
- Different regions and SSL settings
- Independent retention/lifecycle policies

**Example**: Use AWS S3 for voices (premium, reliable) and DigitalOcean Spaces for TTS output (cheaper, high throughput).

**Backwards Compatible**: Legacy single-bucket mode (using `S3_*` vars) still works if you don't set `S3_STORAGE_*` and `S3_OUTPUT_*`.

### S3 Storage Structure

```
Storage Bucket (e.g., "voice-library"):
├── audio-prompts/
│   ├── {voice_id}.wav               # Worker reads voice prompts
│   └── {voice_id}.json              # Voice metadata

Output Bucket (e.g., "tts-output"):
├── studio/
│   ├── {job_id}.wav                 # Worker uploads long-term TTS results
│   └── {job_id}.json                # Job metadata
└── playground/
    ├── {job_id}.wav                 # Worker uploads temporary TTS (24h retention)
    └── {job_id}.json                # Job metadata
```

**Note**: Both buckets are logged clearly in startup summary.

## Configuration

### Environment Variables

#### Required Dual-Bucket Configuration

**Location**: `.env` file in project root (copy from `.env.example`)

Set **all** of these variables:

```bash
# RabbitMQ
RABBITMQ_URL=amqp://user:pass@host:5672/
RABBITMQ_HOST=localhost                 # For startup logs

# Storage Bucket (voices, audio prompts - read-only during synthesis)
S3_STORAGE_ENDPOINT_URL=https://storage-provider.com/s3
S3_STORAGE_ACCESS_KEY_ID=storage-key
S3_STORAGE_SECRET_ACCESS_KEY=storage-secret
S3_STORAGE_BUCKET_NAME=voice-library
S3_STORAGE_REGION=ap-southeast-1
S3_STORAGE_USE_SSL=true

# Output Bucket (TTS synthesis results - write-only during synthesis)
S3_OUTPUT_ENDPOINT_URL=https://output-provider.com/s3
S3_OUTPUT_ACCESS_KEY_ID=output-key
S3_OUTPUT_SECRET_ACCESS_KEY=output-secret
S3_OUTPUT_BUCKET_NAME=tts-output
S3_OUTPUT_REGION=us-east-1
S3_OUTPUT_USE_SSL=true
```

**Benefits**: Different providers, regions, credentials, and costs per bucket.

### Logging

Structured logging is configured in `services/logging_config.py`:

- **Console**: Compact format (HH:MM:SS timestamp, no module paths)
- **File** (optional): Full format with timestamps, module names, all metadata

Enable file logging:

```python
from services.logging_config import configure_logging
configure_logging(use_file=True, file_path="logs/worker.log")
```

## Development Workflow

### Running the Worker

```bash
uv run python services/tts_worker.py
```

Expected startup output:
```
18:30:45 [INFO    ] 
═══════════════════════════════════════════════════════════════════════════
                              STARTUP
═══════════════════════════════════════════════════════════════════════════
...
```

### Testing

```bash
# All tests
uv run pytest

# Specific test file
uv run pytest tests/test_circuit_breaker.py

# With coverage
uv run pytest --cov=services
```

### Code Quality

```bash
# Format code (fixes automatically)
uv run ruff format .

# Check for lint issues
uv run ruff check .

# Auto-fix common issues
uv run ruff check --fix .
```

## Code Style

- **Formatter**: Ruff (see `pyproject.toml` for config)
- **Linter**: Ruff
- **Type hints**: Required for function signatures
- **Docstrings**: Google-style for public methods

## Key Patterns

### Circuit Breaker Usage

Prevents cascading failures when S3 or TTS services are down:

```python
from services.circuit_breaker import get_circuit_breaker, CircuitBreakerError

breaker = get_circuit_breaker("S3Download", failure_threshold=5, reset_timeout=60)

try:
    with breaker:
        result = download_from_s3()
except CircuitBreakerError:
    logger.error("S3 service is unavailable (circuit open)")
```

### S3 Client Usage

```python
from services.s3_config import S3Client

client = S3Client()

# Download from storage bucket (voices)
client.download_file(
    remote_path="audio-prompts/voice_001.wav",
    local_path="/tmp/prompt.wav",
    bucket_type="storage",  # Specify which bucket
    max_retries=3
)

# Upload to output bucket (TTS results)
client.upload_file(
    local_path="/tmp/audio.wav",
    remote_path="tts-output/studio/job_123.wav",
    bucket_type="output",  # Specify which bucket
    metadata={"job_id": "job_123"}
)

# Presigned URL (temporary access)
url = client.generate_presigned_url(
    remote_path="audio-prompts/voice_001.wav",
    bucket_type="storage",
    expiration=3600
)
```

**bucket_type values:**
- `"storage"` - Storage bucket (voices, audio prompts)
- `"output"` - Output bucket (TTS synthesis results)

### Idempotent Upload

Prevents duplicate uploads if job is retried:

```python
from services.idempotent_upload import IdempotentUploader

uploader = IdempotentUploader(s3_client)

s3_path = uploader.upload_with_retry(
    job_id="job_123",
    local_path="/tmp/audio.wav",
    remote_path="tts-output/studio/job_123.wav",
    verify_integrity=True
)
```

### Logging

Use structured logging methods:

```python
from services.logging_config import get_logger

logger = get_logger(__name__)

# Successful operations
logger.success("S3 client initialized")

# Failures
logger.failure("Failed to connect to RabbitMQ")

# Warnings
logger.warning_icon("S3 client initialization failed, will retry on first use")

# Section headers
logger.section("STARTUP")
logger.subsection("Connecting to RabbitMQ")

# Standard logging
logger.info("Processing job: job_123")
logger.warning("Circuit breaker opened")
logger.error("Job processing failed")
```

## Gotchas

1. **Signal handlers**: Only call `_setup_signal_handlers()` once in `__init__` (it was duplicated, now fixed)
2. **S3 buckets**: Two separate concepts - clarify in code/logs which bucket you're using
3. **RabbitMQ prefetch**: Set `prefetch_count=1` to process one job at a time (prevents overload)
4. **Graceful shutdown**: Always stop consuming before closing connection
5. **File cleanup**: Temporary files are cleaned up in the `finally` block after job processing

## Documentation

- `DUAL_BUCKET_GUIDE.md` - Dual-bucket S3 configuration guide
- `LOGGING_REFACTOR.md` - Details on new structured logging system
- `WORKER_QUICKSTART.md` - Quick start guide
- `README.md` - Project overview
- `docs/` - Additional documentation

## Performance Considerations

- **Circuit breaker thresholds**:
  - S3: `failure_threshold=5`, `reset_timeout=60s`
  - TTS: `failure_threshold=3`, `reset_timeout=30s`
- **Exponential backoff**: 2, 4, 8 seconds for retries
- **Idempotent checks**: Prevent re-uploading same audio for same job
- **One-at-a-time processing**: RabbitMQ `prefetch_count=1` ensures stability

## Debugging Tips

1. **Check RabbitMQ queue**: Use RabbitMQ management UI (default: http://localhost:15672)
2. **Monitor circuit breakers**: Startup/shutdown logs show state of all breakers
3. **Check S3 connectivity**: S3 client init log confirms access
4. **Verify job IDs**: All job logs prefixed with `[JOB <id>]` for easy filtering
5. **File logging**: Enable to get full timestamps and module paths for investigation

## Git Workflow

- Create feature branches for changes
- Run `uv run ruff format . && uv run ruff check .` before committing
- Keep commits focused on single issues
- Include issue reference in commit messages if applicable
