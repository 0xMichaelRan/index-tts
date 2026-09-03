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

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

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
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

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
            # Verify file still exists
            if not os.path.exists(entry.base_audio_local_path):
                logger.warning(
                    f"Cache file missing: {entry.base_audio_local_path} (cache_key={cache_key[:16]}...)"
                )
                await self.delete_entry(cache_key)
                return None

            logger.info(
                f"Cache HIT: {cache_key[:16]}... (hit_count={entry.hit_count}, "
                f"duration={entry.audio_duration_seconds:.2f}s)"
            )

            # Update hit count and last accessed time
            await self.increment_hit_count(cache_key)

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

        # Create cache entry
        entry = TTSSynthesisCache(
            cache_key=cache_key,
            text=text,
            audio_prompt_path=audio_prompt_path,
            text_hash=text_hash,
            base_audio_local_path=base_audio_local_path,
            base_audio_s3_path=None,  # S3 backup not used; cache is local-based for speed
            audio_duration_seconds=audio_duration_seconds,
            synthesis_duration_ms=synthesis_duration_ms,
            file_size_bytes=file_size_bytes,
            language=language,
            word_count=count_words(text),
        )

        self.db.add(entry)
        await self.db.commit()
        await self.db.refresh(entry)

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
            try:
                if os.path.exists(entry.base_audio_local_path):
                    os.remove(entry.base_audio_local_path)
                    logger.info(f"Deleted cache file: {entry.base_audio_local_path}")
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
            "total_entries": total_entries,
            "total_hits": total_hits,
            "total_size_mb": round(total_size_bytes / (1024 * 1024), 2),
            "avg_hits_per_entry": (
                round(total_hits / max(total_entries, 1), 2) if total_entries > 0 else 0
            ),
        }

    async def evict_old_entries(
        self, max_entries: int = 10000, evict_count: int = 1000
    ) -> int:
        """
        Evict oldest cache entries when limit exceeded (LRU eviction).

        Args:
            max_entries: Maximum number of cache entries to keep
            evict_count: Number of entries to evict when threshold reached

        Returns:
            Number of entries evicted

        Strategy:
            - Sort by last_accessed_at (ascending)
            - Delete oldest N entries
            - Also delete associated files
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

        # Get oldest entries (by last_accessed_at)
        stmt = (
            select(TTSSynthesisCache)
            .order_by(TTSSynthesisCache.last_accessed_at.asc())
            .limit(evict_count)
        )
        result = await self.db.execute(stmt)
        entries_to_evict = result.scalars().all()

        # Delete entries
        evicted = 0
        for entry in entries_to_evict:
            if await self.delete_entry(entry.cache_key):
                evicted += 1

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

        Returns:
            Number of entries deleted

        WARNING: This is destructive and cannot be undone!
        """
        # Get all entries
        stmt = select(TTSSynthesisCache)
        result = await self.db.execute(stmt)
        entries = result.scalars().all()

        # Delete all entries and files
        deleted = 0
        for entry in entries:
            if await self.delete_entry(entry.cache_key):
                deleted += 1

        logger.warning(f"Cleared entire cache: {deleted} entries deleted")
        return deleted

    async def invalidate_voice_cache(self, audio_prompt_path: str) -> int:
        """
        Delete all cache entries using a specific voice.

        Useful when a voice is updated or deleted.

        Args:
            audio_prompt_path: S3 path to voice prompt

        Returns:
            Number of entries deleted
        """
        # Get all entries with this voice
        stmt = select(TTSSynthesisCache).where(
            TTSSynthesisCache.audio_prompt_path == audio_prompt_path
        )
        result = await self.db.execute(stmt)
        entries = result.scalars().all()

        # Delete all matching entries
        deleted = 0
        for entry in entries:
            if await self.delete_entry(entry.cache_key):
                deleted += 1

        logger.info(
            f"Invalidated cache for voice '{audio_prompt_path}': {deleted} entries deleted"
        )
        return deleted
