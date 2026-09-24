from __future__ import annotations

from fastapi import FastAPI

from services.api.routers import health_router, legacy_router, tts_router, vox_router


def create_app() -> FastAPI:
    """Create and configure the unified multi-pipeline FastAPI application."""
    application = FastAPI(
        title="IndexTTS Multi-Pipeline Worker API",
        description="Unified REST API for testing and executing TTS speech synthesis and Vox video rendering pipelines.",
        version="2.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # Mount routers
    application.include_router(health_router, tags=["System & Discovery"])
    application.include_router(tts_router, prefix="/api/v1/tts", tags=["TTS Pipeline"])
    application.include_router(vox_router, prefix="/api/v1/vox", tags=["Vox Pipeline"])
    application.include_router(legacy_router, tags=["Legacy"])

    return application


app = create_app()
