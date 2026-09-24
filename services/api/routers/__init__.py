from services.api.routers.health import router as health_router
from services.api.routers.tts import legacy_router, router as tts_router
from services.api.routers.vox import router as vox_router

__all__ = [
    "health_router",
    "tts_router",
    "legacy_router",
    "vox_router",
]
