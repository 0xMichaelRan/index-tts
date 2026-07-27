# S3 Storage Configuration Guide

This guide explains how to configure and use the S3-compatible storage system (Supabase Storage) for the IndexTTS worker service.

## Table of Contents

- [Overview](#overview)
- [Storage Structure](#storage-structure)
- [Configuration](#configuration)
- [Setup Instructions](#setup-instructions)
- [Usage Examples](#usage-examples)
- [Lifecycle Rules](#lifecycle-rules)
- [CORS Configuration](#cors-configuration)
- [Troubleshooting](#troubleshooting)

## Overview

The IndexTTS worker uses S3-compatible storage (Supabase Storage) for:
- **Audio prompts**: Voice recordings used as reference for TTS synthesis
- **TTS outputs**: Generated audio files from both studio and playground jobs
- **Logs**: Worker and backend application logs

The storage system implements:
- **Path-based organization**: Structured directory layout for easy management
- **Automatic lifecycle rules**: Cleanup of temporary files (playground outputs)
- **Presigned URLs**: Secure temporary access to files
- **Retry logic**: Automatic retry with exponential backoff for transient failures

## Storage Structure

```
s3://studio/
├── audio-prompts/
│   ├── {voice_id}.wav      # Voice recordings (indefinite retention)
│   └── {voice_id}.json     # Voice metadata
├── tts-output/
│   ├── studio/
│   │   ├── {job_id}.wav    # Studio job outputs (indefinite retention)
│   │   └── {job_id}.json   # Job metadata
│   └── playground/
│       ├── {job_id}.wav    # Playground outputs (24h retention)
│       └── {job_id}.json   # Job metadata
└── logs/
    ├── worker/             # Worker logs (archive after 30d, delete after 365d)
    └── backend/            # Backend logs (archive after 30d, delete after 365d)
```

### Path Structure Details

| Path | Purpose | Retention Policy |
|------|---------|------------------|
| `audio-prompts/` | Voice recordings for TTS | Indefinite (manual cleanup) |
| `tts-output/studio/` | Studio TTS outputs | Indefinite (manual cleanup) |
| `tts-output/playground/` | Playground TTS outputs | 24 hours (auto-delete) |
| `logs/worker/` | Worker application logs | Archive after 30d, delete after 365d |
| `logs/backend/` | Backend application logs | Archive after 30d, delete after 365d |

## Configuration

### Environment Variables

Add the following to your `services/.env` file:

```bash
# S3 Configuration (Supabase Storage S3-compatible API)
S3_ENDPOINT_URL=https://uhxdobvynuwpyzyfqccq.supabase.co/storage/v1/s3
S3_ACCESS_KEY_ID=e53700db19fdab6dacd59ed62471a7bf
S3_SECRET_ACCESS_KEY=e8579fce4c6ec67c6fcd5193719a82d0d77c2585b8145002cd326c955c8ea408
S3_BUCKET_NAME=studio
S3_REGION=ap-southeast-1
S3_USE_SSL=true
```

### Configuration Parameters

| Parameter | Description | Required | Default |
|-----------|-------------|----------|---------|
| `S3_ENDPOINT_URL` | Supabase Storage S3 endpoint | Yes | - |
| `S3_ACCESS_KEY_ID` | S3 access key | Yes | - |
| `S3_SECRET_ACCESS_KEY` | S3 secret key | Yes | - |
| `S3_BUCKET_NAME` | S3 bucket name | Yes | - |
| `S3_REGION` | AWS region | No | `us-east-1` |
| `S3_USE_SSL` | Use SSL for connections | No | `true` |

## Setup Instructions

### 1. Install Dependencies

The S3 module requires `boto3`:

```bash
# Using pip
pip install boto3

# Or add to your requirements
echo "boto3>=1.28.0" >> requirements.txt
pip install -r requirements.txt
```

### 2. Configure Environment Variables

Copy the example environment file and update with your Supabase credentials:

```bash
cd services
cp .env.example .env
# Edit .env with your actual credentials
nano .env
```

### 3. Verify Configuration

Test your S3 configuration:

```bash
cd services
python -c "from s3_config import S3Client; client = S3Client(); print('✓ S3 configuration valid')"
```

### 4. Configure Bucket Structure

Initialize the bucket structure (idempotent operation):

```bash
cd services
python -m s3_config
```

This will:
- Verify bucket accessibility
- Log the path structure
- Display lifecycle rules to configure manually

To also create placeholder files for directory structure:

```bash
python -m s3_config --create-placeholders
```

### 5. Configure Lifecycle Rules (Manual)

Supabase Storage lifecycle rules must be configured through the Supabase dashboard:

1. Log in to [Supabase Dashboard](https://app.supabase.com)
2. Navigate to **Storage** → **Buckets** → **studio**
3. Click **Policies** → **Add Policy**

**Playground Cleanup Rule:**
- **Name**: `playground-cleanup`
- **Prefix**: `tts-output/playground/`
- **Action**: Delete
- **After**: 1 day

**Log Archival Rule:**
- **Name**: `logs-archival`
- **Prefix**: `logs/`
- **Transition**: Glacier after 30 days
- **Delete**: After 365 days

> **Note**: Supabase Storage may not support all lifecycle features. Check documentation for current capabilities.

## Usage Examples

### Basic Upload/Download

```python
from services.s3_config import S3Client

# Initialize client (reads from environment)
client = S3Client()

# Upload audio file
client.upload_audio(
    local_path="/tmp/audio.wav",
    remote_path="audio-prompts/voice_123.wav",
    job_id="job_456",
    metadata={"language": "en", "duration": "5.2"}
)

# Download audio file
client.download_file(
    remote_path="audio-prompts/voice_123.wav",
    local_path="/tmp/downloaded.wav"
)

# Check if file exists
exists = client.file_exists("audio-prompts/voice_123.wav")
print(f"File exists: {exists}")

# Delete file
client.delete_file("tts-output/playground/old_job.wav")
```

### Presigned URLs

Generate temporary URLs for secure file access:

```python
from services.s3_config import S3Client

client = S3Client()

# Generate download URL (valid for 1 hour)
download_url = client.generate_presigned_url(
    remote_path="tts-output/studio/job_789.wav",
    expiration=3600,
    http_method="GET"
)

# Generate upload URL (valid for 30 minutes)
upload_url = client.generate_presigned_url(
    remote_path="audio-prompts/new_voice.wav",
    expiration=1800,
    http_method="PUT"
)
```

### List Files

```python
from services.s3_config import S3Client

client = S3Client()

# List all audio prompts
audio_files = client.list_files("audio-prompts/", max_keys=100)
print(f"Found {len(audio_files)} audio prompts:")
for file in audio_files:
    print(f"  - {file}")

# List studio outputs
studio_outputs = client.list_files("tts-output/studio/")
```

### Path Validation

```python
from services.s3_config import S3Client

client = S3Client()

# Validate path before upload
try:
    client.validate_path("audio-prompts/voice_123.wav")
    print("✓ Path is valid")
except ValueError as e:
    print(f"✗ Invalid path: {e}")

# Path traversal prevention
try:
    client.validate_path("../etc/passwd")
except ValueError:
    print("✗ Path traversal attempt blocked")
```

### Error Handling with Retry

```python
from services.s3_config import S3Client, S3ConfigError

client = S3Client(max_retries=5)

try:
    # Download with automatic retry
    client.download_file(
        remote_path="audio-prompts/voice_123.wav",
        local_path="/tmp/audio.wav",
        max_retries=3  # Override default
    )
except S3ConfigError as e:
    print(f"Download failed after retries: {e}")
```

### Using in TTS Worker

```python
from services.s3_config import S3Client
from services.rabbitmq_config import configure_queues
import tempfile
import os

# Initialize S3 client
s3_client = S3Client()

# Worker job processing
def process_tts_job(job_data):
    """Process TTS job with S3 storage."""
    job_id = job_data["job_id"]
    audio_prompt_path = job_data["audio_prompt_path"]
    
    # Download audio prompt
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        local_prompt = tmp.name
    
    try:
        # Download from S3
        s3_client.download_file(audio_prompt_path, local_prompt)
        
        # Synthesize audio (your TTS logic here)
        output_audio = synthesize_tts(job_data["text"], local_prompt)
        
        # Upload result to S3
        output_path = f"tts-output/studio/{job_id}.wav"
        s3_client.upload_audio(
            local_path=output_audio,
            remote_path=output_path,
            job_id=job_id,
            metadata={
                "language": job_data["language"],
                "voice_id": job_data["voice_id"],
            }
        )
        
        return output_path
        
    finally:
        # Cleanup temporary files
        if os.path.exists(local_prompt):
            os.unlink(local_prompt)
```

## Lifecycle Rules

### Automatic Cleanup

The system implements lifecycle rules to manage storage costs:

#### Playground Cleanup
- **Prefix**: `tts-output/playground/`
- **Expiration**: 24 hours
- **Reason**: Playground outputs are temporary demonstrations

#### Log Archival
- **Prefix**: `logs/`
- **Transition**: Glacier after 30 days (long-term storage)
- **Expiration**: 365 days
- **Reason**: Logs needed for debugging but rarely accessed after 30 days

### Manual Cleanup

For indefinite retention paths (`audio-prompts/`, `tts-output/studio/`):
- Implement application-level cleanup logic
- Use `client.list_files()` to find old files
- Use `client.delete_file()` to remove them

Example cleanup script:

```python
from services.s3_config import S3Client
from datetime import datetime, timedelta

client = S3Client()

# Find old studio outputs (older than 90 days)
cutoff_date = datetime.now() - timedelta(days=90)
studio_files = client.list_files("tts-output/studio/")

for file in studio_files:
    # Check file metadata to determine age
    # Delete if older than cutoff
    pass
```

## CORS Configuration

Configure CORS in Supabase dashboard for cross-origin access:

### Playground Audio Playback

Allow GET requests from `official-landing` domain:

```json
{
  "AllowedOrigins": ["https://official-landing.example.com"],
  "AllowedMethods": ["GET"],
  "AllowedHeaders": ["*"],
  "ExposeHeaders": ["ETag"],
  "MaxAgeSeconds": 3600
}
```

### Worker Uploads

Allow PUT requests from `indexTTS-worker`:

```json
{
  "AllowedOrigins": ["https://worker.example.com"],
  "AllowedMethods": ["PUT", "POST"],
  "AllowedHeaders": ["Content-Type", "x-amz-*"],
  "ExposeHeaders": ["ETag"],
  "MaxAgeSeconds": 3600
}
```

### Backend Verification

Allow GET requests from `studio-backend`:

```json
{
  "AllowedOrigins": ["https://studio-backend.example.com"],
  "AllowedMethods": ["GET"],
  "AllowedHeaders": ["Authorization", "Content-Type"],
  "ExposeHeaders": ["ETag"],
  "MaxAgeSeconds": 3600
}
```

## Troubleshooting

### Connection Issues

**Problem**: `Failed to connect to S3 endpoint`

**Solutions**:
1. Verify `S3_ENDPOINT_URL` is correct
2. Check network connectivity to Supabase
3. Ensure SSL is enabled (`S3_USE_SSL=true`)

```bash
# Test connectivity
curl -I https://uhxdobvynuwpyzyfqccq.supabase.co/storage/v1/s3
```

### Authentication Errors

**Problem**: `Access denied` or `403 Forbidden`

**Solutions**:
1. Verify `S3_ACCESS_KEY_ID` and `S3_SECRET_ACCESS_KEY` are correct
2. Check Supabase project settings for S3 credentials
3. Ensure bucket name matches exactly

```python
# Test authentication
from services.s3_config import S3Client

try:
    client = S3Client()
    print("✓ Authentication successful")
except Exception as e:
    print(f"✗ Authentication failed: {e}")
```

### Upload/Download Failures

**Problem**: Files fail to upload or download

**Solutions**:
1. Check file size limits (Supabase default: 50MB)
2. Verify path format is correct
3. Ensure sufficient storage quota

```python
# Test upload with small file
import tempfile
from services.s3_config import S3Client

client = S3Client()

with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp:
    tmp.write("test content")
    tmp_path = tmp.name

try:
    client.upload_file(tmp_path, "logs/worker/test.txt")
    print("✓ Upload successful")
except Exception as e:
    print(f"✗ Upload failed: {e}")
finally:
    import os
    os.unlink(tmp_path)
```

### Path Validation Errors

**Problem**: `Invalid path` errors

**Solutions**:
1. Ensure path starts with valid prefix (see PATH_STRUCTURE)
2. Remove leading slashes
3. Avoid `..` in paths

```python
# Valid paths
valid_paths = [
    "audio-prompts/voice123.wav",
    "tts-output/studio/job456.wav",
    "logs/worker/app.log"
]

# Invalid paths
invalid_paths = [
    "/audio-prompts/voice123.wav",  # Leading slash
    "../etc/passwd",                  # Path traversal
    "invalid/path.wav"                # Wrong prefix
]
```

### Boto3 Not Installed

**Problem**: `ImportError: boto3 is required`

**Solution**:
```bash
pip install boto3
```

### Performance Issues

**Problem**: Slow uploads/downloads

**Solutions**:
1. Increase `max_retries` for unstable connections
2. Use multipart upload for large files (>5MB)
3. Consider regional endpoints closer to your infrastructure

```python
# Configure for slow connections
client = S3Client(max_retries=5)
```

## Testing

### Unit Tests

Run the test suite:

```bash
# Run all S3 tests
pytest tests/test_s3_config.py -v

# Run specific test class
pytest tests/test_s3_config.py::TestS3FileOperations -v

# Run with coverage
pytest tests/test_s3_config.py --cov=services.s3_config
```

### Integration Tests

Integration tests require valid S3 credentials:

```bash
# Set environment variables
export S3_ENDPOINT_URL="https://your-endpoint.supabase.co/storage/v1/s3"
export S3_ACCESS_KEY_ID="your_key"
export S3_SECRET_ACCESS_KEY="your_secret"
export S3_BUCKET_NAME="your_bucket"

# Run integration tests
pytest tests/test_s3_config.py::TestIntegration -v
```

## Best Practices

1. **Always validate paths** before S3 operations to prevent security issues
2. **Use presigned URLs** for client-side uploads/downloads to avoid exposing credentials
3. **Implement retry logic** for production workloads (built-in with max_retries)
4. **Clean up temporary files** after upload/download operations
5. **Monitor storage costs** and implement appropriate lifecycle rules
6. **Use metadata tags** to track job information and enable auditing
7. **Handle errors gracefully** with proper logging and user feedback

## Additional Resources

- [Supabase Storage Documentation](https://supabase.com/docs/guides/storage)
- [AWS S3 API Documentation](https://docs.aws.amazon.com/s3/)
- [Boto3 Documentation](https://boto3.amazonaws.com/v1/documentation/api/latest/index.html)
- [TTS Service Integration Design](../.kiro/specs/tts-service-integration/design.md)
