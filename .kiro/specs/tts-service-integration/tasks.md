# TTS Service Integration - Implementation Tasks

## Phase 1: Core Infrastructure (Week 1-2)

### 1.1 Create Database Models (studio-backend)
- [ ] Create UserTTSJob ORM model with fields: user_id, project_id, text, voice_id, language, status, progress, retry_count, output_s3_url, audio_duration_seconds, error_message, correlation_id, timestamps
- [ ] Create PlaygroundTTSJob ORM model with fields: text, audio_prompt_id, language, status, progress, retry_count, output_s3_url, audio_duration_seconds, error_message, client_ip_address, user_agent, referrer, correlation_id, timestamps
- [ ] Create PlaygroundAudioPrompt model with fields: prompt_id, language, s3_url, description
- [ ] Create RateLimitCounter model with fields: ip_address, request_count, window_start, window_end
- [ ] Add models to alembic/env.py for migration detection
- [ ] Generate database migration file with `alembic revision --autogenerate -m "Add TTS job tables"`
- [ ] Apply migration: `alembic upgrade head`
- [ ] Write test: verify all models can be created, queried, and have proper constraints

### 1.2 Create Database Models (indexTTS-worker)
- [ ] Create TTSWorkerJob ORM model with fields matching design spec (id, user_id, project_id, text, audio_prompt_url, language, status, current_attempt, output_s3_path, audio_duration_seconds, error_code, error_message, timestamps, worker_instance_id, platform, engine_type)
- [ ] Add database connection configuration to `services/tts_worker.py` using PostgreSQL async driver
- [ ] Create Alembic setup in indexTTS-worker repo (if not exists)
- [ ] Generate initial migration
- [ ] Write test: verify TTSWorkerJob model persistence

### 1.3 Populate Playground Audio Prompts
- [ ] Add 3-5 playground audio prompt files to official-landing/public/audio/prompts/ (e.g., en_neutral.wav, en_upbeat.wav, zh_neutral.wav)
- [ ] Create PlaygroundAudioPrompt database records for each prompt
- [ ] Map prompt_id to S3 URLs in studio-backend config
- [ ] Write test: verify playgrounds can load prompt list and prompts have correct S3 URLs

### 1.4 Setup RabbitMQ Configuration
- [ ] Verify RabbitMQ connection in studio-backend (RABBITMQ_URL env var)
- [ ] Create queue declarations for tts_jobs and tts_results (durable=true, TTL=24h for jobs, 7d for results)
- [ ] Test RabbitMQ connection from both indexTTS-worker and studio-backend
- [ ] Write test: verify queues exist and can publish/consume messages

---

## Phase 2: Backend TTS Endpoints (Week 2-3)

### 2.1 Implement Playground TTS Endpoint
- [ ] Create POST /api/v1/playground/tts endpoint in studio-backend/app/routers/
- [ ] Validate request: text (max 500 chars), audio_prompt_id (required), language (required)
- [ ] Implement rate limiting: 5 requests per IP per hour using RateLimitCounter table
- [ ] Extract client IP address (handle proxies: X-Forwarded-For, X-Real-IP)
- [ ] Create PlaygroundTTSJob record with: text, audio_prompt_id, language, status="pending", client_ip_address, correlation_id (UUID)
- [ ] Look up PlaygroundAudioPrompt by prompt_id, get s3_url
- [ ] Publish message to tts_jobs queue with: job_id, text, audio_prompt_url, language, is_playground=true, user_metadata (correlation_id)
- [ ] Return 202 Accepted with: job_id, status, stream_url
- [ ] Write test: verify endpoint accepts valid requests and returns 202
- [ ] Write test: verify rate limiting returns 429 after 5 requests
- [ ] Write test: verify text length validation returns 400 if >500 chars

### 2.2 Implement Authenticated TTS Endpoint
- [ ] Create POST /api/v1/tts endpoint in studio-backend/app/routers/
- [ ] Require authentication (bearer token)
- [ ] Validate request: project_id (required), text (max 5000 chars), voice_id (required), language (required)
- [ ] Query Voice table by voice_id, verify it exists
- [ ] Check voice permissions: if voice is private (is_public=false), verify user owns it or return 403
- [ ] Implement cache check: query UserTTSJob where voice_id, text match and created_at > now()-30days and status="completed"
- [ ] If cache hit: return existing job with is_cached=true and s3_url
- [ ] If cache miss: create new UserTTSJob record with: user_id (from token), project_id, text, voice_id, language, status="pending", correlation_id
- [ ] Get voice s3_url, publish to tts_jobs queue with: job_id, text, audio_prompt_url (voice s3_url), language, user_metadata (user_id, project_id, voice_id, correlation_id)
- [ ] Return 202 Accepted (or 200 OK for cached) with: job_id, status, stream_url, is_cached
- [ ] Write test: verify authenticated requests return 202
- [ ] Write test: verify cache returns existing s3_url for same voice+text
- [ ] Write test: verify permission check returns 403 for private voices not owned by user
- [ ] Write test: verify 404 for non-existent voice_id

### 2.3 Implement TTS Status Endpoint
- [ ] Create GET /api/v1/tts/{job_id} endpoint
- [ ] Query UserTTSJob or PlaygroundTTSJob by job_id
- [ ] Handle permission: if UserTTSJob, verify user owns project
- [ ] Return job status with: job_id, status, progress (0-100 if processing)
- [ ] If completed: include audio_duration_seconds, s3_url
- [ ] If failed: include error_message, error_code, retry_count
- [ ] Write test: verify status endpoint returns current job status

### 2.4 Implement TTS SSE Streaming Endpoint
- [ ] Create GET /api/v1/tts/{job_id}/stream endpoint (application/event-stream)
- [ ] Query job by job_id, verify permission (if UserTTSJob)
- [ ] Start SSE connection with 30-second heartbeat
- [ ] Poll job status every 2 seconds (or use database notifications if supported)
- [ ] Send "progress" events while status="processing" with: progress percentage, estimated_time_remaining
- [ ] Send "completed" event when status="completed" with: s3_url, audio_duration_seconds, audio_format
- [ ] Send "failed" event when status="failed" with: error_message, error_code, retry_count
- [ ] Close connection after terminal event
- [ ] Write test: verify SSE sends progress events
- [ ] Write test: verify SSE sends completed event with correct data
- [ ] Write test: verify SSE sends failed event with error details

---

## Phase 3: Backend TTS Consumer (Week 3)

### 3.1 Implement TTS Results Consumer
- [ ] Create app/services/tts_consumer.py or integrate into background_worker.py
- [ ] Subscribe to tts_results RabbitMQ queue
- [ ] Consume messages with: job_id, status, output_s3_path (if completed), audio_duration_seconds, error_code, error_message (if failed)
- [ ] Query UserTTSJob or PlaygroundTTSJob by job_id
- [ ] For completed jobs: update status="completed", output_s3_url, audio_duration_seconds, completed_at
- [ ] For failed jobs: update status="failed", error_message, error_code, completed_at
- [ ] For playground jobs: set expires_at = now() + 30 days
- [ ] Log job completion with: job_id, duration, status, s3_url, correlation_id
- [ ] Write test: verify consumer updates job records correctly on completion
- [ ] Write test: verify consumer updates job records with error details on failure

---

## Phase 4: TTS Worker Service (Week 4)

### 4.1 Implement TTS Worker Core
- [ ] Replace services/tts_worker.py with production implementation
- [ ] Implement RabbitMQ connection (pika or aio-pika) with connection pooling
- [ ] Consume from tts_jobs queue with prefetch_count=1
- [ ] Implement job validation: check job_id, text, audio_prompt_url, language present
- [ ] Create/update TTSWorkerJob record with status="received"
- [ ] Log job intake with job_id, correlation_id
- [ ] Write test: verify worker can connect to RabbitMQ and consume messages

### 4.2 Implement Audio Prompt Download
- [ ] Create _download_audio_prompt method
- [ ] Implement S3 download with boto3 (with retry logic: 3 attempts, exponential backoff 5s, 15s)
- [ ] Save to /tmp/tts-{job_id}/prompt.wav
- [ ] Validate audio format (must be WAV, MP3, or FLAC)
- [ ] Validate audio duration (must be >500ms and <60s)
- [ ] On download failure: update TTSWorkerJob with error_code="AUDIO_PROMPT_DOWNLOAD_FAILED", status="failed", attempt count
- [ ] On download success: update TTSWorkerJob status="synthesizing"
- [ ] Write test: verify download succeeds for valid S3 URLs
- [ ] Write test: verify retry logic retries on timeout
- [ ] Write test: verify validation rejects invalid audio files

### 4.3 Implement Audio Synthesis
- [ ] Create _synthesize_audio method
- [ ] Load audio prompt into TTS engine
- [ ] Call tts.infer_fast() or tts.infer() based on text length (fast for >100 words)
- [ ] Capture output_path, audio_duration_seconds, actual synthesis_duration_seconds
- [ ] Handle synthesis errors: distinguish retryable vs non-retryable
- [ ] Retryable errors: increment attempt, retry up to 3 times with exponential backoff
- [ ] Non-retryable errors: fail immediately with error_code and error_message
- [ ] On synthesis success: update TTSWorkerJob status="uploading"
- [ ] Write test: verify synthesis produces WAV output
- [ ] Write test: verify synthesis captures audio duration
- [ ] Write test: verify GPU OOM error is marked retryable

### 4.4 Implement S3 Upload
- [ ] Create _upload_to_s3 method
- [ ] Generate S3 key: tts-output/{job_id}/{timestamp}.wav
- [ ] Upload with metadata tags: job_id, user_id, project_id, language, created_timestamp
- [ ] Set expiration: 24 hours for playground jobs, indefinite for user jobs
- [ ] Implement retry logic: 3 attempts with exponential backoff
- [ ] On upload failure: mark error_code="S3_UPLOAD_FAILED", don't update status yet (will retry)
- [ ] On upload success: capture output_s3_path (full S3 URL)
- [ ] Write test: verify upload creates S3 object with correct path
- [ ] Write test: verify metadata tags are set correctly
- [ ] Write test: verify expiration policy is applied

### 4.5 Implement Result Publication
- [ ] Create _publish_result method
- [ ] Publish message to tts_results queue with: job_id, status, output_s3_path (if successful), audio_duration_seconds, synthesis_duration_seconds, timestamp, user_metadata
- [ ] For failures: include error_code, error_message, retry_count
- [ ] Implement retry logic for ACK: 3 attempts with exponential backoff
- [ ] On ACK failure: log failure with job_id and mark locally as ack_failed
- [ ] On ACK success: proceed to cleanup
- [ ] Write test: verify result message is published correctly
- [ ] Write test: verify ACK retry logic retries on failure

### 4.6 Implement Cleanup
- [ ] Create _cleanup method
- [ ] Delete /tmp/tts-{job_id}/ directory
- [ ] Log cleanup with job_id
- [ ] If cleanup fails: log warning (non-blocking)
- [ ] Write test: verify cleanup removes temp files

### 4.7 Implement Error Handling & Logging
- [ ] Add comprehensive logging throughout worker lifecycle
- [ ] Log with: job_id, operation, timestamp, duration, language, status, error details
- [ ] Add correlation_id to all logs
- [ ] Implement graceful shutdown: handle SIGTERM, complete in-flight jobs before exit
- [ ] Add worker instance tracking: log worker_instance_id in TTSWorkerJob
- [ ] Add platform detection: log "Darwin" for macOS, "Linux" for Linux
- [ ] Add engine type tracking: log "macos_native" or "indextts_gpu"
- [ ] Write test: verify error logging includes all required fields
- [ ] Write test: verify graceful shutdown completes in-flight jobs

---

## Phase 5: Landing Page Integration (Week 4-5)

### 5.1 Create Playground UI Component (official-landing)
- [ ] Create src/components/TTSPlayground.tsx component
- [ ] Implement form with: text input (max 500 chars counter), audio_prompt selector dropdown, language selector dropdown
- [ ] Language options: en, zh, es, fr, de, ja (from i18n config)
- [ ] Audio prompt options: load from hardcoded list or API call
- [ ] Implement form validation: text non-empty, prompt selected, language selected
- [ ] Submit button triggers POST /api/v1/playground/tts with request body
- [ ] Handle response: capture job_id and stream_url
- [ ] Write test: verify form validation works
- [ ] Write test: verify form submission sends correct request

### 5.2 Implement SSE Streaming in Playground
- [ ] Create hooks/useTTSStream.ts for SSE subscription
- [ ] Subscribe to GET /api/v1/tts/{job_id}/stream after job creation
- [ ] Handle progress events: update progress bar, show estimated_time_remaining
- [ ] Handle completed event: display audio player with s3_url
- [ ] Handle failed event: display error message with retry button
- [ ] Implement connection timeout: if no heartbeat for 60 seconds, show error and allow retry
- [ ] Write test: verify SSE connection established
- [ ] Write test: verify progress updates are displayed

### 5.3 Implement Audio Playback
- [ ] Create components/TTSAudioPlayer.tsx
- [ ] Accept s3_url as prop
- [ ] Render HTML5 audio element with controls
- [ ] Implement stream loading from S3 URL (with CORS headers)
- [ ] Handle playback errors gracefully
- [ ] Write test: verify audio player renders with controls
- [ ] Write test: verify audio loads from S3 URL

### 5.4 Implement Graceful Degradation
- [ ] Wrap playground component in error boundary
- [ ] If TTS API is unreachable: catch error, display message "TTS service temporarily unavailable"
- [ ] Page should remain functional, other sections should load normally
- [ ] If SSE times out: display retry button instead of crashing
- [ ] Write test: verify error boundary catches API errors
- [ ] Write test: verify page loads without TTS component if API unreachable

### 5.5 Implement i18n for Playground
- [ ] Add language translations in src/locales/
- [ ] Translate labels: "Text", "Audio Prompt", "Language", "Generate", "Processing", "Error"
- [ ] Translate error messages: "Text too long", "TTS service unavailable", "Please try again"
- [ ] Use next-intl for locale switching
- [ ] Write test: verify translations load correctly
- [ ] Write test: verify UI responds to locale changes

---

## Phase 6: Testing & Quality (Week 5-6)

### 6.1 Write Property-Based Tests (PBT)
- [ ] **Idempotence Test**: Same (voice_id, text) within 30 days returns same S3 URL
- [ ] **Round-trip Test**: Audio encoding/decoding preserves content (decode(encode(x)) == x)
- [ ] **State Machine Test**: Job status transitions are valid (pending → processing → completed/failed only)
- [ ] **Text Length Monotonicity**: Longer text → longer or equal audio duration
- [ ] **Rate Limiting**: 5 requests per IP per hour maximum enforced
- [ ] **Retry Exhaustion**: Failed jobs after 3 retries marked as failed with error details

### 6.2 Write Integration Tests
- [ ] Test full pipeline: playground endpoint → RabbitMQ → worker → S3 → results queue → database
- [ ] Test authenticated endpoint: submit job → worker processes → SSE updates → completion
- [ ] Test error scenarios: invalid audio prompt, synthesis failure, S3 upload failure
- [ ] Test race conditions: multiple workers processing same queue
- [ ] Test graceful degradation: landing page works if TTS service down

### 6.3 Performance Testing
- [ ] Measure synthesis latency for different text lengths (100 words, 1000 words, 5000 words)
- [ ] Measure end-to-end latency: API request → worker picks up → results back (target <5 min for typical text)
- [ ] Load test: 10 concurrent jobs to verify worker scales
- [ ] Measure S3 upload speed and cost

### 6.4 Documentation
- [ ] Create docs/TTS_INTEGRATION_GUIDE.md with architecture, setup, usage examples
- [ ] Document RabbitMQ message format and error codes
- [ ] Document database schema changes (migration guide)
- [ ] Create troubleshooting guide for common issues
- [ ] Document rate limiting and cache behavior

---

## Phase 7: Deployment & Production Launch (Week 6)

### 7.1 Production Preparation
- [ ] Create deployment documentation for all three repos
- [ ] Test database migrations on production-like environment
- [ ] Set up environment variables for all repos (.env.example files)
- [ ] Verify RabbitMQ queue configuration in production

### 7.2 Production Deployment
- [ ] Deploy studio-backend with new TTS endpoints and models
- [ ] Deploy indexTTS-worker service
- [ ] Deploy official-landing with playground component
- [ ] Test full pipeline end-to-end in production

### 7.3 Launch & Monitoring
- [ ] Enable playground feature for production users
- [ ] Monitor error logs, job success rates, and API latency
- [ ] Verify RabbitMQ queues processing jobs correctly
- [ ] Check S3 upload and storage patterns

