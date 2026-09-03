# Alembic Database Migration Guide

## Quick Start

### Installation
```bash
uv sync  # Installs alembic>=1.13.0 and dependencies
```

### Verify Setup
```bash
uv run alembic --version  # Check installation
uv run alembic current    # Check current database revision
```

## Revision ID Convention

Every revision **must be ≤ 32 characters** and follow: **`YYYYMMDD_short_slug`**

| Part | Rule | Example |
|---|---|---|
| `YYYYMMDD` | UTC date of migration (8 digits) | `20260903` |
| `_` | Single underscore separator | `_` |
| `short_slug` | Lowercase `[a-z0-9_]`, descriptive (≤23 chars) | `create_tts_jobs` |

**Regex:** `^[0-9]{8}_[a-z0-9_]+$`

Current migration chain:
```
002_create_tts_jobs (head)
  ← 001_create_tts_synthesis_cache
```

## Creating Migrations

### Generate Migration (Auto-detect from ORM models)

After modifying models in `app/models.py`:

```bash
uv run alembic revision --autogenerate \
  --rev-id YYYYMMDD_short_slug \
  -m "Description of change"
```

Example:
```bash
uv run alembic revision --autogenerate \
  --rev-id 20260903_add_alignment_field \
  -m "Add alignment_status field to tts_jobs"
```

### Review Generated Migration

```bash
cat alembic/versions/YYYYMMDD_short_slug.py
```

Verify:
- Revision IDs are ≤ 32 chars and match format
- Operations match your ORM changes
- All constraints and indexes included

### Apply Migration

```bash
uv run alembic upgrade head
```

### Rollback

```bash
uv run alembic downgrade -1          # Rollback 1 step
uv run alembic downgrade <revision>  # Rollback to specific revision
```

## Status & History

```bash
uv run alembic current   # Show current revision
uv run alembic history   # Show migration chain
```

## Migration File Template

```python
"""Add column to tts_jobs table

Revision ID: 20260903_add_alignment_field
Revises: 002_create_tts_jobs
Create Date: 2026-09-03 14:30:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = '20260903_add_alignment_field'
down_revision = '002_create_tts_jobs'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'tts_jobs',
        sa.Column('alignment_status', sa.String(20), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('tts_jobs', 'alignment_status')
```

**Always include both `upgrade()` and `downgrade()`.**

## Testing Locally

```bash
# Check current revision
uv run alembic current

# Apply all pending migrations
uv run alembic upgrade head

# Test rollback
uv run alembic downgrade -1
uv run alembic upgrade head
```

## Common Issues

| Issue | Solution |
|-------|----------|
| "No changes detected" | Ensure model added to `app/models.py` |
| "Can't find migration environment" | Run from project root (where `alembic.ini` is) |
| Circular import warnings | Already fixed—imports are lazy-loaded |

## Configuration

- **Config file**: `alembic.ini`
- **Environment**: `alembic/env.py` (handles async PostgreSQL)
- **Versions**: `alembic/versions/`
- **Database URL**: Reads from `DATABASE_URL` in `.env`

## Best Practices

✅ **DO**
- Always specify `--rev-id YYYYMMDD_short_slug` (never use auto-generated hex)
- Keep revision IDs ≤ 32 characters
- Review auto-generated migrations before applying
- Include both `upgrade()` and `downgrade()`
- Test locally first
- Commit migration files to version control

❌ **DON'T**
- Use hex hashes or UUIDs for revision IDs
- Skip `downgrade()` function
- Modify migrations after deployment
- Delete migration files

## References

- [Alembic Documentation](https://alembic.sqlalchemy.org/)
- Project guide: `AGENTS.md`
