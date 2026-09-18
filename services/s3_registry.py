"""
IndexTTS Worker – S3 Bucket Registry and Configuration Loader.

Loads S3 bucket configurations from ``config/buckets.toml`` combined with
environment variables following the ``S3_<TYPE>_*`` naming convention:

  - S3_<TYPE>_ENDPOINT_URL
  - S3_<TYPE>_ACCESS_KEY_ID
  - S3_<TYPE>_SECRET_ACCESS_KEY
  - S3_<TYPE>_BUCKET_NAME  (optional override; falls back to TOML bucket_name)
  - S3_<TYPE>_REGION       (optional override; falls back to TOML region)

All bucket names are unique.  When reading or writing to S3 the bucket name
(or its logical type, e.g. "misc", "video", "audio") must always be supplied.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from botocore.config import Config as BotocoreConfig

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

logger = logging.getLogger("s3.registry")

# ---------------------------------------------------------------------------
# Config file discovery
# ---------------------------------------------------------------------------

# Canonical location: <project_root>/config/buckets.toml
CONFIG_FILE = Path(__file__).resolve().parent.parent / "config" / "buckets.toml"
if not CONFIG_FILE.exists():
    # Fallback: buckets.toml at project root
    _fallback = Path(__file__).resolve().parent.parent / "buckets.toml"
    if _fallback.exists():
        CONFIG_FILE = _fallback


# ---------------------------------------------------------------------------
# S3BucketConfig
# ---------------------------------------------------------------------------


@dataclass
class S3BucketConfig:
    """Configuration for a single S3 bucket."""

    name: str  # Logical identifier, e.g. "misc", "video", "audio"
    type: str  # Type classification ("misc" | "video" | "audio")
    bucket_name: str  # Unique S3 bucket name (e.g. "klatu-misc")
    endpoint_url: str  # S3 endpoint URL
    access_key_id: str  # AWS / S3-compatible access key
    secret_access_key: str  # AWS / S3-compatible secret key
    region: str = "auto"
    description: str = ""
    enabled: bool = True

    def get_client_kwargs(self) -> dict[str, Any]:
        """Return keyword arguments suitable for boto3 S3 client creation.

        Always enforces SigV4 signing. Cloudflare R2 (and other S3-compatible
        providers) reject the legacy SigV2 query-string format that boto3 may
        fall back to when no region is set.
        """
        kwargs: dict[str, Any] = {
            "endpoint_url": self.endpoint_url,
            "aws_access_key_id": self.access_key_id,
            "aws_secret_access_key": self.secret_access_key,
            # Force SigV4 — required by Cloudflare R2
            "config": BotocoreConfig(signature_version="s3v4"),
        }
        if self.region and self.region != "auto":
            kwargs["region_name"] = self.region
        return kwargs


# ---------------------------------------------------------------------------
# Registry loader
# ---------------------------------------------------------------------------


def load_buckets(config_path: Path | None = None) -> dict[str, S3BucketConfig]:
    """Load and validate bucket configurations from TOML and environment variables.

    Returns a dictionary mapping unique ``bucket_name`` to ``S3BucketConfig``.

    Env-var resolution order for each bucket of type ``<TYPE>``:
    1. ``S3_<TYPE>_ENDPOINT_URL`` (required)
    2. ``S3_<TYPE>_ACCESS_KEY_ID`` (required)
    3. ``S3_<TYPE>_SECRET_ACCESS_KEY`` (required)
    4. ``S3_<TYPE>_BUCKET_NAME`` → falls back to TOML ``bucket_name``
    5. ``S3_<TYPE>_REGION``      → falls back to TOML ``region``
    """
    path = config_path or CONFIG_FILE
    buckets_by_name: dict[str, S3BucketConfig] = {}

    if not path.is_file():
        logger.warning("Bucket configuration file '%s' not found.", path)
        return buckets_by_name

    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except Exception as exc:
        logger.error("Failed to parse %s: %s", path, exc)
        return buckets_by_name

    entries = data.get("bucket", [])
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if not entry.get("enabled", True):
            continue

        b_name = str(entry.get("name", "")).strip().lower()
        b_type = str(entry.get("type", b_name)).strip().lower()
        if not b_type:
            continue

        prefix = f"S3_{b_type.upper()}"

        endpoint_url = (
            os.getenv(f"{prefix}_ENDPOINT_URL", "").strip()
            or str(entry.get("endpoint_url", "")).strip()
        )
        access_key_id = os.getenv(f"{prefix}_ACCESS_KEY_ID", "").strip()
        secret_access_key = os.getenv(f"{prefix}_SECRET_ACCESS_KEY", "").strip()
        bucket_name = (
            os.getenv(f"{prefix}_BUCKET_NAME", "").strip()
            or str(entry.get("bucket_name", "")).strip()
        )
        region = (
            os.getenv(f"{prefix}_REGION", "").strip()
            or str(entry.get("region", "auto")).strip()
            or "auto"
        )
        description = str(entry.get("description", "")).strip()

        # Validate essential attributes are present
        missing: list[str] = []
        if not bucket_name:
            missing.append(f"bucket_name in TOML or {prefix}_BUCKET_NAME")
        if not endpoint_url:
            missing.append(f"{prefix}_ENDPOINT_URL")
        if not access_key_id:
            missing.append(f"{prefix}_ACCESS_KEY_ID")
        if not secret_access_key:
            missing.append(f"{prefix}_SECRET_ACCESS_KEY")

        if missing:
            logger.warning(
                "S3 bucket '%s' (type: %s) is missing required settings: %s; bucket disabled.",
                b_name,
                b_type,
                ", ".join(missing),
            )
            continue

        # Enforce unique bucket names
        if bucket_name in buckets_by_name:
            raise ValueError(
                f"Duplicate bucket name '{bucket_name}' detected. "
                "All bucket names must be unique."
            )

        config = S3BucketConfig(
            name=b_name,
            type=b_type,
            bucket_name=bucket_name,
            endpoint_url=endpoint_url,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            region=region,
            description=description,
            enabled=True,
        )

        buckets_by_name[bucket_name] = config

        logger.info(
            "Loaded S3 bucket '%s' (type=%s, endpoint=%s, region=%s)",
            bucket_name,
            b_type,
            endpoint_url,
            region,
        )

    return buckets_by_name


# ---------------------------------------------------------------------------
# Singleton registry cache
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, S3BucketConfig] | None = None
_TYPE_INDEX: dict[str, S3BucketConfig] | None = None


def clear_registry_cache() -> None:
    """Clear cached registry (useful in testing or runtime reload)."""
    global _REGISTRY, _TYPE_INDEX
    _REGISTRY = None
    _TYPE_INDEX = None


def get_registry() -> dict[str, S3BucketConfig]:
    """Return dictionary of all configured buckets indexed by unique bucket_name."""
    global _REGISTRY, _TYPE_INDEX
    if _REGISTRY is None:
        _REGISTRY = load_buckets()
        _TYPE_INDEX = {cfg.type: cfg for cfg in _REGISTRY.values()}
        # Also index by logical name (usually matches type)
        for cfg in _REGISTRY.values():
            if cfg.name not in _TYPE_INDEX:
                _TYPE_INDEX[cfg.name] = cfg
    return _REGISTRY


def list_buckets() -> list[S3BucketConfig]:
    """Return list of all registered S3BucketConfig objects."""
    return list(get_registry().values())


def get_bucket_by_name(bucket_name: str) -> S3BucketConfig:
    """Look up a bucket by its unique S3 bucket name.

    Raises KeyError if the bucket is not found.
    """
    reg = get_registry()
    if bucket_name in reg:
        return reg[bucket_name]
    raise KeyError(
        f"S3 bucket with name '{bucket_name}' not found. "
        f"Available buckets: {list(reg.keys())}"
    )


def get_bucket_by_type(bucket_type: str) -> S3BucketConfig:
    """Look up a bucket by its type ('misc', 'video', 'audio').

    Raises KeyError if no bucket is configured for the given type.
    """
    get_registry()  # Populate cache
    assert _TYPE_INDEX is not None
    key = bucket_type.strip().lower()
    if key in _TYPE_INDEX:
        return _TYPE_INDEX[key]
    raise KeyError(
        f"S3 bucket for type '{bucket_type}' not found. "
        f"Configured types: {list(_TYPE_INDEX.keys())}"
    )


def get_bucket(bucket_name_or_type: str) -> S3BucketConfig:
    """Primary accessor for S3 buckets.

    Looks up by the unique S3 ``bucket_name`` first.  If not found, also
    checks if the provided key matches a bucket type / logical name
    ('misc', 'video', 'audio').

    Raises KeyError if neither is found.
    """
    reg = get_registry()
    if bucket_name_or_type in reg:
        return reg[bucket_name_or_type]

    # Fallback to type / name lookup
    assert _TYPE_INDEX is not None
    key = bucket_name_or_type.strip().lower()
    if key in _TYPE_INDEX:
        return _TYPE_INDEX[key]

    raise KeyError(
        f"S3 bucket '{bucket_name_or_type}' not found. "
        f"Available bucket names: {list(reg.keys())}"
    )
