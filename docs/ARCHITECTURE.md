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
            │     Platform Detection              │
            │  (platform.system() == "Darwin"?)   │
            └─────────────────────────────────────┘
                    ↙           ↘
    ┌──────────────────────┐   ┌───────────────────────┐
    │    macOS Path        │   │  Windows/Linux Path   │
    └──────────────────────┘   └───────────────────────┘
           ↓                            ↓
    ┌──────────────────────┐   ┌───────────────────────┐
    │   MacOSTTS Class     │   │   IndexTTS Class      │
    │ (macos_tts.py)       │   │  (infer.py)           │
    ├──────────────────────┤   ├───────────────────────┤
    │ • AVSpeechSynthesizer│   │ • GPT Model           │
    │ • System Voices      │   │ • BigVGAN Vocoder     │
    │ • Real-time TTS      │   │ • GPU Inference       │
    └──────────────────────┘   └───────────────────────┘
           ↓                            ↓
    ┌──────────────────────┐   ┌───────────────────────┐
    │   Dependencies       │   │   Dependencies        │
    ├──────────────────────┤   ├───────────────────────┤
    │ • pyobjc             │   │ • PyTorch             │
    │ • Foundation         │   │ • torchaudio          │
    │ • ~10-50MB total     │   │ • transformers        │
    │                      │   │ • BigVGAN             │
    │                      │   │ • CUDA libraries      │
    │                      │   │ • ~5-10GB total       │
    └──────────────────────┘   └───────────────────────┘
```

---

## File Organization

```
indextts/
├── __init__.py                    Factory function exports
├── infer.py                       GPU inference + factory
├── macos_tts.py                   Native macOS TTS
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
```

---

## Module Design

### Factory Function: `create_tts_engine()`

```python
def create_tts_engine(use_native_macos=None, voice=None, language="en-US", **kwargs):
    """
    Create appropriate TTS engine based on platform
    
    Args:
        use_native_macos: Force use of native TTS on macOS (default: None/auto)
        voice: System voice to use (macOS only)
        language: Language code (default: "en-US")
        **kwargs: Additional arguments for IndexTTS
    
    Returns:
        MacOSTTS or IndexTTS instance
    
    Raises:
        RuntimeError: If platform not supported or dependencies missing
    """
```

**Auto-detection logic:**
1. Platform is macOS?
   - Yes → Use `MacOSTTS` (unless forced otherwise)
   - No → Use `IndexTTS` (GPU inference)
2. Dependencies available?
   - No → Raise clear error with installation instructions
3. Return appropriate engine

**Example:**
```python
from indextts import create_tts_engine

# Auto-detects platform
tts = create_tts_engine()

# Or force specific engine
tts = create_tts_engine(use_native_macos=False)  # Use GPU even on macOS
```

---

### Class: `MacOSTTS` (macOS Native)

Located in: `indextts/macos_tts.py`

```python
class MacOSTTS:
    """Native macOS TTS using AVSpeechSynthesizer"""
    
    def __init__(self, voice=None, language="en-US"):
        """Initialize with optional system voice"""
    
    def list_voices(self, language=None):
        """List available system voices"""
    
    def infer(self, audio_prompt, text, output_path, **kwargs):
        """Synthesize to file (API compatibility)"""
    
    def infer_to_system_audio(self, text, rate=0.5, pitch=1.0, volume=1.0):
        """Speak to system audio output (primary method)"""
```

**Key Features:**
- Lightweight (pyobjc only)
- Real-time synthesis to system audio
- System voice selection
- Multilingual support (English, Chinese, Spanish, etc.)
- No GPU required
- Installation time: 30 seconds - 2 minutes

**Use Cases:**
- Development on macOS laptops
- Testing and prototyping
- Integration testing without GPU
- Audio feedback in applications

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

### macOS TTS Flow

```
User Text Input
    ↓
create_tts_engine()
    ↓
Platform Detection → "Darwin"
    ↓
MacOSTTS.__init__()
    ↓
Load System Voices (AVSpeechSynthesizer)
    ↓
infer_to_system_audio()
    ↓
Create AVSpeechUtterance
    ↓
Synthesizer.speakUtterance_()
    ↓
System Audio Output ✓
```

**Characteristics:**
- Single-threaded (one utterance at a time)
- Real-time output
- No audio file storage required
- Can play to speakers or pipe to device

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

## Dependency Management

### Conditional Imports

```python
# In indextts/__init__.py
import platform

PLATFORM = platform.system()

if PLATFORM == "Darwin":
    try:
        from indextts.macos_tts import MacOSTTS
        MACOS_TTS_AVAILABLE = True
    except ImportError as e:
        MACOS_TTS_AVAILABLE = False
        MACOS_ERROR = str(e)
else:
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

**macOS** (`[mac]` extra):
```toml
pyobjc-framework-AVFoundation>=10.0
pyobjc-framework-Cocoa>=10.0
```
~10-50MB, no GPU needed

**Windows/Linux** (`[cuda]` extra):
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
RuntimeError: "macOS native TTS requires pyobjc-framework-AVFoundation"
RuntimeError: "IndexTTS GPU inference requires PyTorch"
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

## Testing Strategy

### Unit Tests
- Import validation
- Platform detection
- Error messages
- Configuration parsing

### Integration Tests
- macOS TTS: Speaks to system audio
- GPU inference: Synthesizes with reference audio
- Factory function: Correct engine selected
- Batch processing: Multiple texts

### Platform Tests
- macOS: Python 3.10+ with pyobjc
- Windows: CUDA-enabled GPU
- Linux: CUDA-enabled GPU

See `test_platform.py` for verification suite.

---

## Performance Characteristics

### macOS Native TTS
| Metric | Value |
|--------|-------|
| Latency | <100ms (real-time) |
| Memory | ~50MB RAM |
| CPU | Minimal (system-optimized) |
| Quality | System TTS quality |
| Parallelism | Single utterance |

### Windows/Linux GPU Inference
| Metric | Value |
|--------|-------|
| Latency | 2-10s per 10s audio |
| Real-Time Factor | 0.2-1.0 |
| Memory | 6-8GB VRAM (FP16) |
| GPU | NVIDIA CUDA required |
| Quality | Production zero-shot |
| Parallelism | Batch processing |

---

## Extension Points

### Adding a New Platform
1. Create `indextts/platform_name_tts.py`
2. Implement class with `infer()` method
3. Add to `create_tts_engine()` factory
4. Add dependencies to `pyproject.toml`
5. Document in `ARCHITECTURE.md`

### Adding a New Voice Engine
1. Create class with compatible interface
2. Implement required methods
3. Register in factory function
4. Add tests

### Adding a New Vocoder
1. Add vocoder class to appropriate module
2. Update `IndexTTS` to support selection
3. Update configuration schema
4. Add performance benchmarks

---

## Security Considerations

### Input Validation
- Text inputs sanitized before synthesis
- File paths validated before writing
- Audio prompts checked for existence and format

### Dependency Security
- Pinned versions in `pyproject.toml`
- Regular updates recommended
- No arbitrary code execution

### Platform Safety
- Native APIs used as intended by OS
- GPU inference sandboxed by PyTorch
- No special privileges required
- Temporary files cleaned up

---

## Future Enhancements

### Potential Additions
1. Linux native TTS (festival, espeak)
2. GPU optimization (int8 quantization, distillation)
3. Streaming output (partial audio during inference)
4. Advanced batch scheduling
5. REST API caching layer
6. WebRTC for real-time streaming

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
- **macOS APIs**: Apple Developer Documentation for AVFoundation
- **PyTorch**: https://pytorch.org/docs/stable/
- **CUDA**: https://docs.nvidia.com/cuda/
- **Original IndexTTS**: https://github.com/index-tts/index-tts
