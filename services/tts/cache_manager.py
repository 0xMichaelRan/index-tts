"""
TTS synthesis cache management with database and S3-backed file storage.

Phase 3 implementation:
- S3-backed base audio (canonical L0 cache in klatu-tts under cache/tts/<prefix>/<key>.wav)
- Local filesystem (L1 hot cache, opportunistic — may be absent after restart)
- Alignment JSON cached in S3 alongside base audio (skip Whisper on cache hits)
"""

import logging
import os
from typing import Optional, Tuple

from services.common.logging_config import get_logger

logger = get_logger(__name__)

# Try to import cache components
try:
    from services.db.database import SyncDatabaseSession
    from services.db.cache_service import TTSCacheServiceSync

    CACHE_AVAILABLE = True
except ImportError as e:
    logging.warning("Cache dependencies not available: %s", e)
    CACHE_AVAILABLE = False


class CacheManager:
    """Manages TTS synthesis caching with synchronous database operations.

    Phase 3 implementation: all DB calls are made directly on the calling
    thread via :class:`app.cache_service.TTSCacheServiceSync` and
    :func:`app.database.SyncDatabaseSession` (psycopg2).  No thread spawning
    or event-loop creation per cache call.

    Cache hierarchy:
        L0: DB hit + ``base_audio_s3_path`` exists  → download from S3
        L1: DB hit + ``base_audio_local_path`` file exists → copy locally
        MISS: synthesize, store to S3 + local
    """

    def __init__(
        self,
        cache_dir: str,
        max_entries: int = 10000,
        eviction_threshold: int = 9000,
        max_size_mb: int = 0,
        enabled: bool = True,
        storage_manager=None,
    ):
        """
        Initialize cache manager.

        Args:
            cache_dir: Local cache directory path
            max_entries: Maximum cache entries
            eviction_threshold: Threshold for LRU eviction
            max_size_mb: Maximum total cache size in MB (0 = unlimited)
            enabled: Whether synthesis cache is enabled
            storage_manager: StorageManager instance (required for S3 operations)
        """
        self.cache_dir = cache_dir
        self.max_entries = max_entries
        self.eviction_threshold = eviction_threshold
        self.max_size_mb = max_size_mb
        self.storage_manager = storage_manager

        if not enabled:
            self.enabled = False
            logger.info("TTS synthesis cache: DISABLED (by configuration)")
            return

        if not CACHE_AVAILABLE:
            self.enabled = False
            logger.warning("Cache disabled: dependencies not available")
            return

        self.enabled = True
        logger.info("TTS synthesis cache: ENABLED")
        logger.info(f"  Max entries: {max_entries}")
        logger.info(f"  Eviction threshold: {eviction_threshold}")
        if max_size_mb > 0:
            logger.info(f"  Max disk size: {max_size_mb} MB")
        else:
            logger.info("  Max disk size: unlimited")
        logger.info(f"  Cache directory: {cache_dir}")

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def lookup(
        self, job_id: str, text: str, audio_prompt_path: str, ratio: float
    ) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
        """Synchronous cache lookup with L0/L1 priority.

        L0: DB hit + base_audio_s3_path populated → return S3 key (caller downloads)
        L1: DB hit + local file exists             → return local path (caller copies)

        Args:
            job_id: Job identifier (used for log context only).
            text: Text to synthesize.
            audio_prompt_path: S3 path to audio prompt.
            ratio: Speed ratio (not used for lookup key).

        Returns:
            ``(cache_hit, cached_base_path_or_s3_key, cache_key, cached_alignment_s3_path)``

            On L0 hit: ``cached_base_path_or_s3_key`` is the S3 key string
                       (caller must call ``storage_manager.download_s3_to_local``).
            On L1 hit: ``cached_base_path_or_s3_key`` is a local filesystem path.
            On miss:   all ``None``.
        """
        if not self.enabled:
            return (False, None, None, None)

        try:
            with SyncDatabaseSession() as db_session:
                cache_service = TTSCacheServiceSync(db_session, self.cache_dir)
                cache_entry = cache_service.lookup(text, audio_prompt_path)

                if cache_entry:
                    alignment_s3_path = getattr(
                        cache_entry, "base_alignment_s3_path", None
                    )

                    # L0: prefer S3 canonical path (node-agnostic, restart-safe)
                    if cache_entry.base_audio_s3_path:
                        logger.success(
                            f"[JOB {job_id}] Cache HIT (L0/S3) — base audio in S3"
                        )
                        return (
                            True,
                            cache_entry.base_audio_s3_path,
                            cache_entry.cache_key,
                            alignment_s3_path,
                        )

                    # L1: fall back to local file if it still exists
                    local_path = cache_entry.base_audio_local_path
                    if local_path and os.path.exists(local_path):
                        logger.success(
                            f"[JOB {job_id}] Cache HIT (L1/local) — base audio on disk"
                        )
                        return (
                            True,
                            local_path,
                            cache_entry.cache_key,
                            alignment_s3_path,
                        )

                    # Entry exists in DB but neither S3 key nor local file is available.
                    logger.warning(
                        f"[JOB {job_id}] Cache DB hit but no accessible audio "
                        f"(s3={cache_entry.base_audio_s3_path!r}, "
                        f"local={local_path!r}) — treating as MISS"
                    )
                    return (False, None, None, None)

                return (False, None, None, None)

        except Exception as e:
            logger.warning(f"[JOB {job_id}] Cache lookup failed: {e}")
            return (False, None, None, None)

    def store(
        self,
        job_id: str,
        text: str,
        audio_prompt_path: str,
        base_audio_path: str,
        audio_duration: float,
        synthesis_duration: float,
        language: str,
        alignment_s3_path: Optional[str] = None,
    ) -> Optional[str]:
        """Synchronous cache storage with S3 upload.

        Uploads base WAV to content-addressed S3 key under
        ``cache/tts/<prefix>/<key>.wav``.  If ``alignment_s3_path`` is
        provided, copies the alignment JSON to the parallel cache key
        ``cache/tts/<prefix>/<key>_alignment.json`` via a server-side copy.

        Args:
            job_id: Job identifier (used for log context only).
            text: Text to synthesize.
            audio_prompt_path: S3 path to audio prompt.
            base_audio_path: Local path to base audio (ratio=1.0).
            audio_duration: Audio duration in seconds.
            synthesis_duration: Synthesis time in seconds (converted to ms internally).
            language: Language code.
            alignment_s3_path: S3 key of the job-specific alignment JSON to cache.

        Returns:
            ``cache_key`` if successful, ``None`` otherwise.
        """
        if not self.enabled:
            return None

        try:
            with SyncDatabaseSession() as db_session:
                cache_service = TTSCacheServiceSync(db_session, self.cache_dir)

                # Derive content-addressed S3 cache keys
                cache_key = TTSCacheServiceSync.generate_cache_key(
                    text, audio_prompt_path
                )
                prefix = cache_key[:4]
                base_s3_key = f"cache/tts/{prefix}/{cache_key}.wav"
                base_alignment_s3_key: Optional[str] = None

                # Upload base WAV to S3 cache prefix
                if self.storage_manager is not None:
                    try:
                        self.storage_manager.upload_to_cache(
                            base_audio_path, base_s3_key
                        )
                        logger.info(
                            f"[JOB {job_id}] Base audio cached → tts:{base_s3_key}"
                        )
                    except Exception as upload_exc:
                        logger.warning(
                            f"[JOB {job_id}] Failed to upload base audio to S3 cache: "
                            f"{upload_exc} — continuing without S3 cache"
                        )
                        base_s3_key_stored = None
                    else:
                        base_s3_key_stored = base_s3_key

                    # Server-side copy alignment JSON to cache prefix (if available)
                    if alignment_s3_path and base_s3_key_stored:
                        base_alignment_s3_key = (
                            f"cache/tts/{prefix}/{cache_key}_alignment.json"
                        )
                        try:
                            self.storage_manager.copy_s3_to_s3(
                                alignment_s3_path, base_alignment_s3_key
                            )
                            logger.info(
                                f"[JOB {job_id}] Alignment cached → tts:{base_alignment_s3_key}"
                            )
                        except Exception as copy_exc:
                            logger.warning(
                                f"[JOB {job_id}] Failed to cache alignment JSON: "
                                f"{copy_exc} — alignment won't be reused on cache hits"
                            )
                            base_alignment_s3_key = None
                else:
                    base_s3_key_stored = None

                entry = cache_service.store(
                    text=text,
                    audio_prompt_path=audio_prompt_path,
                    base_audio_local_path=base_audio_path,
                    base_audio_s3_path=base_s3_key_stored,
                    base_alignment_s3_path=base_alignment_s3_key,
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

        Eviction fires when **either** the entry count exceeds
        ``max_entries`` **or** total disk usage exceeds ``max_size_mb``
        (Phase 4a).  Within candidates, cheap+stale entries leave first
        (Phase 4b cost-aware LRU).

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
                    max_size_mb=self.max_size_mb,
                )
                if evicted > 0:
                    logger.info(
                        f"Auto-eviction complete: removed {evicted} cache entries "
                        f"(max={self.max_entries}, threshold={self.eviction_threshold}, "
                        f"max_size_mb={self.max_size_mb or 'unlimited'})"
                    )
        except Exception as e:
            logger.warning(f"[JOB {job_id}] Cache auto-eviction failed: {e}")

    def update_alignment_s3_path(
        self, job_id: str, cache_key: str, alignment_s3_path: str
    ) -> None:
        """Backfill base_alignment_s3_path into an existing cache entry.

        Called after a fresh synthesis + alignment upload so that subsequent
        cache hits can reuse the alignment JSON without re-running Whisper.

        Args:
            job_id: Job identifier (log context only).
            cache_key: Cache key of the entry to update.
            alignment_s3_path: S3 key of the uploaded alignment JSON.
        """
        if not self.enabled or not cache_key:
            return

        try:
            from sqlalchemy import update as sa_update
            from services.db.models import TTSSynthesisCache

            with SyncDatabaseSession() as db_session:
                db_session.execute(
                    sa_update(TTSSynthesisCache)
                    .where(TTSSynthesisCache.cache_key == cache_key)
                    .values(base_alignment_s3_path=alignment_s3_path)
                )
                db_session.commit()
            logger.info(
                f"[JOB {job_id}] Cache alignment backfilled → tts:{alignment_s3_path}"
            )
        except Exception as e:
            logger.warning(
                f"[JOB {job_id}] Failed to backfill alignment into cache: {e}"
            )
