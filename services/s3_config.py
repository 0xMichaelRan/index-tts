"""
S3 Storage Configuration with Path-Based Structure

This module provides S3-compatible storage configuration for the TTS service,
supporting Supabase Storage S3 API with path-based organization.

Storage Structure:
    s3://{bucket-name}/
    ├── audio-prompts/
    │   ├── {voice_id}.wav      # Voice recordings (path-based)
    │   └── {voice_id}.json     # Voice metadata
    ├── tts-output/
    │   ├── studio/
    │   │   ├── {job_id}.wav    # Studio job outputs (indefinite retention)
    │   │   └── {job_id}.json   # Job metadata
    │   └── playground/
    │       ├── {job_id}.wav    # Playground outputs (24h retention)
    │       └── {job_id}.json   # Job metadata
    └── logs/
        ├── worker/
        └── backend/

Lifecycle Rules:
    - tts-output/playground/: Delete after 24 hours
    - tts-output/studio/: Never expire (manual cleanup)
    - logs/: Transition to Glacier after 30 days, delete after 365 days

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
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# S3 Path Structure Constants
PATH_STRUCTURE = {
    "audio_prompts": "audio-prompts",
    "tts_output_studio": "tts-output/studio",
    "tts_output_playground": "tts-output/playground",
    "logs_worker": "logs/worker",
    "logs_backend": "logs/backend",
}

# Lifecycle configuration (for documentation - Supabase may not support all features)
LIFECYCLE_RULES = {
    "playground_cleanup": {
        "prefix": "tts-output/playground/",
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
    S3-compatible storage client for Supabase Storage.
    
    Provides methods for uploading, downloading, and managing files in S3-compatible
    storage with automatic retry logic and connection pooling.
    """
    
    def __init__(
        self,
        endpoint_url: Optional[str] = None,
        access_key_id: Optional[str] = None,
        secret_access_key: Optional[str] = None,
        bucket_name: Optional[str] = None,
        region: Optional[str] = None,
        use_ssl: bool = True,
        max_retries: int = 3,
    ):
        """
        Initialize S3 client with Supabase Storage configuration.
        
        Args:
            endpoint_url: S3 endpoint URL (default: from S3_ENDPOINT_URL env var)
            access_key_id: S3 access key (default: from S3_ACCESS_KEY_ID env var)
            secret_access_key: S3 secret key (default: from S3_SECRET_ACCESS_KEY env var)
            bucket_name: S3 bucket name (default: from S3_BUCKET_NAME env var)
            region: S3 region (default: from S3_REGION env var)
            use_ssl: Use SSL for connections (default: from S3_USE_SSL env var or True)
            max_retries: Maximum number of retry attempts for operations
            
        Raises:
            ImportError: If boto3 is not installed
            S3ConfigError: If required configuration is missing
        """
        if not BOTO3_AVAILABLE:
            raise ImportError(
                "boto3 is required for S3 operations. "
                "Install it with: pip install boto3"
            )
        
        # Load configuration from parameters or environment
        self.endpoint_url = endpoint_url or os.getenv("S3_ENDPOINT_URL")
        self.access_key_id = access_key_id or os.getenv("S3_ACCESS_KEY_ID")
        self.secret_access_key = secret_access_key or os.getenv("S3_SECRET_ACCESS_KEY")
        self.bucket_name = bucket_name or os.getenv("S3_BUCKET_NAME")
        self.region = region or os.getenv("S3_REGION", "us-east-1")
        
        # Parse SSL from environment if not provided
        if use_ssl and os.getenv("S3_USE_SSL"):
            use_ssl = os.getenv("S3_USE_SSL", "true").lower() in ("true", "1", "yes")
        
        self.use_ssl = use_ssl
        self.max_retries = max_retries
        
        # Validate required configuration
        self._validate_config()
        
        # Initialize boto3 client with retry configuration
        self.client = self._create_client()
        
        logger.info(f"S3 client initialized (endpoint: {self.endpoint_url}, bucket: {self.bucket_name})")
    
    def _validate_config(self) -> None:
        """Validate required S3 configuration."""
        missing = []
        
        if not self.endpoint_url:
            missing.append("S3_ENDPOINT_URL")
        if not self.access_key_id:
            missing.append("S3_ACCESS_KEY_ID")
        if not self.secret_access_key:
            missing.append("S3_SECRET_ACCESS_KEY")
        if not self.bucket_name:
            missing.append("S3_BUCKET_NAME")
        
        if missing:
            raise S3ConfigError(
                f"Missing required S3 configuration: {', '.join(missing)}. "
                "Set environment variables or pass as parameters."
            )
    
    def _create_client(self):
        """Create boto3 S3 client with retry configuration."""
        config = Config(
            retries={
                "max_attempts": self.max_retries,
                "mode": "adaptive",
            },
            signature_version="s3v4",
        )
        
        return boto3.client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.secret_access_key,
            region_name=self.region,
            use_ssl=self.use_ssl,
            config=config,
        )
    
    def upload_file(
        self,
        local_path: str,
        remote_path: str,
        metadata: Optional[Dict[str, str]] = None,
        content_type: Optional[str] = None,
    ) -> str:
        """
        Upload a file to S3 with automatic retry.
        
        Args:
            local_path: Path to local file
            remote_path: S3 object key (path within bucket)
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
        
        # Auto-detect content type from file extension
        if not content_type:
            content_type = self._get_content_type(local_path)
        
        extra_args = {"ContentType": content_type}
        if metadata:
            extra_args["Metadata"] = metadata
        
        logger.info(f"Uploading {local_path} to s3://{self.bucket_name}/{remote_path}")
        
        try:
            self.client.upload_file(
                Filename=local_path,
                Bucket=self.bucket_name,
                Key=remote_path,
                ExtraArgs=extra_args,
            )
            logger.info(f"✓ Upload successful: {remote_path}")
            return remote_path
            
        except (ClientError, BotoCoreError) as e:
            error_msg = f"Failed to upload {local_path} to {remote_path}: {str(e)}"
            logger.error(error_msg)
            raise S3ConfigError(error_msg) from e
    
    def upload_audio(
        self,
        local_path: str,
        remote_path: str,
        job_id: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None,
    ) -> str:
        """
        Upload audio file with appropriate metadata.
        
        Args:
            local_path: Path to local audio file
            remote_path: S3 object key
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
            metadata=full_metadata,
            content_type="audio/wav",
        )
    
    def download_file(
        self,
        remote_path: str,
        local_path: str,
        max_retries: Optional[int] = None,
    ) -> str:
        """
        Download a file from S3 with retry logic.
        
        Args:
            remote_path: S3 object key
            local_path: Local destination path
            max_retries: Override default max_retries
            
        Returns:
            Local file path
            
        Raises:
            S3ConfigError: If download fails after retries
        """
        retries = max_retries or self.max_retries
        
        logger.info(f"Downloading s3://{self.bucket_name}/{remote_path} to {local_path}")
        
        for attempt in range(1, retries + 1):
            try:
                # Ensure parent directory exists
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                
                self.client.download_file(
                    Bucket=self.bucket_name,
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
    
    def file_exists(self, remote_path: str) -> bool:
        """
        Check if a file exists in S3.
        
        Args:
            remote_path: S3 object key
            
        Returns:
            True if file exists, False otherwise
        """
        try:
            self.client.head_object(Bucket=self.bucket_name, Key=remote_path)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return False
            # Re-raise other errors (permission issues, etc.)
            raise S3ConfigError(f"Error checking file existence: {str(e)}") from e
    
    def delete_file(self, remote_path: str) -> bool:
        """
        Delete a file from S3.
        
        Args:
            remote_path: S3 object key
            
        Returns:
            True if deleted successfully
            
        Raises:
            S3ConfigError: If deletion fails
        """
        logger.info(f"Deleting s3://{self.bucket_name}/{remote_path}")
        
        try:
            self.client.delete_object(Bucket=self.bucket_name, Key=remote_path)
            logger.info(f"✓ Deleted: {remote_path}")
            return True
            
        except (ClientError, BotoCoreError) as e:
            error_msg = f"Failed to delete {remote_path}: {str(e)}"
            logger.error(error_msg)
            raise S3ConfigError(error_msg) from e
    
    def generate_presigned_url(
        self,
        remote_path: str,
        expiration: int = 3600,
        http_method: str = "GET",
    ) -> str:
        """
        Generate a presigned URL for temporary access to an S3 object.
        
        Args:
            remote_path: S3 object key
            expiration: URL expiration time in seconds (default: 1 hour)
            http_method: HTTP method for the URL (GET, PUT, etc.)
            
        Returns:
            Presigned URL
            
        Raises:
            S3ConfigError: If URL generation fails
        """
        try:
            client_method = "get_object" if http_method == "GET" else "put_object"
            
            url = self.client.generate_presigned_url(
                ClientMethod=client_method,
                Params={"Bucket": self.bucket_name, "Key": remote_path},
                ExpiresIn=expiration,
            )
            
            logger.debug(f"Generated presigned URL for {remote_path} (expires in {expiration}s)")
            return url
            
        except (ClientError, BotoCoreError) as e:
            error_msg = f"Failed to generate presigned URL for {remote_path}: {str(e)}"
            logger.error(error_msg)
            raise S3ConfigError(error_msg) from e
    
    def list_files(self, prefix: str, max_keys: int = 1000) -> list:
        """
        List files in S3 with a given prefix.
        
        Args:
            prefix: S3 key prefix (directory path)
            max_keys: Maximum number of keys to return
            
        Returns:
            List of S3 object keys
            
        Raises:
            S3ConfigError: If listing fails
        """
        try:
            response = self.client.list_objects_v2(
                Bucket=self.bucket_name,
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
        print("   - Playground cleanup: tts-output/playground/ → Delete after 1 day")
        print("   - Log archival: logs/ → Archive after 30 days, delete after 365 days")
        print("2. Configure CORS for cross-origin access if needed")
        print("3. Test upload/download with: python -m services.s3_config --test")
        
        return 0
        
    except Exception as e:
        print(f"\n✗ S3 configuration failed: {str(e)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    exit(main())
