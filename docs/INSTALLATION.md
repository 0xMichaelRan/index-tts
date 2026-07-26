# Installation Guide

This guide covers platform-specific setup instructions for Windows, Linux, and macOS.

**Quick Reference:**
| Platform | Time | Method | GPU Required |
|----------|------|--------|--------------|
| **Windows** | 10-25 min | Conda + pip | ✅ NVIDIA |
| **Linux** | 10-25 min | Conda + pip | ✅ NVIDIA |
| **macOS** | 30s-2min | venv + pip | ❌ Optional* |

*macOS uses native TTS by default. Use Windows/Linux for production GPU inference.

---

## Windows Installation

### Prerequisites
- Windows 10/11
- 16GB+ RAM
- NVIDIA GPU with CUDA support (8GB+ VRAM recommended)
- Internet connection for downloading models

### Step-by-Step Setup

#### 1. Install Conda
Download and install Miniconda from https://docs.conda.io/projects/miniconda/en/latest/

```bash
# Verify installation
conda --version
```

#### 2. Create Python Environment
```bash
# Create environment with Python 3.10 or 3.11
conda create -n indexTTS python=3.10
conda activate indexTTS
```

#### 3. Install System Dependencies
```bash
# Install ffmpeg
conda install -c conda-forge ffmpeg

# Verify ffmpeg installation
ffmpeg -version
```

#### 4. Clone Repository
```bash
git clone https://github.com/0xmichaelran/indexTTS-worker.git
cd indexTTS-worker
```

#### 5. Install IndexTTS Package

**Option A: Full Installation (with GPU inference)**
```bash
pip install -e ".[cuda,worker]"
```

**Option B: API Only (without GPU models)**
```bash
pip install -e ".[worker]"
```

**Option C: Everything (including dev tools)**
```bash
pip install -e ".[cuda,worker,webui,dev]"
```

This will install:
- PyTorch with CUDA support
- TensorFlow dependencies
- FastAPI for REST API
- Development tools (pytest, black, ruff, mypy)

#### 6. Download Models
```bash
# Create checkpoints directory
mkdir -p checkpoints

# Option A: Using huggingface-cli (recommended)
huggingface-cli download IndexTeam/IndexTTS-1.5 \
  config.yaml bigvgan_generator.pth bigvgan_discriminator.pth \
  bpe.model dvae.pth gpt.pth unigram_12000.vocab \
  --local-dir checkpoints

# Option B: Using wget (if CLI fails)
cd checkpoints
wget https://huggingface.co/IndexTeam/IndexTTS-1.5/resolve/main/config.yaml
wget https://huggingface.co/IndexTeam/IndexTTS-1.5/resolve/main/bigvgan_generator.pth
wget https://huggingface.co/IndexTeam/IndexTTS-1.5/resolve/main/bigvgan_discriminator.pth
wget https://huggingface.co/IndexTeam/IndexTTS-1.5/resolve/main/bpe.model
wget https://huggingface.co/IndexTeam/IndexTTS-1.5/resolve/main/dvae.pth
wget https://huggingface.co/IndexTeam/IndexTTS-1.5/resolve/main/gpt.pth
wget https://huggingface.co/IndexTeam/IndexTTS-1.5/resolve/main/unigram_12000.vocab
cd ..
```

#### 7. Verify Installation
```bash
# Test Python library
python -c "from indextts.infer import IndexTTS; print('✓ IndexTTS loaded')"

# Test CLI
indextts --help

# Test API server (Ctrl+C to stop)
python run-indextts-1-5.py
# Should show: "Uvicorn running on http://0.0.0.0:8848"
```

#### 8. (Optional) Test with Sample Audio
```bash
# Create test data directory
mkdir -p test_data

# Download a sample audio file (or use your own)
# Place your audio file as: test_data/input.wav

# Run inference
indextts "Hello, this is a test." --voice test_data/input.wav --output test_output.wav
```

### Troubleshooting Windows Installation

#### CUDA Toolkit Not Found
```bash
# Install CUDA directly from conda
conda install pytorch::pytorch torchvision torchaudio pytorch-cuda=12.1 -c pytorch -c nvidia

# Then retry package installation
pip install -e ".[cuda,worker]"
```

#### PyTorch Installation Fails
```bash
# Install specific PyTorch version for your CUDA
pip install torch==2.1.2 torchaudio --index-url https://download.pytorch.org/whl/cu121
```

#### Permission Denied Errors
```bash
# Run Command Prompt as Administrator, then retry installation
```

#### Long Installation Time
- Normal: 10-25 minutes (mostly downloading CUDA libraries ~2GB)
- If stuck: Check internet connection, try again
- Use `pip install --upgrade pip` to ensure latest pip version

### Environment Variables (Windows)

Create a `.env` file in the project root:
```bash
# Model Configuration
MODEL_DIR=checkpoints
CONFIG_PATH=checkpoints/config.yaml
IS_FP16=true

# Device Configuration
DEVICE=cuda:0

# FastAPI Service
HOST=0.0.0.0
PORT=8848
WORKERS=4
```

---

## Linux Installation

### Prerequisites
- Ubuntu 20.04 LTS or later (or compatible Linux distro)
- 16GB+ RAM
- NVIDIA GPU with CUDA Compute Capability 3.5+ (8GB+ VRAM recommended)
- Internet connection for downloading models

### Step-by-Step Setup

#### 1. Update System
```bash
sudo apt-get update
sudo apt-get upgrade -y
```

#### 2. Install System Dependencies
```bash
sudo apt-get install -y \
  python3.10 \
  python3-pip \
  python3-venv \
  conda \
  ffmpeg \
  libsndfile1 \
  git
```

#### 3. Create Python Environment
```bash
# Create conda environment
conda create -n indexTTS python=3.10
conda activate indexTTS

# OR use venv if conda not available
python3 -m venv indexTTS-env
source indexTTS-env/bin/activate
```

#### 4. Clone Repository
```bash
git clone https://github.com/0xmichaelran/indexTTS-worker.git
cd indexTTS-worker
```

#### 5. Install IndexTTS Package
```bash
# Full installation with CUDA support
pip install -e ".[cuda,worker]"

# Verify PyTorch CUDA
python -c "import torch; print(f'CUDA Available: {torch.cuda.is_available()}')"
```

#### 6. Download Models
```bash
mkdir -p checkpoints

huggingface-cli download IndexTeam/IndexTTS-1.5 \
  config.yaml bigvgan_generator.pth bigvgan_discriminator.pth \
  bpe.model dvae.pth gpt.pth unigram_12000.vocab \
  --local-dir checkpoints
```

#### 7. Verify Installation
```bash
# Test CUDA
python -c "import torch; print(f'GPU: {torch.cuda.get_device_name()}')"

# Test IndexTTS
python -c "from indextts.infer import IndexTTS; print('✓ IndexTTS loaded')"

# Test API
python run-indextts-1-5.py  # Ctrl+C to stop
```

### Troubleshooting Linux Installation

#### NVIDIA Drivers Not Installed
```bash
# Check if GPU is detected
lspci | grep -i nvidia

# Install NVIDIA drivers
sudo apt-get install -y nvidia-driver-520  # Use latest available

# Verify
nvidia-smi
```

#### CUDA Not Available
```bash
# Install CUDA Toolkit
cuda_version=12.1
sudo apt-get install -y \
  cuda-toolkit-${cuda_version} \
  libcudnn8 \
  libcudnn8-dev
```

#### Permission Issues
```bash
# Add current user to docker/gpu groups
sudo usermod -a -G docker $USER
sudo usermod -a -G video $USER

# Logout and login for changes to take effect
```

---

## macOS Installation

### Prerequisites
- macOS 10.13 or later
- 8GB+ RAM
- M1/M2/M3 (Apple Silicon) or Intel processor
- Python 3.10 or 3.11 installed

### Step-by-Step Setup

#### Option A: Lightweight Native TTS (Recommended for Development)

This setup uses macOS native AVFoundation for TTS - no GPU or PyTorch required.

##### 1. Create Virtual Environment
```bash
# Using Python venv (recommended)
python3 -m venv indexTTS-env
source indexTTS-env/bin/activate

# OR using uv (faster)
pip install uv
uv venv indexTTS-env
source indexTTS-env/bin/activate
```

##### 2. Clone Repository
```bash
git clone https://github.com/0xmichaelran/indexTTS-worker.git
cd indexTTS-worker
```

##### 3. Install Package with macOS Native TTS
```bash
# Install with native TTS support
pip install -e ".[mac,worker]"

# OR use uv for faster installation
uv pip install -e ".[mac,worker]"
```

##### 4. Verify Installation
```bash
# Test import
python -c "from indextts.macos_tts import MacOSTTS; print('✓ macOS TTS loaded')"

# Test CLI
indextts --help

# Test API
python run-indextts-1-5.py
# Runs on http://localhost:8848
```

##### 5. Use macOS TTS

```python
from indextts import create_tts_engine

# Auto-detects macOS and uses native TTS
tts = create_tts_engine()

# Option 1: Speak to system audio
tts.infer_to_system_audio("Hello world!", rate=0.5, pitch=1.0)

# Option 2: Export to file (uses system audio)
tts.infer("reference.wav", "Hello world", "output.wav")

# List available system voices
voices = tts.list_voices()
print(voices)
```

**Installation time: 30 seconds - 2 minutes ⚡**

#### Option B: Full GPU Inference (For Production)

If you want to use the full IndexTTS model with GPU acceleration on macOS, you'll need to set up on a machine with NVIDIA GPU (Linux/Windows) or use Apple Silicon with MPS.

##### 1. Install Homebrew (if not installed)
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

##### 2. Install Dependencies
```bash
# Python 3.10/3.11
brew install python@3.11

# FFmpeg
brew install ffmpeg

# Create environment
python3.11 -m venv indexTTS-env
source indexTTS-env/bin/activate
```

##### 3. Install PyTorch for Apple Silicon/MPS
```bash
# For Apple Silicon (M1/M2/M3)
pip install torch torchvision torchaudio

# Verify MPS support
python -c "import torch; print(f'MPS available: {torch.backends.mps.is_available()}')"
```

##### 4. Clone & Install
```bash
git clone https://github.com/0xmichaelran/indexTTS-worker.git
cd indexTTS-worker
pip install -e ".[cuda,worker]"  # Note: will use MPS on Apple Silicon
```

##### 5. Download Models
```bash
mkdir -p checkpoints

huggingface-cli download IndexTeam/IndexTTS-1.5 \
  config.yaml bigvgan_generator.pth bigvgan_discriminator.pth \
  bpe.model dvae.pth gpt.pth unigram_12000.vocab \
  --local-dir checkpoints
```

##### 6. Test
```bash
python -c "import torch; print(f'Device: {torch.device(\"mps\" if torch.backends.mps.is_available() else \"cpu\")}')"
python run-indextts-1-5.py
```

**Installation time: 5-15 minutes** (depends on PyTorch download speed)

### Troubleshooting macOS Installation

#### Python Version Issues
```bash
# Check Python version
python3 --version

# Use specific version
python3.11 -m venv indexTTS-env

# Or install via Homebrew
brew install python@3.11
```

#### pyobjc Installation Fails
```bash
# Make sure Xcode Command Line Tools are installed
xcode-select --install

# Then retry
pip install pyobjc-framework-AVFoundation pyobjc-framework-Cocoa
```

#### Native Audio Not Working
```bash
# Check system audio permissions
# System Settings → Privacy & Security → Microphone/Speakers

# Test with explicit speaker output
from indextts.macos_tts import MacOSTTS
tts = MacOSTTS()
tts.infer_to_system_audio("Testing")
```

#### Memory Issues (Apple Silicon)
```bash
# Use CPU instead of MPS for memory-constrained operations
import torch
device = torch.device("cpu")

from indextts.infer import IndexTTS
tts = IndexTTS(model_dir="checkpoints", device="cpu")
```

#### Permission Denied on pip
```bash
# Use --user flag or virtual environment
pip install --user -e ".[mac,worker]"

# Better: use virtual environment (recommended)
python3 -m venv indexTTS-env
source indexTTS-env/bin/activate
pip install -e ".[mac,worker]"
```

### Environment Variables (macOS)

Create a `.env` file in the project root:
```bash
# For native macOS TTS
TTS_ENGINE=macos
LANGUAGE=en-US

# For full GPU inference (Apple Silicon)
DEVICE=mps  # or cpu, or cuda if running on Linux

# FastAPI Service
HOST=127.0.0.1
PORT=8848
```

---

## Verification Checklist

After installation, verify everything is working:

```bash
# 1. Python environment
python --version  # Should be 3.10 or 3.11

# 2. Package imports
python -c "from indextts import create_tts_engine; print('✓ Package OK')"

# 3. CLI tool
indextts --help

# 4. API server (Ctrl+C to stop)
python run-indextts-1-5.py
# Should show: "Uvicorn running on http://0.0.0.0:8848"

# 5. (GPU only) CUDA/MPS availability
python -c "import torch; print(f'GPU: {torch.cuda.is_available() or torch.backends.mps.is_available()}')"

# 6. (GPU only) Download models
# Models should be in ./checkpoints/ directory
ls checkpoints/
# Should show: gpt.pth, bigvgan_generator.pth, config.yaml, etc.
```

---

## Next Steps

After successful installation:

1. **Read Quick Start Guide** → [QUICKSTART.md](./QUICKSTART.md)
2. **Learn the API** → [API.md](./API.md)
3. **Configure Environment** → [CONFIGURATION.md](./CONFIGURATION.md)
4. **Deploy to Production** → [DEPLOYMENT.md](./DEPLOYMENT.md)

---

## Need Help?

- **Installation Issues** → See platform-specific troubleshooting sections above
- **API Questions** → Check [API.md](./API.md)
- **Performance** → Read [PERFORMANCE.md](./PERFORMANCE.md)
- **Reporting Bugs** → Open an issue on [GitHub](https://github.com/0xmichaelran/indexTTS-worker/issues)

---

## Resources

- **PyTorch Installation**: https://pytorch.org/get-started/locally/
- **Conda Documentation**: https://docs.conda.io/
- **uv Project**: https://github.com/astral-sh/uv
- **HuggingFace Hub**: https://huggingface.co/
- **Original IndexTTS**: https://github.com/index-tts/index-tts
