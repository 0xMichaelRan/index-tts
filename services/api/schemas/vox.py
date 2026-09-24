from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator


class VoxJobRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    job_id: str = Field("unknown", alias="jobId", description="Unique job identifier")
    job_type: str = Field("vox", alias="jobType", description="Job type, always 'vox'")
    resolution: str = Field(..., description="Mandatory resolution (e.g. 720p, 1080p)")
    aspect_ratio: str = Field(..., alias="aspectRatio", description="Mandatory aspect ratio (e.g. 9:16, 16:9)")
    clip_s3_keys: list[str] = Field(..., alias="clipS3Keys", min_length=1, description="Ordered list of video clip S3 keys")
    beat_narrations: list[str] = Field(..., alias="beatNarrations", min_length=1, description="Ordered list of narration strings per beat")
    audio_path: str = Field(..., alias="audioPath", description="S3 path to narration audio file")
    alignment_path: str = Field(..., alias="alignmentPath", description="S3 path to word-level alignment JSON")
    output_s3_key: str = Field(..., alias="outputS3Key", description="Target S3 key for rendered MP4 output")
    project_id: Optional[Any] = Field(None, alias="projectId")
    voice_id: Optional[Any] = Field(None, alias="voiceId")
    language: str = Field("en", description="Narration language code")
    burn_subtitles: bool = Field(True, alias="burnSubtitles", description="Whether to burn ASS subtitles into video")
    created_at: Optional[str] = Field(None, alias="createdAt")

    @model_validator(mode="after")
    def validate_clips_and_narrations_match(self) -> VoxJobRequest:
        if len(self.clip_s3_keys) != len(self.beat_narrations):
            raise ValueError(
                f"Mismatch: {len(self.clip_s3_keys)} clips vs {len(self.beat_narrations)} narrations"
            )
        return self


class VoxJobResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    job_id: str = Field(..., alias="jobId")
    job_type: str = Field("vox", alias="jobType")
    status: str
    project_id: Optional[Any] = Field(None, alias="projectId")
    video_path: Optional[str] = Field(None, alias="videoPath")
    video_duration_seconds: Optional[float] = Field(None, alias="videoDurationSeconds")
    render_duration_seconds: Optional[float] = Field(None, alias="renderDurationSeconds")
    completed_at: Optional[str] = Field(None, alias="completedAt")
    error_code: Optional[str] = Field(None, alias="errorCode")
    error_message: Optional[str] = Field(None, alias="errorMessage")
