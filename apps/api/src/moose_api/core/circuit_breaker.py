"""Circuit breaker pattern for API resilience.

Tracks failure counts per API endpoint and opens the circuit after
threshold failures, preventing cascading failures. Automatically
resets after cooldown period.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Circuit is open, blocking requests
    HALF_OPEN = "half_open"  # Testing if service has recovered


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker."""
    failure_threshold: int = 5  # Failures before opening circuit
    recovery_timeout: int = 60  # Seconds before attempting recovery
    expected_exception: type | tuple[type, ...] = Exception


@dataclass
class CircuitBreakerState:
    """Current state of a circuit breaker."""
    failure_count: int = 0
    last_failure_time: float = 0.0
    state: CircuitState = CircuitState.CLOSED
    config: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)


class CircuitBreaker:
    """Circuit breaker for API resilience."""

    _instances: dict[str, CircuitBreakerState] = {}

    @classmethod
    def get_breaker(cls, name: str, config: CircuitBreakerConfig | None = None) -> CircuitBreakerState:
        """Get or create a circuit breaker for a named service."""
        if name not in cls._instances:
            cls._instances[name] = CircuitBreakerState(config=config or CircuitBreakerConfig())
        return cls._instances[name]

    @classmethod
    async def call(
        cls,
        service_name: str,
        func: Callable,
        *args,
        config: CircuitBreakerConfig | None = None,
        **kwargs,
    ):
        """Execute a function with circuit breaker protection.

        Args:
            service_name: Name of the service/API endpoint
            func: Async function to execute
            *args: Positional arguments for func
            config: Circuit breaker configuration
            **kwargs: Keyword arguments for func

        Returns:
            Result of func execution

        Raises:
            Exception: If circuit is open or func fails
        """
        breaker = cls.get_breaker(service_name, config)

        if breaker.state == CircuitState.OPEN:
            if time.time() - breaker.last_failure_time < breaker.config.recovery_timeout:
                logger.warning(
                    "Circuit breaker OPEN for %s. Blocking request. %.1fs remaining until cooldown.",
                    service_name,
                    breaker.config.recovery_timeout - (time.time() - breaker.last_failure_time),
                )
                raise Exception(f"Circuit breaker OPEN for {service_name}")
            else:
                logger.info("Circuit breaker HALF_OPEN for %s. Attempting recovery.", service_name)
                breaker.state = CircuitState.HALF_OPEN

        try:
            result = await func(*args, **kwargs)
            
            if breaker.state == CircuitState.HALF_OPEN:
                logger.info("Circuit breaker CLOSED for %s. Service recovered.", service_name)
                breaker.state = CircuitState.CLOSED
                breaker.failure_count = 0
            
            return result
            
        except Exception as e:
            if isinstance(e, breaker.config.expected_exception):
                breaker.failure_count += 1
                breaker.last_failure_time = time.time()
                
                logger.warning(
                    "Circuit breaker failure for %s: %s (count: %d/%d)",
                    service_name,
                    str(e)[:100],
                    breaker.failure_count,
                    breaker.config.failure_threshold,
                )
                
                if breaker.failure_count >= breaker.config.failure_threshold:
                    breaker.state = CircuitState.OPEN
                    logger.error("Circuit breaker OPENED for %s after %d failures", service_name, breaker.failure_count)
            else:
                logger.error("Unexpected exception for %s: %s", service_name, str(e)[:100])
            
            raise

    @classmethod
    def reset(cls, service_name: str):
        """Manually reset a circuit breaker to CLOSED state."""
        if service_name in cls._instances:
            cls._instances[service_name].state = CircuitState.CLOSED
            cls._instances[service_name].failure_count = 0
            logger.info("Circuit breaker manually reset for %s", service_name)

    @classmethod
    def get_status(cls, service_name: str) -> dict:
        """Get current status of a circuit breaker."""
        if service_name not in cls._instances:
            return {"state": "not_initialized"}
        
        breaker = cls._instances[service_name]
        return {
            "state": breaker.state.value,
            "failure_count": breaker.failure_count,
            "last_failure_time": breaker.last_failure_time,
            "cooldown_remaining": max(
                0,
                breaker.config.recovery_timeout - (time.time() - breaker.last_failure_time),
            ) if breaker.state == CircuitState.OPEN else 0,
        }
