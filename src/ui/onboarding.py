"""Onboarding and demo flow for the product console."""

from dataclasses import dataclass, field
from typing import Dict, Any, List
from enum import Enum


class OnboardingStep(Enum):
    """Onboarding flow steps."""
    WELCOME = "welcome"
    OVERVIEW = "overview"
    FEATURES = "features"
    SETUP = "setup"
    COMPLETE = "complete"


@dataclass
class OnboardingWizard:
    """Wizard for product onboarding and demo flows."""

    current_step: OnboardingStep = OnboardingStep.WELCOME
    completed_steps: List[OnboardingStep] = field(default_factory=list)
    user_profile: Dict[str, Any] = field(default_factory=dict)
    demo_data: Dict[str, Any] = field(default_factory=dict)

    def advance_step(self) -> OnboardingStep:
        """Advance to the next onboarding step."""
        steps = list(OnboardingStep)
        current_index = steps.index(self.current_step)
        if current_index < len(steps) - 1:
            self.current_step = steps[current_index + 1]
        return self.current_step

    def complete_step(self, step: OnboardingStep) -> None:
        """Mark a step as completed."""
        if step not in self.completed_steps:
            self.completed_steps.append(step)

    def reset(self) -> None:
        """Reset the onboarding state."""
        self.current_step = OnboardingStep.WELCOME
        self.completed_steps = []
        self.user_profile = {}
        self.demo_data = {}

    def to_dict(self) -> Dict[str, Any]:
        """Convert onboarding wizard to dictionary for UI rendering."""
        return {
            "current_step": self.current_step.value,
            "completed_steps": [s.value for s in self.completed_steps],
            "total_steps": len(OnboardingStep),
            "progress": len(self.completed_steps) / len(OnboardingStep) if len(OnboardingStep) > 0 else 0,
        }


@dataclass
class DemoFlow:
    """Demo flow configuration and execution state."""

    flow_name: str = "default_demo"
    is_running: bool = False
    current_step: int = 0
    total_steps: int = 5
    captured_state: Dict[str, Any] = field(default_factory=dict)

    def start(self) -> None:
        """Start the demo flow."""
        self.is_running = True
        self.current_step = 0

    def advance(self) -> None:
        """Advance to the next demo step."""
        if self.is_running:
            self.current_step += 1

    def stop(self) -> None:
        """Stop the demo flow."""
        self.is_running = False

    def to_dict(self) -> Dict[str, Any]:
        """Convert demo flow to dictionary for UI rendering."""
        return {
            "flow_name": self.flow_name,
            "is_running": self.is_running,
            "current_step": self.current_step,
            "total_steps": self.total_steps,
            "progress": self.current_step / self.total_steps if self.total_steps > 0 else 0,
        }
