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
from src.kernels._base import KernelLifecycle, KernelStateError

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

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
    #: correlation ids of the inputs that produced this bundle, de-duplicated and
    #: in input order. Carried through so a caller can trace the bundle back to
    #: the event(s) that fed it (the class contract promises end-to-end
    #: traceability; the inputs' ids used to be dropped on the floor).
    correlation_ids: List[str] = field(default_factory=list)


class AttentionMechanism(Enum):
    """Available attention/compression strategies."""
    UNIFORM = "uniform"       # Equal weighting
    IMPORTANCE = "importance"  # Importance-based weighting
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
    lifecycle: KernelLifecycle = KernelLifecycle.UNINITIALIZED

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

        # Payloads per type + the inputs' correlation ids. The bundle is meant to
        # be *model-ready* and end-to-end traceable, so it must carry the actual
        # data and ids -- previously only ``count``/``scope``/``mechanism`` were
        # emitted and every ``inp.data`` / ``inp.correlation_id`` was dropped.
        payloads: Dict[ContextInputType, List[Any]] = {}
        correlation_ids: List[str] = []
        for inp in inputs:
            payloads.setdefault(inp.type, []).append(inp.data)
            cid = inp.correlation_id
            if cid is not None and cid not in correlation_ids:
                correlation_ids.append(cid)

        # Apply attention based on mechanism.
        #
        # Retention used to be decided by an ABSOLUTE threshold (weight > 0.5),
        # which is NOT scale-invariant: the weight's magnitude depends on
        # N_INPUTS and on the raw counts, so for the real N_INPUTS=12 the
        # default HYBRID/UNIFORM/IMPORTANCE mechanisms discarded *every* present
        # type — a silent runtime no-op. We now make the decision RELATIVE:
        #  1) compute a per-mechanism raw weight,
        #  2) normalize the present types into a distribution summing to 1.0,
        #  3) retain types strictly above the mean weight (scale-invariant),
        #  4) never emit an empty retained set — if nothing beats the mean
        #     (e.g. all weights equal under UNIFORM/RECENCY) keep the
        #     highest-weight type(s); ties keep every tied type, so the result
        #     is deterministic and order-independent.
        retained: List[str] = []
        discarded: List[str] = []
        attention_weights: Dict[str, float] = {}
        compressed: Dict[str, Any] = {}

        present: List[ContextInputType] = [
            it for it in ContextInputType if type_counts.get(it, 0) > 0
        ]

        if present:
            # 1) raw attention weight per present type (mechanism-specific shape)
            raw_weights: Dict[ContextInputType, float] = {}
            for itype in present:
                count = type_counts[itype]
                if self.mechanism == AttentionMechanism.UNIFORM:
                    weight = 1.0
                elif self.mechanism == AttentionMechanism.IMPORTANCE:
                    weight = float(count)
                elif self.mechanism == AttentionMechanism.RECENCY:
                    weight = 1.0  # all equal; recency handled at input level
                elif self.mechanism == AttentionMechanism.HYBRID:
                    weight = float(count + 1)
                else:  # pragma: no cover - AttentionMechanism is an exhaustive enum
                    weight = 1.0
                raw_weights[itype] = weight

            # 2) normalize into a distribution summing to 1.0
            total = sum(raw_weights.values())
            norm_weights = {t: w / total for t, w in raw_weights.items()}
            # scale-invariance guarantee: normalized weights must sum to 1
            assert abs(sum(norm_weights.values()) - 1.0) < 1e-9, (
                "attention weights must normalize to a distribution summing to 1"
            )

            # 3) relative retention: keep types strictly above the mean weight
            mean_norm = 1.0 / len(present)
            keep: set = {t for t, w in norm_weights.items() if w > mean_norm}
            # 4) never emit an empty retained set (deterministic tie handling)
            if not keep:
                max_w = max(raw_weights.values())
                keep = {t for t, w in raw_weights.items() if w == max_w}

            # deterministic ordering: descending weight, then enum declaration order
            ordered = sorted(
                present,
                key=lambda t: (-norm_weights[t], list(ContextInputType).index(t)),
            )
            for itype in ordered:
                attention_weights[itype.value] = norm_weights[itype]
                if itype in keep:
                    retained.append(itype.value)
                    compressed[itype.value] = {
                        "count": type_counts[itype],
                        "scope": self.scope,
                        "mechanism": self.mechanism.value,
                        "data": payloads.get(itype, []),
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
            correlation_ids=correlation_ids,
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

    def initialize(self) -> None:
        self.lifecycle = KernelLifecycle.READY

    def shutdown(self) -> None:
        self.lifecycle = KernelLifecycle.STOPPED

    def pause(self) -> None:
        if self.lifecycle not in (KernelLifecycle.READY, KernelLifecycle.UNINITIALIZED):
            raise KernelStateError(f"cannot pause from {self.lifecycle}")
        self.lifecycle = KernelLifecycle.PAUSED

    def resume(self) -> None:
        if self.lifecycle is not KernelLifecycle.PAUSED:
            raise KernelStateError(f"cannot resume from {self.lifecycle}")
        self.lifecycle = KernelLifecycle.READY


# Convenience function for quick usage
def create_context_kernel(
    mechanism: AttentionMechanism = AttentionMechanism.HYBRID,
    scope: str = "L0",
) -> ContextKernel:
    """Factory function to create a configured ContextKernel instance."""
    return ContextKernel(mechanism=mechanism, scope=scope)
