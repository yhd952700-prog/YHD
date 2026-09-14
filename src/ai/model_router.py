"""Model Router — task-aware model selection (Phase 5).

Bridges the existing (but previously off-path) ``src/model_gateway`` into the
live provider selection in ``src/ai/providers.py``.

Design: ``docs/MODEL-ROUTER-DESIGN.md``.

Two things the gateway itself does NOT do that this bridge fixes (root cause,
contained here so ``src/model_gateway`` stays untouched and independently usable):

1. ``ModelRouter._get_candidates`` treats ``max_cost_per_token`` as ``pass`` and
   ignores ``max_latency_ms`` entirely — so cost/latency routing was cosmetic.
2. Capability matching uses ``getattr(caps, name)`` but the attributes are named
   ``supports_*`` — passing the documented ``"vision"`` matched nothing. We
   normalize ``vision`` → ``supports_vision``.

Everything is opt-in: unless ``LIUHAO_MODEL_ROUTER`` is truthy,
``get_provider_for_task()`` returns the historical ``get_provider()`` unchanged.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from src.ai.providers import (
    BaseProvider,
    ProviderFactory,
    ProviderType,
    get_provider,
)
from src.model_gateway import (
    ModelRegistry,
    ModelRouter,
    ProviderCapabilities,
    RegisteredModel,
    RouteResult,
)


class ModelTier(str, Enum):
    """Deployment tier: cloud API / local runtime / special (dev)."""

    CLOUD = "cloud"
    LOCAL = "local"
    SPECIAL = "special"


class NoModelAvailableError(ValueError):
    """Raised when no registered model satisfies the requested constraints."""


@dataclass(frozen=True)
class ModelProfile:
    """Static catalog entry for a routable model.

    ``cost_per_1k_tokens`` / ``typical_latency_ms`` are **budget proxies used for
    constraint filtering, not live billing**.
    """

    model_id: str
    provider: str
    model_name: str
    tier: ModelTier
    capabilities: ProviderCapabilities
    cost_per_1k_tokens: float = 0.0
    typical_latency_ms: int = 1000


def _caps(**kwargs: Any) -> ProviderCapabilities:
    return ProviderCapabilities(**kwargs)


DEFAULT_CATALOG: List[ModelProfile] = [
    # ---------------- CLOUD ----------------
    ModelProfile(
        "openai:gpt-5-mini", ProviderType.OPENAI, "gpt-5-mini", ModelTier.CLOUD,
        _caps(supports_streaming=True, supports_structured_output=True,
              supports_function_calling=True, supports_vision=True,
              max_context_tokens=128000, max_output_tokens=16384),
        0.0006, 1200,
    ),
    ModelProfile(
        "openai:gpt-5", ProviderType.OPENAI, "gpt-5", ModelTier.CLOUD,
        _caps(supports_streaming=True, supports_structured_output=True,
              supports_function_calling=True, supports_vision=True,
              max_context_tokens=200000, max_output_tokens=32768),
        0.005, 2500,
    ),
    ModelProfile(
        "anthropic:claude-sonnet", ProviderType.ANTHROPIC, "claude-sonnet", ModelTier.CLOUD,
        _caps(supports_streaming=True, supports_structured_output=True,
              supports_function_calling=True, supports_vision=True,
              max_context_tokens=200000, max_output_tokens=8192),
        0.003, 1800,
    ),
    ModelProfile(
        "google:gemini", ProviderType.GOOGLE, "gemini-2", ModelTier.CLOUD,
        _caps(supports_streaming=True, supports_structured_output=True,
              supports_vision=True, max_context_tokens=1000000,
              max_output_tokens=8192),
        0.0005, 1400,
    ),
    ModelProfile(
        "deepseek:deepseek-chat", ProviderType.DEEPSEEK, "deepseek-chat", ModelTier.CLOUD,
        _caps(supports_streaming=True, supports_function_calling=True,
              max_context_tokens=64000, max_output_tokens=8192),
        0.0004, 1500,
    ),
    ModelProfile(
        "moonshot:kimi", ProviderType.MOONSHOT, "kimi", ModelTier.CLOUD,
        _caps(supports_streaming=True, max_context_tokens=128000, max_output_tokens=8192),
        0.0008, 1600,
    ),
    # ---------------- LOCAL ----------------
    ModelProfile(
        "ollama:qwen2.5:3b", ProviderType.OLLAMA, "qwen2.5:3b", ModelTier.LOCAL,
        _caps(supports_streaming=True, max_context_tokens=32000, max_output_tokens=4096),
        0.0, 2000,
    ),
    ModelProfile(
        "ollama:llama3", ProviderType.OLLAMA, "llama3", ModelTier.LOCAL,
        _caps(supports_streaming=True, max_context_tokens=8000, max_output_tokens=4096),
        0.0, 3000,
    ),
    # ---------------- SPECIAL (dev) ----------------
    ModelProfile(
        "mock:mock-model", ProviderType.MOCK, "mock-model", ModelTier.SPECIAL,
        _caps(supports_streaming=True, max_context_tokens=4096, max_output_tokens=1024),
        0.0, 50,
    ),
]


@dataclass(frozen=True)
class TaskProfile:
    """Routing profile for a logical task type."""

    required_capabilities: List[str]
    min_context: Optional[int] = None
    max_cost_per_token: Optional[float] = None
    max_latency_ms: Optional[int] = None
    prefer_tier: Optional[ModelTier] = None


TASK_PROFILES: Dict[str, TaskProfile] = {
    "default": TaskProfile([], prefer_tier=ModelTier.CLOUD),
    "reasoning": TaskProfile(["function_calling"], min_context=32000, prefer_tier=ModelTier.CLOUD),
    "vision": TaskProfile(["vision"], min_context=8000, prefer_tier=ModelTier.CLOUD),
    "structured": TaskProfile(["structured_output"], min_context=8000, prefer_tier=ModelTier.CLOUD),
    "cheap_bulk": TaskProfile([], min_context=4000, max_cost_per_token=0.001),
    "privacy": TaskProfile([], min_context=4000, max_latency_ms=3000, prefer_tier=ModelTier.LOCAL),
}

_CATALOG_BY_ID: Dict[str, ModelProfile] = {p.model_id: p for p in DEFAULT_CATALOG}


def _norm_cap(cap: str) -> str:
    """Map documented capability names to the ``supports_*`` attribute names."""
    return cap if cap.startswith("supports_") else f"supports_{cap}"


def get_profile(model_id: str) -> Optional[ModelProfile]:
    return _CATALOG_BY_ID.get(model_id)


class LiuhaoModelRouter(ModelRouter):
    """A ``ModelRouter`` that actually enforces cost/latency and prefers a tier."""

    def __init__(
        self, registry: ModelRegistry, profiles: Dict[str, ModelProfile]
    ) -> None:
        super().__init__(registry)
        self._profiles = profiles
        self._prefer_tier: Optional[ModelTier] = None

    def route(
        self,
        *,
        task_type: Optional[str] = None,
        prefer_tier: Optional[ModelTier] = None,
        required_capabilities: Optional[List[str]] = None,
        min_context: Optional[int] = None,
        max_cost_per_token: Optional[float] = None,
        max_latency_ms: Optional[int] = None,
        exclude_models: Optional[List[str]] = None,
        prefer_recent: bool = True,
    ) -> RouteResult:
        self._prefer_tier = prefer_tier
        try:
            return super().route(
                required_capabilities=required_capabilities,
                min_context=min_context,
                max_cost_per_token=max_cost_per_token,
                max_latency_ms=max_latency_ms,
                exclude_models=exclude_models,
                prefer_recent=prefer_recent,
            )
        finally:
            self._prefer_tier = None

    def _get_candidates(  # type: ignore[override]
        self,
        required_capabilities: Optional[List[str]] = None,
        min_context: Optional[int] = None,
        max_cost_per_token: Optional[float] = None,
        max_latency_ms: Optional[int] = None,
        exclude_models: Optional[List[str]] = None,
        prefer_recent: bool = True,
    ) -> List[RegisteredModel]:
        """Filter candidates with REAL cost/latency enforcement + cap normalization."""
        out: List[RegisteredModel] = []
        for model in self.registry.list_active():
            if exclude_models and model.model_id in exclude_models:
                continue
            caps = model.capabilities
            if required_capabilities:
                if not all(getattr(caps, _norm_cap(c), False) for c in required_capabilities):
                    continue
            if min_context is not None and caps.max_context_tokens < min_context:
                continue
            prof = self._profiles.get(model.model_id)
            if prof is not None:
                if max_cost_per_token is not None and prof.cost_per_1k_tokens > max_cost_per_token:
                    continue
                if max_latency_ms is not None and prof.typical_latency_ms > max_latency_ms:
                    continue
            out.append(model)
        out.sort(key=lambda m: (m.last_seen if prefer_recent else m.created_at), reverse=True)
        return out

    def _sort_candidates(  # type: ignore[override]
        self, candidates: List[RegisteredModel], prefer_recent: bool = True
    ) -> List[RegisteredModel]:
        ordered = super()._sort_candidates(candidates, prefer_recent=prefer_recent)
        if self._prefer_tier is not None:
            # Stable sort: preferred tier first, base ordering preserved within tiers.
            ordered.sort(
                key=lambda m: 0
                if (self._profiles.get(m.model_id)
                    and self._profiles[m.model_id].tier == self._prefer_tier)
                else 1
            )
        return ordered


def build_default_registry() -> ModelRegistry:
    registry = ModelRegistry()
    for prof in DEFAULT_CATALOG:
        registry.register(
            RegisteredModel(
                model_id=prof.model_id,
                provider=prof.provider,
                model_name=prof.model_name,
                capabilities=prof.capabilities,
            )
        )
    return registry


_router_lock = threading.RLock()
_router_instance: Optional[LiuhaoModelRouter] = None


def get_model_router() -> LiuhaoModelRouter:
    """Get or create the process-wide model router (built from DEFAULT_CATALOG)."""
    global _router_instance
    if _router_instance is None:
        with _router_lock:
            if _router_instance is None:
                _router_instance = LiuhaoModelRouter(
                    build_default_registry(), dict(_CATALOG_BY_ID)
                )
    return _router_instance


def route_task(task_type: Optional[str] = None, **overrides: Any) -> RouteResult:
    """Route a logical task type to a model. Raises NoModelAvailableError if none fit."""
    profile = TASK_PROFILES.get(task_type or "default")
    if profile is None:
        raise NoModelAvailableError(f"Unknown task_type: {task_type!r}")

    kwargs: Dict[str, Any] = {
        "required_capabilities": profile.required_capabilities or None,
        "min_context": profile.min_context,
        "max_cost_per_token": profile.max_cost_per_token,
        "max_latency_ms": profile.max_latency_ms,
        "prefer_tier": profile.prefer_tier,
    }
    kwargs.update(overrides)
    try:
        return get_model_router().route(task_type=task_type, **kwargs)
    except ValueError as exc:
        raise NoModelAvailableError(
            f"No model available for task_type={task_type!r}: {exc}"
        ) from exc


def provider_type_for(result: RouteResult) -> str:
    """The providers.py provider-type string for a routing result."""
    return result.selected_model.provider


def _router_enabled() -> bool:
    return os.environ.get("LIUHAO_MODEL_ROUTER", "").strip().lower() in {
        "1", "on", "true", "yes", "enabled"
    }


def get_provider_for_task(task_type: Optional[str] = None, **overrides: Any) -> BaseProvider:
    """Select a provider for a task.

    Default (router off): returns ``get_provider()`` — historical behaviour,
    unchanged. Router on: routes, then reuses the configured singleton when the
    routed provider matches it (avoids rebuilding), otherwise builds one via the
    factory. Missing credentials for a keyed provider fail closed.
    """
    if not _router_enabled():
        return get_provider()

    try:
        result = route_task(task_type, **overrides)
    except NoModelAvailableError as exc:
        print(f"Warning: model routing failed ({exc}); falling back to default provider")
        return get_provider()

    provider = provider_type_for(result)
    configured = (os.environ.get("AI_PROVIDER_TYPE") or ProviderType.MOCK).lower()
    if provider == configured:
        return get_provider()

    model = result.selected_model.model_name
    key = (
        os.environ.get(f"AI_PROVIDER_{provider.upper()}_KEY")
        or os.environ.get("AI_PROVIDER_KEY", "")
    )
    if provider not in (ProviderType.MOCK, ProviderType.OLLAMA):
        if not key or key == "[REDACTED]":
            raise RuntimeError(
                f"model router selected provider {provider!r} but no API key is "
                f"configured; set AI_PROVIDER_{provider.upper()}_KEY (fail-closed)."
            )
    name = os.environ.get("AI_PROVIDER_NAME", "liuhao-assistant")
    return ProviderFactory.create_provider(
        provider, name=name, model=model, api_key=key or "[REDACTED]"
    )
