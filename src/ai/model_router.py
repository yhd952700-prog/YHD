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

Two entry points:

* ``get_provider_for_task()`` — legacy opt-in path (``LIUHAO_MODEL_ROUTER``
  truthy), routed over the static catalog; unchanged.
* ``provider_for_task()`` — the **live path** used by ``/v1/chat``. It routes
  over the enabled models saved in model management and falls back to the
  configured default model on any miss; ``LIUHAO_MODEL_ROUTER=off`` is the
  rollout kill switch. Both paths log every decision with its reason.
"""
from __future__ import annotations

import logging
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
    resolve_provider,
)
from src.ai.model_config import vendor_for
from src.model_gateway import (
    ModelRegistry,
    ModelRouter,
    ProviderCapabilities,
    RegisteredModel,
    RouteResult,
)

logger = logging.getLogger("liuhao.ai.model_router")


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
    base_url: str = ""
    #: 真实厂商（``provider`` 只是协议适配器名），仅用于日志展示。
    vendor: str = ""


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
    "embedding": TaskProfile(["embeddings"], min_context=1024, prefer_tier=ModelTier.LOCAL),
}

#: The ``TaskType`` values already defined by ``src.model_gateway``
#: (chat / summarization / embedding / ...) mapped onto the profiles above, so a
#: live caller can pass the documented task name instead of an internal profile
#: key. Aliases are additive: existing keys keep their exact semantics.
TASK_PROFILE_ALIASES: Dict[str, str] = {
    "chat": "default",
    "text_generation": "default",
    "summarization": "cheap_bulk",
    "summarize": "cheap_bulk",
    "embedding": "embedding",
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


def _resolve_profile(task_type: Optional[str]) -> TaskProfile:
    """Map a documented task type (incl. aliases) to its routing profile."""
    key = task_type or "default"
    key = TASK_PROFILE_ALIASES.get(key, key)
    profile = TASK_PROFILES.get(key)
    if profile is None:
        raise NoModelAvailableError(f"Unknown task_type: {task_type!r}")
    return profile


def _profile_kwargs(profile: TaskProfile, overrides: Dict[str, Any]) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {
        "required_capabilities": profile.required_capabilities or None,
        "min_context": profile.min_context,
        "max_cost_per_token": profile.max_cost_per_token,
        "max_latency_ms": profile.max_latency_ms,
        "prefer_tier": profile.prefer_tier,
    }
    kwargs.update(overrides)
    return kwargs


def route_task(task_type: Optional[str] = None, **overrides: Any) -> RouteResult:
    """Route a logical task type to a model. Raises NoModelAvailableError if none fit."""
    profile = _resolve_profile(task_type)
    try:
        return get_model_router().route(
            task_type=task_type, **_profile_kwargs(profile, overrides)
        )
    except ValueError as exc:
        raise NoModelAvailableError(
            f"No model available for task_type={task_type!r}: {exc}"
        ) from exc


#: Providers whose APIs expose an embeddings endpoint. Capabilities of a saved
#: model are not stored by model management, so routing uses these conservative
#: provider-level traits -- never a claim about a specific model name.
_EMBEDDING_PROVIDERS = frozenset({ProviderType.OPENAI, ProviderType.GOOGLE, ProviderType.OLLAMA})


def _capabilities_for_provider(provider: str) -> ProviderCapabilities:
    provider = provider.strip().lower()
    return ProviderCapabilities(
        supports_streaming=True,
        supports_structured_output=provider in {"openai", "anthropic", "google"},
        supports_function_calling=provider in {"openai", "anthropic", "deepseek"},
        supports_vision=provider in {"openai", "anthropic", "google"},
        supports_embeddings=provider in _EMBEDDING_PROVIDERS,
        # Conservative window: enough for chat/embedding profiles, deliberately
        # below the 32k ``reasoning`` floor so that task is NOT silently claimed
        # for a model whose real window we do not know (it falls back instead).
        max_context_tokens=8192,
        max_output_tokens=4096,
    )


def _tier_for(provider: str) -> ModelTier:
    provider = provider.strip().lower()
    if provider == ProviderType.OLLAMA:
        return ModelTier.LOCAL
    if provider == ProviderType.MOCK:
        return ModelTier.SPECIAL
    return ModelTier.CLOUD


def catalog_from_store(
    store: Any = None,
    *,
    visible_to: Optional[List[str]] = None,
    allow_unowned: bool = False,
    allow_owners: Optional[List[str]] = None,
) -> Dict[str, ModelProfile]:
    """Snapshot the **enabled** models saved in model management as profiles.

    Returns ``{}`` when the store is empty or unreadable. The empty result is
    meaningful: callers must fall back to the default model, never to an
    arbitrary hard-coded catalog entry (that would silently call a provider the
    operator never configured).

    ``visible_to`` / ``allow_unowned`` 按 Key 归属过滤；``allow_owners`` 再收窄
    到"本次允许借用其 Key 的账号集合"（未授权借用不进入候选，绝不静默借用）。
    """
    if store is None:
        try:
            from src.ai.model_config import get_model_config_store

            store = get_model_config_store()
        except Exception as exc:  # pragma: no cover - import/初始化故障
            logger.warning(
                "model router: model config store unavailable (%s)",
                type(exc).__name__,
            )
            return {}
    try:
        records = store.list_models(
            visible_to=visible_to, allow_unowned=allow_unowned
        )
    except Exception as exc:  # pragma: no cover - 存储故障
        logger.warning(
            "model router: reading model config failed (%s)", type(exc).__name__
        )
        return {}

    allowed = set(allow_owners) if allow_owners is not None else None
    profiles: Dict[str, ModelProfile] = {}
    for record in records:
        if not record.get("enabled", True):
            continue
        owner = str(record.get("owner_principal") or "")
        if allowed is not None and owner and owner not in allowed:
            logger.info(
                "model router: skipping model %s owned by %s (not authorized)",
                record.get("id"), owner,
            )
            continue
        model_id = str(record.get("id") or "").strip()
        provider = str(record.get("provider") or "").strip().lower()
        model_name = str(record.get("model") or "").strip()
        if not (model_id and provider and model_name):
            continue
        profiles[model_id] = ModelProfile(
            model_id=model_id,
            provider=provider,
            model_name=model_name,
            tier=_tier_for(provider),
            capabilities=_capabilities_for_provider(provider),
            base_url=str(record.get("base_url") or ""),
            vendor=vendor_for(provider, record.get("base_url")),
        )
    return profiles


def _router_for(profiles: Dict[str, ModelProfile]) -> LiuhaoModelRouter:
    registry = ModelRegistry()
    for profile in profiles.values():
        registry.register(
            RegisteredModel(
                model_id=profile.model_id,
                provider=profile.provider,
                model_name=profile.model_name,
                capabilities=profile.capabilities,
            )
        )
    return LiuhaoModelRouter(registry, dict(profiles))


def route_task_from_store(
    task_type: Optional[str] = None,
    *,
    store: Any = None,
    visible_to: Optional[List[str]] = None,
    allow_unowned: bool = False,
    allow_owners: Optional[List[str]] = None,
    **overrides: Any,
) -> RouteResult:
    """Route against the live model-management store (not the static catalog)."""
    profiles = catalog_from_store(
        store,
        visible_to=visible_to,
        allow_unowned=allow_unowned,
        allow_owners=allow_owners,
    )
    if not profiles:
        raise NoModelAvailableError("model management has no enabled models")
    profile = _resolve_profile(task_type)
    try:
        return _router_for(profiles).route(
            task_type=task_type, **_profile_kwargs(profile, overrides)
        )
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


def _routing_disabled() -> bool:
    """Explicit kill switch. Absent means "route" on the live path."""
    raw = os.environ.get("LIUHAO_MODEL_ROUTER")
    if not raw or not raw.strip():
        return False
    return raw.strip().lower() in {"0", "off", "false", "no", "disabled"}


def provider_for_task(
    task_type: Optional[str] = None,
    *,
    model_id: Optional[str] = None,
    visible_to: Optional[List[str]] = None,
    allow_unowned: bool = False,
    allow_owners: Optional[List[str]] = None,
    allow_env_fallback: bool = True,
) -> BaseProvider:
    """Provider for a **live** call, routed through model management.

    Precedence: explicit ``model_id`` (caller override) > task routing over the
    enabled models saved in model management > the configured default model.
    Any miss (no saved models, capability miss, unreadable store) falls back to
    the default, which is exactly the pre-routing behaviour -- so enabling this
    on the live path cannot change behaviour unless the operator saved models.

    ``visible_to`` / ``allow_owners`` 是 Key 归属视角（见 ``catalog_from_store``）；
    给定后未命中不再回退到 ``.env`` 的机器级 Key，而是明确 409。

    Every branch logs its decision and reason; ``LIUHAO_MODEL_ROUTER=off``
    remains available as a rollout rollback.
    """
    if model_id:
        logger.info("model routing skipped: explicit model_id=%s", model_id)
        return resolve_provider(
            model_id,
            visible_to=visible_to,
            allow_unowned=allow_unowned,
            allow_env_fallback=allow_env_fallback,
        )

    if _routing_disabled():
        logger.info("model routing disabled by LIUHAO_MODEL_ROUTER; using default model")
        return resolve_provider(
            visible_to=visible_to,
            allow_unowned=allow_unowned,
            allow_env_fallback=allow_env_fallback,
        )

    try:
        result = route_task_from_store(
            task_type,
            visible_to=visible_to,
            allow_unowned=allow_unowned,
            allow_owners=allow_owners,
        )
    except NoModelAvailableError as exc:
        logger.info(
            "model routing fallback: task=%s reason=%s -> default model",
            task_type or "default",
            exc,
        )
        return resolve_provider(
            visible_to=visible_to,
            allow_unowned=allow_unowned,
            allow_env_fallback=allow_env_fallback,
        )

    selected = result.selected_model
    provider = resolve_provider(
        selected.model_id,
        visible_to=visible_to,
        allow_unowned=allow_unowned,
        allow_env_fallback=allow_env_fallback,
    )
    # provider 字段是协议适配器名（例如 openai 兼容协议），vendor 才是真实厂商；
    # 两者都记，排障时不再把 DeepSeek 误读成 OpenAI。
    logger.info(
        "model routing decision: task=%s model_id=%s provider=%s vendor=%s model=%s "
        "reason=%s fallbacks=%s",
        task_type or "default",
        selected.model_id,
        selected.provider,
        vendor_for(selected.provider, getattr(provider, "base_url", None)),
        selected.model_name,
        result.reason,
        ",".join(result.fallback_models) or "-",
    )
    return provider


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
        logger.info(
            "model routing failed (%s); falling back to default provider", exc
        )
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
