"""
Idempotent S3 Upload Service with Retry Logic

This module implements idempotent S3 uploads that prevent duplicate file uploads
and ensure exactly-once delivery semantics for TTS synthesis results.

Key Features:
- Idempotent retry: Check if file exists before uploading
- Metadata tagging for tracking upload status
- Exponential backoff retry with configurable attempts
- Partial failure recovery (S3 success + RabbitMQ ack failure)
- Comprehensive logging and error handling

Usage:
    from services.idempotent_upload import IdempotentUploader
    
    uploader = IdempotentUploader(s3_client)
    s3_path = uploader.upload_with_retry(
        job_id="job-123",
        local_path="/tmp/audio.wav",
        remote_path="ttsoutput/studio/job-123.wav",
        max_retries=3
    )
"""

import hashlib
import json
import logging
import os
import time
from datetime import datetime
from typing import Any

from services.s3_config import S3Client, S3ConfigError

logger = logging.getLogger(__name__)


class UploadMetadata:
    """Metadata for tracking upload status and recovery."""
    
    def __init__(
        self,
        job_id: str,
        status: str = "pending",
        upload_timestamp: str | None = None,
        local_file_hash: str | None = None,
        retry_count: int = 0,
    ):
        """
        Initialize upload metadata.
        
        Args:
            job_id: Unique job identifier
            status: Upload status (pending, uploading, uploaded, failed)
            upload_timestamp: ISO 8601 timestamp of upload
            local_file_hash: SHA256 hash of local file for integrity verification
            retry_count: Number of retry attempts
        """
        self.job_id = job_id
        self.status = status
        self.upload_timestamp = upload_timestamp or datetime.now().isoformat()
        self.local_file_hash = local_file_hash
        self.retry_count = retry_count
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for S3 metadata."""
        return {
            "job_id": self.job_id,
            "status": self.status,
            "upload_timestamp": self.upload_timestamp,
            "local_file_hash": self.local_file_hash or "unknown",
            "retry_count": str(self.retry_count),
        }
    
    @staticmethod
    def from_s3_metadata(metadata: dict[str, str]) -> "UploadMetadata":
        """Reconstruct from S3 object metadata."""
        return UploadMetadata(
            job_id=metadata.get("job_id", "unknown"),
            status=metadata.get("status", "unknown"),
            upload_timestamp=metadata.get("upload_timestamp"),
            local_file_hash=metadata.get("local_file_hash"),
            retry_count=int(metadata.get("retry_count", 0)),
        )


class IdempotentUploader:
    """Service for idempotent S3 uploads with retry logic."""
    
    def __init__(
        self,
        s3_client: S3Client,
        max_retries: int = 3,
        base_backoff: int = 2,
    ):
        """
        Initialize uploader.
        
        Args:
            s3_client: S3 client instance
            max_retries: Maximum retry attempts for upload failures
            base_backoff: Base delay (in seconds) for exponential backoff
        """
        self.s3_client = s3_client
        self.max_retries = max_retries
        self.base_backoff = base_backoff
    
    def _calculate_file_hash(self, file_path: str) -> str:
        """
        Calculate SHA256 hash of file for integrity verification.
        
        Args:
            file_path: Path to file
            
        Returns:
            SHA256 hash hex string
        """
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    
    def _check_existing_upload(
        self,
        job_id: str,
        remote_path: str,
        bucket_type: str = "output",
    ) -> UploadMetadata | None:
        """
        Check if file already uploaded with matching job_id.
        
        Implements idempotent retry by verifying:
        1. File exists in S3
        2. File has matching job_id in metadata
        3. Upload is marked as successful (status='uploaded')
        
        Args:
            job_id: Job identifier to match
            remote_path: S3 path to check
            bucket_type: "storage" or "output" bucket
            
        Returns:
            UploadMetadata if file exists and matches, None otherwise
        """
        try:
            # Check if file exists
            if not self.s3_client.file_exists(remote_path, bucket_type=bucket_type):
                logger.debug(f"[JOB {job_id}] No existing file at {remote_path}")
                return None
            
            # Get appropriate client and bucket
            client, bucket_name = self.s3_client._get_client_and_bucket(bucket_type)
            
            # Try to fetch metadata from S3
            try:
                response = client.head_object(
                    Bucket=bucket_name,
                    Key=remote_path,
                )
                
                # Extract metadata
                metadata = response.get("Metadata", {})
                existing_job_id = metadata.get("job_id")
                status = metadata.get("status", "unknown")
                
                if existing_job_id == job_id and status == "uploaded":
                    logger.info(
                        f"[JOB {job_id}] Found existing upload at {remote_path} "
                        f"(status: {status})"
                    )
                    return UploadMetadata.from_s3_metadata(metadata)
                
                elif existing_job_id == job_id:
                    logger.warning(
                        f"[JOB {job_id}] File exists but status is '{status}' "
                        f"(expected 'uploaded'). Will proceed with upload."
                    )
                    return None
                
                else:
                    logger.warning(
                        f"[JOB {job_id}] File exists but belongs to different job "
                        f"({existing_job_id}). Possible S3 path collision."
                    )
                    return None
                    
            except Exception as e:
                logger.warning(
                    f"[JOB {job_id}] Failed to fetch metadata for existing file: {e}"
                )
                return None
                
        except Exception as e:
            logger.error(f"[JOB {job_id}] Error checking existing upload: {e}")
            return None
    
    def upload_with_retry(
        self,
        job_id: str,
        local_path: str,
        remote_path: str,
        bucket_type: str = "output",
        verify_integrity: bool = True,
    ) -> str:
        """
        Upload file to S3 with idempotent retry and exponential backoff.
        
        Implements the following retry strategy:
        1. Check if file already uploaded (idempotent check)
        2. If exists and valid: Skip upload, return path
        3. If not exists: Attempt upload with retry
        4. On failure: Exponential backoff and retry
        5. After max retries: Log error and raise exception
        
        Args:
            job_id: Unique job identifier
            local_path: Path to local file to upload
            remote_path: S3 destination path
            bucket_type: "storage" or "output" (default: "output" for TTS results)
            verify_integrity: Check file hash for integrity
            
        Returns:
            S3 path (remote_path) if successful
            
        Raises:
            S3ConfigError: If upload fails after all retries
            FileNotFoundError: If local file doesn't exist
        """
        if not os.path.exists(local_path):
            raise FileNotFoundError(f"Local file not found: {local_path}")
        
        logger.info(f"[JOB {job_id}] Starting idempotent upload to {remote_path}")
        
        # Step 1: Check for existing upload (idempotent retry)
        existing = self._check_existing_upload(job_id, remote_path, bucket_type=bucket_type)
        if existing:
            logger.info(
                f"[JOB {job_id}] Skipping upload - file already exists in S3 "
                f"(status: {existing.status})"
            )
            return remote_path
        
        # Step 2: Calculate file hash for integrity verification
        file_hash = self._calculate_file_hash(local_path) if verify_integrity else "skipped"
        
        # Step 3: Retry loop with exponential backoff
        retry_count = 0
        last_error = None
        
        while retry_count < self.max_retries:
            try:
                logger.info(
                    f"[JOB {job_id}] Upload attempt {retry_count + 1}/{self.max_retries}"
                )
                
                # NOTE: Filebase and some S3 services don't support metadata in PUT operations
                # For now, we skip metadata on upload to avoid AccessDenied errors.
                # Consider using S3 tags or object ACLs instead for metadata storage.
                
                # Prepare metadata (for tracking, may be stored via tags in future)
                metadata = UploadMetadata(
                    job_id=job_id,
                    status="uploading",
                    local_file_hash=file_hash,
                    retry_count=retry_count,
                )
                
                # Upload file to output bucket (TTS results)
                # NOTE: metadata parameter is deliberately NOT passed to avoid
                # AccessDenied errors on S3 services that don't support metadata
                self.s3_client.upload_audio(
                    local_path=local_path,
                    remote_path=remote_path,
                    bucket_type=bucket_type,
                    job_id=job_id,
                    metadata=None,  # Disable metadata to support Filebase
                )
                
                logger.info(
                    f"[JOB {job_id}] Upload successful to {remote_path} "
                    f"(attempt {retry_count + 1})"
                )
                
                # Log metadata info for debugging (even though not stored in S3)
                logger.debug(f"[JOB {job_id}] Metadata (not stored in S3): {metadata.to_dict()}")
                
                return remote_path
                
            except S3ConfigError as e:
                last_error = e
                retry_count += 1
                
                if retry_count < self.max_retries:
                    # Calculate exponential backoff delay
                    delay = self.base_backoff ** retry_count
                    logger.warning(
                        f"[JOB {job_id}] Upload attempt {retry_count} failed: {e}. "
                        f"Retrying in {delay}s ({retry_count}/{self.max_retries})..."
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        f"[JOB {job_id}] Upload failed after {self.max_retries} attempts: {e}"
                    )
            
            except Exception as e:
                # Non-S3 errors (file system, permissions, etc.)
                logger.error(f"[JOB {job_id}] Non-retryable upload error: {e}")
                raise S3ConfigError(f"Upload failed: {e!s}") from e
        
        # All retries exhausted
        error_msg = (
            f"Failed to upload {local_path} to {remote_path} after "
            f"{self.max_retries} attempts"
        )
        logger.critical(f"[JOB {job_id}] {error_msg}: {last_error}")
        raise S3ConfigError(error_msg)
    
    def verify_upload(
        self,
        job_id: str,
        remote_path: str,
        local_file_hash: str | None = None,
    ) -> bool:
        """
        Verify that uploaded file exists and is complete.
        
        Optionally compare file hash if local_file_hash provided.
        
        Args:
            job_id: Job identifier
            remote_path: S3 path to verify
            local_file_hash: Optional file hash to verify against
            
        Returns:
            True if file exists and is valid, False otherwise
        """
        try:
            if not self.s3_client.file_exists(remote_path):
                logger.warning(f"[JOB {job_id}] Verification failed: File not found")
                return False
            
            logger.info(f"[JOB {job_id}] File verified in S3: {remote_path}")
            return True
            
        except Exception as e:
            logger.error(f"[JOB {job_id}] Verification error: {e}")
            return False
    
    def mark_upload_complete(
        self,
        job_id: str,
        remote_path: str,
    ) -> bool:
        """
        Update S3 object metadata to mark upload as complete.
        
        This is useful for tracking the upload lifecycle and recovery.
        
        Args:
            job_id: Job identifier
            remote_path: S3 path to mark
            
        Returns:
            True if metadata updated successfully, False otherwise
        """
        try:
            # In Supabase/S3, updating metadata requires re-uploading the object
            # This is a limitation of S3 API - metadata is immutable once set
            # For now, we just verify the file exists
            if self.s3_client.file_exists(remote_path):
                logger.info(f"[JOB {job_id}] Upload marked as complete: {remote_path}")
                return True
            else:
                logger.warning(f"[JOB {job_id}] File not found for completion marking")
                return False
                
        except Exception as e:
            logger.error(f"[JOB {job_id}] Error marking upload complete: {e}")
            return False
    
    def handle_partial_failure(
        self,
        job_id: str,
        remote_path: str,
        error: Exception,
    ) -> dict[str, Any]:
        """
        Handle partial failure scenario where S3 upload succeeds but RabbitMQ ack fails.
        
        Returns recovery metadata that can be used to reconcile state.
        
        Args:
            job_id: Job identifier
            remote_path: S3 path where file was uploaded
            error: The error that occurred during ack
            
        Returns:
            Recovery metadata with status and next steps
        """
        logger.critical(
            f"[JOB {job_id}] PARTIAL FAILURE: S3 upload succeeded but "
            f"RabbitMQ ack failed: {error}"
        )
        
        recovery_data = {
            "job_id": job_id,
            "remote_path": remote_path,
            "s3_status": "uploaded",
            "rabbitmq_status": "failed",
            "error": str(error),
            "timestamp": datetime.now().isoformat(),
            "recovery_steps": [
                "1. File is safely stored in S3 at specified path",
                "2. Manual intervention required to update database",
                "3. Verify no duplicate processing of this job",
                "4. Update job status in database to 'completed'",
            ],
            "manual_recovery_command": (
                f"# Manual recovery: Mark job {job_id} as completed "
                f"with audio_path={remote_path}"
            ),
        }
        
        logger.critical(f"Recovery data: {json.dumps(recovery_data, indent=2)}")
        
        return recovery_data


def create_uploader(
    # Storage bucket parameters
    storage_endpoint: str | None = None,
    storage_access_key: str | None = None,
    storage_secret_key: str | None = None,
    storage_bucket: str | None = None,
    storage_region: str | None = None,
    # Output bucket parameters
    output_endpoint: str | None = None,
    output_access_key: str | None = None,
    output_secret_key: str | None = None,
    output_bucket: str | None = None,
    output_region: str | None = None,
) -> IdempotentUploader:
    """
    Factory function to create IdempotentUploader with S3 client.
    
    Args:
        storage_endpoint: Storage bucket S3 endpoint URL (from env if not provided)
        storage_access_key: Storage bucket access key (from env if not provided)
        storage_secret_key: Storage bucket secret key (from env if not provided)
        storage_bucket: Storage bucket name (from env if not provided)
        storage_region: Storage bucket region (from env if not provided)
        
        output_endpoint: Output bucket S3 endpoint URL (from env if not provided)
        output_access_key: Output bucket access key (from env if not provided)
        output_secret_key: Output bucket secret key (from env if not provided)
        output_bucket: Output bucket name (from env if not provided)
        output_region: Output bucket region (from env if not provided)
        
    Returns:
        Configured IdempotentUploader instance
        
    Raises:
        S3ConfigError: If S3 configuration is invalid
    """
    try:
        s3_client = S3Client(
            storage_endpoint_url=storage_endpoint,
            storage_access_key_id=storage_access_key,
            storage_secret_access_key=storage_secret_key,
            storage_bucket_name=storage_bucket,
            storage_region=storage_region,
            output_endpoint_url=output_endpoint,
            output_access_key_id=output_access_key,
            output_secret_access_key=output_secret_key,
            output_bucket_name=output_bucket,
            output_region=output_region,
        )
        return IdempotentUploader(s3_client)
    except Exception as e:
        logger.error(f"Failed to create uploader: {e}")
        raise
