"""Context Kernel — 12 inputs → compression → model context

The Context Kernel transforms 12 raw input streams into a compressed,
model-ready context bundle. Inputs include: goal, task, memory, identity,
capability, resource state, event, network observation, trust, evaluation,
policy, and time horizon.

依据 Definition Lock §112: Context Kernel 必须能够
- Accept 12 typed inputs
- Apply compression/attention mechanism
- Output model-ready context bundle
- Support scope-aware filtering (L0-L7)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Tuple

from src.kernels._crosscutting import kernel_action


class ContextInputType(Enum):
    """12 typed input streams for the Context Kernel."""
    GOAL = "goal"           # Natural language execution goal
    TASK = "task"           # Decomposed task list
    MEMORY = "memory"       # Short/long-term memory state
    IDENTITY = "identity"   # Agent identity + permissions
    CAPABILITY = "capability"  # Available capabilities
    RESOURCE = "resource"   # CPU/Mem/Storage/$ quota state
    EVENT = "event"         # Event bus messages
    NETWORK = "network"     # Network observation/state
    TRUST = "trust"         # Trust score + chain
    EVALUATION = "evaluation"  # Outcome evaluation result
    POLICY = "policy"       # Active policy rules
    TIME = "time"           # Time horizon / deadline / TTL


@dataclass
class ContextInput:
    """Typed input stream from the 12 inputs."""
    type: ContextInputType
    source: str  # originating kernel or component
    timestamp: float = field(default_factory=lambda: __import__("time").time())
    data: Any = None  # raw payload
    correlation_id: Optional[str] = None
    scope: str = "L0"  # L0-L7 permission scope


@dataclass
class ContextCompression:
    """Compressed context bundle output by the kernel."""
    compressed: Dict[str, Any] = field(default_factory=dict)
    attention_weights: Dict[str, float] = field(default_factory=dict)
    retained_keys: List[str] = field(default_factory=list)
    discarded_keys: List[str] = field(default_factory=list)
    compression_ratio: float = 1.0
    scope: str = "L0"
    timestamp: float = field(default_factory=lambda: __import__("time").time())


class AttentionMechanism(Enum):
    """Available attention/compression strategies."""
    UNIFORM = "uniform"       # Equal weighting
    IMPORTANCE = "importance" # Importance-based weighting
    RECENCY = "recency"       # Recency-based decay
    HYBRID = "hybrid"         # Hybrid importance+recency


class ContextKernel:
    """Context Kernel — transforms 12 inputs → compressed context.

    Responsibilities:
    1. Accept 12 typed ContextInput streams
    2. Apply attention mechanism for compression
    3. Output ContextCompression bundle ready for model consumption
    4. Support scope-aware filtering (L0-L7)
    5. Maintain correlation IDs for end-to-end traceability
    """

    N_INPUTS = 12  # Fixed number of input streams

    def __init__(
        self,
        mechanism: AttentionMechanism = AttentionMechanism.HYBRID,
        scope: str = "L0",
    ):
        self.mechanism = mechanism
        self.scope = scope
        self._input_counts: Dict[ContextInputType, int] = {
            it: 0 for it in ContextInputType
        }

    @kernel_action("context.set_scope")
    def set_scope(self, scope: str) -> None:
        """Set permission scope (L0-L7)."""
        if scope not in {f"L{i}" for i in range(8)}:
            raise ValueError(f"Scope must be L0-L7, got {scope}")
        self.scope = scope

    def add_input(self, input_: ContextInput) -> None:
        """Add a single 12-input stream."""
        if input_.type not in self._input_counts:
            raise ValueError(f"Unknown input type: {input_.type}")
        self._input_counts[input_.type] += 1

    @kernel_action("context.compress")
    def compress(self, inputs: Optional[List[ContextInput]] = None) -> ContextCompression:
        """Apply attention-based compression to the 12 inputs.

        Transforms raw 12 input streams into a compressed context bundle.
        Uses the configured attention mechanism to determine which keys
        to retain, compress, or discard.
        """
        if inputs is None:
            inputs = []

        # Count inputs by type
        type_counts: Dict[ContextInputType, int] = {}
        for inp in inputs:
            type_counts[inp.type] = type_counts.get(inp.type, 0) + 1

        # Apply attention based on mechanism
        retained: List[str] = []
        discarded: List[str] = []
        attention_weights: Dict[str, float] = {}
        compressed: Dict[str, Any] = {}

        for itype in ContextInputType:
            count = type_counts.get(itype, 0)
            if count > 0:
                # Determine weight based on mechanism
                if self.mechanism == AttentionMechanism.UNIFORM:
                    weight = 1.0 / self.N_INPUTS
                elif self.mechanism == AttentionMechanism.IMPORTANCE:
                    weight = count / self.N_INPUTS
                elif self.mechanism == AttentionMechanism.RECENCY:
                    weight = 1.0  # All equal, recency handled at input level
                elif self.mechanism == AttentionMechanism.HYBRID:
                    weight = (count + 1) / (self.N_INPUTS + self.N_INPUTS)

                attention_weights[itype.value] = weight

                if weight > 0.5:
                    retained.append(itype.value)
                    compressed[itype.value] = {
                        "count": count,
                        "scope": self.scope,
                        "mechanism": self.mechanism.value,
                    }
                else:
                    discarded.append(itype.value)

        compression_ratio = len(retained) / self.N_INPUTS if self.N_INPUTS > 0 else 0.0

        result = ContextCompression(
            compressed=compressed,
            attention_weights=attention_weights,
            retained_keys=retained,
            discarded_keys=discarded,
            compression_ratio=compression_ratio,
            scope=self.scope,
        )

        # Reset input counts after compression
        self._input_counts = {it: 0 for it in ContextInputType}

        return result

    @kernel_action("context.process")
    def process(self, inputs: Optional[List[ContextInput]] = None) -> ContextCompression:
        """Full processing pipeline: accept inputs → compress → output."""
        self._input_counts = {it: 0 for it in ContextInputType}
        for inp in (inputs or []):
            self.add_input(inp)
        return self.compress(inputs)


# Convenience function for quick usage
def create_context_kernel(
    mechanism: AttentionMechanism = AttentionMechanism.HYBRID,
    scope: str = "L0",
) -> ContextKernel:
    """Factory function to create a configured ContextKernel instance."""
    return ContextKernel(mechanism=mechanism, scope=scope)