# Quick Start Guide

Get IndexTTS running in under 15 minutes.

---

## Installation

### macOS (1-2 minutes, CPU only)

```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone and setup
git clone https://github.com/0xmichaelran/indexTTS-worker.git
cd indexTTS-worker
uv venv --python 3.10
source .venv/bin/activate
uv pip install -e ".[mac,worker]"
```

### Windows/Linux (10-15 minutes, GPU with CUDA)

```bash
# Create conda environment
conda create -n indexTTS python=3.10 -y
conda activate indexTTS
conda install -c conda-forge ffmpeg -y

# Clone and setup
git clone https://github.com/0xmichaelran/indexTTS-worker.git
cd indexTTS-worker
pip install -e ".[cuda,worker]"

# Download models (~2GB, one-time)
mkdir -p checkpoints
huggingface-cli download IndexTeam/IndexTTS-1.5 \
  config.yaml bigvgan_generator.pth bpe.model gpt.pth \
  dvae.pth bigvgan_discriminator.pth unigram_12000.vocab \
  --local-dir checkpoints
```

**➜ Full details**: See [Installation Guide](./INSTALLATION.md)

---

## Start the API Server

```bash
python run-indextts-1-5.py
```

Server runs at: `http://localhost:8848`

**Interactive docs**: http://localhost:8848/docs

---

## Basic Usage

### macOS (Native TTS)

```python
from indextts.macos_tts import MacOSTTS

tts = MacOSTTS(language="en-US")

# Speak to system audio
tts.infer_to_system_audio("Hello world!")

# Save to file
tts.infer(None, "Hello world", "output.wav")

# List available voices
print(tts.list_voices())
```

### Windows/Linux (GPU Inference)

```python
from indextts.infer import IndexTTS

tts = IndexTTS(
    model_dir="checkpoints",
    cfg_path="checkpoints/config.yaml",
    device="cuda:0"  # or "cpu"
)

# Synthesize with reference voice
tts.infer(
    audio_prompt="reference.wav",
    text="Hello, this is a test",
    output_path="output.wav"
)
```

### REST API

```bash
# Synthesize (with GPU models)
curl -X POST "http://localhost:8848/infer/" \
  -F "audio_prompt=@reference_voice.wav" \
  -F "text=Hello, this is a test" \
  --output output.wav

# Health check
curl http://localhost:8848/health
```

### Command Line

```bash
# GPU inference (Windows/Linux)
indextts "Hello world" \
  --voice reference.wav \
  --output output.wav

# macOS native TTS
indextts "Hello world" --output output.wav
```

---

## Advanced Examples

### Multi-Language Support (GPU)

```python
from indextts.infer import IndexTTS

tts = IndexTTS(model_dir="checkpoints", cfg_path="checkpoints/config.yaml")

# Chinese
tts.infer("reference.wav", "你好，欢迎使用", "output_zh.wav")

# English  
tts.infer("reference.wav", "Hello, welcome", "output_en.wav")

# Mixed
tts.infer("reference.wav", "Hello 你好", "output_mixed.wav")
```

### Batch Processing (GPU)

```python
from indextts.infer import IndexTTS

tts = IndexTTS(model_dir="checkpoints", cfg_path="checkpoints/config.yaml")

texts = [
    "First sentence.",
    "Second sentence.",
    "Third sentence."
]

for i, text in enumerate(texts):
    tts.infer("reference.wav", text, f"output_{i}.wav")
    print(f"✓ {i+1}/{len(texts)}")
```

### Fast Batch Mode (2-10x speedup)

```python
from indextts.infer import IndexTTS

tts = IndexTTS(model_dir="checkpoints", cfg_path="checkpoints/config.yaml")

long_text = """
Multiple sentences here.
Fast mode processes them efficiently.
Great for long documents.
"""

tts.infer_fast(
    audio_prompt="reference.wav",
    text=long_text,
    output_path="output_fast.wav"
)
```

---

## Troubleshooting

### Module Not Found
```bash
# Activate environment
source .venv/bin/activate  # macOS with uv
conda activate indexTTS     # Windows/Linux

# Reinstall
uv pip install -e ".[mac,worker]"     # macOS
pip install -e ".[cuda,worker]"       # Windows/Linux
```

### Port Already in Use
```bash
# Check what's using port 8848
lsof -i :8848              # macOS/Linux
netstat -ano | findstr :8848  # Windows

# Use different port
python run-indextts-1-5.py --port 9999
```

### CUDA Out of Memory (Windows/Linux)
```python
# Use CPU instead
from indextts.infer import IndexTTS
tts = IndexTTS(model_dir="checkpoints", device="cpu")
```

### Models Not Found (Windows/Linux)
```bash
# Download models first
mkdir -p checkpoints
huggingface-cli download IndexTeam/IndexTTS-1.5 \
  config.yaml bigvgan_generator.pth bpe.model gpt.pth \
  dvae.pth bigvgan_discriminator.pth unigram_12000.vocab \
  --local-dir checkpoints
```

---

## Next Steps

- **API Reference** → [API.md](./API.md)
- **Configuration** → [CONFIGURATION.md](./CONFIGURATION.md)
- **FAQ** → [FAQ.md](./FAQ.md)

---

## Getting Help

- **Questions**: Check [FAQ.md](./FAQ.md)
- **Bugs**: [GitHub Issues](https://github.com/0xmichaelran/indexTTS-worker/issues)
- **Discussions**: [GitHub Discussions](https://github.com/0xmichaelran/indexTTS-worker/discussions)
