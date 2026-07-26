# TTS Service Integration - Implementation Tasks

## Implementation Plan

This plan implements the TTS service integration across three repositories with focus on:
1. Building upon existing `TTSJob` and `Voice` schemas
2. Implementing dead-letter queues and circuit breaker patterns
3. Using path-based S3 storage (not full URLs)
4. Different caching strategies: preview_text (studio) vs full text (playground)
5. Three-state voice system: private, shared, approved

## Phase 1: Database Migration & Core Infrastructure (Week 1-2)

### 1.1 Extend Existing TTSJob Schema (studio-backend)
- [ ] Add new columns to existing `TTSJob` table:
  - `language`: VARCHAR(10) NOT NULL (default: 'zh')
  - `correlation_id`: VARCHAR(255) NOT NULL (UUID)
  - `full_text_hash`: VARCHAR(64) NULL (SHA256 of full text)
- [ ] Add `rate_limited` to status check constraint
- [ ] Create migration: `alembic revision --autogenerate -m "Extend TTSJob schema"`
- [ ] Apply migration: `alembic upgrade head`
- [ ] Write test: verify new columns accept expected data types

### 1.2 Create PlaygroundTTSJob Table (studio-backend)
- [ ] Create `PlaygroundTTSJob` ORM model with fields:
  - `id`: UUID primary key (different from TTSJob integer IDs)
  - `text`: TEXT (max 1000 chars, full text storage)
  - `voice_id`: INTEGER NOT NULL (approved community voices only)
  - `language`: VARCHAR(10) NOT NULL
  - `status`: VARCHAR(50) (queued, processing, completed, failed, rate_limited)
  - `audio_path`: VARCHAR(512) NULL (path-based: "tts-output/playground/{id}.wav")
  - `audio_duration`: FLOAT NULL
  - `client_ip_hash`: VARCHAR(64) NOT NULL (SHA256 of IP for privacy)
  - `correlation_id`: VARCHAR(255) NOT NULL
  - `expires_at`: DATETIME NOT NULL (created_at + 30 days)
  - Standard timestamps: created_at, started_at, completed_at
- [ ] Create migration: `alembic revision --autogenerate -m "Add PlaygroundTTSJob table"`
- [ ] Apply migration: `alembic upgrade head`
- [ ] Write test: verify playground jobs auto-expire after 30 days

### 1.3 Backfill Voice Language Data (studio-backend)
- [ ] Create migration to add `language` column to `Voice` table (NOT NULL)
- [ ] Create data migration script to backfill existing voices:
  ```python
  # scripts/backfill_voice_language.py
  UPDATE voices SET language = 'zh' WHERE language IS NULL;
  ```
- [ ] Apply migration: `alembic upgrade head`
- [ ] Run backfill script: `uv run python scripts/backfill_voice_language.py`
- [ ] Write test: verify all voices have non-null language

### 1.4 Configure RabbitMQ with Dead-Letter Queues
- [ ] Update RabbitMQ configuration in studio-backend:
  ```python
  # app/services/rabbitmq.py
  QUEUE_CONFIG = {
      'tts_jobs': {
          'durable': True,
          'arguments': {
              'x-dead-letter-exchange': '',
              'x-dead-letter-routing-key': 'tts_jobs_dlq',
              'x-message-ttl': 86400000,  # 24h
              'x-max-length': 10000,
          }
      },
      'tts_jobs_dlq': {'durable': True, 'arguments': {'x-message-ttl': 604800000}},  # 7d
      'tts_results': {'durable': True, 'arguments': {'x-dead-letter-exchange': '', 'x-dead-letter-routing-key': 'tts_results_dlq'}},
      'tts_results_dlq': {'durable': True, 'arguments': {'x-message-ttl': 604800000}},
  }
  ```
- [ ] Test queue declaration and DLQ routing
- [ ] Write test: verify failed messages route to DLQ after 3 retries

### 1.5 Configure S3 Path-Based Storage
- [ ] Create S3 bucket structure documentation:
  ```
  s3://{bucket}/
  ├── audio-prompts/           # Voice recordings
  │   └── {voice_id}.{format}  # Path-based storage
  ├── tts-output/
  │   ├── studio/              # Studio job outputs
  │   │   └── {job_id}.{format}
  │   └── playground/          # Playground outputs (24h retention)
  │       └── {job_id}.{format}
  └── logs/                    # Application logs
  ```
- [ ] Configure S3 lifecycle rules:
  - Delete `tts-output/playground/` objects after 24 hours
  - Keep `tts-output/studio/` objects indefinitely
- [ ] Configure CORS for official-landing domain access
- [ ] Test S3 upload/download from all three repositories

---

## Phase 2: Studio Backend Implementation (Week 2-3)

### 2.1 Implement Studio TTS Endpoint (preview_text caching)
- [ ] Create/update `POST /api/v1/tts` endpoint in `app/routers/tts.py`
- [ ] Extract `preview_text` (first 2 sentences) from full text
- [ ] Implement permission checking for three-state voice system:
  - Private voices: `user_id` must match voice owner
  - Shared voices: Any authenticated user
  - Approved voices: All users (including playground)
- [ ] Implement cache lookup using existing `preview_text` field:
  ```python
  cached_job = await find_cached_tts_job(
      db, voice_id, preview_text, days=30
  )
  ```
- [ ] Create new TTSJob records with extended fields:
  - `language` (required), `correlation_id` (UUID), `full_text_hash` (SHA256)
- [ ] Publish to `tts_jobs` queue with path-based storage:
  - `audio_prompt_path`: "audio-prompts/{voice_id}.wav"
  - `output_path_template`: "tts-output/studio/{job_id}.wav"
  - `job_type`: "studio"
- [ ] Write tests: authentication, permission checks, cache hits/misses

### 2.2 Implement Playground TTS Endpoint (full text caching)
- [ ] Create `POST /api/v1/playground/tts` endpoint in `app/routers/playground.py`
- [ ] Validate input:
  - `text`: max 200 words (≈1000 chars)
  - `voice_id`: must be approved community voice (`is_shared=true AND is_approved=true`)
  - `language`: must match voice language
- [ ] Implement IP-based rate limiting (5/hour):
  - Hash IP with SHA256 for privacy
  - Store in `RateLimitCounter` table
  - Return 429 with `retry_after: 3600` when exceeded
- [ ] Create `PlaygroundTTSJob` records (UUID primary key)
- [ ] Implement cache lookup using full text hash:
  ```python
  text_hash = sha256(text.encode()).hexdigest()
  cached = await find_cached_playground_job(voice_id, text_hash, days=30)
  ```
- [ ] Publish to `tts_jobs` queue with:
  - `job_type`: "playground"
  - `output_path_template`: "tts-output/playground/{job_id}.wav"
- [ ] Write tests: rate limiting, voice validation, cache behavior

### 2.3 Implement SSE Streaming Endpoint
- [ ] Create `GET /api/v1/tts/{job_id}/stream` endpoint
- [ ] Handle both TTSJob (integer ID) and PlaygroundTTSJob (UUID)
- [ ] Implement Server-Sent Events with:
  - 30-second heartbeat to detect connection drops
  - Status events: "queued", "processing", "completed", "failed"
  - Progress updates for "processing" state (0-100%)
  - Terminal events: "completed" with audio_path, "failed" with error
- [ ] Implement permission checking:
  - Studio jobs: user must own project
  - Playground jobs: no authentication required
- [ ] Write tests: SSE connection, event streaming, permission checks

### 2.4 Enhance TTS Results Consumer
- [ ] Update existing `background_worker.py` or create `tts_consumer.py`
- [ ] Consume from `tts_results` queue with DLQ support
- [ ] Route messages based on `job_type`:
  - "studio": Update `TTSJob` records
  - "playground": Update `PlaygroundTTSJob` records
- [ ] Validate `audio_path` exists in S3 before updating database
- [ ] Handle partial failures (S3 success, RabbitMQ failure)
- [ ] Implement circuit breaker for database updates
- [ ] Write tests: message routing, validation, error handling

---

## Phase 3: IndexTTS Worker Implementation (Week 3-4)

### 3.1 Implement Circuit Breaker Pattern
- [ ] Create `CircuitBreaker` class in `indextts/worker/circuit_breaker.py`:
  ```python
  class TTSCircuitBreaker:
      def __init__(self, failure_threshold=5, reset_timeout=60):
          self.state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
          self.failure_count = 0
          # ...
      
      async def execute(self, operation):
          if self.state == "OPEN":
              raise CircuitBreakerOpenError("Service unavailable")
          # ...
  ```
- [ ] Implement circuit breakers for:
  - S3 downloads (threshold: 5 failures in 60s)
  - IndexTTS synthesis (threshold: 3 failures in 30s)
  - RabbitMQ publishing (threshold: 3 failures in 30s)
- [ ] Add metrics and logging for circuit state changes
- [ ] Write tests: circuit opening/closing, failure thresholds

### 3.2 Implement Dead-Letter Queue Consumption
- [ ] Create DLQ monitoring in worker:
  ```python
  async def monitor_dlq():
      while True:
          # Check tts_jobs_dlq depth
          # Alert if >100 messages or >24h old
          await asyncio.sleep(300)  # 5 minutes
  ```
- [ ] Implement DLQ message processing (manual intervention required)
- [ ] Add alerting for DLQ conditions:
  - `tts_jobs_dlq` > 100 messages
  - `tts_results_dlq` > 50 messages
  - Any message >24 hours in DLQ
- [ ] Write tests: DLQ monitoring, alert triggering

### 3.3 Implement Idempotent S3 Upload
- [ ] Create `S3Uploader` class with idempotent retry:
  ```python
  class IdempotentS3Uploader:
      async def upload(self, job_id, file_path, s3_key):
          # Check if object already exists with job_id tag
          existing = await s3.get_object_tagging(job_id)
          if existing and existing.get('status') == 'uploaded':
              return existing['key']  # Skip upload
          
          # Upload with tags: job_id, status=uploaded
          await s3.upload_file(file_path, s3_key, tags={'job_id': job_id, 'status': 'uploaded'})
          return s3_key
  ```
- [ ] Handle partial failure scenario:
  - S3 upload succeeds, RabbitMQ ack fails
  - On retry, detect existing S3 object via tags
  - Skip upload, proceed to RabbitMQ publishing
- [ ] Maximum 3 retry attempts with exponential backoff
- [ ] Write tests: idempotent upload, partial failure recovery

### 3.4 Implement TTS Worker Core
- [ ] Create main worker entry point: `indextts/worker/main.py`
- [ ] Consume from `tts_jobs` queue with prefetch_count optimized for scaling
- [ ] Platform detection for synthesis engine:
  - macOS: Use native AVFoundation TTS
  - Linux/Windows: Use IndexTTS GPU inference
- [ ] Download audio prompts from S3 using path-based storage
- [ ] Execute synthesis with progress tracking
- [ ] Upload results to S3 with path-based keys
- [ ] Publish to `tts_results` queue
- [ ] Handle graceful shutdown (SIGTERM)
- [ ] Write tests: job processing, platform detection, error handling

### 3.5 Implement Structured Logging & Metrics
- [ ] Configure JSON-structured logging with fields:
  - timestamp, log_level, job_id, correlation_id
  - operation, duration_ms, platform, engine_type
  - error_code, error_message, retry_count
- [ ] Add Prometheus metrics:
  - `tts_jobs_processed_total` (counter)
  - `tts_job_duration_seconds` (histogram)
  - `tts_synthesis_errors_total` (counter)
  - `circuit_breaker_state` (gauge)
  - `dlq_depth` (gauge)
- [ ] Document integration with ELK Stack/Grafana
- [ ] Write tests: log format, metric collection

---

## Phase 4: Official Landing Integration (Week 4-5)

### 4.1 Create Playground UI Component
- [ ] Create `src/components/TTSPlayground.tsx` in official-landing
- [ ] Implement form with:
  - Text input (max 200 words, character counter)
  - Voice selector (approved community voices only)
  - Language selector (mandatory, matches voice language)
  - Generate button with loading state
- [ ] Fetch approved community voices from API:
  ```typescript
  const fetchCommunityVoices = async () => {
    const response = await fetch('/api/v1/voices?state=approved');
    return response.json();
  };
  ```
- [ ] Handle form validation and error display
- [ ] Write tests: form validation, voice fetching, error handling

### 4.2 Implement SSE Client Streaming
- [ ] Create `hooks/useTTSStream.ts` for SSE subscription:
  ```typescript
  const useTTSStream = (jobId: string) => {
    const [status, setStatus] = useState<'queued' | 'processing' | 'completed' | 'failed'>('queued');
    const [progress, setProgress] = useState(0);
    const [audioPath, setAudioPath] = useState<string | null>(null);
    
    useEffect(() => {
      const eventSource = new EventSource(`/api/v1/tts/${jobId}/stream`);
      // Handle events: status, progress, completed, failed
    }, [jobId]);
  };
  ```
- [ ] Handle connection timeout (60 seconds)
- [ ] Implement retry logic for failed connections
- [ ] Display real-time progress updates
- [ ] Write tests: SSE connection, event handling, timeout recovery

### 4.3 Implement Audio Playback Component
- [ ] Create `src/components/TTSAudioPlayer.tsx`:
  ```typescript
  interface TTSAudioPlayerProps {
    audioPath: string;  // Path-based: "tts-output/playground/{id}.wav"
    duration?: number;
  }
  ```
- [ ] Generate S3 presigned URL for audio playback:
  ```typescript
  const getAudioUrl = async (audioPath: string) => {
    const response = await fetch(`/api/v1/audio/url?path=${encodeURIComponent(audioPath)}`);
    const { url } = await response.json();
    return url;
  };
  ```
- [ ] Implement HTML5 audio element with controls
- [ ] Handle playback errors and retry
- [ ] Write tests: URL generation, audio playback, error handling

### 4.4 Implement Graceful Degradation
- [ ] Wrap playground component in error boundary
- [ ] Handle API unreachable scenarios:
  ```typescript
  try {
    await submitTTSJob(data);
  } catch (error) {
    if (error instanceof NetworkError) {
      showErrorMessage('TTS service temporarily unavailable. Please try again later.');
    }
  }
  ```
- [ ] Implement offline fallback mode
- [ ] Cache community voices locally for offline use
- [ ] Write tests: error boundary, network failure handling, offline mode

### 4.5 Implement i18n for Playground
- [ ] Add translations in `src/locales/`:
  - English (`en.json`), Chinese (`zh.json`)
  - Translate UI labels: "Text", "Voice", "Language", "Generate"
  - Translate error messages: "Text too long", "Service unavailable"
- [ ] Use `next-intl` for locale switching
- [ ] Implement language detection from browser
- [ ] Write tests: translation loading, locale switching

---

## Phase 5: Testing & Quality (Week 5-6)

### 5.1 Write Property-Based Tests (PBT)
- [ ] **Idempotence Test**: Same `(voice_id, preview_text)` within 30 days returns same `audio_path`
- [ ] **State Machine Test**: Job transitions follow valid sequences
- [ ] **Rate Limiting Test**: Hashed IP cannot exceed 5 requests/hour
- [ ] **Permission Hierarchy Test**: Voice access follows private→shared→approved rules
- [ ] **Language Consistency Test**: Voice language matches job language
- [ ] Use Hypothesis library for Python, fast-check for TypeScript

### 5.2 Write Integration Tests
- [ ] **Full Pipeline Test**: API → RabbitMQ → Worker → S3 → Database
- [ ] **Circuit Breaker Test**: Verify opening/closing behavior
- [ ] **DLQ Test**: Failed messages route to dead-letter queues
- [ ] **Partial Failure Test**: S3 success + RabbitMQ failure recovery
- [ ] **Cache Consistency Test**: Studio vs playground caching strategies
- [ ] **Performance Test**: Measure latency with different text lengths

### 5.3 Security & Compliance Testing
- [ ] **Input Validation Test**: SQL injection, path traversal prevention
- [ ] **Authentication Test**: JWT validation, token refresh
- [ ] **Permission Test**: Voice access control validation
- [ ] **Privacy Test**: IP hashing, data retention compliance
- [ ] **CORS Test**: Cross-origin access restrictions
- [ ] **Rate Limit Test**: Abuse prevention effectiveness

### 5.4 Performance & Load Testing
- [ ] **Synthesis Latency**: Measure for 100, 1000, 5000 word texts
- [ ] **End-to-End Latency**: API request → audio available (target <5 min)
- [ ] **Load Test**: 10 concurrent jobs, measure system stability
- [ ] **Queue Performance**: Message throughput, DLQ handling
- [ ] **S3 Performance**: Upload/download speed, concurrent access
- [ ] **Database Performance**: Cache lookup speed, concurrent updates

### 5.5 Documentation
- [ ] **API Documentation**: OpenAPI/Swagger specification
- [ ] **Architecture Guide**: System diagrams, component interactions
- [ ] **Deployment Guide**: Environment setup, configuration
- [ ] **Troubleshooting Guide**: Common issues and solutions
- [ ] **Monitoring Guide**: Metrics, logs, alerting configuration
- [ ] **Security Guide**: Authentication, authorization, compliance

---

## Phase 6: Deployment & Production Launch (Week 6)

### 6.1 Production Preparation
- [ ] **Environment Configuration**: .env.example files for all repos
- [ ] **Database Migration**: Production schema validation
- [ ] **RabbitMQ Configuration**: Production queue setup with DLQ
- [ ] **S3 Configuration**: Bucket policies, lifecycle rules, CORS
- [ ] **Monitoring Setup**: Prometheus, Grafana, alerting rules
- [ ] **Backup Strategy**: Database backups, S3 versioning

### 6.2 Gradual Rollout Strategy
- [ ] **Feature Flags**: Enable/disable new endpoints
- [ ] **Canary Deployment**: Roll out to small user group first
- [ ] **A/B Testing**: Compare old vs new TTS performance
- [ ] **Rollback Plan**: Quick revert if issues detected
- [ ] **Performance Baseline**: Establish before/after metrics

### 6.3 Production Deployment
- [ ] **Studio Backend**: Deploy with extended TTSJob schema
- [ ] **IndexTTS Worker**: Deploy with circuit breaker and DLQ
- [ ] **Official Landing**: Deploy playground component
- [ ] **Database Migration**: Apply production migrations
- [ ] **Queue Migration**: Transition to new message formats
- [ ] **Cache Warmup**: Pre-load common voice/text combinations

### 6.4 Post-Launch Monitoring
- [ ] **Error Rate Monitoring**: Track synthesis failures
- [ ] **Performance Monitoring**: Latency percentiles, queue depths
- [ ] **Usage Analytics**: Playground adoption, cache hit rates
- [ ] **Cost Monitoring**: S3 storage costs, compute usage
- [ ] **Security Monitoring**: Unusual access patterns, rate limit violations
- [ ] **User Feedback**: Collect and analyze playground feedback

### 6.5 Maintenance & Operations
- [ ] **DLQ Monitoring**: Regular review of dead-letter messages
- [ ] **Cache Management**: Periodic cache cleanup and optimization
- [ ] **Voice Management**: Admin tools for voice approval/management
- [ ] **Performance Optimization**: Continuous latency improvement
- [ ] **Security Updates**: Regular vulnerability scanning and patching
- [ ] **Capacity Planning**: Scale prediction and resource allocation