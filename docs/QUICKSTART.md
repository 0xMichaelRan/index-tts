# Quick Start Guide

Get IndexTTS up and running in 5 minutes.

---

## 1. Installation (2-3 minutes)

### macOS (Lightweight Native TTS)
```bash
python3 -m venv indexTTS-env
source indexTTS-env/bin/activate
pip install -e ".[mac,worker]"
```

### Windows/Linux (Full GPU Inference)
```bash
conda create -n indexTTS python=3.10
conda activate indexTTS
pip install -e ".[cuda,worker]"

# Download models (5-10 min, one time)
mkdir -p checkpoints
huggingface-cli download IndexTeam/IndexTTS-1.5 \
  config.yaml bigvgan_generator.pth bpe.model gpt.pth \
  --local-dir checkpoints
```

**➜ Detailed setup**: See [Installation Guide](./INSTALLATION.md)

---

## 2. Run REST API Service (1 minute)

```bash
python run-indextts-1-5.py
```

Server starts at: `http://localhost:8848`

**Interactive API docs**: Open http://localhost:8848/docs in your browser

---

## 3. Basic Usage

### Option A: Use REST API

```bash
# Synthesize text with reference voice
curl -X POST "http://localhost:8848/infer/" \
  -F "audio_prompt=@reference_voice.wav" \
  -F "text=Hello, this is a test" \
  --output output.wav
```

### Option B: Use Command Line

```bash
indextts "Hello, this is a test" \
  --voice reference_voice.wav \
  --output output.wav
```

### Option C: Use Python Library

```python
from indextts import create_tts_engine

# Create engine (auto-detects platform)
tts = create_tts_engine()

# Synthesize
tts.infer(
    audio_prompt="reference_voice.wav",
    text="Hello, this is a test",
    output_path="output.wav"
)

print("✓ Audio saved to output.wav")
```

---

## 4. Common Tasks

### List API Endpoints
```bash
# Visit interactive docs
http://localhost:8848/docs

# Or curl for health check
curl http://localhost:8848/health
```

### Use Python Library (Recommended)

```python
from indextts.infer import IndexTTS

# Initialize (GPU inference - Windows/Linux)
tts = IndexTTS(
    model_dir="checkpoints",
    cfg_path="checkpoints/config.yaml",
    device="cuda:0"  # or "cpu", "mps"
)

# Synthesize
tts.infer(
    audio_prompt="reference.wav",
    text="中文文本或 English text",
    output_path="output.wav"
)
```

### macOS Native TTS (Lightweight)

```python
from indextts.macos_tts import MacOSTTS

# Initialize macOS native TTS
tts = MacOSTTS(voice=None, language="en-US")

# Speak to system audio
tts.infer_to_system_audio("Hello world!")

# Or save to file
tts.infer(None, "Hello world", "output.wav")

# List available voices
voices = tts.list_voices()
print(voices)
```

### Use Different Language

```python
from indextts.infer import IndexTTS

tts = IndexTTS(model_dir="checkpoints", cfg_path="checkpoints/config.yaml")

# Chinese
tts.infer("reference.wav", "你好，欢迎使用", "output_zh.wav")

# English  
tts.infer("reference.wav", "Hello, welcome to use", "output_en.wav")

# Mixed (Chinese + English)
tts.infer("reference.wav", "Hello 你好", "output_mixed.wav")
```

---

## 5. Example Code

### Simple TTS Synthesis

```python
from indextts import create_tts_engine

# Auto-detects your platform
tts = create_tts_engine()

# Synthesize
tts.infer(
    audio_prompt="reference_voice.wav",
    text="This is a test of the text-to-speech system",
    output_path="output.wav",
    verbose=True
)

print("Done! Check output.wav")
```

### Batch Processing

```python
from indextts.infer import IndexTTS

tts = IndexTTS(model_dir="checkpoints", cfg_path="checkpoints/config.yaml")

texts = [
    "First sentence.",
    "Second sentence.",
    "Third sentence."
]

for i, text in enumerate(texts):
    tts.infer(
        audio_prompt="reference.wav",
        text=text,
        output_path=f"output_{i}.wav"
    )
    print(f"✓ Generated {i+1}/{len(texts)}")
```

### Fast Batch Mode (2-10x Speedup)

```python
from indextts.infer import IndexTTS

tts = IndexTTS(model_dir="checkpoints", cfg_path="checkpoints/config.yaml")

# Fast mode for long texts
long_text = """
This is a longer text with multiple sentences.
You can see significant speedup with the fast mode.
Especially for texts with many sentences.
"""

tts.infer_fast(
    audio_prompt="reference.wav",
    text=long_text,
    output_path="output_fast.wav"
)
```

### Web API Usage (Python)

```python
import requests
import sys

# Upload reference voice and text
with open("reference.wav", "rb") as f:
    files = {"audio_prompt": f}
    data = {"text": "Hello from the API!"}
    
    response = requests.post(
        "http://localhost:8848/infer/",
        files=files,
        data=data
    )
    
    if response.status_code == 200:
        with open("api_output.wav", "wb") as out:
            out.write(response.content)
        print("✓ Audio saved from API")
    else:
        print(f"Error: {response.status_code}")
        print(response.text)
```

### Web API Usage (JavaScript/Node.js)

```javascript
const FormData = require('form-data');
const fs = require('fs');
const axios = require('axios');

async function synthesize() {
    const form = new FormData();
    form.append('audio_prompt', fs.createReadStream('reference.wav'));
    form.append('text', 'Hello from JavaScript!');
    
    try {
        const response = await axios.post(
            'http://localhost:8848/infer/',
            form,
            { headers: form.getHeaders(), responseType: 'arraybuffer' }
        );
        
        fs.writeFileSync('output.wav', response.data);
        console.log('✓ Audio saved!');
    } catch (error) {
        console.error('Error:', error.message);
    }
}

synthesize();
```

---

## 6. Troubleshooting

### Import Error: "No module named 'indextts'"
```bash
# Make sure you're in the virtual environment
source indexTTS-env/bin/activate  # or conda activate indexTTS

# Reinstall package
pip install -e ".[mac,worker]"  # macOS
pip install -e ".[cuda,worker]"  # Windows/Linux
```

### API Not Starting
```bash
# Check if port 8848 is in use
lsof -i :8848  # macOS/Linux
netstat -ano | findstr :8848  # Windows

# Use different port
python run-indextts-1-5.py --port 9999
```

### CUDA Out of Memory
```python
# Use CPU instead
from indextts.infer import IndexTTS
tts = IndexTTS(model_dir="checkpoints", device="cpu")

# Or reduce batch size
tts.infer_fast(..., sentences_bucket_max_size=2)
```

### Models Not Found
```bash
# Download models first
mkdir -p checkpoints
huggingface-cli download IndexTeam/IndexTTS-1.5 \
  config.yaml bigvgan_generator.pth bpe.model gpt.pth \
  --local-dir checkpoints
```

### Permission Denied on macOS
```bash
# Add executable permission
chmod +x run-indextts-1-5.py
```

---

## 7. Next Steps

- **Full Installation Guide** → [INSTALLATION.md](./INSTALLATION.md)
- **API Reference** → [API.md](./API.md)
- **Configuration** → [CONFIGURATION.md](./CONFIGURATION.md)
- **Deployment** → [DEPLOYMENT.md](./DEPLOYMENT.md)
- **Performance Tips** → [PERFORMANCE.md](./PERFORMANCE.md)
- **Troubleshooting** → [FAQ.md](./FAQ.md)

---

## Tips & Tricks

### Use with Audio Tools
```bash
# Convert output to MP3 (requires ffmpeg)
ffmpeg -i output.wav output.mp3

# Play audio immediately (macOS/Linux)
ffplay output.wav

# Check audio properties
ffprobe output.wav
```

### Monitor GPU Usage
```bash
# Windows/Linux with NVIDIA GPU
watch -n 0.1 nvidia-smi

# macOS with Activity Monitor
activity_monitor  # or use Console.app
```

### Profile Performance
```python
import time
from indextts.infer import IndexTTS

tts = IndexTTS(model_dir="checkpoints", cfg_path="checkpoints/config.yaml")

start = time.time()
tts.infer("reference.wav", "Some text", "output.wav")
elapsed = time.time() - start

print(f"Inference time: {elapsed:.2f}s")
print(f"Throughput: {len('Some text') / elapsed:.2f} tokens/sec")
```

---

## Getting Help

- **Questions**: Check [FAQ.md](./FAQ.md)
- **Bugs**: Report on [GitHub Issues](https://github.com/0xmichaelran/indexTTS-worker/issues)
- **Discussions**: Join [GitHub Discussions](https://github.com/0xmichaelran/indexTTS-worker/discussions)

---

**Ready to go?** Follow the installation steps at the top, then try the examples above! 🚀
