"""
LiuHao AI OS Python SDK.

A comprehensive Python SDK for interacting with the LiuHao AI OS platform.
Provides resource-oriented APIs for agent management, memory systems,
knowledge bases, and more.

Quick start::

    from liuhao_sdk import LiuHaoClient

    client = LiuHaoClient(api_key="your-api-key")
    agents = client.agents.list()

Resources:
    - agents: Agent management and execution
    - memory: Memory storage, retrieval, and semantic search
    - knowledge: Knowledge base and document management
"""

from liuhao_sdk.client import LiuHaoClient
from liuhao_sdk.config import Config
from liuhao_sdk.exceptions import (
    LiuHaoError,
    AuthenticationError,
    APIError,
    NotFoundError,
    RateLimitError,
    ValidationError,
    TimeoutError,
)

# Version info
__version__ = "1.0.0"
__author__ = "LiuHao AI OS Team"
__license__ = "Apache-2.0"

__all__ = [
    "LiuHaoClient",
    "Config",
    "LiuHaoError",
    "AuthenticationError",
    "APIError",
    "NotFoundError",
    "RateLimitError",
    "ValidationError",
    "TimeoutError",
    "__version__",
    "__author__",
    "__license__",
]
