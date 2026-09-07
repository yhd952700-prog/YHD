"""Retry utilities using tenacity"""
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import logging

logger = logging.getLogger(__name__)

def retry_with_backoff(
    stop_attempt_number: int = 3,
    wait_min: float = 1.0,
    wait_max: float = 10.0,
    exponential_base: float = 2.0,
    jitter: bool = True,
):
    """Decorator for retry with exponential backoff."""
    return retry(
        stop=stop_after_attempt(stop_attempt_number),
        wait=wait_exponential(
            multiplier=wait_min,
            min=wait_min,
            max=wait_max,
            exp=exponential_base,
        ),
        reraise=True,
    )

@retry_with_backoff()
def safe_execute(func, *args, **kwargs):
    """Execute function with automatic retry."""
    return func(*args, **kwargs)
