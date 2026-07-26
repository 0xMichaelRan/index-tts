# TTS Service Integration Design

## Architecture Overview

The TTS service integration uses an asynchronous, message-driven architecture across three repositories:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                       official-landing (Next.js)                        │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │ Playground Component                                             │  │
│  │  - Text input (max 500 chars)                                    │  │
│  │  - Audio prompt selector                                         │  │
│  │  - Language selector (mandatory)                                 │  │
│  │  - SSE stream subscription for real-time updates                 │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└────────────────────────────┬──────────────────────────────────────────┘
                             │ HTTP POST /api/v1/playground/tts
                             │ (anonymous, rate-limited 5/IP/hour)
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    studio-backend (FastAPI)                             │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │ TTS API Layer                                                    │  │
│  │  - POST /api/v1/tts (authenticated, voice catalog)               │  │
│  │  - POST /api/v1/playground/tts (anonymous, rate-limited)         │  │
│  │  - GET /api/v1/tts/{job_id} (status check)                       │  │
│  │  - GET /api/v1/tts/{job_id}/stream (SSE updates)                 │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │ Database Layer                                                   │  │
│  │  - TTS_Job (job_id, user_id, voice_id, text, status, etc.)       │  │
│  │  - Voice Catalog (voice_id, s3_url, language, owner, etc.)       │  │
│  │  - Rate Limit Counter (ip_address, request_count, window)        │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │ Consumer (RabbitMQ Results)                                      │  │
│  │  - Subscribes to tts_results queue                               │  │
│  │  - Updates TTS_Job records with: status, s3_url, duration, etc.  │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└────────────────────────────┬──────────────────────────────────────────┘
                             │ RabbitMQ
                             │ tts_jobs & tts_results
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                  indexTTS-worker (Python Service)                       │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │ TTS Worker                                                       │  │
│  │  - Consumes tts_jobs queue (prefetch_count=1)                    │  │
│  │  - Validates job (required fields, audio_prompt_url)             │  │
│  │  - Downloads audio prompt from S3 (with retry)                   │  │
│  │  - Synthesizes audio using IndexTTS or macOS TTS                 │  │
│  │  - Uploads result to S3                                          │  │
│  │  - Publishes to tts_results queue                                │  │
│  │  - Acknowledges message to RabbitMQ                              │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└────────────────────────────┬──────────────────────────────────────────┘
                             │ S3 (audio storage)
                             ▼
                    ┌──────────────────┐
                    │   AWS S3 Bucket  │
                    │  tts-output/     │
                    └──────────────────┘
```

---

## Data Models

### 1. PlaygroundTTSJob (studio-backend - new)

```python
class PlaygroundTTSJob(Base):
    __tablename__ = "playground_tts_jobs"
    
    # Identifiers
    id: str                          # UUID primary key
    
    # Input
    text: str                        # Text to synthesize (max 500 chars)
    audio_prompt_id: str             # Foreign key to PlaygroundAudioPrompt
    language: str                    # Language code (e.g., "en", "zh")
    
    # Processing
    status: str                      # pending, processing, completed, failed, rate_limited
    retry_count: int                 # Number of retries attempted
    
    # Results
    output_s3_url: Optional[str]     # S3 path to generated audio
    audio_duration_seconds: Optional[float]  # Duration of generated audio
    error_message: Optional[str]     # Error message if failed
    
    # Metadata (for platform abuse analysis)
    client_ip_address: str           # IP address (required for all playground jobs)
    user_agent: Optional[str]        # User-Agent header for analytics
    referrer: Optional[str]          # Referrer URL
    correlation_id: str              # For tracing request flow
    
    # Timestamps
    created_at: datetime             # Job creation time (UTC)
    completed_at: Optional[datetime] # Job completion time (UTC)
    expires_at: datetime             # Fixed expiration (created_at + 30 days)
```

### 1b. UserTTSJob (studio-backend - new)

```python
class UserTTSJob(Base):
    __tablename__ = "user_tts_jobs"
    
    # Identifiers
    id: str                          # UUID primary key
    user_id: str                     # Foreign key to User table
    project_id: str                  # Foreign key to Project table
    
    # Input
    text: str                        # Text to synthesize (max 5000 chars)
    voice_id: str                    # Foreign key to Voice table
    language: str                    # Language code (e.g., "en", "zh")
    
    # Processing
    status: str                      # pending, processing, completed, failed
    retry_count: int                 # Number of retries attempted
    
    # Results
    output_s3_url: Optional[str]     # S3 path to generated audio
    audio_duration_seconds: Optional[float]  # Duration of generated audio
    error_message: Optional[str]     # Error message if failed
    
    # Metadata
    is_cached: bool                  # True if result from cache
    correlation_id: str              # For tracing request flow
    
    # Timestamps
    created_at: datetime             # Job creation time (UTC)
    completed_at: Optional[datetime] # Job completion time (UTC)
```

### 2. Voice (studio-backend - existing)

```python
class Voice(Base):
    __tablename__ = "voices"
    
    id: str                          # UUID primary key
    name: str                        # Voice name
    language: str                    # Language code
    created_by_user_id: str          # Owner (NULL for community voices)
    s3_url: str                      # S3 URL to voice recording
    is_public: bool                  # True for community voices
    is_deleted: bool                 # Soft delete flag
    created_at: datetime
    updated_at: datetime
```

### 3. Playground_AudioPrompt (studio-backend - new)

```python
class PlaygroundAudioPrompt(Base):
    __tablename__ = "playground_audio_prompts"
    
    id: str                          # UUID primary key
    prompt_id: str                   # Unique identifier (e.g., "en_neutral")
    language: str                    # Language code
    s3_url: str                      # S3 URL to prompt audio file
    description: str                 # Description (e.g., "English Neutral")
    created_at: datetime
```

### 4. RateLimitCounter (studio-backend - new)

```python
class RateLimitCounter(Base):
    __tablename__ = "rate_limit_counters"
    
    id: str                          # UUID primary key
    ip_address: str                  # Client IP
    request_count: int               # Number of requests in window
    window_start: datetime           # Start of 1-hour window
    window_end: datetime             # End of 1-hour window
    created_at: datetime
```

## Infrastructure Configuration

### S3 Bucket Strategy
- **Single S3 bucket** shared across all repositories
- **Path structure**: 
  - Playground audio: `s3://bucket/tts-output/playground/{job_id}/{timestamp}.{format}`
  - User audio: `s3://bucket/tts-output/users/{job_id}/{timestamp}.{format}`
  - Audio prompts: `s3://bucket/audio-prompts/{prompt_id}.{format}`
- **Expiration policies**:
  - Playground audio: 24 hours (lifecycle rule)
  - User audio: indefinite
- **CORS configuration**: Allow GET from official-landing domain for playground audio playback

### RabbitMQ Configuration
- **Connection**: Both indexTTS-worker and studio-backend read `RABBITMQ_URL` from environment variables
- **Queues**: 
  - `tts_jobs` (durable, TTL=24h)
  - `tts_results` (durable, TTL=7d)

### Audio Format
- **Default format**: Use IndexTTS default output format (typically WAV)
- No custom format specifications required
- Worker outputs whatever IndexTTS produces natively

### Logging Infrastructure (indexTTS-worker)
- **Strategy**: Structured logging to stdout/stderr
- **Integration**: Compatible with ELK Stack, Grafana Loki, or Prometheus exporters
- **Required fields**: timestamp, log_level, job_id, correlation_id, worker_instance_id, operation, duration_ms
- **No database**: Worker is stateless; all job state tracking happens in studio-backend

---

## API Specifications

### 1. Playground TTS Endpoint

#### POST /api/v1/playground/tts

**Purpose:** Create anonymous TTS job for playground demonstration

**Authentication:** None (public)

**Rate Limit:** 5 requests per IP per hour

**Request:**
```json
{
  "text": "Hello, this is a test of the TTS system.",
  "audio_prompt_id": "en_neutral",
  "language": "en"
}
```

**Validation:**
- `text`: Required, max 500 chars, non-empty
- `audio_prompt_id`: Required, must exist in PlaygroundAudioPrompt table
- `language`: Required, must match prompt language and be in supported_languages config

**Responses:**

```
202 Accepted
{
  "job_id": "uuid-1234",
  "status": "pending",
  "stream_url": "/api/v1/tts/{job_id}/stream",
  "expires_at": "2024-12-25T12:00:00Z"
}
```

```
400 Bad Request
{
  "error": "text_too_long",
  "message": "Text must be 500 characters or less"
}
```

```
429 Too Many Requests
{
  "error": "rate_limit_exceeded",
  "message": "Maximum 5 requests per hour from your IP",
  "retry_after": 3600
}
```

---

### 2. Authenticated TTS Endpoint

#### POST /api/v1/tts

**Purpose:** Create TTS job with authenticated user's voice recording

**Authentication:** Bearer token (required)

**Request:**
```json
{
  "project_id": "proj-456",
  "text": "This is the full script for my video project.",
  "voice_id": "voice-789",
  "language": "en"
}
```

**Validation:**
- `project_id`: Required, user must own project or be a collaborator
- `text`: Required, max 5000 chars
- `voice_id`: Required, must exist in Voice table
  - If voice is private (not owned by user) → 403 Forbidden
  - If voice doesn't exist → 404 Not Found
- `language`: Required, must match voice language and be supported

**Cache Check:**
- If (voice_id, text) pair exists with successful job from last 30 days → return existing S3 URL immediately

**Responses:**

```
202 Accepted
{
  "job_id": "uuid-2345",
  "status": "pending",
  "stream_url": "/api/v1/tts/{job_id}/stream",
  "is_cached": false,
  "created_at": "2024-12-25T10:00:00Z"
}
```

```
200 OK (from cache)
{
  "job_id": "uuid-cached",
  "status": "completed",
  "stream_url": "/api/v1/tts/{job_id}/stream",
  "audio_duration_seconds": 45.5,
  "is_cached": true,
  "cached_from_date": "2024-12-10T08:00:00Z"
}
```

```
404 Not Found
{
  "error": "voice_not_found",
  "message": "Voice with ID 'voice-789' not found"
}
```

```
403 Forbidden
{
  "error": "voice_permission_denied",
  "message": "You do not have permission to use this voice"
}
```

---

### 3. TTS Job Status Endpoint

#### GET /api/v1/tts/{job_id}

**Purpose:** Get current status of TTS job

**Authentication:** Bearer token (required for non-playground jobs)

**Responses:**

```
200 OK
{
  "job_id": "uuid-1234",
  "status": "processing",
  "created_at": "2024-12-25T10:00:00Z"
}
```

```
200 OK (completed)
{
  "job_id": "uuid-1234",
  "status": "completed",
  "audio_duration_seconds": 30.5,
  "s3_url": "https://bucket.s3.amazonaws.com/tts-output/uuid-1234/audio.wav",
  "created_at": "2024-12-25T10:00:00Z",
  "completed_at": "2024-12-25T10:02:00Z"
}
```

```
200 OK (failed)
{
  "job_id": "uuid-1234",
  "status": "failed",
  "error_message": "Audio prompt download failed after 3 retries",
  "error_code": "AUDIO_PROMPT_DOWNLOAD_FAILED",
  "retry_count": 3,
  "created_at": "2024-12-25T10:00:00Z",
  "completed_at": "2024-12-25T10:05:00Z"
}
```

---

### 4. TTS SSE Streaming Endpoint

#### GET /api/v1/tts/{job_id}/stream

**Purpose:** Stream real-time TTS job updates

**Authentication:** Bearer token (required for non-playground jobs)

**Content-Type:** text/event-stream

**Connection:** Persistent (60 second timeout)

**Heartbeat:** Sent every 30 seconds to detect connection drops

**Event Examples:**

```
# Heartbeat (no action needed)
: heartbeat

# Status update
event: status
data: {"status":"processing"}

# Status update
event: status
data: {"status":"processing"}

# Completion
event: completed
data: {"status":"completed","s3_url":"https://...","audio_duration_seconds":30.5,"audio_format":"wav"}

# Or failure
event: failed
data: {"status":"failed","error_message":"Synthesis timeout","error_code":"SYNTHESIS_TIMEOUT","retry_count":3}

# Connection closes after terminal event
```

---

## RabbitMQ Message Formats

### 1. TTS Job Message (tts_jobs queue)

```json
{
  "job_id": "uuid-1234",
  "text": "Hello world",
  "audio_prompt_url": "https://bucket.s3.amazonaws.com/voice-recording.wav",
  "language": "en",
  "is_playground": false,
  "user_metadata": {
    "user_id": "user-456",
    "project_id": "proj-789",
    "voice_id": "voice-101",
    "correlation_id": "corr-xyz"
  },
  "created_at": "2024-12-25T10:00:00Z"
}
```

**Queue Configuration:**
- Name: `tts_jobs`
- Durable: true
- Message TTL: 24 hours
- Max length: unlimited

---

### 2. TTS Result Message (tts_results queue)

#### Success Message:
```json
{
  "job_id": "uuid-1234",
  "status": "completed",
  "output_s3_path": "s3://bucket/tts-output/uuid-1234/2024-12-25-10-02-30.wav",
  "audio_duration_seconds": 30.5,
  "synthesis_duration_seconds": 45,
  "timestamp": "2024-12-25T10:02:30Z",
  "user_metadata": {
    "user_id": "user-456",
    "project_id": "proj-789",
    "is_playground": false,
    "correlation_id": "corr-xyz"
  }
}
```

#### Failure Message:
```json
{
  "job_id": "uuid-1234",
  "status": "failed",
  "error_code": "AUDIO_PROMPT_DOWNLOAD_FAILED",
  "error_message": "S3 download timeout after 3 retries",
  "retry_count": 3,
  "timestamp": "2024-12-25T10:05:30Z",
  "user_metadata": {
    "user_id": "user-456",
    "project_id": "proj-789",
    "is_playground": false,
    "correlation_id": "corr-xyz"
  }
}
```

**Queue Configuration:**
- Name: `tts_results`
- Durable: true
- Message TTL: 7 days (for debugging)

---

## TTS Worker Processing Flow

### 1. Job Intake & Validation

```
1. Worker starts consuming from tts_jobs queue (prefetch_count=1)
2. Receives job message
3. Validates required fields:
   - job_id exists
   - text non-empty
   - audio_prompt_url is HTTP/HTTPS URL
   - language in supported list
4. If validation fails:
   - Log error with job_id
   - Publish failure message to tts_results
   - Acknowledge message (basic_ack)
   - Skip to next job
5. If validation succeeds:
   - Proceed to audio prompt download
```

### 2. Audio Prompt Download

```
1. Create temp directory: /tmp/tts-{job_id}/
2. Attempt to download audio_prompt_url from S3
3. Retry logic:
   - 1st attempt: immediate
   - 2nd attempt: after 5 seconds
   - 3rd attempt: after 15 seconds
4. If all attempts fail:
   - Log error with job_id, error_type, error_message
   - Publish failure message to tts_results
   - Acknowledge message (basic_ack)
   - Skip to next job
5. If download succeeds:
   - Validate audio file (format, duration, etc.)
   - If invalid → fail (non-retryable error)
   - If valid → proceed to synthesis
```

### 3. Audio Synthesis

```
1. Select synthesis engine:
   - If platform == "Darwin" → Use macOS TTS
   - Else → Use IndexTTS GPU inference
2. Load audio prompt into engine
3. Execute synthesis:
   - Pass: audio_prompt, text, language, output_path
   - Capture output_path, audio_duration_seconds, synthesis_duration_seconds
4. If synthesis fails:
   - Distinguish error type:
     - Retryable (GPU OOM, temp file issue): increment attempt, continue
     - Non-retryable (invalid prompt, language not supported): fail
   - After 3 retries → fail with error details
5. If synthesis succeeds:
   - Verify output file exists and non-empty
   - Proceed to S3 upload
```

### 4. S3 Upload

```
1. Generate S3 key: 
   - Playground: tts-output/playground/{job_id}/{timestamp}.{format}
   - User: tts-output/users/{job_id}/{timestamp}.{format}
2. Upload to S3 with metadata tags:
   - job_id, user_id, project_id, language, created_timestamp
3. Set expiration policy:
   - Playground audio (is_playground=true): 24 hours
   - User audio (is_playground=false): never (indefinite)
4. If upload fails:
   - Retry up to 3 times with exponential backoff
   - If all retries fail → fail with S3 error
5. If upload succeeds:
   - Capture output_s3_path (full S3 URL)
   - Proceed to result publication
```

### 5. Result Publication & Cleanup

```
1. Publish completion message to tts_results queue:
   - Include: job_id, status, output_s3_path, audio_duration_seconds, timestamp
2. Attempt to acknowledge message (basic_ack):
   - If ack succeeds → proceed to cleanup
   - If ack fails:
     - Retry up to 3 times with backoff
     - If all retries fail → log failure, mark job as failed in local tracking
3. Cleanup:
   - Delete temp audio_prompt file
   - Delete output audio file
   - Remove temp directory: /tmp/tts-{job_id}/
4. If cleanup fails:
   - Log warning (non-blocking, job is already processed)
```

### 6. Error Handling Flowchart

```
┌─────────────────────┐
│  Receive Job        │
└──────────┬──────────┘
           │
           ▼
     ┌─────────────┐
     │ Validate    │─── Fail ──► Log + Publish Failure + Ack ──► Next Job
     │ Job Fields  │
     └──────┬──────┘
            │ Pass
            ▼
     ┌─────────────────────┐
     │ Download Audio      │─── Fail (3 retries) ──► Publish Failure + Ack ──► Next Job
     │ Prompt              │
     └──────┬──────────────┘
            │ Success
            ▼
     ┌──────────────────┐
     │ Synthesize Audio │─── Non-retryable ──► Publish Failure + Ack ──► Next Job
     │                  │─── Retryable (3x) ──┤
     └──────┬───────────┘                      └─► Retry exponential backoff
            │ Success
            ▼
     ┌─────────────┐
     │ Upload to   │─── Fail (3 retries) ──► Publish Failure + Ack ──► Next Job
     │ S3          │
     └──────┬──────┘
            │ Success
            ▼
     ┌─────────────────────┐
     │ Publish Result +    │─── Ack Success ──► Cleanup ──► Next Job
     │ Retry Ack (3x)      │─── Ack Fail ──► Log Fail + Mark Failed
     └─────────────────────┘
```

---

## Integration Points

### indexTTS-worker ↔ studio-backend

1. **Job Submission**: studio-backend publishes to `tts_jobs` queue
2. **Result Consumption**: studio-backend consumes from `tts_results` queue
3. **S3 Storage**: Both read/write to same S3 bucket (different paths)
4. **Audio Prompt**: studio-backend stores voice S3 URLs; worker downloads them

### official-landing ↔ studio-backend

1. **Playground API**: official-landing calls POST `/api/v1/playground/tts`
2. **SSE Streaming**: official-landing subscribes to GET `/api/v1/tts/{job_id}/stream`
3. **Graceful Degradation**: if API unreachable → display error, don't crash page

### studio-backend ↔ indexTTS-worker (RabbitMQ)

1. **Queue Publisher**: studio-backend publishes TTS jobs
2. **Queue Consumer**: studio-backend consumes TTS results
3. **Connection Pool**: studio-backend maintains async RabbitMQ connections
4. **Error Handling**: If queue unreachable → 503 Service Unavailable

---

## Error Codes

### Worker-Generated Errors (non-retryable)

| Error Code | Cause | Action |
|---|---|---|
| `INVALID_JOB_FORMAT` | Missing required fields | Fail, don't retry |
| `INVALID_AUDIO_PROMPT` | Prompt file corrupted/invalid format | Fail, don't retry |
| `LANGUAGE_NOT_SUPPORTED` | Language not in model config | Fail, don't retry |
| `GPU_OUT_OF_MEMORY` | CUDA/GPU memory exceeded | Retry (retryable) |
| `SYNTHESIS_TIMEOUT` | Model inference exceeds timeout | Retry (retryable) |

### Worker-Generated Errors (retryable)

| Error Code | Cause | Retry Strategy |
|---|---|---|
| `AUDIO_PROMPT_DOWNLOAD_FAILED` | Network timeout, S3 error | Exponential backoff, max 3 |
| `S3_UPLOAD_FAILED` | Network timeout, S3 error | Exponential backoff, max 3 |
| `RABBITMQ_ACK_FAILED` | Message broker connection issue | Exponential backoff, max 3 |

### Backend-Generated Errors

| Error Code | HTTP | Cause |
|---|---|---|
| `text_too_long` | 400 | Exceeded 500 chars (playground) or 5000 chars (user) |
| `rate_limit_exceeded` | 429 | More than 5 requests/IP/hour (playground) |
| `voice_not_found` | 404 | voice_id doesn't exist |
| `voice_permission_denied` | 403 | Private voice not owned by user |
| `language_not_supported` | 400 | Language not in supported list |
| `invalid_audio_prompt_id` | 400 | audio_prompt_id not in playground catalog |

---

## Correctness Properties (for PBT)

### Property 1: Round-trip - Audio Output Format
**For ALL synthesized audio, parsing then re-encoding SHALL produce an equivalent audio file.**
- Input: text, audio_prompt
- Output: WAV file (from IndexTTS)
- Verify: decode(encode(audio)) == original_audio (with <1% tolerance)

### Property 2: Idempotence - Cached Synthesis
**FOR identical (voice_id, text) pairs, synthesizing multiple times within 30 days SHALL return the same S3 URL.**
- Input: voice_id, text, language
- Output: s3_url
- Verify: f(voice_id, text) == f(voice_id, text) (same S3 URL on retry)

### Property 3: Invariant - Job Status Progression
**Jobs SHALL only transition through valid state sequences: pending → processing → completed/failed.**
- Valid: pending → processing → completed
- Valid: pending → processing → failed
- Valid: pending → failed (immediate validation failure)
- Invalid: completed → processing (illegal transition)
- Invalid: failed → completed (illegal transition)

### Property 4: Error Handling - Retry Exhaustion
**Jobs that exhaust retries SHALL be marked as failed with proper error documentation.**
- Given: retryable_error
- Then: retry_count == 3 AND status == failed AND error_message != NULL

### Property 5: Rate Limiting - IP-based Enforcement
**Any IP SHALL NOT exceed 5 TTS requests per hour window.**
- Given: requests from same IP in 1 hour
- Then: count(successful_requests + rejected_requests) >= 5
- When: count > 5, return 429 Too Many Requests

