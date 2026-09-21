"""
TTS Cache Service - Business logic for synthesis cache management.

This service provides high-level operations for the TTS synthesis cache:
- Lookup: Find cached synthesis by (text, voice)
- Store: Save new synthesis results
- Hit tracking: Update access statistics
- Eviction: Remove old entries when cache is full
- Analytics: Query cache performance metrics

Cache Strategy:
- Key: SHA256(text + audio_prompt_path)
- Base audio always at ratio=1.0
- LRU eviction based on last_accessed_at
- File integrity verification on lookup
"""

import hashlib
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

from sqlalchemy import select, func, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models import TTSSynthesisCache
from services.logging_config import get_logger
from services.text_metrics import count_words

logger = get_logger(__name__)


class TTSCacheService:
    """Service for managing TTS synthesis cache operations."""

    def __init__(self, db_session: AsyncSession, cache_dir: str = "outputs/tts_cache"):
        """
        Initialize cache service.

        Args:
            db_session: Async database session
            cache_dir: Directory for storing cached audio files
        """
        self.db = db_session
        self.cache_dir = Path(cache_dir).resolve()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_path(self, stored_path: str) -> str:
        """Resolve a stored DB path (relative or absolute) to a full local filesystem path."""
        if not os.path.isabs(stored_path):
            return str(self.cache_dir / stored_path)
        return stored_path

    @staticmethod
    def extract_voice_id(audio_prompt_path: str) -> str:
        """
        Extract voice identifier from S3 path.

        Examples:
            "audio-prompts/voice_001.wav" → "voice_001"
            "audio-prompts/user/123/english.wav" → "english"
            "voice.wav" → "voice"

        Args:
            audio_prompt_path: S3 path to voice prompt

        Returns:
            Voice identifier (filename without extension)
        """
        # Get the filename without extension
        filename = Path(audio_prompt_path).stem
        # Remove common prefixes for cleaner IDs
        cleaned = re.sub(r"^(voice_|voice-)", "", filename)
        return cleaned or "unknown"

    @staticmethod
    def sanitize_text_for_filename(text: str, max_length: int = 20) -> str:
        """
        Sanitize text for use in filename.

        Removes special characters, converts to lowercase, keeps only alphanumeric + spaces.

        Examples:
            "Hello, World!" → "hello_world"
            "What's your name?" → "whats_your_name"
            "Test (v2) [edit]" → "test_v2_edit"

        Args:
            text: Text to sanitize
            max_length: Maximum length of output

        Returns:
            Safe filename-compatible string
        """
        # Convert to lowercase
        text = text.lower()
        # Keep only alphanumeric, spaces, hyphens
        text = re.sub(r"[^a-z0-9\s\-]", "", text)
        # Replace spaces with underscores
        text = re.sub(r"\s+", "_", text)
        # Remove multiple underscores
        text = re.sub(r"_+", "_", text)
        # Trim to max length and remove trailing underscore
        text = text[:max_length].rstrip("_")
        return text or "text"

    @staticmethod
    def generate_semantic_filename(text: str, audio_prompt_path: str) -> str:
        """
        Generate semantic filename for cached audio.

        Format: {text_preview}_{voice_id}.wav

        Examples:
            text="Hello world", voice="audio-prompts/voice_001.wav"
            → "hello_world_001.wav"

            text="This is a test", voice="audio-prompts/mary.wav"
            → "this_is_a_test_mary.wav"

        Args:
            text: Synthesized text (preview extracted)
            audio_prompt_path: S3 path to voice prompt

        Returns:
            Meaningful filename with .wav extension
        """
        # Extract components
        text_preview = TTSCacheService.sanitize_text_for_filename(text, max_length=20)
        voice_id = TTSCacheService.extract_voice_id(audio_prompt_path)

        # Build filename (no ratio suffix - always 1.0 for cached base audio)
        filename = f"{text_preview}_{voice_id}.wav"

        return filename

    @staticmethod
    def generate_cache_key(text: str, audio_prompt_path: str) -> str:
        """
        Generate deterministic cache key from text and voice.

        Args:
            text: Synthesized text
            audio_prompt_path: S3 path to voice prompt

        Returns:
            64-character hex string (SHA256 hash)

        Example:
            >>> TTSCacheService.generate_cache_key("Hello", "audio-prompts/voice_123.wav")
            'a3c5e8f2d1b4...'  # 64 chars
        """
        content = f"{text}|{audio_prompt_path}"
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    @staticmethod
    def generate_text_hash(text: str) -> str:
        """
        Generate hash of text only (for indexing).

        Args:
            text: Text to hash

        Returns:
            64-character hex string (SHA256 hash)
        """
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    async def lookup(
        self, text: str, audio_prompt_path: str
    ) -> Optional[TTSSynthesisCache]:
        """
        Look up cached synthesis by text and voice.

        Verifies file still exists and updates hit statistics.

        Args:
            text: Synthesized text
            audio_prompt_path: S3 path to voice prompt

        Returns:
            Cache entry if found and valid, None otherwise

        Side effects:
            - Increments hit_count on cache hit
            - Updates last_accessed_at on cache hit
            - Deletes entry if file missing
        """
        cache_key = self.generate_cache_key(text, audio_prompt_path)

        # Query database
        stmt = select(TTSSynthesisCache).where(TTSSynthesisCache.cache_key == cache_key)
        result = await self.db.execute(stmt)
        entry = result.scalar_one_or_none()

        if entry:
            # Phase 3b: base_audio_local_path may be stored as a relative
            # filename (post-migration) or as a legacy absolute path.
            abs_path = self._resolve_path(entry.base_audio_local_path)

            # Verify file still exists
            if not os.path.exists(abs_path):
                logger.warning(
                    f"Cache file missing: {abs_path} (cache_key={cache_key[:16]}...)"
                )
                await self.delete_entry(cache_key)
                return None

            logger.info(
                f"Cache HIT: {cache_key[:16]}... (hit_count={entry.hit_count}, "
                f"duration={entry.audio_duration_seconds:.2f}s)"
            )

            # Update hit count and last accessed time
            await self.increment_hit_count(cache_key)
            await self.db.refresh(entry)
            self.db.expunge(entry)

            # Expose the resolved absolute path
            entry.base_audio_local_path = abs_path
            return entry
        else:
            logger.info(f"Cache MISS: {cache_key[:16]}...")
            return None

    async def store(
        self,
        text: str,
        audio_prompt_path: str,
        base_audio_local_path: str,
        audio_duration_seconds: float,
        synthesis_duration_ms: int,
        language: Optional[str] = None,
    ) -> TTSSynthesisCache:
        """
        Store new synthesis in cache.

        Args:
            text: Synthesized text
            audio_prompt_path: S3 path to voice prompt
            base_audio_local_path: Local path to base audio (ratio=1.0)
            audio_duration_seconds: Duration of audio
            synthesis_duration_ms: Time taken to synthesize
            language: Language code (e.g., 'en', 'zh')

        Returns:
            Created cache entry

        Raises:
            Exception: If file doesn't exist or database error

        Note:
            All cached audio is stored at ratio=1.0 (base speed).
            Time-stretching is applied separately when needed.
            base_audio_s3_path is intentionally not used. The cache is local-filesystem
            based for performance (avoid S3 latency on every synthesis lookup). S3 upload
            is handled separately by the worker/backend if backup is needed.
        """
        cache_key = self.generate_cache_key(text, audio_prompt_path)
        text_hash = self.generate_text_hash(text)

        # Verify file exists
        if not os.path.exists(base_audio_local_path):
            raise FileNotFoundError(
                f"Base audio file not found: {base_audio_local_path}"
            )

        # Get file size
        file_size_bytes = os.path.getsize(base_audio_local_path)

        # Generate semantic filename for easier debugging
        semantic_filename = self.generate_semantic_filename(text, audio_prompt_path)

        # Phase 3b: store relative path if inside cache_dir.
        src = Path(base_audio_local_path).resolve()
        try:
            relative_path = str(src.relative_to(self.cache_dir.resolve()))
        except ValueError:
            relative_path = base_audio_local_path

        # Phase 3a: upsert — first writer wins, concurrent stores are safe.
        stmt = (
            pg_insert(TTSSynthesisCache)
            .values(
                cache_key=cache_key,
                text=text,
                audio_prompt_path=audio_prompt_path,
                text_hash=text_hash,
                base_audio_local_path=relative_path,
                base_audio_s3_path=None,
                audio_duration_seconds=audio_duration_seconds,
                synthesis_duration_ms=synthesis_duration_ms,
                file_size_bytes=file_size_bytes,
                language=language,
                word_count=count_words(text),
            )
            .on_conflict_do_nothing(index_elements=["cache_key"])
        )
        await self.db.execute(stmt)
        await self.db.commit()

        # Return the entry (existing or freshly inserted).
        entry = (
            await self.db.execute(
                select(TTSSynthesisCache).where(
                    TTSSynthesisCache.cache_key == cache_key
                )
            )
        ).scalar_one()
        self.db.expunge(entry)
        entry.base_audio_local_path = self._resolve_path(entry.base_audio_local_path)

        logger.success(
            f"Cache STORED: {semantic_filename} (duration={audio_duration_seconds:.2f}s, "
            f"size={file_size_bytes / 1024:.1f}KB)"
        )

        return entry

    async def increment_hit_count(self, cache_key: str) -> None:
        """
        Increment hit count and update last accessed time.

        Args:
            cache_key: Cache key to update

        Note:
            Called automatically by lookup() on cache hit
        """
        stmt = select(TTSSynthesisCache).where(TTSSynthesisCache.cache_key == cache_key)
        result = await self.db.execute(stmt)
        entry = result.scalar_one_or_none()

        if entry:
            entry.hit_count += 1
            entry.last_accessed_at = datetime.utcnow()
            await self.db.commit()

    async def delete_entry(self, cache_key: str) -> bool:
        """
        Delete cache entry and associated file.

        Args:
            cache_key: Cache key to delete

        Returns:
            True if deleted, False if not found
        """
        stmt = select(TTSSynthesisCache).where(TTSSynthesisCache.cache_key == cache_key)
        result = await self.db.execute(stmt)
        entry = result.scalar_one_or_none()

        if entry:
            # Delete file
            abs_path = self._resolve_path(entry.base_audio_local_path)
            try:
                if os.path.exists(abs_path):
                    os.remove(abs_path)
                    logger.info(f"Deleted cache file: {abs_path}")
            except Exception as e:
                logger.warning(f"Failed to delete cache file: {e}")

            # Delete DB entry
            await self.db.delete(entry)
            await self.db.commit()

            logger.info(f"Deleted cache entry: {cache_key[:16]}...")
            return True
        else:
            logger.warning(f"Cache entry not found: {cache_key[:16]}...")
            return False

    async def get_cache_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.

        Returns:
            Dictionary with cache metrics:
            - total_entries: Number of cache entries
            - total_hits: Sum of all hit counts
            - total_size_mb: Total disk space used
            - avg_hits_per_entry: Average reuse rate
        """
        # Total entries
        total_stmt = select(func.count(TTSSynthesisCache.cache_key))
        total_result = await self.db.execute(total_stmt)
        total_entries = total_result.scalar() or 0

        # Total hits
        hits_stmt = select(func.sum(TTSSynthesisCache.hit_count))
        hits_result = await self.db.execute(hits_stmt)
        total_hits = hits_result.scalar() or 0

        # Total size
        size_stmt = select(func.sum(TTSSynthesisCache.file_size_bytes))
        size_result = await self.db.execute(size_stmt)
        total_size_bytes = size_result.scalar() or 0

        return {
            "total_entries": int(total_entries),
            "total_hits": int(total_hits),
            "total_size_mb": round(float(total_size_bytes) / (1024 * 1024), 2),
            "avg_hits_per_entry": (
                round(float(total_hits) / max(total_entries, 1), 2)
                if total_entries > 0
                else 0.0
            ),
        }

    async def evict_old_entries(
        self, max_entries: int = 10000, evict_count: int = 1000
    ) -> int:
        """
        Evict oldest cache entries when limit exceeded (LRU eviction).

        Uses a single bulk DELETE instead of N individual deletes (Phase 3c).

        Args:
            max_entries: Maximum number of cache entries to keep
            evict_count: Number of entries to evict when threshold reached

        Returns:
            Number of entries evicted
        """
        # Count current entries
        count_stmt = select(func.count(TTSSynthesisCache.cache_key))
        count_result = await self.db.execute(count_stmt)
        current_count = count_result.scalar() or 0

        if current_count <= max_entries:
            logger.info(
                f"Cache within limit ({current_count}/{max_entries}), no eviction needed"
            )
            return 0

        logger.warning(
            f"Cache limit exceeded ({current_count}/{max_entries}), evicting {evict_count} entries"
        )

        # Fetch keys + paths of the LRU entries to evict.
        lru_stmt = (
            select(
                TTSSynthesisCache.cache_key,
                TTSSynthesisCache.base_audio_local_path,
            )
            .order_by(TTSSynthesisCache.last_accessed_at.asc())
            .limit(evict_count)
        )
        result = await self.db.execute(lru_stmt)
        rows = result.all()
        keys_to_evict = [r.cache_key for r in rows]

        if not keys_to_evict:
            return 0

        # Phase 3c: delete files first, then bulk-DELETE DB rows.
        for r in rows:
            abs_path = self._resolve_path(r.base_audio_local_path)
            try:
                if os.path.exists(abs_path):
                    os.remove(abs_path)
            except OSError as exc:
                logger.warning(f"Failed to delete cache file: {exc}")

        await self.db.execute(
            delete(TTSSynthesisCache).where(
                TTSSynthesisCache.cache_key.in_(keys_to_evict)
            )
        )
        await self.db.commit()

        evicted = len(keys_to_evict)
        logger.success(f"Evicted {evicted} cache entries")
        return evicted

    async def get_top_entries(self, limit: int = 20) -> List[TTSSynthesisCache]:
        """
        Get most frequently accessed cache entries.

        Args:
            limit: Maximum number of entries to return

        Returns:
            List of cache entries sorted by hit_count (descending)
        """
        stmt = (
            select(TTSSynthesisCache)
            .order_by(TTSSynthesisCache.hit_count.desc())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def clear_all(self) -> int:
        """
        Clear entire cache (delete all entries and files).

        Uses a single bulk DELETE (Phase 3c).

        Returns:
            Number of entries deleted

        WARNING: This is destructive and cannot be undone!
        """
        result = await self.db.execute(
            select(
                TTSSynthesisCache.cache_key,
                TTSSynthesisCache.base_audio_local_path,
            )
        )
        rows = result.all()

        for r in rows:
            abs_path = self._resolve_path(r.base_audio_local_path)
            try:
                if os.path.exists(abs_path):
                    os.remove(abs_path)
            except OSError as exc:
                logger.warning(f"Failed to delete cache file: {exc}")

        await self.db.execute(delete(TTSSynthesisCache))
        await self.db.commit()

        deleted = len(rows)
        logger.warning(f"Cleared entire cache: {deleted} entries deleted")
        return deleted

    async def invalidate_voice_cache(self, audio_prompt_path: str) -> int:
        """
        Delete all cache entries using a specific voice.

        Uses a single bulk DELETE (Phase 3c).

        Useful when a voice is updated or deleted.

        Args:
            audio_prompt_path: S3 path to voice prompt

        Returns:
            Number of entries deleted
        """
        result = await self.db.execute(
            select(
                TTSSynthesisCache.cache_key,
                TTSSynthesisCache.base_audio_local_path,
            ).where(TTSSynthesisCache.audio_prompt_path == audio_prompt_path)
        )
        rows = result.all()

        for r in rows:
            abs_path = self._resolve_path(r.base_audio_local_path)
            try:
                if os.path.exists(abs_path):
                    os.remove(abs_path)
            except OSError as exc:
                logger.warning(f"Failed to delete cache file: {exc}")

        await self.db.execute(
            delete(TTSSynthesisCache).where(
                TTSSynthesisCache.audio_prompt_path == audio_prompt_path
            )
        )
        await self.db.commit()

        deleted = len(rows)
        logger.info(
            f"Invalidated cache for voice '{audio_prompt_path}': {deleted} entries deleted"
        )
        return deleted


# ---------------------------------------------------------------------------
# Synchronous cache service (Phase 2)
# ---------------------------------------------------------------------------


class TTSCacheServiceSync:
    """Synchronous mirror of TTSCacheService using a plain SQLAlchemy Session.

    All methods are regular ``def`` (no async/await).  This is used by
    :class:`services.cache_manager.CacheManager` so it can query the DB
    directly from the synchronous ``process_job()`` call chain without
    spawning a thread + event loop per call.

    The API surface intentionally mirrors :class:`TTSCacheService` so that
    callers can switch between the two with minimal changes.
    """

    # Re-use the static helpers from the async class so there is no duplication.
    extract_voice_id = staticmethod(TTSCacheService.extract_voice_id)
    sanitize_text_for_filename = staticmethod(
        TTSCacheService.sanitize_text_for_filename
    )
    generate_semantic_filename = staticmethod(
        TTSCacheService.generate_semantic_filename
    )
    generate_cache_key = staticmethod(TTSCacheService.generate_cache_key)
    generate_text_hash = staticmethod(TTSCacheService.generate_text_hash)

    def __init__(self, db_session: Session, cache_dir: str = "outputs/tts_cache"):
        """
        Initialize synchronous cache service.

        Args:
            db_session: Synchronous SQLAlchemy ``Session`` (psycopg2 driver).
            cache_dir: Directory for cached audio files.
        """
        self.db = db_session
        self.cache_dir = Path(cache_dir).resolve()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_path(self, stored_path: str) -> str:
        """Resolve a stored DB path (relative or absolute) to a full local filesystem path."""
        if not os.path.isabs(stored_path):
            return str(self.cache_dir / stored_path)
        return stored_path

    def lookup(self, text: str, audio_prompt_path: str) -> Optional[TTSSynthesisCache]:
        """Look up cached synthesis by text and voice (synchronous).

        Verifies file still exists and updates hit statistics **in-place**
        (single SELECT + UPDATE, no redundant round-trip).

        Args:
            text: Synthesized text.
            audio_prompt_path: S3 path to voice prompt.

        Returns:
            Cache entry if found and valid, ``None`` otherwise.
        """
        cache_key = self.generate_cache_key(text, audio_prompt_path)

        stmt = select(TTSSynthesisCache).where(TTSSynthesisCache.cache_key == cache_key)
        entry = self.db.execute(stmt).scalar_one_or_none()

        if entry:
            # Phase 3b: base_audio_local_path may be stored as a relative
            # filename (post-migration) or as a legacy absolute path.
            abs_path = self._resolve_path(entry.base_audio_local_path)

            # Verify file still exists.
            if not os.path.exists(abs_path):
                logger.warning(
                    f"Cache file missing: {abs_path} (cache_key={cache_key[:16]}...)"
                )
                self.delete_entry(cache_key)
                return None

            logger.info(
                f"Cache HIT: {cache_key[:16]}... (hit_count={entry.hit_count}, "
                f"duration={entry.audio_duration_seconds:.2f}s)"
            )

            # Inline hit-count update — no extra SELECT (Phase 2 optimisation).
            entry.hit_count += 1
            entry.last_accessed_at = datetime.utcnow()
            self.db.commit()
            self.db.refresh(entry)
            self.db.expunge(entry)

            # Expose the resolved absolute path after commit so callers receive
            # a valid filesystem path without persisting the absolute path to DB.
            entry.base_audio_local_path = abs_path

            return entry

        logger.info(f"Cache MISS: {cache_key[:16]}...")
        return None

    def store(
        self,
        text: str,
        audio_prompt_path: str,
        base_audio_local_path: str,
        audio_duration_seconds: float,
        synthesis_duration_ms: int,
        language: Optional[str] = None,
    ) -> TTSSynthesisCache:
        """Store new synthesis in cache (synchronous).

        Uses ``INSERT ... ON CONFLICT DO NOTHING`` so concurrent workers
        synthesising the same (text, voice) pair don't raise a PK
        violation — the first writer wins and subsequent stores are
        silently ignored (Phase 3a).

        Args:
            text: Synthesized text.
            audio_prompt_path: S3 path to voice prompt.
            base_audio_local_path: Local path to base audio (ratio=1.0).
            audio_duration_seconds: Duration of audio.
            synthesis_duration_ms: Time taken to synthesize.
            language: Language code (e.g. ``'en'``, ``'zh'``).

        Returns:
            Cache entry (existing or newly created).

        Raises:
            FileNotFoundError: If ``base_audio_local_path`` does not exist.
        """
        cache_key = self.generate_cache_key(text, audio_prompt_path)
        text_hash = self.generate_text_hash(text)

        if not os.path.exists(base_audio_local_path):
            raise FileNotFoundError(
                f"Base audio file not found: {base_audio_local_path}"
            )

        file_size_bytes = os.path.getsize(base_audio_local_path)
        semantic_filename = self.generate_semantic_filename(text, audio_prompt_path)

        # Phase 3b: store only the filename (relative to cache_dir) when the
        # source file already lives inside cache_dir.  Files supplied from
        # outside cache_dir (e.g. /tmp during synthesis) are stored with their
        # absolute path so lookup can still find them.
        src = Path(base_audio_local_path).resolve()
        try:
            relative_path = str(src.relative_to(self.cache_dir.resolve()))
        except ValueError:
            # File is outside cache_dir — keep the absolute path.
            relative_path = base_audio_local_path

        # Phase 3a: upsert — first writer wins, concurrent stores are safe.
        stmt = (
            pg_insert(TTSSynthesisCache)
            .values(
                cache_key=cache_key,
                text=text,
                audio_prompt_path=audio_prompt_path,
                text_hash=text_hash,
                base_audio_local_path=relative_path,
                base_audio_s3_path=None,
                audio_duration_seconds=audio_duration_seconds,
                synthesis_duration_ms=synthesis_duration_ms,
                file_size_bytes=file_size_bytes,
                language=language,
                word_count=count_words(text),
            )
            .on_conflict_do_nothing(index_elements=["cache_key"])
        )
        self.db.execute(stmt)
        self.db.commit()

        # Return the entry (existing or freshly inserted).
        entry = self.db.execute(
            select(TTSSynthesisCache).where(TTSSynthesisCache.cache_key == cache_key)
        ).scalar_one()
        self.db.expunge(entry)
        entry.base_audio_local_path = self._resolve_path(entry.base_audio_local_path)

        logger.success(
            f"Cache STORED: {semantic_filename} (duration={audio_duration_seconds:.2f}s, "
            f"size={file_size_bytes / 1024:.1f}KB)"
        )

        return entry

    def delete_entry(self, cache_key: str) -> bool:
        """Delete cache entry and associated file (synchronous).

        Args:
            cache_key: Cache key to delete.

        Returns:
            ``True`` if deleted, ``False`` if not found.
        """
        stmt = select(TTSSynthesisCache).where(TTSSynthesisCache.cache_key == cache_key)
        entry = self.db.execute(stmt).scalar_one_or_none()

        if entry:
            abs_path = self._resolve_path(entry.base_audio_local_path)
            try:
                if os.path.exists(abs_path):
                    os.remove(abs_path)
                    logger.info(f"Deleted cache file: {abs_path}")
            except Exception as e:
                logger.warning(f"Failed to delete cache file: {e}")

            self.db.delete(entry)
            self.db.commit()
            logger.info(f"Deleted cache entry: {cache_key[:16]}...")
            return True

        logger.warning(f"Cache entry not found: {cache_key[:16]}...")
        return False

    def evict_old_entries(
        self, max_entries: int = 10000, evict_count: int = 1000
    ) -> int:
        """Evict oldest cache entries when limit exceeded (LRU, synchronous).

        Uses a single bulk ``DELETE`` instead of N individual deletes
        (Phase 3c) to reduce DB round-trips from O(N×3) to O(1).

        Args:
            max_entries: Maximum number of cache entries to keep.
            evict_count: Number of entries to evict when threshold reached.

        Returns:
            Number of entries evicted.
        """
        count_stmt = select(func.count(TTSSynthesisCache.cache_key))
        current_count = self.db.execute(count_stmt).scalar() or 0

        if current_count <= max_entries:
            logger.info(
                f"Cache within limit ({current_count}/{max_entries}), no eviction needed"
            )
            return 0

        logger.warning(
            f"Cache limit exceeded ({current_count}/{max_entries}), "
            f"evicting {evict_count} entries"
        )

        # Fetch keys + paths of the LRU entries to evict.
        lru_stmt = (
            select(
                TTSSynthesisCache.cache_key,
                TTSSynthesisCache.base_audio_local_path,
            )
            .order_by(TTSSynthesisCache.last_accessed_at.asc())
            .limit(evict_count)
        )
        rows = self.db.execute(lru_stmt).all()
        keys_to_evict = [r.cache_key for r in rows]

        if not keys_to_evict:
            return 0

        # Phase 3c: delete files first, then bulk-DELETE DB rows.
        for r in rows:
            abs_path = self._resolve_path(r.base_audio_local_path)
            try:
                if os.path.exists(abs_path):
                    os.remove(abs_path)
            except OSError as exc:
                logger.warning(f"Failed to delete cache file: {exc}")

        self.db.execute(
            delete(TTSSynthesisCache).where(
                TTSSynthesisCache.cache_key.in_(keys_to_evict)
            )
        )
        self.db.commit()

        evicted = len(keys_to_evict)
        logger.success(f"Evicted {evicted} cache entries")
        return evicted

    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics (synchronous).

        Returns:
            Dictionary with ``total_entries``, ``total_hits``,
            ``total_size_mb``, and ``avg_hits_per_entry``.
        """
        total_entries = (
            self.db.execute(select(func.count(TTSSynthesisCache.cache_key))).scalar()
            or 0
        )

        total_hits = (
            self.db.execute(select(func.sum(TTSSynthesisCache.hit_count))).scalar() or 0
        )

        total_size_bytes = (
            self.db.execute(
                select(func.sum(TTSSynthesisCache.file_size_bytes))
            ).scalar()
            or 0
        )

        return {
            "total_entries": int(total_entries),
            "total_hits": int(total_hits),
            "total_size_mb": round(float(total_size_bytes) / (1024 * 1024), 2),
            "avg_hits_per_entry": (
                round(float(total_hits) / max(total_entries, 1), 2)
                if total_entries > 0
                else 0.0
            ),
        }

    def get_top_entries(self, limit: int = 20) -> List[TTSSynthesisCache]:
        """Get most frequently accessed cache entries (synchronous).

        Args:
            limit: Maximum number of entries to return.

        Returns:
            List of cache entries sorted by ``hit_count`` descending.
        """
        stmt = (
            select(TTSSynthesisCache)
            .order_by(TTSSynthesisCache.hit_count.desc())
            .limit(limit)
        )
        return list(self.db.execute(stmt).scalars().all())

    def clear_all(self) -> int:
        """Clear entire cache, deleting all entries and files (synchronous).

        Uses a single bulk ``DELETE`` (Phase 3c).

        Returns:
            Number of entries deleted.

        .. warning:: This is destructive and cannot be undone.
        """
        rows = self.db.execute(
            select(
                TTSSynthesisCache.cache_key,
                TTSSynthesisCache.base_audio_local_path,
            )
        ).all()

        for r in rows:
            abs_path = self._resolve_path(r.base_audio_local_path)
            try:
                if os.path.exists(abs_path):
                    os.remove(abs_path)
            except OSError as exc:
                logger.warning(f"Failed to delete cache file: {exc}")

        self.db.execute(delete(TTSSynthesisCache))
        self.db.commit()

        deleted = len(rows)
        logger.warning(f"Cleared entire cache: {deleted} entries deleted")
        return deleted

    def invalidate_voice_cache(self, audio_prompt_path: str) -> int:
        """Delete all cache entries using a specific voice (synchronous).

        Uses a single bulk ``DELETE`` (Phase 3c).

        Args:
            audio_prompt_path: S3 path to voice prompt.

        Returns:
            Number of entries deleted.
        """
        rows = self.db.execute(
            select(
                TTSSynthesisCache.cache_key,
                TTSSynthesisCache.base_audio_local_path,
            ).where(TTSSynthesisCache.audio_prompt_path == audio_prompt_path)
        ).all()

        for r in rows:
            abs_path = self._resolve_path(r.base_audio_local_path)
            try:
                if os.path.exists(abs_path):
                    os.remove(abs_path)
            except OSError as exc:
                logger.warning(f"Failed to delete cache file: {exc}")

        self.db.execute(
            delete(TTSSynthesisCache).where(
                TTSSynthesisCache.audio_prompt_path == audio_prompt_path
            )
        )
        self.db.commit()

        deleted = len(rows)
        logger.info(
            f"Invalidated cache for voice '{audio_prompt_path}': {deleted} entries deleted"
        )
        return deleted
