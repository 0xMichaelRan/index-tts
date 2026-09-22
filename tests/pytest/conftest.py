"""
conftest.py — pytest configuration for tests/pytest/

Loads .env from the project root BEFORE test collection so that environment
variables (e.g. RABBITMQ_URL, DATABASE_URL, S3_* keys) are available to
@pytest.mark.skipif decorators and all test code.

Shared fixtures:
  - s3_client: Session-scoped S3Client backed by real .env credentials.
"""

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

# Resolve the project root (two levels up from this conftest)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

load_dotenv(_PROJECT_ROOT / ".env", override=False)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def s3_client():
    """Session-scoped S3Client — reuse across all S3 integration tests.

    Skipped automatically when S3_TTS_ACCESS_KEY_ID is absent (i.e. S3 not
    configured), so individual test classes don't need to repeat the guard.
    """
    if os.getenv("S3_TTS_ACCESS_KEY_ID") is None:
        pytest.skip("S3_TTS_ACCESS_KEY_ID not set — S3 integration tests skipped")
    from services.storage.s3_config import S3Client

    return S3Client()
