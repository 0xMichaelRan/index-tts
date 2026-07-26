# Installation Strategy: Conda + Platform-Specific Package Managers

## Overview

This project uses a **platform-specific installation approach** optimized for each platform:

```
Windows                              macOS
├─ conda create -n indexTTS         ├─ uv venv
│  (Python 3.10 environment)        │  (Python 3.10 environment)
│                                   │
└─ pip install -e ".[cuda,worker]"  └─ uv pip install -e ".[mac,worker]"
   (GPU inference)                     (API testing, lightweight)
```

## Why This Approach?

### Windows: Conda + pip

**Environment Setup**
```bash
conda create -n indexTTS python=3.10
conda activate indexTTS
```

**Why Conda?**
1. Isolates Python 3.10 from system Python
2. Handles pre-compiled binaries well
3. Easy cleanup: `conda env remove -n indexTTS`
4. Standard for ML workflows

**Package Installation**
```bash
pip install -e ".[cuda,worker]"
```

**Why pip (not uv)?**
1. **CUDA Wheel Availability**: PyTorch publishes optimized CUDA wheels on PyPI
2. **Complex Dependencies**: Heavy ML stack (~60 packages + CUDA libraries) needs battle-tested resolver
3. **Stable**: Thousands of Windows ML setups use this pattern
4. **Performance**: 2GB CUDA downloads dominate time, not package resolution
5. **Compatibility**: PyTorch + pip is industry standard

**Installation time:** 10-25 minutes (mostly CUDA download/extraction)

---

### macOS: uv venv + uv pip

**Environment Setup** (no Conda needed)
```bash
python3 -m venv indexTTS-env
source indexTTS-env/bin/activate
```
OR use uv's built-in venv:
```bash
uv venv indexTTS-env
source indexTTS-env/bin/activate
```

**Why uv (not Conda)?**
1. **Minimal Dependencies**: `[mac]` has only ~20 packages (no PyTorch, no CUDA)
2. **Lightweight**: Modern resolver is 10-100x faster for small graphs
3. **Simpler**: One less tool to install/learn
4. **Python Built-in venv**: Python's native venv works perfectly for 20 packages
5. **Speed**: 30 seconds - 2 minutes vs Conda's overhead

**Package Installation**
```bash
pip install uv  # Install uv itself
uv pip install -e ".[mac,worker]"
```

OR fall back to standard pip:
```bash
pip install -e ".[mac,worker]"
```

**Installation time:** 30 seconds - 2 minutes ⚡

---

### Why No Native macOS TTS?

**UPDATED: macOS Native TTS IS Integrated!**

The `[mac]` extra provides **native AVFoundation TTS** for macOS:

- Uses macOS system voices (AVSpeechSynthesizer)
- No PyTorch/CUDA required (lightweight, ~10MB dependencies)
- Fast setup: 30 seconds - 2 minutes
- Compatible API with IndexTTS GPU inference
- Perfect for development/testing on macOS laptops

**Why use native TTS on macOS?**
- macOS doesn't have NVIDIA CUDA GPUs
- IndexTTS requires CUDA for quality inference
- Native TTS provides instant feedback during development
- System voices are high quality for testing purposes
- Can switch to Windows GPU server for production inference

**Dependencies required:**
```toml
mac = [
    "pyobjc-framework-AVFoundation>=10.0",
    "pyobjc-framework-Cocoa>=10.0",
]
```

## Design Decisions

### Decision 1: Windows Conda + pip, macOS uv-only

**Alternative Considered:** Same approach for both

**Why we didn't:** 
- Windows needs Conda's binary pre-compilation for CUDA
- macOS has only 20 packages, so uv + venv is overkill-free
- Different platforms have different needs (GPU vs lightweight testing)
- Optimize for each ecosystem

### Decision 2: Why platform-specific optional dependencies?

**Alternative Considered:** One installation everywhere

**Why we didn't:**
- Windows production needs GPU (~60 packages + CUDA 2GB+)
- macOS testing needs lightweight worker API (~20 packages)
- Forcing users to download CUDA for testing is wasteful
- IndexTTS is GPU-only—no native TTS fallback needed

### Decision 3: No `pyobjc` for macOS

**Why?**
- IndexTTS requires PyTorch GPU inference (can't run on CPU)
- The worker is an API that accepts requests and returns audio
- macOS Clients would call the API, not invoke native TTS directly
- This codebase doesn't integrate macOS TTS—it only does GPU inference
- If you wanted TTS fallback, implement it in client code, not here

## Installation Flows

### Windows Developer (GPU Inference)

```
1. Install Conda (one-time)
   └─ Download from anaconda.com
   
2. Create environment
   └─ conda create -n indexTTS python=3.10
   └─ conda activate indexTTS
   
3. Install dependencies
   └─ pip install -e ".[cuda,worker]"  (10-25 min)
   
4. Download models
   └─ huggingface-cli download ... (5-10 min)
   
5. Ready for inference!
   └─ python -c "from indextts.infer import IndexTTS"
```

### macOS Developer (API Testing)

```
1. Create environment (no Conda needed)
   └─ python3 -m venv indexTTS-env
   └─ source indexTTS-env/bin/activate
   
   OR use uv:
   └─ uv venv indexTTS-env
   └─ source indexTTS-env/bin/activate
   
2. Install dependencies
   └─ uv pip install -e ".[mac,worker]"  (30s-2m) ⚡
   
   OR use pip (fallback):
   └─ pip install -e ".[mac,worker]"  (1-2m)
   
3. Ready for API testing!
   └─ python -m indextts.cli --help
```

## Performance Metrics

### Installation Times (Typical)

| Platform | Environment | Installer | Packages | Time |
|----------|-------------|-----------|----------|------|
| Windows | Conda | pip | ~60 + CUDA | **10-25 min** |
| macOS | venv or uv | uv pip | ~20 | **30s-2m** ⚡ |
| macOS | venv or uv | pip | ~20 | **1-2m** ⚡ |

*Network speed significantly affects downloads*

## Reproducibility

### Ensuring Consistent Installations

**For teams:**

**Windows:**
```bash
conda create -n indexTTS python=3.10
conda activate indexTTS
pip install -e ".[cuda,worker]"
```

**macOS:**
```bash
python3 -m venv indexTTS-env
source indexTTS-env/bin/activate
uv pip install -e ".[mac,worker]"
```

**Version pinning:**
All dependencies are pinned in `pyproject.toml`:
- `torch>=2.1.2` (minimum version)
- `transformers==4.36.2` (exact version)
- Platform-specific versions ensure consistency

## Troubleshooting

### macOS User Having Trouble with `uv`
```bash
# Fall back to pip
pip uninstall indextts-worker  # Clean up
pip install -e ".[mac,worker]"
```

### Windows User Trying `uv`
```bash
# Use pip instead (not recommended on Windows for CUDA)
pip install -e ".[cuda,worker]"
```

### Cross-Platform Issue
Check `pyproject.toml` for platform markers:
```toml
"wetext; sys_platform == 'darwin'"           # macOS only
"WeTextProcessing; sys_platform != 'darwin'" # Windows/Linux only
```

## Future Considerations

### When to Revisit
- **uv reaches 1.0 stability**: Already stable; maturity not a blocker
- **PyTorch for macOS**: If GPU support comes to macOS (unlikely)
- **New installer emerges**: Monitor community trends

### Long-Term
- Consider locking files (requirements.lock) for maximum reproducibility
- Monitor if uv adoption warrants Windows support
- Update if PyTorch changes wheel distribution strategy

## Summary Table

| Aspect | Windows | macOS |
|--------|---------|-------|
| **Environment Manager** | Conda | Python venv (or uv venv) |
| **Package Installer** | pip | uv pip (or pip) |
| **Use Case** | Production GPU Inference | Development API Testing |
| **Dependencies** | ~60 packages + CUDA | ~20 packages |
| **Install Time** | 10-25 minutes | 30s - 2 minutes |
| **GPU Required** | Yes (NVIDIA) | No |
| **Model Download** | Yes (~2GB) | No |
| **Setup Complexity** | Medium | Low |

---

## References

- **Conda Docs**: https://docs.conda.io/
- **uv Project**: https://github.com/astral-sh/uv
- **pip Documentation**: https://pip.pypa.io/
- **PyTorch Installation**: https://pytorch.org/get-started/locally/
