"""MLOps model registry module for the Phase 4 feedback and continuous learning pipeline."""

from typing import Dict, Any, Optional, List


class RegisteredModel:
    """A registered model carrying evaluation metadata."""

    def __init__(self, version: str, metrics: Dict[str, Any]):
        self.version = version
        self.metrics = metrics
        self.registered_at: str = ""

    def __repr__(self):
        return f"RegisteredModel(version={self.version}, metrics={self.metrics})"


class ModelRegistry:
    """Simple version-to-metadata mapping model for A/B rollout and version comparison."""

    def __init__(self):
        self._registry: dict[str, RegisteredModel] = {}

    def register(self, version: str, metrics: Dict[str, Any]) -> RegisteredModel:
        """Register a model version with its evaluation metadata."""
        model = RegisteredModel(version=version, metrics=metrics)
        self._registry[version] = model
        return model

    def get(self, version: str) -> Optional[RegisteredModel]:
        """Get a registered model by version string."""
        return self._registry.get(version)

    def list_versions(self) -> List[str]:
        """List all registered model versions."""
        return list(self._registry.keys())
