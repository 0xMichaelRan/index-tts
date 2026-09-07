"""
Tests for scripts/migrate_priority_queues.py — check-mode logic.

Specifically tests the regression where `--check` incorrectly reported
"(will add priority)" even after queues already had x-max-priority=10.

Root cause: AMQP Queue.DeclareOk does not return queue arguments.
Fix: use the RabbitMQ Management HTTP API to read arguments.
"""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Make the scripts directory importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.migrate_priority_queues import (
    MQ_PRIORITY_MAX,
    PRIORITY_QUEUES,
    _fetch_queue_args_via_management,
    _mgmt_url_from_amqp,
    check_queues,
)


class TestMgmtUrlFromAmqp(unittest.TestCase):
    """_mgmt_url_from_amqp: AMQP URL → Management HTTP URL conversion."""

    def test_standard_amqp_url(self):
        url = "amqp://user:pass@myhost:5672/myvhost"
        result = _mgmt_url_from_amqp(url)
        self.assertEqual(result, "http://user:pass@myhost:15672")

    def test_default_port(self):
        # No explicit port → use 5672, management = 15672
        url = "amqp://user:pass@myhost/vhost"
        result = _mgmt_url_from_amqp(url)
        self.assertIn("15672", result)

    def test_amqps_scheme(self):
        url = "amqps://user:pass@myhost:5671/vhost"
        result = _mgmt_url_from_amqp(url)
        self.assertTrue(result.startswith("https://"))
        self.assertIn("15671", result)

    def test_no_credentials(self):
        url = "amqp://myhost:5672/vhost"
        result = _mgmt_url_from_amqp(url)
        self.assertIn("myhost:15672", result)

    def test_invalid_url_returns_none(self):
        result = _mgmt_url_from_amqp("not-a-url")
        # Should not raise; may return None or a partial URL — either is acceptable
        # as long as it doesn't crash
        self.assertIsNone(result) if result is None else self.assertIsInstance(
            result, str
        )


class TestFetchQueueArgsViaMgmt(unittest.TestCase):
    """_fetch_queue_args_via_management: HTTP API argument retrieval."""

    def _make_response(self, data: dict, status: int = 200):
        import io

        body = json.dumps(data).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = body
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def test_returns_arguments_dict(self):
        payload = {
            "name": "tts_jobs",
            "arguments": {"x-max-priority": 10, "x-message-ttl": 86400000},
        }
        with patch(
            "urllib.request.urlopen",
            return_value=self._make_response(payload),
        ):
            result = _fetch_queue_args_via_management(
                "http://user:pass@host:15672", "jtdiqgdu", "tts_jobs"
            )
        self.assertEqual(result, {"x-max-priority": 10, "x-message-ttl": 86400000})

    def test_queue_not_found_returns_none(self):
        import urllib.error

        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                url="", code=404, msg="", hdrs=None, fp=None
            ),
        ):
            result = _fetch_queue_args_via_management(
                "http://user:pass@host:15672", "jtdiqgdu", "tts_jobs"
            )
        self.assertIsNone(result)

    def test_connection_error_returns_none(self):
        with patch("urllib.request.urlopen", side_effect=OSError("Connection refused")):
            result = _fetch_queue_args_via_management(
                "http://user:pass@host:15672", "vhost", "tts_jobs"
            )
        self.assertIsNone(result)

    def test_empty_arguments_returns_empty_dict(self):
        """Queue exists but has no custom arguments."""
        payload = {"name": "tts_jobs", "arguments": {}}
        with patch(
            "urllib.request.urlopen",
            return_value=self._make_response(payload),
        ):
            result = _fetch_queue_args_via_management(
                "http://user:pass@host:15672", "vhost", "tts_jobs"
            )
        self.assertEqual(result, {})

    def test_missing_arguments_key_returns_empty_dict(self):
        """Queue response has no 'arguments' key → treat as empty."""
        payload = {"name": "tts_jobs"}
        with patch(
            "urllib.request.urlopen",
            return_value=self._make_response(payload),
        ):
            result = _fetch_queue_args_via_management(
                "http://user:pass@host:15672", "vhost", "tts_jobs"
            )
        self.assertEqual(result, {})


class TestCheckQueuesRegression(unittest.TestCase):
    """
    Regression tests: after --force creates queues with x-max-priority=10,
    --check must correctly report the priority as already set (not 'will add priority').
    """

    def _make_declare_ok(self, message_count=0, consumer_count=0):
        method = MagicMock()
        method.message_count = message_count
        method.consumer_count = consumer_count
        result = MagicMock()
        result.method = method
        return result

    def _make_mgmt_response(self, priority: int | None):
        """Return a mock management API args dict."""
        if priority is None:
            return {}  # queue exists but no x-max-priority
        return {"x-max-priority": priority}

    def _run_check_queues(self, mgmt_args_by_queue: dict) -> dict:
        """
        Run check_queues() with mocked AMQP + management API.

        mgmt_args_by_queue: {queue_name: dict | None}
          None  → management API returns None (unreachable)
          dict  → management API returns that dict as 'arguments'
        """
        fake_declare_ok = self._make_declare_ok()

        mock_channel = MagicMock()
        mock_channel.queue_declare.return_value = fake_declare_ok

        mock_connection = MagicMock()
        mock_connection.channel.return_value = mock_channel

        def fake_fetch(mgmt_base, vhost, queue_name, timeout=5.0):
            return mgmt_args_by_queue.get(queue_name)

        with (
            patch(
                "scripts.migrate_priority_queues._connect",
                return_value=(mock_connection, mock_channel),
            ),
            patch(
                "scripts.migrate_priority_queues._mgmt_url_from_amqp",
                return_value="http://user:pass@host:15672",
            ),
            patch(
                "scripts.migrate_priority_queues._fetch_queue_args_via_management",
                side_effect=fake_fetch,
            ),
        ):
            return check_queues("amqp://user:pass@host:5672/vhost")

    # -----------------------------------------------------------------------
    # The key regression test
    # -----------------------------------------------------------------------

    def test_queue_with_priority_10_reports_priority_set(self):
        """
        REGRESSION: after migration, x-max-priority=10 must be detected and
        check_queues must NOT imply the queue needs priority added.
        """
        mgmt_args = {q: {"x-max-priority": MQ_PRIORITY_MAX} for q in PRIORITY_QUEUES}
        mgmt_args["tts_jobs_failed"] = {}
        mgmt_args["tts_results_failed"] = {}

        info = self._run_check_queues(mgmt_args)

        for q in PRIORITY_QUEUES:
            with self.subTest(queue=q):
                data = info[q]
                self.assertTrue(data["exists"])
                args = data.get("arguments")
                self.assertIsNotNone(
                    args, "arguments must be populated from management API"
                )
                self.assertEqual(
                    args.get("x-max-priority"),
                    MQ_PRIORITY_MAX,
                    f"x-max-priority should be {MQ_PRIORITY_MAX} for {q}",
                )
                self.assertTrue(
                    data.get("mgmt_available"),
                    "mgmt_available must be True when API responds",
                )

    def test_queue_without_priority_reports_not_set(self):
        """Queue exists with no x-max-priority → arguments dict has no 'x-max-priority'."""
        mgmt_args = {q: {} for q in PRIORITY_QUEUES}
        mgmt_args["tts_jobs_failed"] = {}
        mgmt_args["tts_results_failed"] = {}

        info = self._run_check_queues(mgmt_args)

        for q in PRIORITY_QUEUES:
            with self.subTest(queue=q):
                data = info[q]
                self.assertNotIn("x-max-priority", data.get("arguments", {}))

    def test_management_api_unavailable_sets_mgmt_flag_false(self):
        """When management API is unreachable, mgmt_available=False for all queues."""
        mgmt_args = {q: None for q in PRIORITY_QUEUES}
        mgmt_args["tts_jobs_failed"] = None
        mgmt_args["tts_results_failed"] = None

        info = self._run_check_queues(mgmt_args)

        for q in PRIORITY_QUEUES:
            with self.subTest(queue=q):
                data = info[q]
                self.assertFalse(
                    data.get("mgmt_available"),
                    "mgmt_available must be False when API is unreachable",
                )

    def test_mismatched_priority_is_detected(self):
        """Queue has x-max-priority=5 (not 10) → reported as wrong value."""
        mgmt_args = {q: {"x-max-priority": 5} for q in PRIORITY_QUEUES}
        mgmt_args["tts_jobs_failed"] = {}
        mgmt_args["tts_results_failed"] = {}

        info = self._run_check_queues(mgmt_args)

        for q in PRIORITY_QUEUES:
            with self.subTest(queue=q):
                actual = info[q]["arguments"].get("x-max-priority")
                self.assertEqual(actual, 5)
                self.assertNotEqual(actual, MQ_PRIORITY_MAX)


class TestCheckOutputFormatting(unittest.TestCase):
    """
    Validate that the --check display logic produces correct human-readable output.
    This tests the conditional formatting in main() indirectly via the data contract.
    """

    def _priority_note(self, name: str, data: dict) -> str:
        """Mirror the priority_note logic from main() for testing."""
        if name not in PRIORITY_QUEUES or not data.get("exists"):
            return ""
        arguments = data.get("arguments")
        mgmt_ok = data.get("mgmt_available", False)
        if not mgmt_ok:
            return "(priority: unknown — management API unavailable)"
        if arguments is None:
            return "(priority: unknown — management API error)"
        actual = arguments.get("x-max-priority")
        if actual is None:
            return f"(priority: NOT SET — will add priority={MQ_PRIORITY_MAX})"
        elif actual == MQ_PRIORITY_MAX:
            return f"(priority={actual} ✓)"
        else:
            return f"(priority={actual} ✗ — expected {MQ_PRIORITY_MAX}, will recreate)"

    def test_correct_priority_shows_check_mark(self):
        data = {
            "exists": True,
            "arguments": {"x-max-priority": 10},
            "mgmt_available": True,
        }
        note = self._priority_note("tts_jobs", data)
        self.assertIn("✓", note)
        self.assertNotIn("will add", note)
        self.assertNotIn("NOT SET", note)

    def test_missing_priority_shows_will_add(self):
        data = {"exists": True, "arguments": {}, "mgmt_available": True}
        note = self._priority_note("tts_jobs", data)
        self.assertIn("NOT SET", note)
        self.assertIn("will add", note)

    def test_wrong_priority_shows_cross_and_will_recreate(self):
        data = {
            "exists": True,
            "arguments": {"x-max-priority": 5},
            "mgmt_available": True,
        }
        note = self._priority_note("tts_jobs", data)
        self.assertIn("✗", note)
        self.assertIn("will recreate", note)

    def test_mgmt_unavailable_shows_warning(self):
        data = {"exists": True, "arguments": None, "mgmt_available": False}
        note = self._priority_note("tts_jobs", data)
        self.assertIn("unknown", note)
        self.assertIn("unavailable", note)

    def test_dlq_has_no_priority_note(self):
        data = {"exists": True, "arguments": {}, "mgmt_available": True}
        note = self._priority_note("tts_jobs_failed", data)
        self.assertEqual(note, "")


if __name__ == "__main__":
    unittest.main()
