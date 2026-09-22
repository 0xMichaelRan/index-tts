"""
Unit tests for services/s3_registry.py.

Tests cover:
  - Loading from TOML with env var credential injection
  - Lookup by type / name
  - Validation of missing credentials
  - Duplicate bucket name detection
  - Registry caching and cache clearing
  - S3BucketConfig.get_client_kwargs()
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from services.storage.s3_registry import (
    S3BucketConfig,
    clear_registry_cache,
    get_bucket,
    get_bucket_by_name,
    get_bucket_by_type,
    get_registry,
    list_buckets,
    load_buckets,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MINIMAL_TOML = textwrap.dedent(
    """\
    [[bucket]]
    name = "misc"
    type = "misc"
    bucket_name = "my-misc-bucket"
    region = "auto"
    enabled = true

    [[bucket]]
    name = "audio"
    type = "audio"
    bucket_name = "my-audio-bucket"
    region = "eu-west-1"
    enabled = true
    """
)

MISC_ENV = {
    "S3_MISC_ENDPOINT_URL": "https://misc.example.com",
    "S3_MISC_ACCESS_KEY_ID": "misc-key",
    "S3_MISC_SECRET_ACCESS_KEY": "misc-secret",
}

AUDIO_ENV = {
    "S3_AUDIO_ENDPOINT_URL": "https://audio.example.com",
    "S3_AUDIO_ACCESS_KEY_ID": "audio-key",
    "S3_AUDIO_SECRET_ACCESS_KEY": "audio-secret",
}


def make_toml_file(tmp_path: Path, content: str) -> Path:
    """Write a temporary TOML file and return its path."""
    p = tmp_path / "buckets.toml"
    p.write_text(content)
    return p


# ---------------------------------------------------------------------------
# Always clear registry cache between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_registry_cache()
    yield
    clear_registry_cache()


# ===========================================================================
# TestLoadBuckets
# ===========================================================================


class TestLoadBuckets:
    """Tests for load_buckets()."""

    def test_loads_both_buckets(self, tmp_path):
        toml_file = make_toml_file(tmp_path, MINIMAL_TOML)
        env = {**MISC_ENV, **AUDIO_ENV}
        with patch.dict(os.environ, env, clear=False):
            registry = load_buckets(toml_file)

        assert "my-misc-bucket" in registry
        assert "my-audio-bucket" in registry

    def test_loads_all_five_project_buckets(self):
        """Verify that default config/buckets.toml contains all 5 architecture buckets."""
        five_env = {
            "S3_MISC_ENDPOINT_URL": "https://s3.example.com",
            "S3_MISC_ACCESS_KEY_ID": "misc-key",
            "S3_MISC_SECRET_ACCESS_KEY": "misc-secret",
            "S3_VIDEO_ENDPOINT_URL": "https://s3.example.com",
            "S3_VIDEO_ACCESS_KEY_ID": "video-key",
            "S3_VIDEO_SECRET_ACCESS_KEY": "video-secret",
            "S3_AUDIO_ENDPOINT_URL": "https://s3.example.com",
            "S3_AUDIO_ACCESS_KEY_ID": "audio-key",
            "S3_AUDIO_SECRET_ACCESS_KEY": "audio-secret",
            "S3_TTS_ENDPOINT_URL": "https://s3.example.com",
            "S3_TTS_ACCESS_KEY_ID": "tts-key",
            "S3_TTS_SECRET_ACCESS_KEY": "tts-secret",
            "S3_11LAB_ENDPOINT_URL": "https://s3.example.com",
            "S3_11LAB_ACCESS_KEY_ID": "lab-key",
            "S3_11LAB_SECRET_ACCESS_KEY": "lab-secret",
        }
        with patch.dict(os.environ, five_env, clear=False):
            reg = load_buckets()
        assert "klatu-misc" in reg
        assert "klatu-video" in reg
        assert "klatu-audio" in reg
        assert "klatu-tts" in reg
        assert "klatu-11lab" in reg
        assert reg["klatu-tts"].type == "tts"
        assert reg["klatu-11lab"].type == "11lab"

    def test_misc_bucket_config(self, tmp_path):
        toml_file = make_toml_file(tmp_path, MINIMAL_TOML)
        with patch.dict(os.environ, {**MISC_ENV, **AUDIO_ENV}, clear=False):
            registry = load_buckets(toml_file)

        cfg = registry["my-misc-bucket"]
        assert cfg.name == "misc"
        assert cfg.type == "misc"
        assert cfg.bucket_name == "my-misc-bucket"
        assert cfg.endpoint_url == "https://misc.example.com"
        assert cfg.access_key_id == "misc-key"
        assert cfg.secret_access_key == "misc-secret"
        assert cfg.region == "auto"
        assert cfg.enabled is True

    def test_audio_bucket_region(self, tmp_path):
        toml_file = make_toml_file(tmp_path, MINIMAL_TOML)
        with patch.dict(os.environ, {**MISC_ENV, **AUDIO_ENV}, clear=False):
            registry = load_buckets(toml_file)
        assert registry["my-audio-bucket"].region == "eu-west-1"

    def test_env_override_bucket_name(self, tmp_path):
        toml_file = make_toml_file(tmp_path, MINIMAL_TOML)
        env = {
            **MISC_ENV,
            **AUDIO_ENV,
            "S3_MISC_BUCKET_NAME": "overridden-misc",
        }
        with patch.dict(os.environ, env, clear=False):
            registry = load_buckets(toml_file)
        assert "overridden-misc" in registry
        assert "my-misc-bucket" not in registry

    def test_env_override_region(self, tmp_path):
        toml_file = make_toml_file(tmp_path, MINIMAL_TOML)
        env = {**MISC_ENV, **AUDIO_ENV, "S3_MISC_REGION": "us-east-1"}
        with patch.dict(os.environ, env, clear=False):
            registry = load_buckets(toml_file)
        assert registry["my-misc-bucket"].region == "us-east-1"

    def test_disabled_bucket_skipped(self, tmp_path):
        toml = MINIMAL_TOML.replace('name = "audio"', 'name = "audio"').replace(
            '[[bucket]]\nname = "audio"', '[[bucket]]\nenabled = false\nname = "audio"'
        )
        toml_file = make_toml_file(tmp_path, toml)
        with patch.dict(os.environ, {**MISC_ENV, **AUDIO_ENV}, clear=False):
            registry = load_buckets(toml_file)
        assert "my-audio-bucket" not in registry

    def test_missing_endpoint_skips_bucket(self, tmp_path):
        toml_file = make_toml_file(tmp_path, MINIMAL_TOML)
        # Provide AUDIO creds only — MISC endpoint missing
        with patch.dict(os.environ, AUDIO_ENV, clear=False):
            registry = load_buckets(toml_file)
        assert "my-misc-bucket" not in registry
        assert "my-audio-bucket" in registry

    def test_missing_access_key_skips_bucket(self, tmp_path):
        toml_file = make_toml_file(tmp_path, MINIMAL_TOML)
        env = {
            "S3_MISC_ENDPOINT_URL": "https://misc.example.com",
            "S3_MISC_SECRET_ACCESS_KEY": "misc-secret",
            **AUDIO_ENV,
        }
        with patch.dict(os.environ, env, clear=False):
            registry = load_buckets(toml_file)
        assert "my-misc-bucket" not in registry

    def test_empty_toml_returns_empty_registry(self, tmp_path):
        toml_file = make_toml_file(tmp_path, "# empty\n")
        with patch.dict(os.environ, {**MISC_ENV, **AUDIO_ENV}, clear=False):
            registry = load_buckets(toml_file)
        assert registry == {}

    def test_missing_file_returns_empty_registry(self, tmp_path):
        missing = tmp_path / "nonexistent.toml"
        with patch.dict(os.environ, {**MISC_ENV, **AUDIO_ENV}, clear=False):
            registry = load_buckets(missing)
        assert registry == {}

    def test_duplicate_bucket_name_raises(self, tmp_path):
        dupe_toml = textwrap.dedent(
            """\
            [[bucket]]
            name = "misc"
            type = "misc"
            bucket_name = "shared-bucket"
            region = "auto"

            [[bucket]]
            name = "audio"
            type = "audio"
            bucket_name = "shared-bucket"
            region = "auto"
            """
        )
        toml_file = make_toml_file(tmp_path, dupe_toml)
        env = {**MISC_ENV, **AUDIO_ENV}
        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(ValueError, match="Duplicate bucket name"):
                load_buckets(toml_file)


# ===========================================================================
# TestS3BucketConfig
# ===========================================================================


class TestS3BucketConfig:
    """Tests for S3BucketConfig.get_client_kwargs()."""

    def _make_cfg(self, region: str = "auto") -> S3BucketConfig:
        return S3BucketConfig(
            name="audio",
            type="audio",
            bucket_name="my-audio",
            endpoint_url="https://s3.example.com",
            access_key_id="key",
            secret_access_key="secret",
            region=region,
        )

    def test_kwargs_always_has_endpoint_and_creds(self):
        cfg = self._make_cfg()
        kwargs = cfg.get_client_kwargs()
        assert kwargs["endpoint_url"] == "https://s3.example.com"
        assert kwargs["aws_access_key_id"] == "key"
        assert kwargs["aws_secret_access_key"] == "secret"

    def test_kwargs_excludes_region_when_auto(self):
        cfg = self._make_cfg(region="auto")
        kwargs = cfg.get_client_kwargs()
        assert "region_name" not in kwargs

    def test_kwargs_includes_region_when_explicit(self):
        cfg = self._make_cfg(region="us-east-1")
        kwargs = cfg.get_client_kwargs()
        assert kwargs["region_name"] == "us-east-1"

    def test_kwargs_has_sigv4_config(self):
        from botocore.config import Config

        cfg = self._make_cfg()
        kwargs = cfg.get_client_kwargs()
        assert isinstance(kwargs["config"], Config)
        assert kwargs["config"].signature_version == "s3v4"


# ===========================================================================
# TestRegistryLookups (integration with module-level cache)
# ===========================================================================


class TestRegistryLookups:
    """Tests for get_registry, get_bucket, get_bucket_by_type, list_buckets."""

    @pytest.fixture()
    def populated_registry(self, tmp_path, monkeypatch):
        """Patch CONFIG_FILE and env so get_registry() loads test data."""
        toml_file = make_toml_file(tmp_path, MINIMAL_TOML)
        monkeypatch.setattr("services.storage.s3_registry.CONFIG_FILE", toml_file)
        env = {**MISC_ENV, **AUDIO_ENV}
        with patch.dict(os.environ, env, clear=False):
            yield

    def test_get_registry_returns_dict(self, populated_registry):
        reg = get_registry()
        assert isinstance(reg, dict)
        assert len(reg) == 2

    def test_list_buckets_returns_list(self, populated_registry):
        buckets = list_buckets()
        assert len(buckets) == 2
        types = {b.type for b in buckets}
        assert types == {"misc", "audio"}

    def test_get_bucket_by_type_misc(self, populated_registry):
        cfg = get_bucket_by_type("misc")
        assert cfg.type == "misc"
        assert cfg.bucket_name == "my-misc-bucket"

    def test_get_bucket_by_type_audio(self, populated_registry):
        cfg = get_bucket_by_type("audio")
        assert cfg.type == "audio"
        assert cfg.bucket_name == "my-audio-bucket"

    def test_get_bucket_by_type_unknown_raises(self, populated_registry):
        with pytest.raises(KeyError, match="video"):
            get_bucket_by_type("video")

    def test_get_bucket_by_name(self, populated_registry):
        cfg = get_bucket_by_name("my-misc-bucket")
        assert cfg.type == "misc"

    def test_get_bucket_by_name_unknown_raises(self, populated_registry):
        with pytest.raises(KeyError, match="nonexistent"):
            get_bucket_by_name("nonexistent")

    def test_get_bucket_by_type_string(self, populated_registry):
        cfg = get_bucket("misc")
        assert cfg.bucket_name == "my-misc-bucket"

    def test_get_bucket_by_bucket_name(self, populated_registry):
        cfg = get_bucket("my-audio-bucket")
        assert cfg.type == "audio"

    def test_get_bucket_unknown_raises(self, populated_registry):
        with pytest.raises(KeyError):
            get_bucket("totally-unknown")

    def test_registry_is_cached(self, populated_registry):
        """Calling get_registry() twice returns the same object."""
        reg1 = get_registry()
        reg2 = get_registry()
        assert reg1 is reg2

    def test_clear_cache_reloads(self, populated_registry):
        reg1 = get_registry()
        clear_registry_cache()
        reg2 = get_registry()
        # Same content but different object (reloaded)
        assert reg1 == reg2
        assert reg1 is not reg2
