# Installation Guide

**Quick Reference:**
| Platform | Time | Method | GPU Support |
|----------|------|--------|-------------|
| **Windows/Linux** | 10-15 min | Conda + CUDA | ✅ NVIDIA GPU |
| **macOS** | 1-2 min | uv + CPU | ❌ No GPU |

**Recommended approach:**
- **Production GPU inference**: Use Windows/Linux with conda + CUDA
- **Development/Testing**: Use macOS with uv (lightweight, no GPU models)

---

## Windows/Linux Installation (GPU + CUDA)

**Prerequisites:**
- Windows 10/11 or Ubuntu 20.04+
- 16GB+ RAM
- NVIDIA GPU (8GB+ VRAM recommended)

### Quick Setup

```bash
# 1. Install Miniconda (if not installed)
# Download from: https://docs.conda.io/projects/miniconda/en/latest/

# 2. Create environment with Python 3.10
conda create -n indexTTS python=3.10 -y
conda activate indexTTS

# 3. Install ffmpeg
conda install -c conda-forge ffmpeg -y

# 4. Clone repository
git clone https://github.com/0xmichaelran/indexTTS-worker.git
cd indexTTS-worker

# 5. Install IndexTTS with CUDA support
pip install -e ".[cuda,worker]"

# 6. Download models (~2GB, one-time)
mkdir -p checkpoints
huggingface-cli download IndexTeam/IndexTTS-1.5 \
  config.yaml bigvgan_generator.pth bigvgan_discriminator.pth \
  bpe.model dvae.pth gpt.pth unigram_12000.vocab \
  --local-dir checkpoints

# 7. Verify installation
python -c "import torch; print(f'CUDA: {torch.cuda.is_available()}')"
python -c "from indextts.infer import IndexTTS; print('✓ Ready')"
```

**Total time: 10-15 minutes** (mostly downloading models and CUDA libraries)

### Troubleshooting

**CUDA not available:**
```bash
# Install PyTorch with CUDA explicitly
conda install pytorch torchvision torchaudio pytorch-cuda=12.1 -c pytorch -c nvidia
```

**NVIDIA drivers missing (Linux):**
```bash
# Check GPU detection
lspci | grep -i nvidia

# Install drivers
sudo apt-get install -y nvidia-driver-520
nvidia-smi  # Verify
```

**Permission errors (Windows):**
Run Command Prompt as Administrator

---

## macOS Installation (CPU, No GPU)

**Prerequisites:**
- macOS 10.13+
- 8GB+ RAM  
- Python 3.10

**Uses:** Native macOS TTS (lightweight, fast setup) - no PyTorch, no CUDA, no GPU models needed.

### Quick Setup with uv (Recommended)

```bash
# 1. Install uv (fast Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Clone repository
git clone https://github.com/0xmichaelran/indexTTS-worker.git
cd indexTTS-worker

# 3. Create virtual environment with Python 3.10
uv venv --python 3.10
source .venv/bin/activate

# 4. Install IndexTTS with macOS native TTS
uv pip install -e ".[mac,worker]"

# 5. Verify installation
python -c "from indextts.macos_tts import MacOSTTS; print('✓ Ready')"
```

**Total time: 1-2 minutes ⚡**

### Alternative Setup (without uv)

```bash
# 1. Clone repository
git clone https://github.com/0xmichaelran/indexTTS-worker.git
cd indexTTS-worker

# 2. Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install
pip install -e ".[mac,worker]"
```

### Usage

```python
from indextts.macos_tts import MacOSTTS

# Create TTS engine (uses native macOS voices)
tts = MacOSTTS(language="en-US")

# Speak to system audio
tts.infer_to_system_audio("Hello world!")

# Or save to file
tts.infer(None, "Hello world", "output.wav")

# List available system voices
voices = tts.list_voices()
print(voices)
```

### Troubleshooting

**uv not found:**
```bash
# Restart terminal or add to PATH manually
export PATH="$HOME/.cargo/bin:$PATH"
```

**Python 3.10 not available:**
```bash
# Install via Homebrew
brew install python@3.10

# Create venv with specific version
uv venv --python 3.10
```

**pyobjc installation fails:**
```bash
# Install Xcode Command Line Tools
xcode-select --install

# Retry installation
uv pip install -e ".[mac,worker]"
```

---

## Verification Checklist

After installation, verify everything works:

```bash
# 1. Python environment
python --version  # Should be 3.10

# 2. Package imports
python -c "from indextts import create_tts_engine; print('✓ Package OK')"

# 3. Start API server (Ctrl+C to stop)
python run-indextts-1-5.py
# Should show: "Uvicorn running on http://0.0.0.0:8848"

# 4. GPU check (Windows/Linux only)
python -c "import torch; print(f'CUDA: {torch.cuda.is_available()}')"

# 5. Models check (Windows/Linux only)
ls checkpoints/
# Should show: gpt.pth, bigvgan_generator.pth, config.yaml, etc.
```

---

## Next Steps

After successful installation:

1. **Quick Start** → [QUICKSTART.md](./QUICKSTART.md)
2. **API Reference** → [API.md](./API.md)
3. **Configuration** → [CONFIGURATION.md](./CONFIGURATION.md)

---

## Resources

- **uv Project**: https://github.com/astral-sh/uv
- **PyTorch + CUDA**: https://pytorch.org/get-started/locally/
- **Conda Documentation**: https://docs.conda.io/
- **HuggingFace Hub**: https://huggingface.co/IndexTeam/IndexTTS-1.5
