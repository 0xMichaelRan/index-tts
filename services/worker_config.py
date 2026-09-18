"""
Worker configuration for IndexTTS worker.

Centralises all environment-variable parsing so IndexTTSWorker.__init__
receives a single, typed config object instead of reading os.getenv() inline.

Usage::

    # Production entry point
    config = WorkerConfig.from_env()
    worker = IndexTTSWorker(config)

    # Tests — construct directly, no os.environ patching needed
    config = WorkerConfig(rabbitmq_url="amqp://guest:guest@localhost:5672/")
    worker = IndexTTSWorker(config)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Private helpers (module-level, used only by from_env())
# ---------------------------------------------------------------------------


def _env_bool(name: str, default: bool) -> bool:
    """Parse a boolean environment variable."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in ("true", "1", "yes")


def _env_int(name: str, default: int) -> int:
    """Parse an integer environment variable."""
    value = os.getenv(name)
    if value is None:
        return default
    return int(value)


def _env_float(name: str, default: float) -> float:
    """Parse a float environment variable."""
    value = os.getenv(name)
    if value is None:
        return default
    return float(value)


def _parse_log_level(level_name: str) -> int:
    """Convert a log-level string (e.g. 'DEBUG') to the logging int constant."""
    level = getattr(logging, level_name.upper(), None)
    if isinstance(level, int):
        return level
    return logging.INFO


# ---------------------------------------------------------------------------
# WorkerConfig
# ---------------------------------------------------------------------------


@dataclass
class WorkerConfig:
    """
    All runtime configuration for IndexTTSWorker.

    Attributes:
        rabbitmq_url: AMQP connection URL (required).
        log_level: Python logging level integer (default: INFO).
        log_file_enabled: Whether to write logs to a file.
        log_file_path: Path to the log file when file logging is enabled.
        use_fast_inference: Use infer_fast() on Linux/Windows (ignored on macOS).
        cache_enabled: Enable database-backed synthesis cache.
        cache_max_entries: Maximum number of cached synthesis entries.
        cache_eviction_threshold: LRU eviction starts when cache reaches this size.
        cache_dir: Local directory for cached audio files.
        normalization_enabled: Apply LUFS loudness normalization to output audio.
        normalization_target_lufs: Target loudness in LUFS (default: -16.0).
        vox_render_enabled: Enable the vox_jobs consumer (Linux/Windows only).
        vox_render_ffmpeg_path: Path to the ffmpeg binary used for video composition.
    """

    rabbitmq_url: str

    # Logging
    log_level: int = field(default=logging.INFO)
    log_file_enabled: bool = False
    log_file_path: str = "logs/worker.log"

    # TTS inference
    use_fast_inference: bool = True

    # Synthesis cache
    cache_enabled: bool = True
    cache_max_entries: int = 10_000
    cache_eviction_threshold: int = 9_000
    cache_dir: str = "outputs/tts_cache"

    # Audio normalization
    normalization_enabled: bool = True
    normalization_target_lufs: float = -16.0

    # Vox render consumer (Linux/Windows only; disabled on macOS)
    vox_render_enabled: bool = True
    vox_render_ffmpeg_path: str = "ffmpeg"

    # ---------------------------------------------------------------------------
    # Factories
    # ---------------------------------------------------------------------------

    @classmethod
    def from_env(cls) -> WorkerConfig:
        """
        Build a WorkerConfig from environment variables.

        All variables are optional except RABBITMQ_URL.

        Environment variables:
            RABBITMQ_URL                  Required. AMQP connection URL.
            LOG_LEVEL                     Logging level name (default: INFO).
            LOG_FILE_ENABLED              Enable file logging (default: false).
            LOG_FILE_PATH                 Log file path (default: logs/worker.log).
            TTS_USE_FAST_INFERENCE        Use infer_fast() (default: true).
            TTS_CACHE_ENABLED             Enable synthesis cache (default: true).
            TTS_CACHE_MAX_ENTRIES         Max cache entries (default: 10000).
            TTS_CACHE_EVICTION_THRESHOLD  Eviction trigger count (default: 9000).
            TTS_CACHE_LOCAL_DIR           Cache directory (default: outputs/tts_cache).
            TTS_NORMALIZATION_ENABLED     Enable LUFS normalization (default: true).
            TTS_NORMALIZATION_TARGET_LUFS Target LUFS (default: -16.0).
            VOX_RENDER_ENABLED            Enable vox render consumer (default: true).
            VOX_RENDER_FFMPEG_PATH        Path to ffmpeg binary (default: ffmpeg).
        """
        rabbitmq_url = os.getenv("RABBITMQ_URL", "")

        config = cls(
            rabbitmq_url=rabbitmq_url,
            # Logging
            log_level=_parse_log_level(os.getenv("LOG_LEVEL", "INFO")),
            log_file_enabled=_env_bool("LOG_FILE_ENABLED", False),
            log_file_path=os.getenv("LOG_FILE_PATH", "logs/worker.log"),
            # TTS inference
            use_fast_inference=_env_bool("TTS_USE_FAST_INFERENCE", True),
            # Cache
            cache_enabled=_env_bool("TTS_CACHE_ENABLED", True),
            cache_max_entries=_env_int("TTS_CACHE_MAX_ENTRIES", 10_000),
            cache_eviction_threshold=_env_int("TTS_CACHE_EVICTION_THRESHOLD", 9_000),
            cache_dir=os.getenv("TTS_CACHE_LOCAL_DIR", "outputs/tts_cache"),
            # Normalization
            normalization_enabled=_env_bool("TTS_NORMALIZATION_ENABLED", True),
            normalization_target_lufs=_env_float(
                "TTS_NORMALIZATION_TARGET_LUFS", -16.0
            ),
            # Vox render consumer
            vox_render_enabled=_env_bool("VOX_RENDER_ENABLED", True),
            vox_render_ffmpeg_path=os.getenv("VOX_RENDER_FFMPEG_PATH", "ffmpeg"),
        )
        return config

    # ---------------------------------------------------------------------------
    # Validation
    # ---------------------------------------------------------------------------

    def validate(self) -> None:
        """
        Raise ValueError for obviously invalid configuration.

        Call this before passing the config to IndexTTSWorker.
        """
        if not self.rabbitmq_url:
            raise ValueError(
                "RABBITMQ_URL is required. See .env.example for configuration template."
            )

        if not (-60.0 <= self.normalization_target_lufs <= 0.0):
            raise ValueError(
                f"normalization_target_lufs must be between -60.0 and 0.0 LUFS, "
                f"got {self.normalization_target_lufs}"
            )

        if self.cache_eviction_threshold >= self.cache_max_entries:
            raise ValueError(
                f"cache_eviction_threshold ({self.cache_eviction_threshold}) "
                f"must be less than cache_max_entries ({self.cache_max_entries})"
            )

    # ---------------------------------------------------------------------------
    # Convenience
    # ---------------------------------------------------------------------------

    @property
    def log_level_name(self) -> str:
        """Human-readable log level name (e.g. 'INFO')."""
        return logging.getLevelName(self.log_level)
