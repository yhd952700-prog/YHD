# liuhao-core

Common library for liuhao AI OS providing:

## Modules

- **retry**: Retry utilities with exponential backoff using tenacity
- **circuit_breaker**: Circuit breaker pattern using pybreaker
- **metrics**: Prometheus metrics integration
- **logging**: Structured logging with structlog
- **configuration**: pydantic-settings based configuration

## Installation

```bash
pip install -e .
```

## Usage

```python
from liuhao_core import configure_logging, start_metrics_server

# Configure structured logging
logger = configure_logging()

# Start metrics server
start_metrics_server()

# Use retry decorator
from liuhao_core.retry import retry_with_backoff

@retry_with_backoff(stop_attempt_number=3)
def call_external_service():
    pass
```
