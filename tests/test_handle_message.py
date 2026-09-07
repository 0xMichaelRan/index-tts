"""
Unit tests for IndexTTSWorker._handle_message.

These tests exercise the method directly — no RabbitMQ connection required.
The key benefit of promoting message_callback to a named method (#4 in
code_structure_suggestions.md) is exactly this testability.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_worker():
    """Return an IndexTTSWorker whose heavy __init__ is fully mocked."""
    with (
        patch("services.tts_worker.configure_logging"),
        patch("services.tts_worker.get_logger", return_value=MagicMock()),
        patch("services.tts_worker.create_tts_engine", return_value=MagicMock()),
        patch("services.tts_worker.StorageManager", return_value=MagicMock()),
        patch("services.tts_worker.CacheManager", return_value=MagicMock()),
        patch("services.tts_worker.SynthesisPipeline", return_value=MagicMock()),
        patch("services.tts_worker.RabbitMQManager", return_value=MagicMock()),
        patch("services.tts_worker.signal"),
    ):
        from services.worker_config import WorkerConfig
        from services.tts_worker import IndexTTSWorker

        cfg = WorkerConfig(rabbitmq_url="amqp://localhost/")
        worker = IndexTTSWorker(config=cfg)
        # Silence the module-level logger that __init__ swapped in
        import services.tts_worker as tw_mod

        tw_mod.logger = MagicMock()
        return worker


def _make_amqp_args(body_dict, amqp_priority=None):
    """Build (ch, method, properties, body) fakes."""
    ch = MagicMock()
    method = SimpleNamespace(delivery_tag=42)
    properties = SimpleNamespace(priority=amqp_priority)
    body = json.dumps(body_dict).encode()
    return ch, method, properties, body


# ---------------------------------------------------------------------------
# Tests: shutdown guard
# ---------------------------------------------------------------------------


class TestHandleMessageShutdown:
    def test_rejects_and_requeues_when_shutdown_requested(self):
        worker = _make_worker()
        worker._shutdown_requested = True

        ch, method, properties, body = _make_amqp_args({"jobId": "j1", "text": "hi"})
        worker._handle_message(ch, method, properties, body)

        ch.basic_nack.assert_called_once_with(delivery_tag=42, requeue=True)
        worker.synthesis_pipeline.process_job.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: happy path
# ---------------------------------------------------------------------------


class TestHandleMessageSuccess:
    def test_processes_job_and_acknowledges(self):
        worker = _make_worker()
        worker.synthesis_pipeline.process_job.return_value = {
            "jobId": "j1",
            "ttsId": "t1",
            "audioUrl": "s3://bucket/j1.mp3",
        }

        ch, method, properties, body = _make_amqp_args(
            {"jobId": "j1", "text": "Hello world"}
        )
        worker._handle_message(ch, method, properties, body)

        worker.synthesis_pipeline.process_job.assert_called_once()
        worker.rabbitmq_manager.publish_result.assert_called_once()
        worker.rabbitmq_manager.acknowledge_message.assert_called_once_with(42)
        assert "j1" in worker._processed_jobs

    def test_job_tracked_in_processed_set(self):
        worker = _make_worker()
        worker.synthesis_pipeline.process_job.return_value = {"jobId": "abc"}

        ch, method, properties, body = _make_amqp_args({"jobId": "abc", "text": "x"})
        worker._handle_message(ch, method, properties, body)

        assert "abc" in worker._processed_jobs

    def test_snake_case_job_id_is_extracted(self):
        """extract_job_id falls back to job_id (snake_case) key."""
        worker = _make_worker()
        worker.synthesis_pipeline.process_job.return_value = {"job_id": "snake1"}

        ch, method, properties, body = _make_amqp_args(
            {"job_id": "snake1", "text": "Hello"}
        )
        worker._handle_message(ch, method, properties, body)

        assert "snake1" in worker._processed_jobs


# ---------------------------------------------------------------------------
# Tests: priority resolution
# ---------------------------------------------------------------------------


class TestHandleMessagePriority:
    def test_amqp_priority_takes_precedence_over_json(self):
        worker = _make_worker()
        worker.synthesis_pipeline.process_job.return_value = {"jobId": "p1"}

        # JSON says priority=1, AMQP header says 9
        ch, method, properties, body = _make_amqp_args(
            {"jobId": "p1", "priority": 1}, amqp_priority=9
        )
        worker._handle_message(ch, method, properties, body)

        call_kwargs = worker.rabbitmq_manager.publish_result.call_args
        assert call_kwargs.kwargs.get("priority") == 9

    def test_json_priority_used_when_no_amqp_header(self):
        worker = _make_worker()
        worker.synthesis_pipeline.process_job.return_value = {"jobId": "p2"}

        ch, method, properties, body = _make_amqp_args(
            {"jobId": "p2", "priority": 7}, amqp_priority=None
        )
        worker._handle_message(ch, method, properties, body)

        call_kwargs = worker.rabbitmq_manager.publish_result.call_args
        assert call_kwargs.kwargs.get("priority") == 7

    def test_priority_clamped_above_max(self):
        from services.rabbitmq_config import MQ_PRIORITY_MAX

        worker = _make_worker()
        worker.synthesis_pipeline.process_job.return_value = {"jobId": "p3"}

        ch, method, properties, body = _make_amqp_args(
            {"jobId": "p3"}, amqp_priority=MQ_PRIORITY_MAX + 100
        )
        worker._handle_message(ch, method, properties, body)

        call_kwargs = worker.rabbitmq_manager.publish_result.call_args
        assert call_kwargs.kwargs.get("priority") == MQ_PRIORITY_MAX

    def test_priority_clamped_below_zero(self):
        worker = _make_worker()
        worker.synthesis_pipeline.process_job.return_value = {"jobId": "p4"}

        ch, method, properties, body = _make_amqp_args(
            {"jobId": "p4"}, amqp_priority=-5
        )
        worker._handle_message(ch, method, properties, body)

        call_kwargs = worker.rabbitmq_manager.publish_result.call_args
        assert call_kwargs.kwargs.get("priority") == 0


# ---------------------------------------------------------------------------
# Tests: error handling
# ---------------------------------------------------------------------------


class TestHandleMessageErrors:
    def test_invalid_json_rejects_without_requeue(self):
        worker = _make_worker()

        ch = MagicMock()
        method = SimpleNamespace(delivery_tag=99)
        properties = SimpleNamespace(priority=None)
        body = b"not-valid-json{"

        worker._handle_message(ch, method, properties, body)

        worker.rabbitmq_manager.reject_message.assert_called_once_with(
            99, requeue=False
        )
        worker.synthesis_pipeline.process_job.assert_not_called()

    def test_pipeline_exception_rejects_without_requeue(self):
        worker = _make_worker()
        worker.synthesis_pipeline.process_job.side_effect = RuntimeError("GPU OOM")

        ch, method, properties, body = _make_amqp_args({"jobId": "fail1", "text": "x"})
        worker._handle_message(ch, method, properties, body)

        worker.rabbitmq_manager.reject_message.assert_called_once_with(
            42, requeue=False
        )
        worker.rabbitmq_manager.acknowledge_message.assert_not_called()

    def test_pipeline_exception_does_not_track_job(self):
        worker = _make_worker()
        worker.synthesis_pipeline.process_job.side_effect = ValueError("bad data")

        ch, method, properties, body = _make_amqp_args({"jobId": "fail2", "text": "x"})
        worker._handle_message(ch, method, properties, body)

        assert "fail2" not in worker._processed_jobs

    def test_publish_failure_does_not_acknowledge(self):
        """If publish_result raises, the message must not be ack'd."""
        worker = _make_worker()
        worker.synthesis_pipeline.process_job.return_value = {"jobId": "pub_fail"}
        worker.rabbitmq_manager.publish_result.side_effect = RuntimeError("MQ down")

        ch, method, properties, body = _make_amqp_args(
            {"jobId": "pub_fail", "text": "x"}
        )
        worker._handle_message(ch, method, properties, body)

        worker.rabbitmq_manager.acknowledge_message.assert_not_called()
        worker.rabbitmq_manager.reject_message.assert_called_once_with(
            42, requeue=False
        )
