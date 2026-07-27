# Requirements Document

## TTS Service Integration Requirements

## Introduction

This feature spec covers the integration of the IndexTTS text-to-speech engine across three repositories:

1. **indexTTS-worker**: Core TTS service and RabbitMQ worker
2. **official-landing**: Next.js playground for anonymous TTS demonstrations  
3. **studio-backend**: FastAPI backend for production TTS jobs with user voice recordings

The feature enables:
- Anonymous users to test TTS with approved community voices using full text up to 200 words (playground)
- Authenticated users to generate quick TTS audio previews using only the first 2 sentences of their script text (studio-backend)
- Production TTS synthesis with automatic retry, monitoring, and S3 storage
- Support for GPU-based IndexTTS inference with optional macOS native TTS in dev/staging

**Key Simplifications:**
- Removed confusing `preview_text` concept
- Simplified caching strategy: playground uses full text, studio-backend uses only first 2 sentences
- Studio-backend TTS jobs are fast and quick by processing only first 2 sentences
- Full-script-text TTS synthesis is never performed for studio-backend

---

## Glossary

- **TTS (Text-to-Speech)**: Process of synthesizing spoken audio from text input
- **IndexTTS**: GPU-based TTS inference engine supporting multiple languages
- **macOS Native TTS**: AVFoundation-based synthesis available on macOS systems
- **Audio Prompt**: Reference audio file used by IndexTTS to match speaker voice characteristics
- **TTS Job**: Asynchronous task submitted to synthesize text into audio
- **RabbitMQ Queue**: Message broker queue for TTS job distribution (tts_jobs and tts_results)
- **TTS Worker**: Background service consuming jobs from RabbitMQ and synthesizing audio
- **Playground**: Anonymous TTS demonstration feature on official-landing
- **Studio Backend**: Authenticated backend service managing TTS jobs and voice recordings
- **S3**: Object storage service used for persisting synthesized audio results
- **SSE (Server-Sent Events)**: HTTP protocol for streaming real-time updates to clients
- **Voice Catalog**: Database of voice recordings (user recordings + community voices) in studio-backend
- **Playground Text**: Full text up to 200 words used for anonymous TTS demonstrations
- **Studio Processed Text**: First 2 sentences of script text used for quick previews in studio-backend

---

## Requirements

### Requirement 1: TTS Worker Service (indexTTS-worker)

**User Story:** As an operations engineer, I want a robust TTS worker service that processes synthesis jobs from a message queue, so that the system can handle asynchronous TTS requests at scale.

#### Acceptance Criteria

1. THE IndexTTS_Worker service SHALL read TTS synthesis requests from the RabbitMQ `tts_jobs` queue.
2. WHEN a job is received, THE IndexTTS_Worker SHALL validate that the job contains required fields: job_id, text, audio_prompt_url, and language.
3. IF audio_prompt_url is invalid or unreachable, THE IndexTTS_Worker SHALL automatically retry the job up to 3 times before marking it as failed.
4. THE IndexTTS_Worker SHALL synthesize audio using IndexTTS GPU inference when running on non-macOS systems.
5. WHEN running on macOS, THE IndexTTS_Worker MAY use native macOS TTS (AVFoundation) for quick testing and development.
6. AFTER synthesis completes successfully, THE IndexTTS_Worker SHALL upload the audio file to S3 and publish a completion message to the RabbitMQ `tts_results` queue.
7. THE TTS_Results message SHALL include: job_id, status (completed/failed), output_s3_path (if successful), audio_duration_seconds, error_message (if failed), and timestamp.
8. IF synthesis fails after retries, THE IndexTTS_Worker SHALL publish a failure message to tts_results queue with error details.
9. WHEN a job is processed successfully or fails, THE IndexTTS_Worker SHALL acknowledge the message to RabbitMQ to prevent reprocessing.
10. THE IndexTTS_Worker SHALL handle SIGTERM signals gracefully and complete in-flight jobs before shutdown.

---

### Requirement 2: Studio TTS Job Submission API (studio-backend)

**User Story:** As a studio backend service, I want to submit TTS jobs using existing TTSJob schema with simplified text caching, so that authenticated users can generate audio from their voice recordings with immediate response.

#### Acceptance Criteria

1. THE Studio_Backend SHALL provide a POST `/api/v1/tts` endpoint to create TTS jobs using the existing `TTSJob` table.
2. WHEN a TTS job is created, THE Studio_Backend SHALL accept: project_id (required), text (required), voice_id (required), and language (required, not optional).
3. THE Studio_Backend SHALL always process only the first 2 sentences of the script text for studio-backend TTS jobs, regardless of the full script length.
4. WHEN the request arrives, THE Studio_Backend SHALL validate voice_id exists in the Voice table and user has permission:
   - Private voices (`is_shared=false`): User must be owner
   - Shared voices (`is_shared=true`): Any user can use
   - Approved voices (`is_shared=true AND is_approved=true`): All users (also eligible for playground)
5. IF voice_id does not exist in the database, THE Studio_Backend SHALL return 404 Not Found.
6. IF voice_id exists but user lacks permission (private voice not owned by user), THE Studio_Backend SHALL return 403 Forbidden.
7. THE Studio_Backend SHALL apply content-based caching based on processed text (first 2 sentences):
   - IF an identical `(voice_id, processed_text)` pair was successfully synthesized within the last 30 days, return existing audio_path immediately
   - Processed text SHALL be the first 2 sentences extracted from the full script text
8. IF no cache hit, THE Studio_Backend SHALL create a TTSJob record with:
   - Extended fields: language (required), correlation_id (UUID), full_text_hash (SHA256 of full text)
   - Status: "queued" (matching existing state machine)
   - audio_path: null (will be populated by worker)
9. THE Studio_Backend SHALL publish to RabbitMQ tts_jobs queue with path-based storage:
   - `audio_prompt_path`: Path to voice recording (e.g., "audio-prompts/{voice_id}.wav")
   - `output_path_template`: "tts-output/studio/{job_id}.wav"
   - `job_type`: "studio"
   - `processed_text`: First 2 sentences of the script text (used for caching)
10. THE Studio_Backend SHALL return appropriate response:
   - 202 Accepted (new job): job_id, status="queued", stream_url, is_cached=false
   - 200 OK (cached): job_id, status="completed", audio_path, audio_duration_seconds, is_cached=true
11. WHEN the consumer receives a tts_results message, THE Studio_Backend SHALL:
   - Validate audio_path exists in S3 before updating record
   - For failed jobs, validate error_message is present
   - Update TTSJob record with status, audio_path, audio_duration, completed_at
12. THE Studio_Backend SHALL provide polling endpoint GET `/api/v1/tts/{job_id}` for status queries (clients poll every 2-5 seconds).
13. FOR Studio-Backend TTS Jobs, THE System SHALL NEVER perform full-script-text TTS synthesis - only the first 2 sentences shall be processed to ensure fast and quick response times for logged-in users.

---

### Requirement 3: Playground TTS Endpoint (official-landing + studio-backend)

**User Story:** As an anonymous user, I want to test the TTS service with approved community voices using full text (up to 200 words), so that I can evaluate quality without signing up.

#### Acceptance Criteria

1. THE Playground SHALL provide an anonymous POST `/api/v1/playground/tts` endpoint accessible without authentication.
2. WHEN a playground TTS request is received, THE Studio_Backend SHALL validate:
   - `text`: Required, max 200 words (≈1000 chars), non-empty
   - `voice_id`: Required, must be approved community voice (`is_shared=true AND is_approved=true`)
   - `language`: Required, must match voice language
3. THE Studio_Backend SHALL apply rate limiting using hashed IP addresses:
   - Maximum 5 requests per hashed IP per hour
   - IP addresses SHALL be hashed with SHA256 for privacy
   - Rate limit counters stored in RateLimitCounter table
4. IF rate limit is exceeded, THE Studio_Backend SHALL:
   - Create PlaygroundTTSJob record with status "rate_limited"
   - Return 429 Too Many Requests with retry_after: 3600 seconds
5. WHEN rate limiting is not exceeded, THE Studio_Backend SHALL create PlaygroundTTSJob record with:
   - UUID primary key (different from TTSJob integer IDs)
   - Full text storage
   - Status: "queued" (matching TTSJob state machine)
   - Hashed client_ip_address for rate limiting
   - expires_at: created_at + 30 days (automatic cleanup)
6. THE Studio_Backend SHALL apply content-based caching for playground:
   - Cache based on `(voice_id, full_text)` pairs within 30 days
   - Use SHA256 hash of full text for cache lookup
   - Different cache strategy than studio: playground uses full text, studio uses first 2 sentences only
7. THE Studio_Backend SHALL publish to RabbitMQ tts_jobs queue with:
   - `job_type`: "playground"
   - `output_path_template`: "tts-output/playground/{job_id}.wav"
   - Path-based audio_prompt_path from voice table
   - `text`: Full text (up to 200 words) for playground synthesis
8. THE Studio_Backend SHALL return 202 Accepted with:
   - job_id (UUID), status="queued", expires_at
   - No user authentication required
9. WHEN the playground job completes, THE Studio_Backend SHALL:
   - Return updated status via polling endpoint GET `/api/v1/tts/{job_id}`
   - Retain PlaygroundTTSJob records for 30 days (automatic cleanup via expires_at)
   - Use path-based audio_path for S3 access

---

### Requirement 4: Job Status Polling (studio-backend)

**User Story:** As a client application, I want to poll for TTS job status updates, so that I can display progress to users with a simple, scalable approach.

#### Acceptance Criteria

1. THE Studio_Backend SHALL provide GET `/api/v1/tts/{job_id}` endpoint for retrieving current job status.
2. THE Endpoint SHALL return complete job information including: job_id, status, progress (0-100), audio_path (if completed), audio_duration_seconds (if completed), error_message (if failed), retry_count, created_at, started_at, completed_at.
3. THE Endpoint SHALL support both authenticated requests (studio jobs) and unauthenticated requests (playground jobs with UUID).
4. WHEN the job is processing, THE Response SHALL include current progress percentage (0-100) for display to users.
5. WHEN the job completes successfully, THE Response SHALL include audio_path and audio_duration_seconds for immediate playback.
6. WHEN the job fails, THE Response SHALL include error_message and retry_count for debugging and user feedback.
7. THE Client SHALL poll this endpoint every 2-5 seconds while job status is "queued" or "processing".
8. THE Client SHALL stop polling when status becomes "completed", "failed", or "rate_limited".
9. IF the client stops polling, THE TTS_Job SHALL continue processing in the background.
10. THE Studio_Backend SHALL set appropriate cache headers (no-cache) to prevent stale status responses.

---

### Requirement 5: Audio Prompt Management (indexTTS-worker + studio-backend)

**User Story:** As the system, I want to manage audio prompts for both playground and authenticated users, so that TTS jobs can reference the correct voice files.

#### Acceptance Criteria

1. THE System SHALL store playground audio prompts as static files in official-landing's public folder with identifiers (e.g., prompt_en_neutral.wav).
2. THE System SHALL map playground audio_prompt_id to S3 URLs in studio-backend configuration or database.
3. THE Studio_Backend SHALL store user voice recordings from the voice catalog with S3 URLs indexed by voice_id.
4. THE IndexTTS_Worker SHALL download audio_prompt_url from S3 before synthesis.
5. IF audio_prompt_url download fails, THE IndexTTS_Worker SHALL retry up to 3 times with exponential backoff.
6. AFTER successful synthesis, THE IndexTTS_Worker SHALL delete the downloaded audio_prompt file from local disk.

---

### Requirement 6: Error Handling and Retry Logic (indexTTS-worker)

**User Story:** As an operations engineer, I want failed synthesis jobs to automatically retry with proper logging, so that transient failures don't permanently block users.

#### Acceptance Criteria

1. IF IndexTTS synthesis encounters an error, THE IndexTTS_Worker SHALL log the error with: job_id, error_type, error_message, and stack_trace.
2. THE IndexTTS_Worker SHALL implement exponential backoff retry with: 1st attempt immediate, 2nd attempt after 5 seconds, 3rd attempt after 15 seconds.
3. AFTER 3 failed attempts, THE IndexTTS_Worker SHALL publish a failure message to tts_results queue with final error details.
4. THE IndexTTS_Worker SHALL distinguish between retryable errors (network, S3 timeout) and non-retryable errors (invalid audio prompt, OOM).
5. FOR non-retryable errors, THE IndexTTS_Worker SHALL fail immediately without retrying.
6. WHEN a job fails, THE IndexTTS_Worker SHALL include error_code and error_message in the tts_results message for studio-backend logging.

---

### Requirement 7: Language Support (indexTTS-worker + studio-backend)

**User Story:** As a user, I want TTS to support multiple languages, so that I can synthesize audio in different locales.

#### Acceptance Criteria

1. THE IndexTTS_Worker SHALL accept language parameter in job message (e.g., "en", "zh", "es").
2. WHEN synthesizing with IndexTTS, THE IndexTTS_Worker SHALL pass language to the inference engine.
3. THE Studio_Backend AND Official_Landing Playground SHALL require language as a mandatory field in TTS requests (no default fallback).
4. THE Studio_Backend SHALL validate language against supported_languages configuration only when TTS requests are received.
5. IF language is not supported, THE Studio_Backend SHALL return 400 Bad Request.
6. THE Studio_Backend SHALL store language with TTS_Job record for auditing and metrics.
7. BOTH repositories SHALL implement internationalization (i18n) to display language options and error messages in the user's locale.

---

### Requirement 8: Graceful Degradation (official-landing)

**User Story:** As a playground user, I want the landing page to remain functional even if the TTS service is temporarily unavailable, so that the site continues to work.

#### Acceptance Criteria

1. WHEN the TTS endpoint is unreachable, THE Official_Landing Playground SHALL display a user-friendly error message: "TTS service temporarily unavailable. Please try again later."
2. THE Official_Landing SHALL NOT fail to load if the TTS endpoint is down.
3. THE Official_Landing SHALL use client-side error handling to gracefully degrade the TTS feature without affecting other page functionality.
4. WHEN a TTS request times out after 60 seconds, THE Official_Landing SHALL notify the user and allow retry.
5. WHEN the TTS endpoint is completely unreachable from the start, THE Official_Landing SHALL NOT offer a retry option to users.

---

### Requirement 9: Storage and Cleanup (indexTTS-worker)

**User Story:** As an operations engineer, I want TTS results to be stored durably and temporary files cleaned up, so that storage costs are minimized.

#### Acceptance Criteria

1. THE IndexTTS_Worker SHALL upload synthesized audio to S3 under path: `s3://bucket/tts-output/{job_id}/{timestamp}.{format}`
2. THE IndexTTS_Worker SHALL include metadata tags in S3 object: job_id, user_id, project_id, language, created_timestamp.
3. THE IndexTTS_Worker SHALL delete local temporary files (audio_prompt, output audio) after successful S3 upload.
4. IF S3 upload fails, THE IndexTTS_Worker SHALL retry up to 3 times before failing the job.
5. THE Studio_Backend SHALL set S3 object expiration policy: delete playground audio after 24 hours, keep user audio indefinitely.

---

### Requirement 10: Monitoring and Logging (indexTTS-worker + studio-backend)

**User Story:** As an operations engineer, I want comprehensive logging of TTS operations, so that I can troubleshoot issues and track system health.

#### Acceptance Criteria

1. THE IndexTTS_Worker SHALL log: job_id, operation (start, complete, fail), timestamp, duration_seconds, language, status_code.
2. THE IndexTTS_Worker SHALL log synthesis errors with: error_type, error_message, stack_trace, retry_count, attempt_number.
3. THE IndexTTS_Worker SHALL integrate with a structured logging system (ELK, Loki, or Prometheus) for centralized log aggregation and monitoring.
4. THE Studio_Backend SHALL log TTS requests with: job_id, user_id, project_id, text_length, voice_id, language, created_at.
5. THE Studio_Backend SHALL log rate limit violations with: ip_address, request_count, window_timestamp.
6. WHEN a job completes, THE Studio_Backend AND IndexTTS_Worker SHALL both log: job_id, duration_seconds, audio_duration_seconds, status, s3_url.
7. ALL logs SHALL include timestamps in ISO 8601 format and correlation IDs for tracing request flow.
8. ALL logs from IndexTTS_Worker SHALL include worker_instance_id for distributed system debugging.

---

### Requirement 11: RabbitMQ Integration (indexTTS-worker + studio-backend)

**User Story:** As the system, I want TTS jobs and results to flow through a reliable message broker, so that components can scale independently.

#### Acceptance Criteria

1. THE Studio_Backend SHALL publish TTS job messages to RabbitMQ queue `tts_jobs` with durable=true.
2. THE RabbitMQ Queue tts_jobs SHALL have message TTL of 24 hours.
3. THE IndexTTS_Worker SHALL set prefetch_count=1 to process one job at a time.
4. WHEN a message is successfully processed, THE IndexTTS_Worker SHALL send basic_ack to acknowledge.
5. IF the basic_ack fails due to network issues, THE IndexTTS_Worker SHALL retry sending the ack up to 3 times with exponential backoff.
6. IF all ack retries fail, THE IndexTTS_Worker SHALL mark the job as failed, log the failure with job_id and error details, and maintain local tracking of the failure without further retries.
7. WHEN processing fails, THE IndexTTS_Worker SHALL send basic_nack with requeue=false (failed jobs go to tts_results, not back to queue).
8. THE Studio_Backend Consumer SHALL subscribe to `tts_results` queue and process all result messages (both completed and failed jobs).
9. THE Studio_Backend Consumer SHALL update TTS_Job database records within 5 seconds of receiving result message for all outcomes.
10. WHEN RabbitMQ connection is lost, THE IndexTTS_Worker SHALL attempt reconnection with exponential backoff (max 30 seconds).

---

### Requirement 12: Dead-Letter Queues and Circuit Breaker

**User Story:** As an operations engineer, I want reliable message processing with dead-letter queues and circuit breaker patterns, so that the system handles failures gracefully without cascading effects.

#### Acceptance Criteria

1. THE RabbitMQ configuration SHALL include dead-letter queues:
   - `tts_jobs_dlq`: For messages rejected after 3 retries from `tts_jobs`
   - `tts_results_dlq`: For failed result processing from `tts_results`
   - DLQs SHALL have 7-day TTL for manual investigation
2. THE `tts_jobs` queue SHALL be configured with:
   - `x-dead-letter-exchange`: '' (default exchange)
   - `x-dead-letter-routing-key`: 'tts_jobs_dlq'
   - `x-message-ttl`: 86400000 (24 hours in milliseconds)
   - `x-max-length`: 10000 (prevent unlimited buildup)
3. THE IndexTTS_Worker SHALL implement circuit breaker pattern:
   - Monitor S3 download failures (threshold: 5 failures in 60 seconds)
   - Monitor IndexTTS synthesis failures (threshold: 3 failures in 30 seconds)
   - Circuit states: CLOSED (normal), OPEN (blocked), HALF_OPEN (testing)
   - Automatic reset after 60 seconds in OPEN state
4. FOR partial failures (S3 upload succeeds, RabbitMQ ack fails):
   - THE IndexTTS_Worker SHALL implement idempotent retry:
     - Tag S3 object with `job_id` and `status=uploaded`
     - On retry, check if S3 object exists with same tags
     - Skip upload if already exists, proceed to RabbitMQ publishing
   - Maximum 3 retry attempts with exponential backoff
5. WHEN all ack retries fail after S3 upload success:
   - THE IndexTTS_Worker SHALL log critical error with job_id
   - S3 object SHALL remain with `status=uploaded` tag
   - Manual intervention required to reconcile state
   - No automatic job requeueing to prevent duplicates
6. THE System SHALL monitor DLQ depths and alert when:
   - `tts_jobs_dlq` exceeds 100 messages
   - `tts_results_dlq` exceeds 50 messages
   - Any message remains in DLQ for >24 hours

### Requirement 13: Voice Catalog Integration (studio-backend)

**User Story:** As studio-backend, I want to use the existing three-state voice system (private/shared/approved) with path-based storage, so that users can leverage their recordings with proper permissions.

#### Acceptance Criteria

1. THE Studio_Backend SHALL query the existing Voice table with three-state system:
   - `is_shared=false`: Private voice (owner only)
   - `is_shared=true, is_approved=false`: Shared voice (owner + collaborators)
   - `is_shared=true, is_approved=true`: Approved voice (all users, playground eligible)
2. WHEN creating a TTS job, THE Studio_Backend SHALL validate voice permissions:
   - Private voices: User must match `user_id` in Voice table
   - Shared voices: Any authenticated user can use
   - Approved voices: Any user (authenticated or playground anonymous)
3. ALL voices SHALL have language field (NOT NULL):
   - Existing voices SHALL be backfilled with "zh" (simplified Chinese)
   - New voices SHALL require language specification
   - Language SHALL match between voice and TTS job
4. THE System SHALL use path-based storage for voice recordings:
   - Store as `audio_path` (e.g., "audio-prompts/{voice_id}.wav")
   - NOT full S3 URLs (shared bucket across repositories)
   - Consistent path format across all services
5. Voice recordings SHALL NOT be updatable:
   - If voice quality needs improvement, create new voice recording
   - Existing TTS jobs SHALL reference original audio_path
   - Cache consistency maintained via voice_id stability
6. THE Studio_Backend SHALL pass `audio_prompt_path` (not URL) to IndexTTS_Worker
7. THE Voice_Catalog SHALL support filtering by:
   - Language (required field)
   - State (private/shared/approved)
   - Owner (for user's own voices)
   - Community eligibility (`is_shared=true AND is_approved=true`)
8. WHEN a user creates a TTS job, THE System SHALL log:
   - voice_id, voice_state, voice_language, owner_user_id
   - For audit trail and analytics

