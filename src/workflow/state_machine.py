"""Simple state machine for workflow execution tracking."""

from typing import Tuple
from typing import Any, Dict, List


class StateMachine:
    """Simple state machine to track workflow execution state."""

    def __init__(self, initial_state: str = "pending"):
        self._state = initial_state
        self._transition_history: List[Tuple[str, str, str]] = []
        self._variables: Dict[str, Any] = {}

    @property
    def state(self) -> str:
        return self._state

    def transition(self, target_state: str, triggered_by: str = "workflow") -> bool:
        """Transition to a new state and record the history."""
        valid_transitions = {
            "pending": ["running", "cancelled"],
            "running": ["completed", "failed", "cancelled"],
            "completed": [],  # terminal state
            "failed": [],  # terminal state
            "cancelled": [],  # terminal state
        }

        allowed = valid_transitions.get(self._state, [])
        if target_state in allowed:
            self._transition_history.append((self._state, target_state, triggered_by))
            self._state = target_state
            return True
        return False

    def can(self, target_state: str) -> bool:
        """Check if a transition to target_state is allowed from current state."""
        valid_transitions = {
            "pending": ["running", "cancelled"],
            "running": ["completed", "failed", "cancelled"],
            "completed": [],
            "failed": [],
            "cancelled": [],
        }
        return target_state in valid_transitions.get(self._state, [])

    def set_variable(self, key: str, value: Any) -> None:
        self._variables[key] = value

    def get_variable(self, key: str, default: Any = None) -> Any:
        return self._variables.get(key, default)

    def get_history(self) -> List[Tuple[str, str, str]]:
        return self._transition_history
