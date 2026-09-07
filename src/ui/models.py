"""Model center for the product console."""

from dataclasses import dataclass, field
from typing import Dict, Any, Optional
from enum import Enum


class ModelStatus(Enum):
    """Model availability status."""
    AVAILABLE = "available"
    BUSY = "busy"
    OFFLINE = "offline"
    LOADING = "loading"


@dataclass
class ModelCenter:
    """Center for managing LLM models in the product console."""

    models: Dict[str, Any] = field(default_factory=dict)
    model_statuses: Dict[str, ModelStatus] = field(default_factory=dict)
    default_model: Optional[str] = None

    def register_model(self, model_name: str, model_config: Any = None, status: ModelStatus = ModelStatus.AVAILABLE) -> None:
        """Register a model with the center."""
        self.models[model_name] = model_config
        self.model_statuses[model_name] = status

    def set_default_model(self, model_name: str) -> None:
        """Set the default model."""
        self.default_model = model_name
        if model_name in self.model_statuses:
            self.model_statuses[model_name] = ModelStatus.AVAILABLE

    def get_model_status(self, model_name: str) -> Optional[ModelStatus]:
        """Get model status."""
        return self.model_statuses.get(model_name)

    def get_all_models(self) -> Dict[str, Any]:
        """Get all registered models."""
        return self.models

    def to_dict(self) -> Dict[str, Any]:
        """Convert model center to dictionary for UI rendering."""
        return {
            "models": {k: {"status": v.value if hasattr(v, 'value') else str(v)} for k, v in self.model_statuses.items()},
            "default_model": self.default_model,
            "total_models": len(self.models),
        }
