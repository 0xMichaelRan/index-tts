"""
Unit tests for S3 configuration module with dual-bucket support.

Tests cover:
- S3Client initialization and validation (dual-bucket mode only)
- Configuration loading from environment variables
- Path validation and traversal prevention
- File operations for both storage and output buckets (upload, download, delete, list)
- Presigned URL generation for both bucket types
- Error handling and retry logic
"""

import pytest
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock, call
from botocore.exceptions import ClientError, BotoCoreError

from services.s3_config import (
    S3Client,
    S3ConfigError,
    configure_bucket_structure,
    PATH_STRUCTURE,
    LIFECYCLE_RULES,
)


# Helper function to create S3Client with dual-bucket config
def create_test_client():
    """Create S3Client for testing with dual-bucket configuration."""
    with patch("services.s3_config.boto3"):
        client = S3Client(
            storage_endpoint_url="https://storage.supabase.co/storage/v1/s3",
            storage_access_key_id="storage_key",
            storage_secret_access_key="storage_secret",
            storage_bucket_name="voice-library",
            output_endpoint_url="https://output.supabase.co/storage/v1/s3",
            output_access_key_id="output_key",
            output_secret_access_key="output_secret",
            output_bucket_name="tts-output",
        )
        # Mock both clients
        client.storage_client = MagicMock()
        client.output_client = MagicMock()
        return client


class TestS3ClientInitialization:
    """Test S3Client initialization and configuration."""
    
    def test_init_with_environment_variables(self):
        """Test S3Client initialization from environment variables."""
        with patch.dict(os.environ, {
            "S3_STORAGE_ENDPOINT_URL": "https://storage.supabase.co/storage/v1/s3",
            "S3_STORAGE_ACCESS_KEY_ID": "storage_key",
            "S3_STORAGE_SECRET_ACCESS_KEY": "storage_secret",
            "S3_STORAGE_BUCKET_NAME": "voice-library",
            "S3_STORAGE_REGION": "ap-southeast-1",
            "S3_OUTPUT_ENDPOINT_URL": "https://output.supabase.co/storage/v1/s3",
            "S3_OUTPUT_ACCESS_KEY_ID": "output_key",
            "S3_OUTPUT_SECRET_ACCESS_KEY": "output_secret",
            "S3_OUTPUT_BUCKET_NAME": "tts-output",
            "S3_OUTPUT_REGION": "us-east-1",
        }):
            with patch("services.s3_config.boto3"):
                client = S3Client()
                assert client.storage_endpoint_url == "https://storage.supabase.co/storage/v1/s3"
                assert client.storage_access_key_id == "storage_key"
                assert client.storage_bucket_name == "voice-library"
                assert client.output_endpoint_url == "https://output.supabase.co/storage/v1/s3"
                assert client.output_access_key_id == "output_key"
                assert client.output_bucket_name == "tts-output"
    
    def test_init_with_explicit_parameters(self):
        """Test S3Client initialization with explicit parameters."""
        with patch("services.s3_config.boto3"):
            client = S3Client(
                storage_endpoint_url="https://storage.supabase.co/storage/v1/s3",
                storage_access_key_id="storage_key",
                storage_secret_access_key="storage_secret",
                storage_bucket_name="voice-library",
                storage_region="ap-southeast-1",
                output_endpoint_url="https://output.supabase.co/storage/v1/s3",
                output_access_key_id="output_key",
                output_secret_access_key="output_secret",
                output_bucket_name="tts-output",
                output_region="us-east-1",
                max_retries=5,
            )
            assert client.storage_endpoint_url == "https://storage.supabase.co/storage/v1/s3"
            assert client.storage_region == "ap-southeast-1"
            assert client.output_region == "us-east-1"
            assert client.max_retries == 5
    
    def test_init_missing_storage_configuration(self):
        """Test S3Client raises error when storage bucket config is missing."""
        with patch.dict(os.environ, {
            "S3_OUTPUT_ENDPOINT_URL": "https://output.supabase.co/storage/v1/s3",
            "S3_OUTPUT_ACCESS_KEY_ID": "output_key",
            "S3_OUTPUT_SECRET_ACCESS_KEY": "output_secret",
            "S3_OUTPUT_BUCKET_NAME": "tts-output",
        }, clear=True):
            with patch("services.s3_config.BOTO3_AVAILABLE", True):
                with pytest.raises(S3ConfigError) as exc_info:
                    S3Client()
                assert "S3_STORAGE_" in str(exc_info.value)
    
    def test_init_missing_output_configuration(self):
        """Test S3Client raises error when output bucket config is missing."""
        with patch.dict(os.environ, {
            "S3_STORAGE_ENDPOINT_URL": "https://storage.supabase.co/storage/v1/s3",
            "S3_STORAGE_ACCESS_KEY_ID": "storage_key",
            "S3_STORAGE_SECRET_ACCESS_KEY": "storage_secret",
            "S3_STORAGE_BUCKET_NAME": "voice-library",
        }, clear=True):
            with patch("services.s3_config.BOTO3_AVAILABLE", True):
                with pytest.raises(S3ConfigError) as exc_info:
                    S3Client()
                assert "S3_OUTPUT_" in str(exc_info.value)
    
    def test_init_boto3_not_available(self):
        """Test S3Client raises error when boto3 is not available."""
        with patch("services.s3_config.BOTO3_AVAILABLE", False):
            with pytest.raises(ImportError) as exc_info:
                S3Client()
            assert "boto3 is required" in str(exc_info.value)


class TestPathValidation:
    """Test S3 path validation and security."""
    
    def test_validate_path_valid_audio_prompts(self):
        """Test validation of valid audio-prompts path."""
        client = create_test_client()
        assert client.validate_path("audio-prompts/voice_123.wav") is True
    
    def test_validate_path_valid_studio_output(self):
        """Test validation of valid studio output path."""
        client = create_test_client()
        assert client.validate_path("tts-output/studio/job_456.wav") is True
    
    def test_validate_path_valid_playground_output(self):
        """Test validation of valid playground output path."""
        client = create_test_client()
        assert client.validate_path("tts-output/playground/job_789.wav") is True
    
    def test_validate_path_path_traversal_detected(self):
        """Test that path traversal attempts are blocked."""
        client = create_test_client()
        with pytest.raises(ValueError) as exc_info:
            client.validate_path("../etc/passwd")
        assert "path traversal" in str(exc_info.value).lower()
    
    def test_validate_path_leading_slash(self):
        """Test that paths with leading slashes are rejected."""
        client = create_test_client()
        with pytest.raises(ValueError) as exc_info:
            client.validate_path("/audio-prompts/voice_123.wav")
        assert "invalid path" in str(exc_info.value).lower()
    
    def test_validate_path_invalid_prefix(self):
        """Test that paths with invalid prefixes are rejected."""
        client = create_test_client()
        with pytest.raises(ValueError) as exc_info:
            client.validate_path("invalid/path.wav")
        assert "invalid path prefix" in str(exc_info.value).lower()


class TestFileOperations:
    """Test S3 file operations for both storage and output buckets."""
    
    def test_upload_file_to_storage_bucket(self):
        """Test successful file upload to storage bucket."""
        client = create_test_client()
        
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"test content")
            tmp_path = tmp.name
        
        try:
            result = client.upload_file(
                local_path=tmp_path,
                remote_path="audio-prompts/voice_123.wav",
                bucket_type="storage",
                content_type="audio/wav",
            )
            
            assert result == "audio-prompts/voice_123.wav"
            client.storage_client.upload_file.assert_called_once()
        finally:
            os.unlink(tmp_path)
    
    def test_upload_file_to_output_bucket(self):
        """Test successful file upload to output bucket."""
        client = create_test_client()
        
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"test content")
            tmp_path = tmp.name
        
        try:
            result = client.upload_file(
                local_path=tmp_path,
                remote_path="tts-output/studio/job_123.wav",
                bucket_type="output",
                content_type="audio/wav",
            )
            
            assert result == "tts-output/studio/job_123.wav"
            client.output_client.upload_file.assert_called_once()
        finally:
            os.unlink(tmp_path)
    
    def test_upload_file_not_found(self):
        """Test upload fails when local file doesn't exist."""
        client = create_test_client()
        with pytest.raises(FileNotFoundError):
            client.upload_file(
                local_path="/nonexistent/file.wav",
                remote_path="audio-prompts/voice_123.wav",
                bucket_type="storage",
            )
    
    def test_upload_file_with_metadata(self):
        """Test file upload includes metadata."""
        client = create_test_client()
        
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"test content")
            tmp_path = tmp.name
        
        try:
            metadata = {"job_id": "job_456", "language": "en"}
            client.upload_file(
                local_path=tmp_path,
                remote_path="tts-output/studio/job_456.wav",
                bucket_type="output",
                metadata=metadata,
            )
            
            # Verify metadata was passed
            call_args = client.output_client.upload_file.call_args
            assert call_args[1]["ExtraArgs"]["Metadata"] == metadata
        finally:
            os.unlink(tmp_path)
    
    def test_download_file_from_storage_bucket(self):
        """Test successful file download from storage bucket."""
        client = create_test_client()
        
        with tempfile.TemporaryDirectory() as tmp_dir:
            local_path = os.path.join(tmp_dir, "downloaded.wav")
            
            result = client.download_file(
                remote_path="audio-prompts/voice_123.wav",
                local_path=local_path,
                bucket_type="storage",
            )
            
            assert result == local_path
            client.storage_client.download_file.assert_called_once()
    
    def test_download_file_from_output_bucket(self):
        """Test successful file download from output bucket."""
        client = create_test_client()
        
        with tempfile.TemporaryDirectory() as tmp_dir:
            local_path = os.path.join(tmp_dir, "downloaded.wav")
            
            result = client.download_file(
                remote_path="tts-output/studio/job_123.wav",
                local_path=local_path,
                bucket_type="output",
            )
            
            assert result == local_path
            client.output_client.download_file.assert_called_once()
    
    def test_download_file_with_retry(self):
        """Test download retries on failure."""
        client = create_test_client()
        client.max_retries = 2
        
        # Simulate failure then success
        client.storage_client.download_file.side_effect = [
            ClientError({"Error": {"Code": "500"}}, "download_file"),
            None,
        ]
        
        with tempfile.TemporaryDirectory() as tmp_dir:
            local_path = os.path.join(tmp_dir, "downloaded.wav")
            
            # Should succeed after retry
            result = client.download_file(
                remote_path="audio-prompts/voice_123.wav",
                local_path=local_path,
                bucket_type="storage",
                max_retries=2,
            )
            
            assert result == local_path
            assert client.storage_client.download_file.call_count == 2
    
    def test_file_exists_in_storage_bucket(self):
        """Test file_exists returns True for existing file in storage bucket."""
        client = create_test_client()
        result = client.file_exists("audio-prompts/voice_123.wav", bucket_type="storage")
        assert result is True
        client.storage_client.head_object.assert_called_once()
    
    def test_file_exists_in_output_bucket(self):
        """Test file_exists returns True for existing file in output bucket."""
        client = create_test_client()
        result = client.file_exists("tts-output/studio/job_123.wav", bucket_type="output")
        assert result is True
        client.output_client.head_object.assert_called_once()
    
    def test_file_exists_false(self):
        """Test file_exists returns False for nonexistent file."""
        client = create_test_client()
        client.storage_client.head_object.side_effect = ClientError(
            {"Error": {"Code": "404"}}, "head_object"
        )
        
        result = client.file_exists("audio-prompts/nonexistent.wav", bucket_type="storage")
        assert result is False
    
    def test_delete_file_from_storage_bucket(self):
        """Test successful file deletion from storage bucket."""
        client = create_test_client()
        result = client.delete_file("audio-prompts/voice_123.wav", bucket_type="storage")
        assert result is True
        client.storage_client.delete_object.assert_called_once()
    
    def test_delete_file_from_output_bucket(self):
        """Test successful file deletion from output bucket."""
        client = create_test_client()
        result = client.delete_file("tts-output/studio/job_123.wav", bucket_type="output")
        assert result is True
        client.output_client.delete_object.assert_called_once()
    
    def test_delete_file_failure(self):
        """Test delete_file raises error on failure."""
        client = create_test_client()
        client.output_client.delete_object.side_effect = ClientError(
            {"Error": {"Code": "500"}}, "delete_object"
        )
        
        with pytest.raises(S3ConfigError):
            client.delete_file("tts-output/studio/job_123.wav", bucket_type="output")


class TestPresignedURLs:
    """Test presigned URL generation for both bucket types."""
    
    def test_generate_presigned_url_storage_bucket(self):
        """Test presigned GET URL generation for storage bucket."""
        client = create_test_client()
        expected_url = "https://example.com/presigned-storage-url"
        client.storage_client.generate_presigned_url.return_value = expected_url
        
        url = client.generate_presigned_url(
            remote_path="audio-prompts/voice_123.wav",
            bucket_type="storage",
            http_method="GET",
            expiration=3600,
        )
        
        assert url == expected_url
        client.storage_client.generate_presigned_url.assert_called_once()
    
    def test_generate_presigned_url_output_bucket(self):
        """Test presigned GET URL generation for output bucket."""
        client = create_test_client()
        expected_url = "https://example.com/presigned-output-url"
        client.output_client.generate_presigned_url.return_value = expected_url
        
        url = client.generate_presigned_url(
            remote_path="tts-output/studio/job_123.wav",
            bucket_type="output",
            http_method="GET",
            expiration=3600,
        )
        
        assert url == expected_url
        client.output_client.generate_presigned_url.assert_called_once()
    
    def test_generate_presigned_url_put(self):
        """Test presigned PUT URL generation."""
        client = create_test_client()
        expected_url = "https://example.com/presigned-put-url"
        client.output_client.generate_presigned_url.return_value = expected_url
        
        url = client.generate_presigned_url(
            remote_path="tts-output/studio/job_123.wav",
            bucket_type="output",
            http_method="PUT",
            expiration=1800,
        )
        
        assert url == expected_url


class TestListFiles:
    """Test file listing functionality for both bucket types."""
    
    def test_list_files_storage_bucket(self):
        """Test listing files in storage bucket."""
        client = create_test_client()
        client.storage_client.list_objects_v2.return_value = {
            "Contents": [
                {"Key": "audio-prompts/voice_123.wav"},
                {"Key": "audio-prompts/voice_456.wav"},
            ]
        }
        
        files = client.list_files("audio-prompts/", bucket_type="storage")
        
        assert len(files) == 2
        assert "audio-prompts/voice_123.wav" in files
        assert "audio-prompts/voice_456.wav" in files
    
    def test_list_files_output_bucket(self):
        """Test listing files in output bucket."""
        client = create_test_client()
        client.output_client.list_objects_v2.return_value = {
            "Contents": [
                {"Key": "tts-output/studio/job_123.wav"},
                {"Key": "tts-output/studio/job_456.wav"},
            ]
        }
        
        files = client.list_files("tts-output/studio/", bucket_type="output")
        
        assert len(files) == 2
        assert "tts-output/studio/job_123.wav" in files
    
    def test_list_files_empty(self):
        """Test listing files when prefix has no objects."""
        client = create_test_client()
        client.storage_client.list_objects_v2.return_value = {}
        
        files = client.list_files("nonexistent/", bucket_type="storage")
        
        assert files == []


class TestContentTypeDetection:
    """Test automatic content type detection."""
    
    def test_get_content_type_wav(self):
        """Test WAV content type detection."""
        content_type = S3Client._get_content_type("audio.wav")
        assert content_type == "audio/wav"
    
    def test_get_content_type_mp3(self):
        """Test MP3 content type detection."""
        content_type = S3Client._get_content_type("audio.mp3")
        assert content_type == "audio/mpeg"
    
    def test_get_content_type_json(self):
        """Test JSON content type detection."""
        content_type = S3Client._get_content_type("metadata.json")
        assert content_type == "application/json"
    
    def test_get_content_type_unknown(self):
        """Test unknown file type defaults to octet-stream."""
        content_type = S3Client._get_content_type("file.xyz")
        assert content_type == "application/octet-stream"


class TestPathStructure:
    """Test path structure constants."""
    
    def test_path_structure_defined(self):
        """Test that path structure is properly defined."""
        assert "audio_prompts" in PATH_STRUCTURE
        assert "tts_output_studio" in PATH_STRUCTURE
        assert "tts_output_playground" in PATH_STRUCTURE
        assert "logs_worker" in PATH_STRUCTURE
        assert "logs_backend" in PATH_STRUCTURE
    
    def test_lifecycle_rules_defined(self):
        """Test that lifecycle rules are properly defined."""
        assert "playground_cleanup" in LIFECYCLE_RULES
        assert "logs_archival" in LIFECYCLE_RULES
        
        # Verify playground cleanup rule
        pg_rule = LIFECYCLE_RULES["playground_cleanup"]
        assert pg_rule["prefix"] == "tts-output/playground/"
        assert pg_rule["expiration_days"] == 1


class TestErrorHandling:
    """Test error handling and S3ConfigError."""
    
    def test_s3_config_error_is_exception(self):
        """Test that S3ConfigError is an Exception."""
        assert issubclass(S3ConfigError, Exception)
    
    def test_s3_config_error_message(self):
        """Test S3ConfigError message."""
        error = S3ConfigError("Test error message")
        assert str(error) == "Test error message"


class TestUploadAudio:
    """Test audio-specific upload functionality for both buckets."""
    
    def test_upload_audio_to_output_bucket_with_job_id(self):
        """Test audio upload to output bucket includes job_id in metadata."""
        client = create_test_client()
        
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"audio data")
            tmp_path = tmp.name
        
        try:
            result = client.upload_audio(
                local_path=tmp_path,
                remote_path="tts-output/studio/job_123.wav",
                bucket_type="output",
                job_id="job_123",
            )
            
            assert result == "tts-output/studio/job_123.wav"
            
            # Verify job_id was added to metadata
            call_args = client.output_client.upload_file.call_args
            assert call_args[1]["ExtraArgs"]["Metadata"]["job_id"] == "job_123"
        finally:
            os.unlink(tmp_path)
    
    def test_upload_audio_to_storage_bucket(self):
        """Test audio upload to storage bucket (voice recordings)."""
        client = create_test_client()
        
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"audio data")
            tmp_path = tmp.name
        
        try:
            result = client.upload_audio(
                local_path=tmp_path,
                remote_path="audio-prompts/voice_123.wav",
                bucket_type="storage",
            )
            
            assert result == "audio-prompts/voice_123.wav"
            
            # Verify content type is set to audio/wav
            call_args = client.storage_client.upload_file.call_args
            assert call_args[1]["ExtraArgs"]["ContentType"] == "audio/wav"
        finally:
            os.unlink(tmp_path)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
