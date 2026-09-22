"""
Integration tests for S3 bucket connectivity and round-trip operations.

These tests require real S3 credentials in .env:
  - S3_TTS_ACCESS_KEY_ID (used as sentinel — if absent, all tests are skipped)
  - S3_<TYPE>_ENDPOINT_URL / S3_<TYPE>_ACCESS_KEY_ID / S3_<TYPE>_SECRET_ACCESS_KEY
    for each of the five bucket types: misc, video, audio, tts, 11lab

Run:
    uv run pytest tests/pytest/test_s3_integration.py -v
"""

from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

import pytest
import requests

from services.storage.s3_config import S3Client

# ---------------------------------------------------------------------------
# Guard — skip entire module when S3 is not configured
# ---------------------------------------------------------------------------

_S3_CONFIGURED = os.getenv("S3_TTS_ACCESS_KEY_ID") is not None

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def s3() -> S3Client:
    """Session-scoped S3Client backed by real .env credentials."""
    return S3Client()


@pytest.fixture(scope="function")
def tts_test_key() -> str:
    """Unique S3 key under tts-audio/ for each test; guaranteed not to collide."""
    return f"tts-audio/integration-test/{uuid.uuid4()}.txt"


@pytest.fixture(scope="function")
def tmp_text_file() -> str:
    """Temporary local text file with known content."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as fh:
        fh.write(f"integration-test-content-{uuid.uuid4()}")
        path = fh.name
    yield path
    if os.path.exists(path):
        os.remove(path)


# ---------------------------------------------------------------------------
# 1. Bucket Reachability
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _S3_CONFIGURED, reason="S3_TTS_ACCESS_KEY_ID not set")
class TestS3BucketReachability:
    """Verify that each of the five buckets is reachable (head_bucket)."""

    @pytest.mark.parametrize(
        "bucket_type",
        ["misc", "video", "audio", "tts", "11lab"],
        ids=["misc", "video", "audio", "tts", "11lab"],
    )
    def test_bucket_reachable(self, s3: S3Client, bucket_type: str) -> None:
        """head_bucket must succeed — confirms credentials and bucket existence."""
        boto_client, bucket_name = s3._resolve(bucket_type)
        # Raises ClientError (e.g. 403/404) if bucket is unreachable
        boto_client.head_bucket(Bucket=bucket_name)


# ---------------------------------------------------------------------------
# 2. TTS Bucket Round-Trip
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _S3_CONFIGURED, reason="S3_TTS_ACCESS_KEY_ID not set")
class TestS3TTSBucketRoundTrip:
    """Upload → verify → download → delete on the TTS bucket (sole writer)."""

    @pytest.fixture(autouse=True)
    def cleanup_s3_key(self, s3: S3Client, tts_test_key: str):
        """Always delete the test object after the test, even on failure."""
        yield
        try:
            boto_client, bucket_name = s3._resolve("tts")
            boto_client.delete_object(Bucket=bucket_name, Key=tts_test_key)
        except Exception:
            pass  # Best-effort cleanup

    def test_upload_and_download_roundtrip(
        self, s3: S3Client, tts_test_key: str, tmp_text_file: str
    ) -> None:
        """Upload a file then download it and verify the content is identical."""
        # Record content before upload
        original_content = Path(tmp_text_file).read_text()

        # Upload
        s3.upload_file(
            local_path=tmp_text_file,
            remote_path=tts_test_key,
            bucket_type="tts",
        )

        # Download to a different temp file
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as fh:
            download_path = fh.name
        try:
            s3.download_file(
                remote_path=tts_test_key,
                local_path=download_path,
                bucket_type="tts",
            )
            downloaded_content = Path(download_path).read_text()
            assert downloaded_content == original_content
        finally:
            if os.path.exists(download_path):
                os.remove(download_path)

    def test_upload_with_metadata(self, s3: S3Client, tmp_text_file: str) -> None:
        """Metadata tags must survive the upload round-trip (head_object check).

        Uses the misc bucket (Cloudflare R2) because Filebase (TTS bucket) does
        not permit user metadata on PutObject. The misc bucket is on a provider
        that supports it.
        """
        misc_key = f"integration-test/{uuid.uuid4()}.txt"
        job_id = str(uuid.uuid4())
        boto_client, bucket_name = s3._resolve("misc")
        try:
            boto_client.put_object(
                Bucket=bucket_name,
                Key=misc_key,
                Body=Path(tmp_text_file).read_bytes(),
                ContentType="text/plain",
                Metadata={"job_id": job_id},
            )
            head = boto_client.head_object(Bucket=bucket_name, Key=misc_key)
            assert head["Metadata"].get("job_id") == job_id
        finally:
            # Clean up misc key
            try:
                boto_client.delete_object(Bucket=bucket_name, Key=misc_key)
            except Exception:
                pass

    def test_presigned_url_is_reachable(
        self, s3: S3Client, tts_test_key: str, tmp_text_file: str
    ) -> None:
        """A GET presigned URL must be HTTP-accessible and return 200."""
        s3.upload_file(
            local_path=tmp_text_file,
            remote_path=tts_test_key,
            bucket_type="tts",
        )

        url = s3.generate_presigned_url(
            remote_path=tts_test_key,
            bucket_type="tts",
            expiration=300,
        )
        assert url.startswith("http")

        response = requests.get(url, timeout=10)
        assert response.status_code == 200

    def test_upload_overwrite(
        self, s3: S3Client, tts_test_key: str, tmp_text_file: str
    ) -> None:
        """Uploading to the same key twice should silently overwrite."""
        # First upload
        s3.upload_file(
            local_path=tmp_text_file,
            remote_path=tts_test_key,
            bucket_type="tts",
        )

        # Write different content and upload again
        new_content = f"overwritten-{uuid.uuid4()}"
        Path(tmp_text_file).write_text(new_content)
        s3.upload_file(
            local_path=tmp_text_file,
            remote_path=tts_test_key,
            bucket_type="tts",
        )

        # Download and verify the new content is present
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as fh:
            download_path = fh.name
        try:
            s3.download_file(
                remote_path=tts_test_key,
                local_path=download_path,
                bucket_type="tts",
            )
            assert Path(download_path).read_text() == new_content
        finally:
            if os.path.exists(download_path):
                os.remove(download_path)


# ---------------------------------------------------------------------------
# 3. Audio Bucket (Read-Only from worker's perspective)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _S3_CONFIGURED, reason="S3_TTS_ACCESS_KEY_ID not set")
class TestS3AudioBucketReadOnly:
    """Audio bucket: credentials must be valid and presigned URLs generatable."""

    def test_audio_bucket_reachable(self, s3: S3Client) -> None:
        """head_bucket must succeed — audio credentials are valid."""
        boto_client, bucket_name = s3._resolve("audio")
        boto_client.head_bucket(Bucket=bucket_name)

    def test_presigned_url_for_audio(self, s3: S3Client) -> None:
        """generate_presigned_url must return a URL string (does not require an existing object)."""
        # Any key is fine — we're testing URL generation, not object existence
        fake_key = f"voice-recordings/integration-test/{uuid.uuid4()}.wav"
        url = s3.generate_presigned_url(
            remote_path=fake_key,
            bucket_type="audio",
            expiration=60,
        )
        assert url.startswith("http")
        assert fake_key in url or fake_key.split("/")[-1] in url
