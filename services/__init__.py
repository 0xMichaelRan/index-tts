"""
services — IndexTTS Worker service layer.

Subpackages:
  services.common    — shared infrastructure (logging, config, circuit breaker)
  services.messaging — RabbitMQ lifecycle and DLX configuration
  services.storage   — S3 client, registry, idempotent upload, storage manager
  services.tts       — TTS audio synthesis domain
  services.vox       — Vox video rendering domain
"""
