# Agent Instructions for IndexTTS Worker

## Project Overview

IndexTTS Worker is a 24/7 background service that processes TTS synthesis jobs and Vox video rendering jobs from RabbitMQ, generates audio using the IndexTTS engine (or macOS native speech synthesis), performs forced alignment for subtitle synchronization, and uploads results to S3-compatible storage.

### Environment & Package Manager

- **macOS / Linux**: `uv` (Python 3.10 pinned in `.python-version` and `pyproject.toml`). Never run bare `python` or `pip`.
- **Windows**: `conda` (activate `index-tts` environment). **Do NOT use `uv` on Windows**.
- **Lock file**: `uv.lock` (auto-generated, do not edit manually).

### Common Commands

```bash
# Dependencies
uv sync                       # Sync dependencies from pyproject.toml / uv.lock
uv pip install -e ".[mac]"     # Install macOS native TTS extras (AVFoundation/Cocoa)
uv pip install -e ".[dev]"     # Install development tools
uv pip install -e .           # Install package in editable mode

# Running Services
uv run python worker.py       # Start main 24/7 IndexTTS & Vox worker
uv run python monitor.py      # Monitor queue lengths, DLQ, and worker health
uv run python cache.py stats  # Inspect synthesis cache (stats, top, evict, clear)

# Testing
uv run pytest tests/unit                              # Run fast unit tests (mocked, ~3s)
uv run pytest tests/unit/test_circuit_breaker.py -v   # Run a single test file
uv run pytest tests/integration                       # Run integration tests (requires live PG/RabbitMQ/S3)
uv run pytest --cov=services                          # Run with code coverage

# Code Quality
uv run ruff check .           # Lint check
uv run ruff check --fix .     # Lint auto-fix
uv run ruff format .          # Format code
uv run ruff format --check .  # Check formatting

# RabbitMQ Topology Setup
uv run python -m services.messaging.rabbitmq_config   # Idempotently declare DLX exchanges, DLQs, and queues
```

*Windows alternative:*
```powershell
conda activate index-tts
python worker.py
python -m pytest tests/unit/test_handle_message.py
```

---

## High-Level Architecture

### 1. Dual-Consumer Process Model

- **TTS Worker (`services/tts/tts_worker.py`)**: Primary consumer on main thread. Consumes `tts_jobs` (`prefetch_count=1`, priority 0–10) and publishes results to `tts_results`.
- **Vox Render Consumer (`services/vox/vox_render_consumer.py`)**: Daemon thread spawned on Linux/Windows. Consumes `vox_jobs` and renders multi-clip MP4 videos matched to TTS narration timings using FFmpeg, publishing to `vox_results`. (Skipped on macOS).

### 2. TTS Job Processing Pipeline (`services/tts/synthesis_pipeline.py`)

Every TTS job moves through the following stages:
1. **Text Sanitization (`services/tts/text_sanitizer.py`)**: Normalizes characters and expands numbers/dates.
2. **Synthesis Cache Lookup (`services/tts/cache_manager.py`, `app/cache_service.py`)**:
   - Cache key: `SHA256(text + audio_prompt_path)`.
   - On hit: Skips synthesis, applies time-stretching (`soundfile` / `sox`) if `speedRatio != 1.0`.
   - On miss: Synthesizes base audio at `speedRatio=1.0`, saves to disk/PostgreSQL, then applies time-stretching.
3. **Voice Mel Caching (`services/tts/audio_processor.py`)**: In-memory caching of `cond_mel` keyed by audio prompt S3 path (saves ~2-5s when reusing voices).
4. **Loudness Normalization (`indextts/utils/audio_normalization.py`)**: Normalizes audio using ITU-R BS.1770-4 LUFS (default `-16.0 LUFS`) with true peak limiting.
5. **Mandatory Forced Alignment (`services/tts/alignment.py`)**:
   - Uses `stable-whisper` on CPU (`TTS_ALIGNMENT_DEVICE=cpu`) to produce word-level timestamps (`{job_id}_alignment.json`).
   - Alignment is mandatory; failure marks the job as failed.
6. **Idempotent S3 Upload (`services/storage/idempotent_upload.py`)**:
   - Uploads audio (`.wav`/`.mp3`) and parsed alignment JSON (`.json`) to the `tts` bucket.
   - Computes SHA256 integrity hashes to prevent duplicate uploads on retried jobs.

---

## S3 Bucket Registry Architecture

Bucket declarations are defined in `config/buckets.toml`, and credentials/endpoints are resolved per-type via environment variables (`services/storage/s3_registry.py` and `services/storage/s3_config.py`):

| Bucket Key | Bucket Name | Type | Access Pattern | Purpose | Env Var Prefix |
|---|---|---|---|---|---|
| `misc` | `klatu-misc` | `misc` | Read / Write | Temporary uploads and general assets | `S3_MISC_*` |
| `video` | `klatu-video` | `video` | Read / Write | Video clips and rendered MP4 outputs | `S3_VIDEO_*` |
| `audio` | `klatu-audio` | `audio` | Read-only | User voice prompts for TTS cloning | `S3_AUDIO_*` |
| `tts` | `klatu-tts` | `tts` | Write-only | Synthesized audio & alignment JSON (worker is sole writer) | `S3_TTS_*` |
| `11lab` | `klatu-11lab` | `11lab` | Read-only | User ElevenLabs audio exports | `S3_11LAB_*` |

Output S3 key structure: `{jobType}/{YYYYMMDD}/{jobId}/{filename}.{ext}`

---

## Messaging & Wire Protocol Rules

### Naming Domains
1. **RabbitMQ Wire Protocol (JSON Payloads)**: Strictly **`camelCase`**.
   - Inbound (`tts_jobs`, `vox_jobs`): `jobId`, `jobType`, `audioPromptPath`, `speedRatio`, `spokenLang`, `resolution`, `aspectRatio`, `clipS3Keys`, `beatNarrations`, etc.
   - Outbound (`tts_results`, `vox_results`): `jobId`, `jobType`, `audioPath`, `alignmentPath`, `audioDurationSeconds`, `synthesisDurationSeconds`, `alignmentDurationSeconds`, `cacheHit`, `retryCount`, `errorCode`, `errorMessage`, `startedAt`, `completedAt`.
   - **Crucial**: **NO snake_case fallbacks** in payload handling code (e.g. do NOT use `data.get("jobId") or data.get("job_id")`).
2. **Internal Python Code**: Strictly standard **`snake_case`** (PEP 8) for variables, DB columns, alignment JSON file contents, and internal methods.

### Dead-Letter Exchanges (DLX) & Queues
- **TTS Input**: `tts_jobs` → DLX: `tts_jobs.dlx` → DLQ: `tts_jobs_failed`
- **TTS Output**: `tts_results` → DLX: `tts_results.dlx` → DLQ: `tts_results_failed`
- **Vox Input**: `vox_jobs` → DLX: `vox_jobs.dlx` → DLQ: `vox_jobs_failed`
- **Vox Output**: `vox_results` → DLX: `vox_results.dlx` → DLQ: `vox_results_failed`

Declarations and bindings are managed via `uv run python -m services.messaging.rabbitmq_config`.

---

## Core Subsystems & Patterns

### 1. TTS Inference Methods (Windows/Linux)
- **`infer_fast()`** (default): Optimized sentence batching, 2-10x faster for long text. Best for production GPU.
- **`infer()`**: Sequential sentence-by-sentence. Set `TTS_USE_FAST_INFERENCE=false` in `.env` for constrained environments or debugging.
- *Note:* macOS always uses native AVFoundation speech synthesis.

### 2. Circuit Breaker (`services/common/circuit_breaker.py`)
Protects against downstream cascades:
- **S3 Download / Upload**: `failure_threshold=5`, `reset_timeout=60s`.
- **TTS Engine**: `failure_threshold=3`, `reset_timeout=30s`.
- **Alignment**: `failure_threshold=3`, `reset_timeout=60s`.

### 3. Synthesis Caching (Database-Backed)
- **Cache Key**: `SHA256(text + audio_prompt_path)`
- **Storage**: PostgreSQL metadata + local filesystem audio
- **CLI**: `uv run python cache.py stats|top|evict|clear`
- See [`docs/CACHE_IMPLEMENTATION_SUMMARY.md`](docs/CACHE_IMPLEMENTATION_SUMMARY.md) for full details.

### 4. Mandatory Forced Alignment
- **Model**: Whisper `small` (CPU-only: `TTS_ALIGNMENT_DEVICE=cpu`)
- Produces word-level timestamps in parsed `{job_id}_alignment.json` which is uploaded to S3. Local files (`.srt`, raw JSON) are kept locally and not uploaded.
- See [`docs/FORCED_ALIGNMENT.md`](docs/FORCED_ALIGNMENT.md) for schema and language detection strategy.

### 5. Audio Loudness Normalization
- Conforms to ITU-R BS.1770-4 standard LUFS (target `-16.0 LUFS`) with true peak limiting.
- Configured via `TTS_NORMALIZATION_ENABLED=true` and `TTS_NORMALIZATION_TARGET_LUFS=-16.0`.
- See [`docs/LOUDNESS_NORMALIZATION_FIX.md`](docs/LOUDNESS_NORMALIZATION_FIX.md) for implementation notes.

### 6. Idempotent S3 Upload (`services/storage/idempotent_upload.py`)
Computes SHA256 hashes to prevent re-uploading the same output if a job is retried or re-delivered.

### 7. Structured Logging (`services/common/logging_config.py`)
- Visual status hierarchy (`logger.success()`, `logger.failure()`, `logger.section()`, `logger.warning_icon()`).
- All job logs prefixed with `[JOB <id>]` for streamlined log filtering.

---

## Configuration Reference

Set these variables in `.env` (see `.env.example`):

```bash
# Database (PostgreSQL - Required for synthesis cache)
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/indextts_worker

# RabbitMQ
RABBITMQ_URL=amqp://user:pass@host:5672/

# S3 Credentials (endpoints and keys defined per bucket type)
S3_MISC_ENDPOINT_URL=https://storage-provider.com/s3
S3_MISC_ACCESS_KEY_ID=misc-key
S3_MISC_SECRET_ACCESS_KEY=misc-secret

S3_VIDEO_ENDPOINT_URL=https://storage-provider.com/s3
S3_VIDEO_ACCESS_KEY_ID=video-key
S3_VIDEO_SECRET_ACCESS_KEY=video-secret

S3_AUDIO_ENDPOINT_URL=https://storage-provider.com/s3
S3_AUDIO_ACCESS_KEY_ID=audio-key
S3_AUDIO_SECRET_ACCESS_KEY=audio-secret

S3_TTS_ENDPOINT_URL=https://output-provider.com/s3
S3_TTS_ACCESS_KEY_ID=tts-key
S3_TTS_SECRET_ACCESS_KEY=tts-secret

S3_11LAB_ENDPOINT_URL=https://storage-provider.com/s3
S3_11LAB_ACCESS_KEY_ID=11lab-key
S3_11LAB_SECRET_ACCESS_KEY=11lab-secret

# Optional Feature Flags
TTS_USE_FAST_INFERENCE=true
TTS_CACHE_ENABLED=true
TTS_NORMALIZATION_ENABLED=true
TTS_NORMALIZATION_TARGET_LUFS=-16.0
TTS_ALIGNMENT_MODEL=small
TTS_ALIGNMENT_DEVICE=cpu
```

---

## Development & Git Workflow

- Always run `uv run ruff check .` and `uv run ruff format .` before committing.
- Run `uv run pytest tests/unit` to verify changes without requiring external services.
- Keep commits focused on single concerns.
- Use `uv run python worker.py` as the standard local runtime entry point.

---

## Documentation Index

- [`docs/WORKER_SETUP.md`](docs/WORKER_SETUP.md) — Windows / Linux GPU production setup
- [`docs/MACOS_SETUP.md`](docs/MACOS_SETUP.md) — macOS development setup (native TTS)
- [`docs/S3_BUCKET_ARCHITECTURE.md`](docs/S3_BUCKET_ARCHITECTURE.md) — 5-bucket architecture spec
- [`docs/FORCED_ALIGNMENT.md`](docs/FORCED_ALIGNMENT.md) — Forced alignment engine & schema
- [`docs/CACHE_IMPLEMENTATION_SUMMARY.md`](docs/CACHE_IMPLEMENTATION_SUMMARY.md) — DB-backed synthesis cache
- [`docs/MONITORING.md`](docs/MONITORING.md) — Queue depth and DLQ monitoring guide
- [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) — Comprehensive environment variables
