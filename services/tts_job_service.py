"""
TTS job service for database tracking and ttsId generation.

Provides database persistence and lifecycle tracking for TTS jobs processed by the worker.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import os
import threading
from typing import Any

from services.logging_config import get_logger

logger = get_logger(__name__)

# Try importing database session and models
try:
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    from app.database import DatabaseSession
    from app.models import TTSJob

    DB_AVAILABLE = True
except ImportError as e:
    logger.warning(f"Database dependencies not available: {e}")
    DB_AVAILABLE = False


def _run_coroutine(coro: Any, timeout: float = 10.0) -> Any:
    """Run an async coroutine synchronously in a dedicated event loop thread."""
    result_container: dict[str, Any] = {}

    def worker():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(coro)
            result_container["result"] = result
        except Exception as e:
            result_container["error"] = e
        finally:
            loop.close()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout=timeout)

    if thread.is_alive():
        raise TimeoutError("Database operation timed out")

    if "error" in result_container:
        raise result_container["error"]

    return result_container.get("result")


class TTSJobService:
    """Manages creation and updates of tts_jobs records."""

    def __init__(self):
        self.enabled = DB_AVAILABLE and os.getenv("DATABASE_URL") is not None
        if not self.enabled:
            logger.warning(
                "TTSJobService: DATABASE_URL not configured or DB unavailable"
            )

    def create_job_record(self, job_data: dict[str, Any]) -> int | None:
        """
        Create a tts_jobs record and return the generated ttsId.

        All jobs (test and production) are persisted to the database.
        Test jobs are marked with is_test=True for filtering/analytics.

        Args:
            job_data: RabbitMQ job payload

        Returns:
            int ttsId (always generated, even for test jobs)
        """
        if not self.enabled:
            raise RuntimeError(
                "DATABASE_CONNECTION_FAILED: DATABASE_URL is required for TTS tracking"
            )

        is_test = job_data.get("isTest", False)

        async def _create() -> int:
            async with DatabaseSession() as db_session:
                raw_job_id = (
                    job_data.get("jobId")
                    if job_data.get("jobId") is not None
                    else job_data.get("job_id")
                )
                try:
                    job_id_int = int(raw_job_id)
                except (ValueError, TypeError):
                    job_id_int = 0

                job_type = job_data.get("jobType", "studio")
                if job_type not in ("studio", "playground", "rem"):
                    job_type = "studio"

                speed_ratio = job_data.get("speedRatio", 1.0)
                try:
                    ratio_val = float(speed_ratio)
                except (ValueError, TypeError):
                    ratio_val = 1.0

                tts_job = TTSJob(
                    job_id=job_id_int,
                    job_type=job_type,
                    status="processing",
                    is_test=is_test,
                    text=job_data.get("text"),
                    audio_prompt_path=job_data.get("audioPromptPath"),
                    language=job_data.get("spokenLang", "en"),
                    ratio=ratio_val,
                    started_at=datetime.now(timezone.utc),
                )
                try:
                    db_session.add(tts_job)
                    await db_session.commit()
                    await db_session.refresh(tts_job)
                    return tts_job.id
                except IntegrityError as e:
                    await db_session.rollback()
                    if "uq_tts_jobs_job_id" in str(e) or "job_id" in str(e):
                        logger.warning(
                            f"Job {job_id_int} already exists in database (retry/redelivery detected)"
                        )
                        stmt = select(TTSJob).where(TTSJob.job_id == job_id_int)
                        result = await db_session.execute(stmt)
                        existing_job = result.scalar_one_or_none()
                        if existing_job:
                            existing_job.status = "processing"
                            existing_job.started_at = datetime.now(timezone.utc)
                            await db_session.commit()
                            return existing_job.id
                    raise

        try:
            tts_id = _run_coroutine(_create())
            job_label = "[TEST]" if is_test else ""
            logger.info(f"[JOB {job_data.get('jobId')}] {job_label} Generated ttsId={tts_id}")
            return tts_id
        except Exception as e:
            logger.error(f"Failed to create tts_jobs record: {e}")
            raise RuntimeError(f"DATABASE_CONNECTION_FAILED: {e}") from e

    def update_job_status(self, tts_id: int | None, status: str, **kwargs: Any) -> None:
        """
        Update tts_jobs record status, results, or error details.

        Args:
            tts_id: Internal ttsId
            status: New status ('completed', 'failed', 'processing')
            **kwargs: Additional columns to update (e.g. audio_path, error_message)
        """
        if tts_id is None:
            return

        if not self.enabled:
            logger.warning(f"Cannot update tts_jobs id={tts_id}: database disabled")
            return

        async def _update() -> None:
            async with DatabaseSession() as db_session:
                tts_job = await db_session.get(TTSJob, tts_id)
                if tts_job:
                    tts_job.status = status
                    if status in ("completed", "failed"):
                        tts_job.completed_at = datetime.now(timezone.utc)

                    for key, value in kwargs.items():
                        if hasattr(tts_job, key):
                            setattr(tts_job, key, value)

                    await db_session.commit()

        try:
            _run_coroutine(_update())
            logger.debug(f"Updated tts_jobs id={tts_id} status={status}")
        except Exception as e:
            logger.error(f"Failed to update tts_jobs id={tts_id}: {e}")
