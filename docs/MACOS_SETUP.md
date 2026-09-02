# macOS Setup Guide

IndexTTS Worker runs on macOS with native text-to-speech synthesis (no GPU required). This guide covers the complete setup process.

## System Requirements

- **OS**: macOS 10.15+ (Intel or Apple Silicon)
- **Python**: 3.10+
- **Disk Space**: ~500MB for dependencies
- **Memory**: 2GB minimum (4GB+ recommended)

## Quick Start (5 minutes)

```bash
# 1. Clone the repository
git clone https://github.com/index-tts/indexTTS-worker.git
cd indexTTS-worker

# 2. Install uv (Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 3. Create Python 3.10+ environment and install dependencies
uv sync --extra mac

# 4. Install package in editable mode
uv pip install -e .

# 5. Configure environment variables
cp .env.example .env
# Edit .env with your RabbitMQ and S3 credentials

# 6. Run the worker
uv run python services/tts_worker.py
```

## Detailed Setup

### 1. Install uv (Package Manager)

macOS uses `uv` instead of `pip` for reliable dependency management:

```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Verify installation
uv --version
```

See [uv installation guide](https://docs.astral.sh/uv/getting-started/installation/) for alternatives.

### 2. Create Python Environment

```bash
cd /path/to/indexTTS-worker

# Create Python 3.10+ virtual environment
uv venv --python 3.10

### 3. Install Dependencies

Install only macOS-specific dependencies (no GPU/CUDA libraries):

```bash
# Sync dependencies with macOS extras
uv sync --extra mac
```

This installs:
- **Core**: RabbitMQ client, S3 SDK, audio processing (librosa, soundfile)
- **macOS Native TTS**: PyObjC frameworks for native speech synthesis
- **Database**: PostgreSQL async driver (for synthesis caching, optional)
- **Utilities**: Logging, environment config, circuit breaker patterns

**What's NOT installed** (macOS doesn't need these):
- PyTorch (`torch`)
- CUDA libraries
- GPU inference dependencies

### 4. Install Package in Editable Mode

```bash
uv pip install -e .
```

This installs the `indextts-worker` package and makes all modules (`services`, `app`, `indextts`) importable.

### 5. Configure Environment

Copy the example environment file:

```bash
cp .env.example .env
```

Edit `.env` with your configuration:

```bash
# RabbitMQ (message queue for job distribution)
RABBITMQ_URL=amqp://user:password@rabbitmq-host:5672/

# Storage Bucket (for voice prompts - read-only during synthesis)
S3_STORAGE_ENDPOINT_URL=https://s3.example.com
S3_STORAGE_ACCESS_KEY_ID=your-storage-key
S3_STORAGE_SECRET_ACCESS_KEY=your-storage-secret
S3_STORAGE_BUCKET_NAME=voice-storage
S3_STORAGE_REGION=us-east-1
S3_STORAGE_USE_SSL=true

# Output Bucket (for TTS results - write-only during synthesis)
S3_OUTPUT_ENDPOINT_URL=https://s3.example.com
S3_OUTPUT_ACCESS_KEY_ID=your-output-key
S3_OUTPUT_SECRET_ACCESS_KEY=your-output-secret
S3_OUTPUT_BUCKET_NAME=tts-output
S3_OUTPUT_REGION=us-east-1
S3_OUTPUT_USE_SSL=true

# Optional: TTS Synthesis Cache (PostgreSQL)
# DATABASE_URL=postgresql+asyncpg://user:password@localhost/tts_cache
```

### 6. Run the Worker

```bash
uv run python services/tts_worker.py
```

Expected output:

```
00:44:05 [INFO    ] 
══════════════════════════════════════════════════════════════════════
                               STARTUP                                
══════════════════════════════════════════════════════════════════════
00:44:05 [INFO    ] Platform:         Darwin
00:44:05 [SUCCESS ] TTS engine initialized
00:44:05 [SUCCESS ] S3 client initialized
...
00:44:05 [SUCCESS ] Connecting to RabbitMQ (rabbitmq-host)
00:44:05 [SUCCESS ] Connected to RabbitMQ
00:44:05 [INFO    ] Starting message consumption...
```

The worker is now listening for TTS jobs from the RabbitMQ queue.

## Optional Features

### Synthesis Caching (Database-Backed)

Cache synthesized audio to avoid regenerating the same (text, voice) pairs:

```bash
# Install PostgreSQL (Homebrew)
brew install postgresql

# Start PostgreSQL
brew services start postgresql

# Create database
createdb tts_cache

# Set in .env
DATABASE_URL=postgresql+asyncpg://user:password@localhost/tts_cache

# Run migrations
uv run alembic upgrade head
```

Then restart the worker. Cache will automatically:
- Store synthesized audio after first job
- Reuse cached audio for same (text, voice) combinations (80% faster)
- Automatically evict old entries to stay under 10k limit

**Cache management commands**:

```bash
# View cache statistics
uv run python scripts/manage_cache.py stats

# View top 20 cached entries
uv run python scripts/manage_cache.py top --limit 20

# Evict least-recently-used entries
uv run python scripts/manage_cache.py evict --count 1000
```

### macOS Native TTS Configuration

Native TTS uses the system's built-in voice synthesis engine. It's fast, offline, and requires no GPU. Configuration happens automatically based on the text language during synthesis.

**Supported languages**: English (en-US) and others supported by the system.

**Audio quality**: 24 kHz sample rate, WAV format.

**Speed control**: Via `ratio` parameter in job payload:
- `ratio=0.5`: 2x slower
- `ratio=1.0`: Normal speed
- `ratio=2.0`: 2x faster

### Audio Loudness Normalization

Automatically normalizes TTS output to consistent perceived loudness using ITU-R BS.1770-4 (LUFS standard):

```bash
# In .env
TTS_NORMALIZATION_ENABLED=true
TTS_NORMALIZATION_TARGET_LUFS=-16.0
```

This ensures all generated audio has the same perceived volume level, preventing "loudness wars" across different jobs.

## Common Tasks

### Check RabbitMQ Connection

```bash
# Verify RabbitMQ is reachable
nc -zv rabbitmq-host 5672
```

### Test S3 Configuration

```bash
# Quick S3 connectivity test
uv run python -c "from services.s3_config import S3Client; S3Client().test_connection()"
```

### View Logs

The worker outputs structured logs to console. To enable file logging, edit `services/tts_worker.py`:

```python
configure_logging(
    use_file=True,
    file_path="logs/worker.log"
)
```

### Run Tests

```bash
# All tests
uv run pytest tests/

# Specific test file
uv run pytest tests/test_tts_worker_core.py -v

# With coverage
uv run pytest --cov=services tests/
```

### Code Quality

```bash
# Format code
uv run ruff format .

# Check for linting issues
uv run ruff check .

# Auto-fix common issues
uv run ruff check --fix .
```

## Troubleshooting

### `ModuleNotFoundError: No module named 'services'`

The package wasn't installed in editable mode:

```bash
uv pip install -e . --force-reinstall
```

### `DATABASE_URL not set - TTS synthesis cache will be disabled`

This is normal if you haven't configured PostgreSQL. The worker functions fine without it (cache is just disabled). To enable:

```bash
# Set DATABASE_URL in .env
DATABASE_URL=postgresql+asyncpg://user:password@localhost/tts_cache

# Run migrations
uv run alembic upgrade head
```

### `Failed to connect to RabbitMQ`

Check that:
1. RabbitMQ is running and accessible
2. `RABBITMQ_URL` in `.env` is correct
3. Network connectivity: `nc -zv rabbitmq-host 5672`

### macOS Native TTS Not Working

Install the required framework:

```bash
uv sync --extra mac --force-reinstall
```

If issues persist, verify PyObjC installation:

```bash
uv run python -c "import PyObjC; print('PyObjC available')"
```

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                  IndexTTS Worker (macOS)                │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  RabbitMQ Queue                                        │
│  ↓ (consume TTS jobs)                                 │
│                                                         │
│  Job Processor                                         │
│  ├─ Download voice prompt from S3 (Storage Bucket)   │
│  ├─ Synthesize with native macOS TTS                 │
│  ├─ Apply time-stretching (if ratio ≠ 1.0)         │
│  ├─ Normalize loudness (LUFS)                        │
│  └─ Check synthesis cache (optional)                 │
│                                                         │
│  S3 Upload (Output Bucket)                            │
│  ↓ (upload generated audio)                           │
│                                                         │
│  Result Publishing                                     │
│  ↓ (publish job status back to RabbitMQ)            │
│                                                         │
│  Optional: PostgreSQL Cache                           │
│  (synthesis caching for faster repeated jobs)        │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

## Performance Characteristics

- **Synthesis Time**: 1-5 seconds per job (depends on text length)
- **Cache Hit Time**: 0.2-1 second (with time-stretching)
- **Upload Time**: 1-2 seconds to S3
- **Total Latency**: 2-8 seconds per job (first run), <2 seconds (cached)

## Next Steps

1. **Set up monitoring**: Use `monitor.py` to track worker health
2. **Configure autoscaling**: Run multiple workers for higher throughput
3. **Enable caching**: Set up PostgreSQL for significant speedup on repeated content
4. **Integrate with backend**: Submit jobs via RabbitMQ queue from your application

## Support

For issues or questions:
- Check the [troubleshooting section](#troubleshooting) above
- Review worker logs: `uv run python services/tts_worker.py`
- Check RabbitMQ management UI (usually `http://localhost:15672`)

## Additional Documentation

- **Architecture**: See `docs/ARCHITECTURE.md` for system design
- **S3 Configuration**: See `docs/DUAL_BUCKET_GUIDE.md` for dual-bucket setup details
- **API Reference**: See `docs/API.md` for job payload schema
- **Agent Instructions**: See `AGENTS.md` for development guidelines
