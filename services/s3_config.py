"""
S3 Storage Configuration with Path-Based Structure

This module provides S3-compatible storage configuration for the TTS service,
supporting Supabase Storage S3 API with path-based organization.


Usage:
    from services.s3_config import S3Client, configure_bucket_structure
    
    # Initialize client
    client = S3Client()
    
    # Configure bucket structure (idempotent)
    configure_bucket_structure(client)
    
    # Upload audio file
    s3_path = client.upload_audio(
        local_path="/tmp/audio.wav",
        remote_path="audio-prompts/voice_123.wav"
    )
    
    # Generate presigned download URL
    url = client.generate_presigned_url("audio-prompts/voice_123.wav")
"""

import os
import logging
import time
from typing import Optional, Dict, Any, BinaryIO
from pathlib import Path
from urllib.parse import urlparse

try:
    import boto3
    from botocore.exceptions import ClientError, BotoCoreError
    from botocore.config import Config
    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False
    logging.warning("boto3 is not installed. Install with: pip install boto3")

# Configure logging
try:
    from services.logging_config import get_logger
    logger = get_logger(__name__)
except ImportError:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logger = logging.getLogger(__name__)


# S3 Path Structure Constants
PATH_STRUCTURE = {
    "audio_prompts": "audio-prompts",
    "tts_output_studio": "ttsoutput/studio",
    "tts_output_playground": "ttsoutput/playground",
    "logs_worker": "logs/worker",
    "logs_backend": "logs/backend",
}

# Lifecycle configuration (for documentation - Supabase may not support all features)
LIFECYCLE_RULES = {
    "playground_cleanup": {
        "prefix": "ttsoutput/playground/",
        "expiration_days": 1,
        "description": "Delete playground outputs after 24 hours",
    },
    "logs_archival": {
        "prefix": "logs/",
        "transition_days": 30,
        "expiration_days": 365,
        "description": "Archive logs to Glacier after 30 days, delete after 365 days",
    },
}


class S3ConfigError(Exception):
    """Raised when S3 configuration or operation fails."""
    pass


class S3Client:
    """
    Dual-bucket S3-compatible storage client.
    
    Requires completely independent configurations for storage bucket (voices) 
    and output bucket (TTS results), including separate endpoints, credentials, 
    regions, and SSL settings.
    """
    
    def __init__(
        self,
        # Storage bucket parameters
        storage_endpoint_url: Optional[str] = None,
        storage_access_key_id: Optional[str] = None,
        storage_secret_access_key: Optional[str] = None,
        storage_bucket_name: Optional[str] = None,
        storage_region: Optional[str] = None,
        storage_use_ssl: Optional[bool] = None,
        # Output bucket parameters
        output_endpoint_url: Optional[str] = None,
        output_access_key_id: Optional[str] = None,
        output_secret_access_key: Optional[str] = None,
        output_bucket_name: Optional[str] = None,
        output_region: Optional[str] = None,
        output_use_ssl: Optional[bool] = None,
        # Shared parameters
        max_retries: int = 3,
    ):
        """
        Initialize S3 client with dual-bucket configuration.
        
        Args:
            storage_endpoint_url: Storage bucket endpoint (voices)
            storage_access_key_id: Storage bucket access key
            storage_secret_access_key: Storage bucket secret key
            storage_bucket_name: Storage bucket name
            storage_region: Storage bucket region
            storage_use_ssl: Storage bucket SSL setting
            
            output_endpoint_url: Output bucket endpoint (TTS results)
            output_access_key_id: Output bucket access key
            output_secret_access_key: Output bucket secret key
            output_bucket_name: Output bucket name
            output_region: Output bucket region
            output_use_ssl: Output bucket SSL setting
            
            max_retries: Maximum retry attempts for operations
            
        Raises:
            ImportError: If boto3 is not installed
            S3ConfigError: If required configuration is missing
        """
        if not BOTO3_AVAILABLE:
            raise ImportError(
                "boto3 is required for S3 operations. "
                "Install it with: pip install boto3"
            )
        
        self.max_retries = max_retries
        
        logger.info("Initializing S3 client in dual-bucket mode")
        
        # Storage bucket configuration
        self.storage_endpoint_url = storage_endpoint_url or os.getenv("S3_STORAGE_ENDPOINT_URL")
        self.storage_access_key_id = storage_access_key_id or os.getenv("S3_STORAGE_ACCESS_KEY_ID")
        self.storage_secret_access_key = storage_secret_access_key or os.getenv("S3_STORAGE_SECRET_ACCESS_KEY")
        self.storage_bucket_name = storage_bucket_name or os.getenv("S3_STORAGE_BUCKET_NAME")
        self.storage_region = storage_region or os.getenv("S3_STORAGE_REGION", "us-east-1")
        self.storage_use_ssl = (
            storage_use_ssl 
            if storage_use_ssl is not None 
            else os.getenv("S3_STORAGE_USE_SSL", "true").lower() in ("true", "1", "yes")
        )
        
        # Output bucket configuration
        self.output_endpoint_url = output_endpoint_url or os.getenv("S3_OUTPUT_ENDPOINT_URL")
        self.output_access_key_id = output_access_key_id or os.getenv("S3_OUTPUT_ACCESS_KEY_ID")
        self.output_secret_access_key = output_secret_access_key or os.getenv("S3_OUTPUT_SECRET_ACCESS_KEY")
        self.output_bucket_name = output_bucket_name or os.getenv("S3_OUTPUT_BUCKET_NAME")
        self.output_region = output_region or os.getenv("S3_OUTPUT_REGION", "us-east-1")
        self.output_use_ssl = (
            output_use_ssl 
            if output_use_ssl is not None 
            else os.getenv("S3_OUTPUT_USE_SSL", "true").lower() in ("true", "1", "yes")
        )
        
        # Validate configuration
        self._validate_config()
        
        # Create separate clients
        config = Config(
            retries={"max_attempts": self.max_retries, "mode": "adaptive"},
            signature_version="s3v4",
        )
        
        self.storage_client = self._create_client(
            endpoint_url=self.storage_endpoint_url,
            access_key_id=self.storage_access_key_id,
            secret_access_key=self.storage_secret_access_key,
            region=self.storage_region,
            use_ssl=self.storage_use_ssl,
            config=config,
        )
        
        self.output_client = self._create_client(
            endpoint_url=self.output_endpoint_url,
            access_key_id=self.output_access_key_id,
            secret_access_key=self.output_secret_access_key,
            region=self.output_region,
            use_ssl=self.output_use_ssl,
            config=config,
        )
        
        logger.success(f"Dual-bucket mode initialized")
        logger.info(f"  Storage bucket: {self.storage_bucket_name} ({self.storage_endpoint_url})")
        logger.info(f"  Output bucket:  {self.output_bucket_name} ({self.output_endpoint_url})")
    
    def _validate_config(self) -> None:
        """Validate required dual-bucket S3 configuration."""
        missing_storage = []
        missing_output = []
        
        # Storage bucket validation
        if not self.storage_endpoint_url:
            missing_storage.append("S3_STORAGE_ENDPOINT_URL")
        if not self.storage_access_key_id:
            missing_storage.append("S3_STORAGE_ACCESS_KEY_ID")
        if not self.storage_secret_access_key:
            missing_storage.append("S3_STORAGE_SECRET_ACCESS_KEY")
        if not self.storage_bucket_name:
            missing_storage.append("S3_STORAGE_BUCKET_NAME")
        
        # Output bucket validation
        if not self.output_endpoint_url:
            missing_output.append("S3_OUTPUT_ENDPOINT_URL")
        if not self.output_access_key_id:
            missing_output.append("S3_OUTPUT_ACCESS_KEY_ID")
        if not self.output_secret_access_key:
            missing_output.append("S3_OUTPUT_SECRET_ACCESS_KEY")
        if not self.output_bucket_name:
            missing_output.append("S3_OUTPUT_BUCKET_NAME")
        
        missing = missing_storage + missing_output
        
        if missing:
            raise S3ConfigError(
                f"Missing required dual-bucket S3 configuration: {', '.join(missing)}. "
                "Set all S3_STORAGE_* and S3_OUTPUT_* environment variables. "
                "See .env.example or DUAL_BUCKET_GUIDE.md for configuration template."
            )
    
    @staticmethod
    def _create_client(endpoint_url, access_key_id, secret_access_key, region, use_ssl, config):
        """Create boto3 S3 client with the given configuration."""
        return boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region,
            use_ssl=use_ssl,
            config=config,
        )
    
    def _get_client_and_bucket(self, bucket_type: str):
        """
        Get the appropriate client and bucket for a bucket type.
        
        Args:
            bucket_type: "storage" or "output"
            
        Returns:
            Tuple of (client, bucket_name)
            
        Raises:
            ValueError: If bucket_type is invalid
        """
        if bucket_type == "storage":
            return self.storage_client, self.storage_bucket_name
        elif bucket_type == "output":
            return self.output_client, self.output_bucket_name
        else:
            raise ValueError(
                f"Invalid bucket type: {bucket_type}. "
                f"Must be 'storage' or 'output'."
            )
    
    def upload_file(
        self,
        local_path: str,
        remote_path: str,
        bucket_type: str = "storage",
        metadata: Optional[Dict[str, str]] = None,
        content_type: Optional[str] = None,
    ) -> str:
        """
        Upload a file to S3 with automatic retry.
        
        Args:
            local_path: Path to local file
            remote_path: S3 object key (path within bucket)
            bucket_type: "storage" or "output" (determines which bucket to use)
            metadata: Optional metadata tags for the object
            content_type: Optional content type (auto-detected if not provided)
            
        Returns:
            S3 path (remote_path)
            
        Raises:
            S3ConfigError: If upload fails after retries
            FileNotFoundError: If local file doesn't exist
        """
        if not os.path.exists(local_path):
            raise FileNotFoundError(f"Local file not found: {local_path}")
        
        # Get appropriate client and bucket
        client, bucket_name = self._get_client_and_bucket(bucket_type)
        
        # Auto-detect content type from file extension
        if not content_type:
            content_type = self._get_content_type(local_path)
        
        extra_args = {"ContentType": content_type}
        if metadata:
            extra_args["Metadata"] = metadata
        
        logger.info(f"Uploading {local_path} to s3://{bucket_name}/{remote_path}")
        
        try:
            client.upload_file(
                Filename=local_path,
                Bucket=bucket_name,
                Key=remote_path,
                ExtraArgs=extra_args,
            )
            logger.info(f"Upload successful: {remote_path}")
            return remote_path
            
        except (ClientError, BotoCoreError) as e:
            error_msg = f"Failed to upload {local_path} to {remote_path}: {str(e)}"
            logger.error(error_msg)
            raise S3ConfigError(error_msg) from e
    
    def upload_audio(
        self,
        local_path: str,
        remote_path: str,
        bucket_type: str = "output",
        job_id: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None,
    ) -> str:
        """
        Upload audio file with appropriate metadata.
        
        Args:
            local_path: Path to local audio file
            remote_path: S3 object key
            bucket_type: "storage" or "output" (default: "output" for TTS results)
            job_id: Optional job ID for tracking
            metadata: Optional additional metadata
            
        Returns:
            S3 path (remote_path)
        """
        # Merge metadata with job_id if provided
        full_metadata = metadata or {}
        if job_id:
            full_metadata["job_id"] = job_id
        
        return self.upload_file(
            local_path=local_path,
            remote_path=remote_path,
            bucket_type=bucket_type,
            metadata=full_metadata,
            content_type="audio/wav",
        )
    
    def download_file(
        self,
        remote_path: str,
        local_path: str,
        bucket_type: str = "storage",
        max_retries: Optional[int] = None,
    ) -> str:
        """
        Download a file from S3 with retry logic.
        
        Args:
            remote_path: S3 object key
            local_path: Local destination path
            bucket_type: "storage" or "output" (determines which bucket to use)
            max_retries: Override default max_retries
            
        Returns:
            Local file path
            
        Raises:
            S3ConfigError: If download fails after retries
        """
        retries = max_retries or self.max_retries
        
        # Get appropriate client and bucket
        client, bucket_name = self._get_client_and_bucket(bucket_type)
        
        logger.info(f"Downloading s3://{bucket_name}/{remote_path} to {local_path}")
        
        for attempt in range(1, retries + 1):
            try:
                # Ensure parent directory exists
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                
                client.download_file(
                    Bucket=bucket_name,
                    Key=remote_path,
                    Filename=local_path,
                )
                
                logger.info(f"✓ Download successful: {local_path}")
                return local_path
                
            except (ClientError, BotoCoreError) as e:
                if attempt == retries:
                    error_msg = f"Failed to download {remote_path} after {retries} attempts: {str(e)}"
                    logger.error(error_msg)
                    raise S3ConfigError(error_msg) from e
                
                delay = 2 ** (attempt - 1)  # Exponential backoff: 1, 2, 4 seconds
                logger.warning(
                    f"Download attempt {attempt}/{retries} failed: {str(e)}. "
                    f"Retrying in {delay} seconds..."
                )
                time.sleep(delay)
    
    def file_exists(self, remote_path: str, bucket_type: str = "storage") -> bool:
        """
        Check if a file exists in S3.
        
        Args:
            remote_path: S3 object key
            bucket_type: "storage" or "output" (determines which bucket to check)
            
        Returns:
            True if file exists, False otherwise
        """
        client, bucket_name = self._get_client_and_bucket(bucket_type)
        
        try:
            client.head_object(Bucket=bucket_name, Key=remote_path)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return False
            # Re-raise other errors (permission issues, etc.)
            raise S3ConfigError(f"Error checking file existence: {str(e)}") from e
    
    def delete_file(self, remote_path: str, bucket_type: str = "storage") -> bool:
        """
        Delete a file from S3.
        
        Args:
            remote_path: S3 object key
            bucket_type: "storage" or "output" (determines which bucket to use)
            
        Returns:
            True if deleted successfully
            
        Raises:
            S3ConfigError: If deletion fails
        """
        client, bucket_name = self._get_client_and_bucket(bucket_type)
        
        logger.info(f"Deleting s3://{bucket_name}/{remote_path}")
        
        try:
            client.delete_object(Bucket=bucket_name, Key=remote_path)
            logger.info(f"✓ Deleted: {remote_path}")
            return True
            
        except (ClientError, BotoCoreError) as e:
            error_msg = f"Failed to delete {remote_path}: {str(e)}"
            logger.error(error_msg)
            raise S3ConfigError(error_msg) from e
    
    def generate_presigned_url(
        self,
        remote_path: str,
        bucket_type: str = "storage",
        expiration: int = 3600,
        http_method: str = "GET",
    ) -> str:
        """
        Generate a presigned URL for temporary access to an S3 object.
        
        Args:
            remote_path: S3 object key
            bucket_type: "storage" or "output" (determines which bucket to use)
            expiration: URL expiration time in seconds (default: 1 hour)
            http_method: HTTP method for the URL (GET, PUT, etc.)
            
        Returns:
            Presigned URL
            
        Raises:
            S3ConfigError: If URL generation fails
        """
        client, bucket_name = self._get_client_and_bucket(bucket_type)
        
        try:
            client_method = "get_object" if http_method == "GET" else "put_object"
            
            url = client.generate_presigned_url(
                ClientMethod=client_method,
                Params={"Bucket": bucket_name, "Key": remote_path},
                ExpiresIn=expiration,
            )
            
            logger.debug(f"Generated presigned URL for {remote_path} (expires in {expiration}s)")
            return url
            
        except (ClientError, BotoCoreError) as e:
            error_msg = f"Failed to generate presigned URL for {remote_path}: {str(e)}"
            logger.error(error_msg)
            raise S3ConfigError(error_msg) from e
    
    def list_files(self, prefix: str, bucket_type: str = "storage", max_keys: int = 1000) -> list:
        """
        List files in S3 with a given prefix.
        
        Args:
            prefix: S3 key prefix (directory path)
            bucket_type: "storage" or "output" (determines which bucket to use)
            max_keys: Maximum number of keys to return
            
        Returns:
            List of S3 object keys
            
        Raises:
            S3ConfigError: If listing fails
        """
        client, bucket_name = self._get_client_and_bucket(bucket_type)
        
        try:
            response = client.list_objects_v2(
                Bucket=bucket_name,
                Prefix=prefix,
                MaxKeys=max_keys,
            )
            
            if "Contents" not in response:
                return []
            
            return [obj["Key"] for obj in response["Contents"]]
            
        except (ClientError, BotoCoreError) as e:
            error_msg = f"Failed to list files with prefix {prefix}: {str(e)}"
            logger.error(error_msg)
            raise S3ConfigError(error_msg) from e
    
    def validate_path(self, path: str) -> bool:
        """
        Validate S3 path format and prevent path traversal.
        
        Args:
            path: S3 object key to validate
            
        Returns:
            True if path is valid
            
        Raises:
            ValueError: If path contains invalid characters or patterns
        """
        # Check for path traversal attempts
        if ".." in path or path.startswith("/"):
            raise ValueError(f"Invalid path (path traversal detected): {path}")
        
        # Check for valid prefix
        valid_prefixes = list(PATH_STRUCTURE.values())
        if not any(path.startswith(prefix) for prefix in valid_prefixes):
            raise ValueError(
                f"Invalid path prefix. Path must start with one of: {valid_prefixes}"
            )
        
        return True
    
    @staticmethod
    def _get_content_type(file_path: str) -> str:
        """Determine content type from file extension."""
        ext = os.path.splitext(file_path)[1].lower()
        
        content_types = {
            ".wav": "audio/wav",
            ".mp3": "audio/mpeg",
            ".json": "application/json",
            ".txt": "text/plain",
            ".log": "text/plain",
        }
        
        return content_types.get(ext, "application/octet-stream")


def configure_bucket_structure(
    client: S3Client,
    create_test_files: bool = False,
) -> None:
    """
    Configure S3 bucket with path-based structure.
    
    This function is idempotent and creates the necessary directory structure
    in S3 by uploading placeholder files (if requested).
    
    Note: S3 doesn't have true directories, but prefixes simulate them.
    This function optionally creates placeholder files to establish the structure.
    
    Args:
        client: Initialized S3Client instance
        create_test_files: If True, create placeholder files for directory structure
        
    Raises:
        S3ConfigError: If bucket configuration fails
    """
    logger.info("=" * 70)
    logger.info("Configuring S3 Bucket Structure")
    logger.info("=" * 70)
    logger.info(f"Bucket: {client.bucket_name}")
    logger.info(f"Endpoint: {client.endpoint_url}")
    
    # Verify bucket exists and is accessible
    try:
        client.client.head_bucket(Bucket=client.bucket_name)
        logger.info("✓ Bucket is accessible")
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        if error_code == "404":
            raise S3ConfigError(f"Bucket '{client.bucket_name}' does not exist") from e
        elif error_code == "403":
            raise S3ConfigError(f"Access denied to bucket '{client.bucket_name}'") from e
        else:
            raise S3ConfigError(f"Error accessing bucket: {str(e)}") from e
    
    # Log path structure
    logger.info("\nPath structure:")
    logger.info("-" * 70)
    for name, path in PATH_STRUCTURE.items():
        logger.info(f"  {name:30} → {path}")
    
    # Optionally create placeholder files to establish structure
    if create_test_files:
        logger.info("\nCreating directory structure with placeholder files...")
        logger.info("-" * 70)
        
        import tempfile
        
        for name, path in PATH_STRUCTURE.items():
            placeholder_key = f"{path}/.placeholder"
            
            try:
                # Check if already exists
                if client.file_exists(placeholder_key):
                    logger.info(f"  ✓ {placeholder_key} (already exists)")
                    continue
                
                # Create temporary placeholder file
                with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp:
                    tmp.write(f"Placeholder for {path}\n")
                    tmp_path = tmp.name
                
                # Upload placeholder
                client.upload_file(
                    local_path=tmp_path,
                    remote_path=placeholder_key,
                    content_type="text/plain",
                )
                
                # Cleanup temp file
                os.unlink(tmp_path)
                
                logger.info(f"  ✓ {placeholder_key} (created)")
                
            except Exception as e:
                logger.warning(f"  ✗ Failed to create {placeholder_key}: {str(e)}")
    
    logger.info("-" * 70)
    logger.info("✓ Bucket structure configured successfully")
    
    # Log lifecycle rules information
    logger.info("\nLifecycle rules (configure manually in Supabase dashboard):")
    logger.info("-" * 70)
    for rule_name, rule_config in LIFECYCLE_RULES.items():
        logger.info(f"  {rule_name}:")
        logger.info(f"    Description: {rule_config['description']}")
        logger.info(f"    Prefix: {rule_config['prefix']}")
        if "expiration_days" in rule_config:
            logger.info(f"    Expiration: {rule_config['expiration_days']} days")
        if "transition_days" in rule_config:
            logger.info(f"    Transition: {rule_config['transition_days']} days")
    
    logger.info("=" * 70)


def main():
    """CLI entry point for S3 configuration."""
    import sys
    
    try:
        # Initialize client from environment variables
        client = S3Client()
        
        # Configure bucket structure
        create_test = "--create-placeholders" in sys.argv
        configure_bucket_structure(client, create_test_files=create_test)
        
        print("\n✓ S3 configuration completed successfully")
        print("\nNext steps:")
        print("1. Configure lifecycle rules in Supabase dashboard:")
        print("   - Playground cleanup: ttsoutput/playground/ → Delete after 1 day")
        print("   - Log archival: logs/ → Archive after 30 days, delete after 365 days")
        print("2. Configure CORS for cross-origin access if needed")
        print("3. Test upload/download with: python -m services.s3_config --test")
        
        return 0
        
    except Exception as e:
        print(f"\n✗ S3 configuration failed: {str(e)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    exit(main())
