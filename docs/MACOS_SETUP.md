# macOS Setup Guide

IndexTTS Worker runs on macOS using native AVFoundation speech synthesis (`NSSpeechSynthesizer` / `AVSpeechSynthesizer`). No NVIDIA GPU or CUDA dependencies are required, making macOS ideal for local development and integration testing.

---

## Prerequisites

- **macOS**: 10.15+ (Intel or Apple Silicon)
- **Python**: 3.10 (managed automatically by `uv`)
- **Package Manager**: [`uv`](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- **Optional Tools**: `ffmpeg` (`brew install ffmpeg`)

---

## Quick Start (3 minutes)

```bash
# 1. Clone repository
git clone https://github.com/index-tts/indexTTS-worker.git
cd indexTTS-worker

# 2. Sync dependencies with macOS extras (PyObjC AVFoundation & Cocoa)
uv sync --extra mac

# 3. Install package in editable mode
uv pip install -e .

# 4. Configure environment
cp .env.example .env
# Edit .env with your RabbitMQ and S3 credentials

# 5. Run the worker
uv run python worker.py
```

---

## macOS Runtime Characteristics

1. **Native Speech Synthesis**:
   - Audio is generated using macOS built-in voices instead of the IndexTTS BigVGAN GPU model.
   - Low CPU and RAM footprint (~500MB vs 8GB+ GPU VRAM on Linux/Windows).
   - Audio output format: 24 kHz WAV.
   - Speed adjustments via `speedRatio` payload parameter work identically to production.

2. **Vox Video Rendering**:
   - The multi-clip video render consumer (`vox_jobs`) is disabled on macOS by default to avoid heavy local video processing.

3. **Forced Alignment**:
   - Whisper runs on CPU (`TTS_ALIGNMENT_DEVICE=cpu`). Do not set to `mps` as stable-whisper alignment has stability issues with Apple Silicon MPS.

---

## Common Commands

For day-to-day commands, refer to [`AGENTS.md`](../AGENTS.md):

```bash
# Run services
uv run python worker.py       # Main worker
uv run python monitor.py      # Queue and DLQ health monitor
uv run python cache.py stats  # Cache inspect

# Quality & Testing
uv run pytest tests/unit      # Fast unit tests (~3s)
uv run ruff check .           # Linting
uv run ruff format .          # Code formatting
```

---

## macOS Troubleshooting

### `ModuleNotFoundError: No module named 'AppKit'` or `PyObjC`
Reinstall the macOS extra dependencies:
```bash
uv sync --extra mac --force-reinstall
```
Verify PyObjC is available:
```bash
uv run python -c "import AVFoundation, Cocoa; print('PyObjC loaded successfully')"
```

### `ffmpeg` not found
If audio conversion or forced alignment fails to locate FFmpeg:
```bash
brew install ffmpeg
```

---

## Related Documentation

- [`AGENTS.md`](../AGENTS.md) — Comprehensive developer & agent guide, commands, and wire protocol
- [`docs/WORKER_SETUP.md`](WORKER_SETUP.md) — Windows / Linux GPU production setup guide
- [`docs/CONFIGURATION.md`](CONFIGURATION.md) — Full `.env` variable reference
- [`docs/CACHE_IMPLEMENTATION_SUMMARY.md`](CACHE_IMPLEMENTATION_SUMMARY.md) — Optional PostgreSQL synthesis cache setup
