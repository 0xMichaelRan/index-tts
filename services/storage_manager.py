"""
Storage and file operations management for S3 and local filesystem.
"""

import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from services.idempotent_upload import IdempotentUploader
from services.logging_config import get_logger
from services.s3_config import S3Client, S3ConfigError

logger = get_logger(__name__)


class StorageManager:
    """Manages S3 operations and local file storage."""

    def __init__(self, s3_client: Optional[S3Client] = None):
        """
        Initialize storage manager.

        Args:
            s3_client: S3Client instance (creates if None)
        """
        if s3_client is None:
            self.s3_client = S3Client()
        else:
            self.s3_client = s3_client

        self.uploader = IdempotentUploader(self.s3_client)

        # Cache bucket names
        self.s3_misc_bucket = self.s3_client.storage_bucket_name
        self.r2_voice_bucket = self.s3_client.output_bucket_name

    def download_audio_prompt(
        self,
        job_id: str,
        audio_prompt_path: str,
    ) -> str:
        """
        Download audio prompt from S3 storage bucket.

        Args:
            job_id: Job identifier
            audio_prompt_path: S3 path to audio prompt

        Returns:
            Local file path to downloaded audio

        Raises:
            S3ConfigError: If download fails
        """
        # Create temp directory
        temp_dir = os.path.join("outputs", "temp", job_id)
        os.makedirs(temp_dir, exist_ok=True)

        local_path = os.path.join(temp_dir, os.path.basename(audio_prompt_path))

        # Download from storage bucket
        self.s3_client.download_file(
            remote_path=audio_prompt_path,
            local_path=local_path,
            bucket_type="storage",
            max_retries=3,
        )

        logger.info(f"[JOB {job_id}] Downloaded audio prompt to {local_path}")
        return local_path

    def upload_audio(
        self,
        job_id: str,
        local_path: str,
        remote_path: str,
    ) -> str:
        """
        Upload synthesized audio to S3 with idempotent retry.

        Args:
            job_id: Job identifier
            local_path: Local file path
            remote_path: S3 destination path

        Returns:
            S3 path

        Raises:
            S3ConfigError: If upload fails
        """
        logger.info(f"[JOB {job_id}] Starting idempotent S3 upload")

        try:
            s3_path = self.uploader.upload_with_retry(
                job_id=job_id,
                local_path=local_path,
                remote_path=remote_path,
                verify_integrity=True,
            )

            logger.info(f"[JOB {job_id}] Upload completed: {s3_path}")
            return s3_path

        except S3ConfigError as e:
            logger.error(f"[JOB {job_id}] Upload failed: {e}")
            raise

    def upload_alignment_json(
        self,
        job_id: str,
        local_parsed_json: str,
        output_s3_path: str,
    ) -> str:
        """
        Upload parsed alignment JSON sidecar to S3.

        S3 path is derived by replacing file extension with .json

        Args:
            job_id: Job identifier
            local_parsed_json: Local path to parsed alignment JSON
            output_s3_path: S3 path of the audio file

        Returns:
            S3 key of uploaded alignment JSON

        Raises:
            S3ConfigError: If upload fails
        """
        # Replace file extension with .json
        alignment_s3_path = output_s3_path.rsplit(".", 1)[0] + ".json"

        logger.info(
            f"[JOB {job_id}] Uploading alignment JSON sidecar → {alignment_s3_path}"
        )

        s3_path = self.uploader.upload_with_retry(
            job_id=job_id,
            local_path=local_parsed_json,
            remote_path=alignment_s3_path,
            verify_integrity=True,
        )

        logger.info(f"[JOB {job_id}] Alignment JSON uploaded: {s3_path}")
        return s3_path

    def cleanup_local_files(self, *paths: str) -> None:
        """Remove local temporary files."""
        for path in paths:
            if not path:
                continue
            try:
                if os.path.exists(path):
                    os.remove(path)
                    logger.debug(f"Removed: {path}")
            except Exception as e:
                logger.warning(f"Failed to remove {path}: {e!s}")

    def create_output_dir(self, job_id: str) -> str:
        """
        Create output directory for job artifacts.

        Args:
            job_id: Job identifier

        Returns:
            Path to output directory
        """
        output_dir = os.path.join("outputs", "tts_output", job_id)
        os.makedirs(output_dir, exist_ok=True)
        return output_dir

    def create_cache_dir(self, cache_dir: str) -> str:
        """
        Create cache directory if it doesn't exist.

        Args:
            cache_dir: Cache directory path

        Returns:
            Path to cache directory
        """
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        return cache_dir

    def get_temp_dir(self, job_id: str) -> str:
        """
        Create and return temp directory for job.

        Args:
            job_id: Job identifier

        Returns:
            Path to temp directory
        """
        temp_dir = os.path.join("outputs", "temp", job_id)
        os.makedirs(temp_dir, exist_ok=True)
        return temp_dir

    def copy_file(self, src: str, dst: str) -> None:
        """Copy file from src to dst."""
        shutil.copy(src, dst)

    @staticmethod
    def build_s3_output_path(
        job_type: str,
        job_id: str,
        language: str,
        ratio: float,
        environment: str,
        voice_id: Optional[int],
        file_extension: str,
    ) -> str:
        """
        Build S3 output path.

        Format: {job_type}/{YYYYMMDD}/{job_id}/{language}_r{ratio}_{environment}[_voice{id}].{ext}

        Args:
            job_type: "studio", "playground", or "rem"
            job_id: Job identifier
            language: Language code
            ratio: Speed ratio
            environment: Environment name
            voice_id: Voice ID (include if > 0)
            file_extension: File extension without dot

        Returns:
            S3 path string
        """
        # Get current date
        date_str = datetime.now().strftime("%Y%m%d")

        # Format ratio
        ratio_int = int(ratio * 10)
        ratio_str = f"r{ratio_int:02d}"

        # Build filename
        filename_parts = [language, ratio_str, environment]
        if voice_id and voice_id > 0:
            filename_parts.append(f"voice{voice_id}")

        filename = "_".join(filename_parts) + f".{file_extension}"

        # Build full path
        return f"{job_type}/{date_str}/{job_id}/{filename}"
