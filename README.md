# IndexTTS - High-Quality Text-to-Speech Synthesis

IndexTTS is a state-of-the-art text-to-speech synthesis system that supports both IndexTTS v1.5 and v2.0 models with advanced voice cloning capabilities.

## 🚀 Quick Start

### System Requirements
- Linux/Windows/macOS
- Python 3.11+
- CUDA 11.8+ (for GPU acceleration)
- 8GB+ RAM (16GB+ recommended for v2.0)

### Installation

```bash
# Linux system setup
apt update
apt install ffmpeg git-lfs
git lfs install
git clone git@github.com/0xMichaelRan/index-tts.git
cd index-tts

# uv environment setup
curl -LsSf https://astral.sh/uv/install.sh | sh
uv venv --python 3.11
uv pip install -r requirements.txt
uv run --no-project python --version
uv pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu118
```

### Model Setup

#### IndexTTS v2.0 (Default - Latest)
```bash
# Download v2.0 model
uv tool install "modelscope"
modelscope download --model IndexTeam/IndexTTS-2 --local_dir checkpoints_v20

# OR using HuggingFace
uv tool install "huggingface_hub[cli]"
hf download IndexTeam/IndexTTS-2 --local-dir=checkpoints_v20
```

#### IndexTTS v1.5 (Legacy)
```bash
# Download v1.5 model
modelscope download --model IndexTeam/IndexTTS-1.5 --local_dir checkpoints_v15

# OR using HuggingFace
hf download IndexTeam/IndexTTS-1.5 --local-dir=checkpoints_v15
```

## 🎯 Usage

### API Server (Recommended)

**Run IndexTTS v2.0 (default):**
```bash
# Default settings
uv run python main.py

# Custom host/port
uv run python main.py --host 127.0.0.1 --port 8000
```

**Run IndexTTS v1.5:**
```bash
uv run python main.py --version v1.5
```

**Access the API:**
- Interactive docs: `http://localhost:8848/docs`
- API endpoints: `/infer_v2/` (v2.0) or `/infer_v15/` (v1.5)

### Command Line Interface

```bash
# IndexTTS v1.5 CLI
uv run python -m indextts.cli "Hello world" -v examples/voice.wav -o output.wav

# With custom model directory
uv run python -m indextts.cli "Hello world" -v examples/voice.wav -o output.wav --model_dir checkpoints_v15
```

### Web UI (Legacy)

```bash
# Test official webui (v1.5)
source .venv/bin/activate
python webui.py

# Legacy run script (v1.5)
python run-indextts-1-5.py
```

## 📊 Model Comparison

| Feature | IndexTTS v1.5 | IndexTTS v2.0 |
|---------|---------------|---------------|
| **Stability** | ✅ High | ✅ High |
| **Performance** | ✅ Good | ✅ Excellent |
| **Emotional Control** | ❌ Limited | ✅ Advanced |
| **Duration Control** | ❌ No | ✅ Yes |
| **Resource Usage** | ✅ Lower | ⚠️ Higher |
| **Recommended Use** | Production/Legacy | Latest Features |
| **CLI Support** | ✅ Full | ⚠️ Limited |
| **API Endpoint** | `/infer_v15/` | `/infer_v2/` |

## 🔧 API Usage Examples

### Using curl

**IndexTTS v2.0:**
```bash
curl -X POST "http://localhost:8848/infer_v2/" \
  -H "Content-Type: multipart/form-data" \
  -F "audio_prompt=@examples/voice_01.wav" \
  -F "text=Hello, this is IndexTTS v2.0!"
```

**IndexTTS v1.5:**
```bash
curl -X POST "http://localhost:8848/infer_v15/" \
  -H "Content-Type: multipart/form-data" \
  -F "audio_prompt=@examples/voice_01.wav" \
  -F "text=Hello, this is IndexTTS v1.5!"
```

### Using Python

```python
import requests

# IndexTTS v2.0
url = "http://localhost:8848/infer_v2/"
files = {"audio_prompt": open("examples/voice_01.wav", "rb")}
data = {"text": "Hello, this is a test!"}
response = requests.post(url, files=files, data=data)

# Save the audio response
with open("output.wav", "wb") as f:
    f.write(response.content)
```

## 📁 Directory Structure

After setup, your directory structure should look like:

```
index-tts/
├── main.py                 # API server (supports both versions)
├── webui.py               # Web UI (v1.5 only)
├── run-indextts-1-5.py    # Legacy run script (v1.5)
├── checkpoints_v20/        # IndexTTS v2.0 model files
│   ├── config.yaml
│   ├── bpe.model
│   └── ... (other model files)
├── checkpoints_v15/        # IndexTTS v1.5 model files
│   ├── config.yaml
│   ├── bpe.model
│   └── ... (other model files)
├── examples/               # Example audio files
├── outputs/                # Generated output files
│   ├── audio_prompt/       # Temporary uploaded files
│   └── tts_output/         # Generated audio files
├── indextts/              # Core library
│   ├── cli.py             # Command line interface
│   ├── infer.py           # IndexTTS v1.5 inference
│   ├── infer_v2.py        # IndexTTS v2.0 inference
│   └── utils/
├── tests/                 # Test files
├── docs/                  # Version-specific documentation
└── requirements.txt       # Python dependencies
```

## 🛠️ Command Line Options

### API Server (`main.py`)
- `--version`: Model version (`v1.5` or `v2.0`, default: `v2.0`)
- `--host`: Host to bind to (default: `0.0.0.0`)
- `--port`: Port to bind to (default: `8848`)

### CLI (`indextts.cli`)
- `text`: Text to synthesize (required)
- `-v, --voice`: Path to audio prompt file (required)
- `-o, --output_path`: Output wav file path (default: `gen.wav`)
- `-c, --config`: Config file path (default: `checkpoints_v15/config.yaml`)
- `--model_dir`: Model directory (default: `checkpoints_v15`)
- `--fp16`: Use FP16 for inference
- `-f, --force`: Force overwrite output file
- `-d, --device`: Device (cpu, cuda, mps, xpu)

## 🔍 Troubleshooting

### Common Issues

1. **Model not found error:**
   ```bash
   # Ensure model files are in correct directory
   ls checkpoints_v20/config.yaml  # for v2.0
   ls checkpoints_v15/config.yaml  # for v1.5
   ```

2. **CUDA/GPU issues:**
   ```bash
   # Check GPU availability
   uv run python -c "import torch; print(torch.cuda.is_available())"
   ```

3. **Port already in use:**
   ```bash
   # Use different port
   uv run python main.py --port 8849
   ```

4. **Slow performance:**
   - Ensure GPU acceleration is enabled
   - Use FP16 for lower VRAM usage
   - Check PyTorch CUDA installation

### Performance Tips

- **IndexTTS v1.5**: Use `infer_fast()` for optimized inference
- **IndexTTS v2.0**: Use `use_cuda_kernel=True` for faster processing
- **Both**: Enable `use_fp16=True` for lower memory usage

## 📚 Documentation

- **[docs/README_MAIN.md](docs/README_MAIN.md)** - Detailed API server documentation
- **[docs/README_v20.md](docs/README_v20.md)** - IndexTTS v2.0 specific guide
- **[docs/README_v15.md](docs/README_v15.md)** - IndexTTS v1.5 specific guide
- **[docs/README_v20_zh.md](docs/README_v20_zh.md)** - 中文文档 (Chinese)

## Quick start:

```
uv run python main.py
```

Or

```
uv run python main.py --version v1.5
```