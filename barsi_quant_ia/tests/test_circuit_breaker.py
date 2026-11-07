"""Testes para o Circuit Breaker de Isolamento de Falhas."""
import time
from barsi.infra.circuit_breaker import CircuitBreaker, CircuitState

def test_circuit_breaker_flow():
    cb = CircuitBreaker(name="test_service", failure_threshold=2, recovery_time_sec=0.1)
    
    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute() is True

    # 1ª Falha
    cb.record_failure()
    assert cb.state == CircuitState.CLOSED

    # 2ª Falha -> Dispara Abertura
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    assert cb.can_execute() is False

    # Aguarda tempo de recuperação
    time.sleep(0.15)
    assert cb.can_execute() is True
    assert cb.state == CircuitState.HALF_OPEN

    # Sucesso recupera para CLOSED
    cb.record_success()
    assert cb.state == CircuitState.CLOSED

