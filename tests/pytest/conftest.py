"""
conftest.py — pytest configuration for tests/pytest/

Loads .env from the project root BEFORE test collection so that environment
variables (e.g. RABBITMQ_URL, DATABASE_URL, S3_* keys) are available to
@pytest.mark.skipif decorators and all test code.
"""

from pathlib import Path

from dotenv import load_dotenv

# Resolve the project root (two levels up from this conftest)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

load_dotenv(_PROJECT_ROOT / ".env", override=False)
