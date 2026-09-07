"""
Shared utilities for job data handling.

These helpers centralise patterns that appear across tts_worker.py,
synthesis_pipeline.py, and tts_job_service.py so that any future schema
changes (e.g. key renames) only need to be made in one place.
"""

from __future__ import annotations

from typing import Any


def extract_job_id(job_data: dict[str, Any]) -> str | None:
    """Resolve jobId from camelCase or snake_case key.

    The RabbitMQ message schema uses ``jobId`` (camelCase), but some
    internal paths fall back to ``job_id`` (snake_case) for backwards
    compatibility.  This function checks both and returns the first
    non-None value as a string, or ``None`` if neither key is present.

    Args:
        job_data: Parsed JSON payload from the RabbitMQ message body.

    Returns:
        The job ID as a string, or ``None`` if both keys are absent.

    Example::

        >>> extract_job_id({"jobId": 42})
        '42'
        >>> extract_job_id({"job_id": "abc"})
        'abc'
        >>> extract_job_id({})
        None
    """
    val = job_data.get("jobId")
    if val is None:
        val = job_data.get("job_id")
    return str(val) if val is not None else None
