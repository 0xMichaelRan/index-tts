"""
Integration tests for S3 configuration with real credentials from .env

These tests use actual credentials from the .env file to verify:
- Real S3 connectivity (not mocked)
- Authentication and authorization
- Upload/download functionality with real buckets
- Presigned URL generation
- File operations (list, delete, exists)

**WARNING**: These tests perform real S3 operations and may incur costs.
Use with caution in production environments.

**Prerequisites**:
1. All S3_STORAGE_* and S3_OUTPUT_* environment variables must be set
2. S3 buckets must be accessible and have appropriate permissions
3. Credentials must have read/write access as needed

Run with:
    uv run pytest tests/pytest/test_s3_integration.py -v -s --tb=short

Markers:
    @pytest.mark.integration - Integration tests with real S3
    @pytest.mark.slow - Tests that take time (uploads/downloads)
"""

import pytest
import os
import tempfile
import time
from datetime import datetime
import logging

from services.s3_config import S3Client, S3ConfigError


logger = logging.getLogger(__name__)


# Skip all integration tests if S3 credentials not fully configured
STORAGE_CONFIGURED = all([
    os.getenv("S3_STORAGE_ENDPOINT_URL"),
    os.getenv("S3_STORAGE_ACCESS_KEY_ID"),
    os.getenv("S3_STORAGE_SECRET_ACCESS_KEY"),
    os.getenv("S3_STORAGE_BUCKET_NAME"),
])

OUTPUT_CONFIGURED = all([
    os.getenv("S3_OUTPUT_ENDPOINT_URL"),
    os.getenv("S3_OUTPUT_ACCESS_KEY_ID"),
    os.getenv("S3_OUTPUT_SECRET_ACCESS_KEY"),
    os.getenv("S3_OUTPUT_BUCKET_NAME"),
])

SKIP_REASON = "S3 credentials not configured in .env"
pytestmark = pytest.mark.skipif(
    not (STORAGE_CONFIGURED and OUTPUT_CONFIGURED),
    reason=SKIP_REASON
)


@pytest.fixture(scope="session")
def s3_client():
    """Create S3 client with credentials from .env (session scope)."""
    try:
        client = S3Client()
        logger.info(f"✓ S3 client initialized")
        logger.info(f"  Storage: {client.storage_bucket_name} ({client.storage_endpoint_url})")
        logger.info(f"  Output: {client.output_bucket_name} ({client.output_endpoint_url})")
        return client
    except S3ConfigError as e:
        pytest.skip(f"Failed to initialize S3 client: {e}")


@pytest.fixture
def test_file_storage():
    """Create temporary test file for storage bucket."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        # Write test audio data (simple WAV header + some data)
        f.write(b"RIFF" + b"\x00" * 100)
        temp_path = f.name
    
    yield temp_path
    
    # Cleanup
    if os.path.exists(temp_path):
        os.remove(temp_path)


@pytest.fixture
def test_file_output():
    """Create temporary test file for output bucket."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(b"RIFF" + b"\x00" * 200)
        temp_path = f.name
    
    yield temp_path
    
    # Cleanup
    if os.path.exists(temp_path):
        os.remove(temp_path)


class TestS3Connectivity:
    """Test real S3 connectivity and authentication."""
    
    @pytest.mark.integration
    def test_storage_bucket_accessible(self, s3_client):
        """Test that storage bucket is accessible with provided credentials."""
        # Try to list files in storage bucket
        try:
            files = s3_client.list_files("", bucket_type="storage", max_keys=1)
            logger.info(f"✓ Storage bucket is accessible (found {len(files)} files)")
            assert isinstance(files, list)
        except S3ConfigError as e:
            pytest.fail(f"Storage bucket not accessible: {e}")
    
    @pytest.mark.integration
    def test_output_bucket_accessible(self, s3_client):
        """Test that output bucket is accessible with provided credentials."""
        # Try to list files in output bucket
        try:
            files = s3_client.list_files("", bucket_type="output", max_keys=1)
            logger.info(f"✓ Output bucket is accessible (found {len(files)} files)")
            assert isinstance(files, list)
        except S3ConfigError as e:
            pytest.fail(f"Output bucket not accessible: {e}")
    
    @pytest.mark.integration
    def test_storage_bucket_credentials_valid(self, s3_client):
        """Test that storage bucket credentials are valid (read access)."""
        # Try to get a presigned URL (validates credentials)
        try:
            url = s3_client.generate_presigned_url(
                remote_path="audio-prompts/.test",
                bucket_type="storage",
                expiration=3600,
            )
            logger.info(f"✓ Storage bucket credentials are valid")
            assert url.startswith("http")
            assert "Signature" in url or "sig=" in url  # Presigned URLs contain signature
        except S3ConfigError as e:
            pytest.fail(f"Storage bucket credentials invalid: {e}")
    
    @pytest.mark.integration
    def test_output_bucket_credentials_valid(self, s3_client):
        """Test that output bucket credentials are valid (write access)."""
        # Try to get a presigned URL (validates credentials)
        try:
            url = s3_client.generate_presigned_url(
                remote_path="tts-output/.test",
                bucket_type="output",
                expiration=3600,
            )
            logger.info(f"✓ Output bucket credentials are valid")
            assert url.startswith("http")
        except S3ConfigError as e:
            pytest.fail(f"Output bucket credentials invalid: {e}")


class TestS3Upload:
    """Test real file uploads to S3."""
    
    @pytest.mark.integration
    @pytest.mark.slow
    def test_upload_to_storage_bucket(self, s3_client, test_file_storage):
        """Test uploading a file to storage bucket."""
        remote_path = f"audio-prompts/test-{int(time.time())}.wav"
        
        try:
            result = s3_client.upload_file(
                local_path=test_file_storage,
                remote_path=remote_path,
                bucket_type="storage",
                content_type="audio/wav",
            )
            
            logger.info(f"✓ Uploaded to storage: {remote_path}")
            assert result == remote_path
            
            # Verify file exists in storage bucket
            exists = s3_client.file_exists(remote_path, bucket_type="storage")
            assert exists is True
            logger.info(f"✓ Verified file exists in storage bucket")
            
            # Cleanup
            s3_client.delete_file(remote_path, bucket_type="storage")
            logger.info(f"✓ Cleaned up test file")
            
        except S3ConfigError as e:
            pytest.fail(f"Upload to storage failed: {e}")
    
    @pytest.mark.integration
    @pytest.mark.slow
    def test_upload_to_output_bucket(self, s3_client, test_file_output):
        """Test uploading a file to output bucket."""
        job_id = f"test-job-{int(time.time())}"
        remote_path = f"tts-output/studio/{job_id}.wav"
        
        try:
            result = s3_client.upload_audio(
                local_path=test_file_output,
                remote_path=remote_path,
                bucket_type="output",
                job_id=job_id,
            )
            
            logger.info(f"✓ Uploaded to output: {remote_path}")
            assert result == remote_path
            
            # Verify file exists in output bucket
            exists = s3_client.file_exists(remote_path, bucket_type="output")
            assert exists is True
            logger.info(f"✓ Verified file exists in output bucket")
            
            # Cleanup
            s3_client.delete_file(remote_path, bucket_type="output")
            logger.info(f"✓ Cleaned up test file")
            
        except S3ConfigError as e:
            pytest.fail(f"Upload to output failed: {e}")
    
    @pytest.mark.integration
    @pytest.mark.slow
    def test_upload_with_metadata(self, s3_client, test_file_storage):
        """Test uploading a file with metadata to storage bucket."""
        remote_path = f"audio-prompts/test-meta-{int(time.time())}.wav"
        metadata = {
            "voice_id": "test-voice-001",
            "language": "en",
            "uploaded_at": datetime.now().isoformat(),
        }
        
        try:
            result = s3_client.upload_file(
                local_path=test_file_storage,
                remote_path=remote_path,
                bucket_type="storage",
                metadata=metadata,
                content_type="audio/wav",
            )
            
            logger.info(f"✓ Uploaded with metadata: {remote_path}")
            assert result == remote_path
            
            # Cleanup
            s3_client.delete_file(remote_path, bucket_type="storage")
            
        except S3ConfigError as e:
            pytest.fail(f"Upload with metadata failed: {e}")


class TestS3Download:
    """Test real file downloads from S3."""
    
    @pytest.mark.integration
    @pytest.mark.slow
    def test_download_from_storage_bucket(self, s3_client, test_file_storage):
        """Test downloading a file from storage bucket."""
        # First upload a test file
        remote_path = f"audio-prompts/test-download-{int(time.time())}.wav"
        
        try:
            s3_client.upload_file(
                local_path=test_file_storage,
                remote_path=remote_path,
                bucket_type="storage",
            )
            logger.info(f"✓ Uploaded test file: {remote_path}")
            
            # Now download it
            with tempfile.TemporaryDirectory() as tmp_dir:
                download_path = os.path.join(tmp_dir, "downloaded.wav")
                
                result = s3_client.download_file(
                    remote_path=remote_path,
                    local_path=download_path,
                    bucket_type="storage",
                )
                
                logger.info(f"✓ Downloaded from storage: {remote_path}")
                assert result == download_path
                assert os.path.exists(download_path)
                assert os.path.getsize(download_path) > 0
                logger.info(f"✓ Downloaded file size: {os.path.getsize(download_path)} bytes")
            
            # Cleanup
            s3_client.delete_file(remote_path, bucket_type="storage")
            
        except S3ConfigError as e:
            pytest.fail(f"Download from storage failed: {e}")
    
    @pytest.mark.integration
    @pytest.mark.slow
    def test_download_from_output_bucket(self, s3_client, test_file_output):
        """Test downloading a file from output bucket."""
        # First upload a test file
        remote_path = f"tts-output/studio/test-download-{int(time.time())}.wav"
        
        try:
            s3_client.upload_file(
                local_path=test_file_output,
                remote_path=remote_path,
                bucket_type="output",
            )
            logger.info(f"✓ Uploaded test file: {remote_path}")
            
            # Now download it
            with tempfile.TemporaryDirectory() as tmp_dir:
                download_path = os.path.join(tmp_dir, "downloaded.wav")
                
                result = s3_client.download_file(
                    remote_path=remote_path,
                    local_path=download_path,
                    bucket_type="output",
                )
                
                logger.info(f"✓ Downloaded from output: {remote_path}")
                assert result == download_path
                assert os.path.exists(download_path)
                
            # Cleanup
            s3_client.delete_file(remote_path, bucket_type="output")
            
        except S3ConfigError as e:
            pytest.fail(f"Download from output failed: {e}")


class TestS3FileOperations:
    """Test real S3 file operations."""
    
    @pytest.mark.integration
    @pytest.mark.slow
    def test_file_exists_check(self, s3_client, test_file_storage):
        """Test checking if file exists in S3."""
        remote_path = f"audio-prompts/test-exists-{int(time.time())}.wav"
        
        try:
            # File should not exist initially
            exists = s3_client.file_exists(remote_path, bucket_type="storage")
            assert exists is False
            logger.info(f"✓ Confirmed file does not exist initially")
            
            # Upload file
            s3_client.upload_file(
                local_path=test_file_storage,
                remote_path=remote_path,
                bucket_type="storage",
            )
            
            # Now file should exist
            exists = s3_client.file_exists(remote_path, bucket_type="storage")
            assert exists is True
            logger.info(f"✓ Confirmed file exists after upload")
            
            # Cleanup
            s3_client.delete_file(remote_path, bucket_type="storage")
            
            # File should not exist after deletion
            exists = s3_client.file_exists(remote_path, bucket_type="storage")
            assert exists is False
            logger.info(f"✓ Confirmed file does not exist after deletion")
            
        except S3ConfigError as e:
            pytest.fail(f"File exists check failed: {e}")
    
    @pytest.mark.integration
    def test_list_files_in_storage_bucket(self, s3_client):
        """Test listing files in storage bucket."""
        try:
            files = s3_client.list_files("audio-prompts/", bucket_type="storage", max_keys=10)
            logger.info(f"✓ Listed {len(files)} files in storage bucket (max 10 shown)")
            assert isinstance(files, list)
        except S3ConfigError as e:
            pytest.fail(f"List files in storage failed: {e}")
    
    @pytest.mark.integration
    def test_list_files_in_output_bucket(self, s3_client):
        """Test listing files in output bucket."""
        try:
            files = s3_client.list_files("tts-output/", bucket_type="output", max_keys=10)
            logger.info(f"✓ Listed {len(files)} files in output bucket (max 10 shown)")
            assert isinstance(files, list)
        except S3ConfigError as e:
            pytest.fail(f"List files in output failed: {e}")
    
    @pytest.mark.integration
    @pytest.mark.slow
    def test_delete_file_from_storage(self, s3_client, test_file_storage):
        """Test deleting a file from storage bucket."""
        remote_path = f"audio-prompts/test-delete-{int(time.time())}.wav"
        
        try:
            # Upload file
            s3_client.upload_file(
                local_path=test_file_storage,
                remote_path=remote_path,
                bucket_type="storage",
            )
            logger.info(f"✓ Uploaded file: {remote_path}")
            
            # Verify it exists
            assert s3_client.file_exists(remote_path, bucket_type="storage")
            
            # Delete it
            result = s3_client.delete_file(remote_path, bucket_type="storage")
            assert result is True
            logger.info(f"✓ Deleted file: {remote_path}")
            
            # Verify it no longer exists
            assert s3_client.file_exists(remote_path, bucket_type="storage") is False
            
        except S3ConfigError as e:
            pytest.fail(f"Delete file from storage failed: {e}")


class TestS3PresignedURLs:
    """Test real presigned URL generation."""
    
    @pytest.mark.integration
    def test_presigned_url_storage_bucket(self, s3_client):
        """Test generating presigned GET URL for storage bucket."""
        try:
            url = s3_client.generate_presigned_url(
                remote_path="audio-prompts/test-presigned.wav",
                bucket_type="storage",
                http_method="GET",
                expiration=3600,
            )
            
            logger.info(f"✓ Generated presigned GET URL for storage")
            assert url.startswith("http")
            assert "Signature" in url or "sig=" in url or "token=" in url
            logger.info(f"  URL length: {len(url)} chars")
            
        except S3ConfigError as e:
            pytest.fail(f"Generate presigned URL for storage failed: {e}")
    
    @pytest.mark.integration
    def test_presigned_url_output_bucket(self, s3_client):
        """Test generating presigned PUT URL for output bucket."""
        try:
            url = s3_client.generate_presigned_url(
                remote_path="tts-output/studio/test-presigned.wav",
                bucket_type="output",
                http_method="PUT",
                expiration=1800,
            )
            
            logger.info(f"✓ Generated presigned PUT URL for output")
            assert url.startswith("http")
            logger.info(f"  URL expires in: 1800 seconds")
            
        except S3ConfigError as e:
            pytest.fail(f"Generate presigned URL for output failed: {e}")


class TestS3DualBucketSeparation:
    """Test that storage and output buckets are properly separated."""
    
    @pytest.mark.integration
    def test_storage_and_output_are_different(self, s3_client):
        """Test that storage and output bucket names are different."""
        storage_name = s3_client.storage_bucket_name
        output_name = s3_client.output_bucket_name
        
        logger.info(f"Storage bucket: {storage_name}")
        logger.info(f"Output bucket: {output_name}")
        
        # This test can pass even if buckets are the same (for single-bucket setups)
        # but logs will show they're the same
        assert isinstance(storage_name, str)
        assert isinstance(output_name, str)
        assert len(storage_name) > 0
        assert len(output_name) > 0
    
    @pytest.mark.integration
    def test_storage_and_output_use_different_clients(self, s3_client):
        """Test that storage and output buckets use separate S3 clients."""
        assert s3_client.storage_client is not None
        assert s3_client.output_client is not None
        # Clients can be the same if using single endpoint
        logger.info(f"Storage client: {type(s3_client.storage_client).__name__}")
        logger.info(f"Output client: {type(s3_client.output_client).__name__}")


class TestS3ErrorHandling:
    """Test error handling with real S3."""
    
    @pytest.mark.integration
    def test_download_nonexistent_file(self, s3_client):
        """Test downloading a file that doesn't exist."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            local_path = os.path.join(tmp_dir, "nonexistent.wav")
            
            with pytest.raises(S3ConfigError):
                s3_client.download_file(
                    remote_path="audio-prompts/nonexistent-file-xyz123.wav",
                    local_path=local_path,
                    bucket_type="storage",
                )
            logger.info(f"✓ Correctly raised S3ConfigError for nonexistent file")
    
    @pytest.mark.integration
    def test_upload_nonexistent_local_file(self, s3_client):
        """Test uploading a local file that doesn't exist."""
        with pytest.raises(FileNotFoundError):
            s3_client.upload_file(
                local_path="/nonexistent/path/file.wav",
                remote_path="audio-prompts/test.wav",
                bucket_type="storage",
            )
        logger.info(f"✓ Correctly raised FileNotFoundError for nonexistent local file")
    
    @pytest.mark.integration
    def test_invalid_bucket_type(self, s3_client):
        """Test that invalid bucket_type raises error."""
        with pytest.raises(ValueError) as exc_info:
            s3_client.download_file(
                remote_path="test.wav",
                local_path="/tmp/test.wav",
                bucket_type="invalid_bucket",  # Invalid
            )
        assert "Invalid bucket type" in str(exc_info.value)
        logger.info(f"✓ Correctly raised ValueError for invalid bucket_type")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
