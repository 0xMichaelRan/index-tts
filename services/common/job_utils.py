"""
Shared utilities for job data handling.

These helpers centralise patterns that appear across tts_worker.py,
synthesis_pipeline.py, and tts_job_service.py so that any future schema
changes (e.g. key renames) only need to be made in one place.
"""

from __future__ import annotations

from typing import Any


def extract_job_id(job_data: dict[str, Any]) -> str | None:
    """Resolve jobId from camelCase key.

    The RabbitMQ message schema strictly uses ``jobId`` (camelCase).
    This function returns the value as a string, or ``None`` if absent.

    Args:
        job_data: Parsed JSON payload from the RabbitMQ message body.

    Returns:
        The job ID as a string, or ``None`` if absent.

    Example::

        >>> extract_job_id({"jobId": 42})
        '42'
        >>> extract_job_id({})
        None
    """
    val = job_data.get("jobId")
    return str(val) if val is not None else None
