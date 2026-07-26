# Platform-Specific TTS Architecture

## System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                   indexTTS Package                          │
│                  (indextts/__init__.py)                     │
│              Exports: IndexTTS, create_tts_engine            │
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
    │ • ~10MB total        │   │ • transformers        │
    │                      │   │ • BigVGAN             │
    │                      │   │ • CUDA libraries      │
    │                      │   │ • ~5GB+ total         │
    └──────────────────────┘   └───────────────────────┘
```

---

## File Structure

```
indexTTS/
├── indextts/
│   ├── __init__.py                    (NEW) Package exports
│   ├── infer.py                       (MODIFIED) GPU inference + factory
│   ├── macos_tts.py                   (NEW) Native TTS
│   ├── cli.py                         (existing)
│   ├── BigVGAN/                       (existing)
│   ├── gpt/                           (existing)
│   └── utils/                         (existing)
│
├── examples/
│   └── platform_demo.py               (NEW) Usage examples
│
├── QUICKSTART.md                      (NEW) Quick start guide
├── PLATFORM_USAGE.md                  (NEW) Detailed API guide
├── INSTALLATION_STRATEGY.md           (MODIFIED) Installation rationale
├── CHANGES.md                         (NEW) Change summary
├── IMPLEMENTATION_COMPLETE.md         (NEW) Project completion
├── ARCHITECTURE.md                    (NEW) This file
└── test_platform.py                   (NEW) Verification suite
```

---

## Module Design

### `MacOSTTS` Class (`macos_tts.py`)

```python
class MacOSTTS:
    """Native macOS TTS using AVSpeechSynthesizer"""
    
    def __init__(self, voice=None, language="en-US"):
        """Initialize with system voice"""
    
    def list_voices(self, language=None):
        """List available system voices"""
    
    def infer(self, audio_prompt, text, output_path, **kwargs):
        """Synthesize to file (API compatibility)"""
    
    def infer_to_system_audio(self, text, rate=0.5, pitch=1.0, volume=1.0):
        """Speak to system audio output (primary method)"""
```

**Key Features:**
- Lightweight (pyobjc only)
- Real-time synthesis
- System voice selection
- Multilingual support

### `IndexTTS` Class (`infer.py`)

```python
class IndexTTS:
    """GPU-based zero-shot voice cloning TTS"""
    
    def __init__(self, cfg_path, model_dir, is_fp16=True, device=None):
        """Initialize with model config and checkpoints"""
    
    def infer(self, audio_prompt, text, output_path, **kwargs):
        """Full zero-shot inference with reference audio"""
    
    def infer_fast(self, audio_prompt, text, output_path, **kwargs):
        """Optimized batch inference"""
```

**Key Features:**
- Production quality
- Reference-based voice cloning
- GPU acceleration
- Batch processing support

### Factory Function (`infer.py`)

```python
def create_tts_engine(use_native_macos=None, voice=None, language="en-US", **kwargs):
    """
    Create appropriate TTS engine based on platform
    
    Auto-detect: macOS → MacOSTTS, Windows/Linux → IndexTTS
    Override: use_native_macos=True/False
    """
```

---

## Data Flow

### macOS TTS Flow

```
User Text
   ↓
create_tts_engine()
   ↓
Platform Detection (Darwin)
   ↓
MacOSTTS.__init__()
   ↓
AVSpeechSynthesizer Setup
   ↓
System Voices Loaded
   ↓
infer_to_system_audio()
   ↓
AVSpeechUtterance Creation
   ↓
Synthesizer.speakUtterance_()
   ↓
Speaker Audio Output ✓
```

### Windows/Linux GPU Flow

```
User Text + Reference Audio
   ↓
create_tts_engine()
   ↓
Platform Detection (Windows)
   ↓
IndexTTS.__init__()
   ↓
Load GPT Model (GPU)
   ↓
Load BigVGAN Vocoder (GPU)
   ↓
infer()
   ├─ Process audio prompt
   ├─ Tokenize text
   ├─ GPT inference
   ├─ BigVGAN vocoding
   └─ Save WAV file ✓
```

---

## Dependency Management

### Conditional Imports

```python
# Platform-specific behavior
if platform.system() == "Darwin":
    # macOS: Optional PyTorch, required macOS TTS
    try:
        from indextts.macos_tts import MacOSTTS
        MACOS_TTS_AVAILABLE = True
    except ImportError:
        MACOS_TTS_AVAILABLE = False
else:
    # Windows/Linux: Required PyTorch
    import torch
    import torchaudio
    PYTORCH_AVAILABLE = True
```

### Graceful Degradation

1. **Import-time:** Missing dependencies detected early
2. **Runtime:** Clear error messages guide installation
3. **Testing:** `test_platform.py` identifies missing deps

---

## Configuration

### pyproject.toml

```toml
[project.optional-dependencies]
# macOS: Lightweight native TTS
mac = [
    "pyobjc-framework-AVFoundation>=10.0",
    "pyobjc-framework-Cocoa>=10.0",
]

# Windows/Linux: Full GPU inference
cuda = [
    "torch>=2.1.2",
    "torchaudio",
    "transformers==4.36.2",
    # ... ~60 packages
]

# API worker
worker = [
    "fastapi[standard]",
    "uvicorn[standard]",
    "pika",
]
```

---

## Environment Setup

### macOS Environment
```bash
# Virtual environment (no conda needed)
python3 -m venv env
source env/bin/activate

# Package installer (fast)
pip install uv
uv pip install -e ".[mac,worker]"

# Size: ~10-50MB
# Time: 30s - 2min
```

### Windows/Linux Environment
```bash
# System environment manager
conda create -n indexTTS python=3.10
conda activate indexTTS

# Package installer (standard)
pip install -e ".[cuda,worker]"

# Size: ~5-10GB
# Time: 10-25min
```

---

## API Compatibility

### Old API (Still Works)
```python
from indextts.infer import IndexTTS

tts = IndexTTS(cfg_path="...", model_dir="...")
```

### New Unified API
```python
from indextts import create_tts_engine

tts = create_tts_engine()  # Works everywhere
```

### Both APIs Coexist
- Users can migrate gradually
- No breaking changes
- Backward compatible

---

## Error Handling

### Import Errors
```python
# Platform not supported
RuntimeError: "macOS native TTS is only available on macOS systems"

# Dependencies missing
ImportError: "macOS TTS requires pyobjc-framework-AVFoundation"

# GPU not available
RuntimeError: "IndexTTS GPU inference requested but PyTorch is not available"
```

### Runtime Errors
```python
# Voice not found
print("Warning: Voice not found. Using default for language.")

# CUDA out of memory
tts = IndexTTS(device="cpu")  # Fallback to CPU
```

---

## Testing Strategy

### Unit Tests
- **Import validation:** Modules load correctly
- **Platform detection:** Correct engine selected
- **Error messages:** Helpful and actionable

### Integration Tests
- **macOS TTS:** Speaks to system audio
- **GPU inference:** Synthesizes with reference audio
- **Factory function:** Auto-detects correctly

### Platform Tests
- **macOS:** Python 3.10+ with pyobjc
- **Windows:** CUDA-enabled GPU
- **Linux:** CUDA-enabled GPU

---

## Performance Characteristics

### macOS Native TTS
- **Latency:** <100ms (real-time)
- **Memory:** ~50MB RAM
- **CPU:** Minimal (system optimized)
- **Quality:** System TTS quality
- **Parallelism:** Single utterance at a time

### Windows/Linux GPU Inference
- **Latency:** ~2-10 seconds per 10s audio (RTF ~0.2-1.0)
- **Memory:** ~6GB VRAM (FP16)
- **GPU:** NVIDIA CUDA required
- **Quality:** Production zero-shot voice cloning
- **Parallelism:** Batch processing supported

---

## Extension Points

### Adding a New Platform
1. Create `indextts/platform_tts.py`
2. Implement `class PlatformTTS` with `infer()` method
3. Add platform detection in `create_tts_engine()`
4. Add dependencies to `pyproject.toml`
5. Document in `PLATFORM_USAGE.md`

### Adding a New Voice Engine
1. Create new class inheriting from base interface
2. Implement required methods
3. Register in factory function
4. Add tests

---

## Security Considerations

### Input Validation
- Text inputs sanitized before synthesis
- File paths validated before writing
- Audio prompts checked for existence

### Dependency Security
- Pinned versions in `pyproject.toml`
- Regular updates recommended
- No arbitrary code execution

### Platform Safety
- Native APIs used as intended
- GPU inference sandboxed by PyTorch
- No special privileges required

---

## Future Enhancements

### Potential Additions
1. **Linux Native TTS** (using festival, espeak)
2. **GPU optimization** (int8 quantization)
3. **Streaming output** (partial audio during synthesis)
4. **Batch processing** (multiple texts concurrently)
5. **REST API** (FastAPI integration)
6. **Docker support** (containerized setup)

### Backward Compatibility
- All enhancements maintain existing APIs
- Gradual migration path provided
- Old code continues to work

---

## Summary

This architecture provides:

✅ **Platform optimization** - Right tool for each platform
✅ **Minimal dependencies** - Only install what you need
✅ **Unified API** - Same code works everywhere
✅ **Graceful degradation** - Clear messages when deps missing
✅ **Backward compatibility** - Old code still works
✅ **Easy testing** - Quick verification suite
✅ **Clear documentation** - Guides for each platform

Users get the best experience for their platform while maintaining code portability.
