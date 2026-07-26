# IndexTTS-Worker: Personal TTS Microservice

## 🎯 Overview

This is a **personal production-ready Text-to-Speech (TTS) microservice** built on top of IndexTTS-1.5, enhanced with:
- FastAPI REST API for synchronous inference
- RabbitMQ message queue integration for asynchronous tasks
- Standalone worker architecture (microservice design)
- Cloud-ready deployment support

**Note:** This is a fork of [IndexTTS](https://github.com/index-tts/index-tts) optimized for microservice deployment. It is not affiliated with or maintained by the original Index team.

## 🚀 Quick Start

### Current Features
- ✅ FastAPI REST API for instant TTS inference
- ✅ RabbitMQ task queue worker (production-ready)
- ✅ High-performance TTS inference (`indextts/infer.py`)
- ✅ Command-line interface (`indextts/cli.py`)
- ✅ Multi-language support (Chinese/English)
- ✅ GPU/CPU/MPS device support
- ✅ Horizontal scaling ready

### Service Architecture
```
Client Applications
    ↓
┌─────────────────┐
│  FastAPI REST   │  ← Synchronous requests
│  API (Port 8848)│
└─────────────────┘
    ↓
┌─────────────────┐     ┌──────────────┐
│   RabbitMQ      │────→│  TTS Worker  │  ← Async processing
│    Queue        │     └──────────────┘
└─────────────────┘
```

## Core Components

### 1. FastAPI Service (`run-indextts-1-5.py`)
- **Purpose**: REST API endpoint for synchronous TTS inference
- **Status**: Production-ready
- **Endpoint**: `POST /infer/`
- **Use case**: Direct API calls, web browser integration, low-latency applications

### 2. RabbitMQ Worker (Planned)
- **Purpose**: Asynchronous TTS processing via message queue
- **Use case**: High-throughput batch processing, decoupled systems

### 3. IndexTTS Inference Engine (`indextts/infer.py`)
- **Purpose**: Core TTS model with zero-shot voice cloning
- **Features**:
  - Fast batch inference mode (2-10x speedup)
  - Multi-language support (Chinese, English)
  - GPU/CPU/MPS acceleration
  - FP16 optimization for faster inference

### 4. Command Line Interface (`indextts/cli.py`)
- **Purpose**: Direct command-line TTS access
- **Usage**: `indextts "text" --voice reference.wav --output output.wav`

## Production Architecture (Planned)

### Current Architecture (REST API)
```
Client Request
    ↓
[FastAPI Server] → TTS Inference → Audio Response
    ↓
Local File Storage
```

### Planned Architecture (With RabbitMQ)
```
┌──────────────────┐
│  Client Services │
└────────┬─────────┘
         │
    ┌────▼─────┐
    │ FastAPI  │  ← Sync API for immediate results
    │ (8848)   │
    └──────────┘
         │
    ┌────▼──────────┐
    │ RabbitMQ      │  ← Async task queue
    │ Message Queue │
    └────┬──────────┘
         │
    ┌────▼──────────┐
    │ TTS Workers   │  ← Scalable worker pool
    │ (Multiple)    │
    └────┬──────────┘
         │
    ┌────▼──────────┐
    │ S3 Storage    │  ← Cloud file storage
    │ (Optional)    │
    └───────────────┘
```

## Current Implementation

## Available Tools

### 1. Command Line Interface (CLI)
```bash
# Install the package
pip install -e .

# Basic usage
indextts "Text to synthesize" --voice reference.wav --output output.wav

# With custom configuration
indextts "Hello world" \
  --voice prompt.wav \
  --output result.wav \
  --model_dir checkpoints \
  --config checkpoints/config.yaml \
  --fp16 \
  --device cuda:0

# Help and options
indextts --help
```

### 2. REST API Server
```bash
# Start the API service
python run-indextts-1-5.py
# Server starts at http://0.0.0.0:8848
# Interactive docs: http://localhost:8848/docs

# API Request Example
curl -X POST "http://localhost:8848/infer/" \
  -F "audio_prompt=@reference.wav" \
  -F "text=Hello, this is a test of the TTS system" \
  --output generated.wav

# Python client example
import requests

response = requests.post(
    "http://localhost:8848/infer/",
    files={"audio_prompt": open("reference.wav", "rb")},
    data={"text": "Text to synthesize"}
)
with open("output.wav", "wb") as f:
    f.write(response.content)
```

### 3. Python Library
```python
from indextts.infer import IndexTTS

# Initialize
tts = IndexTTS(
    cfg_path="checkpoints/config.yaml",
    model_dir="checkpoints",
    is_fp16=True,
    device="cuda:0"  # auto-detects if None
)

# Standard inference
tts.infer(
    audio_prompt="reference.wav",
    text="Text to synthesize",
    output_path="output.wav",
    verbose=True,
    max_text_tokens_per_sentence=120
)

# Fast inference (2-10x speedup for long texts)
tts.infer_fast(
    audio_prompt="reference.wav",
    text="Long text with multiple sentences...",
    output_path="output.wav",
    verbose=True,
    max_text_tokens_per_sentence=100,
    sentences_bucket_max_size=4
)
```

### Python Version Requirements

**Supported:** Python 3.10 or 3.11 (recommended for production)
- Python 3.10: Fully tested and stable
- Python 3.11: Fully compatible with all dependencies
- Python 3.12+: May work but not officially tested (some PyTorch features may have compatibility issues)

**Why 3.10-3.11?**
- `transformers==4.36.2` requires Python >=3.8
- `torch>=2.1.2` has best support for Python 3.10-3.11
- All dependencies fully tested on these versions

### Environment Setup

**Note:** This project now uses `pyproject.toml` for dependency management. The old `requirements.txt` is deprecated.

```bash
# 0. Create conda environment with Python 3.10 or 3.11
conda create -n index-tts python=3.10  # or python=3.11
conda activate index-tts

# 1. Install with dependencies (choose one based on your needs)
pip install -e .                    # Base dependencies only
pip install -e ".[webui]"          # Include Gradio web interface
pip install -e ".[api]"            # Include FastAPI server
pip install -e ".[all]"            # Install everything (webui + api + dev tools)

# 2. Download models (IndexTTS-1.5 recommended for production)
huggingface-cli download IndexTeam/IndexTTS-1.5 \
  config.yaml bigvgan_generator.pth bpe.model gpt.pth \
  --local-dir checkpoints

# Alternative: Use wget for direct download
mkdir -p checkpoints
cd checkpoints
wget https://huggingface.co/IndexTeam/IndexTTS-1.5/resolve/main/config.yaml
wget https://huggingface.co/IndexTeam/IndexTTS-1.5/resolve/main/bigvgan_generator.pth
wget https://huggingface.co/IndexTeam/IndexTTS-1.5/resolve/main/bpe.model
wget https://huggingface.co/IndexTeam/IndexTTS-1.5/resolve/main/gpt.pth

# 3. Test the installation
indextts "测试文本" --voice test_data/input.wav --output test.wav

# 4. Start production service
python run-indextts-1-5.py
```

## Development Roadmap

### Phase 1: REST API (✅ Current)
- [x] FastAPI endpoint for synchronous inference
- [x] Local file storage
- [x] Error handling and validation
- [x] Comprehensive documentation

### Phase 2: RabbitMQ Integration (🔄 In Progress)
- [ ] RabbitMQ consumer implementation
- [ ] Task serialization/deserialization (JSON schema)
- [ ] Worker pool management
- [ ] Task priority queues
- [ ] Dead letter queue for failed tasks
- [ ] Message acknowledgment and retry logic

### Phase 3: Monitoring & Ops
- [ ] Health check endpoints
- [ ] Prometheus metrics
- [ ] Structured logging
- [ ] Graceful shutdown

### Phase 4: Optional Cloud Integration
- [ ] S3 file storage support
- [ ] Presigned URLs for file access
- [ ] Database backend for task tracking

```json
{
  "task_id": "uuid-v4",
  "status": "pending|processing|completed|failed",
  "created_at": "ISO-8601 timestamp",
  "updated_at": "ISO-8601 timestamp",
  "metadata": {
    "audio_prompt_url": "s3://bucket/path/to/reference.wav",
    "text": "Text to synthesize",
    "language": "zh|en",
    "voice_settings": {
      "temperature": 1.0,
      "top_p": 0.8,
      "repetition_penalty": 10.0
    },
    "output_format": {
      "format": "wav",
      "sample_rate": 24000,
      "bit_depth": 16
    }
  },
  "result": {
    "audio_url": "s3://bucket/path/to/output.wav",
    "duration_seconds": 12.5,
    "processing_time_seconds": 3.2,
    "error_message": null
  }
}
### Phase 4: Scalability
- [ ] Horizontal scaling support
- [ ] Load balancing
- [ ] Auto-scaling based on queue depth

### Phase 5: Monitoring & Operations
- [ ] Comprehensive logging
- [ ] Performance metrics
- [ ] Alerting system

## Task Schema (for RabbitMQ integration)

## Configuration

### Service Configuration
Create a `.env` file in the project root:

```bash
# Model Configuration
MODEL_DIR=checkpoints
CONFIG_PATH=checkpoints/config.yaml
IS_FP16=true
USE_CUDA_KERNEL=false

# Device Configuration (auto-detects if not set)
# DEVICE=cuda:0  # NVIDIA GPU
# DEVICE=mps     # Apple Silicon
# DEVICE=cpu     # CPU only

# FastAPI Service
HOST=0.0.0.0
PORT=8848
WORKERS=4
LOG_LEVEL=info

# File Storage
UPLOAD_DIR=outputs/audio_prompt
OUTPUT_DIR=outputs/tts_output
MAX_UPLOAD_SIZE=104857600  # 100MB

# Inference Parameters (defaults)
MAX_TEXT_TOKENS_PER_SENTENCE=120
SENTENCES_BUCKET_MAX_SIZE=4
MAX_MEL_TOKENS=600
DO_SAMPLE=true
TOP_P=0.8
TOP_K=30
TEMPERATURE=1.0
REPETITION_PENALTY=10.0
LENGTH_PENALTY=0.0
NUM_BEAMS=3
```

### Production Environment Variables (Future Integration)
```bash
# RabbitMQ Configuration
RABBITMQ_HOST=localhost
RABBITMQ_PORT=5672
RABBITMQ_USERNAME=guest
RABBITMQ_PASSWORD=guest
RABBITMQ_QUEUE=tts_tasks
RABBITMQ_VHOST=/
RABBITMQ_HEARTBEAT=60

# AWS S3 Configuration
AWS_ACCESS_KEY_ID=your_access_key
AWS_SECRET_ACCESS_KEY=your_secret_key
AWS_S3_BUCKET=your-bucket-name
AWS_REGION=us-east-1
AWS_S3_ENDPOINT=  # for non-AWS S3 compatible storage

# Database Configuration (for task tracking)
DATABASE_URL=postgresql://user:password@localhost:5432/tts_db
REDIS_URL=redis://localhost:6379/0

# Monitoring
PROMETHEUS_PORT=9090
JAEGER_ENDPOINT=http://localhost:14268/api/traces

# Security
API_KEY=your_api_key_here
RATE_LIMIT=100  # requests per minute
```

### Model Configuration
- Default model: IndexTTS-1.5 (latest version)
- Supports both Chinese and English
- Optimized for zero-shot voice cloning
- Fast inference mode available

## Performance Considerations

### Inference Speed
- **Fast mode**: ~2-10x speedup for long texts
- **Batch processing**: Multiple sentences in parallel
- **GPU acceleration**: CUDA/MPS support

### Resource Requirements
- **GPU**: 8GB+ VRAM recommended
- **CPU**: 4+ cores for preprocessing
- **Memory**: 16GB+ RAM
- **Storage**: 10GB+ for models and cache

## Error Handling

### Common Error Scenarios
1. **Model loading failures**: Check model files and permissions
2. **Memory exhaustion**: Reduce batch size or use CPU
3. **Invalid input**: Validate audio format and text encoding
4. **Network issues**: Retry logic for external dependencies

### Recovery Strategies
- Automatic retry with exponential backoff
- Dead letter queue for failed tasks
- Health check endpoints for monitoring
- Graceful degradation when resources are limited

## Security Considerations

### Input Validation
- Validate audio file formats (WAV, MP3)
- Sanitize text input to prevent injection
- Limit file size uploads
- Rate limiting per client/IP

### Data Privacy
- Temporary file cleanup
- Secure S3 bucket policies
- Encryption at rest and in transit
- Compliance with data protection regulations

## Deployment

### Production Docker Container
```dockerfile
FROM nvidia/cuda:12.1-runtime-ubuntu22.04

WORKDIR /app

# Install system dependencies (Python 3.10 or 3.11 recommended)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsndfile1 \
    python3.10 \
    python3-pip \
    python3.10-venv \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment with Python 3.10
RUN python3.10 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy requirements
COPY pyproject.toml MANIFEST.in ./

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -e ".[api]"

# Install IndexTTS package
COPY . .
RUN pip install --no-cache-dir -e .

# Create directories
RUN mkdir -p checkpoints outputs/audio_prompt outputs/tts_output

# Download models (optional - can be mounted as volume)
# RUN huggingface-cli download IndexTeam/IndexTTS-1.5 \
#     config.yaml bigvgan_generator.pth bpe.model gpt.pth \
#     --local-dir checkpoints

# Non-root user
RUN useradd -m -u 1000 -s /bin/bash appuser && \
    chown -R appuser:appuser /app
USER appuser

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8848/health || exit 1

EXPOSE 8848
CMD ["python", "run-indextts-1-5.py"]
```

### Docker Compose for Development
```yaml
version: '3.8'

services:
  indextts-api:
    build: .
    ports:
      - "8848:8848"
    environment:
      - MODEL_DIR=/app/checkpoints
      - CONFIG_PATH=/app/checkpoints/config.yaml
      - IS_FP16=true
      - DEVICE=cuda:0
    volumes:
      - ./checkpoints:/app/checkpoints
      - ./outputs:/app/outputs
      - ./logs:/app/logs
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    restart: unless-stopped

  # Future services (planned)
  # rabbitmq:
  #   image: rabbitmq:3-management
  #   ports:
  #     - "5672:5672"
  #     - "15672:15672"
  #
  # indextts-worker:
  #   build: .
  #   command: python worker.py
  #   depends_on:
  #     - rabbitmq
  #   environment:
  #     - RABBITMQ_HOST=rabbitmq
  #     - MODEL_DIR=/app/checkpoints
  #   volumes:
  #     - ./checkpoints:/app/checkpoints
```

### Kubernetes Deployment
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: indextts-worker
spec:
  replicas: 3
  selector:
    matchLabels:
      app: indextts
  template:
    metadata:
      labels:
        app: indextts
    spec:
      containers:
      - name: indextts
        image: indextts:latest
        env:
        - name: RABBITMQ_HOST
          value: "rabbitmq-service"
        - name: AWS_S3_BUCKET
          valueFrom:
            secretKeyRef:
              name: aws-credentials
              key: bucket
        resources:
          limits:
            memory: "16Gi"
            nvidia.com/gpu: 1
```

## Contributing

This is a personal project. While it's open source, contributions are welcome via GitHub issues and discussions.

## Author

**Michael Ran** - [@0xmichaelran](https://github.com/0xmichaelran)

**Acknowledgments:**
- Original IndexTTS-1.5 model: [Index team](https://github.com/index-tts/index-tts)
- This project extends their work for microservice deployment

## License

Apache-2.0 - See [LICENSE](LICENSE) file for details.

Model usage may be subject to additional terms in [INDEX_MODEL_LICENSE](INDEX_MODEL_LICENSE).

---

**Repository:** [indexTTS-worker](https://github.com/0xmichaelran/indexTTS-worker)

*A personal TTS microservice with FastAPI and RabbitMQ integration, built on IndexTTS-1.5.*
