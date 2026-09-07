"""Scaling module for Phase 6 SRE."""

from dataclasses import dataclass, field
from typing import Dict, Any, Optional
from enum import Enum
import time


class ResourceType(Enum):
    """Resource types for scaling decisions."""
    CPU = "cpu"
    MEMORY = "memory"
    TASK_QUEUE = "task_queue"
    WORKER_LOAD = "worker_load"
    LLM_REQUEST = "llm_request"


@dataclass
class ResourceSample:
    """A resource sample from the monitor."""
    resource_type: ResourceType
    value: float
    timestamp: float = field(default_factory=lambda: time.time())


class ScalingDecision(Enum):
    """Scaling decision types."""
    SCALE_UP = "scale_up"
    SCALE_DOWN = "scale_down"
    MAINTAIN = "maintain"


@dataclass
class ScalingPolicy:
    """A scaling policy that decides when to scale."""
    
    name: str = "default"
    cpu_threshold: float = 80.0
    memory_threshold: float = 80.0
    task_queue_threshold: float = 70.0
    worker_load_threshold: float = 80.0
    llm_request_threshold: float = 90.0
    
    def decide(self, samples: Dict[ResourceType, float]) -> ScalingDecision:
        """Decide scaling action based on resource samples."""
        # Check CPU threshold
        if ResourceType.CPU in samples and samples[ResourceType.CPU] > self.cpu_threshold:
            return ScalingDecision.SCALE_UP
        
        # Check memory threshold
        if ResourceType.MEMORY in samples and samples[ResourceType.MEMORY] > self.memory_threshold:
            return ScalingDecision.SCALE_UP
        
        # Check task queue threshold
        if ResourceType.TASK_QUEUE in samples and samples[ResourceType.TASK_QUEUE] > self.task_queue_threshold:
            return ScalingDecision.SCALE_UP
        
        # Check worker load threshold
        if ResourceType.WORKER_LOAD in samples and samples[ResourceType.WORKER_LOAD] > self.worker_load_threshold:
            return ScalingDecision.SCALE_UP
        
        # Check LLM request load threshold
        if ResourceType.LLM_REQUEST in samples and samples[ResourceType.LLM_REQUEST] > self.llm_request_threshold:
            return ScalingDecision.SCALE_UP
        
        return ScalingDecision.MAINTAIN


@dataclass
class CapacityPlanner:
    """Plans capacity based on pressure and capacity classification."""
    
    policy: ScalingPolicy = field(default_factory=ScalingPolicy)
    
    def plan(self, samples: Dict[ResourceType, float]) -> Dict[str, Any]:
        """Plan capacity based on resource samples."""
        decision = self.policy.decide(samples)
        
        return {
            "decision": decision.value,
            "policy_name": self.policy.name,
            "samples": {k: v for k, v in samples.items()},
            "thresholds": {
                "cpu": self.policy.cpu_threshold,
                "memory": self.policy.memory_threshold,
                "task_queue": self.policy.task_queue_threshold,
                "worker_load": self.policy.worker_load_threshold,
                "llm_request": self.policy.llm_request_threshold,
            },
        }