# Modular Architecture Guide

## Quick Start

### Running the Worker

```bash
# With environment variables from .env
uv run python -m services.tts_worker

# Or directly
python services/tts_worker.py
```

The worker will:
1. Initialize all modular components
2. Connect to RabbitMQ
3. Start consuming jobs from `tts_jobs` queue
4. Process each job through the synthesis pipeline
5. Publish results to `tts_results` queue

## Using Individual Components

### StorageManager - S3 & File Operations

```python
from services.storage_manager import StorageManager

storage = StorageManager()

# Download voice recording
audio_path = storage.download_audio_prompt(
    job_id="job_123",
    audio_prompt_path="audio-prompts/voice_001.wav"
)

# Upload synthesized audio
s3_path = storage.upload_audio(
    job_id="job_123",
    local_path="/tmp/audio.wav",
    remote_path="studio/20260902/job_123/zh_r10_prod.mp3"
)

# Upload alignment JSON sidecar
alignment_path = storage.upload_alignment_json(
    job_id="job_123",
    local_parsed_json="/tmp/alignment.json",
    output_s3_path="studio/20260902/job_123/zh_r10_prod.mp3"
)

# Build S3 output path
s3_path = StorageManager.build_s3_output_path(
    job_type="studio",
    job_id="job_123",
    language="zh",
    ratio=1.0,
    environment="prod",
    voice_id=42,
    file_extension="mp3"
)
# Output: "studio/20260902/job_123/zh_r10_prod_voice42.mp3"

# Cleanup
storage.cleanup_local_files(audio_path, alignment_path)
```

### AudioProcessor - Audio Operations

```python
from services.audio_processor import AudioProcessor

# Get audio duration
duration = AudioProcessor.get_audio_duration("/tmp/audio.wav")
print(f"Audio duration: {duration:.2f}s")

# Apply time-stretching (pitch-preserving speed adjustment)
AudioProcessor.apply_time_stretch(
    audio_path="/tmp/audio.wav",
    ratio=1.2,  # 1.2x faster (1.0 = normal, 2.0 = 2x faster)
    job_id="job_123"
)

# Copy audio file to output directory
output_path = AudioProcessor.copy_audio_file(
    src_path="/tmp/audio.wav",
    job_id="job_123",
    output_dir="outputs/tts_output"
)

# Apply time-stretching to cached audio
stretched_path = AudioProcessor.apply_ratio_to_audio(
    base_audio_path="/cache/audio.wav",
    ratio=0.8,  # Slow down to 0.8x speed
    job_id="job_123",
    output_dir="outputs/tts_output"
)
```

### CacheManager - Synthesis Caching

```python
from services.cache_manager import CacheManager

cache = CacheManager(
    cache_dir="outputs/tts_cache",
    max_entries=10000,
    eviction_threshold=9000
)

# Check cache for existing synthesis
cache_hit, cached_audio_path = cache.lookup(
    job_id="job_123",
    text="你好，世界",
    audio_prompt_path="audio-prompts/voice_001.wav",
    ratio=1.0
)

if cache_hit:
    print(f"Cache hit! Using: {cached_audio_path}")
else:
    # ... perform synthesis ...
    # Store result in cache for future reuse
    cache.store(
        job_id="job_123",
        text="你好，世界",
        audio_prompt_path="audio-prompts/voice_001.wav",
        base_audio_path="/tmp/synthesized.wav",
        audio_duration=2.5,
        synthesis_duration=15.3,  # seconds
        language="zh"
    )
```

### RabbitMQManager - Queue Operations

```python
from services.rabbitmq_manager import RabbitMQManager

rmq = RabbitMQManager(rabbitmq_url="amqp://user:pass@localhost:5672/")

# Connect and setup queues
rmq.connect()

# Define message handler
def handle_message(ch, method, properties, body):
    import json
    job_data = json.loads(body)
    
    # Process job...
    result = {"status": "completed", "jobId": job_data["jobId"]}
    
    # Publish result
    rmq.publish_result(result)
    
    # Acknowledge processing
    rmq.acknowledge_message(method.delivery_tag)

# Start consuming
rmq.consume_messages(callback=handle_message, prefetch_count=1)

# Cleanup
rmq.disconnect()
```

### SynthesisPipeline - Complete TTS Workflow

```python
from services.synthesis_pipeline import SynthesisPipeline
from services.storage_manager import StorageManager
from services.cache_manager import CacheManager
from indextts.infer import create_tts_engine

# Initialize dependencies
tts_engine = create_tts_engine(cfg_path="checkpoints/config.yaml")
storage = StorageManager()
cache = CacheManager(cache_dir="outputs/tts_cache")

# Create pipeline
pipeline = SynthesisPipeline(
    tts_engine=tts_engine,
    storage_manager=storage,
    cache_manager=cache,
    use_fast_inference=True,
    normalization_enabled=True,
    normalization_target_lufs=-16.0
)

# Process a job
job_data = {
    "jobId": "job_123",
    "text": "你好，世界",
    "audioPromptPath": "audio-prompts/voice_001.wav",
    "spokenLang": "zh",
    "jobType": "studio",
    "speedRatio": 1.0,
    "environment": "prod",
    "voiceId": 42
}

result = pipeline.process_job(job_data)

# Result structure:
# {
#     "jobId": "job_123",
#     "jobType": "studio",
#     "status": "completed" or "failed",
#     "audioPath": "studio/20260902/job_123/zh_r10_prod_voice42.mp3",
#     "audioDurationSeconds": 2.5,
#     "alignmentPath": "studio/20260902/job_123/zh_r10_prod_voice42.json",
#     "alignmentDurationSeconds": 1.87,
#     "cacheHit": false,
#     "startedAt": "2026-09-02T12:30:45.123456",
#     "completedAt": "2026-09-02T12:31:05.654321",
#     "retryCount": 0
# }

if result["status"] == "completed":
    print(f"Success! Audio: {result['audioPath']}")
else:
    print(f"Failed: {result['errorMessage']}")
```

## Component Initialization Pattern

Each component follows a consistent initialization pattern:

```python
# All components initialize from environment variables
import os

# RabbitMQManager
from services.rabbitmq_manager import RabbitMQManager
rabbitmq_url = os.getenv("RABBITMQ_URL")
rmq = RabbitMQManager(rabbitmq_url)

# StorageManager
from services.storage_manager import StorageManager
storage = StorageManager()  # Reads S3_* and R2_* env vars

# CacheManager
from services.cache_manager import CacheManager
cache = CacheManager(
    cache_dir=os.getenv("TTS_CACHE_LOCAL_DIR", "outputs/tts_cache"),
    max_entries=int(os.getenv("TTS_CACHE_MAX_ENTRIES", "10000")),
    eviction_threshold=int(os.getenv("TTS_CACHE_EVICTION_THRESHOLD", "9000"))
)

# AudioProcessor (no initialization needed, uses static methods)
from services.audio_processor import AudioProcessor

# SynthesisPipeline
from services.synthesis_pipeline import SynthesisPipeline
pipeline = SynthesisPipeline(
    tts_engine=tts_engine,
    storage_manager=storage,
    cache_manager=cache,
    use_fast_inference=os.getenv("TTS_USE_FAST_INFERENCE", "true").lower() == "true",
    normalization_enabled=os.getenv("TTS_NORMALIZATION_ENABLED", "true").lower() == "true",
    normalization_target_lufs=float(os.getenv("TTS_NORMALIZATION_TARGET_LUFS", "-16.0"))
)
```

## Error Handling

### Component-Specific Errors

```python
from services.s3_config import S3ConfigError

try:
    storage.download_audio_prompt(job_id, path)
except S3ConfigError as e:
    print(f"S3 error: {e}")
    # Handle S3 failure (retryable)

from services.circuit_breaker import CircuitBreakerError

try:
    pipeline.process_job(job_data)
except CircuitBreakerError as e:
    print(f"Service unavailable: {e}")
    # Handle circuit breaker open (transient)
except Exception as e:
    print(f"Processing failed: {e}")
    # Handle job processing failure (may be permanent)
```

### DLX (Dead Letter Queue) Handling

Failed jobs are automatically routed to the DLQ:

```bash
# Query DLQ in RabbitMQ management UI
http://localhost:15672/

# Or via CLI
rabbitmqadmin get queue=tts_jobs_failed count=10

# Check failed job from worker logs
# [JOB job_123] Processing failed, sending to DLQ
```

## Configuration Best Practices

### Development Setup

```bash
# .env
RABBITMQ_URL=amqp://guest:guest@localhost:5672/
LOG_LEVEL=DEBUG
LOG_FILE_ENABLED=true
TTS_CACHE_ENABLED=false
TTS_USE_FAST_INFERENCE=false
TTS_NORMALIZATION_ENABLED=false
```

### Production Setup

```bash
# .env
RABBITMQ_URL=amqp://user:pass@rabbitmq.production.internal:5672/
LOG_LEVEL=INFO
LOG_FILE_ENABLED=true
LOG_FILE_PATH=/var/log/tts-worker.log
TTS_CACHE_ENABLED=true
TTS_USE_FAST_INFERENCE=true
TTS_NORMALIZATION_ENABLED=true
CIRCUIT_BREAKER_S3_FAILURE_THRESHOLD=3
CIRCUIT_BREAKER_TTS_FAILURE_THRESHOLD=3
```

## Testing Tips

### Unit Testing Components

```python
import pytest
from unittest.mock import Mock, patch

def test_audio_processor_duration():
    """Test audio duration detection."""
    # Create a test WAV file
    duration = AudioProcessor.get_audio_duration("test_audio.wav")
    assert duration > 0

def test_storage_manager_path_building():
    """Test S3 path construction."""
    path = StorageManager.build_s3_output_path(
        job_type="studio",
        job_id="test_123",
        language="zh",
        ratio=1.2,
        environment="prod",
        voice_id=42,
        file_extension="mp3"
    )
    
    assert "studio" in path
    assert "test_123" in path
    assert "zh_r12_prod_voice42.mp3" in path

@patch('services.storage_manager.S3Client')
def test_storage_manager_initialization(mock_s3):
    """Test storage manager initialization."""
    mock_s3.return_value.storage_bucket_name = "test-bucket"
    mock_s3.return_value.output_bucket_name = "output-bucket"
    
    storage = StorageManager()
    assert storage.s3_misc_bucket == "test-bucket"
    assert storage.r2_voice_bucket == "output-bucket"
```

### Integration Testing

```python
def test_full_pipeline():
    """Integration test: full job processing."""
    # Setup
    pipeline = SynthesisPipeline(...)
    
    job_data = {
        "jobId": "test_123",
        "text": "test",
        "audioPromptPath": "voice.wav",
        "spokenLang": "en",
        "jobType": "studio",
        "speedRatio": 1.0,
        "environment": "dev",
        "voiceId": 0
    }
    
    # Execute
    result = pipeline.process_job(job_data)
    
    # Verify
    assert result["status"] in ["completed", "failed"]
    assert "jobId" in result
    assert result["jobId"] == "test_123"
```

## Troubleshooting

### "AlignmentService unavailable"
- Check that `services/alignment.py` is available
- Verify stable-whisper is installed: `pip list | grep stable`

### "S3 client initialization failed"
- Verify S3 credentials in `.env` (S3_MISC_*, R2_VOICE_*)
- Check S3 bucket names and access permissions

### "Cache lookup failed"
- Verify database connection (check DATABASE_URL)
- Ensure alembic migrations have run: `uv run alembic upgrade head`

### "Circuit breaker open"
- Check downstream service health (S3, RabbitMQ, TTS)
- Review logs for repeated failures
- Circuit will auto-reset after timeout

## Next Steps

1. **Extend Components**: Add metrics collection to each component
2. **Add Features**: Implement component-level rate limiting
3. **Async Migration**: Convert to fully async architecture
4. **Plugin System**: Allow pluggable implementations
5. **Health Checks**: Component health endpoint
