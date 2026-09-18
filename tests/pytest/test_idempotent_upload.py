"""
Unit tests for IdempotentUploader service.

Tests cover:
- Idempotent retry: Check if file exists before uploading
- Metadata tagging for tracking upload status
- Exponential backoff retry with configurable attempts
- Partial failure recovery (S3 success + RabbitMQ ack failure)
- File integrity verification
- Various failure scenarios and error handling
"""

import os
import pytest
import tempfile
from unittest.mock import Mock, patch, call

from services.idempotent_upload import (
    IdempotentUploader,
    UploadMetadata,
)
from services.s3_config import S3ConfigError


class TestUploadMetadata:
    """Test UploadMetadata data class."""

    def test_initialization(self):
        """Test metadata initialization."""
        metadata = UploadMetadata(
            job_id="job-123",
            status="uploaded",
            upload_timestamp="2024-12-25T10:00:00Z",
            local_file_hash="abc123",
            retry_count=1,
        )

        assert metadata.job_id == "job-123"
        assert metadata.status == "uploaded"
        assert metadata.upload_timestamp == "2024-12-25T10:00:00Z"
        assert metadata.local_file_hash == "abc123"
        assert metadata.retry_count == 1

    def test_to_dict(self):
        """Test conversion to dictionary."""
        metadata = UploadMetadata(
            job_id="job-123",
            status="uploading",
            local_file_hash="hash123",
            retry_count=2,
        )

        result = metadata.to_dict()

        assert result["job_id"] == "job-123"
        assert result["status"] == "uploading"
        assert result["local_file_hash"] == "hash123"
        assert result["retry_count"] == "2"

    def test_from_s3_metadata(self):
        """Test reconstruction from S3 metadata."""
        s3_metadata = {
            "job_id": "job-456",
            "status": "uploaded",
            "upload_timestamp": "2024-12-25T10:00:00Z",
            "local_file_hash": "xyz789",
            "retry_count": "3",
        }

        metadata = UploadMetadata.from_s3_metadata(s3_metadata)

        assert metadata.job_id == "job-456"
        assert metadata.status == "uploaded"
        assert metadata.upload_timestamp == "2024-12-25T10:00:00Z"
        assert metadata.local_file_hash == "xyz789"
        assert metadata.retry_count == 3


class TestIdempotentUploader:
    """Test IdempotentUploader service."""

    @pytest.fixture
    def mock_s3_client(self):
        """Create mock S3 client with multi-bucket support."""
        client = Mock()
        client.misc_bucket_name = "klatu-misc"
        client.audio_bucket_name = "klatu-audio"
        client.video_bucket_name = "klatu-video"
        client.file_exists = Mock(return_value=False)
        client.upload_audio = Mock()
        client.misc_client = Mock()
        client.audio_client = Mock()
        client.video_client = Mock()
        client._resolve = Mock(
            side_effect=lambda bucket_type="audio": (
                (client.misc_client, client.misc_bucket_name)
                if bucket_type == "misc"
                else (client.video_client, client.video_bucket_name)
                if bucket_type == "video"
                else (client.audio_client, client.audio_bucket_name)
            )
        )
        return client

    @pytest.fixture
    def uploader(self, mock_s3_client):
        """Create uploader instance with mock S3 client."""
        return IdempotentUploader(mock_s3_client)

    @pytest.fixture
    def temp_audio_file(self):
        """Create a temporary audio file for testing."""
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(b"fake audio data" * 100)
            temp_path = f.name

        yield temp_path

        # Cleanup
        if os.path.exists(temp_path):
            os.remove(temp_path)

    def test_initialization(self, mock_s3_client):
        """Test uploader initialization."""
        uploader = IdempotentUploader(
            mock_s3_client,
            max_retries=5,
            base_backoff=3,
        )

        assert uploader.s3_client == mock_s3_client
        assert uploader.max_retries == 5
        assert uploader.base_backoff == 3

    def test_calculate_file_hash(self, uploader, temp_audio_file):
        """Test file hash calculation."""
        file_hash = uploader._calculate_file_hash(temp_audio_file)

        # Verify it's a valid SHA256 hash
        assert len(file_hash) == 64  # SHA256 hex is 64 chars
        assert all(c in "0123456789abcdef" for c in file_hash)

    def test_calculate_file_hash_consistency(self, uploader, temp_audio_file):
        """Test that file hash is consistent across calls."""
        hash1 = uploader._calculate_file_hash(temp_audio_file)
        hash2 = uploader._calculate_file_hash(temp_audio_file)

        assert hash1 == hash2

    def test_check_existing_upload_not_exists(self, uploader, mock_s3_client):
        """Test checking for non-existent upload."""
        mock_s3_client.file_exists.return_value = False

        result = uploader._check_existing_upload(
            "job-123", "s3://bucket/file.wav", bucket_type="audio"
        )

        assert result is None
        mock_s3_client.file_exists.assert_called_once_with(
            "s3://bucket/file.wav", bucket_type="audio"
        )

    def test_check_existing_upload_exists_and_valid(self, uploader, mock_s3_client):
        """Test checking for existing upload that is valid."""
        mock_s3_client.file_exists.return_value = True
        mock_s3_client.audio_client.head_object.return_value = {
            "Metadata": {
                "job_id": "job-123",
                "status": "uploaded",
                "upload_timestamp": "2024-12-25T10:00:00Z",
                "local_file_hash": "hash123",
                "retry_count": "0",
            }
        }

        result = uploader._check_existing_upload(
            "job-123", "tts-audio/studio/job-123.mp3", bucket_type="audio"
        )

        assert result is not None
        assert result.job_id == "job-123"
        assert result.status == "uploaded"

    def test_check_existing_upload_exists_but_wrong_job(self, uploader, mock_s3_client):
        """Test checking for existing upload from different job."""
        mock_s3_client.file_exists.return_value = True
        mock_s3_client.audio_client.head_object.return_value = {
            "Metadata": {
                "job_id": "job-999",  # Different job ID
                "status": "uploaded",
            }
        }

        result = uploader._check_existing_upload(
            "job-123", "tts-audio/studio/file.mp3", bucket_type="audio"
        )

        assert result is None

    def test_check_existing_upload_exists_but_not_complete(
        self, uploader, mock_s3_client
    ):
        """Test checking for existing upload that's not yet complete."""
        mock_s3_client.file_exists.return_value = True
        mock_s3_client.audio_client.head_object.return_value = {
            "Metadata": {
                "job_id": "job-123",
                "status": "uploading",  # Still in progress
            }
        }

        result = uploader._check_existing_upload(
            "job-123", "tts-audio/studio/file.mp3", bucket_type="audio"
        )

        assert result is None

    def test_check_existing_upload_metadata_error(self, uploader, mock_s3_client):
        """Test handling of metadata fetch errors."""
        mock_s3_client.file_exists.return_value = True
        mock_s3_client.audio_client.head_object.side_effect = Exception("Access denied")

        result = uploader._check_existing_upload(
            "job-123", "tts-audio/studio/file.mp3", bucket_type="audio"
        )

        assert result is None

    def test_upload_with_retry_success_first_attempt(
        self, uploader, mock_s3_client, temp_audio_file
    ):
        """Test successful upload on first attempt."""
        mock_s3_client.file_exists.return_value = False

        result = uploader.upload_with_retry(
            job_id="job-123",
            local_path=temp_audio_file,
            remote_path="tts-audio/studio/job-123.mp3",
        )

        assert result == "tts-audio/studio/job-123.mp3"
        mock_s3_client.upload_audio.assert_called_once()

    def test_upload_with_retry_skips_existing(
        self, uploader, mock_s3_client, temp_audio_file
    ):
        """Test that existing upload is skipped."""
        # Mock existing upload
        mock_metadata = UploadMetadata(
            job_id="job-123",
            status="uploaded",
        )
        uploader._check_existing_upload = Mock(return_value=mock_metadata)

        result = uploader.upload_with_retry(
            job_id="job-123",
            local_path=temp_audio_file,
            remote_path="tts-audio/studio/job-123.mp3",
        )

        assert result == "tts-audio/studio/job-123.mp3"
        mock_s3_client.upload_audio.assert_not_called()  # Should not upload

    def test_upload_with_retry_file_not_found(self, uploader):
        """Test error handling for missing local file."""
        with pytest.raises(FileNotFoundError):
            uploader.upload_with_retry(
                job_id="job-123",
                local_path="/nonexistent/file.wav",
                remote_path="tts-audio/studio/job-123.mp3",
            )

    def test_upload_with_retry_exponential_backoff(
        self, uploader, mock_s3_client, temp_audio_file
    ):
        """Test exponential backoff on retries."""
        # Mock failures on first two attempts, success on third
        mock_s3_client.file_exists.return_value = False
        mock_s3_client.upload_audio.side_effect = [
            S3ConfigError("Timeout"),
            S3ConfigError("Throttled"),
            None,  # Success
        ]

        with patch("time.sleep") as mock_sleep:
            result = uploader.upload_with_retry(
                job_id="job-123",
                local_path=temp_audio_file,
                remote_path="tts-audio/studio/job-123.mp3",
                verify_integrity=False,
            )

        assert result == "tts-audio/studio/job-123.mp3"

        # Verify exponential backoff: 2^1=2, 2^2=4
        mock_sleep.assert_has_calls([call(2), call(4)])
        assert mock_s3_client.upload_audio.call_count == 3

    def test_upload_with_retry_max_retries_exceeded(
        self, uploader, mock_s3_client, temp_audio_file
    ):
        """Test error when max retries exceeded."""
        mock_s3_client.file_exists.return_value = False
        mock_s3_client.upload_audio.side_effect = S3ConfigError("Persistent failure")

        with pytest.raises(S3ConfigError) as exc_info:
            with patch("time.sleep"):
                uploader.upload_with_retry(
                    job_id="job-123",
                    local_path=temp_audio_file,
                    remote_path="tts-audio/studio/job-123.mp3",
                    verify_integrity=False,
                )

        assert "3 attempts" in str(exc_info.value)

    def test_upload_with_retry_non_retryable_error(
        self, uploader, mock_s3_client, temp_audio_file
    ):
        """Test handling of non-retryable errors."""
        mock_s3_client.file_exists.return_value = False
        mock_s3_client.upload_audio.side_effect = ValueError("Invalid argument")

        with pytest.raises(S3ConfigError):
            uploader.upload_with_retry(
                job_id="job-123",
                local_path=temp_audio_file,
                remote_path="tts-audio/studio/job-123.mp3",
            )

    def test_verify_upload_success(self, uploader, mock_s3_client):
        """Test successful upload verification."""
        mock_s3_client.file_exists.return_value = True

        result = uploader.verify_upload("job-123", "tts-audio/studio/job-123.mp3")

        assert result is True

    def test_verify_upload_file_not_found(self, uploader, mock_s3_client):
        """Test verification failure when file not found."""
        mock_s3_client.file_exists.return_value = False

        result = uploader.verify_upload("job-123", "tts-audio/studio/job-123.mp3")

        assert result is False

    def test_verify_upload_error(self, uploader, mock_s3_client):
        """Test verification error handling."""
        mock_s3_client.file_exists.side_effect = Exception("Access denied")

        result = uploader.verify_upload("job-123", "tts-audio/studio/job-123.mp3")

        assert result is False

    def test_mark_upload_complete_success(self, uploader, mock_s3_client):
        """Test marking upload as complete."""
        mock_s3_client.file_exists.return_value = True

        result = uploader.mark_upload_complete(
            "job-123", "tts-audio/studio/job-123.mp3"
        )

        assert result is True

    def test_mark_upload_complete_not_found(self, uploader, mock_s3_client):
        """Test marking non-existent file as complete."""
        mock_s3_client.file_exists.return_value = False

        result = uploader.mark_upload_complete(
            "job-123", "tts-audio/studio/job-123.mp3"
        )

        assert result is False

    def test_handle_partial_failure(self, uploader):
        """Test handling of partial failure scenario."""
        error = Exception("RabbitMQ connection lost")

        recovery_data = uploader.handle_partial_failure(
            job_id="job-123",
            remote_path="tts-audio/studio/job-123.mp3",
            error=error,
        )

        assert recovery_data["job_id"] == "job-123"
        assert recovery_data["remote_path"] == "tts-audio/studio/job-123.mp3"
        assert recovery_data["s3_status"] == "uploaded"
        assert recovery_data["rabbitmq_status"] == "failed"
        assert "recovery_steps" in recovery_data
        assert len(recovery_data["recovery_steps"]) == 4

    def test_upload_with_integrity_verification(
        self, uploader, mock_s3_client, temp_audio_file
    ):
        """Test upload with file integrity verification."""
        mock_s3_client.file_exists.return_value = False

        result = uploader.upload_with_retry(
            job_id="job-123",
            local_path=temp_audio_file,
            remote_path="tts-audio/studio/job-123.mp3",
            verify_integrity=True,
        )

        assert result == "tts-audio/studio/job-123.mp3"

        # Verify metadata includes file hash
        call_args = mock_s3_client.upload_audio.call_args
        metadata = call_args.kwargs.get("metadata", {})
        assert "local_file_hash" in metadata
        assert metadata["local_file_hash"] != "skipped"


class TestIdempotentUploaderIntegration:
    """Integration tests for IdempotentUploader."""

    def test_upload_workflow_complete(self):
        """Test complete upload workflow."""
        mock_s3_client = Mock()
        mock_s3_client.audio_bucket_name = "klatu-audio"
        mock_s3_client.file_exists = Mock(return_value=False)
        mock_s3_client.upload_audio = Mock()
        mock_s3_client.audio_client = Mock()
        mock_s3_client._resolve = Mock(
            return_value=(mock_s3_client.audio_client, mock_s3_client.audio_bucket_name)
        )

        uploader = IdempotentUploader(mock_s3_client)

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(b"test audio data")
            temp_path = f.name

        try:
            # Step 1: Upload file
            result = uploader.upload_with_retry(
                job_id="job-123",
                local_path=temp_path,
                remote_path="tts-audio/studio/job-123.mp3",
                bucket_type="audio",
            )

            assert result == "tts-audio/studio/job-123.mp3"

            # Step 2: Verify upload
            mock_s3_client.file_exists.return_value = True
            verified = uploader.verify_upload("job-123", "tts-audio/studio/job-123.mp3")

            assert verified is True

            # Step 3: Mark as complete
            completed = uploader.mark_upload_complete(
                "job-123", "tts-audio/studio/job-123.mp3"
            )

            assert completed is True

        finally:
            os.remove(temp_path)

    def test_idempotent_retry_workflow(self):
        """Test idempotent retry on second upload attempt."""
        mock_s3_client = Mock()
        mock_s3_client.audio_bucket_name = "klatu-audio"
        mock_s3_client.audio_client = Mock()
        mock_s3_client._resolve = Mock(
            return_value=(mock_s3_client.audio_client, mock_s3_client.audio_bucket_name)
        )

        uploader = IdempotentUploader(mock_s3_client)

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(b"test audio data")
            temp_path = f.name

        try:
            # First upload - file doesn't exist yet
            mock_s3_client.file_exists.return_value = False
            mock_s3_client.upload_audio.side_effect = None

            result1 = uploader.upload_with_retry(
                job_id="job-123",
                local_path=temp_path,
                remote_path="tts-audio/studio/job-123.mp3",
                bucket_type="audio",
            )

            assert result1 == "tts-audio/studio/job-123.mp3"
            assert mock_s3_client.upload_audio.call_count == 1

            # Second upload - file already exists (idempotent)
            existing_metadata = UploadMetadata(
                job_id="job-123",
                status="uploaded",
            )
            uploader._check_existing_upload = Mock(return_value=existing_metadata)

            result2 = uploader.upload_with_retry(
                job_id="job-123",
                local_path=temp_path,
                remote_path="tts-audio/studio/job-123.mp3",
                bucket_type="audio",
            )

            assert result2 == "tts-audio/studio/job-123.mp3"
            # Should not have called upload again
            assert mock_s3_client.upload_audio.call_count == 1

        finally:
            os.remove(temp_path)
