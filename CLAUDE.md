# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Environment & Commands

- **Python Version**: `3.10.*` (pinned in `.python-version` and `pyproject.toml`)
- **Package Manager**:
  - **macOS / Linux**: `uv` (do not run bare `python` or `pip`)
  - **Windows**: `conda` (activate `index-tts` environment; do NOT use `uv` on Windows)

### Dependency Management

```bash
uv sync                       # Sync dependencies from pyproject.toml / uv.lock
uv pip install -e ".[mac]"     # Install macOS native TTS extras (AVFoundation/Cocoa)
uv pip install -e ".[dev]"     # Install development tools
```

### Running Services

```bash
uv run python worker.py       # Start main 24/7 IndexTTS & Vox worker
uv run python monitor.py      # Monitor queue lengths and circuit breakers
uv run python webui.py        # Run Gradio Web UI on port 7860
```

### Testing

Tests are divided into hermetic unit tests (`tests/unit`) and live-service integration tests (`tests/integration`):

```bash
uv run pytest tests/unit                              # Run fast unit tests (mocked, ~3s)
uv run pytest tests/unit/test_circuit_breaker.py -v   # Run a single test file
uv run pytest tests/unit/test_circuit_breaker.py::TestCircuitBreakerBasics::test_initial_state_is_closed -v  # Single test case
uv run pytest tests/integration                       # Run integration tests (requires live PostgreSQL, RabbitMQ, S3)
uv run pytest --cov=services                          # Run with code coverage
```

*Windows alternative:* `python -m pytest tests/unit/<test_file>.py`

### Linting & Formatting

The project enforces Ruff with line-length rules ignored for long strings/Chinese text (`E501`).

```bash
uv run ruff check .          # Lint check
uv run ruff check --fix .    # Lint auto-fix
uv run ruff format .         # Format code
uv run ruff format --check . # Format verification
```

### RabbitMQ Queue Setup

```bash
uv run python -m services.messaging.rabbitmq_config   # Idempotently declare DLX exchanges, DLQs, and main queues
```

---

## High-Level Architecture

IndexTTS Worker is a background processing service consuming jobs from RabbitMQ, synthesizing audio via the IndexTTS engine (or macOS native speech synthesis in dev), normalizing audio, performing forced alignment for video subtitle syncing, and persisting outputs to S3-compatible storage.

### 1. Dual-Consumer Process Model

- **TTS Worker (`services/tts/tts_worker.py`)**: The primary consumer running on the main thread. Consumes `tts_jobs` (`prefetch_count=1`, priority 0–10) and publishes results to `tts_results`.
- **Vox Render Consumer (`services/vox/vox_render_consumer.py`)**: A background daemon thread spawned on Linux/Windows. Consumes `vox_jobs` and renders multi-clip videos matched to TTS narration timings using FFmpeg, publishing to `vox_results`. (Skipped on macOS).

### 2. TTS Job Processing Pipeline (`services/tts/synthesis_pipeline.py`)

Every TTS job moves through the following stages:

1. **Text Sanitization (`services/tts/text_sanitizer.py`)**: Strips unsupported characters, expands numbers/dates via language scripts.
2. **Synthesis Cache Lookup (`services/tts/cache_manager.py`, `app/cache_service.py`)**:
   - Cache key: `SHA256(text + audio_prompt_path)`.
   - On hit: Skips synthesis, applies time-stretching (`soundfile` / `sox`) if `speedRatio != 1.0`.
   - On miss: Synthesizes base audio at `speedRatio=1.0`, stores audio in local cache and metadata in PostgreSQL, then stretches if needed.
3. **Voice Mel Caching**: Mel-spectrograms (`cond_mel`) are cached in memory keyed by the audio prompt S3 path (`services/tts/audio_processor.py`), saving 2–5s when the same voice prompt is reused.
4. **Loudness Normalization (`indextts/utils/audio_normalization.py`)**: Normalizes audio using ITU-R BS.1770-4 LUFS (default `-16.0 LUFS`) with peak limiting.
5. **Mandatory Forced Alignment (`services/tts/alignment.py`)**:
   - Runs `stable-whisper` on CPU (`TTS_ALIGNMENT_DEVICE=cpu`) to generate word-level timestamp JSON (`{job_id}_alignment.json`).
   - Alignment is mandatory; failure marks the job as failed.
6. **Idempotent S3 Upload (`services/storage/idempotent_upload.py`)**:
   - Uploads audio (`.wav`/`.mp3`) and alignment JSON (`.json`) to the `tts` bucket.
   - Computes SHA256 integrity hashes to avoid redundant re-uploads on job retries.

### 3. S3 Bucket Registry Architecture

Bucket declarations are defined structurally in `config/buckets.toml`, while credentials and endpoints are resolved per-type via environment variables (`services/storage/s3_registry.py` and `services/storage/s3_config.py`):

| Bucket Name | Type | Access Pattern | Purpose |
|---|---|---|---|
| `klatu-misc` | `misc` | Read / Write | Temporary uploads and miscellaneous assets (`S3_MISC_*`) |
| `klatu-video` | `video` | Read / Write | Video clips and final rendered outputs (`S3_VIDEO_*`) |
| `klatu-audio` | `audio` | Read-only | Voice recording prompts for TTS cloning (`S3_AUDIO_*`) |
| `klatu-tts` | `tts` | Write-only | Synthesized audio & alignment JSON; worker is sole writer (`S3_TTS_*`) |
| `klatu-11lab` | `11lab` | Read-only | User ElevenLabs audio exports (`S3_11LAB_*`) |

Output S3 key structure: `{jobType}/{YYYYMMDD}/{jobId}/{filename}.{ext}`

### 4. Messaging & Wire Protocol Rules

- **Wire Protocol (RabbitMQ JSON payloads)**: Must strictly use **`camelCase`** (`jobId`, `jobType`, `audioPromptPath`, `speedRatio`, `spokenLang`, `alignmentPath`, etc.).
  - **Crucial**: Do NOT use snake_case fallbacks in message parsing (e.g., avoid `data.get("jobId") or data.get("job_id")`).
- **Internal Python Code**: Must strictly use standard **`snake_case`** (PEP 8) for variables, DB models, alignment file fields, and internal methods.
- **Dead-Letter Exchanges (DLX)**:
  - Input: `tts_jobs` → DLX: `tts_jobs.dlx` → DLQ: `tts_jobs_failed`
  - Output: `tts_results` → DLX: `tts_results.dlx` → DLQ: `tts_results_failed`
  - Vox: `vox_jobs` → `vox_jobs.dlx` → `vox_jobs_failed`; `vox_results` → `vox_results.dlx` → `vox_results_failed`
- **Circuit Breakers (`services/common/circuit_breaker.py`)**: Protect S3 interactions and TTS inference against downstream failure cascades.
