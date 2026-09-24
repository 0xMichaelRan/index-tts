from __future__ import annotations

import os
import platform
import shutil
import tempfile
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from services.api.deps import get_tts_engine, get_tts_pipeline
from services.api.schemas.tts import TTSJobRequest, TTSJobResponse
from services.common.logging_config import get_logger
from services.tts.synthesis_pipeline import SynthesisPipeline

logger = get_logger(__name__)

router = APIRouter()
legacy_router = APIRouter()


async def _execute_direct_tts(
    text: str,
    audio_prompt: Optional[UploadFile],
    speed_ratio: float,
    ratio: Optional[float],
    tts_engine: Any,
) -> FileResponse:
    """Execute direct TTS inference without pipeline orchestration and return WAV file."""
    effective_ratio = ratio if ratio is not None else speed_ratio
    sys_plat = platform.system()

    output_dir = os.path.join("outputs", "tts_output")
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")

    if sys_plat == "Darwin":
        output_filename = f"{timestamp}_macos_tts.wav"
        output_path = os.path.join(output_dir, output_filename)
        logger.info(f"Direct macOS TTS: synthesizing to {output_path} (ratio: {effective_ratio})")
        tts_engine.infer(
            audio_prompt=None,
            text=text,
            output_path=output_path,
            ratio=effective_ratio,
            pitch=1.0,
            volume=1.0,
        )
    else:
        if audio_prompt is None:
            raise HTTPException(
                status_code=400,
                detail="audio_prompt is required for GPU-based inference on Windows/Linux",
            )
        output_filename = f"{timestamp}_gpu_tts.wav"
        output_path = os.path.join(output_dir, output_filename)

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_prompt:
            temp_prompt_path = temp_prompt.name
            shutil.copyfileobj(audio_prompt.file, temp_prompt)

        try:
            logger.info(f"Direct GPU TTS: synthesizing to {output_path} (ratio: {effective_ratio})")
            if hasattr(tts_engine, "infer_fast"):
                tts_engine.infer_fast(
                    audio_prompt=temp_prompt_path,
                    text=text,
                    output_path=output_path,
                )
            else:
                tts_engine.infer(
                    audio_prompt=temp_prompt_path,
                    text=text,
                    output_path=output_path,
                )
        finally:
            if os.path.exists(temp_prompt_path):
                os.remove(temp_prompt_path)

    return FileResponse(
        path=output_path,
        media_type="audio/wav",
        filename=output_filename,
    )


@router.post(
    "/synthesize",
    response_model=TTSJobResponse,
    summary="Execute full TTS SynthesisPipeline",
)
async def synthesize(
    request: TTSJobRequest,
    pipeline: SynthesisPipeline = Depends(get_tts_pipeline),
):
    """
    Execute complete production TTS synthesis pipeline.

    Includes text sanitization, cache lookup, model inference,
    loudness normalization, mandatory forced alignment, and S3 upload.
    """
    job_data = request.model_dump(by_alias=True, exclude_none=True)
    logger.info(f"API TTS /synthesize request: jobId={request.job_id}")

    try:
        result = pipeline.process_job(job_data)
        if result.get("status") == "completed":
            return TTSJobResponse(**result)
        else:
            return JSONResponse(status_code=500, content=result)
    except Exception as e:
        logger.error(f"Error executing TTS pipeline for jobId={request.job_id}: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "jobId": request.job_id,
                "jobType": request.job_type,
                "status": "failed",
                "errorCode": type(e).__name__,
                "errorMessage": str(e),
            },
        )


@router.post("/direct", summary="Direct TTS audio synthesis")
async def direct(
    text: str = Form(..., description="Text to synthesize"),
    audio_prompt: Optional[UploadFile] = File(None, description="Reference audio file for voice cloning"),
    speed_ratio: float = Form(1.0, description="Speech rate multiplier"),
    ratio: Optional[float] = Form(None, description="Legacy alias for speed_ratio"),
    tts_engine: Any = Depends(get_tts_engine),
):
    """
    Direct text-to-speech inference returning a WAV audio file directly.

    Useful for quick local listening tests without S3 uploads or alignment.
    """
    return await _execute_direct_tts(text, audio_prompt, speed_ratio, ratio, tts_engine)


@legacy_router.post("/infer/", summary="Legacy TTS inference endpoint (deprecated)")
async def legacy_infer(
    response: Response,
    text: str = Form(..., description="Text to synthesize"),
    audio_prompt: Optional[UploadFile] = File(None, description="Reference audio file for voice cloning"),
    ratio: float = Form(1.0, description="Speech rate multiplier"),
    tts_engine: Any = Depends(get_tts_engine),
):
    """
    Legacy inference endpoint matching previous /infer/ route.

    Deprecated: Use /api/v1/tts/direct instead.
    """
    response.headers["Deprecation-Warning"] = (
        "/infer/ is deprecated; use /api/v1/tts/direct instead"
    )
    file_response = await _execute_direct_tts(text, audio_prompt, 1.0, ratio, tts_engine)
    file_response.headers["Deprecation-Warning"] = (
        "/infer/ is deprecated; use /api/v1/tts/direct instead"
    )
    return file_response
