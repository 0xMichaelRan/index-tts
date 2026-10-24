# Configuration Guide

Comprehensive configuration reference for **IndexTTS Worker** (24/7 RabbitMQ worker for TTS synthesis and Vox video rendering).

---

## Overview

All runtime configuration is managed through environment variables loaded from a `.env` file in the project root (see [`.env.example`](../.env.example)), parsed into typed settings by [`WorkerConfig`](file:///Users/aa/git/github_uncgra/indexTTS-worker/services/common/worker_config.py).

```bash
cp .env.example .env
# Edit .env with your environment credentials
```

---

## Required Settings

### 1. Database & Messaging

```bash
# Database (PostgreSQL - required for synthesis cache and job tracking)
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/indextts

# RabbitMQ (AMQP connection URL - required)
RABBITMQ_URL=amqp://guest:guest@localhost:5672/
```

### 2. S3 5-Bucket Architecture Credentials

Bucket names and regions are defined in [`config/buckets.toml`](file:///Users/aa/git/github_uncgra/indexTTS-worker/config/buckets.toml). Credentials and endpoints are provided via environment variables:

| Bucket Type | Purpose | Access Pattern | Env Var Prefix |
|---|---|---|---|
| `misc` | General assets, temporary uploads | Read / Write | `S3_MISC_*` |
| `video` | Video clips and rendered MP4 outputs | Read / Write | `S3_VIDEO_*` |
| `audio` | User voice recordings for TTS voice cloning | Read-only | `S3_AUDIO_*` |
| `tts` | Synthesized audio & alignment JSON (worker is sole writer) | Write-only | `S3_TTS_*` |
| `11lab` | User-uploaded ElevenLabs audio exports | Read-only | `S3_11LAB_*` |

Example `.env` block:
```bash
S3_MISC_ENDPOINT_URL=https://project.storage.example.com/s3
S3_MISC_ACCESS_KEY_ID=your-misc-key
S3_MISC_SECRET_ACCESS_KEY=your-misc-secret

S3_VIDEO_ENDPOINT_URL=https://project.storage.example.com/s3
S3_VIDEO_ACCESS_KEY_ID=your-video-key
S3_VIDEO_SECRET_ACCESS_KEY=your-video-secret

S3_AUDIO_ENDPOINT_URL=https://project.storage.example.com/s3
S3_AUDIO_ACCESS_KEY_ID=your-audio-key
S3_AUDIO_SECRET_ACCESS_KEY=your-audio-secret

S3_TTS_ENDPOINT_URL=https://project.storage.example.com/s3
S3_TTS_ACCESS_KEY_ID=your-tts-key
S3_TTS_SECRET_ACCESS_KEY=your-tts-secret

S3_11LAB_ENDPOINT_URL=https://project.storage.example.com/s3
S3_11LAB_ACCESS_KEY_ID=your-11lab-key
S3_11LAB_SECRET_ACCESS_KEY=your-11lab-secret
```

---

## Optional Worker Settings

### 1. Logging

| Variable | Default | Description |
|---|---|---|
| `LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `LOG_FILE_ENABLED` | `false` | When `true`, also writes structured logs to disk |
| `LOG_FILE_PATH` | `logs/worker.log` | Path to log file when file logging is active |

### 2. Synthesis Caching

Database-backed synthesis caching avoids re-synthesizing audio for repeated `(text, audio_prompt)` pairs:

| Variable | Default | Description |
|---|---|---|
| `TTS_CACHE_ENABLED` | `true` | Toggle synthesis cache |
| `TTS_CACHE_MAX_ENTRIES` | `10000` | Maximum number of cached records |
| `TTS_CACHE_EVICTION_THRESHOLD` | `9000` | Cache size at which LRU eviction triggers |
| `TTS_CACHE_MAX_SIZE_MB` | `5000` | Disk limit in MB for cached WAV files (0 = unlimited) |
| `TTS_CACHE_LOCAL_DIR` | `outputs/tts_cache` | Local directory for cached audio files |

### 3. TTS Inference & Normalization

| Variable | Default | Description |
|---|---|---|
| `TTS_USE_FAST_INFERENCE` | `true` | On Windows/Linux GPU: `true` uses batched `infer_fast()`; `false` uses sequential `infer()`. Ignored on macOS. |
| `TTS_NORMALIZATION_ENABLED` | `true` | Apply ITU-R BS.1770-4 LUFS loudness normalization |
| `TTS_NORMALIZATION_TARGET_LUFS` | `-16.0` | Target loudness in LUFS (-60.0 to 0.0) |
| `TTS_OUTPUT_DIR` | `outputs/tts_output` | Directory for raw synthesis outputs |

### 4. Mandatory Forced Alignment (stable-whisper)

Every TTS job produces word-level timestamps via `stable-whisper`:

| Variable | Default | Description |
|---|---|---|
| `TTS_ALIGNMENT_MODEL` | `small` | Whisper model name (`tiny`, `base`, `small`, `medium`) |
| `TTS_ALIGNMENT_DEVICE` | `cpu` | Device for Whisper (must be `cpu`; do not set `mps` on macOS) |
| `TTS_ALIGNMENT_MAX_RETRIES` | `2` | Retry attempts on transient alignment failure |
| `TTS_ALIGNMENT_RETENTION_DAYS` | `16` | Days to retain raw alignment JSON and `.srt` debug files locally |

### 5. Circuit Breakers

Cascading failure protection across external systems:

| Variable | Default | Description |
|---|---|---|
| `CIRCUIT_BREAKER_S3_FAILURE_THRESHOLD` | `5` | Consecutive failures before S3 breaker opens |
| `CIRCUIT_BREAKER_S3_RESET_TIMEOUT` | `60` | Cooldown seconds before half-open probe |
| `CIRCUIT_BREAKER_TTS_FAILURE_THRESHOLD` | `3` | Consecutive failures before TTS breaker opens |
| `CIRCUIT_BREAKER_TTS_RESET_TIMEOUT` | `30` | Cooldown seconds before TTS probe |
| `CIRCUIT_BREAKER_ALIGNMENT_FAILURE_THRESHOLD` | `3` | Consecutive failures before alignment breaker opens |
| `CIRCUIT_BREAKER_ALIGNMENT_RESET_TIMEOUT` | `60` | Cooldown seconds before alignment probe |

### 6. Vox Video Render Consumer

Runs on Windows/Linux in a daemon thread consuming `vox_jobs` and publishing to `vox_results`:

| Variable | Default | Description |
|---|---|---|
| `VOX_RENDER_ENABLED` | `true` | Set `false` to disable the vox consumer on this worker |
| `VOX_RENDER_FFMPEG_PATH` | `ffmpeg` | Path to FFmpeg binary |

---

## Model & Checkpoint Configuration

Model weights and architecture parameters reside in `checkpoints/config.yaml`:

```yaml
gpt_model_dim: 768
gpt_num_heads: 12
gpt_num_layers: 12
gpt_context_window: 4096

vocoder: bigvgan2
vocoder_dim: 512

sample_rate: 24000
n_fft: 1024
n_mel_channels: 128
win_length: 1024
hop_length: 300
```

### Inference Generation Parameters

When running through the Python API:

```python
from indextts.infer import IndexTTS

tts = IndexTTS(model_dir="checkpoints", cfg_path="checkpoints/config.yaml")

# Standard inference
tts.infer(
    audio_prompt="reference.wav",
    text="Text to synthesize",
    output_path="output.wav",
    temperature=1.0,
    top_p=0.8,
    top_k=30,
    repetition_penalty=10.0,
)

# Batched fast inference
tts.infer_fast(
    audio_prompt="reference.wav",
    text="Long multi-sentence text...",
    output_path="output.wav",
    sentences_bucket_max_size=4,
)
```

---

## Troubleshooting

### Issue: Out of GPU Memory (CUDA OOM)
- Switch to sequential inference: `TTS_USE_FAST_INFERENCE=false` in `.env`
- Ensure no competing GPU processes are running
- Reduce `sentences_bucket_max_size` in code if invoking `infer_fast()` directly

### Issue: S3 Connection Failure
- Verify all `S3_<TYPE>_*` environment variables in `.env`
- Run integration tests to test each bucket: `uv run pytest tests/integration/test_s3_integration.py`
- Check circuit breaker status via `uv run python monitor.py --once`
