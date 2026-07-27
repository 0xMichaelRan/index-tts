"""
Unit tests for S3 configuration module.

Tests S3 client initialization, upload/download operations, path validation,
and error handling scenarios.
"""

import os
import pytest
import tempfile
from unittest.mock import Mock, patch, MagicMock, call
from services.s3_config import (
    S3Client,
    S3ConfigError,
    configure_bucket_structure,
    PATH_STRUCTURE,
    LIFECYCLE_RULES,
)


class TestS3ClientInitialization:
    """Test S3 client initialization and configuration."""

    @patch("services.s3_config.BOTO3_AVAILABLE", True)
    @patch("services.s3_config.boto3")
    def test_init_with_parameters(self, mock_boto3):
        """Test initialization with explicit parameters."""
        mock_client = Mock()
        mock_boto3.client.return_value = mock_client
        
        client = S3Client(
            endpoint_url="https://test.supabase.co/storage/v1/s3",
            access_key_id="test_key",
            secret_access_key="test_secret",
            bucket_name="test_bucket",
            region="ap-southeast-1",
            use_ssl=True,
        )
        
        assert client.endpoint_url == "https://test.supabase.co/storage/v1/s3"
        assert client.access_key_id == "test_key"
        assert client.bucket_name == "test_bucket"
        assert client.region == "ap-southeast-1"
        assert client.use_ssl is True
        
        mock_boto3.client.assert_called_once()

    @patch("services.s3_config.BOTO3_AVAILABLE", True)
    @patch("services.s3_config.boto3")
    def test_init_from_environment(self, mock_boto3, monkeypatch):
        """Test initialization from environment variables."""
        monkeypatch.setenv("S3_ENDPOINT_URL", "https://env.supabase.co/storage/v1/s3")
        monkeypatch.setenv("S3_ACCESS_KEY_ID", "env_key")
        monkeypatch.setenv("S3_SECRET_ACCESS_KEY", "env_secret")
        monkeypatch.setenv("S3_BUCKET_NAME", "env_bucket")
        monkeypatch.setenv("S3_REGION", "us-east-1")
        monkeypatch.setenv("S3_USE_SSL", "true")
        
        mock_client = Mock()
        mock_boto3.client.return_value = mock_client
        
        client = S3Client()
        
        assert client.endpoint_url == "https://env.supabase.co/storage/v1/s3"
        assert client.access_key_id == "env_key"
        assert client.bucket_name == "env_bucket"
        assert client.region == "us-east-1"
        assert client.use_ssl is True

    @patch("services.s3_config.BOTO3_AVAILABLE", False)
    def test_init_boto3_not_installed(self):
        """Test error when boto3 is not installed."""
        with pytest.raises(ImportError, match="boto3 is required"):
            S3Client(
                endpoint_url="https://test.supabase.co",
                access_key_id="key",
                secret_access_key="secret",
                bucket_name="bucket",
            )

    @patch("services.s3_config.BOTO3_AVAILABLE", True)
    def test_init_missing_endpoint_url(self):
        """Test error when endpoint URL is missing."""
        with pytest.raises(S3ConfigError, match="Missing required S3 configuration"):
            S3Client(
                access_key_id="key",
                secret_access_key="secret",
                bucket_name="bucket",
            )

    @patch("services.s3_config.BOTO3_AVAILABLE", True)
    def test_init_missing_credentials(self):
        """Test error when credentials are missing."""
        with pytest.raises(S3ConfigError, match="S3_ACCESS_KEY_ID"):
            S3Client(
                endpoint_url="https://test.supabase.co",
                secret_access_key="secret",
                bucket_name="bucket",
            )

    @patch("services.s3_config.BOTO3_AVAILABLE", True)
    def test_init_missing_bucket_name(self):
        """Test error when bucket name is missing."""
        with pytest.raises(S3ConfigError, match="S3_BUCKET_NAME"):
            S3Client(
                endpoint_url="https://test.supabase.co",
                access_key_id="key",
                secret_access_key="secret",
            )


class TestS3FileOperations:
    """Test S3 file upload, download, and management operations."""

    @pytest.fixture
    def mock_s3_client(self):
        """Create a mock S3 client for testing."""
        with patch("services.s3_config.boto3") as mock_boto3:
            mock_client = Mock()
            mock_boto3.client.return_value = mock_client
            
            client = S3Client(
                endpoint_url="https://test.supabase.co/storage/v1/s3",
                access_key_id="test_key",
                secret_access_key="test_secret",
                bucket_name="test_bucket",
            )
            
            yield client, mock_client

    def test_upload_file_success(self, mock_s3_client):
        """Test successful file upload."""
        client, mock_client = mock_s3_client
        
        with tempfile.NamedTemporaryFile(mode="w", suffix=".wav", delete=False) as tmp:
            tmp.write("test audio data")
            tmp_path = tmp.name
        
        try:
            result = client.upload_file(
                local_path=tmp_path,
                remote_path="audio-prompts/test.wav",
                metadata={"job_id": "123"},
            )
            
            assert result == "audio-prompts/test.wav"
            mock_client.upload_file.assert_called_once()
            
            # Verify upload arguments
            call_args = mock_client.upload_file.call_args
            assert call_args[1]["Bucket"] == "test_bucket"
            assert call_args[1]["Key"] == "audio-prompts/test.wav"
            assert call_args[1]["ExtraArgs"]["ContentType"] == "audio/wav"
            assert call_args[1]["ExtraArgs"]["Metadata"]["job_id"] == "123"
            
        finally:
            os.unlink(tmp_path)

    def test_upload_file_not_found(self, mock_s3_client):
        """Test upload with non-existent file."""
        client, _ = mock_s3_client
        
        with pytest.raises(FileNotFoundError):
            client.upload_file(
                local_path="/nonexistent/file.wav",
                remote_path="audio-prompts/test.wav",
            )

    def test_upload_file_s3_error(self, mock_s3_client):
        """Test upload with S3 error."""
        client, mock_client = mock_s3_client
        
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp:
            tmp.write("test")
            tmp_path = tmp.name
        
        try:
            # Mock S3 error
            from botocore.exceptions import ClientError
            mock_client.upload_file.side_effect = ClientError(
                {"Error": {"Code": "AccessDenied", "Message": "Access denied"}},
                "upload_file"
            )
            
            with pytest.raises(S3ConfigError, match="Failed to upload"):
                client.upload_file(tmp_path, "test.wav")
                
        finally:
            os.unlink(tmp_path)

    def test_upload_audio(self, mock_s3_client):
        """Test audio-specific upload with job metadata."""
        client, mock_client = mock_s3_client
        
        with tempfile.NamedTemporaryFile(mode="w", suffix=".wav", delete=False) as tmp:
            tmp.write("audio data")
            tmp_path = tmp.name
        
        try:
            result = client.upload_audio(
                local_path=tmp_path,
                remote_path="tts-output/studio/job_456.wav",
                job_id="job_456",
                metadata={"language": "en"},
            )
            
            assert result == "tts-output/studio/job_456.wav"
            
            # Verify metadata includes job_id
            call_args = mock_client.upload_file.call_args
            metadata = call_args[1]["ExtraArgs"]["Metadata"]
            assert metadata["job_id"] == "job_456"
            assert metadata["language"] == "en"
            
        finally:
            os.unlink(tmp_path)

    def test_download_file_success(self, mock_s3_client):
        """Test successful file download."""
        client, mock_client = mock_s3_client
        
        with tempfile.TemporaryDirectory() as tmpdir:
            local_path = os.path.join(tmpdir, "downloaded.wav")
            
            result = client.download_file(
                remote_path="audio-prompts/test.wav",
                local_path=local_path,
            )
            
            assert result == local_path
            mock_client.download_file.assert_called_once()

    def test_download_file_with_retry(self, mock_s3_client):
        """Test download with retry on failure."""
        client, mock_client = mock_s3_client
        
        from botocore.exceptions import ClientError
        
        # First attempt fails, second succeeds
        mock_client.download_file.side_effect = [
            ClientError(
                {"Error": {"Code": "500", "Message": "Internal error"}},
                "download_file"
            ),
            None,  # Success on second attempt
        ]
        
        with tempfile.TemporaryDirectory() as tmpdir:
            local_path = os.path.join(tmpdir, "test.wav")
            
            with patch("services.s3_config.time.sleep"):  # Skip actual sleep
                result = client.download_file(
                    remote_path="audio-prompts/test.wav",
                    local_path=local_path,
                    max_retries=3,
                )
            
            assert result == local_path
            assert mock_client.download_file.call_count == 2

    def test_download_file_exhausted_retries(self, mock_s3_client):
        """Test download failure after exhausting retries."""
        client, mock_client = mock_s3_client
        
        from botocore.exceptions import ClientError
        
        mock_client.download_file.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not found"}},
            "download_file"
        )
        
        with tempfile.TemporaryDirectory() as tmpdir:
            local_path = os.path.join(tmpdir, "test.wav")
            
            with patch("services.s3_config.time.sleep"):
                with pytest.raises(S3ConfigError, match="Failed to download"):
                    client.download_file(
                        remote_path="nonexistent.wav",
                        local_path=local_path,
                        max_retries=2,
                    )
            
            assert mock_client.download_file.call_count == 2

    def test_file_exists_true(self, mock_s3_client):
        """Test file existence check when file exists."""
        client, mock_client = mock_s3_client
        
        mock_client.head_object.return_value = {"ContentLength": 1024}
        
        assert client.file_exists("audio-prompts/test.wav") is True
        mock_client.head_object.assert_called_once_with(
            Bucket="test_bucket",
            Key="audio-prompts/test.wav"
        )

    def test_file_exists_false(self, mock_s3_client):
        """Test file existence check when file doesn't exist."""
        client, mock_client = mock_s3_client
        
        from botocore.exceptions import ClientError
        
        mock_client.head_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not found"}},
            "head_object"
        )
        
        assert client.file_exists("nonexistent.wav") is False

    def test_file_exists_error(self, mock_s3_client):
        """Test file existence check with access error."""
        client, mock_client = mock_s3_client
        
        from botocore.exceptions import ClientError
        
        mock_client.head_object.side_effect = ClientError(
            {"Error": {"Code": "403", "Message": "Access denied"}},
            "head_object"
        )
        
        with pytest.raises(S3ConfigError, match="Error checking file existence"):
            client.file_exists("test.wav")

    def test_delete_file_success(self, mock_s3_client):
        """Test successful file deletion."""
        client, mock_client = mock_s3_client
        
        result = client.delete_file("audio-prompts/old.wav")
        
        assert result is True
        mock_client.delete_object.assert_called_once_with(
            Bucket="test_bucket",
            Key="audio-prompts/old.wav"
        )

    def test_delete_file_error(self, mock_s3_client):
        """Test file deletion with S3 error."""
        client, mock_client = mock_s3_client
        
        from botocore.exceptions import ClientError
        
        mock_client.delete_object.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Access denied"}},
            "delete_object"
        )
        
        with pytest.raises(S3ConfigError, match="Failed to delete"):
            client.delete_file("test.wav")


class TestPresignedURLs:
    """Test presigned URL generation."""

    @pytest.fixture
    def mock_s3_client(self):
        """Create a mock S3 client for testing."""
        with patch("services.s3_config.boto3") as mock_boto3:
            mock_client = Mock()
            mock_boto3.client.return_value = mock_client
            
            client = S3Client(
                endpoint_url="https://test.supabase.co/storage/v1/s3",
                access_key_id="test_key",
                secret_access_key="test_secret",
                bucket_name="test_bucket",
            )
            
            yield client, mock_client

    def test_generate_presigned_url_get(self, mock_s3_client):
        """Test presigned URL generation for GET."""
        client, mock_client = mock_s3_client
        
        mock_client.generate_presigned_url.return_value = (
            "https://test.supabase.co/test.wav?signature=abc123"
        )
        
        url = client.generate_presigned_url(
            remote_path="audio-prompts/test.wav",
            expiration=3600,
            http_method="GET",
        )
        
        assert "signature=abc123" in url
        mock_client.generate_presigned_url.assert_called_once()
        
        call_args = mock_client.generate_presigned_url.call_args
        assert call_args[1]["ClientMethod"] == "get_object"
        assert call_args[1]["ExpiresIn"] == 3600

    def test_generate_presigned_url_put(self, mock_s3_client):
        """Test presigned URL generation for PUT."""
        client, mock_client = mock_s3_client
        
        mock_client.generate_presigned_url.return_value = (
            "https://test.supabase.co/upload?signature=xyz789"
        )
        
        url = client.generate_presigned_url(
            remote_path="tts-output/studio/new.wav",
            expiration=1800,
            http_method="PUT",
        )
        
        assert "signature=xyz789" in url
        
        call_args = mock_client.generate_presigned_url.call_args
        assert call_args[1]["ClientMethod"] == "put_object"
        assert call_args[1]["ExpiresIn"] == 1800

    def test_generate_presigned_url_error(self, mock_s3_client):
        """Test presigned URL generation with error."""
        client, mock_client = mock_s3_client
        
        from botocore.exceptions import ClientError
        
        mock_client.generate_presigned_url.side_effect = ClientError(
            {"Error": {"Code": "InvalidRequest", "Message": "Invalid"}},
            "generate_presigned_url"
        )
        
        with pytest.raises(S3ConfigError, match="Failed to generate presigned URL"):
            client.generate_presigned_url("test.wav")


class TestListFiles:
    """Test S3 file listing operations."""

    @pytest.fixture
    def mock_s3_client(self):
        """Create a mock S3 client for testing."""
        with patch("services.s3_config.boto3") as mock_boto3:
            mock_client = Mock()
            mock_boto3.client.return_value = mock_client
            
            client = S3Client(
                endpoint_url="https://test.supabase.co/storage/v1/s3",
                access_key_id="test_key",
                secret_access_key="test_secret",
                bucket_name="test_bucket",
            )
            
            yield client, mock_client

    def test_list_files_success(self, mock_s3_client):
        """Test successful file listing."""
        client, mock_client = mock_s3_client
        
        mock_client.list_objects_v2.return_value = {
            "Contents": [
                {"Key": "audio-prompts/voice1.wav"},
                {"Key": "audio-prompts/voice2.wav"},
                {"Key": "audio-prompts/voice3.wav"},
            ]
        }
        
        files = client.list_files("audio-prompts/", max_keys=100)
        
        assert len(files) == 3
        assert "audio-prompts/voice1.wav" in files
        assert "audio-prompts/voice2.wav" in files

    def test_list_files_empty(self, mock_s3_client):
        """Test listing with no matching files."""
        client, mock_client = mock_s3_client
        
        mock_client.list_objects_v2.return_value = {}
        
        files = client.list_files("empty-prefix/")
        
        assert files == []

    def test_list_files_error(self, mock_s3_client):
        """Test file listing with S3 error."""
        client, mock_client = mock_s3_client
        
        from botocore.exceptions import ClientError
        
        mock_client.list_objects_v2.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Access denied"}},
            "list_objects_v2"
        )
        
        with pytest.raises(S3ConfigError, match="Failed to list files"):
            client.list_files("audio-prompts/")


class TestPathValidation:
    """Test S3 path validation."""

    @pytest.fixture
    def mock_s3_client(self):
        """Create a mock S3 client for testing."""
        with patch("services.s3_config.boto3") as mock_boto3:
            mock_client = Mock()
            mock_boto3.client.return_value = mock_client
            
            client = S3Client(
                endpoint_url="https://test.supabase.co/storage/v1/s3",
                access_key_id="test_key",
                secret_access_key="test_secret",
                bucket_name="test_bucket",
            )
            
            yield client

    def test_validate_path_success(self, mock_s3_client):
        """Test validation of valid paths."""
        client = mock_s3_client
        
        valid_paths = [
            "audio-prompts/voice123.wav",
            "tts-output/studio/job456.wav",
            "tts-output/playground/job789.wav",
            "logs/worker/2024-01-01.log",
        ]
        
        for path in valid_paths:
            assert client.validate_path(path) is True

    def test_validate_path_traversal(self, mock_s3_client):
        """Test path traversal prevention."""
        client = mock_s3_client
        
        invalid_paths = [
            "../etc/passwd",
            "audio-prompts/../secret.txt",
            "tts-output/../../root",
        ]
        
        for path in invalid_paths:
            with pytest.raises(ValueError, match="path traversal detected"):
                client.validate_path(path)

    def test_validate_path_absolute(self, mock_s3_client):
        """Test rejection of absolute paths."""
        client = mock_s3_client
        
        with pytest.raises(ValueError, match="path traversal detected"):
            client.validate_path("/absolute/path.wav")

    def test_validate_path_invalid_prefix(self, mock_s3_client):
        """Test rejection of invalid path prefixes."""
        client = mock_s3_client
        
        invalid_paths = [
            "invalid-prefix/file.wav",
            "random/path/file.txt",
            "audio/prompts/test.wav",  # Wrong separator
        ]
        
        for path in invalid_paths:
            with pytest.raises(ValueError, match="Invalid path prefix"):
                client.validate_path(path)


class TestContentTypeDetection:
    """Test automatic content type detection."""

    def test_get_content_type_audio(self):
        """Test content type detection for audio files."""
        assert S3Client._get_content_type("file.wav") == "audio/wav"
        assert S3Client._get_content_type("file.mp3") == "audio/mpeg"

    def test_get_content_type_json(self):
        """Test content type detection for JSON files."""
        assert S3Client._get_content_type("data.json") == "application/json"

    def test_get_content_type_text(self):
        """Test content type detection for text files."""
        assert S3Client._get_content_type("file.txt") == "text/plain"
        assert S3Client._get_content_type("file.log") == "text/plain"

    def test_get_content_type_unknown(self):
        """Test content type detection for unknown extensions."""
        assert S3Client._get_content_type("file.xyz") == "application/octet-stream"
        assert S3Client._get_content_type("noextension") == "application/octet-stream"


class TestConfigureBucketStructure:
    """Test bucket structure configuration."""

    @pytest.fixture
    def mock_s3_client(self):
        """Create a mock S3 client for testing."""
        with patch("services.s3_config.boto3") as mock_boto3:
            mock_client = Mock()
            mock_boto3.client.return_value = mock_client
            
            client = S3Client(
                endpoint_url="https://test.supabase.co/storage/v1/s3",
                access_key_id="test_key",
                secret_access_key="test_secret",
                bucket_name="test_bucket",
            )
            
            yield client, mock_client

    def test_configure_bucket_accessible(self, mock_s3_client):
        """Test bucket configuration with accessible bucket."""
        client, mock_client = mock_s3_client
        
        mock_client.head_bucket.return_value = {}
        
        # Should not raise any exceptions
        configure_bucket_structure(client, create_test_files=False)
        
        mock_client.head_bucket.assert_called_once_with(Bucket="test_bucket")

    def test_configure_bucket_not_found(self, mock_s3_client):
        """Test bucket configuration with non-existent bucket."""
        client, mock_client = mock_s3_client
        
        from botocore.exceptions import ClientError
        
        mock_client.head_bucket.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not found"}},
            "head_bucket"
        )
        
        with pytest.raises(S3ConfigError, match="does not exist"):
            configure_bucket_structure(client)

    def test_configure_bucket_access_denied(self, mock_s3_client):
        """Test bucket configuration with access denied."""
        client, mock_client = mock_s3_client
        
        from botocore.exceptions import ClientError
        
        mock_client.head_bucket.side_effect = ClientError(
            {"Error": {"Code": "403", "Message": "Forbidden"}},
            "head_bucket"
        )
        
        with pytest.raises(S3ConfigError, match="Access denied"):
            configure_bucket_structure(client)

    def test_configure_bucket_with_placeholders(self, mock_s3_client):
        """Test bucket configuration with placeholder file creation."""
        client, mock_client = mock_s3_client
        
        mock_client.head_bucket.return_value = {}
        mock_client.head_object.side_effect = [
            # Mock "file not exists" for all placeholders
            Exception("Not found"),
            Exception("Not found"),
            Exception("Not found"),
            Exception("Not found"),
            Exception("Not found"),
        ]
        
        configure_bucket_structure(client, create_test_files=True)
        
        # Should attempt to upload placeholder files
        assert mock_client.upload_file.call_count == 5


class TestPathStructureConstants:
    """Test path structure and lifecycle rule constants."""

    def test_path_structure_complete(self):
        """Test PATH_STRUCTURE contains all required paths."""
        required_paths = [
            "audio_prompts",
            "tts_output_studio",
            "tts_output_playground",
            "logs_worker",
            "logs_backend",
        ]
        
        for path_key in required_paths:
            assert path_key in PATH_STRUCTURE

    def test_lifecycle_rules_defined(self):
        """Test lifecycle rules are properly defined."""
        assert "playground_cleanup" in LIFECYCLE_RULES
        assert "logs_archival" in LIFECYCLE_RULES
        
        playground_rule = LIFECYCLE_RULES["playground_cleanup"]
        assert playground_rule["prefix"] == "tts-output/playground/"
        assert playground_rule["expiration_days"] == 1
        
        logs_rule = LIFECYCLE_RULES["logs_archival"]
        assert logs_rule["prefix"] == "logs/"
        assert logs_rule["transition_days"] == 30
        assert logs_rule["expiration_days"] == 365


@pytest.mark.skipif(
    os.getenv("S3_ENDPOINT_URL") is None,
    reason="S3_ENDPOINT_URL not set - skipping integration test"
)
class TestIntegration:
    """Integration tests with real S3-compatible storage (requires configuration)."""

    def test_s3_operations_integration(self):
        """Test full S3 upload/download/delete cycle."""
        client = S3Client()
        
        # Create temporary test file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as tmp:
            tmp.write("Integration test content")
            tmp_path = tmp.name
        
        try:
            # Upload
            remote_path = "logs/worker/integration-test.txt"
            client.upload_file(tmp_path, remote_path)
            
            # Verify exists
            assert client.file_exists(remote_path) is True
            
            # Download
            with tempfile.TemporaryDirectory() as tmpdir:
                download_path = os.path.join(tmpdir, "downloaded.txt")
                client.download_file(remote_path, download_path)
                
                with open(download_path, "r") as f:
                    content = f.read()
                    assert content == "Integration test content"
            
            # Delete
            client.delete_file(remote_path)
            
            # Verify deleted
            assert client.file_exists(remote_path) is False
            
        finally:
            os.unlink(tmp_path)
            # Cleanup in case test failed
            try:
                client.delete_file("logs/worker/integration-test.txt")
            except:
                pass
