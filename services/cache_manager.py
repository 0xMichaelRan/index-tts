"""
TTS synthesis cache management with database and file storage.
"""

import asyncio
import logging
import threading
import time
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

    def __init__(self, cache_dir: str, max_entries: int = 10000, eviction_threshold: int = 9000):
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
    ) -> Optional[Tuple[bool, Optional[str]]]:
        """
        Async cache lookup.

        Returns:
            (cache_hit, cached_audio_path) tuple
        """
        try:
            async with DatabaseSession() as db_session:
                cache_service = TTSCacheService(self.cache_dir, db_session)
                cache_entry = await cache_service.lookup(text, audio_prompt_path)

                if cache_entry:
                    return (True, cache_entry.base_audio_local_path)

                return (False, None)

        except Exception as e:
            logger.warning(f"Cache lookup failed: {e}")
            return (False, None)

    async def _store_async(
        self,
        text: str,
        audio_prompt_path: str,
        base_audio_path: str,
        audio_duration: float,
        synthesis_duration_ms: int,
        language: str,
    ) -> None:
        """
        Async cache storage.

        Args:
            text: Synthesized text
            audio_prompt_path: S3 path to audio prompt
            base_audio_path: Local path to base audio
            audio_duration: Audio duration in seconds
            synthesis_duration_ms: Synthesis time in milliseconds
            language: Language code
        """
        try:
            async with DatabaseSession() as db_session:
                cache_service = TTSCacheService(self.cache_dir, db_session)
                await cache_service.store(
                    text=text,
                    audio_prompt_path=audio_prompt_path,
                    base_audio_local_path=base_audio_path,
                    audio_duration_seconds=audio_duration,
                    synthesis_duration_ms=synthesis_duration_ms,
                    language=language,
                )

                logger.success("Base audio cached for future reuse")

        except Exception as e:
            logger.warning(f"Failed to cache synthesis: {e}")

    def lookup(
        self, job_id: str, text: str, audio_prompt_path: str, ratio: float
    ) -> Tuple[bool, Optional[str]]:
        """
        Synchronous wrapper for cache lookup.

        Args:
            job_id: Job identifier
            text: Text to synthesize
            audio_prompt_path: S3 path to audio prompt
            ratio: Speed ratio

        Returns:
            (cache_hit, cached_audio_path) tuple
        """
        if not self.enabled:
            return (False, None)

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
            return (False, None)

        result = result_container.get("result", (False, None))
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
    ) -> None:
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
        """
        if not self.enabled:
            return

        def run_async():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(
                    self._store_async(
                        text,
                        audio_prompt_path,
                        base_audio_path,
                        audio_duration,
                        int(synthesis_duration * 1000),
                        language,
                    )
                )
            except Exception as e:
                logger.warning(f"[JOB {job_id}] Cache store failed: {e}")
            finally:
                loop.close()

        thread = threading.Thread(target=run_async)
        thread.start()
        thread.join(timeout=10.0)
