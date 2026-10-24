"""
Database connection and session management for TTS synthesis cache and jobs.

This module provides both async (asyncpg) and sync (psycopg2) database connections
using SQLAlchemy. The async engine is used by FastAPI / alembic; the sync engine is
used by the worker's CacheManager to avoid thread+event-loop overhead per DB call.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import AsyncGenerator, Generator

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker,
    AsyncEngine,
)
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import NullPool

# Load environment variables
load_dotenv()

# Lazy logger initialization to avoid circular imports during alembic initialization
_logger = None


def _get_logger():
    """Get or initialize logger (lazy init to avoid circular imports)."""
    global _logger
    if _logger is None:
        from services.common.logging_config import get_logger

        _logger = get_logger(__name__)
    return _logger


def async_to_sync_database_url(database_url: str) -> str:
    """Convert async SQLAlchemy URL to sync (e.g. postgresql+asyncpg -> postgresql).

    IMPORTANT: Must rebuild the URL string directly without using str() on the URL object,
    since SQLAlchemy's str() method hides the password with ***, which then becomes
    the literal password when parsed again.
    """
    from sqlalchemy.engine import make_url

    parsed = make_url(database_url)
    if "+asyncpg" not in parsed.drivername:
        return database_url

    # Build URL string directly from components to preserve password
    new_driver = parsed.drivername.replace("+asyncpg", "", 1)

    # Reconstruct URL string: drivername://user:password@host:port/database?query
    user_part = (
        f"{parsed.username}:{parsed.password}@"
        if parsed.password
        else f"{parsed.username}@"
    )
    port_part = f":{parsed.port}" if parsed.port else ""
    query_part = f"?{parsed.query}" if parsed.query else ""

    return f"{new_driver}://{user_part}{parsed.host}{port_part}/{parsed.database}{query_part}"


def get_sync_database_url() -> str | None:
    """
    Sync database URL for Alembic and other sync tools.

    Derived from DATABASE_URL by stripping the async driver (+asyncpg).
    """
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        return None

    return async_to_sync_database_url(database_url)


# Database configuration from environment
DATABASE_URL = os.getenv("DATABASE_URL")

# Create async engine
engine: AsyncEngine | None = None

if DATABASE_URL:
    engine = create_async_engine(
        DATABASE_URL,
        echo=False,
        poolclass=NullPool,
        pool_pre_ping=True,
        pool_recycle=3600,
    )
    _get_logger().info("Database engine initialized successfully (NullPool mode)")
else:
    _get_logger().warning("Database engine not initialized - cache disabled")

# ---------------------------------------------------------------------------
# Synchronous engine (psycopg2) — used by CacheManager to avoid per-call
# thread+asyncio overhead.  Derived from DATABASE_URL by stripping +asyncpg.
# ---------------------------------------------------------------------------
_sync_engine = None
SyncSessionLocal: sessionmaker | None = None

if DATABASE_URL:
    try:
        _sync_url = async_to_sync_database_url(DATABASE_URL)
        _sync_engine = create_engine(
            _sync_url,
            pool_pre_ping=True,
            pool_recycle=3600,
            pool_size=2,
            max_overflow=2,
        )
        SyncSessionLocal = sessionmaker(
            bind=_sync_engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )
    except Exception as _e:
        _get_logger().warning(f"Sync DB engine not initialized: {_e}")


@contextmanager
def SyncDatabaseSession() -> Generator[Session, None, None]:
    """
    Context manager for synchronous database sessions (psycopg2 / cache worker).

    Usage::

        with SyncDatabaseSession() as session:
            result = session.execute(...)
    """
    if SyncSessionLocal is None:
        raise RuntimeError("Sync database not initialized — check DATABASE_URL in .env")
    session: Session = SyncSessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# Session factory
AsyncSessionLocal: async_sessionmaker[AsyncSession] | None = None

if engine:
    AsyncSessionLocal = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for getting async database session."""
    if not AsyncSessionLocal:
        raise RuntimeError("Database not initialized - check DATABASE_URL in .env")

    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception as e:
            await session.rollback()
            _get_logger().error(f"Database session error: {e}")
            raise
        finally:
            await session.close()


async def init_db():
    """Initialize database (create tables if they don't exist)."""
    if not engine:
        raise RuntimeError(
            "Database engine not initialized - check DATABASE_URL in .env"
        )

    from services.db.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    _get_logger().success("Database tables created successfully")


async def close_db():
    """Close database connection pool."""
    if engine:
        await engine.dispose()
        _get_logger().info("Database connection pool closed")


async def check_db_connection() -> bool:
    """Check if database connection is working."""
    if not engine:
        _get_logger().error("Database engine not initialized")
        return False

    try:
        from sqlalchemy import text

        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        _get_logger().info("Database connection check: OK")
        return True
    except Exception as e:
        _get_logger().error(f"Database connection check failed: {e}")
        return False


class DatabaseSession:
    """Context manager for database sessions in non-FastAPI contexts."""

    def __init__(self):
        if not AsyncSessionLocal:
            raise RuntimeError("Database not initialized - check DATABASE_URL in .env")
        self.session: AsyncSession | None = None

    async def __aenter__(self) -> AsyncSession:
        self.session = AsyncSessionLocal()
        return self.session

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            try:
                if exc_type is None:
                    await self.session.commit()
                else:
                    await self.session.rollback()
                    _get_logger().error(f"Database transaction rolled back: {exc_val}")
            finally:
                await self.session.close()
