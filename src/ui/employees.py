"""Employee/agent center for the product console."""

from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from enum import Enum
from datetime import datetime


class AgentStatus(Enum):
    """Agent/employee status states."""
    ACTIVE = "active"
    IDLE = "idle"
    BUSY = "busy"
    OFFLINE = "offline"


@dataclass
class AgentCard:
    """Card displaying agent/employee information."""
    
    agent_id: str
    agent_type: str = "unknown"
    status: AgentStatus = AgentStatus.IDLE
    current_task: str = ""
    model_preferences: Dict[str, Any] = field(default_factory=dict)
    monthly_uptime: float = 0.0
    avg_response_time: float = 0.0
    last_activity: str = field(default_factory=lambda: datetime.now().isoformat())
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert card to dictionary for UI rendering."""
        return {
            "agent_id": self.agent_id,
            "agent_type": self.agent_type,
            "status": self.status.value,
            "current_task": self.current_task,
            "monthly_uptime": round(self.monthly_uptime, 2),
            "avg_response_time": round(self.avg_response_time, 2),
            "last_activity": self.last_activity,
        }


@dataclass
class AIEmployeeCenter:
    """Center for managing AI employees/agents."""
    
    agents: Dict[str, AgentCard] = field(default_factory=dict)
    agent_types: Dict[str, int] = field(default_factory=lambda: {"total": 0, "active": 0, "idle": 0, "busy": 0, "offline": 0})
    
    def add_agent(self, card: AgentCard) -> None:
        """Add an agent card."""
        self.agents[card.agent_id] = card
        self._update_counts()
    
    def remove_agent(self, agent_id: str) -> None:
        """Remove an agent card."""
        if agent_id in self.agents:
            del self.agents[agent_id]
            self._update_counts()
    
    def get_agent(self, agent_id: str) -> Optional[AgentCard]:
        """Get an agent card by ID."""
        return self.agents.get(agent_id)
    
    def _update_counts(self) -> None:
        """Update agent type counts."""
        total = len(self.agents)
        active = sum(1 for a in self.agents.values() if a.status == AgentStatus.ACTIVE)
        idle = sum(1 for a in self.agents.values() if a.status == AgentStatus.IDLE)
        busy = sum(1 for a in self.agents.values() if a.status == AgentStatus.BUSY)
        offline = sum(1 for a in self.agents.values() if a.status == AgentStatus.OFFLINE)
        self.agent_types = {
            "total": total,
            "active": active,
            "idle": idle,
            "busy": busy,
            "offline": offline,
        }
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert employee center to dictionary for UI rendering."""
        return {
            "agents": {k: v.to_dict() for k, v in self.agents.items()},
            "agent_types": self.agent_types,
            "total": self.agent_types["total"],
        }