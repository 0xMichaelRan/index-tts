from __future__ import annotations

import platform
from fastapi import APIRouter, Depends

from services.api.deps import get_s3_status

router = APIRouter()


@router.get("/", summary="API information and route discovery")
async def root():
    """Return API metadata and registered pipeline routes."""
    sys_plat = platform.system()
    engine_desc = (
        "macOS AVFoundation TTS"
        if sys_plat == "Darwin"
        else "IndexTTS GPU Inference"
    )
    return {
        "name": "IndexTTS Unified Media Worker API",
        "version": "2.0.0",
        "platform": sys_plat,
        "engine": engine_desc,
        "endpoints": {
            "/": "API information and metadata",
            "/health": "System health and storage connectivity check",
            "/api/v1/tts/synthesize": "Execute complete TTS SynthesisPipeline (JSON)",
            "/api/v1/tts/direct": "Direct speech synthesis returning WAV audio (Form)",
            "/api/v1/vox/render": "Execute VoxRenderPipeline video rendering (JSON)",
            "/infer/": "Legacy TTS inference endpoint (deprecated)",
        },
    }


@router.get("/health", summary="System health check")
async def health(s3_connected: bool = Depends(get_s3_status)):
    """Return health status of the API, active engine, and storage connectivity."""
    sys_plat = platform.system()
    engine_name = "macOS_native" if sys_plat == "Darwin" else "indexTTS_gpu"

    return {
        "status": "healthy" if s3_connected else "degraded",
        "platform": sys_plat,
        "engine": engine_name,
        "s3": {
            "status": "connected" if s3_connected else "unavailable"
        },
        "pipelines": ["tts", "vox"],
    }
