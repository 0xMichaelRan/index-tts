"""
Unit tests for services.worker_config.WorkerConfig.

Tests cover:
- Default values when no env vars are set
- Correct parsing of each env var type (bool, int, float, str)
- validate() raising ValueError for invalid configurations
- Direct construction (no os.environ patching needed)
"""

from __future__ import annotations

import logging

import pytest

from services.worker_config import WorkerConfig, _env_bool, _env_float, _env_int


# ---------------------------------------------------------------------------
# Helper-function unit tests
# ---------------------------------------------------------------------------


class TestEnvHelpers:
    """Test the private env-parsing helpers."""

    def test_env_bool_true_values(self, monkeypatch):
        for value in ("true", "True", "TRUE", "1", "yes", "YES"):
            monkeypatch.setenv("_TEST_BOOL", value)
            assert _env_bool("_TEST_BOOL", False) is True

    def test_env_bool_false_values(self, monkeypatch):
        for value in ("false", "False", "FALSE", "0", "no", "NO"):
            monkeypatch.setenv("_TEST_BOOL", value)
            assert _env_bool("_TEST_BOOL", True) is False

    def test_env_bool_missing_returns_default(self, monkeypatch):
        monkeypatch.delenv("_TEST_BOOL", raising=False)
        assert _env_bool("_TEST_BOOL", True) is True
        assert _env_bool("_TEST_BOOL", False) is False

    def test_env_int_parses_correctly(self, monkeypatch):
        monkeypatch.setenv("_TEST_INT", "42")
        assert _env_int("_TEST_INT", 0) == 42

    def test_env_int_missing_returns_default(self, monkeypatch):
        monkeypatch.delenv("_TEST_INT", raising=False)
        assert _env_int("_TEST_INT", 99) == 99

    def test_env_float_parses_correctly(self, monkeypatch):
        monkeypatch.setenv("_TEST_FLOAT", "-16.5")
        assert _env_float("_TEST_FLOAT", 0.0) == pytest.approx(-16.5)

    def test_env_float_missing_returns_default(self, monkeypatch):
        monkeypatch.delenv("_TEST_FLOAT", raising=False)
        assert _env_float("_TEST_FLOAT", -16.0) == pytest.approx(-16.0)


# ---------------------------------------------------------------------------
# Direct construction
# ---------------------------------------------------------------------------


class TestDirectConstruction:
    """WorkerConfig can be built without from_env() — useful in tests."""

    def test_minimal_construction(self):
        config = WorkerConfig(rabbitmq_url="amqp://guest:guest@localhost:5672/")
        assert config.rabbitmq_url == "amqp://guest:guest@localhost:5672/"

    def test_defaults_are_sensible(self):
        config = WorkerConfig(rabbitmq_url="amqp://guest:guest@localhost:5672/")
        assert config.log_level == logging.INFO
        assert config.log_file_enabled is False
        assert config.log_file_path == "logs/worker.log"
        assert config.use_fast_inference is True
        assert config.cache_enabled is True
        assert config.cache_max_entries == 10_000
        assert config.cache_eviction_threshold == 9_000
        assert config.cache_dir == "outputs/tts_cache"
        assert config.normalization_enabled is True
        assert config.normalization_target_lufs == pytest.approx(-16.0)

    def test_log_level_name_property(self):
        config = WorkerConfig(
            rabbitmq_url="amqp://guest:guest@localhost/",
            log_level=logging.DEBUG,
        )
        assert config.log_level_name == "DEBUG"

    def test_override_individual_fields(self):
        config = WorkerConfig(
            rabbitmq_url="amqp://user:pass@rabbit:5672/vhost",
            cache_enabled=False,
            normalization_target_lufs=-23.0,
            use_fast_inference=False,
        )
        assert config.cache_enabled is False
        assert config.normalization_target_lufs == pytest.approx(-23.0)
        assert config.use_fast_inference is False


# ---------------------------------------------------------------------------
# from_env()
# ---------------------------------------------------------------------------


class TestFromEnv:
    """Test WorkerConfig.from_env() reads environment variables correctly."""

    def test_from_env_reads_rabbitmq_url(self, monkeypatch):
        monkeypatch.setenv("RABBITMQ_URL", "amqp://u:p@rabbit/")
        config = WorkerConfig.from_env()
        assert config.rabbitmq_url == "amqp://u:p@rabbit/"

    def test_from_env_missing_rabbitmq_url_is_empty(self, monkeypatch):
        monkeypatch.delenv("RABBITMQ_URL", raising=False)
        config = WorkerConfig.from_env()
        assert config.rabbitmq_url == ""

    def test_from_env_log_level(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        config = WorkerConfig.from_env()
        assert config.log_level == logging.DEBUG

    def test_from_env_log_level_invalid_defaults_to_info(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "NONSENSE")
        config = WorkerConfig.from_env()
        assert config.log_level == logging.INFO

    def test_from_env_log_file(self, monkeypatch):
        monkeypatch.setenv("LOG_FILE_ENABLED", "true")
        monkeypatch.setenv("LOG_FILE_PATH", "/tmp/test.log")
        config = WorkerConfig.from_env()
        assert config.log_file_enabled is True
        assert config.log_file_path == "/tmp/test.log"

    def test_from_env_cache_settings(self, monkeypatch):
        monkeypatch.setenv("TTS_CACHE_ENABLED", "false")
        monkeypatch.setenv("TTS_CACHE_MAX_ENTRIES", "500")
        monkeypatch.setenv("TTS_CACHE_EVICTION_THRESHOLD", "400")
        monkeypatch.setenv("TTS_CACHE_LOCAL_DIR", "/data/cache")
        config = WorkerConfig.from_env()
        assert config.cache_enabled is False
        assert config.cache_max_entries == 500
        assert config.cache_eviction_threshold == 400
        assert config.cache_dir == "/data/cache"

    def test_from_env_normalization(self, monkeypatch):
        monkeypatch.setenv("TTS_NORMALIZATION_ENABLED", "false")
        monkeypatch.setenv("TTS_NORMALIZATION_TARGET_LUFS", "-23.0")
        config = WorkerConfig.from_env()
        assert config.normalization_enabled is False
        assert config.normalization_target_lufs == pytest.approx(-23.0)

    def test_from_env_fast_inference_disabled(self, monkeypatch):
        monkeypatch.setenv("TTS_USE_FAST_INFERENCE", "false")
        config = WorkerConfig.from_env()
        assert config.use_fast_inference is False

    def test_from_env_defaults_when_nothing_set(self, monkeypatch):
        """All optional vars absent → defaults match dataclass field defaults."""
        vars_to_clear = [
            "LOG_LEVEL",
            "LOG_FILE_ENABLED",
            "LOG_FILE_PATH",
            "TTS_USE_FAST_INFERENCE",
            "TTS_CACHE_ENABLED",
            "TTS_CACHE_MAX_ENTRIES",
            "TTS_CACHE_EVICTION_THRESHOLD",
            "TTS_CACHE_LOCAL_DIR",
            "TTS_NORMALIZATION_ENABLED",
            "TTS_NORMALIZATION_TARGET_LUFS",
        ]
        for var in vars_to_clear:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("RABBITMQ_URL", "amqp://guest:guest@localhost/")

        config = WorkerConfig.from_env()
        default = WorkerConfig(rabbitmq_url="amqp://guest:guest@localhost/")

        assert config.log_level == default.log_level
        assert config.log_file_enabled == default.log_file_enabled
        assert config.use_fast_inference == default.use_fast_inference
        assert config.cache_enabled == default.cache_enabled
        assert config.cache_max_entries == default.cache_max_entries
        assert config.cache_eviction_threshold == default.cache_eviction_threshold
        assert config.cache_dir == default.cache_dir
        assert config.normalization_enabled == default.normalization_enabled
        assert config.normalization_target_lufs == pytest.approx(
            default.normalization_target_lufs
        )


# ---------------------------------------------------------------------------
# validate()
# ---------------------------------------------------------------------------


class TestValidate:
    """Test WorkerConfig.validate() catches invalid configs."""

    def test_valid_config_does_not_raise(self):
        config = WorkerConfig(rabbitmq_url="amqp://guest:guest@localhost/")
        config.validate()  # should not raise

    def test_missing_rabbitmq_url_raises(self):
        config = WorkerConfig(rabbitmq_url="")
        with pytest.raises(ValueError, match="RABBITMQ_URL is required"):
            config.validate()

    def test_lufs_above_zero_raises(self):
        config = WorkerConfig(
            rabbitmq_url="amqp://u:p@h/", normalization_target_lufs=1.0
        )
        with pytest.raises(ValueError, match="normalization_target_lufs"):
            config.validate()

    def test_lufs_below_minus_60_raises(self):
        config = WorkerConfig(
            rabbitmq_url="amqp://u:p@h/", normalization_target_lufs=-61.0
        )
        with pytest.raises(ValueError, match="normalization_target_lufs"):
            config.validate()

    def test_lufs_boundary_values_pass(self):
        for lufs in (0.0, -60.0, -16.0, -23.0):
            config = WorkerConfig(
                rabbitmq_url="amqp://u:p@h/", normalization_target_lufs=lufs
            )
            config.validate()  # must not raise

    def test_eviction_threshold_gte_max_entries_raises(self):
        config = WorkerConfig(
            rabbitmq_url="amqp://u:p@h/",
            cache_max_entries=1000,
            cache_eviction_threshold=1000,  # equal → invalid
        )
        with pytest.raises(ValueError, match="cache_eviction_threshold"):
            config.validate()

    def test_eviction_threshold_less_than_max_entries_passes(self):
        config = WorkerConfig(
            rabbitmq_url="amqp://u:p@h/",
            cache_max_entries=1000,
            cache_eviction_threshold=900,
        )
        config.validate()  # must not raise
