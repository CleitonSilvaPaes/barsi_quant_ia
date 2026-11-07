"""Circuit Breaker para isolamento de falhas em chamadas de I/O de rede."""
import time
from enum import Enum
from typing import Callable, Any
from barsi.infra.logging import logger

class CircuitState(str, Enum):
    CLOSED = "CLOSED"      # Operação normal
    OPEN = "OPEN"          # Falhas consecutivas, chamadas bloqueadas temporariamente
    HALF_OPEN = "HALF_OPEN"# Tentativa controlada de recuperação

class CircuitBreakerOpenException(Exception):
    pass

class CircuitBreaker:
    def __init__(self, name: str, failure_threshold: int = 3, recovery_time_sec: float = 60.0):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_time_sec = recovery_time_sec
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time = 0.0

    def can_execute(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        now = time.time()
        if self.state == CircuitState.OPEN:
            if now - self.last_failure_time > self.recovery_time_sec:
                self.state = CircuitState.HALF_OPEN
                logger.info("circuit_breaker_half_open", name=self.name)
                return True
            return False
        return True  # HALF_OPEN

    def record_success(self):
        if self.state != CircuitState.CLOSED:
            logger.info("circuit_breaker_recovered", name=self.name)
        self.state = CircuitState.CLOSED
        self.failure_count = 0

    def record_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.warn("circuit_breaker_opened", name=self.name, failures=self.failure_count)

