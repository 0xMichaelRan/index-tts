"""
IndexTTS Worker Services - Modular components for TTS processing.

This package provides a set of modular, single-responsibility services:

- RabbitMQManager: Queue connection, consumption, and result publishing
- StorageManager: S3 operations and file management
- AudioProcessor: Audio duration detection and time-stretching
- CacheManager: Synthesis caching with database backend
- SynthesisPipeline: Orchestrates synthesis, alignment, and upload
- IndexTTSWorker: Main orchestrator that ties everything together
"""

from services.audio_processor import AudioProcessor
from services.cache_manager import CacheManager
from services.rabbitmq_manager import RabbitMQManager
from services.storage_manager import StorageManager
from services.synthesis_pipeline import SynthesisPipeline
from services.tts_worker import IndexTTSWorker

__all__ = [
    "AudioProcessor",
    "CacheManager",
    "RabbitMQManager",
    "StorageManager",
    "SynthesisPipeline",
    "IndexTTSWorker",
]
