"""
Circuit Breaker Pattern Implementation

Prevents cascading failures during S3/IndexTTS outages by implementing
a circuit breaker that opens after a threshold of failures and periodically
attempts to recover.

Circuit States:
    - CLOSED: Normal operation, requests flow through
    - OPEN: Failure threshold exceeded, requests fail immediately
    - HALF_OPEN: Testing recovery, limited requests allowed

Usage:
    from services.circuit_breaker import CircuitBreaker, CircuitBreakerError
    
    # Create circuit breakers for different services
    s3_breaker = CircuitBreaker(
        name="S3Download",
        failure_threshold=5,
        reset_timeout=60,
        half_open_max_calls=3
    )
    
    tts_breaker = CircuitBreaker(
        name="IndexTTS",
        failure_threshold=3,
        reset_timeout=30,
        half_open_max_calls=2
    )
    
    # Use circuit breaker
    try:
        with s3_breaker:
            result = download_from_s3()
    except CircuitBreakerError:
        logger.error("S3 circuit breaker is open - service unavailable")
    except Exception as e:
        # Actual S3 error is still raised after being recorded
        logger.error(f"S3 download failed: {e}")
"""

import time
import logging
import threading
from enum import Enum
from typing import Optional, Callable, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "CLOSED"  # Normal operation
    OPEN = "OPEN"  # Failure threshold exceeded
    HALF_OPEN = "HALF_OPEN"  # Testing recovery


@dataclass
class CircuitBreakerStats:
    """Statistics for circuit breaker monitoring."""
    name: str
    state: CircuitState
    failure_count: int
    success_count: int
    total_calls: int
    last_failure_time: Optional[datetime] = None
    last_state_change: Optional[datetime] = field(default_factory=datetime.now)
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    
    def to_dict(self) -> dict:
        """Convert stats to dictionary for logging/monitoring."""
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "total_calls": self.total_calls,
            "last_failure_time": self.last_failure_time.isoformat() if self.last_failure_time else None,
            "last_state_change": self.last_state_change.isoformat() if self.last_state_change else None,
            "consecutive_failures": self.consecutive_failures,
            "consecutive_successes": self.consecutive_successes,
            "error_rate": self.failure_count / self.total_calls if self.total_calls > 0 else 0,
        }


class CircuitBreakerError(Exception):
    """Raised when circuit breaker is open and request is rejected."""
    pass


class CircuitBreaker:
    """
    Circuit breaker implementation to prevent cascading failures.
    
    The circuit breaker monitors the failure rate of operations and opens
    when the failure threshold is exceeded. After a reset timeout, it enters
    HALF_OPEN state to test recovery.
    
    Attributes:
        name: Identifier for this circuit breaker
        failure_threshold: Number of consecutive failures before opening
        reset_timeout: Seconds to wait before attempting recovery
        half_open_max_calls: Maximum calls allowed in HALF_OPEN state
        success_threshold: Consecutive successes needed to close from HALF_OPEN
    """
    
    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        reset_timeout: int = 60,
        half_open_max_calls: int = 3,
        success_threshold: int = 2,
    ):
        """
        Initialize circuit breaker.
        
        Args:
            name: Circuit breaker identifier
            failure_threshold: Consecutive failures to trigger OPEN state
            reset_timeout: Seconds before transitioning to HALF_OPEN
            half_open_max_calls: Max calls in HALF_OPEN before reopening
            success_threshold: Successes needed to close from HALF_OPEN
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.half_open_max_calls = half_open_max_calls
        self.success_threshold = success_threshold
        
        # State management
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._total_calls = 0
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self._last_failure_time: Optional[float] = None
        self._last_state_change: float = time.time()
        self._half_open_calls = 0
        
        # Thread safety
        self._lock = threading.RLock()
        
        logger.info(
            f"Circuit breaker '{name}' initialized: "
            f"failure_threshold={failure_threshold}, "
            f"reset_timeout={reset_timeout}s, "
            f"half_open_max_calls={half_open_max_calls}, "
            f"success_threshold={success_threshold}"
        )
    
    @property
    def state(self) -> CircuitState:
        """Get current circuit state."""
        with self._lock:
            self._check_reset_timeout()
            return self._state
    
    @property
    def stats(self) -> CircuitBreakerStats:
        """Get current statistics."""
        with self._lock:
            return CircuitBreakerStats(
                name=self.name,
                state=self._state,
                failure_count=self._failure_count,
                success_count=self._success_count,
                total_calls=self._total_calls,
                last_failure_time=datetime.fromtimestamp(self._last_failure_time) if self._last_failure_time else None,
                last_state_change=datetime.fromtimestamp(self._last_state_change),
                consecutive_failures=self._consecutive_failures,
                consecutive_successes=self._consecutive_successes,
            )
    
    def _check_reset_timeout(self) -> None:
        """Check if circuit should transition to HALF_OPEN state."""
        if self._state == CircuitState.OPEN:
            elapsed = time.time() - self._last_state_change
            if elapsed >= self.reset_timeout:
                logger.info(
                    f"Circuit breaker '{self.name}' transitioning to HALF_OPEN "
                    f"after {elapsed:.1f}s"
                )
                self._transition_to_half_open()
    
    def _transition_to_half_open(self) -> None:
        """Transition circuit to HALF_OPEN state."""
        self._state = CircuitState.HALF_OPEN
        self._half_open_calls = 0
        self._consecutive_successes = 0
        self._last_state_change = time.time()
    
    def _transition_to_open(self) -> None:
        """Transition circuit to OPEN state."""
        if self._state != CircuitState.OPEN:
            logger.warning(
                f"Circuit breaker '{self.name}' OPENED after "
                f"{self._consecutive_failures} consecutive failures "
                f"(threshold: {self.failure_threshold})"
            )
            self._state = CircuitState.OPEN
            self._last_state_change = time.time()
            self._half_open_calls = 0
    
    def _transition_to_closed(self) -> None:
        """Transition circuit to CLOSED state."""
        if self._state != CircuitState.CLOSED:
            logger.info(
                f"Circuit breaker '{self.name}' CLOSED after "
                f"{self._consecutive_successes} consecutive successes"
            )
            self._state = CircuitState.CLOSED
            self._consecutive_failures = 0
            self._last_state_change = time.time()
    
    def _record_success(self) -> None:
        """Record a successful operation."""
        with self._lock:
            self._success_count += 1
            self._total_calls += 1
            self._consecutive_successes += 1
            self._consecutive_failures = 0
            
            if self._state == CircuitState.HALF_OPEN:
                logger.debug(
                    f"Circuit breaker '{self.name}' HALF_OPEN: "
                    f"{self._consecutive_successes}/{self.success_threshold} successes"
                )
                if self._consecutive_successes >= self.success_threshold:
                    self._transition_to_closed()
    
    def _record_failure(self) -> None:
        """Record a failed operation."""
        with self._lock:
            self._failure_count += 1
            self._total_calls += 1
            self._consecutive_failures += 1
            self._consecutive_successes = 0
            self._last_failure_time = time.time()
            
            if self._state == CircuitState.CLOSED:
                if self._consecutive_failures >= self.failure_threshold:
                    self._transition_to_open()
            
            elif self._state == CircuitState.HALF_OPEN:
                logger.warning(
                    f"Circuit breaker '{self.name}' failed in HALF_OPEN state, "
                    f"reopening circuit"
                )
                self._transition_to_open()
    
    def _can_execute(self) -> bool:
        """Check if operation can be executed based on circuit state."""
        with self._lock:
            self._check_reset_timeout()
            
            if self._state == CircuitState.CLOSED:
                return True
            
            elif self._state == CircuitState.OPEN:
                return False
            
            elif self._state == CircuitState.HALF_OPEN:
                if self._half_open_calls < self.half_open_max_calls:
                    self._half_open_calls += 1
                    return True
                logger.debug(
                    f"Circuit breaker '{self.name}' HALF_OPEN: "
                    f"max calls reached ({self.half_open_max_calls})"
                )
                return False
            
            return False
    
    def __enter__(self):
        """Enter context manager."""
        if not self._can_execute():
            raise CircuitBreakerError(
                f"Circuit breaker '{self.name}' is {self._state.value}"
            )
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context manager."""
        if exc_type is None:
            # No exception occurred
            self._record_success()
        else:
            # Exception occurred
            self._record_failure()
        
        # Don't suppress the exception - let it propagate
        return False
    
    def __call__(self):
        """
        Allow circuit breaker to be used as context manager via 'with breaker:'.
        
        This enables both:
            - with breaker:  (using __enter__/__exit__)
            - with breaker():  (using this __call__ method)
        """
        return self
    
    def execute(self, func: Callable[..., Any], *args, **kwargs) -> Any:
        """
        Execute a function with circuit breaker protection.
        
        Args:
            func: Function to execute
            *args: Positional arguments for func
            **kwargs: Keyword arguments for func
            
        Returns:
            Result of func execution
            
        Raises:
            CircuitBreakerError: If circuit is open
            Exception: Original exception from func
        """
        with self:
            return func(*args, **kwargs)
    
    def reset(self) -> None:
        """Manually reset circuit breaker to CLOSED state."""
        with self._lock:
            logger.info(f"Manually resetting circuit breaker '{self.name}'")
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._consecutive_failures = 0
            self._consecutive_successes = 0
            self._last_failure_time = None
            self._last_state_change = time.time()
            self._half_open_calls = 0
    
    def __repr__(self) -> str:
        """String representation of circuit breaker."""
        return (
            f"CircuitBreaker(name='{self.name}', "
            f"state={self._state.value}, "
            f"failures={self._failure_count}, "
            f"successes={self._success_count})"
        )


class CircuitBreakerRegistry:
    """
    Registry for managing multiple circuit breakers.
    
    Provides centralized management and monitoring of circuit breakers
    across different services.
    """
    
    def __init__(self):
        """Initialize circuit breaker registry."""
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = threading.RLock()
    
    def register(
        self,
        name: str,
        failure_threshold: int = 5,
        reset_timeout: int = 60,
        half_open_max_calls: int = 3,
        success_threshold: int = 2,
    ) -> CircuitBreaker:
        """
        Register a new circuit breaker.
        
        Args:
            name: Circuit breaker identifier
            failure_threshold: Consecutive failures to trigger OPEN
            reset_timeout: Seconds before HALF_OPEN transition
            half_open_max_calls: Max calls in HALF_OPEN state
            success_threshold: Successes to close from HALF_OPEN
            
        Returns:
            Registered circuit breaker instance
        """
        with self._lock:
            if name in self._breakers:
                logger.warning(f"Circuit breaker '{name}' already registered")
                return self._breakers[name]
            
            breaker = CircuitBreaker(
                name=name,
                failure_threshold=failure_threshold,
                reset_timeout=reset_timeout,
                half_open_max_calls=half_open_max_calls,
                success_threshold=success_threshold,
            )
            self._breakers[name] = breaker
            return breaker
    
    def get(self, name: str) -> Optional[CircuitBreaker]:
        """Get circuit breaker by name."""
        with self._lock:
            return self._breakers.get(name)
    
    def get_all_stats(self) -> dict[str, dict]:
        """Get statistics for all registered circuit breakers."""
        with self._lock:
            return {
                name: breaker.stats.to_dict()
                for name, breaker in self._breakers.items()
            }
    
    def reset_all(self) -> None:
        """Reset all circuit breakers to CLOSED state."""
        with self._lock:
            for breaker in self._breakers.values():
                breaker.reset()
            logger.info(f"Reset {len(self._breakers)} circuit breakers")
    
    def __repr__(self) -> str:
        """String representation of registry."""
        return f"CircuitBreakerRegistry(breakers={len(self._breakers)})"


# Global registry instance
_registry = CircuitBreakerRegistry()


def get_circuit_breaker(
    name: str,
    failure_threshold: int = 5,
    reset_timeout: int = 60,
    half_open_max_calls: int = 3,
    success_threshold: int = 2,
) -> CircuitBreaker:
    """
    Get or create a circuit breaker from global registry.
    
    Args:
        name: Circuit breaker identifier
        failure_threshold: Consecutive failures to trigger OPEN
        reset_timeout: Seconds before HALF_OPEN transition
        half_open_max_calls: Max calls in HALF_OPEN state
        success_threshold: Successes to close from HALF_OPEN
        
    Returns:
        Circuit breaker instance
    """
    breaker = _registry.get(name)
    if breaker is None:
        breaker = _registry.register(
            name=name,
            failure_threshold=failure_threshold,
            reset_timeout=reset_timeout,
            half_open_max_calls=half_open_max_calls,
            success_threshold=success_threshold,
        )
    return breaker


def get_all_circuit_breaker_stats() -> dict[str, dict]:
    """Get statistics for all circuit breakers in global registry."""
    return _registry.get_all_stats()


def reset_all_circuit_breakers() -> None:
    """Reset all circuit breakers in global registry."""
    _registry.reset_all()
