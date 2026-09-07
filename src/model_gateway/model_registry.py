"""Model Registry for Model Gateway

Tracks registered models with capabilities, versions, and status.

Spec items 181-182: Model Registry supports:
- Model registration with full metadata
- Capability tracking per model
- Version management
- Status monitoring (active/deprecated/disabled)
- Owner/permission tracking
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Set,
    Tuple,
    Union,
)


class RegisteredModel:
    """A registered model in the Model Registry.

    Attributes:
        model_id: Unique model identifier
        provider: Provider type
        model_name: Human-readable model name
        version: Model version
        capabilities: Provider capabilities
        status: Registration status
        owner: Model owner/organization
        created_at: Registration timestamp
        last_seen: Last time model was used
    """

    def __init__(
        self,
        *,
        model_id: str,
        provider: str,
        model_name: str,
        version: str = "1.0.0",
        capabilities: Optional[ProviderCapabilities] = None,
        status: str = "active",
        owner: Optional[str] = None,
    ):
        self.model_id = model_id
        self.provider = provider
        self.model_name = model_name
        self.version = version
        self.capabilities = capabilities or ProviderCapabilities()
        self.status = status
        self.owner = owner
        self.created_at = datetime.now(timezone.utc)
        self.last_seen = self.created_at

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "model_id": self.model_id,
            "provider": self.provider,
            "model_name": self.model_name,
            "version": self.version,
            "capabilities": {
                "supports_streaming": self.capabilities.supports_streaming,
                "supports_structured_output": self.capabilities.supports_structured_output,
                "supports_function_calling": self.capabilities.supports_function_calling,
                "supports_vision": self.capabilities.supports_vision,
                "supports_embeddings": self.capabilities.supports_embeddings,
                "max_context_tokens": self.capabilities.max_context_tokens,
                "max_output_tokens": self.capabilities.max_output_tokens,
                "rate_limit_rpm": self.capabilities.rate_limit_rpm,
                "rate_limit_tpm": self.capabilities.rate_limit_tpm,
            },
            "status": self.status,
            "owner": self.owner,
            "created_at": self.created_at.isoformat(),
            "last_seen": self.last_seen.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> RegisteredModel:
        """Create from dictionary."""
        caps_data = data.get("capabilities", {})
        from .provider_adapter import ProviderCapabilities
        caps = ProviderCapabilities(
            supports_streaming=caps_data.get("supports_streaming", False),
            supports_structured_output=caps_data.get("supports_structured_output", False),
            supports_function_calling=caps_data.get("supports_function_calling", False),
            supports_vision=caps_data.get("supports_vision", False),
            supports_embeddings=caps_data.get("supports_embeddings", False),
            max_context_tokens=caps_data.get("max_context_tokens", 4096),
            max_output_tokens=caps_data.get("max_output_tokens", 1024),
            rate_limit_rpm=caps_data.get("rate_limit_rpm", 60),
            rate_limit_tpm=caps_data.get("rate_limit_tpm", 10000),
        )
        from datetime import datetime
        from zoneinfo import ZoneInfo
        created_at = datetime.fromisoformat(
            data["created_at"]
        ).replace(tzinfo=ZoneInfo("UTC"))
        last_seen = datetime.fromisoformat(
            data["last_seen"]
        ).replace(tzinfo=ZoneInfo("UTC"))
        model = cls(
            model_id=data["model_id"],
            provider=data["provider"],
            model_name=data["model_name"],
            version=data.get("version", "1.0.0"),
            capabilities=caps,
            status=data.get("status", "active"),
            owner=data.get("owner"),
        )
        model.created_at = created_at
        model.last_seen = last_seen
        return model

    def __repr__(self) -> str:
        return f"RegisteredModel(model_id={self.model_id}, provider={self.provider}, status={self.status})"


class ModelRegistry:
    """Registry for tracking all registered models.

    Supports:
    - Register new models
    - Lookup by model_id or provider+name
    - Update model status and last_seen
    - List models by status, provider, or capability
    - Deregister/Deprecate models
    - Track usage metrics
    """

    def __init__(self, storage_path: Optional[str] = None):
        self._models: Dict[str, RegisteredModel] = {}
        self._by_provider: Dict[str, Set[str]] = {}  # provider -> set of model_ids
        self._by_status: Dict[str, Set[str]] = {}  # status -> set of model_ids
        self._storage_path = storage_path
        self._dirty = False

    def register(self, model: RegisteredModel) -> None:
        """Register a new model."""
        self._models[model.model_id] = model
        if model.provider not in self._by_provider:
            self._by_provider[model.provider] = set()
        self._by_provider[model.provider].add(model.model_id)
        if model.status not in self._by_status:
            self._by_status[model.status] = set()
        self._by_status[model.status].add(model.model_id)
        self._dirty = True

    def unregister(self, model_id: str) -> Optional[RegisteredModel]:
        """Unregister a model. Returns the model if found, None otherwise."""
        if model_id not in self._models:
            return None
        model = self._models.pop(model_id)
        self._by_provider[model.provider].discard(model_id)
        if not self._by_provider[model.provider]:
            del self._by_provider[model.provider]
        self._by_status[model.status].discard(model_id)
        if not self._by_status[model.status]:
            del self._by_status[model.status]
        self._dirty = True
        return model

    def get(self, model_id: str) -> Optional[RegisteredModel]:
        """Get a model by model_id."""
        return self._models.get(model_id)

    def get_by_provider(
        self, provider: str
    ) -> List[RegisteredModel]:
        """Get all models from a specific provider."""
        model_ids = self._by_provider.get(provider, [])
        return [self._models[mid] for mid in model_ids if mid in self._models]

    def get_by_status(
        self, status: str
    ) -> List[RegisteredModel]:
        """Get all models with a specific status."""
        model_ids = self._by_status.get(status, set())
        return [self._models[mid] for mid in model_ids if mid in self._models]

    def list_all(self) -> List[RegisteredModel]:
        """List all registered models."""
        return list(self._models.values())

    def list_active(self) -> List[RegisteredModel]:
        """List all active models."""
        return self.get_by_status("active")

    def list_deprecated(self) -> List[RegisteredModel]:
        """List all deprecated models."""
        return self.get_by_status("deprecated")

    def list_disabled(self) -> List[RegisteredModel]:
        """List all disabled models."""
        return self.get_by_status("disabled")

    def update_status(
        self, model_id: str, status: str
    ) -> Optional[RegisteredModel]:
        """Update a model's status."""
        model = self.get(model_id)
        if model is None:
            return None
        model.status = status
        model.last_seen = datetime.now(timezone.utc)
        self._dirty = True
        return model

    def update_last_seen(self, model_id: str) -> Optional[RegisteredModel]:
        """Update the last_seen timestamp for a model."""
        model = self.get(model_id)
        if model is None:
            return None
        model.last_seen = datetime.now(timezone.utc)
        self._dirty = True
        return model

    def find_by_capability(
        self,
        capability: str,
        min_context: Optional[int] = None,
        max_context: Optional[int] = None,
    ) -> List[RegisteredModel]:
        """Find models supporting a specific capability.

        Args:
            capability: One of "streaming", "structured_output", "function_calling", "vision", "embeddings"
            min_context: Minimum context window required (tokens)
            max_context: Maximum context window allowed (tokens)

        Returns:
            List of models matching the capability criteria
        """
        results = []
        for model in self._models.values():
            caps = model.capabilities
            capability_ok = False
            if capability == "streaming" and caps.supports_streaming:
                capability_ok = True
            elif capability == "structured_output" and caps.supports_structured_output:
                capability_ok = True
            elif capability == "function_calling" and caps.supports_function_calling:
                capability_ok = True
            elif capability == "vision" and caps.supports_vision:
                capability_ok = True
            elif capability == "embeddings" and caps.supports_embeddings:
                capability_ok = True

            if not capability_ok:
                continue

            # Context window filtering
            if min_context is not None and caps.max_context_tokens < min_context:
                continue
            if max_context is not None and caps.max_context_tokens > max_context:
                continue

            results.append(model)

        return results

    def to_dict(self) -> Dict[str, Any]:
        """Convert registry to dictionary for serialization."""
        return {
            "model_ids": [m.model_id for m in self._models.values()],
            "models": {m.model_id: m.to_dict() for m in self._models.values()},
            "by_provider": {
                provider: [m.model_id for m in models]
                for provider, models in self._by_provider.items()
            },
            "by_status": {
                status: [m.model_id for m in models]
                for status, models in self._by_status.items()
            },
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ModelRegistry:
        """Create registry from dictionary."""
        registry = ModelRegistry()
        from .provider_adapter import ProviderCapabilities

        for md in data.get("models", []):
            model = RegisteredModel.from_dict(md)
            registry._models[model.model_id] = model
            if model.provider not in registry._by_provider:
                registry._by_provider[model.provider] = set()
            registry._by_provider[model.provider].add(model.model_id)
            if model.status not in registry._by_status:
                registry._by_status[model.status] = set()
            registry._by_status[model.status].add(model.model_id)

        registry._dirty = False
        return registry

    def persist(self) -> None:
        """Persist registry to disk if storage_path is set."""
        if self._storage_path and self._dirty:
            import json
            with open(self._storage_path, "w") as f:
                json.dump(self.to_dict(), f, indent=2)
            self._dirty = False

    @classmethod
    def load(cls, storage_path: str) -> ModelRegistry:
        """Load registry from disk."""
        import json
        with open(storage_path, "r") as f:
            data = json.load(f)
        return cls.from_dict(data)

    def __len__(self) -> int:
        return len(self._models)

    def __contains__(self, model_id: str) -> bool:
        return model_id in self._models

    def __repr__(self) -> str:
        return f"ModelRegistry(num_models={len(self._models)}, active={len(self.get_active())})"

    def get_active(self) -> List[RegisteredModel]:
        """Convenience: get active models list."""
        return self.get_by_status("active")