"""
Cache management utility for TTS synthesis cache.

Commands:
    stats       - Show cache statistics
    top         - Show most frequently accessed entries
    evict       - Evict oldest cache entries
    clear       - Clear entire cache (WARNING: destructive!)
    invalidate  - Invalidate all entries for a specific voice

Usage:
    conda activate index-tts
    python scripts/manage_cache.py stats
    python scripts/manage_cache.py top --limit 20
    python scripts/manage_cache.py evict --count 1000
    python scripts/manage_cache.py clear --confirm
    python scripts/manage_cache.py invalidate --voice "audio-prompts/voice_123.wav"
"""

import asyncio
import argparse
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import DatabaseSession
from app.cache_service import TTSCacheService
from services.logging_config import get_logger

logger = get_logger(__name__)


async def cmd_stats():
    """Show cache statistics."""
    logger.section("CACHE STATISTICS")

    async with DatabaseSession() as db:
        service = TTSCacheService(db)
        stats = await service.get_cache_stats()

        print(f"📊 Total entries:        {stats['total_entries']:,}")
        print(f"🎯 Total hits:           {stats['total_hits']:,}")
        print(f"💾 Total size:           {stats['total_size_mb']:.2f} MB")
        print(f"📈 Avg hits per entry:   {stats['avg_hits_per_entry']:.2f}")

        # Calculate cache hit rate (if we had total requests)
        if stats["total_entries"] > 0:
            print(
                f"\n💡 Average reuse rate:   {stats['avg_hits_per_entry']:.1f}x per entry"
            )


async def cmd_top(limit: int = 20):
    """Show most frequently accessed entries."""
    logger.section(f"TOP {limit} CACHE ENTRIES")

    async with DatabaseSession() as db:
        service = TTSCacheService(db)
        entries = await service.get_top_entries(limit)

        if not entries:
            print("No cache entries found.")
            return

        print(f"\nShowing top {len(entries)} entries by hit count:\n")

        for i, entry in enumerate(entries, 1):
            text_preview = (
                entry.text[:60] + "..." if len(entry.text) > 60 else entry.text
            )
            voice_name = (
                entry.audio_prompt_path.split("/")[-1]
                if "/" in entry.audio_prompt_path
                else entry.audio_prompt_path
            )

            print(
                f"{i:2d}. 🎯 Hits: {entry.hit_count:4d} | ⏱️  {entry.audio_duration_seconds:.1f}s | 🗣️  {voice_name}"
            )
            print(f"    📝 Text: {text_preview}")
            print(f"    🆔 Key:  {entry.cache_key[:24]}...")
            print()


async def cmd_evict(count: int = 1000):
    """Evict oldest cache entries."""
    logger.section(f"EVICTING {count} ENTRIES")

    async with DatabaseSession() as db:
        service = TTSCacheService(db)

        # Get stats before
        stats_before = await service.get_cache_stats()
        entries_before = stats_before["total_entries"]

        print(f"Current cache size: {entries_before:,} entries")

        if entries_before == 0:
            print("Cache is empty, nothing to evict.")
            return

        # Confirm
        actual_count = min(count, entries_before)
        print(f"\n⚠️  About to evict {actual_count} oldest entries...")
        response = input("Continue? (y/N): ").strip().lower()

        if response != "y":
            print("Eviction cancelled.")
            return

        # Evict
        evicted = await service.evict_old_entries(
            max_entries=entries_before - actual_count, evict_count=actual_count
        )

        # Get stats after
        stats_after = await service.get_cache_stats()
        entries_after = stats_after["total_entries"]

        print(f"\n✅ Evicted {evicted} entries")
        print(f"Cache size: {entries_before:,} → {entries_after:,}")


async def cmd_clear(confirm: bool = False):
    """Clear entire cache."""
    logger.section("CLEAR ENTIRE CACHE")

    async with DatabaseSession() as db:
        service = TTSCacheService(db)

        # Get stats
        stats = await service.get_cache_stats()
        entries = stats["total_entries"]

        if entries == 0:
            print("Cache is already empty.")
            return

        print(f"⚠️  WARNING: This will delete ALL {entries:,} cache entries!")
        print(f"💾 Total size: {stats['total_size_mb']:.2f} MB")
        print(f"🎯 Total hits: {stats['total_hits']:,}")

        if not confirm:
            print("\n⚠️  This operation cannot be undone!")
            response = input("Type 'DELETE' to confirm: ").strip()

            if response != "DELETE":
                print("Clear operation cancelled.")
                return

        # Clear
        deleted = await service.clear_all()

        print(f"\n✅ Cleared cache: {deleted:,} entries deleted")


async def cmd_invalidate(voice_path: str):
    """Invalidate all entries for a specific voice."""
    logger.section("INVALIDATE VOICE CACHE")

    print(f"Voice: {voice_path}")

    async with DatabaseSession() as db:
        service = TTSCacheService(db)

        # Preview affected entries
        from sqlalchemy import select
        from app.models import TTSSynthesisCache

        stmt = select(TTSSynthesisCache).where(
            TTSSynthesisCache.audio_prompt_path == voice_path
        )
        result = await db.execute(stmt)
        entries = result.scalars().all()

        if not entries:
            print(f"No cache entries found for voice: {voice_path}")
            return

        print(f"\n⚠️  Found {len(entries)} entries using this voice")

        # Show sample
        if len(entries) > 0:
            print("\nSample entries:")
            for entry in entries[:3]:
                text_preview = (
                    entry.text[:50] + "..." if len(entry.text) > 50 else entry.text
                )
                print(f"  - {text_preview}")

        # Confirm
        response = (
            input(f"\nDelete all {len(entries)} entries? (y/N): ").strip().lower()
        )

        if response != "y":
            print("Invalidation cancelled.")
            return

        # Invalidate
        deleted = await service.invalidate_voice_cache(voice_path)

        print(f"\n✅ Invalidated {deleted} cache entries for voice")


async def cmd_inspect(cache_key: str):
    """Inspect a specific cache entry."""
    logger.section("INSPECT CACHE ENTRY")

    async with DatabaseSession() as db:
        from sqlalchemy import select
        from app.models import TTSSynthesisCache

        stmt = select(TTSSynthesisCache).where(
            TTSSynthesisCache.cache_key.like(f"{cache_key}%")
        )
        result = await db.execute(stmt)
        entry = result.scalar_one_or_none()

        if not entry:
            print(f"❌ No cache entry found matching: {cache_key}")
            return

        # Display full details
        print("\n📋 Cache Entry Details:")
        print("─" * 60)
        print(f"🆔 Cache Key:           {entry.cache_key}")
        print(f"📝 Text:                {entry.text}")
        print(f"🗣️  Voice:               {entry.audio_prompt_path}")
        print(f"📁 Local Path:          {entry.base_audio_local_path}")
        print(f"☁️  S3 Path:             {entry.base_audio_s3_path or '(none)'}")
        print(f"⏱️  Duration:            {entry.audio_duration_seconds:.2f}s")
        print(f"📊 Sample Rate:         {entry.sample_rate} Hz")
        print(f"💾 File Size:           {entry.file_size_bytes / 1024:.1f} KB")
        print(f"⚡ Synthesis Time:      {entry.synthesis_duration_ms}ms")
        print(f"🎯 Hit Count:           {entry.hit_count}")
        print(f"🕐 Created:             {entry.created_at}")
        print(f"🕐 Last Accessed:       {entry.last_accessed_at}")
        print(f"🌍 Language:            {entry.language or '(unknown)'}")
        print(f"🔧 TTS Engine:          {entry.tts_engine}")

        # Check file existence
        import os

        file_exists = os.path.exists(entry.base_audio_local_path)
        status = "✅ exists" if file_exists else "❌ missing"
        print(f"📂 File Status:         {status}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Manage TTS synthesis cache",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/manage_cache.py stats
  python scripts/manage_cache.py top --limit 20
  python scripts/manage_cache.py evict --count 1000
  python scripts/manage_cache.py clear --confirm
  python scripts/manage_cache.py invalidate --voice "audio-prompts/voice_123.wav"
  python scripts/manage_cache.py inspect --key abc123def456
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Stats command
    subparsers.add_parser("stats", help="Show cache statistics")

    # Top command
    top_parser = subparsers.add_parser(
        "top", help="Show most frequently accessed entries"
    )
    top_parser.add_argument(
        "--limit", type=int, default=20, help="Number of entries to show (default: 20)"
    )

    # Evict command
    evict_parser = subparsers.add_parser("evict", help="Evict oldest entries")
    evict_parser.add_argument(
        "--count",
        type=int,
        default=1000,
        help="Number of entries to evict (default: 1000)",
    )

    # Clear command
    clear_parser = subparsers.add_parser(
        "clear", help="Clear entire cache (WARNING: destructive!)"
    )
    clear_parser.add_argument(
        "--confirm", action="store_true", help="Skip confirmation prompt"
    )

    # Invalidate command
    invalidate_parser = subparsers.add_parser(
        "invalidate", help="Invalidate all entries for a specific voice"
    )
    invalidate_parser.add_argument(
        "--voice",
        required=True,
        help="Voice S3 path (e.g., 'audio-prompts/voice_123.wav')",
    )

    # Inspect command
    inspect_parser = subparsers.add_parser(
        "inspect", help="Inspect a specific cache entry"
    )
    inspect_parser.add_argument("--key", required=True, help="Cache key (or prefix)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    # Run command
    try:
        if args.command == "stats":
            asyncio.run(cmd_stats())
        elif args.command == "top":
            asyncio.run(cmd_top(args.limit))
        elif args.command == "evict":
            asyncio.run(cmd_evict(args.count))
        elif args.command == "clear":
            asyncio.run(cmd_clear(args.confirm))
        elif args.command == "invalidate":
            asyncio.run(cmd_invalidate(args.voice))
        elif args.command == "inspect":
            asyncio.run(cmd_inspect(args.key))
        else:
            parser.print_help()
            return 1

        return 0

    except KeyboardInterrupt:
        logger.warning("\nOperation cancelled by user")
        return 1
    except Exception as e:
        logger.error(f"Error: {e}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
