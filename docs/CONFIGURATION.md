# Configuration Guide

Configure IndexTTS for your specific use case.

---

## Environment Variables

Create a `.env` file in your project root:

```bash
# Model Configuration
MODEL_DIR=checkpoints
CONFIG_PATH=checkpoints/config.yaml

# Inference
IS_FP16=true
DEVICE=cuda:0
USE_CUDA_KERNEL=false

# API Service
HOST=0.0.0.0
PORT=8848
WORKERS=4
LOG_LEVEL=info

# File Storage
UPLOAD_DIR=outputs/audio_prompt
OUTPUT_DIR=outputs/tts_output
MAX_UPLOAD_SIZE=104857600

# Inference Parameters
MAX_TEXT_TOKENS_PER_SENTENCE=120
SENTENCES_BUCKET_MAX_SIZE=4
```

### Loading Environment Variables

**Python:**
```python
import os
from dotenv import load_dotenv

load_dotenv()

model_dir = os.getenv("MODEL_DIR", "checkpoints")
device = os.getenv("DEVICE", "auto")
```

**FastAPI:**
```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    MODEL_DIR: str = "checkpoints"
    DEVICE: str = "cuda:0"
    PORT: int = 8848
    
    class Config:
        env_file = ".env"

settings = Settings()
```

---

## Configuration Parameters

### Model Configuration

**Location:** `checkpoints/config.yaml`

```yaml
# Model dimensions
gpt_model_dim: 768
gpt_num_heads: 12
gpt_num_layers: 12
gpt_context_window: 4096

# Vocoder
vocoder: bigvgan2
vocoder_dim: 512

# Audio
sample_rate: 24000
n_fft: 1024
n_mel_channels: 128
win_length: 1024
hop_length: 300
f_min: 0
f_max: 12000

# Text processing
num_mels: 128
n_speakers: 1

# Inference defaults
temperature: 1.0
top_p: 0.8
top_k: 30
repetition_penalty: 10.0
length_penalty: 0.0
num_beams: 3
```

### Inference Parameters

```python
from indextts.infer import IndexTTS

tts = IndexTTS(model_dir="checkpoints", cfg_path="checkpoints/config.yaml")

# Standard inference
tts.infer(
    audio_prompt="reference.wav",
    text="Text to synthesize",
    output_path="output.wav",
    
    # Optional parameters
    language="en",
    temperature=1.0,              # Higher = more creative/variation; Lower = more stable (0.5-2.0)
    top_p=0.8,                   # Nucleus sampling, Lower = more focused (0.0-1.0)
    top_k=30,                    # Top-k sampling
    repetition_penalty=10.0,     # Penalty for repeating tokens
    max_text_tokens_per_sentence=120,
    do_sample=True,
    num_beams=3,
    length_penalty=0.0,
    verbose=False
)

# Fast inference for long texts
tts.infer_fast(
    audio_prompt="reference.wav",
    text="Long text with many sentences...",
    output_path="output_fast.wav",
    sentences_bucket_max_size=4  # Process multiple sentences in parallel
)
```

### Temperature vs Quality

| Temperature | Use Case | Characteristics |
|------------|----------|-----------------|
| **0.1-0.5** | Consistent output | Deterministic, less variation |
| **0.8-1.0** | Balanced (default) | Natural, slight variation |
| **1.2-1.5** | Creative output | More variation, occasional artifacts |
| **2.0+** | Very creative | High variation, less stable |

### Top-P vs Diversity

| Top-P | Use Case | Effect |
|-------|----------|--------|
| **0.5** | Focused | Only top 50% of tokens considered |
| **0.8** | Standard | Most likely tokens (default) |
| **0.95** | Diverse | Almost all tokens allowed |
| **1.0** | Maximum | All tokens allowed |

---

## Device Configuration

### Auto-Detection

```python
from indextts.infer import IndexTTS

# Automatically detects best device
tts = IndexTTS(model_dir="checkpoints", device=None)

# Detects as: CUDA (if available) → MPS → CPU
```

### Manual Selection

**NVIDIA GPU:**
```python
tts = IndexTTS(model_dir="checkpoints", device="cuda:0")  # First GPU
tts = IndexTTS(model_dir="checkpoints", device="cuda:1")  # Second GPU
```

**Apple Silicon (MPS):**
```python
tts = IndexTTS(model_dir="checkpoints", device="mps")
```

**CPU Only:**
```python
tts = IndexTTS(model_dir="checkpoints", device="cpu")
```

### Device Check

```python
import torch

# Check available devices
print(f"CUDA available: {torch.cuda.is_available()}")
print(f"Device count: {torch.cuda.device_count()}")
print(f"Current device: {torch.cuda.current_device()}")
print(f"Device name: {torch.cuda.get_device_name()}")

# MPS (Apple Silicon)
print(f"MPS available: {torch.backends.mps.is_available()}")

# Get default device
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"Default device: {device}")
```

---

## Language Configuration

### Language Detection

```python
from indextts.infer import IndexTTS

tts = IndexTTS(model_dir="checkpoints", cfg_path="checkpoints/config.yaml")

# Automatic detection
tts.infer("reference.wav", "你好", "output_zh.wav")  # Detects Chinese
tts.infer("reference.wav", "Hello", "output_en.wav")  # Detects English
```

### Explicit Language Selection

```python
# Specify language
tts.infer(
    "reference.wav",
    "Text here",
    "output.wav",
    language="zh"  # "zh" for Chinese, "en" for English
)
```

### Mixed Language Support

```python
# Mix Chinese and English
mixed_text = "你好 Hello 世界 World"
tts.infer("reference.wav", mixed_text, "output_mixed.wav")
```

---

## Performance Configuration

### Memory Optimization

```python
import torch
from indextts.infer import IndexTTS

# Check GPU memory
print(f"GPU memory: {torch.cuda.memory_allocated() / 1e9:.2f}GB")

# Use FP16 (float16) for 2x memory savings
tts = IndexTTS(
    model_dir="checkpoints",
    device="cuda:0",
    is_fp16=True  # Faster, uses less memory
)

# FP32 if you need more precision
tts = IndexTTS(
    model_dir="checkpoints",
    device="cuda:0",
    is_fp16=False  # Slower, uses more memory
)

# Clear cache after inference
torch.cuda.empty_cache()
```

### Batch Processing

```python
from indextts.infer import IndexTTS

tts = IndexTTS(model_dir="checkpoints", cfg_path="checkpoints/config.yaml")

# Standard mode (one sentence at a time)
tts.infer("reference.wav", "Long text...", "output_standard.wav")

# Fast mode (batch multiple sentences)
tts.infer_fast(
    "reference.wav",
    "Long text...",
    "output_fast.wav",
    sentences_bucket_max_size=4  # Process 4 sentences at once
)

# Fine-tune batch size based on GPU memory
# Higher = faster but more memory
# Lower = slower but less memory
```

### Inference Speed Tuning

| Setting | Speed | Memory | Quality |
|---------|-------|--------|---------|
| FP32, batch=1 | Baseline | High | Best |
| FP16, batch=1 | 2x | Medium | Excellent |
| FP16, batch=4 | 8-10x | High | Excellent |
| CPU | 0.1x | Low | Good |

---

## Logging Configuration

### Python Logging

```python
import logging

# Set logging level
logging.basicConfig(level=logging.INFO)

# Configure for specific module
logging.getLogger("indextts").setLevel(logging.DEBUG)

# Create logger
logger = logging.getLogger(__name__)
logger.info("Application started")
```

### FastAPI Logging

```python
import logging
from fastapi import FastAPI

app = FastAPI()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

@app.get("/health")
async def health():
    logger.info("Health check called")
    return {"status": "ok"}
```

### Log Levels

| Level | Use Case | Example |
|-------|----------|---------|
| **DEBUG** | Development | Variable values, function calls |
| **INFO** | General info | API requests, inference start/end |
| **WARNING** | Issues | Deprecated features, missing files |
| **ERROR** | Errors | Failed inference, missing models |
| **CRITICAL** | Fatal | Out of memory, system failure |

---

## Platform-Specific Configuration

### Windows/Linux GPU Configuration

```bash
# .env for CUDA GPU
DEVICE=cuda:0
IS_FP16=true
MODEL_DIR=/path/to/checkpoints
```

```python
from indextts.infer import IndexTTS

tts = IndexTTS(
    model_dir="/path/to/checkpoints",
    cfg_path="/path/to/config.yaml",
    is_fp16=True,
    device="cuda:0"
)
```

---

## API Server Configuration

### FastAPI Settings

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="IndexTTS API",
    description="Text-to-Speech API",
    version="1.0.0"
)

# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Change to specific origins in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Start with: uvicorn app:app --host 0.0.0.0 --port 8848
```

### Uvicorn Server Options

```bash
# Basic
python run-indextts-1-5.py

# With custom settings
python run-indextts-1-5.py \
  --host 0.0.0.0 \
  --port 9999 \
  --workers 4 \
  --reload

# Production
uvicorn run-indextts-1-5:app \
  --host 127.0.0.1 \
  --port 8848 \
  --workers 4 \
  --access-log \
  --timeout-keep-alive 5
```

---

## Docker Configuration

### .dockerenv

```bash
MODEL_DIR=/app/checkpoints
CONFIG_PATH=/app/checkpoints/config.yaml
IS_FP16=true
DEVICE=cuda:0
PORT=8848
HOST=0.0.0.0
```

### docker-compose.yml

```yaml
services:
  indextts:
    build: .
    ports:
      - "8848:8848"
    environment:
      MODEL_DIR: /app/checkpoints
      DEVICE: cuda:0
      IS_FP16: "true"
    volumes:
      - ./checkpoints:/app/checkpoints:ro
      - ./outputs:/app/outputs
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
```

---

## Troubleshooting Configuration

### Issue: Wrong Device Being Used

```python
import torch
from indextts.infer import IndexTTS

# Verify device selection
tts = IndexTTS(model_dir="checkpoints", device="cuda:0")
print(f"Using device: {tts.device}")

# Check if device is actually available
assert torch.cuda.is_available(), "CUDA not available!"
assert torch.cuda.device_count() > 0, "No GPU found!"
```

### Issue: OOM (Out of Memory)

```python
# Solution 1: Reduce precision
tts = IndexTTS(model_dir="checkpoints", is_fp16=True)

# Solution 2: Use CPU
tts = IndexTTS(model_dir="checkpoints", device="cpu")

# Solution 3: Reduce batch size
tts.infer_fast(..., sentences_bucket_max_size=2)

# Solution 4: Clear cache
import torch
torch.cuda.empty_cache()
```

### Issue: Slow Inference

```python
# Use FP16
tts = IndexTTS(model_dir="checkpoints", is_fp16=True)

# Use fast mode for long texts
tts.infer_fast(..., sentences_bucket_max_size=4)

# Profile to identify bottleneck
import cProfile
cProfile.run('tts.infer(...)')
```

---

## References

- **PyTorch Configuration**: https://pytorch.org/docs/stable/
- **FastAPI Settings**: https://fastapi.tiangolo.com/
- **Environment Variables**: https://en.wikipedia.org/wiki/.env
- **CUDA Computing**: https://docs.nvidia.com/cuda/
