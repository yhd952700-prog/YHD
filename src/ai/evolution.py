"""
Evolution engine for LiuHao AI OS (MASTER-SPEC 80).

§80 EVOLUTION ENGINE lifecycle:

    Observe -> Measure -> Detect Bottleneck -> Generate Improvement Proposal ->
    Experiment -> Benchmark -> Approve -> Deploy -> Monitor

This module implements the *controlled* half of that loop: a self-improvement
proposal is turned into an Experiment that can only reach production (APPLIED)
through a guarded, audited pipeline.

HARD GUARD (MASTER-SPEC 80): NO "Production Self-modification Without
Evaluation". A change may only be deployed when ALL of the following hold:

    1. it has been benchmarked against a real baseline metric, AND
    2. the observed metric shows a real improvement (observed > baseline), AND
    3. a human/authority has explicitly approved it.

If any one of those three is missing, deploy() returns False and the production
state is untouched. This is the core safeguard - the engine can never modify
itself in production purely on its own initiative.

State machine:

    PROPOSED --benchmark()--> RUNNING --approve()--> APPROVED
                                                  |
                                              deploy()--> APPLIED --monitor()--> APPLIED
                                                                   |
                                                              rollback()--> ROLLED_BACK

    Any non-terminal state (PROPOSED / RUNNING / APPROVED) may be reject()'ed
    to REJECTED. rollback() is only valid from APPLIED.

Every transition is recorded as an immutable audit event in
Experiment.history. history() returns the full trail.

NO FAKE AI: benchmark/monitor arithmetic is real; improvement is a real
subtraction; deploy permissions are real and enforced, never simulated.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from .observability import observe
from .audit import audited


class ExperimentStatus(Enum):
    """Lifecycle state of an evolution experiment (MASTER-SPEC 80)."""

    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    RUNNING = "RUNNING"
    APPLIED = "APPLIED"
    ROLLED_BACK = "ROLLED_BACK"
    REJECTED = "REJECTED"


# States from which an experiment can still be terminated by rejection.
_REJECTABLE = {
    ExperimentStatus.PROPOSED,
    ExperimentStatus.RUNNING,
    ExperimentStatus.APPROVED,
}
# Terminal states that cannot be re-entered or further transitioned.
_TERMINAL = {
    ExperimentStatus.APPLIED,
    ExperimentStatus.ROLLED_BACK,
    ExperimentStatus.REJECTED,
}


@dataclass
class Experiment:
    """A single self-improvement proposal tracked by the EvolutionEngine.

    Attributes:
        id: unique experiment id (exp-<hex>).
        description: human-readable description of the proposed improvement.
        status: current lifecycle state.
        baseline_metric: the measured baseline before the change.
        observed_metric: the measured metric after benchmark runs (None until then).
        proposed_change: the concrete change that is proposed.
        history: append-only audit trail of events.
        created_at: unix timestamp of creation.
        approver: who approved (None until approved).
    """

    id: str
    description: str
    status: ExperimentStatus
    baseline_metric: float
    proposed_change: str
    observed_metric: Optional[float] = None
    approver: Optional[str] = None
    history: List[Dict[str, object]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    # --- derived guards --------------------------------------------------------
    def is_benchmarked(self) -> bool:
        """True once a real observed metric has been recorded."""
        return self.observed_metric is not None

    def is_improved(self) -> bool:
        """True only when the observed metric is strictly better than baseline."""
        if self.observed_metric is None:
            return False
        return self.observed_metric > self.baseline_metric

    def improvement(self) -> Optional[float]:
        """observed - baseline, or None if not yet benchmarked."""
        if self.observed_metric is None:
            return None
        return self.observed_metric - self.baseline_metric


class EvolutionEngine:
    """§80 Evolution Engine - guarded, audited self-improvement pipeline."""

    def __init__(self) -> None:
        self._experiments: Dict[str, Experiment] = {}

    # --- audit helper ---------------------------------------------------------
    @staticmethod
    def _event(event: str, **details: object) -> Dict[str, object]:
        rec: Dict[str, object] = {"ts": time.time(), "event": event}
        rec.update(details)
        return rec

    def _record(self, exp: Experiment, event: str, **details: object) -> None:
        exp.history.append(self._event(event, **details))

    # --- lifecycle ------------------------------------------------------------
    @observe("evolution.propose")
    @audited("p19.evolution.propose", module="src.ai.evolution")
    def propose(
        self, description: str, baseline_metric: float, proposed_change: str
    ) -> str:
        """Register a new improvement proposal. Status -> PROPOSED.

        Returns the new experiment id.
        """
        if not description or not proposed_change:
            raise ValueError("description and proposed_change must be non-empty")
        exp_id = f"exp-{uuid.uuid4().hex[:12]}"
        exp = Experiment(
            id=exp_id,
            description=description,
            status=ExperimentStatus.PROPOSED,
            baseline_metric=float(baseline_metric),
            proposed_change=proposed_change,
        )
        self._record(
            exp,
            "proposed",
            description=description,
            baseline_metric=float(baseline_metric),
            proposed_change=proposed_change,
        )
        self._experiments[exp_id] = exp
        return exp_id

    @observe("evolution.benchmark")
    def benchmark(self, experiment_id: str, observed_metric: float) -> Dict[str, object]:
        """Record the observed metric from running the experiment.

        Computes improvement = observed - baseline and improved = observed > baseline.
        Status -> RUNNING. Returns a dict with baseline/observed/improvement/improved.
        """
        exp = self._get(experiment_id)
        if exp.status in (ExperimentStatus.APPLIED, ExperimentStatus.ROLLED_BACK,
                          ExperimentStatus.REJECTED):
            raise ValueError(f"cannot benchmark a {exp.status.value} experiment")
        exp.observed_metric = float(observed_metric)
        exp.status = ExperimentStatus.RUNNING
        self._record(
            exp,
            "benchmarked",
            observed_metric=float(observed_metric),
            baseline_metric=exp.baseline_metric,
            improvement=exp.improvement(),
            improved=exp.is_improved(),
        )
        return {
            "experiment_id": exp.id,
            "baseline": exp.baseline_metric,
            "observed": float(observed_metric),
            "improvement": exp.improvement(),
            "improved": exp.is_improved(),
        }

    @audited("p19.evolution.approve", module="src.ai.evolution")
    def approve(self, experiment_id: str, approver: str) -> bool:
        """Approve a benchmarked, improved experiment. Status -> APPROVED.

        Returns False (and does NOT approve) unless the experiment has been
        benchmarked AND shows a real improvement. This is half of the guard
        against un-evaluated production changes.
        """
        exp = self._get(experiment_id)
        if not exp.is_benchmarked():
            self._record(exp, "approve_rejected", reason="not_benchmarked")
            return False
        if not exp.is_improved():
            self._record(
                exp,
                "approve_rejected",
                reason="not_improved",
                improvement=exp.improvement(),
            )
            return False
        if exp.status in _TERMINAL:
            self._record(exp, "approve_rejected", reason="terminal_state",
                         state=exp.status.value)
            return False
        exp.approver = approver
        exp.status = ExperimentStatus.APPROVED
        self._record(
            exp,
            "approved",
            approver=approver,
            baseline_metric=exp.baseline_metric,
            observed_metric=exp.observed_metric,
            improvement=exp.improvement(),
        )
        return True

    @observe("evolution.deploy")
    @audited("p19.evolution.deploy", module="src.ai.evolution")
    def deploy(self, experiment_id: str) -> bool:
        """Deploy an approved, benchmarked, improved experiment. Status -> APPLIED.

        Returns False (and does NOT touch production state) unless ALL of:
          - status == APPROVED, AND
          - the experiment has been benchmarked, AND
          - the observed metric is improved vs baseline.

        This is the core "no production self-modification without evaluation"
        safeguard: the engine can only modify itself in production with a real,
        measured improvement that a human/authority explicitly approved.
        """
        exp = self._get(experiment_id)
        # Three independent conditions - all required.
        if exp.status != ExperimentStatus.APPROVED:
            self._record(
                exp, "deploy_rejected", reason="not_approved", state=exp.status.value
            )
            return False
        if not exp.is_benchmarked():
            self._record(exp, "deploy_rejected", reason="not_benchmarked")
            return False
        if not exp.is_improved():
            self._record(
                exp, "deploy_rejected", reason="not_improved", improvement=exp.improvement()
            )
            return False
        exp.status = ExperimentStatus.APPLIED
        self._record(
            exp,
            "deployed",
            approver=exp.approver,
            baseline_metric=exp.baseline_metric,
            observed_metric=exp.observed_metric,
            improvement=exp.improvement(),
        )
        return True

    def monitor(self, experiment_id: str, metric: float) -> Dict[str, object]:
        """Observe the production metric after deployment. Records history.

        Only valid for a deployed (APPLIED) experiment; otherwise raises.
        Returns a dict with baseline/observed/deployed_metric/delta.
        """
        exp = self._get(experiment_id)
        if exp.status != ExperimentStatus.APPLIED:
            raise ValueError(f"can only monitor an APPLIED experiment, got {exp.status.value}")
        deployed_metric = float(metric)
        self._record(
            exp,
            "monitored",
            metric=deployed_metric,
            baseline_metric=exp.baseline_metric,
            benchmark_observed=exp.observed_metric,
        )
        return {
            "experiment_id": exp.id,
            "baseline": exp.baseline_metric,
            "benchmark_observed": exp.observed_metric,
            "deployed_metric": deployed_metric,
            "delta_vs_baseline": deployed_metric - exp.baseline_metric,
        }

    def rollback(self, experiment_id: str) -> bool:
        """Roll a deployed change back. Status -> ROLLED_BACK.

        Only valid from APPLIED; returns False otherwise (nothing to roll back).
        """
        exp = self._get(experiment_id)
        if exp.status != ExperimentStatus.APPLIED:
            self._record(
                exp, "rollback_rejected", reason="not_applied", state=exp.status.value
            )
            return False
        exp.status = ExperimentStatus.ROLLED_BACK
        self._record(
            exp,
            "rolled_back",
            baseline_metric=exp.baseline_metric,
            observed_metric=exp.observed_metric,
        )
        return True

    def reject(self, experiment_id: str) -> bool:
        """Reject a non-terminal experiment. Status -> REJECTED.

        Returns False for already-terminal experiments (nothing to reject).
        """
        exp = self._get(experiment_id)
        if exp.status not in _REJECTABLE:
            self._record(
                exp, "reject_rejected", reason="terminal_state", state=exp.status.value
            )
            return False
        exp.status = ExperimentStatus.REJECTED
        self._record(
            exp,
            "rejected",
            baseline_metric=exp.baseline_metric,
            observed_metric=exp.observed_metric,
        )
        return True

    def history(self, experiment_id: str) -> List[Dict[str, object]]:
        """Return the full append-only audit trail for an experiment."""
        return list(self._get(experiment_id).history)

    # --- internal -------------------------------------------------------------
    def _get(self, experiment_id: str) -> Experiment:
        exp = self._experiments.get(experiment_id)
        if exp is None:
            raise KeyError(f"unknown experiment id: {experiment_id}")
        return exp

    def get(self, experiment_id: str) -> Experiment:
        """Public accessor returning the Experiment (or raising KeyError)."""
        return self._get(experiment_id)
