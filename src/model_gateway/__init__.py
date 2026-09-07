"""Model Gateway for LiuHao AI OS — Phase 4

Implements:
- Provider Adapter: Wraps various AI model providers
- Model Registry: Tracks registered models with capabilities
- Model Router: Routes requests to appropriate models
- Usage & Cost Tracking
- Streaming Support
- Structured Output
- Tool Calling
"""

from .provider_adapter import ProviderAdapter, ProviderType, ProviderCapabilities
from .model_registry import ModelRegistry, RegisteredModel
from .model_router import ModelRouter, RoutingRule, RouteResult

__all__ = [
    "ProviderAdapter",
    "ProviderType",
    "ProviderCapabilities",
    "ModelRegistry",
    "RegisteredModel",
    "ModelRouter",
    "RoutingRule",
    "RouteResult",
]