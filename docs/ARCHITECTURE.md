# Architecture Guide

IndexTTS is designed with platform-specific optimizations while maintaining a unified API.

---

## System Overview

```
┌─────────────────────────────────────────────────────────────┐
│                   indexTTS Package                          │
│              (indextts/__init__.py)                         │
│           Exports: IndexTTS, create_tts_engine              │
└─────────────────────────────────────────────────────────────┘
                              ↓
                    ┌─────────────────────┐
                    │ create_tts_engine() │
                    │  (factory function)  │
                    └─────────────────────┘
                         ↓        ↓
            ┌─────────────────────────────────────┐
            │        Windows/Linux Platform       │
            │    (GPU Inference with CUDA)        │
            └─────────────────────────────────────┘
                            ↓
                ┌───────────────────────┐
                │   IndexTTS Class      │
                │  (infer.py)           │
                ├───────────────────────┤
                │ • GPT Model           │
                │ • BigVGAN Vocoder     │
                │ • GPU Inference       │
                └───────────────────────┘
                            ↓
                ┌───────────────────────┐
                │   Dependencies        │
                ├───────────────────────┤
                │ • PyTorch             │
                │ • torchaudio          │
                │ • transformers        │
                │ • BigVGAN             │
                │ • CUDA libraries      │
                │ • ~5-10GB total       │
                └───────────────────────┘
```

---

## File Organization

```
indextts/
├── __init__.py                    Factory function exports
├── infer.py                       GPU inference + factory
├── cli.py                         Command-line interface
├── BigVGAN/                       Vocoder implementation
│   ├── bigvgan.py
│   ├── models.py
│   ├── utils.py
│   ├── ECAPA_TDNN.py
│   ├── activations.py
│   ├── alias_free_activation/     CUDA optimizations
│   └── nnet/                      Neural network blocks
├── gpt/                           GPT model
│   ├── model.py
│   ├── perceiver.py
│   ├── conformer_encoder.py
│   └── conformer/                 Conformer blocks
└── utils/                         Utilities
    └── audio_normalization.py     LUFS loudness normalization

services/                          Worker services
├── tts_worker.py                  Main RabbitMQ worker
├── alignment.py                   Forced alignment (stable-whisper)
├── circuit_breaker.py             Circuit breaker pattern
├── s3_config.py                   Dual-bucket S3 client
├── idempotent_upload.py           Idempotent S3 upload
└── logging_config.py              Structured logging

app/                               Database & caching
├── cache_service.py               TTS synthesis cache
├── database.py                    Database connection
└── models.py                      SQLAlchemy models
```

---

## Module Design

### Factory Function: `create_tts_engine()`

```python
def create_tts_engine(cfg_path="checkpoints/config.yaml", model_dir="checkpoints", is_fp16=True, device=None, **kwargs):
    """
    Create IndexTTS engine for GPU inference
    
    Args:
        cfg_path: Path to model config YAML
        model_dir: Path to model checkpoints directory
        is_fp16: Use float16 precision (default: True)
        device: Device to use (default: auto-detect)
        **kwargs: Additional arguments
    
    Returns:
        IndexTTS instance for GPU inference
    
    Raises:
        RuntimeError: If dependencies missing or models not found
    """
```

**Auto-detection logic:**
1. Dependencies available?
   - No → Raise clear error with installation instructions
2. Return IndexTTS engine (GPU inference)

**Example:**
```python
from indextts import create_tts_engine

# Auto-detects GPU and initializes IndexTTS
tts = create_tts_engine()
```

---

### Class: `IndexTTS` (GPU Inference)

Located in: `indextts/infer.py`

```python
class IndexTTS:
    """GPU-based zero-shot voice cloning TTS"""
    
    def __init__(self, cfg_path, model_dir, is_fp16=True, device=None):
        """Initialize with model config and checkpoints"""
    
    def infer(self, audio_prompt, text, output_path, **kwargs):
        """Full zero-shot inference with reference audio"""
    
    def infer_fast(self, audio_prompt, text, output_path, **kwargs):
        """Optimized batch inference (2-10x speedup for long texts)"""
```

**Key Features:**
- Production-quality voice cloning
- Reference-based synthesis
- GPU acceleration (NVIDIA CUDA, Apple MPS, CPU fallback)
- Batch processing support
- FP16 optimization for faster inference
- Installation time: 10-25 minutes (GPU required)

**Use Cases:**
- Production TTS inference
- High-quality voice cloning
- Batch processing
- GPU-accelerated systems

---

## Data Flow

### IndexTTS Flow

```
User Text Input
    ↓
create_tts_engine()
    ↓
Initialize IndexTTS Engine
    ↓
Load GPT Model
    ↓
Load Reference Audio Features
    ↓
infer() or infer_fast()
    ↓
Generate Tokens
    ↓
BigVGAN Vocoding
    ↓
Save WAV File ✓
```

**Characteristics:**
- Batch processing capable
- GPU-accelerated (CUDA/MPS)
- Produces high-quality audio
- File-based storage

---

### Windows/Linux GPU Flow

```
User Text + Reference Audio
    ↓
create_tts_engine()
    ↓
Platform Detection → Windows/Linux
    ↓
IndexTTS.__init__()
    ↓
Load GPT Model → GPU/CUDA
Load BigVGAN Vocoder → GPU/CUDA
Load Tokenizers & Configs
    ↓
infer() or infer_fast()
    ├─ Preprocess reference audio
    ├─ Extract voice features
    ├─ Tokenize text
    ├─ Run GPT inference
    ├─ Generate acoustic tokens
    ├─ BigVGAN vocoding
    └─ Encode WAV output
    ↓
Save WAV File ✓
```

**Characteristics:**
- Multi-GPU support
- Batch processing capable
- 2-10 seconds per 10s audio (RTF ~0.2-1.0)
- Memory intensive (6-8GB VRAM)

---

### Worker Pipeline (RabbitMQ + S3)

```
RabbitMQ Job Message
    ↓
┌─────────────────────────────────────────────┐
│ 1. Cache Lookup (text + voice)             │
│    ├─ Cache HIT → Copy base audio          │
│    └─ Cache MISS → Synthesize (ratio=1.0)  │
│                    → Store in cache          │
└─────────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────────┐
│ 2. Time-Stretch (if ratio != 1.0)          │
│    local_output = stretched or base audio   │
└─────────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────────┐
│ 3. Forced Alignment (stable-whisper, CPU)  │
│    ├─ Input: local_output, text, language  │
│    ├─ Model: Whisper small (CPU-only)      │
│    └─ Output: word-level timestamps         │
│       ├─ raw_alignment.json (kept on disk)  │
│       ├─ alignment.srt (kept on disk)       │
│       └─ alignment.json (uploaded to S3)    │
└─────────────────────────────────────────────┘
    ↓
┌─────────────────────────────────────────────┐
│ 4. Upload Audio + Parsed Alignment to S3   │
│    Output bucket (tts-audio/)               │
└─────────────────────────────────────────────┘
    ↓
Return Job Result (audio_path + alignment_path)
```

**Pipeline Characteristics:**
- **Synthesis Cache**: 65-80% faster for cache hits (10,000 entry capacity)
- **Time-Stretching**: Librosa time_stretch for speed adjustment (ratio parameter)
- **Forced Alignment**: Mandatory; ~0.5-5s per minute of audio (CPU)
- **S3 Dual-Bucket**: Separate storage (voices) and output (TTS results) buckets
- **Circuit Breakers**: S3, TTS, and Alignment (prevents cascading failures)
- **Idempotent Upload**: Prevents duplicate uploads on job retry

---

## Dependency Management

### Conditional Imports

```python
# In indextts/__init__.py
import platform

# GPU inference
try:
    import torch
    PYTORCH_AVAILABLE = True
except ImportError as e:
    PYTORCH_AVAILABLE = False
    PYTORCH_ERROR = str(e)
```

### Graceful Degradation

1. **Import-time validation**: Catch missing dependencies early
2. **Runtime guidance**: Provide clear, actionable error messages
3. **Automated testing**: `test_platform.py` identifies missing deps

### Platform-Specific Dependencies

**Windows/Linux** (GPU Inference):
```toml
torch>=2.1.2
torchaudio
transformers==4.36.2
tokenizers==0.15.0
accelerate==0.25.0
einops==0.8.1
vocos==0.1.0
numba==0.58.1
```
~5-10GB, NVIDIA GPU required

---

## Configuration

### Environment Variables

```bash
# Model paths
MODEL_DIR=checkpoints
CONFIG_PATH=checkpoints/config.yaml

# Inference parameters
IS_FP16=true              # Use float16 for faster inference
DEVICE=cuda:0             # GPU device (cuda:0, mps, cpu)
USE_CUDA_KERNEL=false     # Use CUDA kernels if available

# API service
HOST=0.0.0.0
PORT=8848
WORKERS=4
LOG_LEVEL=info

# File storage
UPLOAD_DIR=outputs/audio_prompt
OUTPUT_DIR=outputs/tts_output
MAX_UPLOAD_SIZE=104857600  # 100MB

# Inference defaults
MAX_TEXT_TOKENS_PER_SENTENCE=120
SENTENCES_BUCKET_MAX_SIZE=4
```

### Model Configuration

Models are configured in `checkpoints/config.yaml`:

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
```

---

## API Compatibility

### Old API (Still Works)
```python
from indextts.infer import IndexTTS

tts = IndexTTS(cfg_path="...", model_dir="...")
tts.infer("reference.wav", "text", "output.wav")
```

### New Unified API
```python
from indextts import create_tts_engine

tts = create_tts_engine()  # Works everywhere
tts.infer("reference.wav", "text", "output.wav")
```

**Both APIs coexist** - Users can migrate gradually without breaking changes.

---

## Error Handling

### Import Errors
```
RuntimeError: "IndexTTS GPU inference requires PyTorch"
RuntimeError: "CUDA support requires torch with CUDA enabled"
```

### Runtime Errors
```
ValueError: "Voice not found. Using default voice."
RuntimeError: "CUDA out of memory. Try reducing batch size or using CPU."
FileNotFoundError: "Model checkpoint not found in {model_dir}"
```

### Recovery Strategies
- Clear error messages with next steps
- Automatic fallback where safe
- Device fallback: CUDA → MPS → CPU
- Timeout handling for long inference

---

## Summary

This architecture provides:

✅ **Platform optimization** - Right tool for each platform
✅ **Minimal dependencies** - Install only what needed
✅ **Unified API** - Same code works everywhere  
✅ **Graceful degradation** - Clear guidance when deps missing
✅ **Easy testing** - Quick verification suite
✅ **Clear documentation** - Guides for each platform

Users get the best experience for their platform while maintaining code portability.

---

## References

- **Platform Detection**: `platform` module documentation
- **PyTorch**: https://pytorch.org/docs/stable/
- **CUDA**: https://docs.nvidia.com/cuda/
- **Original IndexTTS**: https://github.com/index-tts/index-tts
