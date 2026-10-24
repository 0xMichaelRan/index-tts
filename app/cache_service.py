"""
Backwards compatibility shim for app.cache_service.
Canonical location is services.db.cache_service.
"""

from services.db.cache_service import *  # noqa: F401, F403
