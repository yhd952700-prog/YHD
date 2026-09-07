"""CEO Dashboard and system status cards for the product console."""

from dataclasses import dataclass, field
from typing import Dict, Any, List
from enum import Enum
from datetime import datetime


class CardType(Enum):
    """Dashboard card types."""
    SYSTEM_STATUS = "system_status"
    AI_WORKER = "ai_worker"
    RISK = "risk"
    BUSINESS_OVERVIEW = "business_overview"


@dataclass
class SystemStatusCard:
    """Card displaying system health and status."""

    system_name: str
    status: str = "healthy"
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    active_workers: int = 0
    last_check: str = field(default_factory=lambda: datetime.now().isoformat())
    alerts: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert card to dictionary for UI rendering."""
        return {
            "system_name": self.system_name,
            "status": self.status,
            "cpu_percent": self.cpu_percent,
            "memory_percent": self.memory_percent,
            "active_workers": self.active_workers,
            "last_check": self.last_check,
            "alerts": self.alerts,
        }


@dataclass
class AIWorkerCard:
    """Card displaying AI worker status."""

    worker_id: str
    model_name: str = "unknown"
    status: str = "idle"
    active_sessions: int = 0
    monthly_tokens: int = 0
    monthly_cost: float = 0.0
    last_activity: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        """Convert card to dictionary for UI rendering."""
        return {
            "worker_id": self.worker_id,
            "model_name": self.model_name,
            "status": self.status,
            "active_sessions": self.active_sessions,
            "monthly_tokens": self.monthly_tokens,
            "monthly_cost": round(self.monthly_cost, 4),
            "last_activity": self.last_activity,
        }


@dataclass
class CEODashboard:
    """CEO dashboard with high-level business and system overview."""

    system_cards: Dict[str, SystemStatusCard] = field(default_factory=dict)
    ai_worker_cards: Dict[str, AIWorkerCard] = field(default_factory=dict)
    risk_score: float = 0.0
    overall_health: str = "excellent"
    business_kpis: Dict[str, Any] = field(default_factory=dict)
    last_updated: str = field(default_factory=lambda: datetime.now().isoformat())

    def add_system_card(self, card: SystemStatusCard) -> None:
        """Add a system status card."""
        self.system_cards[card.system_name] = card

    def add_ai_worker_card(self, card: AIWorkerCard) -> None:
        """Add an AI worker card."""
        self.ai_worker_cards[card.worker_id] = card

    def to_dict(self) -> Dict[str, Any]:
        """Convert dashboard to dictionary for UI rendering."""
        return {
            "risk_score": self.risk_score,
            "overall_health": self.overall_health,
            "system_cards": {k: v.to_dict() for k, v in self.system_cards.items()},
            "ai_worker_cards": {k: v.to_dict() for k, v in self.ai_worker_cards.items()},
            "business_kpis": self.business_kpis,
            "last_updated": self.last_updated,
        }
