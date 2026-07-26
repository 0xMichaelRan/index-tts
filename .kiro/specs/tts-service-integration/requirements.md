# TTS Service Integration Requirements

## Introduction

This feature spec covers the integration of the IndexTTS text-to-speech engine across three repositories:

1. **indexTTS-worker**: Core TTS service and RabbitMQ worker
2. **official-landing**: Next.js playground for anonymous TTS demonstrations  
3. **studio-backend**: FastAPI backend for production TTS jobs with user voice recordings

The feature enables:
- Anonymous users to test TTS with predefined audio prompts (playground)
- Authenticated users to generate TTS audio using their own voice recordings (studio-backend)
- Production TTS synthesis with automatic retry, monitoring, and S3 storage
- Support for GPU-based IndexTTS inference with optional macOS native TTS in dev/staging

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

### Requirement 2: TTS Job Submission API (studio-backend)

**User Story:** As a studio backend service, I want to submit TTS jobs to the worker and track their status, so that authenticated users can generate audio from scripts with voice recordings.

#### Acceptance Criteria

1. THE Studio_Backend SHALL provide a POST `/api/v1/tts` endpoint to create TTS jobs.
2. WHEN a TTS job is created, THE Studio_Backend SHALL accept: user_id, project_id, text, voice_id, and language (required, not optional).
3. WHEN the request arrives, THE Studio_Backend SHALL validate voice_id exists in the Voice table before creating any job record.
4. IF voice_id does not exist in the database, THE Studio_Backend SHALL return 404 Not Found instead of creating a job.
5. IF voice_id exists but is private (owned by another user), THE Studio_Backend SHALL return 403 Forbidden instead of creating a job.
6. IF voice_id is valid and accessible, THE Studio_Backend SHALL retrieve its S3 URL from the voice catalog.
7. THE Studio_Backend SHALL create a TTS_Job database record with status "pending" and store job_id, user_id, project_id, text, voice_id, language, created_timestamp.
8. THE Studio_Backend SHALL publish a message to RabbitMQ tts_jobs queue containing: job_id, text, audio_prompt_url (from voice catalog), language, and user_metadata (user_id, project_id).
9. THE Studio_Backend SHALL return a 202 Accepted response with job_id and polling/SSE URL.
10. WHEN requesting job status via GET `/api/v1/tts/{job_id}`, THE Studio_Backend SHALL return current status, progress (if processing), and S3 audio URL (if completed).
11. WHEN the consumer receives a tts_results message with status "completed", THE Studio_Backend SHALL validate output_s3_url exists and audio_duration_seconds is positive before updating the TTS_Job record.
12. WHEN the consumer receives a tts_results message with status "failed", THE Studio_Backend SHALL validate error_message is present before updating the TTS_Job record.
13. IF validation fails for either completed or failed results, THE TTS_Job SHALL remain in pending status and not be updated.
14. THE Studio_Backend SHALL provide SSE endpoint GET `/api/v1/tts/{job_id}/stream` for real-time job status updates.
15. THE Studio_Backend SHALL apply content-based caching: if an identical (voice_id, text) pair was synthesized within the last 30 days, reuse the audio instead of resubmitting a job.

---

### Requirement 3: Playground TTS Endpoint (official-landing + studio-backend)

**User Story:** As an anonymous user, I want to test the TTS service with predefined audio prompts, so that I can evaluate the quality without signing up.

#### Acceptance Criteria

1. THE Playground SHALL provide an anonymous POST `/api/v1/playground/tts` endpoint accessible without authentication.
2. WHEN a playground TTS request is received, THE Studio_Backend SHALL validate the request contains: text, audio_prompt_id, and language (required, not optional).
3. THE Studio_Backend SHALL look up the playground audio_prompt_id from a predefined list (managed in config/database).
4. IF text length exceeds 500 characters, THE Studio_Backend SHALL return 400 Bad Request with error message.
5. THE Studio_Backend SHALL apply rate limiting: maximum 5 TTS requests per IP address per hour to prevent abuse.
6. IF rate limit is exceeded, THE Studio_Backend SHALL return 429 Too Many Requests.
7. WHEN a rate limit is exceeded, THE Studio_Backend SHALL create a temporary TTS job record with status "pending" for analytics tracking even though the request is rejected.
8. WHEN rate limiting is not exceeded and the request is valid, THE Studio_Backend SHALL create a temporary TTS job record with status "pending", user_id = NULL (anonymous), and store client_ip_address for analytics and rate limiting.
9. THE Studio_Backend SHALL publish to RabbitMQ tts_jobs queue with: job_id, text, audio_prompt_url, language, and is_playground=true flag.
10. THE Studio_Backend SHALL return 202 Accepted with job_id and SSE URL.
11. WHEN the playground job completes, THE Studio_Backend SHALL serve audio directly via SSE stream and retain job records with anonymized IP for 30 days for data analysis and rate limiting verification.

---

### Requirement 4: Audio Streaming Response (studio-backend)

**User Story:** As a client application, I want to receive synthesized audio as a continuous stream, so that playback can start while synthesis completes.

#### Acceptance Criteria

1. THE Studio_Backend SHALL provide GET `/api/v1/tts/{job_id}/stream` endpoint that accepts application/event-stream content type.
2. WHEN the job is still processing, THE SSE_Stream SHALL send progress events with status "processing", progress percentage (0-100), and estimated_time_remaining_seconds.
3. WHEN the job completes successfully, THE SSE_Stream SHALL send a "completed" event with: status, s3_url, audio_duration_seconds, and audio_format (determined by IndexTTS output).
4. WHEN the job fails, THE SSE_Stream SHALL NOT send a "completed" event.
5. WHEN the job fails, THE SSE_Stream SHALL send a "failed" event with: status, error_message, and retry_count.
6. THE SSE_Stream SHALL close the connection after sending a terminal event (completed/failed).
7. IF the client disconnects before completion, THE TTS_Job SHALL continue processing in the background.
8. THE Studio_Backend SHALL set SSE heartbeat interval to 30 seconds to detect connection drops.

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

1. THE IndexTTS_Worker SHALL log: job_id, operation (start, progress, complete, fail), timestamp, duration_seconds, language, status_code.
2. THE IndexTTS_Worker SHALL log synthesis errors with: error_type, error_message, stack_trace, retry_count, attempt_number.
3. THE Studio_Backend SHALL log TTS requests with: job_id, user_id, project_id, text_length, voice_id, language, creation_timestamp.
4. THE Studio_Backend SHALL log rate limit violations with: ip_address, request_count, window_timestamp.
5. WHEN a job completes, THE Studio_Backend AND IndexTTS_Worker SHALL both log: job_id, duration_seconds, audio_duration_seconds, status, s3_url.
6. ALL logs SHALL include timestamps in ISO 8601 format and correlation IDs for tracing request flow.

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

### Requirement 12: Voice Catalog Integration (studio-backend)

**User Story:** As studio-backend, I want to seamlessly use the existing voice catalog for TTS synthesis, so that users can leverage their recordings.

#### Acceptance Criteria

1. THE Studio_Backend SHALL query the Voice table to retrieve voice_id, s3_url, language, created_by_user_id.
2. WHEN creating a TTS job, THE Studio_Backend SHALL validate that voice_id exists in the Voice catalog.
3. IF voice_id does not exist in the database, THE Studio_Backend SHALL return 404 Not Found.
4. IF voice_id exists but user lacks permission to use it, THE Studio_Backend SHALL return 403 Forbidden (applies to private voices not owned by user, and also applies if private voice doesn't exist but user has no permission).
5. IF voice_id is a community voice (public), THE Studio_Backend SHALL proceed regardless of permissions.
6. THE Studio_Backend SHALL retrieve the voice's S3 URL and pass it as audio_prompt_url to IndexTTS_Worker.
7. THE Voice_Catalog SHALL support filtering voices by language and availability status.
8. WHEN a user creates a TTS job, THE System SHALL log: voice_id, voice_language, voice_owner_user_id, for analytics.

