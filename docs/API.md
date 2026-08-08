# API Reference

Complete API documentation for IndexTTS REST API and Python library.

---

## Table of Contents

1. [REST API](#rest-api)
2. [Python Library](#python-library)
3. [Command Line Interface](#command-line-interface)
4. [Error Handling](#error-handling)
5. [Examples](#examples)

---

## REST API

### Base URL
```
http://localhost:8848
```

### Starting the Server

```bash
python run-indextts-1-5.py
```

**Interactive API Documentation**: http://localhost:8848/docs

### Endpoints

#### Health Check

```http
GET /health
```

**Response:**
```json
{
  "status": "ok",
  "version": "1.0.0",
  "platform": "Linux",
  "device": "cuda:0"
}
```

---

#### Inference

```http
POST /infer/
```

**Request:**
- `audio_prompt` (file): Reference audio file (WAV, MP3)
- `text` (string): Text to synthesize
- `language` (string, optional): Language ("zh" or "en", auto-detected if omitted)
- `temperature` (float, optional): Sampling temperature (default: 1.0)
- `top_p` (float, optional): Top-p sampling (default: 0.8)
- `repetition_penalty` (float, optional): Repetition penalty (default: 10.0)
- `max_text_tokens_per_sentence` (int, optional): Max tokens per sentence (default: 120)

**Response:**
- `audio/wav`: Generated audio in WAV format

**Example (curl):**
```bash
curl -X POST "http://localhost:8848/infer/" \
  -F "audio_prompt=@reference.wav" \
  -F "text=Hello, this is a test" \
  --output output.wav
```

**Example (Python):**
```python
import requests

files = {'audio_prompt': open('reference.wav', 'rb')}
data = {'text': 'Hello, this is a test'}

response = requests.post('http://localhost:8848/infer/', files=files, data=data)

if response.status_code == 200:
    with open('output.wav', 'wb') as f:
        f.write(response.content)
    print("✓ Audio generated")
else:
    print(f"Error: {response.status_code}")
```

**Example (JavaScript):**
```javascript
const formData = new FormData();
formData.append('audio_prompt', audioFile);  // File input element
formData.append('text', 'Hello, this is a test');

fetch('http://localhost:8848/infer/', {
    method: 'POST',
    body: formData
})
.then(response => response.blob())
.then(blob => {
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    audio.play();
})
.catch(error => console.error('Error:', error));
```

---

#### Batch Inference (Future)

```http
POST /infer/batch
```

**Request:**
```json
{
  "tasks": [
    {
      "audio_prompt": "base64_encoded_wav",
      "text": "First sentence",
      "task_id": "task-1"
    },
    {
      "audio_prompt": "base64_encoded_wav",
      "text": "Second sentence",
      "task_id": "task-2"
    }
  ]
}
```

**Response:**
```json
{
  "batch_id": "batch-123",
  "status": "processing",
  "tasks": [
    {
      "task_id": "task-1",
      "status": "completed",
      "output": "base64_encoded_wav"
    }
  ]
}
```

---

## Python Library

### Unified API

```python
from indextts import create_tts_engine

# Auto-detects platform
tts = create_tts_engine()

# macOS: Uses native TTS (lightweight)
# Windows/Linux: Uses GPU inference
```

### IndexTTS Class (GPU Inference)

```python
from indextts.infer import IndexTTS

tts = IndexTTS(
    cfg_path="checkpoints/config.yaml",
    model_dir="checkpoints",
    is_fp16=True,
    device=None  # Auto-detects
)
```

**Constructor Parameters:**
- `cfg_path` (str): Path to model config YAML
- `model_dir` (str): Path to model checkpoints directory
- `is_fp16` (bool): Use float16 precision (faster, default: True)
- `device` (str): Device to use ("cuda:0", "mps", "cpu", default: auto-detect)

**Methods:**

#### `infer()`

```python
tts.infer(
    audio_prompt: str,
    text: str,
    output_path: str,
    language: str = None,
    temperature: float = 1.0,
    top_p: float = 0.8,
    repetition_penalty: float = 10.0,
    max_text_tokens_per_sentence: int = 120,
    do_sample: bool = True,
    top_k: int = 30,
    num_beams: int = 3,
    length_penalty: float = 0.0,
    verbose: bool = False
) -> None
```

**Parameters:**
- `audio_prompt`: Path to reference audio file
- `text`: Text to synthesize
- `output_path`: Path to save generated audio
- `language`: Language code ("zh" for Chinese, "en" for English)
- `temperature`: Higher = more creative (0.5-2.0)
- `top_p`: Probability mass for nucleus sampling (0.0-1.0)
- `repetition_penalty`: Penalty for repeating tokens (0.5-20.0)
- `max_text_tokens_per_sentence`: Max tokens per sentence
- `verbose`: Print progress messages

**Example:**
```python
from indextts.infer import IndexTTS

tts = IndexTTS(
    cfg_path="checkpoints/config.yaml",
    model_dir="checkpoints"
)

tts.infer(
    audio_prompt="reference.wav",
    text="This is a test of the text-to-speech system.",
    output_path="output.wav",
    language="en",
    temperature=1.0,
    verbose=True
)
```

---

#### `infer_fast()`

Fast batch mode with 2-10x speedup for long texts.

```python
tts.infer_fast(
    audio_prompt: str,
    text: str,
    output_path: str,
    language: str = None,
    temperature: float = 1.0,
    top_p: float = 0.8,
    repetition_penalty: float = 10.0,
    max_text_tokens_per_sentence: int = 100,
    sentences_bucket_max_size: int = 4,
    verbose: bool = False
) -> None
```

**Additional Parameters:**
- `sentences_bucket_max_size`: Process multiple sentences in parallel (higher = faster but more memory)

**Example:**
```python
long_text = """
First sentence with some content.
Second sentence with more content.
Third sentence continuing the story.
And many more sentences...
"""

tts.infer_fast(
    audio_prompt="reference.wav",
    text=long_text,
    output_path="output_long.wav",
    sentences_bucket_max_size=4,
    verbose=True
)

# ~2-10x faster than infer() for long texts
```

---

### MacOSTTS Class (Native macOS)

```python
from indextts.macos_tts import MacOSTTS

tts = MacOSTTS(
    voice=None,           # Use default voice
    language="en-US"      # Language code
)
```

**Constructor Parameters:**
- `voice` (str, optional): Specific voice to use
- `language` (str): Language code (e.g., "en-US", "zh-CN")

**Methods:**

#### `list_voices()`

```python
voices = tts.list_voices(language=None)
```

**Returns:** List of available system voices

**Example:**
```python
# List all voices
all_voices = tts.list_voices()
print(all_voices)

# List English voices
en_voices = tts.list_voices(language="en")
```

---

#### `infer_to_system_audio()`

Primary method - synthesizes and plays to system speakers.

```python
tts.infer_to_system_audio(
    text: str,
    rate: float = 0.5,
    pitch: float = 1.0,
    volume: float = 1.0,
    voice: str = None
) -> None
```

**Parameters:**
- `text`: Text to synthesize and speak
- `rate`: Speaking rate (0.0-2.0, default: 0.5)
- `pitch`: Pitch adjustment (0.5-2.0, default: 1.0)
- `volume`: Volume level (0.0-1.0, default: 1.0)
- `voice`: Override voice for this utterance

**Example:**
```python
tts = MacOSTTS(language="en-US")

# Simple usage
tts.infer_to_system_audio("Hello, world!")

# With adjustments
tts.infer_to_system_audio(
    "This is important!",
    ratio=0.6,     # Slow down (0.5-2.0, 1.0=normal)
    pitch=1.2,     # Higher pitch
    volume=1.0     # Maximum volume
)
```

---

#### `infer()`

Save synthesis to file (uses system TTS).

```python
tts.infer(
    audio_prompt: str,
    text: str,
    output_path: str,
    **kwargs
) -> None
```

**Example:**
```python
tts.infer(
    audio_prompt=None,
    text="Hello, world!",
    output_path="output.wav"
)
```

---

### Factory Function

```python
from indextts import create_tts_engine

tts = create_tts_engine(
    use_native_macos: bool = None,    # Force engine choice
    voice: str = None,                 # Voice name (macOS)
    language: str = "en-US",           # Language
    **kwargs                           # Additional args
)
```

**Returns:** MacOSTTS (macOS) or IndexTTS (Windows/Linux)

**Example:**
```python
# Auto-detects platform
tts = create_tts_engine()

# Force GPU inference even on macOS
tts_gpu = create_tts_engine(use_native_macos=False, model_dir="checkpoints")

# Use specific voice on macOS
tts_voice = create_tts_engine(voice="Daniel", language="en-US")
```

---

## Command Line Interface

### Basic Usage

```bash
indextts "Text to synthesize" --voice reference.wav --output output.wav
```

### Full Options

```bash
indextts --help
```

**Output:**
```
usage: indextts [-h] [--voice VOICE] [--output OUTPUT] [--model_dir MODEL_DIR] 
                [--config CONFIG] [--device DEVICE] [--fp16] [--language LANGUAGE]
                [--temperature TEMPERATURE] [--top_p TOP_P] 
                [--repetition_penalty REPETITION_PENALTY] text

Text-to-Speech Synthesis

positional arguments:
  text                  Text to synthesize

options:
  --voice VOICE         Path to reference audio file
  --output OUTPUT       Output audio path (default: output.wav)
  --model_dir MODEL_DIR Path to model directory (default: checkpoints)
  --config CONFIG       Path to config file (default: checkpoints/config.yaml)
  --device DEVICE       Device to use (default: auto)
  --fp16                Use float16 precision
  --language LANGUAGE   Language (zh/en, default: auto)
  --temperature TEMPERATURE
                        Sampling temperature (default: 1.0)
  --top_p TOP_P         Top-p sampling (default: 0.8)
  --repetition_penalty REPETITION_PENALTY
                        Repetition penalty (default: 10.0)
```

### Examples

```bash
# Basic synthesis
indextts "Hello world" --voice reference.wav --output output.wav

# Chinese text
indextts "你好，世界" --voice reference.wav --output output_zh.wav --language zh

# With custom parameters
indextts "Test" \
  --voice reference.wav \
  --output output.wav \
  --device cuda:1 \
  --temperature 0.8 \
  --top_p 0.9

# Using different model
indextts "Text" \
  --voice reference.wav \
  --model_dir /path/to/models \
  --config /path/to/config.yaml
```

---

## Error Handling

### Common Errors and Solutions

#### Model Not Found
```
FileNotFoundError: [Errno 2] No such file or directory: 'checkpoints/gpt.pth'
```

**Solution:** Download models first
```bash
mkdir -p checkpoints
huggingface-cli download IndexTeam/IndexTTS-1.5 \
  config.yaml bigvgan_generator.pth gpt.pth bpe.model \
  --local-dir checkpoints
```

---

#### CUDA Out of Memory
```
RuntimeError: CUDA out of memory
```

**Solutions:**
1. Use CPU inference
   ```python
   tts = IndexTTS(model_dir="checkpoints", device="cpu")
   ```

2. Reduce batch size
   ```python
   tts.infer_fast(..., sentences_bucket_max_size=2)
   ```

3. Use FP32 (slower but uses less memory)
   ```python
   tts = IndexTTS(model_dir="checkpoints", is_fp16=False)
   ```

---

#### Audio File Error
```
ValueError: Audio file not found or cannot be read
```

**Solutions:**
1. Check file path
   ```python
   import os
   assert os.path.exists("reference.wav"), "File not found"
   ```

2. Check audio format (should be WAV, MP3, FLAC)
   ```bash
   ffprobe reference.wav
   ```

3. Verify audio quality
   ```python
   import librosa
   audio, sr = librosa.load("reference.wav")
   print(f"Sample rate: {sr}, Duration: {len(audio)/sr:.2f}s")
   ```

---

#### API Server Issues

**Port already in use:**
```bash
# Find process using port 8848
lsof -i :8848  # macOS/Linux
netstat -ano | findstr :8848  # Windows

# Use different port
python run-indextts-1-5.py --port 9999
```

**Connection refused:**
```bash
# Verify server is running
curl http://localhost:8848/health

# Check firewall
# Allow port 8848 in firewall settings
```

---

## Examples

### Example 1: Simple TTS

```python
from indextts import create_tts_engine

tts = create_tts_engine()

tts.infer(
    audio_prompt="speaker.wav",
    text="This is a test.",
    output_path="output.wav"
)

print("✓ Done!")
```

---

### Example 2: Batch Processing

```python
from indextts.infer import IndexTTS

tts = IndexTTS(
    model_dir="checkpoints",
    cfg_path="checkpoints/config.yaml"
)

texts = [
    "First sentence.",
    "Second sentence.",
    "Third sentence."
]

for i, text in enumerate(texts):
    output = f"output_{i:02d}.wav"
    tts.infer("speaker.wav", text, output)
    print(f"✓ Generated {output}")
```

---

### Example 3: Long Text with Fast Mode

```python
from indextts.infer import IndexTTS

tts = IndexTTS(model_dir="checkpoints", cfg_path="checkpoints/config.yaml")

long_text = """
This is the first paragraph with multiple sentences.
Here is another sentence in the same paragraph.
And one more to make it longer.
Now we move to a new paragraph.
This paragraph also has multiple sentences.
Let's add a few more for demonstration purposes.
"""

tts.infer_fast(
    audio_prompt="speaker.wav",
    text=long_text,
    output_path="long_output.wav",
    sentences_bucket_max_size=4
)
```

---

### Example 4: macOS Native TTS

```python
from indextts.macos_tts import MacOSTTS

tts = MacOSTTS(language="en-US")

# List available voices
voices = tts.list_voices(language="en")
print("Available voices:", voices)

# Speak text
tts.infer_to_system_audio("Hello, I am speaking from your Mac!")

# Use specific voice
tts.infer_to_system_audio(
    "This is a different voice",
    voice="Daniel",
    ratio=1.0  # Normal speed (0.5-2.0)
)
```

---

### Example 5: Web API Integration

```python
from fastapi import FastAPI
from fastapi.responses import FileResponse
import requests

app = FastAPI()

@app.post("/synthesize/")
async def synthesize(text: str, voice_file: str = "default.wav"):
    """
    Endpoint that calls IndexTTS API and returns audio
    """
    files = {'audio_prompt': open(voice_file, 'rb')}
    data = {'text': text}
    
    response = requests.post(
        "http://localhost:8848/infer/",
        files=files,
        data=data
    )
    
    if response.status_code == 200:
        # Save to temp file
        with open("temp_output.wav", "wb") as f:
            f.write(response.content)
        return FileResponse("temp_output.wav", media_type="audio/wav")
    else:
        raise Exception(f"TTS API error: {response.status_code}")
```

---

## Performance Tips

### GPU Optimization
```python
# Use FP16 for 2x speedup
tts = IndexTTS(model_dir="checkpoints", is_fp16=True)

# Use specific GPU
tts = IndexTTS(model_dir="checkpoints", device="cuda:0")

# Check device
print(f"Using device: {tts.device}")
```

### Batch Processing
```python
# Fast mode for multiple sentences (2-10x speedup)
tts.infer_fast(
    audio_prompt="speaker.wav",
    text="Many sentences...",
    output_path="output.wav",
    sentences_bucket_max_size=4
)
```

### Memory Management
```python
# Monitor memory usage
import torch
print(f"GPU memory: {torch.cuda.memory_allocated() / 1e9:.2f}GB")

# Clear cache between inferences
torch.cuda.empty_cache()
```

---

## References

- **REST API Framework**: [FastAPI](https://fastapi.tiangolo.com/)
- **Audio Processing**: [librosa](https://librosa.org/)
- **Deep Learning**: [PyTorch](https://pytorch.org/)
- **Model Hub**: [HuggingFace](https://huggingface.co/)
