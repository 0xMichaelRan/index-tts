# Dual-Bucket S3 Configuration Guide

## Overview

The IndexTTS worker requires **completely independent S3 configurations** for storage and output buckets, matching the architecture of the studio-backend. This allows using different S3 providers, regions, credentials, and settings for each bucket.

## Benefits

### 1. Provider Flexibility
- Use AWS S3 for voices, DigitalOcean Spaces for TTS output
- Mix providers based on performance, cost, or location
- Easy to migrate between providers per bucket

### 2. Cost Optimization
- Premium storage for valuable voice recordings (infrequent access)
- Cheap storage for temporary TTS output (high volume writes)
- Different storage classes per bucket

### 3. Security & Compliance
- Separate credentials per bucket (better security)
- Independent access controls and audit logs
- Different retention/lifecycle policies

### 4. Performance
- Optimize endpoint selection per bucket (latency, throughput)
- Independent connection pools
- Region-specific optimizations

## Required Configuration

Set **all** of these environment variables:

```bash
# Storage Bucket Configuration (voices, audio prompts)
S3_STORAGE_ENDPOINT_URL=https://...
S3_STORAGE_ACCESS_KEY_ID=...
S3_STORAGE_SECRET_ACCESS_KEY=...
S3_STORAGE_BUCKET_NAME=voice-library
S3_STORAGE_REGION=ap-southeast-1
S3_STORAGE_USE_SSL=true

# Output Bucket Configuration (TTS synthesis results)
S3_OUTPUT_ENDPOINT_URL=https://...
S3_OUTPUT_ACCESS_KEY_ID=...
S3_OUTPUT_SECRET_ACCESS_KEY=...
S3_OUTPUT_BUCKET_NAME=tts-output
S3_OUTPUT_REGION=us-east-1
S3_OUTPUT_USE_SSL=true
```

## Configuration Examples

### Example 1: Same Provider, Different Buckets (Most Common)

Use the same S3 provider for both buckets:

```bash
# Both on Supabase
S3_STORAGE_ENDPOINT_URL=https://project.storage.supabase.co/storage/v1/s3
S3_STORAGE_ACCESS_KEY_ID=your-key
S3_STORAGE_SECRET_ACCESS_KEY=your-secret
S3_STORAGE_BUCKET_NAME=voice-library
S3_STORAGE_REGION=ap-southeast-1
S3_STORAGE_USE_SSL=true

S3_OUTPUT_ENDPOINT_URL=https://project.storage.supabase.co/storage/v1/s3
S3_OUTPUT_ACCESS_KEY_ID=your-key
S3_OUTPUT_SECRET_ACCESS_KEY=your-secret
S3_OUTPUT_BUCKET_NAME=tts-output
S3_OUTPUT_REGION=ap-southeast-1
S3_OUTPUT_USE_SSL=true
```

### Example 2: AWS S3 (Same Provider, Different Buckets)

```bash
# Storage bucket
S3_STORAGE_ENDPOINT_URL=https://s3.ap-southeast-1.amazonaws.com
S3_STORAGE_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE
S3_STORAGE_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
S3_STORAGE_BUCKET_NAME=company-voices
S3_STORAGE_REGION=ap-southeast-1
S3_STORAGE_USE_SSL=true

# Output bucket
S3_OUTPUT_ENDPOINT_URL=https://s3.us-east-1.amazonaws.com
S3_OUTPUT_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE
S3_OUTPUT_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
S3_OUTPUT_BUCKET_NAME=company-tts-output
S3_OUTPUT_REGION=us-east-1
S3_OUTPUT_USE_SSL=true
```

### Example 3: Mixed Providers (Advanced)

Use different S3 providers per bucket:

```bash
# Storage on AWS S3 (premium, reliable)
S3_STORAGE_ENDPOINT_URL=https://s3.ap-southeast-1.amazonaws.com
S3_STORAGE_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE
S3_STORAGE_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
S3_STORAGE_BUCKET_NAME=voices
S3_STORAGE_REGION=ap-southeast-1
S3_STORAGE_USE_SSL=true

# Output on DigitalOcean Spaces (cheaper, high write throughput)
S3_OUTPUT_ENDPOINT_URL=https://nyc3.digitaloceanspaces.com
S3_OUTPUT_ACCESS_KEY_ID=DO00ABC9XYZ...
S3_OUTPUT_SECRET_ACCESS_KEY=SecretKey123...
S3_OUTPUT_BUCKET_NAME=tts-results
S3_OUTPUT_REGION=nyc3
S3_OUTPUT_USE_SSL=true
```

## Code Changes

### S3Client API

The `S3Client` requires a `bucket_type` parameter in all methods:

```python
from services.s3_config import S3Client

client = S3Client()

# Download from storage bucket (voices)
client.download_file(
    remote_path="audio-prompts/voice_123.wav",
    local_path="/tmp/voice.wav",
    bucket_type="storage"  # Required
)

# Upload to output bucket (TTS results)
client.upload_file(
    local_path="/tmp/result.wav",
    remote_path="tts-output/studio/job_456.wav",
    bucket_type="output"  # Required
)

# Generate presigned URL
url = client.generate_presigned_url(
    remote_path="audio-prompts/voice_001.wav",
    bucket_type="storage",
    expiration=3600
)
```

**Valid bucket_type values:**
- `"storage"` - Storage bucket (voices, audio prompts)
- `"output"` - Output bucket (TTS synthesis results)

## Testing

### 1. Test Dual-Bucket Detection

```python
from services.s3_config import S3Client

client = S3Client()

print(f"Storage bucket: {client.storage_bucket_name}")
print(f"Output bucket: {client.output_bucket_name}")
```

### 2. Test Download from Storage Bucket

```bash
# Set up dual-bucket .env
# Then run worker
uv run python services/tts_worker.py
```

Check logs for:
```
INFO: Dual-bucket mode initialized
INFO:   Storage bucket: voice-library (https://provider-a.com/s3)
INFO:   Output bucket:  tts-output (https://provider-b.com/s3)
```

### 3. Test End-to-End

1. Upload a voice recording to storage bucket via backend
2. Create a TTS job
3. Worker downloads from storage bucket (voices)
4. Worker uploads TTS result to output bucket
5. Backend retrieves result from output bucket

### 4. Run Test Suite

```bash
# Run all S3-related tests
uv run pytest tests/pytest/test_s3_config.py -v
uv run pytest tests/test_idempotent_upload.py -v

# Run with coverage
uv run pytest tests/pytest/test_s3_config.py --cov=services.s3_config
```

## Integration with Studio Backend

Both the worker and backend must use dual-bucket configurations. Ensure they're aligned:

| Purpose | Backend Variable | Worker Variable | Must Match |
|---------|-----------------|-----------------|------------|
| Storage bucket name | `S3_STORAGE_BUCKET_NAME` | `S3_STORAGE_BUCKET_NAME` | ✓ Yes |
| Storage endpoint | `S3_STORAGE_ENDPOINT_URL` | `S3_STORAGE_ENDPOINT_URL` | ✓ Yes |
| Storage credentials | `S3_STORAGE_ACCESS_KEY_*` | `S3_STORAGE_ACCESS_KEY_*` | ✓ Yes |
| Output bucket name | `S3_OUTPUT_BUCKET_NAME` | `S3_OUTPUT_BUCKET_NAME` | ✓ Yes |
| Output endpoint | `S3_OUTPUT_ENDPOINT_URL` | `S3_OUTPUT_ENDPOINT_URL` | ✓ Yes |
| Output credentials | `S3_OUTPUT_ACCESS_KEY_*` | `S3_OUTPUT_ACCESS_KEY_*` | ✓ Yes |

**Both services must use the same physical S3 buckets and have compatible credentials to access them.**

## Troubleshooting

### Issue: Missing environment variables

**Error**: `Missing required dual-bucket S3 configuration`

**Solution**: Check that **all** dual-bucket variables are set:
```bash
echo $S3_STORAGE_ENDPOINT_URL
echo $S3_STORAGE_BUCKET_NAME
echo $S3_OUTPUT_ENDPOINT_URL
echo $S3_OUTPUT_BUCKET_NAME
# All should return values
```

### Issue: Worker can't download audio prompts

**Cause**: Storage bucket credentials incorrect

**Solution**: Verify storage bucket access:
```bash
aws s3 ls s3://voice-library --endpoint-url $S3_STORAGE_ENDPOINT_URL
```

### Issue: Worker can't upload TTS results

**Cause**: Output bucket credentials incorrect

**Solution**: Verify output bucket access:
```bash
aws s3 ls s3://tts-output --endpoint-url $S3_OUTPUT_ENDPOINT_URL
```

### Issue: Permission denied errors

**Cause**: Credentials don't have required permissions

**Solution**: Ensure both credentials have appropriate permissions:
- Storage bucket credentials: Read access required
- Output bucket credentials: Write access required

## Files Modified

- **`services/s3_config.py`** - Dual-bucket support with separate clients (legacy mode removed)
- **`services/tts_worker.py`** - Updated to specify `bucket_type="storage"` for downloads
- **`services/idempotent_upload.py`** - Updated to use `bucket_type="output"` for uploads
- **`.env.example`** - Dual-bucket configuration template

## Next Steps

1. **Update `.env`**: Add all dual-bucket variables
2. **Test locally**: Verify worker can access both buckets
3. **Deploy**: Update production `.env` and restart worker
4. **Monitor logs**: Confirm dual-bucket mode is active (check startup logs)

## Questions?

- Check `AGENTS.md` for environment variable reference
- See `docs/S3_DUAL_BUCKET_GUIDE.md` in studio-backend for backend integration
- See `DUAL_BUCKET_VERIFICATION.md` for verification procedures
