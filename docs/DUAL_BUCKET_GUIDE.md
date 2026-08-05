# Dual S3 Bucket Configuration Guide - IndexTTS Worker

## Quick Reference - Path Standardization

**Key Rules** (applies to both backend and worker):
- ✓ Voice recordings: Storage Bucket → `voice-recordings/`
- ✓ Voice prompts: Storage Bucket → `audio-prompts/`
- ✓ **ALL TTS output: Output Bucket → `tts-audio/`**
- ✓ Thumbnails: Output Bucket → `thumbnails/`

## Overview

The IndexTTS Worker supports **separate S3 buckets** for storage and output, allowing independent management of:
- **Storage Bucket**: Voice recordings, audio prompts (read-only during synthesis)
- **Output Bucket**: TTS synthesis results (write-only during synthesis)

Each bucket can have:
- Different S3 endpoints (e.g., different regions, providers)
- Separate credentials (access keys)
- Independent regions and SSL settings
- Different retention policies
- Independent scaling based on load

## Why Separate Buckets?

### Separation of Concerns
- **Storage bucket**: Stable, infrequently accessed, long-term retention (voice library)
- **Output bucket**: High-throughput writes, variable retention, transient data (TTS results)

### Cost Optimization
- Route high-volume TTS output to cheaper storage
- Keep voice assets on premium, low-latency storage
- Use different storage classes per bucket

### Scalability
- Scale storage bucket independently for voice catalog growth
- Scale output bucket for TTS throughput spikes
- Use different CDN/caching per bucket

### Compliance & Security
- Separate access controls per bucket
- Different lifecycle policies (e.g., output auto-deletes after 24h for playground)
- Audit trails per bucket type

## Configuration

### Setup: Dual-Bucket Mode (Required)

Set **all** of these variables in `.env` file in project root:

```bash
# Storage Bucket (voice recordings, audio prompts - READ access)
S3_STORAGE_ENDPOINT_URL=https://storage-region.example.com/s3
S3_STORAGE_ACCESS_KEY_ID=storage_key_123
S3_STORAGE_SECRET_ACCESS_KEY=storage_secret_abc
S3_STORAGE_BUCKET_NAME=bucket-name
S3_STORAGE_REGION=ap-southeast-1
S3_STORAGE_USE_SSL=true

# Output Bucket (TTS results - WRITE access)
S3_OUTPUT_ENDPOINT_URL=https://output-region.example.com/s3
S3_OUTPUT_ACCESS_KEY_ID=output_key_456
S3_OUTPUT_SECRET_ACCESS_KEY=output_secret_def
S3_OUTPUT_BUCKET_NAME=bucket-name
S3_OUTPUT_REGION=us-west-2
S3_OUTPUT_USE_SSL=true
```

**All variables are required** - the worker will fail to start if any are missing.

## Environment Variables Reference

### Storage Bucket (for voice recordings, audio prompts)

| Variable | Required | Default | Example |
|----------|----------|---------|---------|
| `S3_STORAGE_ENDPOINT_URL` | **Yes** | None | `https://storage.example.com/s3` |
| `S3_STORAGE_ACCESS_KEY_ID` | **Yes** | None | `key_abc123` |
| `S3_STORAGE_SECRET_ACCESS_KEY` | **Yes** | None | `secret_xyz789` |
| `S3_STORAGE_BUCKET_NAME` | **Yes** | None | `voice-library` |
| `S3_STORAGE_REGION` | No | `us-east-1` | `ap-southeast-1` |
| `S3_STORAGE_USE_SSL` | No | `true` | `true` or `false` |

### Output Bucket (for TTS results)

| Variable | Required | Default | Example |
|----------|----------|---------|---------|
| `S3_OUTPUT_ENDPOINT_URL` | **Yes** | None | `https://output.example.com/s3` |
| `S3_OUTPUT_ACCESS_KEY_ID` | **Yes** | None | `key_def456` |
| `S3_OUTPUT_SECRET_ACCESS_KEY` | **Yes** | None | `secret_uvw123` |
| `S3_OUTPUT_BUCKET_NAME` | **Yes** | None | `tts-output` |
| `S3_OUTPUT_REGION` | No | `us-east-1` | `us-west-2` |
| `S3_OUTPUT_USE_SSL` | No | `true` | `true` or `false` |

## File Organization

### Storage Bucket (Read-Only During Synthesis)

```
voice-library/  (or your S3_STORAGE_BUCKET_NAME)
├── voice-recordings/
│   ├── user/{user_id}/{uuid}.webm    # User voice recordings
│   ├── stock/{uuid}.webm             # Stock voices
│   └── ...
└── audio-prompts/
    ├── voice_001.wav                 # Voice prompts for synthesis
    ├── voice_002.wav
    └── ...
```

### Output Bucket (Write-Only During Synthesis)

```
tts-output/  (or your S3_OUTPUT_BUCKET_NAME)
├── tts-audio/
│   ├── studio/
│   │   ├── job_123.mp3               # Worker uploads TTS results here
│   │   ├── job_456.mp3
│   │   └── ...
│   └── playground/
│       ├── job_789.mp3               # Temporary TTS (24h retention)
│       └── ...
└── thumbnails/
    └── ...
```

**Path Format Standard**:
- Studio TTS: `tts-audio/studio/{job_id}.mp3` (not `studio/{job_id}.wav`)
- Playground TTS: `tts-audio/playground/{job_id}.mp3` (not `playground/{job_id}.wav`)

## Worker Behavior

### During Job Processing

1. **Download phase** (from storage bucket):
   - Downloads voice prompt: `audio-prompts/{voice_id}.wav`
   - Uses `bucket_type="storage"` parameter

2. **Synthesis phase**:
   - Generates audio locally using IndexTTS engine
   - No S3 access during synthesis

3. **Upload phase** (to output bucket):
   - Formats path from template: `tts-audio/studio/{job_id}.mp3`
   - Uploads result: `tts-audio/studio/{job_id}.mp3` or `tts-audio/playground/{job_id}.mp3`
   - Uses `bucket_type="output"` parameter

### Bucket Routing Logic

```python
# In S3Client methods, bucket_type parameter determines routing:
s3_client.download_file(
    remote_path="audio-prompts/voice_001.wav",
    local_path="/tmp/prompt.wav",
    bucket_type="storage"  # Routes to storage bucket
)

s3_client.upload_file(
    local_path="/tmp/result.wav",
    remote_path="studio/job_123.wav",
    bucket_type="output"   # Routes to output bucket
)
```

## Examples

### Example 1: Supabase Storage (Same Provider, Two Buckets)

```bash
# Both buckets on same Supabase project, different bucket names
S3_STORAGE_ENDPOINT_URL=https://abcdef.supabase.co/storage/v1/s3
S3_STORAGE_ACCESS_KEY_ID=supabase_key_123
S3_STORAGE_SECRET_ACCESS_KEY=supabase_secret_xyz
S3_STORAGE_BUCKET_NAME=bucket-name
S3_STORAGE_REGION=ap-southeast-1
S3_STORAGE_USE_SSL=true

S3_OUTPUT_ENDPOINT_URL=https://abcdef.supabase.co/storage/v1/s3
S3_OUTPUT_ACCESS_KEY_ID=supabase_key_123
S3_OUTPUT_SECRET_ACCESS_KEY=supabase_secret_xyz
S3_OUTPUT_BUCKET_NAME=bucket-name
S3_OUTPUT_REGION=ap-southeast-1
S3_OUTPUT_USE_SSL=true
```

**Use case**: Simple setup with one provider, separate buckets for organization.

### Example 2: AWS S3 + DigitalOcean Spaces (Different Providers)

```bash
# Voice storage on AWS S3 (premium, low-latency)
S3_STORAGE_ENDPOINT_URL=https://s3.ap-southeast-1.amazonaws.com
S3_STORAGE_ACCESS_KEY_ID=aws_key_123
S3_STORAGE_SECRET_ACCESS_KEY=aws_secret_xyz
S3_STORAGE_BUCKET_NAME=bucket-name
S3_STORAGE_REGION=ap-southeast-1
S3_STORAGE_USE_SSL=true

# TTS output on DigitalOcean Spaces (cheaper, high throughput)
S3_OUTPUT_ENDPOINT_URL=https://nyc3.digitaloceanspaces.com
S3_OUTPUT_ACCESS_KEY_ID=do_spaces_key
S3_OUTPUT_SECRET_ACCESS_KEY=do_spaces_secret
S3_OUTPUT_BUCKET_NAME=tts-results
S3_OUTPUT_REGION=nyc3
S3_OUTPUT_USE_SSL=true
```

**Use case**: Optimize costs by using cheaper storage for high-volume TTS output.

### Example 3: MinIO for Development (Local Storage)

```bash
# Storage on MinIO
S3_STORAGE_ENDPOINT_URL=http://127.0.0.1:9000
S3_STORAGE_ACCESS_KEY_ID=minioadmin
S3_STORAGE_SECRET_ACCESS_KEY=minioadmin
S3_STORAGE_BUCKET_NAME=bucket-name
S3_STORAGE_REGION=us-east-1
S3_STORAGE_USE_SSL=false

# Output on MinIO (different bucket)
S3_OUTPUT_ENDPOINT_URL=http://127.0.0.1:9000
S3_OUTPUT_ACCESS_KEY_ID=minioadmin
S3_OUTPUT_SECRET_ACCESS_KEY=minioadmin
S3_OUTPUT_BUCKET_NAME=bucket-name
S3_OUTPUT_REGION=us-east-1
S3_OUTPUT_USE_SSL=false
```

**Use case**: Local development without cloud dependencies.

## Testing

### Test Configuration

```bash
# Start the worker with dry-run or debug logging
LOG_LEVEL=DEBUG uv run worker.py
```

Check startup logs:

```
18:30:45 [INFO    ] 
═══════════════════════════════════════════════════════════════════════════
                              STARTUP
═══════════════════════════════════════════════════════════════════════════
...
18:30:46 [SUCCESS ] S3 client initialized
18:30:46 [INFO    ]   Storage bucket: voice-library (https://storage.example.com/s3)
18:30:46 [INFO    ]   Output bucket:  tts-output (https://output.example.com/s3)
...
```

### Test Bucket Access

```python
# Test script: test_s3_access.py
from services.s3_config import S3Client

client = S3Client()

# Test storage bucket read access
try:
    files = client.list_files("audio-prompts/", bucket_type="storage")
    print(f"✓ Storage bucket accessible: {len(files)} voice prompts found")
except Exception as e:
    print(f"✗ Storage bucket error: {e}")

# Test output bucket write access
try:
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("test")
        test_path = f.name
    
    client.upload_file(
        local_path=test_path,
        remote_path="test/worker_access_test.txt",
        bucket_type="output"
    )
    print("✓ Output bucket writable")
    
    # Cleanup
    client.delete_file("test/worker_access_test.txt", bucket_type="output")
    import os
    os.unlink(test_path)
except Exception as e:
    print(f"✗ Output bucket error: {e}")
```

## Troubleshooting

### Issue: "Missing required dual-bucket S3 configuration"

**Cause**: One or more S3 environment variables are not set.

**Solution**:
1. Verify `.env` file exists in project root
2. Check all required variables are set:
   ```bash
   cat .env | grep S3_STORAGE
   cat .env | grep S3_OUTPUT
   ```
3. Ensure no typos in variable names (must match exactly)
4. Restart worker after updating `.env`

### Issue: Permission errors when downloading voice prompts

**Cause**: Storage bucket credentials lack read permission.

**Solution**:
1. Verify credentials with AWS CLI or S3 browser
2. Check IAM/bucket policy allows `s3:GetObject` on `audio-prompts/*`
3. Test with presigned URL generation:
   ```python
   url = client.generate_presigned_url(
       "audio-prompts/voice_001.wav",
       bucket_type="storage"
   )
   print(url)  # Test in browser
   ```

### Issue: Permission errors when uploading TTS results

**Cause**: Output bucket credentials lack write permission.

**Solution**:
1. Check IAM/bucket policy allows `s3:PutObject` on output bucket
2. Verify credentials are correct for output bucket
3. Test upload manually:
   ```bash
   aws s3 cp test.txt s3://tts-output/test/ \
     --endpoint-url https://your-output-endpoint \
     --profile output
   ```

### Issue: "Circuit breaker opened" for S3 operations

**Cause**: Multiple consecutive S3 failures (5 for storage, 3 for output).

**Solution**:
1. Check S3 endpoint connectivity:
   ```bash
   curl -I https://your-endpoint-url/
   ```
2. Verify bucket exists and is accessible
3. Check network/firewall rules
4. Wait for circuit breaker reset timeout (60s for storage, 30s for output)
5. Circuit breaker will auto-recover after timeout

### Issue: Worker processes jobs but uploads to wrong bucket

**Cause**: Bucket type parameter missing or incorrect in code.

**Solution**: This shouldn't happen with the current implementation, but verify:
```python
# Always specify bucket_type explicitly
client.download_file(..., bucket_type="storage")  # For voices
client.upload_file(..., bucket_type="output")     # For TTS results
```

## Performance Considerations

- **Connection pooling**: Each bucket gets its own boto3 client with separate connection pools
- **Parallel downloads/uploads**: Circuit breakers operate independently per bucket
- **No overhead**: Dual-bucket mode has negligible performance impact compared to single-bucket
- **Retry logic**: Exponential backoff (2, 4, 8 seconds) per bucket

## Security Best Practices

1. **Separate credentials**: Use different access keys per bucket if possible
2. **Least privilege**: 
   - Storage bucket: Grant only `s3:GetObject`, `s3:ListBucket`
   - Output bucket: Grant only `s3:PutObject`, `s3:DeleteObject`
3. **Rotation**: Rotate credentials regularly per bucket
4. **Monitoring**: Monitor access patterns per bucket separately
5. **Encryption**: Enable encryption at rest per bucket if supported

## Coordination with Backend

The IndexTTS Worker must coordinate with the studio-backend for consistent bucket usage:

### Variable Naming Differences

**Worker** (this project):
- Storage: `S3_STORAGE_*` variables
- Output: `S3_OUTPUT_*` variables

**Backend** (studio-backend):
- Storage: `S3_STORAGE_*` variables (same)
- Output: `S3_OUTPUT_*` variables (same)

✓ **Both use the same variable names** - no translation needed!

### Bucket Name Consistency

Ensure bucket names match between worker and backend:

```bash
# Worker .env
S3_STORAGE_BUCKET_NAME=bucket-name
S3_OUTPUT_BUCKET_NAME=bucket-name

# Backend .env (should match)
S3_STORAGE_BUCKET_NAME=bucket-name
S3_OUTPUT_BUCKET_NAME=bucket-name
```

If bucket names don't match, worker won't find voice prompts or backend won't find TTS results.

## Migration from Single-Bucket (Legacy)

**Note**: The worker no longer supports legacy single-bucket mode. You must configure dual-bucket mode.

If migrating from an older version:

1. Create two S3 buckets (or use existing bucket as one of them)
2. Set all `S3_STORAGE_*` and `S3_OUTPUT_*` environment variables
3. Optionally migrate existing files to appropriate buckets:
   - `audio-prompts/*` → Storage bucket
   - `tts-output/*` → Output bucket
4. Update `.env` file
5. Restart worker

The worker will fail to start if dual-bucket variables are not set, preventing accidental misconfiguration.

## Quick Reference

### Download Voice Prompt (Storage Bucket)

```python
prompt_path = worker._download_audio_prompt(
    voice_id="voice_001",
    s3_path="audio-prompts/voice_001.wav"
)
# Routes to: S3_STORAGE_BUCKET_NAME
```

### Upload TTS Result (Output Bucket)

```python
s3_path = worker._upload_to_s3_idempotent(
    local_path="/tmp/result.wav",
    job_id="job_123",
    remote_path="studio/job_123.wav"
)
# Routes to: S3_OUTPUT_BUCKET_NAME
```

### Generate Presigned URL

```python
# Storage bucket (voice preview)
url = s3_client.generate_presigned_url(
    "audio-prompts/voice_001.wav",
    bucket_type="storage"
)

# Output bucket (TTS result download)
url = s3_client.generate_presigned_url(
    "studio/job_123.wav",
    bucket_type="output"
)
```

## Further Reading

- `.env.example` - Configuration template
- `services/s3_config.py` - S3 client implementation
- `services/idempotent_upload.py` - Upload integrity verification
- `services/circuit_breaker.py` - Resilience patterns
