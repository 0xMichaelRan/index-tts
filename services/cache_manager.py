"""
TTS synthesis cache management with database and file storage.
"""

import logging
from typing import Optional, Tuple

from services.logging_config import get_logger

logger = get_logger(__name__)

# Try to import cache components
try:
    from app.database import SyncDatabaseSession
    from app.cache_service import TTSCacheServiceSync

    CACHE_AVAILABLE = True
except ImportError as e:
    logging.warning("Cache dependencies not available: %s", e)
    CACHE_AVAILABLE = False


class CacheManager:
    """Manages TTS synthesis caching with synchronous database operations.

    Phase 2 implementation: all DB calls are made directly on the calling
    thread via :class:`app.cache_service.TTSCacheServiceSync` and
    :func:`app.database.SyncDatabaseSession` (psycopg2).  No thread spawning
    or event-loop creation per cache call.
    """

    def __init__(
        self, cache_dir: str, max_entries: int = 10000, eviction_threshold: int = 9000
    ):
        """
        Initialize cache manager.

        Args:
            cache_dir: Local cache directory path
            max_entries: Maximum cache entries
            eviction_threshold: Threshold for LRU eviction
        """
        if not CACHE_AVAILABLE:
            self.enabled = False
            logger.warning("Cache disabled: dependencies not available")
            return

        self.enabled = True
        self.cache_dir = cache_dir
        self.max_entries = max_entries
        self.eviction_threshold = eviction_threshold

        logger.info("TTS synthesis cache: ENABLED")
        logger.info(f"  Max entries: {max_entries}")
        logger.info(f"  Eviction threshold: {eviction_threshold}")
        logger.info(f"  Cache directory: {cache_dir}")

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def lookup(
        self, job_id: str, text: str, audio_prompt_path: str, ratio: float
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """Synchronous cache lookup.

        Args:
            job_id: Job identifier (used for log context only).
            text: Text to synthesize.
            audio_prompt_path: S3 path to audio prompt.
            ratio: Speed ratio (not used for lookup key, returned for caller convenience).

        Returns:
            ``(cache_hit, cached_audio_path, cache_key)`` tuple.
        """
        if not self.enabled:
            return (False, None, None)

        try:
            with SyncDatabaseSession() as db_session:
                cache_service = TTSCacheServiceSync(db_session, self.cache_dir)
                cache_entry = cache_service.lookup(text, audio_prompt_path)

                if cache_entry:
                    logger.success(f"[JOB {job_id}] Cache HIT - reusing base audio")
                    return (
                        True,
                        cache_entry.base_audio_local_path,
                        cache_entry.cache_key,
                    )

                return (False, None, None)

        except Exception as e:
            logger.warning(f"[JOB {job_id}] Cache lookup failed: {e}")
            return (False, None, None)

    def store(
        self,
        job_id: str,
        text: str,
        audio_prompt_path: str,
        base_audio_path: str,
        audio_duration: float,
        synthesis_duration: float,
        language: str,
    ) -> Optional[str]:
        """Synchronous cache storage.

        Args:
            job_id: Job identifier (used for log context only).
            text: Text to synthesize.
            audio_prompt_path: S3 path to audio prompt.
            base_audio_path: Local path to base audio.
            audio_duration: Audio duration in seconds.
            synthesis_duration: Synthesis time in seconds (converted to ms internally).
            language: Language code.

        Returns:
            ``cache_key`` if successful, ``None`` otherwise.
        """
        if not self.enabled:
            return None

        try:
            with SyncDatabaseSession() as db_session:
                cache_service = TTSCacheServiceSync(db_session, self.cache_dir)
                entry = cache_service.store(
                    text=text,
                    audio_prompt_path=audio_prompt_path,
                    base_audio_local_path=base_audio_path,
                    audio_duration_seconds=audio_duration,
                    synthesis_duration_ms=int(synthesis_duration * 1000),
                    language=language,
                )
                logger.success("Base audio cached for future reuse")
                return entry.cache_key

        except Exception as e:
            logger.warning(f"[JOB {job_id}] Failed to cache synthesis: {e}")
            return None

    def maybe_evict(self, job_id: str = "") -> None:
        """Fire-and-forget LRU eviction check (synchronous, same thread).

        Runs eviction inline after a successful ``store()``.  Because eviction
        is fast when the cache is within limits (a single COUNT query that
        returns immediately), running it on the calling thread is fine and
        avoids the overhead of an extra daemon thread.

        When the threshold is exceeded the eviction loop runs synchronously.
        This adds a small one-off cost to the job that triggered it; this is
        the same trade-off as before but without the thread-spawn overhead.

        Args:
            job_id: Job identifier used for log context only.
        """
        if not self.enabled:
            return

        try:
            with SyncDatabaseSession() as db_session:
                cache_service = TTSCacheServiceSync(db_session, self.cache_dir)
                evict_count = max(self.max_entries - self.eviction_threshold, 1)
                evicted = cache_service.evict_old_entries(
                    max_entries=self.max_entries,
                    evict_count=evict_count,
                )
                if evicted > 0:
                    logger.info(
                        f"Auto-eviction complete: removed {evicted} cache entries "
                        f"(max={self.max_entries}, threshold={self.eviction_threshold})"
                    )
        except Exception as e:
            logger.warning(f"[JOB {job_id}] Cache auto-eviction failed: {e}")
