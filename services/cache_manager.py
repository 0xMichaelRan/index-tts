"""
TTS synthesis cache management with database and file storage.
"""

import asyncio
import logging
import threading
from typing import Optional, Tuple

from services.logging_config import get_logger

logger = get_logger(__name__)

# Try to import cache components
try:
    from app.database import DatabaseSession
    from app.cache_service import TTSCacheService

    CACHE_AVAILABLE = True
except ImportError as e:
    logging.warning("Cache dependencies not available: %s", e)
    CACHE_AVAILABLE = False


class CacheManager:
    """Manages TTS synthesis caching with async database operations."""

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

    async def _lookup_async(
        self, text: str, audio_prompt_path: str
    ) -> Optional[Tuple[bool, Optional[str], Optional[str]]]:
        """
        Async cache lookup.

        Returns:
            (cache_hit, cached_audio_path, cache_key) tuple
        """
        try:
            async with DatabaseSession() as db_session:
                cache_service = TTSCacheService(db_session, self.cache_dir)
                cache_entry = await cache_service.lookup(text, audio_prompt_path)

                if cache_entry:
                    return (
                        True,
                        cache_entry.base_audio_local_path,
                        cache_entry.cache_key,
                    )

                return (False, None, None)

        except Exception as e:
            logger.warning(f"Cache lookup failed: {e}")
            return (False, None, None)

    async def _store_async(
        self,
        text: str,
        audio_prompt_path: str,
        base_audio_path: str,
        audio_duration: float,
        synthesis_duration_ms: int,
        language: str,
    ) -> Optional[str]:
        """
        Async cache storage.

        Args:
            text: Synthesized text
            audio_prompt_path: S3 path to audio prompt
            base_audio_path: Local path to base audio
            audio_duration: Audio duration in seconds
            synthesis_duration_ms: Synthesis time in milliseconds
            language: Language code

        Returns:
            cache_key if successful, None otherwise
        """
        try:
            async with DatabaseSession() as db_session:
                cache_service = TTSCacheService(db_session, self.cache_dir)
                entry = await cache_service.store(
                    text=text,
                    audio_prompt_path=audio_prompt_path,
                    base_audio_local_path=base_audio_path,
                    audio_duration_seconds=audio_duration,
                    synthesis_duration_ms=synthesis_duration_ms,
                    language=language,
                )

                logger.success("Base audio cached for future reuse")
                return entry.cache_key

        except Exception as e:
            logger.warning(f"Failed to cache synthesis: {e}")
            return None

    def lookup(
        self, job_id: str, text: str, audio_prompt_path: str, ratio: float
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Synchronous wrapper for cache lookup.

        Args:
            job_id: Job identifier
            text: Text to synthesize
            audio_prompt_path: S3 path to audio prompt
            ratio: Speed ratio

        Returns:
            (cache_hit, cached_audio_path, cache_key) tuple
        """
        if not self.enabled:
            return (False, None, None)

        result_container = {}

        def run_async():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(
                    self._lookup_async(text, audio_prompt_path)
                )
                result_container["result"] = result
            except Exception as e:
                result_container["error"] = e
            finally:
                loop.close()

        thread = threading.Thread(target=run_async)
        thread.start()
        thread.join(timeout=10.0)

        if "error" in result_container:
            logger.warning(
                f"[JOB {job_id}] Cache lookup failed: {result_container['error']}"
            )
            return (False, None, None)

        result = result_container.get("result", (False, None, None))
        if result and result[0]:
            logger.success(f"[JOB {job_id}] Cache HIT - reusing base audio")

        return result

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
        """
        Synchronous wrapper for cache storage.

        Args:
            job_id: Job identifier
            text: Text to synthesize
            audio_prompt_path: S3 path to audio prompt
            base_audio_path: Local path to base audio
            audio_duration: Audio duration in seconds
            synthesis_duration: Synthesis time in seconds
            language: Language code

        Returns:
            cache_key if successful, None otherwise
        """
        if not self.enabled:
            return None

        result_container = {}

        def run_async():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(
                    self._store_async(
                        text,
                        audio_prompt_path,
                        base_audio_path,
                        audio_duration,
                        int(synthesis_duration * 1000),
                        language,
                    )
                )
                result_container["result"] = result
            except Exception as e:
                logger.warning(f"[JOB {job_id}] Cache store failed: {e}")
                result_container["error"] = e
            finally:
                loop.close()

        thread = threading.Thread(target=run_async)
        thread.start()
        thread.join(timeout=10.0)

        if "error" in result_container:
            return None

        return result_container.get("result")

    async def _evict_async(self) -> None:
        """
        Async LRU eviction helper.

        Checks the current entry count and evicts the oldest entries when
        ``max_entries`` is exceeded. Evicts ``max_entries - eviction_threshold``
        entries (e.g. 10 000 − 9 000 = 1 000) to restore the cache to the
        safe threshold in a single pass.
        """
        try:
            async with DatabaseSession() as db_session:
                cache_service = TTSCacheService(db_session, self.cache_dir)
                evict_count = self.max_entries - self.eviction_threshold
                evicted = await cache_service.evict_old_entries(
                    max_entries=self.max_entries,
                    evict_count=max(evict_count, 1),
                )
                if evicted > 0:
                    logger.info(
                        f"Auto-eviction complete: removed {evicted} cache entries "
                        f"(max={self.max_entries}, threshold={self.eviction_threshold})"
                    )
        except Exception as e:
            logger.warning(f"Cache auto-eviction failed: {e}")

    def maybe_evict(self, job_id: str = "") -> None:
        """
        Fire-and-forget background eviction check.

        Spawns a daemon thread that runs LRU eviction if the cache has exceeded
        ``max_entries``.  The thread is daemonised so it never blocks worker
        shutdown, and we deliberately do **not** join it — eviction must not
        add latency to the synthesis pipeline.

        Args:
            job_id: Job identifier used for log context only.
        """
        if not self.enabled:
            return

        def run_async():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self._evict_async())
            except Exception as e:
                logger.warning(f"[JOB {job_id}] Background eviction error: {e}")
            finally:
                loop.close()

        thread = threading.Thread(target=run_async, daemon=True, name="cache-evict")
        thread.start()
        # Intentionally no join — eviction runs in the background.
