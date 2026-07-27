# Implementation Plan

## Overview

This plan implements the TTS service integration across three repositories with focus on:
1. Building upon existing `TTSJob` and `Voice` schemas in studio-backend
2. Implementing dead-letter queues and circuit breaker patterns for reliability
3. Using path-based S3 storage (not full URLs) across all repositories
4. **Simplified text processing**: Studio-backend processes only first 2 sentences, playground uses full text (up to 200 words)
5. Three-state voice system: private, shared, approved

## Tasks

### Phase 1: Database Migration & Core Infrastructure (Week 1-2)

- [x] 1. Extend existing TTSJob schema with language, correlation_id, and full_text_hash columns
- [x] 2. Create PlaygroundTTSJob table with UUID primary key and full text storage
- [x] 3. Backfill voice language data for existing voices
- [x] 4. Configure RabbitMQ with dead-letter queues (tts_jobs_dlq, tts_results_dlq)
- [x] 5. Configure S3 path-based storage structure with lifecycle rules

### Phase 2: Studio Backend Implementation (Week 2-3)

- [x] 6. Implement Studio TTS endpoint processing only first 2 sentences of script text
- [x] 7. Implement Playground TTS endpoint with full text processing (max 200 words)
- [x] 8. Implement polling endpoint for TTS job status (simplified from SSE)
- [x] 9. Enhance TTS results consumer with circuit breaker for database updates

### Phase 3: IndexTTS Worker Implementation (Week 3-4)

- [ ] 10. Implement circuit breaker pattern for S3 downloads and IndexTTS synthesis
- [ ] 11. Implement dead-letter queue monitoring and alerting
- [ ] 12. Implement idempotent S3 upload with retry logic
- [ ] 13. Implement TTS worker core with platform-specific synthesis (GPU/macOS)
- [ ] 14. Implement structured logging and Prometheus metrics

### Phase 4: Official Landing Integration (Week 4-5)

- [ ] 15. Create Playground UI component with text input and voice selector
- [ ] 16. Implement SSE client streaming for real-time updates
- [ ] 17. Implement audio playback component with S3 presigned URLs
- [ ] 18. Implement graceful degradation for TTS service unavailability
- [ ] 19. Implement i18n for playground with English and Chinese translations

### Phase 5: Testing & Quality (Week 5-6)

- [ ] 20. Write property-based tests for cache consistency and state machines
- [ ] 21. Write integration tests for full pipeline (API → RabbitMQ → Worker → S3)
- [ ] 22. Perform security testing (input validation, authentication, rate limiting)
- [ ] 23. Conduct performance and load testing with different text lengths
- [ ] 24. Create comprehensive documentation (API, architecture, deployment guides)

### Phase 6: Deployment & Production Launch (Week 6)

- [ ] 25. Prepare production environment with monitoring and backup strategy
- [ ] 26. Implement gradual rollout with feature flags and canary deployment
- [ ] 27. Deploy studio-backend, indexTTS-worker, and official-landing
- [ ] 28. Set up post-launch monitoring and analytics
- [ ] 29. Establish maintenance procedures for DLQ monitoring and cache management

## Task Dependency Graph

```json
{
  "waves": [
    {
      "id": "wave-1",
      "name": "Database & Infrastructure",
      "tasks": [1, 2, 3, 4, 5]
    },
    {
      "id": "wave-2", 
      "name": "Backend Implementation",
      "tasks": [6, 7, 8, 9],
      "dependsOn": ["wave-1"]
    },
    {
      "id": "wave-3",
      "name": "TTS Worker",
      "tasks": [10, 11, 12, 13, 14],
      "dependsOn": ["wave-1"]
    },
    {
      "id": "wave-4",
      "name": "Frontend Integration",
      "tasks": [15, 16, 17, 18, 19],
      "dependsOn": ["wave-2", "wave-3"]
    },
    {
      "id": "wave-5",
      "name": "Testing & Documentation",
      "tasks": [20, 21, 22, 23, 24],
      "dependsOn": ["wave-4"]
    },
    {
      "id": "wave-6",
      "name": "Deployment",
      "tasks": [25, 26, 27, 28, 29],
      "dependsOn": ["wave-5"]
    }
  ]
}
```

## Notes

1. **Simplified Text Processing**: Studio-backend only processes first 2 sentences of script text, playground processes full text (up to 200 words)
2. **Backward Compatibility**: Existing `TTSJob` schema extended with new fields, not breaking changes
3. **Cache Strategy**: 
   - Studio: Cache by `(voice_id, first_2_sentences)` pairs
   - Playground: Cache by `(voice_id, full_text_hash)` pairs
4. **Error Handling**: Circuit breakers and dead-letter queues implemented for all critical paths
5. **Performance**: Studio-backend jobs designed for fast response (<5 seconds for cached, <30 seconds for new)
6. **Security**: IP hashing for rate limiting, path-based S3 storage, proper voice permission checking
7. **Testing**: Property-based tests for cache consistency, state machines, and rate limiting
8. **Deployment**: Gradual rollout with feature flags, canary deployment, and rollback plan
9. **Polling vs SSE**: Simple HTTP polling every 2-5 seconds replaces SSE for better simplicity and horizontal scaling