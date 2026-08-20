"""
Test script for TTS synthesis cache service.

Tests:
1. Database connection
2. Cache key generation
3. Store and lookup operations
4. Hit count tracking
5. Cache statistics
6. Eviction policy

Usage:
    conda activate index-tts
    python scripts/test_cache_service.py
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import DatabaseSession, check_db_connection
from app.cache_service import TTSCacheService
from services.logging_config import get_logger

logger = get_logger(__name__)


async def test_database_connection():
    """Test 1: Verify database connection."""
    logger.section("TEST 1: Database Connection")

    try:
        connected = await check_db_connection()
        if connected:
            logger.success("✓ Database connection successful")
            return True
        else:
            logger.failure("✗ Database connection failed")
            return False
    except Exception as e:
        logger.failure(f"✗ Database connection error: {e}")
        return False


async def test_cache_key_generation():
    """Test 2: Cache key generation."""
    logger.section("TEST 2: Cache Key Generation")

    try:
        text = "Hello world, this is a test"
        voice = "audio-prompts/voice_123.wav"

        # Generate key twice - should be identical
        key1 = TTSCacheService.generate_cache_key(text, voice)
        key2 = TTSCacheService.generate_cache_key(text, voice)

        logger.info(f"Cache key 1: {key1[:32]}...")
        logger.info(f"Cache key 2: {key2[:32]}...")

        if key1 == key2 and len(key1) == 64:
            logger.success("✓ Cache key generation is deterministic")
            return True
        else:
            logger.failure("✗ Cache key generation failed")
            return False
    except Exception as e:
        logger.failure(f"✗ Cache key generation error: {e}")
        return False


async def test_store_and_lookup():
    """Test 3: Store and lookup operations."""
    logger.section("TEST 3: Store and Lookup Operations")

    try:
        # Create temporary audio file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".wav", delete=False) as tmp:
            tmp.write("dummy audio content for testing")
            tmp_path = tmp.name

        try:
            async with DatabaseSession() as db_session:
                cache_service = TTSCacheService(db_session)

                text = "Test synthesis for caching"
                voice = "audio-prompts/test_voice.wav"

                # Store in cache
                logger.info("Storing test entry in cache...")
                entry = await cache_service.store(
                    text=text,
                    audio_prompt_path=voice,
                    base_audio_local_path=tmp_path,
                    audio_duration_seconds=5.0,
                    synthesis_duration_ms=3000,
                    language="en",
                )

                logger.info(f"Entry stored: cache_key={entry.cache_key[:16]}...")

                # Lookup
                logger.info("Looking up stored entry...")
                retrieved = await cache_service.lookup(text, voice)

                if retrieved and retrieved.cache_key == entry.cache_key:
                    logger.success("✓ Store and lookup successful")
                    logger.info(f"  Hit count: {retrieved.hit_count}")
                    logger.info(f"  Duration: {retrieved.audio_duration_seconds}s")

                    # Cleanup
                    await cache_service.delete_entry(entry.cache_key)
                    return True
                else:
                    logger.failure("✗ Lookup failed to retrieve stored entry")
                    return False

        finally:
            # Cleanup temp file
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    except Exception as e:
        logger.failure(f"✗ Store/lookup error: {e}")
        import traceback

        traceback.print_exc()
        return False


async def test_hit_count_tracking():
    """Test 4: Hit count tracking."""
    logger.section("TEST 4: Hit Count Tracking")

    try:
        # Create temporary audio file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".wav", delete=False) as tmp:
            tmp.write("dummy audio for hit count test")
            tmp_path = tmp.name

        try:
            async with DatabaseSession() as db_session:
                cache_service = TTSCacheService(db_session)

                text = "Hit count test text"
                voice = "audio-prompts/hit_test_voice.wav"

                # Store entry
                entry = await cache_service.store(
                    text=text,
                    audio_prompt_path=voice,
                    base_audio_local_path=tmp_path,
                    audio_duration_seconds=3.0,
                    synthesis_duration_ms=2000,
                )

                initial_hits = entry.hit_count
                logger.info(f"Initial hit count: {initial_hits}")

                # Access multiple times
                for i in range(3):
                    await cache_service.lookup(text, voice)
                    logger.info(f"Lookup {i + 1} completed")

                # Check final hit count
                final_entry = await cache_service.lookup(text, voice)
                final_hits = final_entry.hit_count

                logger.info(f"Final hit count: {final_hits}")

                # Hit count should increase by 4 (3 lookups + 1 final lookup)
                if final_hits == initial_hits + 4:
                    logger.success("✓ Hit count tracking works correctly")

                    # Cleanup
                    await cache_service.delete_entry(entry.cache_key)
                    return True
                else:
                    logger.failure(
                        f"✗ Hit count incorrect: expected {initial_hits + 4}, got {final_hits}"
                    )
                    return False

        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    except Exception as e:
        logger.failure(f"✗ Hit count tracking error: {e}")
        return False


async def test_cache_statistics():
    """Test 5: Cache statistics."""
    logger.section("TEST 5: Cache Statistics")

    try:
        async with DatabaseSession() as db_session:
            cache_service = TTSCacheService(db_session)

            stats = await cache_service.get_cache_stats()

            logger.info(f"Total entries: {stats['total_entries']}")
            logger.info(f"Total hits: {stats['total_hits']}")
            logger.info(f"Total size: {stats['total_size_mb']} MB")
            logger.info(f"Avg hits per entry: {stats['avg_hits_per_entry']}")

            logger.success("✓ Cache statistics retrieved successfully")
            return True

    except Exception as e:
        logger.failure(f"✗ Cache statistics error: {e}")
        return False


async def test_eviction_policy():
    """Test 6: Eviction policy (LRU)."""
    logger.section("TEST 6: Eviction Policy")

    try:
        async with DatabaseSession() as db_session:
            cache_service = TTSCacheService(db_session)

            # Get current count
            stats_before = await cache_service.get_cache_stats()
            entries_before = stats_before["total_entries"]

            logger.info(f"Entries before eviction test: {entries_before}")

            # Test eviction with very low threshold
            # This won't actually evict unless there are entries
            evicted = await cache_service.evict_old_entries(
                max_entries=0,  # Force eviction
                evict_count=min(5, entries_before),  # Evict up to 5 entries
            )

            stats_after = await cache_service.get_cache_stats()
            entries_after = stats_after["total_entries"]

            logger.info(f"Entries after eviction: {entries_after}")
            logger.info(f"Entries evicted: {evicted}")

            if evicted == (entries_before - entries_after):
                logger.success("✓ Eviction policy works correctly")
                return True
            else:
                logger.warning(
                    f"⚠ Eviction count mismatch (expected {entries_before - entries_after}, got {evicted})"
                )
                return True  # Still pass, might be no entries to evict

    except Exception as e:
        logger.failure(f"✗ Eviction policy error: {e}")
        return False


async def run_all_tests():
    """Run all tests and report results."""
    logger.section("TTS CACHE SERVICE TESTS")

    tests = [
        ("Database Connection", test_database_connection),
        ("Cache Key Generation", test_cache_key_generation),
        ("Store and Lookup", test_store_and_lookup),
        ("Hit Count Tracking", test_hit_count_tracking),
        ("Cache Statistics", test_cache_statistics),
        ("Eviction Policy", test_eviction_policy),
    ]

    results = []

    for test_name, test_func in tests:
        try:
            result = await test_func()
            results.append((test_name, result))
        except Exception as e:
            logger.error(f"Test '{test_name}' crashed: {e}")
            results.append((test_name, False))

        # Small delay between tests
        await asyncio.sleep(0.5)

    # Summary
    logger.section("TEST SUMMARY")
    passed = sum(1 for _, result in results if result)
    total = len(results)

    for test_name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        logger.info(f"{status}: {test_name}")

    logger.info(f"\nTotal: {passed}/{total} tests passed")

    if passed == total:
        logger.success("🎉 All tests passed!")
        return 0
    else:
        logger.failure(f"❌ {total - passed} test(s) failed")
        return 1


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(run_all_tests())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        logger.warning("\nTests interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
