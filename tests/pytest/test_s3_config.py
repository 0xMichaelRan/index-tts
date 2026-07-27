"""
Unit tests for S3 configuration module.

Tests cover:
- S3Client initialization and validation
- Configuration loading from environment variables
- Path validation and traversal prevention
- File operations (upload, download, delete, list)
- Presigned URL generation
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


class TestS3ClientInitialization:
    """Test S3Client initialization and configuration."""
    
    def test_init_with_environment_variables(self):
        """Test S3Client initialization from environment variables."""
        with patch.dict(os.environ, {
            "S3_ENDPOINT_URL": "https://example.supabase.co/storage/v1/s3",
            "S3_ACCESS_KEY_ID": "test_key",
            "S3_SECRET_ACCESS_KEY": "test_secret",
            "S3_BUCKET_NAME": "test_bucket",
            "S3_REGION": "us-east-1",
        }):
            with patch("services.s3_config.boto3"):
                client = S3Client()
                assert client.endpoint_url == "https://example.supabase.co/storage/v1/s3"
                assert client.access_key_id == "test_key"
                assert client.secret_access_key == "test_secret"
                assert client.bucket_name == "test_bucket"
    
    def test_init_with_explicit_parameters(self):
        """Test S3Client initialization with explicit parameters."""
        with patch("services.s3_config.boto3"):
            client = S3Client(
                endpoint_url="https://example.supabase.co/storage/v1/s3",
                access_key_id="test_key",
                secret_access_key="test_secret",
                bucket_name="test_bucket",
                region="us-west-2",
                max_retries=5,
            )
            assert client.endpoint_url == "https://example.supabase.co/storage/v1/s3"
            assert client.region == "us-west-2"
            assert client.max_retries == 5
    
    def test_init_missing_required_configuration(self):
        """Test S3Client raises error when required configuration is missing."""
        with patch.dict(os.environ, {}, clear=True):
            with patch("services.s3_config.BOTO3_AVAILABLE", True):
                with pytest.raises(S3ConfigError) as exc_info:
                    S3Client()
                assert "Missing required S3 configuration" in str(exc_info.value)
    
    def test_init_boto3_not_available(self):
        """Test S3Client raises error when boto3 is not available."""
        with patch("services.s3_config.BOTO3_AVAILABLE", False):
            with pytest.raises(ImportError) as exc_info:
                S3Client()
            assert "boto3 is required" in str(exc_info.value)


class TestPathValidation:
    """Test S3 path validation and security."""
    
    @pytest.fixture
    def s3_client(self):
        """Create S3Client instance for testing."""
        with patch("services.s3_config.boto3"):
            return S3Client(
                endpoint_url="https://example.supabase.co/storage/v1/s3",
                access_key_id="test_key",
                secret_access_key="test_secret",
                bucket_name="test_bucket",
            )
    
    def test_validate_path_valid_audio_prompts(self, s3_client):
        """Test validation of valid audio-prompts path."""
        assert s3_client.validate_path("audio-prompts/voice_123.wav") is True
    
    def test_validate_path_valid_studio_output(self, s3_client):
        """Test validation of valid studio output path."""
        assert s3_client.validate_path("tts-output/studio/job_456.wav") is True
    
    def test_validate_path_valid_playground_output(self, s3_client):
        """Test validation of valid playground output path."""
        assert s3_client.validate_path("tts-output/playground/job_789.wav") is True
    
    def test_validate_path_path_traversal_detected(self, s3_client):
        """Test that path traversal attempts are blocked."""
        with pytest.raises(ValueError) as exc_info:
            s3_client.validate_path("../etc/passwd")
        assert "path traversal" in str(exc_info.value).lower()
    
    def test_validate_path_leading_slash(self, s3_client):
        """Test that paths with leading slashes are rejected."""
        with pytest.raises(ValueError) as exc_info:
            s3_client.validate_path("/audio-prompts/voice_123.wav")
        assert "invalid path" in str(exc_info.value).lower()
    
    def test_validate_path_invalid_prefix(self, s3_client):
        """Test that paths with invalid prefixes are rejected."""
        with pytest.raises(ValueError) as exc_info:
            s3_client.validate_path("invalid/path.wav")
        assert "invalid path prefix" in str(exc_info.value).lower()


class TestFileOperations:
    """Test S3 file operations."""
    
    @pytest.fixture
    def s3_client(self):
        """Create S3Client instance with mocked boto3."""
        with patch("services.s3_config.boto3"):
            client = S3Client(
                endpoint_url="https://example.supabase.co/storage/v1/s3",
                access_key_id="test_key",
                secret_access_key="test_secret",
                bucket_name="test_bucket",
            )
            client.client = MagicMock()
            return client
    
    def test_upload_file_success(self, s3_client):
        """Test successful file upload."""
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"test content")
            tmp_path = tmp.name
        
        try:
            result = s3_client.upload_file(
                local_path=tmp_path,
                remote_path="audio-prompts/voice_123.wav",
                content_type="audio/wav",
            )
            
            assert result == "audio-prompts/voice_123.wav"
            s3_client.client.upload_file.assert_called_once()
        finally:
            os.unlink(tmp_path)
    
    def test_upload_file_not_found(self, s3_client):
        """Test upload fails when local file doesn't exist."""
        with pytest.raises(FileNotFoundError):
            s3_client.upload_file(
                local_path="/nonexistent/file.wav",
                remote_path="audio-prompts/voice_123.wav",
            )
    
    def test_upload_file_with_metadata(self, s3_client):
        """Test file upload includes metadata."""
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"test content")
            tmp_path = tmp.name
        
        try:
            metadata = {"job_id": "job_456", "language": "en"}
            s3_client.upload_file(
                local_path=tmp_path,
                remote_path="audio-prompts/voice_123.wav",
                metadata=metadata,
            )
            
            # Verify metadata was passed
            call_args = s3_client.client.upload_file.call_args
            assert call_args[1]["ExtraArgs"]["Metadata"] == metadata
        finally:
            os.unlink(tmp_path)
    
    def test_download_file_success(self, s3_client):
        """Test successful file download."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            local_path = os.path.join(tmp_dir, "downloaded.wav")
            
            result = s3_client.download_file(
                remote_path="audio-prompts/voice_123.wav",
                local_path=local_path,
            )
            
            assert result == local_path
            s3_client.client.download_file.assert_called_once()
    
    def test_download_file_with_retry(self, s3_client):
        """Test download retries on failure."""
        s3_client.max_retries = 2
        
        # Simulate failure then success
        s3_client.client.download_file.side_effect = [
            ClientError({"Error": {"Code": "500"}}, "download_file"),
            None,
        ]
        
        with tempfile.TemporaryDirectory() as tmp_dir:
            local_path = os.path.join(tmp_dir, "downloaded.wav")
            
            # Should succeed after retry
            result = s3_client.download_file(
                remote_path="audio-prompts/voice_123.wav",
                local_path=local_path,
                max_retries=2,
            )
            
            assert result == local_path
            assert s3_client.client.download_file.call_count == 2
    
    def test_file_exists_true(self, s3_client):
        """Test file_exists returns True for existing file."""
        result = s3_client.file_exists("audio-prompts/voice_123.wav")
        assert result is True
        s3_client.client.head_object.assert_called_once()
    
    def test_file_exists_false(self, s3_client):
        """Test file_exists returns False for nonexistent file."""
        s3_client.client.head_object.side_effect = ClientError(
            {"Error": {"Code": "404"}}, "head_object"
        )
        
        result = s3_client.file_exists("audio-prompts/nonexistent.wav")
        assert result is False
    
    def test_delete_file_success(self, s3_client):
        """Test successful file deletion."""
        result = s3_client.delete_file("audio-prompts/voice_123.wav")
        assert result is True
        s3_client.client.delete_object.assert_called_once()
    
    def test_delete_file_failure(self, s3_client):
        """Test delete_file raises error on failure."""
        s3_client.client.delete_object.side_effect = ClientError(
            {"Error": {"Code": "500"}}, "delete_object"
        )
        
        with pytest.raises(S3ConfigError):
            s3_client.delete_file("audio-prompts/voice_123.wav")


class TestPresignedURLs:
    """Test presigned URL generation."""
    
    @pytest.fixture
    def s3_client(self):
        """Create S3Client instance with mocked boto3."""
        with patch("services.s3_config.boto3"):
            client = S3Client(
                endpoint_url="https://example.supabase.co/storage/v1/s3",
                access_key_id="test_key",
                secret_access_key="test_secret",
                bucket_name="test_bucket",
            )
            client.client = MagicMock()
            return client
    
    def test_generate_presigned_url_get(self, s3_client):
        """Test presigned GET URL generation."""
        expected_url = "https://example.com/presigned-get-url"
        s3_client.client.generate_presigned_url.return_value = expected_url
        
        url = s3_client.generate_presigned_url(
            remote_path="audio-prompts/voice_123.wav",
            http_method="GET",
            expiration=3600,
        )
        
        assert url == expected_url
        s3_client.client.generate_presigned_url.assert_called_once()
    
    def test_generate_presigned_url_put(self, s3_client):
        """Test presigned PUT URL generation."""
        expected_url = "https://example.com/presigned-put-url"
        s3_client.client.generate_presigned_url.return_value = expected_url
        
        url = s3_client.generate_presigned_url(
            remote_path="audio-prompts/voice_123.wav",
            http_method="PUT",
            expiration=1800,
        )
        
        assert url == expected_url
    
    def test_generate_presigned_url_default_expiration(self, s3_client):
        """Test presigned URL uses default expiration."""
        s3_client.client.generate_presigned_url.return_value = "https://example.com/url"
        
        s3_client.generate_presigned_url("audio-prompts/voice_123.wav")
        
        # Check that ExpiresIn was passed correctly
        call_args = s3_client.client.generate_presigned_url.call_args
        assert call_args[1]["ExpiresIn"] == 3600  # Default 1 hour


class TestListFiles:
    """Test file listing functionality."""
    
    @pytest.fixture
    def s3_client(self):
        """Create S3Client instance with mocked boto3."""
        with patch("services.s3_config.boto3"):
            client = S3Client(
                endpoint_url="https://example.supabase.co/storage/v1/s3",
                access_key_id="test_key",
                secret_access_key="test_secret",
                bucket_name="test_bucket",
            )
            client.client = MagicMock()
            return client
    
    def test_list_files_with_prefix(self, s3_client):
        """Test listing files with prefix."""
        s3_client.client.list_objects_v2.return_value = {
            "Contents": [
                {"Key": "audio-prompts/voice_123.wav"},
                {"Key": "audio-prompts/voice_456.wav"},
            ]
        }
        
        files = s3_client.list_files("audio-prompts/")
        
        assert len(files) == 2
        assert "audio-prompts/voice_123.wav" in files
        assert "audio-prompts/voice_456.wav" in files
    
    def test_list_files_empty(self, s3_client):
        """Test listing files when prefix has no objects."""
        s3_client.client.list_objects_v2.return_value = {}
        
        files = s3_client.list_files("nonexistent/")
        
        assert files == []
    
    def test_list_files_with_max_keys(self, s3_client):
        """Test listing files with max_keys parameter."""
        s3_client.client.list_objects_v2.return_value = {
            "Contents": [
                {"Key": "audio-prompts/voice_123.wav"},
            ]
        }
        
        s3_client.list_files("audio-prompts/", max_keys=100)
        
        call_args = s3_client.client.list_objects_v2.call_args
        assert call_args[1]["MaxKeys"] == 100


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
    """Test audio-specific upload functionality."""
    
    @pytest.fixture
    def s3_client(self):
        """Create S3Client instance with mocked boto3."""
        with patch("services.s3_config.boto3"):
            client = S3Client(
                endpoint_url="https://example.supabase.co/storage/v1/s3",
                access_key_id="test_key",
                secret_access_key="test_secret",
                bucket_name="test_bucket",
            )
            client.client = MagicMock()
            return client
    
    def test_upload_audio_with_job_id(self, s3_client):
        """Test audio upload includes job_id in metadata."""
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"audio data")
            tmp_path = tmp.name
        
        try:
            result = s3_client.upload_audio(
                local_path=tmp_path,
                remote_path="tts-output/studio/job_123.wav",
                job_id="job_123",
            )
            
            assert result == "tts-output/studio/job_123.wav"
            
            # Verify job_id was added to metadata
            call_args = s3_client.client.upload_file.call_args
            assert call_args[1]["ExtraArgs"]["Metadata"]["job_id"] == "job_123"
        finally:
            os.unlink(tmp_path)
    
    def test_upload_audio_sets_correct_content_type(self, s3_client):
        """Test audio upload sets audio/wav content type."""
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"audio data")
            tmp_path = tmp.name
        
        try:
            s3_client.upload_audio(
                local_path=tmp_path,
                remote_path="tts-output/studio/job_123.wav",
            )
            
            # Verify content type is set to audio/wav
            call_args = s3_client.client.upload_file.call_args
            assert call_args[1]["ExtraArgs"]["ContentType"] == "audio/wav"
        finally:
            os.unlink(tmp_path)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
