# IndexTTS Worker Setup Guide

Complete setup guide for the 24/7 TTS worker service. Choose your platform below.

---

## Table of Contents

- [Windows/Linux Setup (GPU + CUDA)](#windowslinux-setup-gpu--cuda)
- [macOS Setup (CPU, Mock TTS)](#macos-setup-cpu-mock-tts)
- [Configuration](#configuration)
- [Running the Worker](#running-the-worker)
- [Verification](#verification)
- [Troubleshooting](#troubleshooting)

---

## Windows/Linux Setup (GPU + CUDA)

For **production TTS synthesis** with GPU acceleration. The worker uses IndexTTS-1.5 models for real audio generation.

### Prerequisites

- Windows 10/11 or Ubuntu 20.04+
- 16GB+ RAM
- NVIDIA GPU (8GB+ VRAM recommended)
- Python 3.10

### Step-by-Step Installation

#### 1. Install Miniconda

Download and install from: https://docs.conda.io/projects/miniconda/en/latest/

#### 2. Create Conda Environment

```bash
# Create environment with Python 3.10
conda create -n tts_worker python=3.10 -y
conda activate tts_worker
```

#### 3. Install System Dependencies

```bash
# Install ffmpeg
conda install -c conda-forge ffmpeg -y
```

#### 4. Clone Repository

```bash
git clone https://github.com/0xmichaelran/indexTTS-worker.git
cd indexTTS-worker
```

#### 5. Install PyTorch with CUDA

**This is the critical step for GPU support:**

```bash
# Install PyTorch with CUDA 12.1 support via conda
conda install pytorch torchvision torchaudio pytorch-cuda=12.1 -c pytorch -c nvidia -y
```

**Why conda instead of pip?**
- `pip install torch` defaults to CPU-only version
- `conda install pytorch pytorch-cuda` ensures correct CUDA binaries
- Handles CUDA library dependencies automatically

#### 6. Install Project Dependencies

```bash
# Install remaining dependencies (Note: NOT using [cuda] extra since PyTorch is already installed)
pip install -e ".[worker]"
```

**Important:** We use `[worker]` instead of `[cuda,worker]` because:
- PyTorch with CUDA was installed via conda (step 5)
- `[cuda]` extra would try to reinstall CPU-only PyTorch
- `[worker]` installs only RabbitMQ, S3, and TTS dependencies

#### 7. Handle Windows-Specific Dependency (if on Windows)

If you encounter an error with `pynini`:

```bash
# ERROR: Failed building wheel for pynini
# Solution: Install via conda first
conda install -c conda-forge pynini==2.1.6 -y
pip install WeTextProcessing --no-deps
```

#### 8. Download TTS Models

```bash
# Download IndexTTS-1.5 models (~2GB, one-time download)
mkdir -p checkpoints
huggingface-cli download IndexTeam/IndexTTS-1.5 \
  config.yaml bigvgan_generator.pth bigvgan_discriminator.pth \
  bpe.model dvae.pth gpt.pth unigram_12000.vocab \
  --local-dir checkpoints
```

**For users in China:**
```bash
export HF_ENDPOINT="https://hf-mirror.com"
```

#### 9. Verify Installation

```bash
# Verify CUDA is available
python -c "import torch; print(f'PyTorch: {torch.__version__}'); print(f'CUDA available: {torch.cuda.is_available()}')"

# Expected output:
# PyTorch: 2.x.x+cu121  (note the +cu121 suffix, NOT +cpu)
# CUDA available: True

# Verify TTS engine loads
python -c "from indextts.infer import create_tts_engine; print('✓ TTS engine OK')"
```

**Total time: 10-15 minutes** (mostly downloading models and CUDA libraries)

---

## macOS Setup (CPU, Mock TTS)

For **development and testing** without GPU requirements. Uses native macOS `say` command instead of IndexTTS models.

### Prerequisites

- macOS 10.13+
- 8GB+ RAM
- Python 3.10

### Quick Setup with uv (Recommended)

```bash
# 1. Install uv (fast Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Restart terminal or add to PATH
export PATH="$HOME/.cargo/bin:$PATH"

# 2. Clone repository
git clone https://github.com/0xmichaelran/indexTTS-worker.git
cd indexTTS-worker

# 3. Create virtual environment with Python 3.10
uv venv --python 3.10
source .venv/bin/activate

# 4. Install IndexTTS with macOS native TTS
uv pip install -e ".[mac,worker]"

# 5. Verify installation
python -c "from indextts.macos_tts import MacOSTTS; print('✓ macOS TTS Ready')"
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

### Troubleshooting macOS Setup

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

## Configuration

### 1. Environment Variables

Create a `.env` file in the project root:

```bash
cp .env.example .env
```

Edit `.env` with your settings:

```bash
# RabbitMQ Configuration
RABBITMQ_URL=amqp://user:password@host:5672/
RABBITMQ_HOST=localhost  # For startup logs

# S3 Storage Bucket (voices, audio prompts - read-only)
S3_STORAGE_ENDPOINT_URL=https://storage-provider.com
S3_STORAGE_ACCESS_KEY_ID=your-storage-key
S3_STORAGE_SECRET_ACCESS_KEY=your-storage-secret
S3_STORAGE_BUCKET_NAME=your-storage-bucket
S3_STORAGE_REGION=ap-southeast-1
S3_STORAGE_USE_SSL=true

# S3 Output Bucket (TTS results - write-only)
S3_OUTPUT_ENDPOINT_URL=https://output-provider.com
S3_OUTPUT_ACCESS_KEY_ID=your-output-key
S3_OUTPUT_SECRET_ACCESS_KEY=your-output-secret
S3_OUTPUT_BUCKET_NAME=your-output-bucket
S3_OUTPUT_REGION=us-east-1
S3_OUTPUT_USE_SSL=true
```

**Why two buckets?**
- **Storage bucket**: Stores voice recordings and audio prompts (read-only during synthesis)
- **Output bucket**: Stores TTS synthesis results (write-only during synthesis)
- Benefits: Different providers, regions, credentials, and costs per bucket

### 2. RabbitMQ Setup

See [RABBITMQ_SETUP.md](./RABBITMQ_SETUP.md) for detailed RabbitMQ installation and configuration.

### 3. S3 Bucket Structure

```
Storage Bucket:
├── audio-prompts/
│   ├── {voice_id}.wav       # Worker reads voice prompts
│   └── {voice_id}.json      # Voice metadata

Output Bucket:
├── tts-audio/
│   ├── studio/{job_id}.mp3      # Studio TTS results (long-term retention)
│   └── playground/{job_id}.mp3  # Playground TTS (30-day retention)
```

---

## Running the Worker

### Windows (with conda)

```powershell
# Activate environment
conda activate tts_worker

# Start worker
python -m services.tts_worker
```

### macOS (with uv)

```bash
# Activate environment
source .venv/bin/activate

# Start worker
python -m services.tts_worker
```

### Expected Startup Output

```
18:30:45 [INFO    ] 
═══════════════════════════════════════════════════════════════════════════
                              STARTUP
═══════════════════════════════════════════════════════════════════════════

18:30:45 [INFO    ] Platform:         Windows / Darwin
18:30:46 [SUCCESS ] TTS engine initialized
18:30:46 [SUCCESS ] S3 client initialized
18:30:46 [SUCCESS ] Idempotent uploader initialized

───────────────────────────────────────────────────────────────────────────
                       Initializing Circuit Breakers
───────────────────────────────────────────────────────────────────────────

18:30:46 [SUCCESS ] Signal handlers registered (SIGTERM, SIGINT)

───────────────────────────────────────────────────────────────────────────
                       Connecting to RabbitMQ (localhost:5672)
───────────────────────────────────────────────────────────────────────────

18:30:47 [SUCCESS ] Connected to RabbitMQ

───────────────────────────────────────────────────────────────────────────
                              CONNECTIONS
───────────────────────────────────────────────────────────────────────────

18:30:47 [INFO    ] S3 Storage Bucket:   your-storage-bucket
18:30:47 [INFO    ] S3 Output Bucket:    your-output-bucket

═══════════════════════════════════════════════════════════════════════════
                          STARTUP COMPLETE
═══════════════════════════════════════════════════════════════════════════

18:30:47 [INFO    ] Starting message consumption...
```

### Running as a Service (Production)

**systemd (Linux):**

Create `/etc/systemd/system/tts-worker.service`:

```ini
[Unit]
Description=IndexTTS Worker Service
After=network.target

[Service]
Type=simple
User=your-user
WorkingDirectory=/path/to/indexTTS-worker
Environment="PATH=/path/to/miniconda/envs/tts_worker/bin"
ExecStart=/path/to/miniconda/envs/tts_worker/bin/python -m services.tts_worker
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable tts-worker
sudo systemctl start tts-worker
sudo systemctl status tts-worker
```

**Windows Service:**

Use NSSM (Non-Sucking Service Manager):

```powershell
# Install NSSM
choco install nssm

# Create service
nssm install TTSWorker "C:\path\to\miniconda\envs\tts_worker\python.exe" "-m services.tts_worker"
nssm set TTSWorker AppDirectory "D:\runway\git\indextts-0xmichaelran"
nssm start TTSWorker
```

---

## Verification

### 1. Check Worker Logs

Worker should show:
- ✓ TTS engine initialized
- ✓ S3 client initialized
- ✓ Connected to RabbitMQ
- Starting message consumption...

### 2. Test Job Processing

Send a test job to RabbitMQ:

```python
import pika
import json

connection = pika.BlockingConnection(
    pika.ConnectionParameters('localhost')
)
channel = connection.channel()

job = {
    "job_id": "test_001",
    "text": "Hello, this is a test",
    "audio_prompt_path": "audio-prompts/test_voice.wav",
    "language": "en",
    "job_type": "studio",
    "output_path_template": "tts-audio/studio/{job_id}.mp3"
}

channel.basic_publish(
    exchange='',
    routing_key='tts_jobs',
    body=json.dumps(job)
)

print("✓ Test job sent")
connection.close()
```

### 3. Monitor Processing

Worker logs should show:
```
18:31:00 [INFO    ] [JOB test_001] Received from queue
18:31:00 [INFO    ] [JOB test_001] Processing TTS request (type: studio, language: en)
18:31:01 [INFO    ] [JOB test_001] Downloading audio prompt from S3...
18:31:02 [INFO    ] [JOB test_001] Synthesizing audio...
18:31:05 [INFO    ] [JOB test_001] Uploading to S3...
18:31:06 [INFO    ] [JOB test_001] Job completed successfully in 6.23s
```

### 4. Check Results Queue

Results should appear in `tts_results` queue:

```python
import pika

connection = pika.BlockingConnection(
    pika.ConnectionParameters('localhost')
)
channel = connection.channel()

method, properties, body = channel.basic_get('tts_results', auto_ack=True)
if method:
    result = json.loads(body)
    print(f"✓ Result received: {result}")
else:
    print("No results yet")

connection.close()
```

---

## Troubleshooting

### CUDA Not Available (Windows/Linux)

**Symptom:**
```python
>>> import torch
>>> torch.cuda.is_available()
False
```

**Solution:**
```bash
# Reinstall PyTorch with CUDA via conda
conda activate tts_worker
conda install pytorch torchvision torchaudio pytorch-cuda=12.1 -c pytorch -c nvidia -y

# Verify
python -c "import torch; print(torch.__version__)"
# Should show: 2.x.x+cu121 (NOT +cpu)
```

### CPU-Only PyTorch Installed

**Symptom:**
```python
>>> import torch
>>> torch.__version__
'2.13.0+cpu'  # Note the +cpu suffix
```

**Cause:**
You ran `pip install -e ".[cuda,worker]"` before installing PyTorch via conda.

**Solution:**
```bash
# Uninstall CPU version
pip uninstall torch torchaudio -y

# Install CUDA version via conda
conda install pytorch torchvision torchaudio pytorch-cuda=12.1 -c pytorch -c nvidia -y
```

### TorchCodec Required Error

**Symptom:**
```
TorchCodec is required for load_with_torchcodec. Please install torchcodec to use this function.
```

**Solution:**
```bash
conda activate tts_worker
pip install torchcodec
```

### Huggingface-Hub Version Conflict

**Symptom:**
```
ImportError: huggingface-hub>=0.19.3,<1.0 is required for a normal functioning of this module, but found huggingface-hub==1.26.1
```

**Solution:**
```bash
conda activate tts_worker
pip install "huggingface-hub>=0.19.3,<1.0"
```

**Note:** This may conflict with gradio if installed. Gradio is only needed for web UI, not for the worker service.

### RabbitMQ Connection Refused

**Symptom:**
```
Failed to connect to RabbitMQ: [Errno 111] Connection refused
```

**Solution:**
```bash
# Check if RabbitMQ is running
sudo systemctl status rabbitmq-server  # Linux
brew services list | grep rabbitmq     # macOS

# Start RabbitMQ
sudo systemctl start rabbitmq-server   # Linux
brew services start rabbitmq           # macOS
```

### S3 Access Denied

**Symptom:**
```
[JOB xxx] S3ConfigError: Access Denied
```

**Solution:**
1. Verify credentials in `.env`:
   - `S3_STORAGE_ACCESS_KEY_ID`
   - `S3_STORAGE_SECRET_ACCESS_KEY`
   - `S3_OUTPUT_ACCESS_KEY_ID`
   - `S3_OUTPUT_SECRET_ACCESS_KEY`

2. Check bucket permissions (IAM policy should allow `s3:GetObject`, `s3:PutObject`)

3. Verify bucket names are correct

### Models Not Found

**Symptom:**
```
FileNotFoundError: [Errno 2] No such file or directory: 'checkpoints/gpt.pth'
```

**Solution:**
```bash
# Download models
mkdir -p checkpoints
huggingface-cli download IndexTeam/IndexTTS-1.5 \
  config.yaml bigvgan_generator.pth bigvgan_discriminator.pth \
  bpe.model dvae.pth gpt.pth unigram_12000.vocab \
  --local-dir checkpoints

# Verify
ls checkpoints/
# Should show: gpt.pth, bigvgan_generator.pth, config.yaml, etc.
```

### Worker Crashes on Job Processing

**Check logs for specific error:**
```bash
# Enable file logging
# Edit services/tts_worker.py:
configure_logging(
    level=logging.INFO,
    use_file=True,  # Change to True
    file_path="logs/worker.log"
)

# Restart worker
python -m services.tts_worker

# Check logs
tail -f logs/worker.log
```

---

## Performance Tuning

### Circuit Breaker Thresholds

Adjust in `services/tts_worker.py`:

```python
# S3 circuit breaker
self.s3_breaker = get_circuit_breaker(
    name="S3Download",
    failure_threshold=5,      # Open after 5 failures
    reset_timeout=60,         # Try again after 60s
)

# TTS circuit breaker
self.tts_breaker = get_circuit_breaker(
    name="IndexTTS",
    failure_threshold=3,      # Open after 3 failures
    reset_timeout=30,         # Try again after 30s
)
```

### RabbitMQ Prefetch Count

Process one job at a time (prevents overload):

```python
# In services/tts_worker.py
self.channel.basic_qos(prefetch_count=1)
```

### Exponential Backoff for Retries

Configured in `process_job()`:
- Initial delay: 2 seconds
- Multiplier: 2
- Sequence: 2s → 4s → 8s

---

## Next Steps

- **Architecture Details** → [ARCHITECTURE.md](./ARCHITECTURE.md)
- **Dual-Bucket S3 Guide** → [DUAL_BUCKET_GUIDE.md](./DUAL_BUCKET_GUIDE.md)
- **Network Resilience** → [NETWORK_RESILIENCE.md](./NETWORK_RESILIENCE.md)
- **API Reference** → [API.md](./API.md)
- **FAQ** → [FAQ.md](./FAQ.md)

---

## Getting Help

- **GitHub Issues**: https://github.com/0xmichaelran/indexTTS-worker/issues
- **QQ群（二群）**: 1048202584
- **Discord**: https://discord.gg/uT32E7KDmy
