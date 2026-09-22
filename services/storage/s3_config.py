"""
S3 Storage Client – Registry-backed.

Provides ``S3Client``: a thin wrapper around the bucket registry that
creates and caches boto3 S3 clients per bucket type.

Bucket types:
  - "misc"  – miscellaneous assets, temporary uploads
  - "video" – video clips and rendered MP4 outputs
  - "audio" – user voice recordings and audio prompts for TTS voice cloning
  - "tts"   – synthesised TTS audio and forced alignment JSON (worker writes here)
  - "11lab" – user-uploaded ElevenLabs audio exports (frontend uploads)

Credentials are resolved via ``config/buckets.toml`` + ``S3_<TYPE>_*``
environment variables.  See ``services/s3_registry.py`` for details.

Usage::

    from services.storage.s3_config import S3Client, S3ConfigError

    client = S3Client()

    # Download a voice recording (audio bucket)
    client.download_file(
        remote_path="audio-prompts/voice_001.wav",
        local_path="/tmp/prompt.wav",
        bucket_type="audio",
    )

    # Upload a TTS result (tts bucket)
    client.upload_file(
        local_path="/tmp/output.wav",
        remote_path="tts-audio/studio/job_123.wav",
        bucket_type="tts",
    )
"""

from __future__ import annotations

import logging
import os
import time

try:
    import boto3
    from botocore.config import Config
    from botocore.exceptions import BotoCoreError, ClientError

    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False
    logging.warning("boto3 is not installed. Install with: pip install boto3")

try:
    from services.common.logging_config import get_logger

    logger = get_logger(__name__)
except ImportError:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logger = logging.getLogger(__name__)

from services.storage.s3_registry import (
    S3BucketConfig,
    clear_registry_cache,
    get_bucket,
    get_bucket_by_name,
    get_bucket_by_type,
    get_registry,
    list_buckets,
    load_buckets,
    CONFIG_FILE,
)

# Re-export registry helpers for convenience
__all__ = [
    "S3Client",
    "S3ConfigError",
    "CONFIG_FILE",
    "S3BucketConfig",
    "clear_registry_cache",
    "get_bucket",
    "get_bucket_by_name",
    "get_bucket_by_type",
    "get_registry",
    "list_buckets",
    "load_buckets",
]


# ---------------------------------------------------------------------------
# Path structure constants (for validation)
# ---------------------------------------------------------------------------

PATH_STRUCTURE = {
    "audio_prompts": "audio-prompts",
    "tts_output_studio": "tts-audio/studio",
    "tts_output_playground": "tts-audio/playground",
    "logs_worker": "logs/worker",
    "logs_backend": "logs/backend",
}


class S3ConfigError(Exception):
    """Raised when S3 configuration or operation fails."""


class S3Client:
    """
    Registry-backed S3 client.

    Creates and caches one boto3 S3 client per configured bucket type
    (misc, video, audio, tts, 11lab).  All methods accept a ``bucket_type``
    parameter that resolves to the appropriate client and bucket via the registry.

    Raises:
        ImportError: If boto3 is not installed.
        S3ConfigError: If required configuration is missing.
    """

    def __init__(self, max_retries: int = 3) -> None:
        if not BOTO3_AVAILABLE:
            raise ImportError(
                "boto3 is required for S3 operations. "
                "Install it with: pip install boto3"
            )

        self.max_retries = max_retries
        self._boto_config = Config(
            retries={"max_attempts": max_retries, "mode": "adaptive"},
            signature_version="s3v4",
        )

        # Eagerly load registry to surface config errors at startup
        registry = get_registry()
        if not registry:
            raise S3ConfigError(
                "No S3 buckets configured. "
                "Ensure config/buckets.toml exists and S3_<TYPE>_* env vars are set."
            )

        # Per-bucket boto3 clients, keyed by unique bucket_name
        self._clients: dict[str, object] = {}
        for cfg in registry.values():
            self._clients[cfg.bucket_name] = self._create_client(cfg)

        logger.info("S3Client initialized with %d bucket(s):", len(registry))
        for cfg in registry.values():
            logger.info(
                "  [%s] %s @ %s (region=%s)",
                cfg.type,
                cfg.bucket_name,
                cfg.endpoint_url,
                cfg.region,
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _create_client(self, cfg: S3BucketConfig) -> object:
        """Create a boto3 S3 client for the given bucket config."""
        return boto3.client("s3", **cfg.get_client_kwargs())

    def _resolve(self, bucket_type: str) -> tuple[object, str]:
        """Return (boto3_client, bucket_name) for the given type or bucket name.

        Raises:
            S3ConfigError: If the bucket type is not registered.
        """
        try:
            cfg = get_bucket(bucket_type)
        except KeyError as exc:
            raise S3ConfigError(
                f"Unknown bucket type or name '{bucket_type}'. "
                f"Available: {[c.type for c in list_buckets()]}"
            ) from exc

        client = self._clients.get(cfg.bucket_name)
        if client is None:
            raise S3ConfigError(
                f"boto3 client for bucket '{cfg.bucket_name}' was not initialised."
            )
        return client, cfg.bucket_name

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def upload_file(
        self,
        local_path: str,
        remote_path: str,
        bucket_type: str = "audio",
        metadata: dict[str, str] | None = None,
        content_type: str | None = None,
    ) -> str:
        """Upload a file to S3 with automatic retry.

        Args:
            local_path:   Path to local file.
            remote_path:  S3 object key (path within bucket).
            bucket_type:  "misc", "video", or "audio" (or unique bucket name).
            metadata:     Optional metadata tags for the object.
            content_type: Optional content type (auto-detected if not provided).

        Returns:
            ``remote_path`` on success.

        Raises:
            S3ConfigError: If upload fails after retries.
            FileNotFoundError: If local file doesn't exist.
        """
        if not os.path.exists(local_path):
            raise FileNotFoundError(f"Local file not found: {local_path}")

        client, bucket_name = self._resolve(bucket_type)

        if not content_type:
            content_type = self._get_content_type(local_path)

        extra_args: dict[str, object] = {"ContentType": content_type}
        if metadata:
            extra_args["Metadata"] = metadata

        logger.info("Uploading %s → s3://%s/%s", local_path, bucket_name, remote_path)

        try:
            client.upload_file(  # type: ignore[attr-defined]
                Filename=local_path,
                Bucket=bucket_name,
                Key=remote_path,
                ExtraArgs=extra_args,
            )
            logger.info("Upload successful: %s", remote_path)
            return remote_path

        except (ClientError, BotoCoreError) as e:
            msg = f"Failed to upload {local_path} → {bucket_name}/{remote_path}: {e}"
            logger.error(msg)
            raise S3ConfigError(msg) from e

    def upload_audio(
        self,
        local_path: str,
        remote_path: str,
        bucket_type: str = "audio",
        job_id: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> str:
        """Upload an audio file.

        Args:
            local_path:  Path to local audio file.
            remote_path: S3 object key.
            bucket_type: Defaults to ``"audio"`` (TTS results bucket).
            job_id:      Optional job ID (for logging only; not stored in S3).
            metadata:    Optional metadata (may not be supported by all providers).

        Returns:
            ``remote_path`` on success.
        """
        # Some S3 providers (e.g. Filebase) don't support user-defined metadata
        # on PUT operations — skip to avoid AccessDenied errors.
        return self.upload_file(
            local_path=local_path,
            remote_path=remote_path,
            bucket_type=bucket_type,
            metadata=None,
            content_type="audio/wav",
        )

    def download_file(
        self,
        remote_path: str,
        local_path: str,
        bucket_type: str = "misc",
        max_retries: int | None = None,
    ) -> str:
        """Download a file from S3 with retry logic.

        Args:
            remote_path: S3 object key.
            local_path:  Local destination path.
            bucket_type: "misc", "video", or "audio" (or unique bucket name).
            max_retries: Override default max_retries.

        Returns:
            Local file path.

        Raises:
            S3ConfigError: If download fails after retries.
        """
        retries = max_retries if max_retries is not None else self.max_retries
        client, bucket_name = self._resolve(bucket_type)

        logger.info("Downloading s3://%s/%s → %s", bucket_name, remote_path, local_path)

        for attempt in range(1, retries + 1):
            try:
                os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
                client.download_file(  # type: ignore[attr-defined]
                    Bucket=bucket_name,
                    Key=remote_path,
                    Filename=local_path,
                )
                logger.info("Download successful: %s", local_path)
                return local_path

            except (ClientError, BotoCoreError) as e:
                if attempt == retries:
                    msg = (
                        f"Failed to download {remote_path} "
                        f"after {retries} attempts: {e}"
                    )
                    logger.error(msg)
                    raise S3ConfigError(msg) from e

                delay = 2 ** (attempt - 1)
                logger.warning(
                    "Download attempt %d/%d failed: %s. Retrying in %ds…",
                    attempt,
                    retries,
                    e,
                    delay,
                )
                time.sleep(delay)

    def file_exists(self, remote_path: str, bucket_type: str = "audio") -> bool:
        """Check if a file exists in S3.

        Args:
            remote_path: S3 object key.
            bucket_type: "misc", "video", or "audio" (or unique bucket name).

        Returns:
            True if the file exists, False otherwise.
        """
        client, bucket_name = self._resolve(bucket_type)
        try:
            client.head_object(Bucket=bucket_name, Key=remote_path)  # type: ignore[attr-defined]
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return False
            raise S3ConfigError(f"Error checking file existence: {e}") from e

    def delete_file(self, remote_path: str, bucket_type: str = "audio") -> bool:
        """Delete a file from S3.

        Args:
            remote_path: S3 object key.
            bucket_type: "misc", "video", or "audio" (or unique bucket name).

        Returns:
            True if deleted successfully.

        Raises:
            S3ConfigError: If deletion fails.
        """
        client, bucket_name = self._resolve(bucket_type)
        logger.info("Deleting s3://%s/%s", bucket_name, remote_path)
        try:
            client.delete_object(Bucket=bucket_name, Key=remote_path)  # type: ignore[attr-defined]
            logger.info("Deleted: %s", remote_path)
            return True
        except (ClientError, BotoCoreError) as e:
            msg = f"Failed to delete {remote_path}: {e}"
            logger.error(msg)
            raise S3ConfigError(msg) from e

    def generate_presigned_url(
        self,
        remote_path: str,
        bucket_type: str = "audio",
        expiration: int = 3600,
        http_method: str = "GET",
    ) -> str:
        """Generate a presigned URL for temporary access to an S3 object.

        Args:
            remote_path: S3 object key.
            bucket_type: "misc", "video", or "audio" (or unique bucket name).
            expiration:  URL expiration in seconds (default: 1 hour).
            http_method: "GET" or "PUT".

        Returns:
            Presigned URL string.

        Raises:
            S3ConfigError: If URL generation fails.
        """
        client, bucket_name = self._resolve(bucket_type)
        try:
            client_method = "get_object" if http_method == "GET" else "put_object"
            url: str = client.generate_presigned_url(  # type: ignore[attr-defined]
                ClientMethod=client_method,
                Params={"Bucket": bucket_name, "Key": remote_path},
                ExpiresIn=expiration,
            )
            logger.debug(
                "Generated presigned URL for %s (expires in %ds)",
                remote_path,
                expiration,
            )
            return url
        except (ClientError, BotoCoreError) as e:
            msg = f"Failed to generate presigned URL for {remote_path}: {e}"
            logger.error(msg)
            raise S3ConfigError(msg) from e

    def list_files(
        self,
        prefix: str,
        bucket_type: str = "audio",
        max_keys: int = 1000,
    ) -> list[str]:
        """List files in S3 with a given prefix.

        Args:
            prefix:      S3 key prefix (directory path).
            bucket_type: "misc", "video", or "audio" (or unique bucket name).
            max_keys:    Maximum number of keys to return.

        Returns:
            List of S3 object keys.

        Raises:
            S3ConfigError: If listing fails.
        """
        client, bucket_name = self._resolve(bucket_type)
        try:
            response = client.list_objects_v2(  # type: ignore[attr-defined]
                Bucket=bucket_name,
                Prefix=prefix,
                MaxKeys=max_keys,
            )
            if "Contents" not in response:
                return []
            return [obj["Key"] for obj in response["Contents"]]
        except (ClientError, BotoCoreError) as e:
            msg = f"Failed to list files with prefix '{prefix}': {e}"
            logger.error(msg)
            raise S3ConfigError(msg) from e

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_content_type(file_path: str) -> str:
        """Determine content type from file extension."""
        ext = os.path.splitext(file_path)[1].lower()
        content_types = {
            ".wav": "audio/wav",
            ".mp3": "audio/mpeg",
            ".mp4": "video/mp4",
            ".json": "application/json",
            ".txt": "text/plain",
            ".log": "text/plain",
        }
        return content_types.get(ext, "application/octet-stream")
