# S3 Path Structure Migration Guide

**Effective Date**: 2026-09-02  
**Status**: 🚨 **Breaking Change**  
**Affected Services**: All services consuming TTS worker outputs (backend API, frontend, video rendering)

---

## Overview

The IndexTTS Worker has migrated to a new S3 path structure for storing TTS audio and alignment outputs. This change affects how downstream services access and reference these files.

**Migration Urgency**: High - Old path references will break after worker deployment.

---

## What Changed

### Old Path Structure (Deprecated)

```
tts-audio/studio/{job_id}.mp3
tts-audio/studio/{job_id}.json
tts-audio/playground/{job_id}.mp3
```

**Example**:
```
tts-audio/studio/abc123.mp3
tts-audio/studio/abc123.json
```

### New Path Structure (Current)

```
{job_type}/{YYYYMMDD}/{job_id}/{filename}.{ext}
```

Where `{filename}` = `{language}_r{ratio}_{environment}[_voice{voice_id}]`

**Example**:
```
studio/20260902/abc123/zh_r10_prod_voice42.mp3
studio/20260902/abc123/zh_r10_prod_voice42.json
```

---

## Path Components Explained

| Component | Description | Example Values |
|-----------|-------------|----------------|
| `{job_type}` | Job classification | `studio`, `playground` |
| `{YYYYMMDD}` | Date in local server timezone | `20260902` |
| `{job_id}` | Unique job identifier | `abc123`, `367` |
| `{language}` | Detected language from alignment | `zh`, `en`, `mixed_fallback` |
| `{ratio}` | Speed ratio (× 10, zero-padded) | `r10` (1.0), `r12` (1.2), `r07` (0.7) |
| `{environment}` | Deployment environment | `prod`, `dev`, `staging` |
| `{voice_id}` | Voice clone ID (optional) | `voice42` (if `voice_id > 0`) |
| `{ext}` | File extension | `mp3`, `wav`, `json` |

### Ratio Format Reference

| Speed Ratio | Old Format | New Format |
|-------------|------------|------------|
| 0.5 (half speed) | `ratio0-5` | `r05` |
| 0.7 | `ratio0-7` | `r07` |
| 0.8 | `ratio0-8` | `r08` |
| 1.0 (normal) | `ratio1-0` | `r10` |
| 1.2 | `ratio1-2` | `r12` |
| 1.5 | `ratio1-5` | `r15` |
| 2.0 (double speed) | `ratio2-0` | `r20` |

---

## Migration Steps by Service

### 1. Backend API (Job Result Consumer)

**Location**: RabbitMQ result queue consumer

#### Old Code (Before)
```python
# ❌ DEPRECATED - Do NOT use
result = json.loads(message.body)
audio_url = f"https://cdn.example.com/{result['audio_path']}"
# Expected: https://cdn.example.com/tts-audio/studio/abc123.mp3
```

#### New Code (After)
```python
# ✅ NEW - Use paths directly from result payload
result = json.loads(message.body)
audio_url = f"https://cdn.example.com/{result['audio_path']}"
alignment_url = f"https://cdn.example.com/{result['alignment_path']}"

# Result now contains:
# {
#   "audio_path": "studio/20260902/abc123/zh_r10_prod_voice42.mp3",
#   "alignment_path": "studio/20260902/abc123/zh_r10_prod_voice42.json"
# }
```

**Action Required**:
- ✅ No code changes needed if you use `result['audio_path']` directly
- ⚠️ Update any code that constructs S3 paths manually

---

### 2. Frontend / API Routes

**Location**: Routes that serve or redirect to TTS audio

#### Old Code (Before)
```typescript
// ❌ DEPRECATED
const audioUrl = `/api/tts/${job.job_type}/${job.job_id}.mp3`;

// Manual S3 path construction
const s3Key = `tts-audio/${job.job_type}/${job.job_id}.mp3`;
```

#### New Code (After)
```typescript
// ✅ NEW - Use paths from job result
const audioUrl = `/api/tts/${job.audio_path}`;
// Example: /api/tts/studio/20260902/abc123/zh_r10_prod_voice42.mp3

const alignmentUrl = `/api/tts/${job.alignment_path}`;
// Example: /api/tts/studio/20260902/abc123/zh_r10_prod_voice42.json
```

**Action Required**:
- ✅ Store `audio_path` and `alignment_path` in your database when job completes
- ✅ Update API routes to accept the full path
- ⚠️ Update database schema if paths are truncated (increase varchar length)

---

### 3. Video Rendering (Remotion)

**Location**: Remotion compositions fetching alignment data

#### Old Code (Before)
```typescript
// ❌ DEPRECATED
const alignmentUrl = `https://cdn.example.com/tts-audio/studio/${jobId}.json`;
```

#### New Code (After)
```typescript
// ✅ NEW - Use alignment_path from job result
interface JobResult {
  job_id: string;
  audio_path: string;
  alignment_path: string;  // NEW field
}

export const TtsVideo = ({ job }: { job: JobResult }) => {
  const [alignment, setAlignment] = useState<AlignmentData | null>(null);

  useEffect(() => {
    // Use alignment_path directly
    const url = `https://cdn.example.com/${job.alignment_path}`;
    fetch(url)
      .then(res => res.json())
      .then(data => setAlignment(data));
  }, [job.alignment_path]);

  // ... rest of component
};
```

**Action Required**:
- ✅ Update TypeScript interfaces to include `alignment_path`
- ✅ Pass `alignment_path` to video rendering compositions
- ⚠️ Update any hardcoded path construction logic

---

### 4. S3 Lifecycle Policies

**Location**: S3 bucket configuration / Infrastructure as Code

#### Old Rules (Before)
```json
{
  "Rules": [
    {
      "Id": "DeletePlaygroundAfter24h",
      "Filter": {
        "Prefix": "tts-audio/playground/"
      },
      "Expiration": {
        "Days": 1
      }
    }
  ]
}
```

#### New Rules (After)
```json
{
  "Rules": [
    {
      "Id": "DeletePlaygroundAfter24h",
      "Filter": {
        "Prefix": "playground/"
      },
      "Expiration": {
        "Days": 1
      }
    },
    {
      "Id": "DeleteOldStudioFiles",
      "Filter": {
        "Prefix": "studio/"
      },
      "Expiration": {
        "Days": 365
      }
    }
  ]
}
```

**Action Required**:
- ✅ Update S3 lifecycle policy prefixes
- ✅ Consider date-based cleanup (e.g., delete folders older than N days)
- ⚠️ Test policies in staging before production deployment

---

### 5. Monitoring & Analytics

**Location**: Log parsing, metrics collection, cost analysis

#### Old Patterns (Before)
```python
# ❌ DEPRECATED - Path parsing
s3_path = "tts-audio/studio/abc123.mp3"
parts = s3_path.split("/")
job_type = parts[1]  # "studio"
job_id = parts[2].replace(".mp3", "")  # "abc123"
```

#### New Patterns (After)
```python
# ✅ NEW - Path parsing
s3_path = "studio/20260902/abc123/zh_r10_prod_voice42.mp3"
parts = s3_path.split("/")
job_type = parts[0]        # "studio"
date_str = parts[1]        # "20260902"
job_id = parts[2]          # "abc123"
filename = parts[3]        # "zh_r10_prod_voice42.mp3"

# Extract metadata from filename
filename_parts = filename.rsplit(".", 1)[0].split("_")
language = filename_parts[0]      # "zh"
ratio_str = filename_parts[1]     # "r10"
environment = filename_parts[2]   # "prod"
voice_id = filename_parts[3] if len(filename_parts) > 3 else None  # "voice42"

# Convert ratio back to float
ratio = int(ratio_str[1:]) / 10.0  # "r10" → 1.0
```

**Action Required**:
- ✅ Update log parsing regex patterns
- ✅ Update cost analysis scripts to handle new path structure
- ✅ Update monitoring dashboards with new path filters

---

## Database Schema Changes

If you store S3 paths in your database, consider these schema updates:

### Option 1: Increase Column Length (Recommended)

```sql
-- Old: 100 characters was sufficient
-- New: 200+ characters needed for full paths

ALTER TABLE tts_jobs 
  MODIFY COLUMN audio_path VARCHAR(255);

ALTER TABLE tts_jobs 
  MODIFY COLUMN alignment_path VARCHAR(255);
```

### Option 2: Add Metadata Columns (Optional)

```sql
-- Store path components separately for easier querying
ALTER TABLE tts_jobs
  ADD COLUMN audio_date DATE,
  ADD COLUMN language VARCHAR(20),
  ADD COLUMN ratio DECIMAL(3, 1),
  ADD COLUMN environment VARCHAR(20),
  ADD COLUMN voice_id INT;

-- Index for date-based queries
CREATE INDEX idx_tts_jobs_audio_date ON tts_jobs(audio_date);
```

---

## Backward Compatibility Strategy

### Transition Period (Recommended: 2 weeks)

If you need to support both old and new paths during migration:

```python
def get_audio_url(job: dict) -> str:
    """
    Get audio URL with backward compatibility.
    Supports both old and new path formats.
    """
    audio_path = job.get("audio_path", "")
    
    # Check if it's the new format (contains date folder)
    if re.match(r"^(studio|playground)/\d{8}/", audio_path):
        # New format: use as-is
        return f"https://cdn.example.com/{audio_path}"
    
    # Old format: fallback to legacy path construction
    job_type = job.get("job_type", "studio")
    job_id = job.get("job_id")
    return f"https://cdn.example.com/tts-audio/{job_type}/{job_id}.mp3"
```

---

## Testing Checklist

Before deploying to production, verify:

- [ ] **Backend API**: Job result consumer reads new `audio_path` and `alignment_path`
- [ ] **Database**: Schema supports longer path strings (255+ chars)
- [ ] **Frontend**: API routes accept full S3 paths (not just job_id)
- [ ] **Video Rendering**: Compositions use `alignment_path` from job result
- [ ] **CDN/S3**: New path structure is accessible via public URLs
- [ ] **Lifecycle Policies**: S3 cleanup rules target correct prefixes
- [ ] **Monitoring**: Dashboards updated with new path filters
- [ ] **Analytics**: Cost analysis scripts parse new path format

### Test Cases

Create test jobs with various configurations:

```json
[
  {
    "job_type": "studio",
    "job_id": "test001",
    "language": "zh",
    "ratio": 1.0,
    "environment": "dev",
    "voice_id": 42
  },
  {
    "job_type": "playground",
    "job_id": "test002",
    "language": "en",
    "ratio": 1.5,
    "environment": "staging",
    "voice_id": 0
  }
]
```

Expected paths:
```
studio/20260902/test001/zh_r10_dev_voice42.mp3
playground/20260902/test002/en_r15_staging.mp3
```

---

## Rollback Plan

If issues arise after deployment:

1. **Worker Rollback**: Deploy previous worker version
2. **Database**: Old paths still accessible if stored
3. **S3**: Files remain accessible at both old and new locations during transition

**Note**: Once old files expire (per lifecycle policies), rollback becomes difficult. Plan migration carefully.

---

## FAQ

### Q: Can I still access old files at their original paths?

**A**: No. The worker now only generates files at the new path structure. Old files remain at their original paths until lifecycle policies delete them.

### Q: Do I need to migrate existing S3 files?

**A**: Not required. Old files can remain at their original paths. Only new jobs will use the new structure.

### Q: What if I need the old path format?

**A**: The `output_path_template` field is now ignored. If you absolutely need custom paths, modify the worker's `_build_s3_output_path()` function.

### Q: How do I extract metadata from the new paths?

**A**: Parse the filename components:
```python
# Path: studio/20260902/abc123/zh_r10_prod_voice42.mp3
# Filename: zh_r10_prod_voice42.mp3
# Parts: [language, ratio, environment, voice(optional)]
```

### Q: Does this affect voice recordings (audio prompts)?

**A**: No. Voice recordings in the storage bucket remain unchanged:
```
audio-prompts/{voice_id}.wav  # Unchanged
```

### Q: What timezone is used for the date folder?

**A**: The worker's local server timezone (not UTC). This may vary by deployment region.

---

## Support & Questions

For migration assistance or questions:

- **Documentation**: See `docs/FORCED_ALIGNMENT.md` for complete path format reference
- **Worker Code**: See `services/tts_worker.py` (`_build_s3_output_path()` function)
- **Issues**: Create a ticket in the worker repository

---

**Last Updated**: 2026-09-02  
**Document Version**: 1.0  
**Migration Deadline**: TBD (coordinate with team)
