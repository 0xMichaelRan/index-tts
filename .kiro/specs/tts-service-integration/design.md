# TTS Service Integration Design

## Overview

This specification defines the integration of IndexTTS text-to-speech engine across three repositories with a **simplified text processing approach**:

- **indexTTS-worker**: Core TTS service and RabbitMQ worker
- **official-landing**: Next.js playground for anonymous TTS demonstrations  
- **studio-backend**: FastAPI backend for production TTS jobs

**Simplified Text Processing Strategy**:
1. **Studio-backend (authenticated users)**: Only the **first 2 sentences** of the script text are processed for TTS synthesis
   - Guarantees **fast and quick response times** for logged-in users
   - **Never** performs full-script-text TTS synthesis
   - Caching based on `(voice_id, first_2_sentences)` pairs

2. **Playground (anonymous users)**: **Full text** up to 200 words is processed
   - Provides **complete demo experience** for evaluation
   - Caching based on `(voice_id, full_text_hash)` pairs

**Key Design Principles**:
1. Build upon existing `TTSJob` and `Voice` schemas in studio-backend
2. Simple, clear text processing rules (no confusing `preview_text` concept)
3. S3 path-based storage (not full URLs) across all repositories
4. Three voice states: private, shared, approved
5. Implement circuit breaker and dead-letter queue patterns
6. Language as mandatory field with simplified Chinese backfill

## Architecture

### High-Level Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                       official-landing (Next.js)                        │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │ Playground Component                                             │  │
│  │  - Text input (max 200 words)                                    │  │
│  │  - Voice selector (approved community voices only)               │  │
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
│  │  - POST /api/v1/tts (authenticated)                              │  │
│  │  - POST /api/v1/playground/tts (anonymous)                       │  │
│  │  - GET /api/v1/tts/{job_id}                                      │  │
│  │  - GET /api/v1/tts/{job_id}/stream (SSE)                         │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │ Database Layer                                                   │  │
│  │  - TTSJob (extended existing schema)                             │  │
│  │  - Voice (existing three-state system)                           │  │
│  │  - PlaygroundTTSJob (new table)                                  │  │
│  │  - RateLimitCounter (new table)                                  │  │
│  └──────────────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │ RabbitMQ Consumer                                               │  │
│  │  - Subscribes to tts_results queue                              │  │
│  │  - Updates job records with circuit breaker                     │  │
│  │  - Dead-letter queue integration                                │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└────────────────────────────┬──────────────────────────────────────────┘
                             │ RabbitMQ
                             │ tts_jobs + tts_jobs_dlq
                             │ tts_results + tts_results_dlq
                             ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                  indexTTS-worker (Python Service)                       │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │ TTS Worker                                                       │  │
│  │  - Circuit breaker for S3/IndexTTS                              │  │
│  │  - Dead-letter queue consumption                                │  │
│  │  - Downloads audio prompt from S3                                │  │
│  │  - Platform-specific synthesis (GPU/macOS)                      │  │
│  │  - Idempotent S3 upload with retry                              │  │
│  │  - Partial failure recovery                                      │  │
│  │  - Publishes to tts_results queue                               │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└────────────────────────────┬──────────────────────────────────────────┘
                             │ AWS S3 Bucket
                             │ (shared across repositories)
                             ▼
                    ┌─────────────────────────┐
                    │   Path-Based Storage    │
                    │  - audio-prompts/       │
                    │  - tts-output/studio/   │
                    │  - tts-output/playground/│
                    └─────────────────────────┘
```

## Components and Interfaces

### 1. Database Models

#### TTSJob (existing schema - extended)
```python
# Extended from existing app/models/tts_job.py
class TTSJob(Base):
    __tablename__ = "tts_jobs"
    
    # Existing fields (maintained for backward compatibility)
    id: int                          # BigInteger auto-increment
    project_id: int                  # Foreign key to projects
    script_id: int | None
    voice_id: int | None             # Foreign key to voices
    voice_name: str | None           # Snapshot of voice name
    processed_text: str | None       # First 2 sentences (for studio-backend caching)
    external_job_id: str | None
    
    # Enhanced status (existing + new)
    status: str                      # queued, processing, completed, failed, rate_limited
    progress: int                    # 0-100
    
    # Path-based storage (not full URLs)
    audio_path: str | None           # e.g., "tts-output/studio/{job_id}.wav"
    audio_duration: float | None     # seconds
    
    # New required fields
    language: str                    # Language code (e.g., "zh", "en") - NOT NULL
    correlation_id: str              # For distributed tracing
    full_text_hash: str | None       # SHA256 hash of full text for deduplication
    
    # Existing timestamps
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
```

#### PlaygroundTTSJob (new table)
```python
class PlaygroundTTSJob(Base):
    __tablename__ = "playground_tts_jobs"
    
    # Identifiers
    id: str                          # UUID primary key (different from TTSJob)
    
    # Input - restricted to approved community voices only
    text: str                        # Full text (max 200 words ≈ 1000 chars)
    voice_id: int                    # Foreign key to approved community voices
    language: str                    # Language code (required, NOT NULL)
    
    # Processing states aligned with TTSJob
    status: str                      # queued, processing, completed, failed, rate_limited
    retry_count: int                 # Number of retries attempted
    
    # Results - path-based storage
    audio_path: str | None           # e.g., "tts-output/playground/{job_id}.wav"
    audio_duration: float | None     # seconds
    error_message: str | None
    
    # Metadata for abuse prevention
    client_ip_address: str           # Required for rate limiting (hashed)
    user_agent: str | None
    correlation_id: str              # For distributed tracing
    
    # Timestamps with automatic cleanup
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    expires_at: datetime             # Created_at + 30 days (automatic cleanup)
```

#### Voice (existing implementation - enhanced)
```python
class Voice(Base):
    __tablename__ = "voices"
    
    # Existing fields
    id: int                          # BigInteger auto-increment
    user_id: int                     # NOT NULL - every voice belongs to user
    name: str
    audio_path: str                  # Path-based (e.g., "audio-prompts/{voice_id}.wav")
    mime_type: str
    
    # Enhanced language support
    language: str                    # NOT NULL after backfill (default: "zh")
    
    # Three-state voice system (existing)
    is_shared: bool                  # User-controlled: private vs shared
    is_approved: bool                # Admin-controlled: approved for community use
    
    # Metadata
    duration_seconds: float | None
    created_at: datetime
    updated_at: datetime
    
    # State helper properties
    @property
    def state(self) -> str:
        """Returns: 'private', 'shared', or 'approved'"""
        if not self.is_shared:
            return "private"
        return "approved" if self.is_approved else "shared"
    
    @property
    def is_community_eligible(self) -> bool:
        """Can be used in playground (approved + shared)"""
        return self.is_shared and self.is_approved
```

#### RateLimitCounter (new table)
```python
class RateLimitCounter(Base):
    __tablename__ = "rate_limit_counters"
    
    id: int                          # Auto-increment
    ip_address_hash: str             # SHA256 hash of client IP (for privacy)
    request_count: int               # Count in current window
    window_start: datetime           # Start of 1-hour window
    window_end: datetime             # End of 1-hour window
    created_at: datetime
    
    # Composite index on (ip_address_hash, window_end) for fast lookups
```

### 2. RabbitMQ Configuration

#### Queue Architecture with Dead-Letter Queues
```
Main Queues (Durable):
├── tts_jobs (TTL: 24h) → Dead-letter: tts_jobs_dlq
└── tts_results (TTL: 7d) → Dead-letter: tts_results_dlq

Dead-Letter Queues (Durable):
├── tts_jobs_dlq (TTL: 7 days) - Messages rejected after 3 retries
└── tts_results_dlq (TTL: 7 days) - Failed result processing
```

#### Queue Arguments
```python
# tts_jobs queue
{
    'x-dead-letter-exchange': '',
    'x-dead-letter-routing-key': 'tts_jobs_dlq',
    'x-message-ttl': 86400000,        # 24 hours in milliseconds
    'x-max-length': 10000,            # Prevent unlimited queue buildup
    'x-overflow': 'reject-publish',   # Reject new messages when full
}

# Dead-letter queues
{
    'x-message-ttl': 604800000,       # 7 days in milliseconds
    'x-max-length': 5000,
}
```

### 3. Message Formats

#### TTS Job Message (tts_jobs queue)
```json
{
  "job_type": "studio" | "playground",
  "job_id": "uuid-1234",
  "text": "Hello world",
  "audio_prompt_path": "audio-prompts/123.wav",
  "language": "zh",
  "text_processing_mode": "first_2_sentences" | "full_text",
  "metadata": {
    "user_id": 456,
    "project_id": 789,
    "voice_id": 101,
    "correlation_id": "corr-xyz",
    "full_text_hash": "sha256-hash"
  },
  "output_path_template": "tts-output/{type}/{job_id}.wav",
  "created_at": "2024-12-25T10:00:00Z"
}
```

#### TTS Result Message (tts_results queue)
```json
{
  "job_type": "studio" | "playground",
  "job_id": "uuid-1234",
  "status": "completed" | "failed",
  "audio_path": "tts-output/studio/uuid-1234.wav",  # or playground path
  "audio_duration_seconds": 30.5,
  "synthesis_duration_seconds": 45,
  "error_code": null | "SYNTHESIS_TIMEOUT",
  "error_message": null | "Synthesis timeout after 3 retries",
  "retry_count": 0,
  "timestamp": "2024-12-25T10:02:30Z",
  "metadata": {
    "user_id": 456,
    "project_id": 789,
    "voice_id": 101,
    "correlation_id": "corr-xyz"
  }
}
```

## API Specifications

### 1. Playground TTS Endpoint

#### POST /api/v1/playground/tts
**Purpose**: Create anonymous TTS job using approved community voices (complete demo)

**Authentication**: None (public)

**Rate Limit**: 5 requests per hashed IP per hour

**Request**:
```json
{
  "text": "Hello, this is a demonstration of our TTS system with the complete text experience up to 200 words.",
  "voice_id": 123,
  "language": "zh"
}
```

**Important**: Playground TTS jobs process the **full text** (up to 200 words) to give anonymous users the complete TTS demo experience.

**Validation**:
1. `text`: Required, max 200 words (≈1000 chars), non-empty
2. `voice_id`: Required, must exist and be approved community voice (`is_shared=true AND is_approved=true`)
3. `language`: Required, must match voice language

**Caching Strategy**: Cache based on `(voice_id, full_text_hash)` pairs within 30 days

**Responses**:
```http
202 Accepted
{
  "job_id": "uuid-1234",
  "status": "queued",
  "stream_url": "/api/v1/tts/{job_id}/stream",
  "expires_at": "2024-12-25T12:00:00Z"
}

429 Too Many Requests
{
  "error": "rate_limit_exceeded",
  "message": "Maximum 5 requests per hour",
  "retry_after": 3600
}
```

### 2. Authenticated Studio TTS Endpoint

#### POST /api/v1/tts
**Purpose**: Create TTS job using user's voice recordings (fast preview only)

**Authentication**: Bearer token (required)

**Request**:
```json
{
  "project_id": 456,
  "text": "This is the full script for my video project. It could be very long with many paragraphs. But only the first 2 sentences will be processed for TTS synthesis.",
  "voice_id": 789,
  "language": "zh"
}
```

**Important**: Studio-backend TTS jobs **only process the first 2 sentences** of the script text, regardless of how long the full script is. This ensures fast and quick response times for logged-in users.

**Validation**:
1. `project_id`: Required, user must own project
2. `text`: Required (full script text - only first 2 sentences are processed for TTS)
3. `voice_id`: Required, must exist and user must have permission
   - Private voices: User must own (`user_id` matches)
   - Shared voices: Any user can use (`is_shared=true`)
4. `language`: Required, must match voice language

**Caching Strategy**: Cache based on `(voice_id, first_2_sentences)` pairs within 30 days

**Responses**:
```http
202 Accepted
{
  "job_id": 12345,
  "status": "queued",
  "stream_url": "/api/v1/tts/{job_id}/stream",
  "is_cached": false
}

200 OK (cached)
{
  "job_id": 12345,
  "status": "completed",
  "audio_path": "tts-output/studio/12345.wav",
  "audio_duration_seconds": 45.5,
  "is_cached": true,
  "cached_from_date": "2024-12-10T08:00:00Z"
}
```

### 3. TTS Job Status Endpoints

#### GET /api/v1/tts/{job_id}
**Purpose**: Get current status of TTS job

**Authentication**: Required for studio jobs, optional for playground

**Responses**:
```http
200 OK
{
  "job_id": "uuid-1234",
  "status": "processing",
  "created_at": "2024-12-25T10:00:00Z"
}

200 OK (completed)
{
  "job_id": "uuid-1234",
  "status": "completed",
  "audio_duration_seconds": 30.5,
  "audio_path": "tts-output/playground/uuid-1234.wav",
  "created_at": "2024-12-25T10:00:00Z",
  "completed_at": "2024-12-25T10:02:00Z"
}
```

#### GET /api/v1/tts/{job_id}/stream
**Purpose**: Server-Sent Events stream for real-time job updates

**Content-Type**: `text/event-stream`

**Events**:
```text
event: status
data: {"status":"processing","progress":50}

event: completed
data: {"status":"completed","audio_path":"tts-output/studio/12345.wav","audio_duration_seconds":30.5}

event: failed
data: {"status":"failed","error_message":"Synthesis timeout","retry_count":3}
```

## Error Handling

### Circuit Breaker Pattern
**Purpose**: Prevent cascading failures during S3/IndexTTS outages

**Implementation**:
```python
class TTSCircuitBreaker:
    def __init__(self, failure_threshold=5, reset_timeout=60):
        self.failure_count = 0
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
    
    async def execute(self, operation):
        if self.state == "OPEN":
            raise CircuitBreakerOpenError("Service unavailable")
        
        try:
            result = await operation()
            self._on_success()
            return result
        except (S3Error, IndexTTSError) as e:
            self._on_failure()
            raise
```

### Dead-Letter Queue Strategy
**Process**:
1. Message fails processing 3 times (with exponential backoff)
2. Message routed to dead-letter queue
3. Dead-letter processor logs error and alerts
4. Manual intervention required for DLQ messages

### Partial Failure Recovery
**Scenario**: S3 upload succeeds but RabbitMQ ack fails

**Solution**: Idempotent retry with S3 metadata tagging
1. Tag S3 object with `job_id` and `status=uploaded`
2. On retry, check if object exists with same tags
3. Skip upload if already exists, proceed to RabbitMQ publishing
4. Implement at-least-once delivery guarantee

## Correctness Properties

### Property 1: Cache Consistency - Studio Jobs
**For studio jobs**, identical `(voice_id, first_2_sentences)` pairs within 30 days SHALL return the same `audio_path`.

### Property 2: Cache Consistency - Playground Jobs  
**For playground jobs**, identical `(voice_id, full_text_hash)` pairs within 30 days SHALL return the same `audio_path`.

### Property 3: Text Processing Guarantee
**Studio-backend jobs SHALL ONLY process** the first 2 sentences of the script text, regardless of total script length.

### Property 4: State Machine Validity
**Jobs SHALL only transition**: 
- `queued → processing → completed/failed/rate_limited`
- `queued → failed` (immediate validation failure)
- Invalid: `completed → processing`, `failed → completed`

### Property 5: Rate Limiting Enforcement
**Any hashed IP SHALL NOT exceed** 5 playground requests per 1-hour window.

### Property 6: Voice Permission Hierarchy
**Access rules SHALL follow**:
1. Private voices: Owner only
2. Shared voices: Owner + collaborators
3. Approved voices: All users (playground eligible)

### Property 7: Language Consistency
**Voice language SHALL match** job language for all TTS requests.

## Testing Strategy

### Unit Tests
- Database model validation
- Message format serialization
- Cache lookup logic
- Permission checking

### Integration Tests
- Full pipeline: API → RabbitMQ → Worker → S3 → Results
- Circuit breaker behavior
- Dead-letter queue routing
- Partial failure scenarios

### Property-Based Tests
- Generate random job parameters
- Verify state machine transitions
- Test cache consistency properties
- Validate rate limiting bounds

### Performance Tests
- Measure synthesis latency (100, 1000, 5000 words)
- End-to-end latency targets (<5 min for typical text)
- Load testing: 10 concurrent jobs
- S3 upload/download speed benchmarks

## Infrastructure Configuration

### S3 Bucket Structure
```
s3://{bucket-name}/
├── audio-prompts/
│   ├── {voice_id}.wav      # Voice recordings (path-based)
│   └── {voice_id}.json     # Voice metadata
├── tts-output/
│   ├── studio/
│   │   ├── {job_id}.wav    # Studio job outputs (indefinite retention)
│   │   └── {job_id}.json   # Job metadata
│   └── playground/
│       ├── {job_id}.wav    # Playground outputs (24h retention)
│       └── {job_id}.json   # Job metadata
└── logs/
    ├── worker/
    └── backend/
```

### S3 Lifecycle Rules
- `tts-output/playground/`: Delete after 24 hours
- `tts-output/studio/`: Never expire (manual cleanup)
- `logs/`: Transition to Glacier after 30 days, delete after 365 days

### CORS Configuration
- Allow `GET` from official-landing domain for playground audio playback
- Allow `PUT` from indexTTS-worker for audio uploads
- Allow `GET` from studio-backend for audio verification

## Security Considerations

### Input Validation
- SQL injection protection for text parameters
- S3 path traversal prevention
- Voice ID enumeration prevention
- Language code whitelisting

### API Security
- Playground endpoint: CSRF protection, rate limiting
- SSE endpoints: Connection limiting, CORS restrictions
- Authentication: JWT validation, token refresh

### Data Privacy
- IP address hashing (SHA256) for rate limiting
- Audio file encryption at rest
- Access logs anonymization
- GDPR compliance for playground data retention

### Secrets Management
- AWS IAM roles for S3 access (not access keys)
- RabbitMQ credentials rotation
- JWT secret rotation
- Environment variable encryption