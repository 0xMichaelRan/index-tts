# Agent Instructions for IndexTTS Worker

## Project Overview

IndexTTS Worker is a 24/7 background service that processes TTS synthesis jobs from a RabbitMQ queue, generates audio using the IndexTTS engine, and uploads results to S3-compatible storage.

### Windows Setup

Before running any Python commands, activate the conda environment:

```powershell
conda activate index-tts
```

Then you can run Python commands normally:

```bash
python -m services.tts_worker
python -m pytest tests/pytest/test_tts_worker_core.py
```

**Note**: Do NOT use `uv` on Windows. The project uses conda for environment management on Windows.

### Package manager

- **Package manager**: `uv` on macOS/Linux, `conda` on Windows (see https://docs.astral.sh/uv/getting-started/installation/)
- **Python version**: 3.10+ (specified in `.python-version`)
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
- `python -m pytest` → use `uv run pytest` instead (on non-Windows)
- `python script.py` → use `uv run python script.py` instead (on non-Windows)
- `pip install ...` → use `uv add ...` instead
- `python -m py_compile` → use `uv run python -m py_compile` instead (on non-Windows)

## Architecture

### Key Components

- **`services/tts_worker.py`** - Main worker process (RabbitMQ consumer, orchestration)
- **`services/circuit_breaker.py`** - Circuit breaker pattern for resilience (S3, TTS)
- **`services/s3_config.py`** - **Dual-bucket S3 client** (independent misc/voice configs)
- **`services/idempotent_upload.py`** - Idempotent upload with integrity verification
- **`services/logging_config.py`** - Structured logging with visual hierarchy
- **`indextts/`** - TTS engine (BigVGAN vocoder, FastSpeech2 acoustic model)

### S3 Dual-Bucket Architecture

The worker now supports **completely independent S3 configurations** for misc and voice buckets:

- **Misc Bucket** (`S3_MISC_*`): Voice recordings, audio prompts (read-only during synthesis)
- **Voice Bucket** (`R2_VOICE_*`): TTS synthesis results (write-only during synthesis)

Each bucket can have:
- Different S3 endpoints (different providers or regions)
- Separate credentials (different access keys)
- Different regions and SSL settings
- Independent retention/lifecycle policies

**Example**: Use Supabase S3 for voices (premium, reliable) and Cloudflare R2 for TTS output (cheaper, high throughput).

### S3 Storage Structure

```
Misc Bucket:
├── audio-prompts/
│   ├── {voice_id}.wav               # Worker reads voice prompts
│   └── {voice_id}.json              # Voice metadata

Voice Bucket:
├── {job_type}/                      # studio or playground
│   └── {YYYYMMDD}/                  # Date folder (local timezone, e.g., 20260902)
│       └── {job_id}/                # Job-specific directory
│           ├── {filename}.mp3       # Audio output
│           └── {filename}.json      # Alignment sidecar
```

**Filename format**: `{language}_r{ratio}_{environment}[_voice{voice_id}].{ext}`

**Ratio format**: `r` + (ratio × 10, zero-padded to 2 digits)
- `1.0` → `r10`
- `1.2` → `r12`
- `0.7` → `r07`

**Path examples**:
```
# Studio with voice clone
studio/20260902/abc123/zh_r10_prod_voice42.mp3
studio/20260902/abc123/zh_r10_prod_voice42.json

# Playground without voice, faster speed
playground/20260902/xyz789/en_r15_dev.mp3
playground/20260902/xyz789/en_r15_dev.json

# Studio with slower speed
studio/20260902/def456/mixed_r07_prod.mp3
studio/20260902/def456/mixed_r07_prod.json
```

**Notes**: 
- Date is in local server timezone (YYYYMMDD format)
- Ratio format is compact: multiply by 10 and zero-pad (e.g., `r10`, `r12`, `r07`)
- `voice_id` only included in filename if > 0
- Language is detected from alignment (e.g., `zh`, `en`, `mixed_fallback`)
- Both buckets are logged clearly in startup summary

### Voice Caching

The worker implements **S3-path-based voice caching** to avoid regenerating mel-spectrograms for the same voice:

- **Cache Key**: S3 path (e.g., `voice-recordings/user/123/english.wav`) instead of local temp path
- **Benefits**: Eliminates redundant `cond_mel` generation (saves ~2-5s per job with same voice)
- **Scope**: Worker instance (cleared on restart)
- **Behavior**:
  - First job with a voice: Generates and caches `cond_mel`
  - Subsequent jobs with same S3 path: Reuses cached `cond_mel`
  - Different voice: Clears cache and generates new `cond_mel`

See `docs/VOICE_CACHING_FIX.md` for implementation details.

### TTS Inference Methods

The worker supports two inference methods for Windows/Linux systems:

- **`infer_fast()`** (default): Optimized for long text with sentence batching
  - 2-10x faster for multi-sentence text
  - Higher GPU memory usage
  - Bucket-based batching with configurable parameters
  - Best for production with adequate GPU resources
  
- **`infer()`**: Sequential processing, sentence by sentence
  - Slower but more stable
  - Lower GPU memory usage
  - More predictable behavior
  - Best for debugging or memory-constrained environments

**Configuration**: Set `TTS_USE_FAST_INFERENCE=false` in `.env` to use `infer()` instead of `infer_fast()`.

**Note**: macOS always uses `infer()` with native TTS, this setting only affects Windows/Linux GPU inference.

### Synthesis Caching (Database-Backed) ✅ IMPLEMENTED

**Status**: Fully implemented and tested

The worker implements **database-backed synthesis caching** to eliminate redundant synthesis when the same (text, voice) is requested with different speed ratios:

- **Cache Key**: SHA256 hash of `text + audio_prompt_path`
- **Storage**: PostgreSQL + local file system
- **Benefits**: 65-80% faster for cache hits (5s synthesis → 1s cache + time-stretch)
- **Capacity**: 10,000 cached entries with LRU eviction
- **Persistence**: Survives worker restarts

**Documentation**:
- **Implementation guide**: `docs/CACHE_IMPLEMENTATION_SUMMARY.md` - Architecture, setup, CLI, and troubleshooting

**Key Features**:
- ✅ Automatic cache lookup before synthesis
- ✅ Separate time-stretching step for ratio variations
- ✅ Automatic eviction of least-recently-used entries
- ✅ Cache management CLI tools
- ✅ Performance metrics and analytics
- ✅ Unit and integration tests

**Commands**:
```bash
# View cache statistics
uv run python scripts/manage_cache.py stats

# View top entries
uv run python scripts/manage_cache.py top --limit 20

# Evict old entries
uv run python scripts/manage_cache.py evict --count 1000

# Test cache service
uv run python scripts/test_cache_service.py

# Run unit tests
uv run pytest tests/test_cache_service.py -v
```

### Forced Alignment ✅ IMPLEMENTED

**Status**: Fully implemented and tested

The worker implements **mandatory forced alignment** using stable-whisper to generate word-level timestamps for every TTS synthesis job:

- **Engine**: stable-whisper (stable-ts >= 2.19.1)
- **Model**: Whisper `small` (CPU-only)
- **Library**: openai-whisper (via stable-ts)
- **Performance**: ~0.5-5s per minute of audio (CPU)
- **Language Strategy**: Majority-script detection for mixed ZH/EN text

**Why Forced Alignment?**
- Enables subtitle/karaoke rendering in video applications (Remotion)
- Provides word-level timestamps matching the final delivered audio
- Mandatory for all jobs (alignment failure fails the job)
- Runs on CPU to avoid GPU contention with IndexTTS synthesis

**Configuration** (`.env`):
```bash
# Whisper model size (default: small)
TTS_ALIGNMENT_MODEL=small

# Device (must be cpu; never use mps on macOS)
TTS_ALIGNMENT_DEVICE=cpu

# Model cache directory (optional, for persistent storage)
TTS_ALIGNMENT_MODEL_DIR=/opt/models/whisper

# Retry and circuit breaker
TTS_ALIGNMENT_MAX_RETRIES=2
CIRCUIT_BREAKER_ALIGNMENT_FAILURE_THRESHOLD=3
CIRCUIT_BREAKER_ALIGNMENT_RESET_TIMEOUT=60
```

**Output Files** (per job):
- `{job_id}_raw_alignment.json` — Native stable-whisper output (kept on disk, NOT uploaded)
- `{job_id}_alignment.srt` — SRT subtitle format (kept on disk, NOT uploaded)
- `{job_id}_alignment.json` — Parsed JSON (uploaded to S3, then deleted locally)

**JSON Schema** (v1):
```json
{
  "version": "1.0",
  "job_id": "abc123",
  "engine": "stable-whisper",
  "engine_version": "2.19.1",
  "model": "small",
  "device": "cpu",
  "audio_duration_seconds": 12.34,
  "language_strategy": "monolingual_zh",
  "alignment_quality": "monolingual_zh",
  "source_text": "你好，世界。",
  "segments": [...],
  "words": [
    {"word": "你", "start": 0.12, "end": 0.28, "probability": 0.98}
  ],
  "alignment_duration_seconds": 1.87,
  "aligned_at": "2026-09-02T02:30:00+00:00"
}
```

**RabbitMQ Result Extension**:
```python
{
    "alignment_path": "tts-audio/studio/{job_id}.json",  # S3 key of parsed JSON
    "alignment_duration_seconds": 1.87                   # CPU alignment time
}
```

**Features**:
- ✅ Automatic alignment after synthesis and time-stretching
- ✅ Majority-script language detection (monolingual ZH/EN + mixed fallback)
- ✅ Three output formats (raw JSON, SRT, parsed JSON)
- ✅ S3 upload of parsed JSON only
- ✅ Circuit breaker for transient failures
- ✅ Normalization mismatch detection (digits/currency warnings)
- ✅ Comprehensive error handling
- ✅ Unit tests (32 tests, all passing)

**Module Location**: `services/alignment.py`

**Documentation**: See [`docs/FORCED_ALIGNMENT.md`](./docs/FORCED_ALIGNMENT.md) for complete reference

**Testing**:
```bash
# Run alignment unit tests
uv run pytest tests/test_alignment.py -v

# Run worker integration tests (Windows/Linux with GPU only)
python -m pytest tests/pytest/test_tts_worker_alignment.py -v
```

---

### Audio Loudness Normalization ✅ IMPLEMENTED

**Status**: Fully implemented and tested

The worker implements **LUFS loudness normalization** using the ITU-R BS.1770-4 standard to ensure consistent perceived loudness across all generated TTS audio:

- **Standard**: ITU-R BS.1770-4 (industry-standard LUFS measurement)
- **Library**: `pyloudnorm` (BS.1770 compliant)
- **Default Target**: -16.0 LUFS (optimized for TTS/voice content)
- **Performance**: ~50ms overhead per audio file
- **Fallback**: Peak normalization if pyloudnorm unavailable

**Why LUFS?**
- LUFS (Loudness Units relative to Full Scale) measures **perceived loudness**, not just peak amplitude
- Used by all major streaming platforms (Spotify, YouTube, Apple Music)
- Prevents "loudness war" issues where different audio has inconsistent volume
- Ensures comfortable listening experience without manual volume adjustment

**Target LUFS Guidelines**:
- **-14.0 LUFS**: Streaming platforms (Spotify, YouTube, Apple Music)
- **-16.0 LUFS**: TTS/Voice content (default, good balance for speech clarity)
- **-18.0 to -20.0 LUFS**: Quiet content (podcasts, audiobooks)
- **-23.0 LUFS**: Broadcasting (EBU R128 standard)

**Configuration** (`.env`):
```bash
# Enable/disable loudness normalization
TTS_NORMALIZATION_ENABLED=true

# Target loudness in LUFS
TTS_NORMALIZATION_TARGET_LUFS=-16.0
```

**Features**:
- ✅ Automatic normalization on all TTS output
- ✅ True peak limiting to prevent clipping
- ✅ Silent audio detection (skips normalization for very quiet audio)
- ✅ Supports both torch tensors and numpy arrays
- ✅ Fallback to peak normalization if pyloudnorm unavailable
- ✅ Comprehensive error handling
- ✅ Detailed logging of normalization metrics

**Normalization Metrics** (logged for each job):
```
>> Loudness normalization applied (lufs_bs1770)
   Original LUFS: -22.35 dB
   Target LUFS: -16.00 dB
   Gain applied: +6.35 dB
>> Normalization time: 0.05 seconds
```

**Module Location**: `indextts/utils/audio_normalization.py`

**API Usage**:
```python
from indextts.utils.audio_normalization import normalize_loudness

# Normalize audio to -16 LUFS
normalized, metrics = normalize_loudness(
    audio=wav_tensor,           # torch.Tensor or np.ndarray
    sample_rate=24000,
    target_lufs=-16.0,
    enable_normalization=True,
    verbose=False
)

# Check metrics
print(f"Gain applied: {metrics['gain_db']:.2f} dB")
print(f"Method: {metrics['method']}")  # 'lufs_bs1770', 'peak_fallback', or 'disabled'
```

**Testing**:
```bash
# Run normalization unit tests
uv run pytest tests/test_audio_normalization.py -v

# Test with various audio types
uv run pytest tests/test_audio_normalization.py::TestNormalizeLoudness -v
```

## Configuration

### Environment Variables

#### Required Dual-Bucket Configuration

**Location**: `.env` file in project root (copy from `.env.example`)

Set **all** of these variables:

```bash
# RabbitMQ
RABBITMQ_URL=amqp://user:pass@host:5672/

# Misc Bucket (voices, audio prompts - read-only during synthesis)
S3_MISC_ENDPOINT_URL=https://storage-provider.com/s3
S3_MISC_ACCESS_KEY_ID=storage-key
S3_MISC_SECRET_ACCESS_KEY=storage-secret
S3_MISC_BUCKET_NAME=bucket-name
S3_MISC_REGION=ap-southeast-1
S3_MISC_USE_SSL=true

# Voice Bucket (TTS synthesis results - write-only during synthesis)
R2_VOICE_ENDPOINT_URL=https://output-provider.com/s3
R2_VOICE_ACCESS_KEY_ID=output-key
R2_VOICE_SECRET_ACCESS_KEY=output-secret
R2_VOICE_BUCKET_NAME=bucket-name
R2_VOICE_REGION=us-east-1
R2_VOICE_USE_SSL=true
```

**Benefits**: Different providers, regions, credentials, and costs per bucket.

#### Optional Configuration

**TTS Inference Method** (Windows/Linux only):
```bash
# Use fast inference mode (default: true)
TTS_USE_FAST_INFERENCE=true  # infer_fast() - 2-10x faster, higher memory
# TTS_USE_FAST_INFERENCE=false  # infer() - slower, lower memory, more stable
```

**TTS Synthesis Cache**:
```bash
TTS_CACHE_ENABLED=true
TTS_CACHE_MAX_ENTRIES=10000
TTS_CACHE_EVICTION_THRESHOLD=9000
TTS_CACHE_LOCAL_DIR=outputs/tts_cache
```

**Audio Normalization**:
```bash
TTS_NORMALIZATION_ENABLED=true
TTS_NORMALIZATION_TARGET_LUFS=-16.0
```

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
    remote_path="tts-audio/studio/job_123.mp3",
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
    remote_path="tts-audio/studio/job_123.mp3",
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

- `docs/DUAL_BUCKET_GUIDE.md` - Dual-bucket S3 configuration guide
- `docs/WORKER_SETUP.md` - Complete worker setup and installation guide
- `docs/FORCED_ALIGNMENT.md` - Forced alignment reference documentation
- `docs/CACHE_IMPLEMENTATION_SUMMARY.md` - Synthesis cache implementation guide
- `docs/LOUDNESS_NORMALIZATION_FIX.md` - Audio normalization implementation
- `docs/` - Complete documentation directory

## Performance Considerations

- **Circuit breaker thresholds**:
  - S3: `failure_threshold=5`, `reset_timeout=60s`
  - TTS: `failure_threshold=3`, `reset_timeout=30s`
- **Exponential backoff**: 2, 4, 8 seconds for retries
- **Idempotent checks**: Prevent re-uploading same audio for same job
- **One-at-a-time processing**: RabbitMQ `prefetch_count=1` ensures stability
- **Automatic reconnection**: RabbitMQ connection automatically reconnects with exponential backoff (5s → 300s)
  - Initial delay: 5 seconds
  - Maximum delay: 300 seconds (5 minutes)
  - Infinite retries with graceful shutdown support

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


## RabbitMQ Configuration (Updated)

### Dead Letter Exchange (DLX) Setup

The worker follows a standardized DLX pattern (consistent with studio-backend and remotion worker):

**Pattern**:
- **Main Queue**: `tts_jobs`
- **DLX Exchange**: `tts_jobs.dlx` (fanout, durable)
- **DLQ Queue**: `tts_jobs_failed` (durable)

**Configuration** (in `services/tts_worker.py`):
```python
# Declare DLX (fanout exchange)
channel.exchange_declare(
    exchange="tts_jobs.dlx",
    exchange_type="fanout",
    durable=True
)

# Declare DLQ
channel.queue_declare(queue="tts_jobs_failed", durable=True)

# Bind DLQ to DLX
channel.queue_bind(
    queue="tts_jobs_failed",
    exchange="tts_jobs.dlx",
    routing_key=""
)

# Declare main queue with DLX routing
channel.queue_declare(
    queue="tts_jobs",
    durable=True,
    arguments={
        "x-dead-letter-exchange": "tts_jobs.dlx",
        "x-dead-letter-routing-key": "tts_jobs_failed",
    }
)
```

### Message Flow

1. **Normal Processing**: Producer → `tts_jobs` → Worker → Ack → Message Removed
2. **Transient Failure**: Producer → `tts_jobs` → Worker → Nack (requeue=True) → Back to `tts_jobs`
3. **Permanent Failure**: Producer → `tts_jobs` → Worker → Nack (requeue=False) → `tts_jobs.dlx` → `tts_jobs_failed`

### Retry Strategy

- **Transient errors**: S3 download failures, temporary TTS engine errors → Retry with circuit breaker
- **Data errors**: Invalid text, corrupt audio prompts → Fail immediately (no requeue)
- **Max retries**: `MAX_RETRY_COUNT` env var (default: 3) → routes to DLQ after exhaustion

### Documentation

See `docs/RABBITMQ_DLX_STANDARD.md` (in studio-backend repo) for complete standard specification.
