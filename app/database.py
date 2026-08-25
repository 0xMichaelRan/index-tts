"""
Database connection and session management for TTS synthesis cache.

This module provides async database connection using SQLAlchemy with asyncpg driver.
Supports connection pooling, automatic reconnection, and session management.
"""

import os
from typing import AsyncGenerator

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker,
    AsyncEngine,
)
from sqlalchemy.pool import NullPool

from services.logging_config import get_logger

# Load environment variables
load_dotenv()

logger = get_logger(__name__)

def async_to_sync_database_url(database_url: str) -> str:
    """Convert async SQLAlchemy URL to sync (e.g. postgresql+asyncpg -> postgresql)."""
    from sqlalchemy.engine import make_url

    parsed = make_url(database_url)
    if "+asyncpg" in parsed.drivername:
        return str(parsed.set(drivername=parsed.drivername.replace("+asyncpg", "", 1)))
    return database_url


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

if not DATABASE_URL:
    logger.warning("DATABASE_URL not set - TTS synthesis cache will be disabled")

# Create async engine
# Using pool_pre_ping to verify connections before using them
# pool_size and max_overflow control connection pooling
engine: AsyncEngine | None = None

if DATABASE_URL:
    engine = create_async_engine(
        DATABASE_URL,
        echo=False,  # Set to True for SQL query logging (debugging)
        poolclass=NullPool,  # No pooling - safer for sync/async bridge in worker
        pool_pre_ping=True,  # Verify connections before using (handles stale connections)
        pool_recycle=3600,  # Recycle connections after 1 hour
    )
    logger.info("Database engine initialized successfully (NullPool mode)")
else:
    logger.warning("Database engine not initialized - cache disabled")

# Session factory
# expire_on_commit=False keeps objects accessible after commit
AsyncSessionLocal: async_sessionmaker[AsyncSession] | None = None

if engine:
    AsyncSessionLocal = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,  # Keep objects accessible after commit
        autoflush=False,  # Manual flush control for better performance
        autocommit=False,  # Explicit transaction control
    )


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency for getting database session.

    Usage in FastAPI:
        @app.get("/items")
        async def read_items(db: AsyncSession = Depends(get_db)):
            ...

    Usage in worker:
        async with get_db() as session:
            # Use session
            ...

    Yields:
        AsyncSession: Database session with automatic cleanup

    Note:
        - Automatically commits on success
        - Automatically rolls back on exception
        - Always closes session in finally block
    """
    if not AsyncSessionLocal:
        raise RuntimeError("Database not initialized - check DATABASE_URL in .env")

    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.error(f"Database session error: {e}")
            raise
        finally:
            await session.close()


async def init_db():
    """
    Initialize database (create tables if they don't exist).

    WARNING: This uses create_all() which is NOT suitable for production migrations.
    In production, use Alembic migrations instead:
        alembic upgrade head

    Usage:
        await init_db()

    Note:
        - Only creates tables that don't exist
        - Does not modify existing tables
        - Does not handle schema changes
        - For testing/development only
    """
    if not engine:
        raise RuntimeError(
            "Database engine not initialized - check DATABASE_URL in .env"
        )

    from app.models import Base

    async with engine.begin() as conn:
        # Create all tables
        await conn.run_sync(Base.metadata.create_all)

    logger.success("Database tables created successfully")


async def close_db():
    """
    Close database connection pool.

    Call this during application shutdown to properly close all connections.

    Usage:
        await close_db()
    """
    if engine:
        await engine.dispose()
        logger.info("Database connection pool closed")


async def check_db_connection() -> bool:
    """
    Check if database connection is working.

    Returns:
        bool: True if connection successful, False otherwise

    Usage:
        if await check_db_connection():
            print("Database is ready")
    """
    if not engine:
        logger.error("Database engine not initialized")
        return False

    try:
        from sqlalchemy import text

        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        logger.info("Database connection check: OK")
        return True
    except Exception as e:
        logger.error(f"Database connection check failed: {e}")
        return False


# Context manager for standalone usage (outside FastAPI)
class DatabaseSession:
    """
    Context manager for database sessions in non-FastAPI contexts.

    Usage:
        async with DatabaseSession() as session:
            # Use session
            result = await session.execute(...)
            # Auto-commit on exit (if no exception)
    """

    def __init__(self):
        if not AsyncSessionLocal:
            raise RuntimeError("Database not initialized - check DATABASE_URL in .env")
        self.session: AsyncSession | None = None

    async def __aenter__(self) -> AsyncSession:
        """Enter context - create session."""
        self.session = AsyncSessionLocal()
        return self.session

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Exit context - commit or rollback, then close."""
        if self.session:
            try:
                if exc_type is None:
                    # No exception - commit
                    await self.session.commit()
                else:
                    # Exception occurred - rollback
                    await self.session.rollback()
                    logger.error(f"Database transaction rolled back: {exc_val}")
            finally:
                await self.session.close()
