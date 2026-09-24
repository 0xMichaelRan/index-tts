from __future__ import annotations

from unittest.mock import MagicMock
import pytest
from fastapi.testclient import TestClient

from services.api.app import app
from services.api.deps import get_s3_status


@pytest.fixture
def client():
    # Clear overrides before each test
    app.dependency_overrides.clear()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_root_endpoint(client):
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "IndexTTS Unified Media Worker API"
    assert data["version"] == "2.0.0"
    assert "platform" in data
    assert "endpoints" in data
    assert "/health" in data["endpoints"]
    assert "/api/v1/tts/synthesize" in data["endpoints"]
    assert "/api/v1/vox/render" in data["endpoints"]


def test_health_healthy(client):
    app.dependency_overrides[get_s3_status] = lambda: True

    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["s3"]["status"] == "connected"
    assert "tts" in data["pipelines"]
    assert "vox" in data["pipelines"]


def test_health_degraded_when_s3_unavailable(client):
    app.dependency_overrides[get_s3_status] = lambda: False

    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "degraded"
    assert data["s3"]["status"] == "unavailable"
