from __future__ import annotations

from typing import Any, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field


class TTSJobRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    job_id: str = Field(..., alias="jobId", description="Unique job identifier")
    text: str = Field(..., min_length=1, description="Raw text to synthesize")
    audio_prompt_path: Optional[str] = Field(
        None, alias="audioPromptPath", description="S3 path to voice reference audio"
    )
    spoken_lang: str = Field("en", alias="spokenLang", description="Spoken language code")
    job_type: Literal["studio", "playground", "rem", "flow"] = Field(
        "studio", alias="jobType", description="TTS job pipeline type"
    )
    speed_ratio: float = Field(
        1.0, alias="speedRatio", ge=0.25, le=4.0, description="Speech rate multiplier"
    )
    locale: Optional[str] = Field(None, description="Optional locale for flow jobs (e.g. en, zh-CN)")
    voice_id: Optional[Any] = Field(None, alias="voiceId", description="Optional voice identifier")
    is_test: Optional[bool] = Field(False, alias="isTest", description="Whether job is a test run")
    environment: Optional[str] = Field("prod", description="Execution environment")
    remotion_style: Optional[str] = Field(None, alias="remotionStyle")
    resolution: Optional[str] = None
    aspect_ratio: Optional[str] = Field(None, alias="aspectRatio")


class TTSJobResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    job_id: str = Field(..., alias="jobId")
    job_type: str = Field("studio", alias="jobType")
    status: str
    audio_path: Optional[str] = Field(None, alias="audioPath")
    audio_duration_seconds: Optional[float] = Field(None, alias="audioDurationSeconds")
    synthesis_duration_seconds: Optional[float] = Field(None, alias="synthesisDurationSeconds")
    alignment_path: Optional[str] = Field(None, alias="alignmentPath")
    alignment_duration_seconds: Optional[float] = Field(None, alias="alignmentDurationSeconds")
    started_at: Optional[str] = Field(None, alias="startedAt")
    completed_at: Optional[str] = Field(None, alias="completedAt")
    cache_hit: Optional[bool] = Field(None, alias="cacheHit")
    retry_count: Optional[int] = Field(None, alias="retryCount")
    tts_id: Optional[Any] = Field(None, alias="ttsId")
    locale: Optional[str] = None
    error_code: Optional[str] = Field(None, alias="errorCode")
    error_message: Optional[str] = Field(None, alias="errorMessage")
    is_test: Optional[bool] = Field(None, alias="isTest")
