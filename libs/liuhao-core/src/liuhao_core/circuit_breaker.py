"""Circuit breaker using pybreaker"""
from pybreaker import CircuitBreaker
import logging

logger = logging.getLogger(__name__)

# Circuit breaker configuration
db_circuit_breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=30, timeout=120)
api_circuit_breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=60, timeout=180)

def get_circuit_breaker(name: str = "default"):
    """Get configured circuit breaker by name."""
    return {
        "db": db_circuit_breaker,
        "api": api_circuit_breaker,
    }.get(name, api_circuit_breaker)
