# FAQ - Frequently Asked Questions

---

## Installation & Setup

### Q: Which Python version should I use?
**A:** Python 3.10 is required:
- Python 3.10: Fully tested and stable (REQUIRED)

See [Installation Guide](./INSTALLATION.md) for details.

---

### Q: Why does macOS installation take 30 seconds but Windows takes 10-25 minutes?
**A:** 
- **macOS**: Native TTS uses only pyobjc (~50MB), no CUDA download
- **Windows/Linux**: Must download PyTorch + CUDA libraries (~5-10GB)

If you need GPU inference on macOS, use a Windows/Linux machine.

---

### Q: Do I need Conda on macOS?
**A:** No! You can use Python's built-in `venv`:
```bash
python3 -m venv indexTTS-env
source indexTTS-env/bin/activate
pip install -e ".[mac,worker]"
```

Conda is mainly for Windows (better CUDA support).

---

### Q: Do I need a GPU?
**A:** 
- **macOS**: No (uses native TTS)
- **Windows/Linux**: Yes (NVIDIA GPU with CUDA 11.8+ required)
- **CPU fallback**: Possible but very slow (0.1x speed)

---

### Q: Where do I download models?
**A:** 
```bash
mkdir -p checkpoints

# IndexTTS-1.5 (recommended)
huggingface-cli download IndexTeam/IndexTTS-1.5 \
  config.yaml bigvgan_generator.pth bigvgan_discriminator.pth \
  bpe.model dvae.pth gpt.pth unigram_12000.vocab \
  --local-dir checkpoints

# OR IndexTTS-1.0
huggingface-cli download IndexTeam/IndexTTS \
  config.yaml bigvgan_generator.pth bpe.model gpt.pth \
  --local-dir checkpoints
```

Models are ~2GB total. See [Installation Guide](./INSTALLATION.md) for more details.

---

## Usage & Configuration

### Q: How do I use IndexTTS?
**A:** Three ways:

1. **REST API** (easiest for web apps)
   ```bash
   python run-indextts-1-5.py
   # Visit http://localhost:8848/docs
   ```

2. **Command Line**
   ```bash
   indextts "Hello world" --voice reference.wav --output output.wav
   ```

3. **Python Library** (most flexible)
   ```python
   from indextts import create_tts_engine
   tts = create_tts_engine()
   tts.infer("reference.wav", "Hello", "output.wav")
   ```

See [Quick Start](./QUICKSTART.md) for examples.

---

### Q: What's the difference between `infer()` and `infer_fast()`?
**A:** 
- `infer()`: Standard mode, processes one sentence at a time
- `infer_fast()`: Batch mode, processes multiple sentences in parallel (2-10x faster)

Use `infer_fast()` for long texts with many sentences.

---

### Q: How do I change the speaking voice?
**A:**
**Windows/Linux:**
```python
from indextts.infer import IndexTTS
tts = IndexTTS(model_dir="checkpoints", cfg_path="checkpoints/config.yaml")

# Voice is determined by the reference audio
tts.infer("speaker1.wav", "Hello", "output.wav")  # Speaker 1's voice
tts.infer("speaker2.wav", "Hello", "output.wav")  # Speaker 2's voice
```

**macOS:**
```python
from indextts.macos_tts import MacOSTTS
tts = MacOSTTS(voice="Daniel")  # Use Daniel voice
tts.infer_to_system_audio("Hello")
```

---

### Q: Can I control speech speed, pitch, volume?
**A:** Depends on platform:

**macOS (native TTS):**
```python
tts.infer_to_system_audio(
    "Hello world",
    rate=0.5,   # Slow down
    pitch=1.2,  # Higher pitch
    volume=1.0  # Maximum volume
)
```

**Windows/Linux (GPU inference):**
Use `temperature` and `top_p` parameters:
```python
tts.infer(
    "reference.wav",
    "Hello",
    "output.wav",
    temperature=1.0,  # Higher = more variation
    top_p=0.8        # Lower = more focused
)
```

---

### Q: What languages are supported?
**A:**
- **Chinese** (Mandarin) - Optimized with pinyin support
- **English** - Full support
- **Other languages** - May work but not officially tested

Specify with:
```python
tts.infer("reference.wav", "Text", "output.wav", language="zh")  # Chinese
tts.infer("reference.wav", "Text", "output.wav", language="en")  # English
```

---

## Performance & Troubleshooting

### Q: Why is inference so slow?
**A:** Common causes:

1. **Using CPU** - 0.1x speed
   ```python
   tts = IndexTTS(model_dir="checkpoints", device="cuda:0")  # Use GPU
   ```

2. **FP32 instead of FP16** - 2x slower
   ```python
   tts = IndexTTS(model_dir="checkpoints", is_fp16=True)  # Use FP16
   ```

3. **Not using fast mode** for long texts
   ```python
   tts.infer_fast(...)  # 2-10x faster for multiple sentences
   ```

4. **CUDA not installed** - Falls back to CPU
   ```bash
   python -c "import torch; print(torch.cuda.is_available())"
   ```

---

### Q: CUDA out of memory error - what do I do?
**A:** Try these in order:

1. **Use FP16** (reduces memory by 50%)
   ```python
   tts = IndexTTS(model_dir="checkpoints", is_fp16=True)
   ```

2. **Reduce batch size**
   ```python
   tts.infer_fast(..., sentences_bucket_max_size=2)
   ```

3. **Use CPU** (slower but works)
   ```python
   tts = IndexTTS(model_dir="checkpoints", device="cpu")
   ```

4. **Restart Python** to clear GPU memory
   ```python
   import torch
   torch.cuda.empty_cache()
   ```

---

### Q: API server won't start - "port already in use"
**A:** 

```bash
# Find what's using port 8848
lsof -i :8848  # macOS/Linux
netstat -ano | findstr :8848  # Windows

# Use a different port
python run-indextts-1-5.py --port 9999
```

---

### Q: Getting "Model not found" error
**A:**

Ensure models are downloaded:
```bash
# Check if checkpoints directory exists
ls checkpoints/
# Should show: config.yaml, gpt.pth, bigvgan_generator.pth, etc.

# If empty, download:
huggingface-cli download IndexTeam/IndexTTS-1.5 \
  config.yaml bigvgan_generator.pth gpt.pth bpe.model \
  --local-dir checkpoints
```

---

### Q: Getting "CUDA kernel not found" warning
**A:** This is normal - it's falling back to PyTorch kernels:

```python
# Suppress the warning (it still works fine)
from indextts.infer import IndexTTS
tts = IndexTTS(model_dir="checkpoints", use_cuda_kernel=False)
```

---

### Q: Audio quality is poor - what can I improve?
**A:**

1. **Better reference audio**
   - Use high-quality, clear audio file (16kHz+)
   - 3-10 seconds of clear speech
   - Minimize background noise

2. **Adjust parameters**
   ```python
   tts.infer(
       "reference.wav",
       text,
       "output.wav",
       temperature=0.8,        # Lower = more stable
       repetition_penalty=10.0 # Prevent repeating tokens
   )
   ```

3. **Use higher quality model**
   - IndexTTS-1.5 is better than IndexTTS-1.0

---

### Q: How much disk space do I need?
**A:**

| Component | Size | Optional |
|-----------|------|----------|
| Code | ~100MB | No |
| Models | ~2GB | Yes (if using GPU) |
| Outputs | Variable | No |
| **Total** | **~2.1GB** | |

**macOS** (native TTS): Only ~100MB needed

---

## API & Integration

### Q: How do I integrate with my web app?
**A:**

```javascript
// JavaScript/React example
async function synthesize(text, audioFile) {
    const formData = new FormData();
    formData.append('text', text);
    formData.append('audio_prompt', audioFile);
    
    const response = await fetch('http://localhost:8848/infer/', {
        method: 'POST',
        body: formData
    });
    
    if (response.ok) {
        const audioBlob = await response.blob();
        const audioUrl = URL.createObjectURL(audioBlob);
        const audio = new Audio(audioUrl);
        audio.play();
    }
}
```

See [API Reference](./API.md) for more examples.

---

### Q: Can I use the API from another machine?
**A:**

Yes, change the host:

```bash
# Listen on all interfaces (not just localhost)
python run-indextts-1-5.py --host 0.0.0.0 --port 8848

# Then from another machine:
# http://<server-ip>:8848
```

**Warning:** This exposes the API publicly. Use firewall rules or authentication in production.

---

### Q: How do I set up authentication for the API?
**A:**

Use FastAPI's built-in security:

```python
from fastapi import FastAPI, Security, HTTPException
from fastapi.security import HTTPBearer

app = FastAPI()
security = HTTPBearer()

@app.post("/infer/")
async def infer(credentials: HTTPAuthorizationCredentials = Security(security)):
    if credentials.credentials != "your-secret-token":
        raise HTTPException(status_code=403, detail="Unauthorized")
    # Inference code
```

See [FastAPI Security](https://fastapi.tiangolo.com/tutorial/security/) for more.

---

## Deployment & Production

### Q: How do I deploy to production?
**A:**

See [Deployment Guide](./DEPLOYMENT.md) for Docker, Kubernetes, and cloud options.

---

### Q: Should I use Docker?
**A:**

Yes, for production:

```bash
docker build -t indextts .
docker run -p 8848:8848 --gpus all indextts
```

Benefits:
- Reproducible environment
- Easy scaling
- Cloud deployment ready

See [Deployment Guide](./DEPLOYMENT.md) for details.

---

### Q: How do I scale to handle multiple requests?
**A:**

1. **Increase workers** in API server
   ```bash
   python run-indextts-1-5.py --workers 4
   ```

2. **Use load balancer** (nginx, HAProxy)
   ```nginx
   upstream indextts {
       server localhost:8848;
       server localhost:8849;
   }
   ```

3. **Deploy with Kubernetes**
   See [Deployment Guide](./DEPLOYMENT.md)

---

### Q: What's the max file size?
**A:**

Default: 100MB (configurable)

```python
MAX_UPLOAD_SIZE = 104857600  # bytes
# In .env: MAX_UPLOAD_SIZE=104857600
```

---

## General Questions

### Q: Is this a commercial product?
**A:**

No, this is an open-source project:
- **License:** Apache 2.0
- **Original Model:** IndexTTS by Index team
- **This Fork:** Personal microservice wrapper
- **Free to use** for research and production

See [LICENSE](../LICENSE) and [INDEX_MODEL_LICENSE](../INDEX_MODEL_LICENSE).

---

### Q: Can I train my own model?
**A:**

Not with this package. To train:

1. See [original IndexTTS](https://github.com/index-tts/index-tts)
2. Or use [XTTS](https://github.com/coqui-ai/TTS) which is training-focused

This package is for inference only.

---

### Q: How do I report bugs?
**A:**

1. Check [existing issues](https://github.com/0xmichaelran/indexTTS-worker/issues)
2. Include:
   - Platform (Windows/Linux/macOS)
   - Python version
   - Error message (full traceback)
   - Steps to reproduce
3. Open issue on GitHub

---

### Q: How do I request features?
**A:**

1. Check [existing issues](https://github.com/0xmichaelran/indexTTS-worker/issues)
2. Join [discussions](https://github.com/0xmichaelran/indexTTS-worker/discussions)
3. Describe your use case

---

### Q: Can I contribute?
**A:**

Yes! See [Contributing Guide](./CONTRIBUTING.md)

---

## More Help

- **Documentation**: Start with [docs/README.md](./README.md)
- **Installation Issues**: [Installation Guide](./INSTALLATION.md)
- **Usage Examples**: [Quick Start](./QUICKSTART.md)
- **API Details**: [API Reference](./API.md)
- **Deployment**: [Deployment Guide](./DEPLOYMENT.md)
- **GitHub Issues**: https://github.com/0xmichaelran/indexTTS-worker/issues
- **Original Project**: https://github.com/index-tts/index-tts

---

## Still stuck?

1. Check if there are similar issues on GitHub
2. Read the error message carefully - often has hints
3. Try the troubleshooting sections in relevant guide
4. Open an issue with as much detail as possible
5. Join Discord or QQ group (see main README)

We're here to help! 🚀
