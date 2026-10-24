"""
services.db — Database connection, SQLAlchemy models, and cache service.
"""

from services.db.cache_service import TTSCacheService, TTSCacheServiceSync
from services.db.database import (
    AsyncSessionLocal,
    DatabaseSession,
    SyncDatabaseSession,
    SyncSessionLocal,
    check_db_connection,
    close_db,
    get_db,
    get_sync_database_url,
    init_db,
)
from services.db.models import Base, TTSJob, TTSSynthesisCache

__all__ = [
    "Base",
    "TTSSynthesisCache",
    "TTSJob",
    "TTSCacheService",
    "TTSCacheServiceSync",
    "get_db",
    "init_db",
    "close_db",
    "check_db_connection",
    "DatabaseSession",
    "SyncDatabaseSession",
    "AsyncSessionLocal",
    "SyncSessionLocal",
    "get_sync_database_url",
]
