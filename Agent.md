# IndexTTS Production Service

## 🎯 Repository Purpose

This repository provides a **production-ready Text-to-Speech (TTS) service** based on the IndexTTS model. Unlike the demo web interface (`webui.py`), this service is designed for:

1. **Scalable TTS processing** with FastAPI and message queue integration
2. **Asynchronous task handling** via RabbitMQ (planned)
3. **Cloud storage integration** with S3 for audio file management
4. **Industrial-grade TTS inference** with IndexTTS-1.5 model

## 🚀 Quick Start for Production

### Current Production Features
- ✅ FastAPI REST API (`run-indextts-1-5.py`)
- ✅ High-performance TTS inference (`indextts/infer.py`)
- ✅ Command-line interface (`indextts/cli.py`)
- ✅ Batch processing and fast inference modes
- ✅ Multi-language support (Chinese/English)
- ✅ GPU/CPU/MPS device support

### Planned Production Features
- 🔄 RabbitMQ task queue integration
- 🔄 S3 cloud storage for audio files
- 🔄 Task status tracking and monitoring
- 🔄 Horizontal scaling support
- 🔄 Comprehensive logging and metrics

## 📋 Service Overview

This is a production TTS service that can be deployed as:
1. **Standalone REST API** (current implementation)
2. **Message queue worker** (planned - consumes from RabbitMQ)
3. **Cloud-native microservice** (planned - Kubernetes deployment)

## Core Components

### 1. FastAPI Service (`run-indextts-1-5.py`)
- **Purpose**: REST API endpoint for synchronous TTS inference
- **Endpoint**: `POST /infer/`
- **Input**: Audio prompt file + text
- **Output**: Generated audio file
- **Features**:
  - Handles file uploads and processing
  - Timestamped output file naming
  - Error handling with HTTP status codes
  - Local file storage for processed audio
  - Uses fast inference mode for better performance

### 2. IndexTTS Inference Engine (`indextts/infer.py`)
- **Purpose**: Core TTS model with fast inference capabilities
- **Features**:
  - Zero-shot voice cloning
  - Fast batch inference mode (2-10x speedup)
  - Multi-language support (Chinese, English)
  - GPU/CPU/MPS device support
  - FP16 optimization for faster inference
  - Automatic device detection (CUDA → MPS → CPU)

### 3. Command Line Interface (`indextts/cli.py`)
- **Purpose**: Direct command-line access to TTS functionality
- **Usage**: `indextts "text" --voice reference.wav --output output.wav`
- **Features**:
  - Simple text-to-speech conversion
  - Device auto-detection
  - Configurable model paths
  - Overwrite protection with `--force` flag

## Production Architecture (Planned)

### Target Architecture
```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Client     │────▶│ RabbitMQ    │────▶│ IndexTTS    │────▶│ S3 Bucket   │
│  Services   │     │  Queue      │     │  Worker     │     │  Storage    │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
                          │                      │
                          └──────────────────────┘
                                 Status Updates
```

### Components to Build

1. **RabbitMQ Consumer Service**
   - Consume TTS tasks from RabbitMQ queues
   - Parse task payloads (audio URL/text)
   - Handle retry logic and error queuing

2. **S3 Storage Integration**
   - Upload generated audio files to S3
   - Generate presigned URLs for access
   - Manage file lifecycle and cleanup

3. **Task Status Management**
   - Update task status (pending, processing, completed, failed)
   - Store metadata in database (PostgreSQL/Redis)
   - Provide status query endpoints

4. **Monitoring & Metrics**
   - Prometheus metrics for inference latency
   - Health check endpoints
   - Log aggregation (ELK stack)

## Current Implementation

### Available Tools

#### 1. Command Line Interface (CLI)
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

#### 2. FastAPI Service (Production Ready)
```bash
# Start the API service
python run-indextts-1-5.py
# Server starts at http://0.0.0.0:8848

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

#### 3. Python Library
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

```bash
# 0. Create conda environment with Python 3.10 or 3.11
conda create -n index-tts python=3.10  # or python=3.11
conda activate index-tts

# 1. Install with all dependencies
pip install -e ".[webui]"  # includes gradio for web interface

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

### Phase 1: Basic Service (Current)
- [x] FastAPI endpoint for synchronous inference
- [x] Local file storage
- [x] Error handling

### Phase 2: Message Queue Integration
- [ ] RabbitMQ consumer implementation
- [ ] Task serialization/deserialization (JSON schema)
- [ ] Worker pool management
- [ ] Task priority queues
- [ ] Dead letter queue for failed tasks
- [ ] Message acknowledgment and retry logic

### Phase 3: Cloud Storage
- [ ] S3 client integration
- [ ] File upload with metadata
- [ ] CDN integration for delivery
- [ ] File lifecycle management
- [ ] Access control with presigned URLs

### Task Schema (for RabbitMQ integration)

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
```

### Phase 4: Scalability
- [ ] Horizontal scaling support
- [ ] Load balancing
- [ ] Auto-scaling based on queue depth

### Phase 5: Monitoring & Operations
- [ ] Comprehensive logging
- [ ] Performance metrics
- [ ] Alerting system

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
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir "fastapi[standard]" uvicorn[standard]

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

### Development Setup
1. Fork the repository
2. Create feature branch
3. Implement changes with tests
4. Submit pull request

### Testing
- Unit tests for core functions
- Integration tests with mock services
- Performance benchmarks
- Load testing for scalability

## Support

- **Issues**: GitHub issue tracker
- **Documentation**: This file and inline code comments
- **Community**: Discord server (link in README.md)

## License

See [LICENSE](LICENSE) file for details. Model usage may be subject to additional terms in [INDEX_MODEL_LICENSE](INDEX_MODEL_LICENSE).

---

*This document describes the production service architecture. For model details and demo usage, see [README.md](README.md).*
