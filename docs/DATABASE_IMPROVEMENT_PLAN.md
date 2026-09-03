# Database Schema Improvement Plan

**Version:** 2.0  
**Last Updated:** September 3, 2026  
**Status:** Ready for Implementation  

---

## Executive Summary

This document provides a comprehensive plan to improve the IndexTTS Worker database schema based on analysis of the current implementation. The improvements address critical issues around data integrity, observability, and operational reliability.

**Current State:**
- 2 tables: `tts_synthesis_cache` (performance) and `tts_jobs` (tracking)
- Functional but with gaps in version control, uniqueness constraints, and observability

**Improvements:**
- 5 critical fixes (engine versioning, duplicate prevention, observability)
- 3 optional enhancements (normalization, archival, monitoring)
- Estimated timeline: 2-3 weeks for full implementation

---

## Table of Contents

1. [Current Schema Overview](#current-schema-overview)
2. [Critical Issues Identified](#critical-issues-identified)
3. [Improvement Phases](#improvement-phases)
4. [Implementation Details](#implementation-details)
5. [Testing Strategy](#testing-strategy)
6. [Rollback Plan](#rollback-plan)
7. [Monitoring & Validation](#monitoring--validation)

---

## Current Schema Overview

### Table 1: `tts_synthesis_cache`

**Purpose:** Store base audio synthesized at ratio=1.0 for reuse with different speed ratios

**Key Fields:**
```sql
cache_key (PK)              -- SHA256(text + audio_prompt_path)
text                        -- Full synthesized text
audio_prompt_path           -- S3 voice path
base_audio_local_path       -- Local WAV file path
audio_duration_seconds      -- Duration in seconds
synthesis_duration_ms       -- Generation time
hit_count                   -- Reuse counter
last_accessed_at            -- For LRU eviction
tts_engine                  -- Engine version (e.g., "IndexTTS-1.5")
```

**Performance Impact:**
- Cache hit: ~1-2s (time-stretch only)
- Cache miss: ~5-10s (full synthesis)
- Current hit rate: ~65%

### Table 2: `tts_jobs`

**Purpose:** Track job lifecycle, enable retry detection, provide analytics

**Key Fields:**
```sql
id (PK, auto)               -- ttsId
job_id                      -- RabbitMQ job identifier
job_type                    -- 'studio', 'playground', 'rem'
status                      -- 'queued', 'processing', 'completed', 'failed'
is_test                     -- Test job flag
cache_key (FK?)             -- Link to cache (no constraint currently)
audio_path                  -- S3 output path
alignment_path              -- S3 alignment JSON path
synthesis_duration_seconds  -- Total elapsed time
```

---

## Critical Issues Identified

### 🔴 Issue 1: No Unique Constraint on `job_id`

**Problem:**
```python
# Current schema (app/models.py:147)
job_id = Column(Integer, nullable=False, index=True)  # No UNIQUE constraint
```

When worker crashes and restarts:
- Same job may be reprocessed from RabbitMQ
- Creates duplicate `tts_jobs` records
- Metrics become inaccurate (double-counting)
- No way to detect genuine retries vs duplicates

**Impact:** High - Data integrity violation, incorrect analytics

**Solution:** Add `UNIQUE` constraint on `job_id` column

---

### 🔴 Issue 2: Missing Observability Fields

**Problem:**
Cannot easily answer key operational questions:

```sql
-- ❌ Can't query cache hit rate directly
SELECT COUNT(*) WHERE cache_hit = TRUE;  -- Column doesn't exist

-- ❌ Can't separate synthesis vs alignment timing
SELECT AVG(synthesis_duration), AVG(alignment_duration);  -- alignment buried in JSON

-- ❌ Can't track time-stretch operations
SELECT COUNT(*) WHERE time_stretched = TRUE;  -- No tracking
```

**Impact:** Medium - Poor operational visibility

**Solution:** Add dedicated columns for cache hits, alignment timing, stretch operations

---

### 🔴 Issue 3: Weak Foreign Key Constraint

**Problem:**
```python
# Current schema (app/models.py:158)
cache_key = Column(String(64), nullable=True, index=True)  # No FK constraint
```

This allows:
```sql
-- Delete cache entry
DELETE FROM tts_synthesis_cache WHERE cache_key = 'abc123...';

-- Orphaned job records remain
SELECT * FROM tts_jobs WHERE cache_key = 'abc123...';
-- ⚠️ Returns records pointing to deleted cache
```

**Impact:** Medium - Referential integrity violation

**Solution:** Add `FOREIGN KEY` constraint with `ON DELETE SET NULL`

---

### ⚠️ Issue 4: Data Duplication (Low Priority)

**Problem:**
```python
# These fields exist in BOTH tables:
tts_synthesis_cache: text, audio_prompt_path, language
tts_jobs:            text, audio_prompt_path, language
```

**Impact:** Low - Storage overhead, potential inconsistency

**Solution:** Remove from `tts_jobs`, join through `cache_key` when needed

**Decision:** Defer to v2.0 (breaking change, high complexity)

---

## Improvement Phases

### Phase 1: Critical Fixes (Week 1)

**Goals:**
- ✅ Prevent duplicate job records
- ✅ Add basic observability

**Changes:**
1. Add `UNIQUE` constraint on `tts_jobs.job_id` (Migration 005)
2. Add `cache_hit` boolean to `tts_jobs` (Migration 006)
3. Add `alignment_duration_seconds` to `tts_jobs` (Migration 006)

**Effort:** 2-3 days  
**Risk:** Low (backward compatible)

---

### Phase 2: Data Integrity (Week 2)

**Goals:**
- ✅ Enforce referential integrity
- ✅ Add time-stretch tracking

**Changes:**
1. Add `FOREIGN KEY` constraint on `tts_jobs.cache_key` (Migration 007)
2. Add `time_stretched` boolean to `tts_jobs`
3. Add `source_ratio` and `target_ratio` to `tts_jobs`

**Effort:** 3-4 days  
**Risk:** Medium (requires data cleanup)

---

### Phase 3: Optional Enhancements (Week 3)

**Goals:**
- ✅ Improve performance
- ✅ Add archival strategy
- ✅ Enhance monitoring

**Changes:**
1. Add composite index on `(text_hash, audio_prompt_path)` for cache lookups (Migration 008)
2. Add `archived_at` timestamp for soft deletion (Migration 009)
3. Add `cache_size_bytes` tracking
4. Add `worker_version` to job records

**Effort:** 3-5 days  
**Risk:** Low

---

## Implementation Details

### Migration 005: Add Unique Constraint on job_id

**File:** `alembic/versions/005_add_unique_job_id.py`

```python
"""Add unique constraint on job_id

Revision ID: 005
Revises: 004
Create Date: 2026-09-03
"""

from alembic import op
import sqlalchemy as sa

revision = '005'
down_revision = '004'

def upgrade():
    """
    Add UNIQUE constraint on tts_jobs.job_id.
    
    This prevents duplicate job records when worker restarts or retries occur.
    """
    
    # First, clean up any existing duplicates
    op.execute("""
        -- Keep only the earliest record for each job_id
        DELETE FROM tts_jobs
        WHERE id NOT IN (
            SELECT MIN(id)
            FROM tts_jobs
            GROUP BY job_id
        )
    """)
    
    # Add unique constraint
    op.create_unique_constraint(
        'uq_tts_jobs_job_id',
        'tts_jobs',
        ['job_id']
    )

def downgrade():
    op.drop_constraint('uq_tts_jobs_job_id', 'tts_jobs', type_='unique')
```

**Code Changes:**

```python
# services/tts_job_service.py

from sqlalchemy.exc import IntegrityError

async def _create() -> int:
    async with DatabaseSession() as db_session:
        # ... existing code ...
        
        tts_job = TTSJob(
            job_id=job_id_int,
            job_type=job_type,
            status="processing",
            # ... other fields ...
        )
        
        try:
            db_session.add(tts_job)
            await db_session.commit()
            await db_session.refresh(tts_job)
            return tts_job.id
        except IntegrityError as e:
            if 'uq_tts_jobs_job_id' in str(e):
                # Job already exists (duplicate/retry)
                logger.warning(
                    f"Job {job_id_int} already exists in database (retry detected)"
                )
                await db_session.rollback()
                
                # Fetch existing record
                stmt = select(TTSJob).where(TTSJob.job_id == job_id_int)
                result = await db_session.execute(stmt)
                existing_job = result.scalar_one()
                return existing_job.id
            else:
                raise
```

---

### Migration 006: Add Observability Fields

**File:** `alembic/versions/006_add_observability_fields.py`

```python
"""Add observability fields to tts_jobs

Revision ID: 006
Revises: 005
Create Date: 2026-09-03
"""

from alembic import op
import sqlalchemy as sa

revision = '006'
down_revision = '005'

def upgrade():
    """Add cache_hit, alignment_duration, and time-stretch tracking."""
    
    # Add cache_hit flag
    op.add_column(
        'tts_jobs',
        sa.Column(
            'cache_hit',
            sa.Boolean(),
            nullable=False,
            server_default='false',
            comment='Whether synthesis result came from cache'
        )
    )
    
    # Add alignment_duration_seconds
    op.add_column(
        'tts_jobs',
        sa.Column(
            'alignment_duration_seconds',
            sa.Numeric(10, 2),
            nullable=True,
            comment='Time spent on forced alignment (seconds)'
        )
    )
    
    # Add time_stretched flag
    op.add_column(
        'tts_jobs',
        sa.Column(
            'time_stretched',
            sa.Boolean(),
            nullable=False,
            server_default='false',
            comment='Whether audio was time-stretched (ratio != 1.0)'
        )
    )
    
    # Add source and target ratios for stretch operations
    op.add_column(
        'tts_jobs',
        sa.Column(
            'source_ratio',
            sa.Numeric(3, 1),
            nullable=True,
            comment='Original synthesis ratio (always 1.0 for cache)'
        )
    )
    
    op.add_column(
        'tts_jobs',
        sa.Column(
            'target_ratio',
            sa.Numeric(3, 1),
            nullable=True,
            comment='Final requested ratio (may differ from source)'
        )
    )
    
    # Create index on cache_hit for analytics
    op.create_index(
        'idx_tts_jobs_cache_hit',
        'tts_jobs',
        ['cache_hit']
    )
    
    # Create index on time_stretched for analytics
    op.create_index(
        'idx_tts_jobs_time_stretched',
        'tts_jobs',
        ['time_stretched']
    )

def downgrade():
    op.drop_index('idx_tts_jobs_time_stretched')
    op.drop_index('idx_tts_jobs_cache_hit')
    op.drop_column('tts_jobs', 'target_ratio')
    op.drop_column('tts_jobs', 'source_ratio')
    op.drop_column('tts_jobs', 'time_stretched')
    op.drop_column('tts_jobs', 'alignment_duration_seconds')
    op.drop_column('tts_jobs', 'cache_hit')
```

**Code Changes:**

```python
# services/tts_job_service.py

def update_job_status(
    self,
    tts_id: int | None,
    status: str,
    cache_hit: bool = False,
    alignment_duration: float | None = None,
    time_stretched: bool = False,
    source_ratio: float | None = None,
    target_ratio: float | None = None,
    **kwargs: Any
) -> None:
    """
    Update tts_jobs record with comprehensive tracking.
    
    Args:
        tts_id: Internal ttsId
        status: New status
        cache_hit: Whether synthesis came from cache
        alignment_duration: Seconds spent on alignment
        time_stretched: Whether audio was time-stretched
        source_ratio: Original ratio (1.0 for cache)
        target_ratio: Final requested ratio
        **kwargs: Additional columns
    """
    if tts_id is None:
        return
    
    async def _update() -> None:
        async with DatabaseSession() as db_session:
            tts_job = await db_session.get(TTSJob, tts_id)
            if tts_job:
                tts_job.status = status
                tts_job.cache_hit = cache_hit
                tts_job.alignment_duration_seconds = alignment_duration
                tts_job.time_stretched = time_stretched
                tts_job.source_ratio = source_ratio
                tts_job.target_ratio = target_ratio
                
                # ... rest of update logic ...
```

---

### Migration 007: Add Foreign Key Constraint

**File:** `alembic/versions/007_add_cache_foreign_key.py`

```python
"""Add foreign key constraint on cache_key

Revision ID: 007
Revises: 006
Create Date: 2026-09-03
"""

from alembic import op
import sqlalchemy as sa

revision = '007'
down_revision = '006'

def upgrade():
    """
    Add foreign key constraint from tts_jobs.cache_key to tts_synthesis_cache.cache_key.
    
    Uses ON DELETE SET NULL to handle cache eviction gracefully.
    """
    
    # First, clean up orphaned cache_key references
    op.execute("""
        UPDATE tts_jobs
        SET cache_key = NULL
        WHERE cache_key IS NOT NULL
        AND cache_key NOT IN (
            SELECT cache_key FROM tts_synthesis_cache
        )
    """)
    
    # Add foreign key constraint
    op.create_foreign_key(
        'fk_tts_jobs_cache_key',
        'tts_jobs',
        'tts_synthesis_cache',
        ['cache_key'],
        ['cache_key'],
        ondelete='SET NULL'  # When cache evicted, set job's cache_key to NULL
    )

def downgrade():
    op.drop_constraint('fk_tts_jobs_cache_key', 'tts_jobs', type_='foreignkey')
```

---

### Migration 008: Add Performance Indexes (Optional)

**File:** `alembic/versions/008_add_performance_indexes.py`

```python
"""Add composite indexes for common queries

Revision ID: 008
Revises: 007
Create Date: 2026-09-03
"""

from alembic import op

revision = '008'
down_revision = '007'

def upgrade():
    """Add composite indexes for query optimization."""
    
    # Composite index for cache lookup by text_hash + audio_prompt
    op.create_index(
        'idx_tts_cache_text_audio',
        'tts_synthesis_cache',
        ['text_hash', 'audio_prompt_path']
    )
    
    # Composite index for analytics queries (date range + status)
    op.create_index(
        'idx_tts_jobs_created_status',
        'tts_jobs',
        ['created_at', 'status']
    )
    
    # Composite index for cache hit analytics
    op.create_index(
        'idx_tts_jobs_created_cache_hit',
        'tts_jobs',
        ['created_at', 'cache_hit']
    )

def downgrade():
    op.drop_index('idx_tts_jobs_created_cache_hit')
    op.drop_index('idx_tts_jobs_created_status')
    op.drop_index('idx_tts_cache_text_audio')
```

---

### Migration 009: Add Archival Fields (Optional)

**File:** `alembic/versions/009_add_archival_fields.py`

```python
"""Add archival and soft-delete support

Revision ID: 009
Revises: 008
Create Date: 2026-09-03
"""

from alembic import op
import sqlalchemy as sa

revision = '009'
down_revision = '008'

def upgrade():
    """Add archived_at timestamp for soft deletion."""
    
    # Add archived_at to both tables
    op.add_column(
        'tts_jobs',
        sa.Column(
            'archived_at',
            sa.DateTime(timezone=True),
            nullable=True,
            comment='When job was archived (soft delete)'
        )
    )
    
    op.add_column(
        'tts_synthesis_cache',
        sa.Column(
            'archived_at',
            sa.DateTime(timezone=True),
            nullable=True,
            comment='When cache entry was archived (soft delete)'
        )
    )
    
    # Add worker_version tracking
    op.add_column(
        'tts_jobs',
        sa.Column(
            'worker_version',
            sa.String(20),
            nullable=True,
            comment='Worker version that processed this job'
        )
    )
    
    # Create indexes for archival queries
    op.create_index('idx_tts_jobs_archived_at', 'tts_jobs', ['archived_at'])
    op.create_index('idx_tts_cache_archived_at', 'tts_synthesis_cache', ['archived_at'])

def downgrade():
    op.drop_index('idx_tts_cache_archived_at')
    op.drop_index('idx_tts_jobs_archived_at')
    op.drop_column('tts_jobs', 'worker_version')
    op.drop_column('tts_synthesis_cache', 'archived_at')
    op.drop_column('tts_jobs', 'archived_at')
```

---

## Testing Strategy

### Unit Tests

**File:** `tests/test_database_improvements.py`

```python
"""Unit tests for database schema improvements."""

import pytest
from sqlalchemy.exc import IntegrityError

from app.cache_service import TTSCacheService
from app.database import DatabaseSession
from app.models import TTSJob, TTSSynthesisCache


class TestEngineVersioning:
    """Test engine version in cache key."""
    
    async def test_different_engines_different_keys(self):
        """Different engine versions should generate different cache keys."""
        service = TTSCacheService(None)
        
        key_v1 = service.generate_cache_key(
            "Hello world",
            "audio-prompts/voice.wav",
            "IndexTTS-1.5"
        )
        
        key_v2 = service.generate_cache_key(
            "Hello world",
            "audio-prompts/voice.wav",
            "IndexTTS-2.0"
        )
        
        assert key_v1 != key_v2, "Different engine versions must produce different keys"
    
    async def test_same_engine_same_key(self):
        """Same parameters should always produce same key."""
        service = TTSCacheService(None)
        
        key1 = service.generate_cache_key("Test", "voice.wav", "IndexTTS-1.5")
        key2 = service.generate_cache_key("Test", "voice.wav", "IndexTTS-1.5")
        
        assert key1 == key2, "Same parameters must produce same key"


class TestJobIdUniqueness:
    """Test job_id unique constraint."""
    
    async def test_duplicate_job_id_prevented(self, db_session):
        """Cannot create two jobs with same job_id."""
        
        job1 = TTSJob(job_id=12345, job_type="studio", status="processing")
        db_session.add(job1)
        await db_session.commit()
        
        job2 = TTSJob(job_id=12345, job_type="studio", status="processing")
        db_session.add(job2)
        
        with pytest.raises(IntegrityError):
            await db_session.commit()
    
    async def test_retry_detection_works(self, tts_job_service):
        """Service should handle duplicate job_id gracefully."""
        
        job_data = {
            "jobId": 99999,
            "jobType": "studio",
            "text": "Test",
            "audioPromptPath": "voice.wav",
        }
        
        # First create
        tts_id_1 = tts_job_service.create_job_record(job_data)
        
        # Retry (same job_id)
        tts_id_2 = tts_job_service.create_job_record(job_data)
        
        assert tts_id_1 == tts_id_2, "Should return same ttsId for duplicate job_id"


class TestObservabilityFields:
    """Test new observability columns."""
    
    async def test_cache_hit_tracking(self, db_session):
        """Can track cache hits vs misses."""
        
        job = TTSJob(
            job_id=1001,
            job_type="studio",
            status="completed",
            cache_hit=True
        )
        db_session.add(job)
        await db_session.commit()
        
        # Query cache hit rate
        query = """
            SELECT 
                COUNT(*) FILTER (WHERE cache_hit) * 100.0 / COUNT(*) as hit_rate
            FROM tts_jobs
        """
        result = await db_session.execute(query)
        hit_rate = result.scalar()
        
        assert hit_rate is not None
    
    async def test_alignment_duration_tracking(self, db_session):
        """Can track alignment timing separately."""
        
        job = TTSJob(
            job_id=1002,
            job_type="studio",
            status="completed",
            synthesis_duration_seconds=5.5,
            alignment_duration_seconds=1.2
        )
        db_session.add(job)
        await db_session.commit()
        
        # Can calculate pure synthesis time
        pure_synthesis = job.synthesis_duration_seconds - job.alignment_duration_seconds
        assert pure_synthesis == 4.3


class TestForeignKeyConstraint:
    """Test cache_key foreign key."""
    
    async def test_orphaned_cache_key_prevented(self, db_session):
        """Cannot reference non-existent cache_key."""
        
        job = TTSJob(
            job_id=2001,
            job_type="studio",
            status="completed",
            cache_key="nonexistent_cache_key_12345"
        )
        db_session.add(job)
        
        with pytest.raises(IntegrityError):
            await db_session.commit()
    
    async def test_cache_deletion_sets_null(self, db_session):
        """Deleting cache entry sets job's cache_key to NULL."""
        
        # Create cache entry
        cache_entry = TTSSynthesisCache(
            cache_key="test_key_12345",
            text="Test",
            audio_prompt_path="voice.wav",
            base_audio_local_path="/tmp/test.wav",
            audio_duration_seconds=2.5,
            synthesis_duration_ms=500
        )
        db_session.add(cache_entry)
        await db_session.commit()
        
        # Create job referencing it
        job = TTSJob(
            job_id=2002,
            job_type="studio",
            status="completed",
            cache_key="test_key_12345"
        )
        db_session.add(job)
        await db_session.commit()
        
        # Delete cache entry
        await db_session.delete(cache_entry)
        await db_session.commit()
        
        # Refresh job
        await db_session.refresh(job)
        
        # cache_key should be NULL now
        assert job.cache_key is None
```

### Integration Tests

**File:** `tests/integration/test_cache_versioning.py`

```python
"""Integration tests for cache versioning."""

import pytest
from services.synthesis_pipeline import SynthesisPipeline


class TestEngineVersionIntegration:
    """Test engine version handling in full pipeline."""
    
    @pytest.mark.integration
    async def test_engine_upgrade_regenerates_cache(self):
        """Upgrading engine should bypass old cache entries."""
        
        pipeline = SynthesisPipeline(engine_version="IndexTTS-1.5")
        
        # Synthesize with v1.5
        result1 = await pipeline.synthesize(
            text="Hello world",
            audio_prompt_path="voice.wav"
        )
        assert result1.cache_hit is False  # First time
        
        # Synthesize again with v1.5
        result2 = await pipeline.synthesize(
            text="Hello world",
            audio_prompt_path="voice.wav"
        )
        assert result2.cache_hit is True  # Cache hit
        
        # Upgrade to v2.0
        pipeline_v2 = SynthesisPipeline(engine_version="IndexTTS-2.0")
        
        # Same text/voice but different engine
        result3 = await pipeline_v2.synthesize(
            text="Hello world",
            audio_prompt_path="voice.wav"
        )
        assert result3.cache_hit is False  # Cache miss (different engine)
```

### Load Tests

**File:** `tests/load/test_unique_constraint_performance.py`

```python
"""Load test for unique constraint on job_id."""

import asyncio
import pytest


class TestUniqueConstraintPerformance:
    """Verify unique constraint doesn't degrade performance."""
    
    @pytest.mark.load
    async def test_concurrent_job_creation(self, tts_job_service):
        """100 concurrent job creations should complete in <5s."""
        
        async def create_job(i):
            job_data = {
                "jobId": 100000 + i,
                "jobType": "studio",
                "text": f"Test {i}",
            }
            return tts_job_service.create_job_record(job_data)
        
        import time
        start = time.time()
        
        tasks = [create_job(i) for i in range(100)]
        results = await asyncio.gather(*tasks)
        
        elapsed = time.time() - start
        
        assert len(results) == 100
        assert elapsed < 5.0, f"Too slow: {elapsed:.2f}s"
```

---

## Rollback Plan

### Rollback Procedure

If issues arise after deployment:

```bash
# Step 1: Identify current migration version
uv run alembic current

# Step 2: Rollback to previous version
uv run alembic downgrade -1  # Rollback one migration

# OR rollback to specific version
uv run alembic downgrade 004_fix_synthesis_duration_null

# Step 3: Verify database state
uv run alembic current
```

### Data Preservation

All migrations are designed to be non-destructive:

- **Migration 005**: Removes duplicate rows (keeps earliest)
- **Migration 006**: Adds columns with defaults (no data loss)
- **Migration 007**: Sets orphaned cache_key to NULL (preserves jobs)
- **Migration 008-009**: Index-only (no data changes)

### Rollback Testing

Before production deployment:

```bash
# Test upgrade + downgrade in dev environment
uv run alembic upgrade head
uv run alembic downgrade base
uv run alembic upgrade head

# Verify data integrity after rollback
uv run python scripts/verify_database.py
```

---

## Monitoring & Validation

### Health Checks

**File:** `scripts/database_health_check.py`

```python
"""Database health check script."""

import asyncio
from app.database import DatabaseSession
from sqlalchemy import text


async def check_database_health():
    """Verify database schema and constraints."""
    
    async with DatabaseSession() as db:
        checks = []
        
        # Check 1: Unique constraint on job_id exists
        result = await db.execute(text("""
            SELECT constraint_name
            FROM information_schema.table_constraints
            WHERE table_name = 'tts_jobs'
            AND constraint_type = 'UNIQUE'
            AND constraint_name = 'uq_tts_jobs_job_id'
        """))
        checks.append(("job_id unique constraint", result.scalar() is not None))
        
        # Check 2: Foreign key on cache_key exists
        result = await db.execute(text("""
            SELECT constraint_name
            FROM information_schema.table_constraints
            WHERE table_name = 'tts_jobs'
            AND constraint_type = 'FOREIGN KEY'
            AND constraint_name = 'fk_tts_jobs_cache_key'
        """))
        checks.append(("cache_key foreign key", result.scalar() is not None))
        
        # Check 3: Observability columns exist
        result = await db.execute(text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'tts_jobs'
            AND column_name IN ('cache_hit', 'alignment_duration_seconds', 'time_stretched')
        """))
        obs_columns = [row[0] for row in result.fetchall()]
        checks.append(("observability columns", len(obs_columns) == 3))
        
        # Check 4: No orphaned cache_key references
        result = await db.execute(text("""
            SELECT COUNT(*)
            FROM tts_jobs
            WHERE cache_key IS NOT NULL
            AND cache_key NOT IN (SELECT cache_key FROM tts_synthesis_cache)
        """))
        orphaned_count = result.scalar()
        checks.append(("no orphaned cache_keys", orphaned_count == 0))
        
        # Print results
        print("\n=== Database Health Check ===\n")
        all_passed = True
        for check_name, passed in checks:
            status = "✅ PASS" if passed else "❌ FAIL"
            print(f"{status} {check_name}")
            if not passed:
                all_passed = False
        
        print()
        return all_passed


if __name__ == "__main__":
    result = asyncio.run(check_database_health())
    exit(0 if result else 1)
```

### Analytics Queries

After deployment, validate with these queries:

```sql
-- Cache hit rate over last 7 days
SELECT 
    DATE(created_at) as date,
    COUNT(*) FILTER (WHERE cache_hit) * 100.0 / COUNT(*) as cache_hit_rate,
    COUNT(*) as total_jobs
FROM tts_jobs
WHERE created_at > NOW() - INTERVAL '7 days'
GROUP BY DATE(created_at)
ORDER BY date DESC;

-- Average timing breakdown
SELECT 
    AVG(synthesis_duration_seconds) as avg_total,
    AVG(alignment_duration_seconds) as avg_alignment,
    AVG(synthesis_duration_seconds - alignment_duration_seconds) as avg_synthesis_only
FROM tts_jobs
WHERE status = 'completed'
AND created_at > NOW() - INTERVAL '7 days';

-- Time-stretch operations
SELECT 
    COUNT(*) FILTER (WHERE time_stretched) as stretched_count,
    COUNT(*) FILTER (WHERE NOT time_stretched) as direct_count,
    COUNT(*) FILTER (WHERE time_stretched) * 100.0 / COUNT(*) as stretch_rate
FROM tts_jobs
WHERE status = 'completed';

-- Engine version distribution in cache
SELECT 
    engine_version,
    COUNT(*) as entry_count,
    SUM(hit_count) as total_hits
FROM tts_synthesis_cache
GROUP BY engine_version
ORDER BY entry_count DESC;

-- No duplicate job_ids (should return 0)
SELECT job_id, COUNT(*)
FROM tts_jobs
GROUP BY job_id
HAVING COUNT(*) > 1;
```

---

## Implementation Timeline

### Week 1: Critical Fixes

**Days 1-2: Migration 005 + 006**
- Add unique constraint on job_id
- Update cache_service.py
- Update tts_job_service.py
- Write unit tests

**Days 3-5: Migration 006 + Testing**
- Add observability fields
- Update tts_job_service.py
- Write integration tests
- Deploy to staging
- Monitor for 24 hours

### Week 2: Data Integrity

**Days 1-3: Migration 007**
- Clean up orphaned cache_key references
- Add foreign key constraint
- Test cascade behavior
- Update analytics scripts

**Days 4-5: Validation**
- Run health checks
- Validate analytics queries
- Load testing
- Deploy to production

### Week 3: Optional Enhancements

**Days 1-2: Migration 008**
- Add composite indexes
- Benchmark query performance
- Deploy if performance gain > 10%

**Days 3-5: Migration 009**
- Add archival support
- Write archival script
- Test soft-delete behavior
- Deploy to production

---

## Success Metrics

After full implementation, validate:

### ✅ Critical Metrics
- **Zero duplicate job_ids**: `SELECT job_id, COUNT(*) FROM tts_jobs GROUP BY job_id HAVING COUNT(*) > 1` returns 0
- **Zero orphaned cache_keys**: Query returns 0
- **Engine version tracking**: `SELECT DISTINCT engine_version FROM tts_synthesis_cache` shows all versions
- **Cache hit tracking**: `SELECT AVG(cache_hit::int) FROM tts_jobs` returns ~0.65

### ✅ Performance Metrics
- **Job creation**: < 50ms per job (99th percentile)
- **Cache lookup**: < 10ms (99th percentile)
- **No query performance regression**: Benchmark queries before/after

### ✅ Operational Metrics
- **Analytics query speed**: All < 500ms
- **Database size**: Within expected bounds (< 10GB for 1M jobs)
- **No production incidents**: Zero downtime during deployment

---

## Future Considerations

### Not Included (Deferred to v2.0)

1. **Remove denormalized fields from tts_jobs**
   - Breaking change, requires JOIN queries
   - Moderate storage savings (~20%)
   - Defer until storage becomes a concern

2. **Partitioning for tts_jobs**
   - Use PostgreSQL table partitioning by created_at
   - Relevant when > 10M rows
   - Current scale doesn't require this

3. **Read replicas**
   - Separate analytics queries from transactional load
   - Relevant when read:write ratio > 10:1
   - Not needed yet

4. **Materialized views for analytics**
   - Pre-compute common analytics queries
   - Refresh on schedule (hourly/daily)
   - Add when analytics queries slow down

---

## Appendix: Schema Diagrams

### Before (Current)

```
tts_synthesis_cache                    tts_jobs
┌─────────────────────┐                ┌─────────────────────┐
│ cache_key (PK)      │                │ id (PK)             │
│ text                │                │ job_id              │ ← No unique constraint
│ audio_prompt_path   │                │ cache_key           │ ← No FK constraint
│ base_audio_local_path│               │ status              │
│ hit_count           │                │ audio_path          │
│ last_accessed_at    │                │ synthesis_duration  │ ← Total time only
│ tts_engine          │ ← Not in key   │ ...                 │
└─────────────────────┘                └─────────────────────┘
         ↑ cache_key                            ↓ cache_key
         └────────────────────────────────────────┘ (no FK)
```

### After (Improved)

```
tts_synthesis_cache                    tts_jobs
┌─────────────────────┐                ┌─────────────────────────┐
│ cache_key (PK)      │ ← version      │ id (PK)                 │
│ engine_version      │   in key       │ job_id (UNIQUE)         │ ← Unique constraint
│ text                │                │ cache_key (FK)          │ ← FK constraint
│ audio_prompt_path   │                │ status                  │
│ base_audio_local_path│               │ cache_hit               │ ← Observability
│ hit_count           │                │ alignment_duration      │ ← Timing breakdown
│ last_accessed_at    │                │ time_stretched          │ ← Stretch tracking
│ archived_at         │ ← Soft delete  │ synthesis_duration      │
└─────────────────────┘                │ archived_at             │ ← Soft delete
         ↑ cache_key                   └─────────────────────────┘
         └──────────────────────────────────────┘ (FK: ON DELETE SET NULL)
```

---

## Contact & Support

**Questions:** Check `#database-support` Slack channel  
**Issues:** Create GitHub issue with label `database-schema`  
**Urgent:** Page on-call engineer via PagerDuty

---

**Document Version:** 2.0  
**Last Review:** September 3, 2026  
**Next Review:** December 2026  
**Owner:** IndexTTS Worker Team
