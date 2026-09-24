from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from services.api.deps import get_vox_pipeline
from services.api.schemas.vox import VoxJobRequest, VoxJobResponse
from services.common.logging_config import get_logger
from services.vox.pipeline import VoxRenderPipeline

logger = get_logger(__name__)

router = APIRouter()


@router.post(
    "/render",
    response_model=VoxJobResponse,
    summary="Execute VoxRenderPipeline video rendering",
)
async def render(
    request: VoxJobRequest,
    pipeline: VoxRenderPipeline = Depends(get_vox_pipeline),
):
    """
    Execute full video rendering pipeline from a vox_jobs message payload.

    Resolves audio and Whisper word-level alignment, downloads clips,
    adapts clips using ScriptGuidedAligner to beat narrations,
    and uploads the rendered MP4 to S3.
    """
    job_data = request.model_dump(by_alias=True, exclude_none=True)
    logger.info(f"API Vox /render request: jobId={request.job_id}")

    try:
        result = pipeline.process_job(job_data)
        if result.get("status") == "completed":
            return VoxJobResponse(**result)
        else:
            return JSONResponse(status_code=500, content=result)
    except Exception as e:
        logger.error(f"Error executing Vox pipeline for jobId={request.job_id}: {e}")
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
