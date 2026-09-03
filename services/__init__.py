"""
IndexTTS Worker Services - Modular components for TTS processing.

This package provides a set of modular, single-responsibility services:

- RabbitMQManager: Queue connection, consumption, and result publishing
- StorageManager: S3 operations and file management
- AudioProcessor: Audio duration detection and time-stretching
- CacheManager: Synthesis caching with database backend
- SynthesisPipeline: Orchestrates synthesis, alignment, and upload
- IndexTTSWorker: Main orchestrator that ties everything together

NOTE: Services are lazily imported to avoid circular imports during initialization
(e.g., during alembic migrations). Import them directly from their modules as needed.
"""

__all__ = [
    "AudioProcessor",
    "CacheManager",
    "RabbitMQManager",
    "StorageManager",
    "SynthesisPipeline",
    "IndexTTSWorker",
]
