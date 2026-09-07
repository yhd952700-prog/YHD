"""
Model Registry for LiuHao AI OS

Provides MLflow-style model version management with:
- Model version tracking and URI addressing
- Version lifecycle management (create/register/archived)
- Model metadata and provenance tracking
- Compatibility and capability checking
"""

from dataclasses import dataclass, field, asdict
from typing import Dict, Any, Optional, List

import json
import uuid
import time
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class ModelStatus:
    """Model version status values."""
    REGISTERED = "registered"
    ACTIVE = "active"
    ARCHIVED = "archived"
    DEPRECATED = "deprecated"


class ModelType:
    """Supported model types."""
    LLM = "llm"
    EMBEDDING = "embedding"
    CLASSIFIER = "classifier"
    REGRESSOR = "regressor"
    CUSTOM = "custom"


@dataclass
class ModelMetadata:
    """Metadata for a model version."""
    model_id: str           # Unique model identifier (name + version)
    version: str            # Model version string
    model_type: str         # Type of model (llm, embedding, etc)
    name: str               # Human-readable model name
    description: str = ""
    author: str = ""
    license: str = ""

    # Capabilities
    max_tokens: int = 4096
    temperature: float = 0.0
    capabilities: List[str] = field(default_factory=list)

    # Provenance
    source: str = "local"   # local, s3, gcs, remote_api
    training_data_hash: str = ""
    created_at: float = field(default_factory=time.time)
    created_by: str = ""

    # Version tracking
    parent_version: Optional[str] = None
    derivative_of: Optional[str] = None

    # Status
    status: str = ModelStatus.REGISTERED
    tags: List[str] = field(default_factory=list)

    # Performance metrics
    eval_metrics: Dict[str, float] = field(default_factory=dict)
    baseline_metrics: Optional[Dict[str, float]] = None

    # Resource requirements
    estimated_latency_ms: int = 1000
    estimated_cost_per_1k: float = 0.01


class ModelRegistry:
    """
    Model version registry for LiuHua AI OS.

    Provides:
    - Model version creation and registration
    - Version retrieval and lookup
    - Lifecycle management (activate, archive, deprecate)
    - Metadata and provenance tracking
    - Compatibility checking
    """

    def __init__(self, storage_path: Optional[str] = None):
        """
        Initialize Model Registry.

        Args:
            storage_path: Path to model metadata storage file
        """
        self.storage_path = Path(storage_path) if storage_path else Path(
            "D:\\LiuHao-AI-OS\\data\\model_registry.json"
        )
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

        # In-memory registry
        self._models: Dict[str, ModelMetadata] = {}
        self._version_index: Dict[str, List[str]] = {}  # version -> [model_id]
        self._name_index: Dict[str, List[str]] = {}  # name -> [model_id]

        # Load existing state
        self._load_state()

    def _load_state(self) -> None:
        """Load registry state from persistent storage."""
        if self.storage_path.exists():
            try:
                with open(self.storage_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                for model_data in data.get("models", []):
                    metadata = ModelMetadata(
                        model_id=model_data["model_id"],
                        version=model_data["version"],
                        model_type=model_data["model_type"],
                        name=model_data["name"],
                        description=model_data.get("description", ""),
                        author=model_data.get("author", ""),
                        license=model_data.get("license", ""),
                        max_tokens=model_data.get("max_tokens", 4096),
                        temperature=model_data.get("temperature", 0.0),
                        capabilities=model_data.get("capabilities", []),
                        source=model_data.get("source", "local"),
                        training_data_hash=model_data.get("training_data_hash", ""),
                        created_at=model_data.get("created_at", time.time()),
                        created_by=model_data.get("created_by", ""),
                        parent_version=model_data.get("parent_version"),
                        derivative_of=model_data.get("derivative_of"),
                        status=model_data.get("status", ModelStatus.REGISTERED),
                        tags=model_data.get("tags", []),
                        eval_metrics=model_data.get("eval_metrics", {}),
                        baseline_metrics=model_data.get("baseline_metrics"),
                        estimated_latency_ms=model_data.get("estimated_latency_ms", 1000),
                        estimated_cost_per_1k=model_data.get("estimated_cost_per_1k", 0.01),
                    )

                    self._models[metadata.model_id] = metadata

                    # Index by version
                    if metadata.version not in self._version_index:
                        self._version_index[metadata.version] = []
                    self._version_index[metadata.version].append(metadata.model_id)

                    # Index by name
                    if metadata.name not in self._name_index:
                        self._name_index[metadata.name] = []
                    self._name_index[metadata.name].append(metadata.model_id)

            except Exception as e:
                logger.error(f"Failed to load model registry: {e}")

    def _save_state(self) -> None:
        """Save registry state to persistent storage."""
        data = {
            "version": 1,
            "saved_at": time.time(),
            "models": [asdict(metadata) for metadata in self._models.values()],
        }

        with open(self.storage_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)

    # ==================== Model Registration ====================

    def register_model(
        self,
        name: str,
        model_type: str,
        version: Optional[str] = None,
        description: str = "",
        **kwargs,
    ) -> ModelMetadata:
        """
        Register a new model version.

        Args:
            name: Human-readable model name
            model_type: Type of model (llm, embedding, classifier, etc)
            version: Version string (auto-generated if None)
            description: Model description
            **kwargs: Additional ModelMetadata fields

        Returns:
            ModelMetadata object for the registered model
        """
        # Generate version if not provided
        if version is None:
            version = f"v{int(time.time())}"

        # Generate model_id if not provided
        model_id = f"{name.lower().replace(' ', '-')}-{version}"

        # Create metadata
        metadata = ModelMetadata(
            model_id=model_id,
            version=version,
            model_type=model_type,
            name=name,
            description=description,
            **kwargs,
        )

        # Register
        self._models[model_id] = metadata

        if version not in self._version_index:
            self._version_index[version] = []
        self._version_index[version].append(model_id)

        if name not in self._name_index:
            self._name_index[name] = []
        self._name_index[name].append(model_id)

        # Persist
        self._save_state()

        logger.info(f"Model registered: {model_id} v{version}")
        return metadata

    # ==================== Model Retrieval ====================

    def get_model(self, model_id: str) -> Optional[ModelMetadata]:
        """Get model metadata by model_id."""
        return self._models.get(model_id)

    def get_model_by_name(self, name: str) -> List[ModelMetadata]:
        """Get all models with the given name (for version history)."""
        model_ids = self._name_index.get(name, [])
        return [self._models[mid] for mid in model_ids if mid in self._models]

    def get_model_by_version(self, version: str) -> List[ModelMetadata]:
        """Get all models with the given version string."""
        model_ids = self._version_index.get(version, [])
        return [self._models[mid] for mid in model_ids if mid in self._models]

    def list_models(
        self,
        model_type: Optional[str] = None,
        status: Optional[str] = None,
        name: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> List[ModelMetadata]:
        """List models with optional filtering."""
        models = list(self._models.values())

        if model_type:
            models = [m for m in models if m.model_type == model_type]

        if status:
            models = [m for m in models if m.status == status]

        if name:
            models = [m for m in models if m.name == name]

        if tags:
            models = [m for m in models if any(t in m.tags for t in tags)]

        return models

    # ==================== Lifecycle Management ====================

    def activate_model(self, model_id: str) -> bool:
        """Activate a model version (deactivate others with same name)."""
        if model_id not in self._models:
            return False

        # Deactivate other versions with same name
        name = self._models[model_id].name
        for mid in self._name_index.get(name, []):
            self._models[mid].status = ModelStatus.ARCHIVED

        # Activate target
        self._models[model_id].status = ModelStatus.ACTIVE

        self._save_state()
        logger.info(f"Model activated: {model_id}")
        return True

    def archive_model(self, model_id: str) -> bool:
        """Archive a model version."""
        if model_id not in self._models:
            return False

        self._models[model_id].status = ModelStatus.ARCHIVED
        self._save_state()
        logger.info(f"Model archived: {model_id}")
        return True

    def deprecate_model(self, model_id: str) -> bool:
        """Deprecate a model version."""
        if model_id not in self._models:
            return False

        self._models[model_id].status = ModelStatus.DEPRECATED
        self._save_state()
        logger.info(f"Model deprecated: {model_id}")
        return True

    # ==================== Provenance & Comparison ====================

    def get_model_lineage(self, model_id: str) -> List[ModelMetadata]:
        """Get model version lineage (parent/child relationships)."""
        lineage = []
        visited = set()

        current = model_id
        while current and current in self._models:
            if current in visited:
                break
            visited.add(current)
            metadata = self._models[current]
            lineage.append(metadata)

            # Move to parent if exists
            current = metadata.parent_version

        return lineage

    def compare_models(self, model_id_1: str, model_id_2: str) -> Dict[str, Any]:
        """Compare two model versions."""
        m1 = self._models.get(model_id_1)
        m2 = self._models.get(model_id_2)

        if not m1 or not m2:
            return {"error": "One or both models not found"}

        comparison = {
            "model_1": {"id": m1.model_id, "version": m1.version, "status": m1.status},
            "model_2": {"id": m2.model_id, "version": m2.version, "status": m2.status},
            "differences": {},
            "metrics_comparison": {},
        }

        # Compare metrics
        if m1.eval_metrics and m2.eval_metrics:
            comparison["metrics_comparison"] = {}
            for key in set(list(m1.eval_metrics.keys()) + list(m2.eval_metrics.keys())):
                v1 = m1.eval_metrics.get(key)
                v2 = m2.eval_metrics.get(key)
                if v1 is not None and v2 is not None:
                    comparison["metrics_comparison"][key] = {
                        "v1": v1,
                        "v2": v2,
                        "change": v2 - v1 if isinstance(v1, (int, float)) and isinstance(v2, (int, float)) else "N/A",
                    }

        # Compare basic metadata
        comparison["differences"] = {
            "name": ("same" if m1.name == m2.name else "different"),
            "model_type": ("same" if m1.model_type == m2.model_type else "different"),
            "status": ("same" if m1.status == m2.status else "different"),
            "created_at": (m1.created_at, m2.created_at),
        }

        return comparison

    # ==================== Persistence ====================

    def reload(self) -> None:
        """Reload registry state from disk."""
        self._load_state()

    def export_registry(self, path: Optional[str] = None) -> str:
        """Export registry to JSON file path."""
        export_path = Path(path) if path else self.storage_path
        data = {
            "version": 1,
            "exported_at": time.time(),
            "models": [asdict(m) for m in self._models.values()],
        }
        export_path.write_text(json.dumps(data, indent=2))
        return str(export_path)

    def import_registry(self, path: str) -> int:
        """Import registry from JSON file. Returns number of models imported."""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            count = 0
            for model_data in data.get("models", []):
                metadata = ModelMetadata(
                    model_id=model_data["model_id"],
                    version=model_data["version"],
                    model_type=model_data["model_type"],
                    name=model_data["name"],
                    description=model_data.get("description", ""),
                    author=model_data.get("author", ""),
                    license=model_data.get("license", ""),
                    max_tokens=model_data.get("max_tokens", 4096),
                    temperature=model_data.get("temperature", 0.0),
                    capabilities=model_data.get("capabilities", []),
                    source=model_data.get("source", "local"),
                    training_data_hash=model_data.get("training_data_hash", ""),
                    created_at=model_data.get("created_at", time.time()),
                    created_by=model_data.get("created_by", ""),
                    parent_version=model_data.get("parent_version"),
                    derivative_of=model_data.get("derivative_of"),
                    status=model_data.get("status", ModelStatus.REGISTERED),
                    tags=model_data.get("tags", []),
                    eval_metrics=model_data.get("eval_metrics", {}),
                    baseline_metrics=model_data.get("baseline_metrics"),
                    estimated_latency_ms=model_data.get("estimated_latency_ms", 1000),
                    estimated_cost_per_1k=model_data.get("estimated_cost_per_1k", 0.01),
                )

                self._models[metadata.model_id] = metadata

                if metadata.version not in self._version_index:
                    self._version_index[metadata.version] = []
                self._version_index[metadata.version].append(metadata.model_id)

                if metadata.name not in self._name_index:
                    self._name_index[metadata.name] = []
                self._name_index[metadata.name].append(metadata.model_id)

                count += 1

            self._save_state()
            return count

        except Exception as e:
            logger.error(f"Failed to import registry: {e}")
            return 0


# Module-level convenience
_default_registry: Optional[ModelRegistry] = None


def get_model_registry() -> ModelRegistry:
    """Get the default Model Registry instance."""
    global _default_registry

    if _default_registry is None:
        _default_registry = ModelRegistry()

    return _default_registry


def register_model(name: str, model_type: str, **kwargs) -> ModelMetadata:
    """Convenience function to register a model."""
    return get_model_registry().register_model(name, model_type, **kwargs)


def get_model(model_id: str) -> Optional[ModelMetadata]:
    """Convenience function to get a model by ID."""
    return get_model_registry().get_model(model_id)


def list_models(**filters) -> List[ModelMetadata]:
    """Convenience function to list models with filters."""
    registry = get_model_registry()
    return registry.list_models(**filters)