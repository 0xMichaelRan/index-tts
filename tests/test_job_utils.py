"""
Unit tests for services.common.job_utils.extract_job_id.

Covers:
- camelCase key (jobId)
- snake_case key (job_id)
- camelCase takes precedence when both keys present
- Integer values are stringified
- Empty dict returns None
- None values are treated as absent
"""

from services.common.job_utils import extract_job_id


class TestExtractJobId:
    def test_camel_case_key(self):
        assert extract_job_id({"jobId": "abc123"}) == "abc123"

    def test_snake_case_key(self):
        assert extract_job_id({"job_id": "abc123"}) == "abc123"

    def test_camel_case_takes_precedence(self):
        """jobId should win when both keys are present."""
        assert extract_job_id({"jobId": "camel", "job_id": "snake"}) == "camel"

    def test_integer_value_is_stringified(self):
        assert extract_job_id({"jobId": 42}) == "42"

    def test_snake_integer_value_is_stringified(self):
        assert extract_job_id({"job_id": 99}) == "99"

    def test_empty_dict_returns_none(self):
        assert extract_job_id({}) is None

    def test_none_camel_falls_back_to_snake(self):
        """A None jobId value should fall back to job_id."""
        assert extract_job_id({"jobId": None, "job_id": "snake"}) == "snake"

    def test_both_none_returns_none(self):
        assert extract_job_id({"jobId": None, "job_id": None}) is None

    def test_unrelated_keys_returns_none(self):
        assert extract_job_id({"text": "hello", "priority": 1}) is None

    def test_zero_value_is_stringified(self):
        """0 is falsy but not None — must still be returned as '0'."""
        assert extract_job_id({"jobId": 0}) == "0"
