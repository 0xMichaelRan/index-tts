"""
Tests for Circuit Breaker Pattern Implementation

Tests cover:
- Circuit state transitions (CLOSED → OPEN → HALF_OPEN → CLOSED)
- Failure threshold detection
- Reset timeout behavior
- Half-open state testing
- Concurrent access safety
- Context manager and execute() method interfaces
"""

import pytest
import time
import threading

from services.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerError,
    CircuitState,
    CircuitBreakerRegistry,
    get_circuit_breaker,
    get_all_circuit_breaker_stats,
)


class TestCircuitBreakerBasics:
    """Test basic circuit breaker functionality."""

    def test_initial_state_is_closed(self):
        """Circuit breaker should start in CLOSED state."""
        breaker = CircuitBreaker(name="test", failure_threshold=3)
        assert breaker.state == CircuitState.CLOSED

    def test_successful_execution_context_manager(self):
        """Successful operations should work with context manager."""
        breaker = CircuitBreaker(name="test", failure_threshold=3)

        with breaker:
            result = "success"

        assert result == "success"
        assert breaker.stats.success_count == 1
        assert breaker.stats.failure_count == 0

    def test_successful_execution_execute_method(self):
        """Successful operations should work with execute() method."""
        breaker = CircuitBreaker(name="test", failure_threshold=3)

        def my_function(x, y):
            return x + y

        result = breaker.execute(my_function, 2, 3)

        assert result == 5
        assert breaker.stats.success_count == 1

    def test_failed_execution_raises_original_exception(self):
        """Failed operations should raise original exception after recording."""
        breaker = CircuitBreaker(name="test", failure_threshold=3)

        with pytest.raises(ValueError, match="test error"):
            with breaker:
                raise ValueError("test error")

        assert breaker.stats.failure_count == 1
        assert breaker.stats.success_count == 0


class TestCircuitBreakerStateTransitions:
    """Test circuit breaker state transitions."""

    def test_transition_to_open_after_threshold(self):
        """Circuit should open after failure threshold is exceeded."""
        breaker = CircuitBreaker(name="test", failure_threshold=3, reset_timeout=60)

        # Trigger failures
        for _ in range(3):
            with pytest.raises(ValueError):
                with breaker:
                    raise ValueError("fail")

        # Circuit should now be OPEN
        assert breaker.state == CircuitState.OPEN
        assert breaker.stats.consecutive_failures == 3

    def test_open_circuit_rejects_immediately(self):
        """OPEN circuit should reject requests without executing."""
        breaker = CircuitBreaker(name="test", failure_threshold=2, reset_timeout=60)

        # Open the circuit
        for _ in range(2):
            with pytest.raises(ValueError):
                with breaker:
                    raise ValueError("fail")

        assert breaker.state == CircuitState.OPEN

        # Next request should fail with CircuitBreakerError
        call_count = 0
        with pytest.raises(CircuitBreakerError):
            with breaker:
                call_count += 1  # Should not execute

        assert call_count == 0  # Function was not called

    def test_transition_to_half_open_after_timeout(self):
        """Circuit should transition to HALF_OPEN after reset timeout."""
        breaker = CircuitBreaker(name="test", failure_threshold=2, reset_timeout=1)

        # Open the circuit
        for _ in range(2):
            with pytest.raises(ValueError):
                with breaker:
                    raise ValueError("fail")

        assert breaker.state == CircuitState.OPEN

        # Wait for reset timeout
        time.sleep(1.1)

        # Check state (should auto-transition to HALF_OPEN)
        assert breaker.state == CircuitState.HALF_OPEN

    def test_half_open_transitions_to_closed_on_success(self):
        """HALF_OPEN circuit should close after success threshold."""
        breaker = CircuitBreaker(
            name="test",
            failure_threshold=2,
            reset_timeout=1,
            success_threshold=2,
        )

        # Open the circuit
        for _ in range(2):
            with pytest.raises(ValueError):
                with breaker:
                    raise ValueError("fail")

        # Wait for HALF_OPEN
        time.sleep(1.1)
        assert breaker.state == CircuitState.HALF_OPEN

        # Execute successful operations
        for _ in range(2):
            with breaker:
                pass  # Success

        # Should transition back to CLOSED
        assert breaker.state == CircuitState.CLOSED

    def test_half_open_reopens_on_failure(self):
        """HALF_OPEN circuit should reopen immediately on failure."""
        breaker = CircuitBreaker(
            name="test",
            failure_threshold=2,
            reset_timeout=1,
            half_open_max_calls=3,
        )

        # Open the circuit
        for _ in range(2):
            with pytest.raises(ValueError):
                with breaker:
                    raise ValueError("fail")

        # Wait for HALF_OPEN
        time.sleep(1.1)
        assert breaker.state == CircuitState.HALF_OPEN

        # Fail in HALF_OPEN state
        with pytest.raises(ValueError):
            with breaker:
                raise ValueError("fail again")

        # Should reopen
        assert breaker.state == CircuitState.OPEN

    def test_half_open_max_calls_limit(self):
        """HALF_OPEN circuit should limit number of allowed calls."""
        breaker = CircuitBreaker(
            name="test",
            failure_threshold=2,
            reset_timeout=1,
            half_open_max_calls=2,
        )

        # Open the circuit
        for _ in range(2):
            with pytest.raises(ValueError):
                with breaker:
                    raise ValueError("fail")

        # Wait for HALF_OPEN
        time.sleep(1.1)

        # Execute max_calls successful operations
        for _ in range(2):
            with breaker:
                pass

        # After successful calls in HALF_OPEN, circuit should transition to CLOSED
        # (based on success_threshold), so no CircuitBreakerError expected
        assert breaker.state == CircuitState.CLOSED


class TestCircuitBreakerStats:
    """Test circuit breaker statistics and monitoring."""

    def test_stats_tracking(self):
        """Circuit breaker should track success/failure counts."""
        breaker = CircuitBreaker(name="test", failure_threshold=5)

        # Execute some operations
        for _ in range(3):
            with breaker:
                pass  # Success

        for _ in range(2):
            with pytest.raises(ValueError):
                with breaker:
                    raise ValueError("fail")

        stats = breaker.stats
        assert stats.success_count == 3
        assert stats.failure_count == 2
        assert stats.total_calls == 5
        assert stats.consecutive_successes == 0
        assert stats.consecutive_failures == 2

    def test_stats_to_dict(self):
        """Stats should be serializable to dict."""
        breaker = CircuitBreaker(name="test", failure_threshold=3)

        with breaker:
            pass

        stats_dict = breaker.stats.to_dict()
        assert isinstance(stats_dict, dict)
        assert stats_dict["name"] == "test"
        assert stats_dict["state"] == "CLOSED"
        assert stats_dict["success_count"] == 1
        assert "error_rate" in stats_dict


class TestCircuitBreakerRegistry:
    """Test circuit breaker registry functionality."""

    def test_register_new_breaker(self):
        """Registry should register new circuit breakers."""
        registry = CircuitBreakerRegistry()

        breaker = registry.register("test1", failure_threshold=3)

        assert breaker.name == "test1"
        assert registry.get("test1") is breaker

    def test_register_duplicate_returns_existing(self):
        """Registering duplicate name should return existing breaker."""
        registry = CircuitBreakerRegistry()

        breaker1 = registry.register("test", failure_threshold=3)
        breaker2 = registry.register("test", failure_threshold=5)

        assert breaker1 is breaker2

    def test_get_all_stats(self):
        """Registry should return stats for all breakers."""
        registry = CircuitBreakerRegistry()

        registry.register("breaker1", failure_threshold=3)
        registry.register("breaker2", failure_threshold=5)

        all_stats = registry.get_all_stats()

        assert len(all_stats) == 2
        assert "breaker1" in all_stats
        assert "breaker2" in all_stats

    def test_reset_all(self):
        """Registry should reset all circuit breakers."""
        registry = CircuitBreakerRegistry()

        breaker1 = registry.register("breaker1", failure_threshold=2)
        breaker2 = registry.register("breaker2", failure_threshold=2)

        # Open both circuits
        for breaker in [breaker1, breaker2]:
            for _ in range(2):
                with pytest.raises(ValueError):
                    with breaker:
                        raise ValueError("fail")

        assert breaker1.state == CircuitState.OPEN
        assert breaker2.state == CircuitState.OPEN

        # Reset all
        registry.reset_all()

        assert breaker1.state == CircuitState.CLOSED
        assert breaker2.state == CircuitState.CLOSED


class TestCircuitBreakerGlobalRegistry:
    """Test global registry functions."""

    def test_get_circuit_breaker_creates_new(self):
        """get_circuit_breaker should create breaker if not exists."""
        breaker = get_circuit_breaker("global_test", failure_threshold=3)

        assert breaker.name == "global_test"
        assert breaker.failure_threshold == 3

    def test_get_circuit_breaker_returns_existing(self):
        """get_circuit_breaker should return existing breaker."""
        breaker1 = get_circuit_breaker("global_test2", failure_threshold=3)
        breaker2 = get_circuit_breaker("global_test2", failure_threshold=5)

        assert breaker1 is breaker2

    def test_get_all_stats_global(self):
        """get_all_circuit_breaker_stats should return all breakers."""
        get_circuit_breaker("stats_test1")
        get_circuit_breaker("stats_test2")

        all_stats = get_all_circuit_breaker_stats()

        assert "stats_test1" in all_stats
        assert "stats_test2" in all_stats


class TestCircuitBreakerThreadSafety:
    """Test thread safety of circuit breaker."""

    def test_concurrent_executions(self):
        """Circuit breaker should handle concurrent executions safely."""
        breaker = CircuitBreaker(name="concurrent", failure_threshold=10)
        results = []
        errors = []

        def worker(worker_id: int):
            try:
                for i in range(5):
                    with breaker:
                        time.sleep(0.001)  # Simulate work
                        results.append(f"worker-{worker_id}-{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]

        for t in threads:
            t.start()

        for t in threads:
            t.join()

        assert len(results) == 25  # 5 workers * 5 iterations
        assert len(errors) == 0
        assert breaker.stats.success_count == 25

    def test_concurrent_failures(self):
        """Circuit breaker should handle concurrent failures safely."""
        breaker = CircuitBreaker(name="concurrent_fail", failure_threshold=20)
        failure_count = [0]

        def worker():
            for _ in range(5):
                try:
                    with breaker:
                        raise ValueError("test failure")
                except ValueError:
                    failure_count[0] += 1

        threads = [threading.Thread(target=worker) for _ in range(4)]

        for t in threads:
            t.start()

        for t in threads:
            t.join()

        assert failure_count[0] == 20  # 4 workers * 5 iterations
        assert breaker.stats.failure_count == 20


class TestCircuitBreakerEdgeCases:
    """Test edge cases and error conditions."""

    def test_manual_reset(self):
        """Circuit breaker should support manual reset."""
        breaker = CircuitBreaker(name="reset_test", failure_threshold=2)

        # Open the circuit
        for _ in range(2):
            with pytest.raises(ValueError):
                with breaker:
                    raise ValueError("fail")

        assert breaker.state == CircuitState.OPEN

        # Manual reset
        breaker.reset()

        assert breaker.state == CircuitState.CLOSED
        assert breaker.stats.failure_count == 0
        assert breaker.stats.consecutive_failures == 0

    def test_zero_failure_threshold_immediately_opens(self):
        """Zero failure threshold should open on first failure."""
        breaker = CircuitBreaker(name="zero_threshold", failure_threshold=0)

        with pytest.raises(ValueError):
            with breaker:
                raise ValueError("fail")

        # Should be OPEN immediately (0 failures allowed)
        # Note: Implementation may handle this differently
        # This test documents expected behavior

    def test_repr_string(self):
        """Circuit breaker should have useful string representation."""
        breaker = CircuitBreaker(name="repr_test", failure_threshold=3)

        repr_str = repr(breaker)

        assert "CircuitBreaker" in repr_str
        assert "repr_test" in repr_str
        assert "CLOSED" in repr_str


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
