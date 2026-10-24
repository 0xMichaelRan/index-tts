"""
Backwards compatibility shim for app.database.
Canonical location is services.db.database.
"""

from services.db.database import *  # noqa: F401, F403
