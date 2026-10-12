"""Extended Model Registry for Model Gateway.

Enhanced model registry with:
- Rich model metadata (provider, capabilities, context window, cost)
- Model tiers (economy, standard, premium)
- Benchmark scores across various tasks
- Model status lifecycle (active, deprecated, experimental, maintenance)
- Model versioning with semantic version support
- Feature flags per model
- Model comparison utilities
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


class Capability(str, Enum):
    """Model capabilities."""

    TEXT_GENERATION = "text_generation"
    VISION = "vision"
    AUDIO = "audio"
    CODE_GENERATION = "code_generation"
    FUNCTION_CALLING = "function_calling"
    EMBEDDINGS = "embeddings"
    STRUCTURED_OUTPUT = "structured_output"
    STREAMING = "streaming"
    REASONING = "reasoning"
    MULTIMODAL = "multimodal"
    TOOL_USE = "tool_use"
    FINE_TUNABLE = "fine_tunable"
    IMAGE_GENERATION = "image_generation"
    VIDEO_GENERATION = "video_generation"


class ModelTier(str, Enum):
    """Model quality/cost tiers."""

    ECONOMY = "economy"
    STANDARD = "standard"
    PREMIUM = "premium"


class ModelStatus(str, Enum):
    """Model lifecycle status."""

    ACTIVE = "active"
    EXPERIMENTAL = "experimental"
    DEPRECATED = "deprecated"
    MAINTENANCE = "maintenance"
    ARCHIVED = "archived"


class TaskType(str, Enum):
    """Types of tasks models can perform."""

    TEXT_GENERATION = "text_generation"
    CODE_GENERATION = "code_generation"
    SUMMARIZATION = "summarization"
    TRANSLATION = "translation"
    QUESTION_ANSWERING = "question_answering"
    REASONING = "reasoning"
    VISION = "vision"
    EMBEDDING = "embedding"
    CLASSIFICATION = "classification"
    CREATIVE_WRITING = "creative_writing"


@dataclass
class ModelPricing:
    """Pricing information for a model.

    Attributes:
        cost_per_1k_input: Cost per 1000 input tokens in dollars
        cost_per_1k_output: Cost per 1000 output tokens in dollars
        cost_per_image: Cost per image (for vision models)
        cost_per_minute_audio: Cost per minute of audio
        currency: Currency code (e.g., "USD")
    """

    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0
    cost_per_image: float = 0.0
    cost_per_minute_audio: float = 0.0
    currency: str = "USD"


@dataclass
class ModelPerformance:
    """Performance characteristics of a model.

    Attributes:
        avg_latency_ms: Average latency in milliseconds
        p50_latency_ms: 50th percentile latency
        p95_latency_ms: 95th percentile latency
        p99_latency_ms: 99th percentile latency
        throughput_tokens_per_sec: Tokens per second throughput
        max_requests_per_minute: Rate limit per minute
        max_tokens_per_minute: Token rate limit per minute
    """

    avg_latency_ms: float = 500.0
    p50_latency_ms: float = 400.0
    p95_latency_ms: float = 1200.0
    p99_latency_ms: float = 2500.0
    throughput_tokens_per_sec: float = 50.0
    max_requests_per_minute: int = 3500
    max_tokens_per_minute: int = 90000


@dataclass
class ModelVersion:
    """A specific version of a model.

    Attributes:
        version: Semantic version string
        release_date: Release date of this version
        changelog: List of changes in this version
        is_latest: Whether this is the latest version
        deprecation_date: When this version will be deprecated
    """

    version: str = "1.0.0"
    release_date: Optional[str] = None
    changelog: List[str] = field(default_factory=list)
    is_latest: bool = True
    deprecation_date: Optional[str] = None


@dataclass
class FeatureFlags:
    """Feature flags for a model.

    Attributes:
        enabled: Whether the model is globally enabled
        streaming_enabled: Whether streaming is enabled
        function_calling_enabled: Whether function calling is enabled
        vision_enabled: Whether vision capabilities are enabled
        fine_tuning_enabled: Whether fine-tuning is available
        beta_features: Set of beta feature names
        experimental_features: Set of experimental feature names
    """

    enabled: bool = True
    streaming_enabled: bool = True
    function_calling_enabled: bool = False
    vision_enabled: bool = False
    fine_tuning_enabled: bool = False
    beta_features: Set[str] = field(default_factory=set)
    experimental_features: Set[str] = field(default_factory=set)


@dataclass
class RegisteredModelExtended:
    """Extended model registration with rich metadata.

    Attributes:
        model_id: Unique model identifier
        model_name: Human-readable model name
        provider: Provider name (e.g., "openai", "anthropic")
        tier: Model quality/cost tier
        status: Current lifecycle status
        capabilities: Set of model capabilities
        context_window: Maximum context window in tokens
        max_output_tokens: Maximum output tokens per request
        cost_per_1k_input: Cost per 1K input tokens (USD)
        cost_per_1k_output: Cost per 1K output tokens (USD)
        pricing: Detailed pricing information
        performance: Performance characteristics
        benchmarks: Benchmark scores keyed by task type
        versions: List of model versions
        current_version: Current active version
        feature_flags: Feature flags for this model
        description: Human-readable model description
        tags: Categorization tags
        owner: Team or individual responsible
        created_at: Registration timestamp
        updated_at: Last update timestamp
        metadata: Additional custom metadata
    """

    model_id: str
    model_name: str
    provider: str
    tier: ModelTier = ModelTier.STANDARD
    status: ModelStatus = ModelStatus.ACTIVE
    capabilities: Set[Capability] = field(default_factory=set)
    context_window: int = 8192
    max_output_tokens: int = 2048
    cost_per_1k_input: float = 0.001
    cost_per_1k_output: float = 0.002
    pricing: ModelPricing = field(default_factory=ModelPricing)
    performance: ModelPerformance = field(default_factory=ModelPerformance)
    benchmarks: Dict[str, float] = field(default_factory=dict)
    versions: List[ModelVersion] = field(default_factory=list)
    current_version: str = "1.0.0"
    feature_flags: FeatureFlags = field(default_factory=FeatureFlags)
    description: str = ""
    tags: List[str] = field(default_factory=list)
    owner: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Post-initialization setup."""
        if not self.versions:
            self.versions = [
                ModelVersion(version=self.current_version, is_latest=True)
            ]
        # Sync pricing fields
        if self.pricing.cost_per_1k_input == 0.0 and self.cost_per_1k_input > 0:
            self.pricing.cost_per_1k_input = self.cost_per_1k_input
        if self.pricing.cost_per_1k_output == 0.0 and self.cost_per_1k_output > 0:
            self.pricing.cost_per_1k_output = self.cost_per_1k_output
        # Sync performance latency
        if self.performance.avg_latency_ms == 500.0:
            pass  # Keep default

    @property
    def avg_latency_ms(self) -> float:
        """Average latency in milliseconds."""
        return self.performance.avg_latency_ms

    @property
    def is_active(self) -> bool:
        """Whether the model is active and available."""
        return (
            self.status == ModelStatus.ACTIVE and self.feature_flags.enabled
        )

    def has_capability(self, capability: Capability) -> bool:
        """Check if the model has a specific capability.

        Args:
            capability: Capability to check

        Returns:
            True if the model has the capability
        """
        return capability in self.capabilities

    def has_all_capabilities(self, required: Set[Capability]) -> bool:
        """Check if the model has all required capabilities.

        Args:
            required: Set of required capabilities

        Returns:
            True if the model has all required capabilities
        """
        return required.issubset(self.capabilities)

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Estimate the cost for a given token count.

        Args:
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens

        Returns:
            Estimated cost in dollars
        """
        return (
            input_tokens / 1000.0 * self.cost_per_1k_input
            + output_tokens / 1000.0 * self.cost_per_1k_output
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary.

        Returns:
            Dictionary representation
        """
        return {
            "model_id": self.model_id,
            "model_name": self.model_name,
            "provider": self.provider,
            "tier": self.tier.value,
            "status": self.status.value,
            "capabilities": [c.value for c in self.capabilities],
            "context_window": self.context_window,
            "max_output_tokens": self.max_output_tokens,
            "cost_per_1k_input": self.cost_per_1k_input,
            "cost_per_1k_output": self.cost_per_1k_output,
            "benchmarks": dict(self.benchmarks),
            "current_version": self.current_version,
            "description": self.description,
            "tags": list(self.tags),
            "owner": self.owner,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RegisteredModelExtended":
        """Deserialize from dictionary.

        Args:
            data: Dictionary representation

        Returns:
            RegisteredModelExtended instance
        """
        return cls(
            model_id=data["model_id"],
            model_name=data["model_name"],
            provider=data["provider"],
            tier=ModelTier(data.get("tier", "standard")),
            status=ModelStatus(data.get("status", "active")),
            capabilities={Capability(c) for c in data.get("capabilities", [])},
            context_window=data.get("context_window", 8192),
            max_output_tokens=data.get("max_output_tokens", 2048),
            cost_per_1k_input=data.get("cost_per_1k_input", 0.001),
            cost_per_1k_output=data.get("cost_per_1k_output", 0.002),
            benchmarks=data.get("benchmarks", {}),
            current_version=data.get("current_version", "1.0.0"),
            description=data.get("description", ""),
            tags=data.get("tags", []),
            owner=data.get("owner", ""),
            created_at=data.get(
                "created_at", datetime.now(timezone.utc).isoformat()
            ),
            updated_at=data.get(
                "updated_at", datetime.now(timezone.utc).isoformat()
            ),
        )


class ModelRegistryExtended:
    """Extended model registry with rich metadata management.

    Supports:
    - Model CRUD operations
    - Filtering by capabilities, tier, status, provider
    - Model comparison
    - Version management
    - Benchmark tracking
    """

    def __init__(self) -> None:
        """Initialize the extended registry."""
        self._models: Dict[str, RegisteredModelExtended] = {}
        self._provider_index: Dict[str, List[str]] = {}
        self._capability_index: Dict[Capability, List[str]] = {}
        self._tier_index: Dict[ModelTier, List[str]] = {}
        self._status_index: Dict[ModelStatus, List[str]] = {}

    # ------------------------------------------------------------------
    # Model CRUD
    # ------------------------------------------------------------------

    def register(self, model: RegisteredModelExtended) -> None:
        """Register a model.

        Args:
            model: Model to register

        Raises:
            ValueError: If model ID already exists
        """
        if model.model_id in self._models:
            raise ValueError(f"Model {model.model_id} already registered")

        self._models[model.model_id] = model
        self._update_indexes(model)

    def unregister(self, model_id: str) -> None:
        """Unregister a model.

        Args:
            model_id: ID of model to remove
        """
        model = self._models.pop(model_id, None)
        if model:
            self._remove_from_indexes(model)

    def get(self, model_id: str) -> Optional[RegisteredModelExtended]:
        """Get a model by ID.

        Args:
            model_id: Model identifier

        Returns:
            The model if found, None otherwise
        """
        return self._models.get(model_id)

    def update(self, model: RegisteredModelExtended) -> None:
        """Update an existing model.

        Args:
            model: Updated model data

        Raises:
            ValueError: If model doesn't exist
        """
        if model.model_id not in self._models:
            raise ValueError(f"Model {model.model_id} not found")

        old_model = self._models[model.model_id]
        self._remove_from_indexes(old_model)
        model.updated_at = datetime.now(timezone.utc).isoformat()
        self._models[model.model_id] = model
        self._update_indexes(model)

    # ------------------------------------------------------------------
    # Index management
    # ------------------------------------------------------------------

    def _update_indexes(self, model: RegisteredModelExtended) -> None:
        """Add model to all indexes."""
        # Provider index
        if model.provider not in self._provider_index:
            self._provider_index[model.provider] = []
        if model.model_id not in self._provider_index[model.provider]:
            self._provider_index[model.provider].append(model.model_id)

        # Capability index
        for cap in model.capabilities:
            if cap not in self._capability_index:
                self._capability_index[cap] = []
            if model.model_id not in self._capability_index[cap]:
                self._capability_index[cap].append(model.model_id)

        # Tier index
        if model.tier not in self._tier_index:
            self._tier_index[model.tier] = []
        if model.model_id not in self._tier_index[model.tier]:
            self._tier_index[model.tier].append(model.model_id)

        # Status index
        if model.status not in self._status_index:
            self._status_index[model.status] = []
        if model.model_id not in self._status_index[model.status]:
            self._status_index[model.status].append(model.model_id)

    def _remove_from_indexes(self, model: RegisteredModelExtended) -> None:
        """Remove model from all indexes."""
        if model.provider in self._provider_index:
            self._provider_index[model.provider] = [
                mid for mid in self._provider_index[model.provider]
                if mid != model.model_id
            ]

        for cap in model.capabilities:
            if cap in self._capability_index:
                self._capability_index[cap] = [
                    mid for mid in self._capability_index[cap]
                    if mid != model.model_id
                ]

        if model.tier in self._tier_index:
            self._tier_index[model.tier] = [
                mid for mid in self._tier_index[model.tier]
                if mid != model.model_id
            ]

        if model.status in self._status_index:
            self._status_index[model.status] = [
                mid for mid in self._status_index[model.status]
                if mid != model.model_id
            ]

    # ------------------------------------------------------------------
    # Query and filtering
    # ------------------------------------------------------------------

    def list_all(self) -> List[RegisteredModelExtended]:
        """List all registered models.

        Returns:
            List of all models
        """
        return list(self._models.values())

    def filter_by_capability(
        self, capability: Capability
    ) -> List[RegisteredModelExtended]:
        """Filter models by capability.

        Args:
            capability: Required capability

        Returns:
            List of models with the capability
        """
        model_ids = self._capability_index.get(capability, [])
        return [self._models[mid] for mid in model_ids]

    def filter_by_tier(
        self, tier: ModelTier
    ) -> List[RegisteredModelExtended]:
        """Filter models by tier.

        Args:
            tier: Model tier

        Returns:
            List of models in the tier
        """
        model_ids = self._tier_index.get(tier, [])
        return [self._models[mid] for mid in model_ids]

    def filter_by_status(
        self, status: ModelStatus
    ) -> List[RegisteredModelExtended]:
        """Filter models by status.

        Args:
            status: Model status

        Returns:
            List of models with the status
        """
        model_ids = self._status_index.get(status, [])
        return [self._models[mid] for mid in model_ids]

    def filter_by_provider(
        self, provider: str
    ) -> List[RegisteredModelExtended]:
        """Filter models by provider.

        Args:
            provider: Provider name

        Returns:
            List of models from the provider
        """
        model_ids = self._provider_index.get(provider, [])
        return [self._models[mid] for mid in model_ids]

    def query(
        self,
        *,
        capabilities: Optional[Set[Capability]] = None,
        tier: Optional[ModelTier] = None,
        status: Optional[ModelStatus] = None,
        provider: Optional[str] = None,
        min_context_window: Optional[int] = None,
        max_cost_per_1k_input: Optional[float] = None,
    ) -> List[RegisteredModelExtended]:
        """Query models with multiple filters.

        Args:
            capabilities: Required capabilities (all must be present)
            tier: Model tier
            status: Model status
            provider: Provider name
            min_context_window: Minimum context window size
            max_cost_per_1k_input: Maximum cost per 1K input tokens

        Returns:
            List of matching models
        """
        results = set(self._models.keys())

        if capabilities:
            cap_results: Optional[Set[str]] = None
            for cap in capabilities:
                cap_models = set(self._capability_index.get(cap, []))
                if cap_results is None:
                    cap_results = cap_models
                else:
                    cap_results = cap_results & cap_models
            if cap_results is not None:
                results = results & cap_results

        if tier:
            results = results & set(self._tier_index.get(tier, []))

        if status:
            results = results & set(self._status_index.get(status, []))

        if provider:
            results = results & set(self._provider_index.get(provider, []))

        models = [self._models[mid] for mid in results]

        if min_context_window:
            models = [
                m for m in models if m.context_window >= min_context_window
            ]

        if max_cost_per_1k_input is not None:
            models = [
                m for m in models
                if m.cost_per_1k_input <= max_cost_per_1k_input
            ]

        return models

    # ------------------------------------------------------------------
    # Comparison utilities
    # ------------------------------------------------------------------

    def compare(
        self, model_ids: List[str]
    ) -> Dict[str, Dict[str, Any]]:
        """Compare multiple models side by side.

        Args:
            model_ids: List of model IDs to compare

        Returns:
            Dictionary of model_id -> comparison data
        """
        result: Dict[str, Dict[str, Any]] = {}
        for mid in model_ids:
            model = self._models.get(mid)
            if model:
                result[mid] = {
                    "name": model.model_name,
                    "provider": model.provider,
                    "tier": model.tier.value,
                    "status": model.status.value,
                    "context_window": model.context_window,
                    "max_output_tokens": model.max_output_tokens,
                    "cost_per_1k_input": model.cost_per_1k_input,
                    "cost_per_1k_output": model.cost_per_1k_output,
                    "avg_latency_ms": model.avg_latency_ms,
                    "capabilities": sorted([c.value for c in model.capabilities]),
                    "benchmarks": dict(model.benchmarks),
                }
        return result

    def get_best_for_task(
        self,
        task_type: TaskType,
        *,
        capabilities: Optional[Set[Capability]] = None,
        tier: Optional[ModelTier] = None,
    ) -> Optional[RegisteredModelExtended]:
        """Get the best model for a specific task based on benchmarks.

        Args:
            task_type: Type of task
            capabilities: Required capabilities
            tier: Preferred tier

        Returns:
            Best model for the task, or None if no eligible models
        """
        candidates = self.query(
            capabilities=capabilities,
            tier=tier,
            status=ModelStatus.ACTIVE,
        )

        if not candidates:
            return None

        return max(
            candidates,
            key=lambda m: m.benchmarks.get(task_type.value, 0.0),
        )

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        """Get registry statistics.

        Returns:
            Dictionary of statistics
        """
        return {
            "total_models": len(self._models),
            "by_provider": {
                p: len(ids) for p, ids in self._provider_index.items()
            },
            "by_tier": {
                t.value: len(ids) for t, ids in self._tier_index.items()
            },
            "by_status": {
                s.value: len(ids) for s, ids in self._status_index.items()
            },
            "by_capability": {
                c.value: len(ids)
                for c, ids in self._capability_index.items()
            },
        }

    def __len__(self) -> int:
        return len(self._models)

    def __contains__(self, model_id: str) -> bool:
        return model_id in self._models

    def __repr__(self) -> str:
        return f"ModelRegistryExtended(models={len(self._models)})"
