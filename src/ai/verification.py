"""Verification & Experience Engine — MASTER-SPEC Phase 18 (S78-79).

Implements the two Phase 18 capabilities on top of existing kernels:

S78 Verification Engine
    Maps an execution ``result`` (optionally against ``criteria``) onto one of
    four verdicts:

        VERIFIED  - outcome fully satisfies the (implicit or explicit) goal
        PARTIAL   - outcome mostly satisfies the goal (ratio >= 0.7)
        FAILED    - outcome does not satisfy the goal (ratio < 0.7)
        UNKNOWN   - cannot be determined (no criteria + ambiguous result)

    The matching itself is NOT reimplemented: it reuses the real
    ``Verifier`` from ``src.kernels.execution`` (Execution Kernel), so the
    four-state verdict is a faithful projection of the kernel's
    ``VerifyResult`` / match-score logic onto S78's vocabulary.

S79 Experience Engine
    Extracts an ``ExperienceEntry`` from a verified outcome and persists it
    into the real ``MemoryKernel`` (``src.kernels.memory``) — never an
    in-memory fake. Entries are tagged (including an ``owner:`` tag) so they
    can be retrieved and re-applied to future missions.

Per S158 (NO FAKE): the experience layer performs genuine store/recall against
the Memory Kernel. The kernel is injected so tests can use a fresh, isolated
instance while still exercising the real kernel code path.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from ..kernels.execution import ActionResult, Task, Verifier
from ..kernels.memory import MemoryKernel, MemoryScope, MemoryTier, get_memory_kernel
from .observability import observe


# ---------------------------------------------------------------------------
# S78 Verdict
# ---------------------------------------------------------------------------
class Verdict(str, Enum):
    """Four-state verification verdict (S78)."""

    VERIFIED = "verified"
    PARTIAL = "partial"
    FAILED = "failed"
    UNKNOWN = "unknown"


# Thresholds (mirror the kernel's partial/off_track cut points).
_PARTIAL_THRESHOLD = 0.7


class VerificationEngine:
    """Maps a result onto a four-state verdict, reusing the Execution Kernel."""

    def __init__(self, verifier: Optional[Verifier] = None):
        # Reuse the real kernel verifier rather than reimplementing matching.
        self._verifier = verifier or Verifier()

    def _result_success_flag(self, result: Dict[str, Any]) -> Optional[bool]:
        """Extract an explicit success boolean from a result dict.

        Returns True / False when the result clearly states success, or None
        when it is ambiguous (used to decide UNKNOWN).
        """
        if not isinstance(result, dict):
            return None
        if "success" in result:
            val = result["success"]
            if isinstance(val, bool):
                return val
            # Truthy/falsy coercion for non-bool values.
            return bool(val)
        if "failed" in result:
            return not bool(result["failed"])
        return None

    @observe("verification.verify")
    def verify(self, result: Dict[str, Any], criteria: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Verify ``result`` against optional ``criteria``.

        Returns a dict: ``{"verdict", "score", "reasons"}``.

        Mapping rules
        -------------
        * No criteria + clearly successful result  -> VERIFIED
        * No criteria + clearly failed result      -> FAILED
        * No criteria + ambiguous result           -> UNKNOWN
        * Criteria provided                        -> ratio -> VERIFIED /
          PARTIAL / FAILED based on the kernel match score
        """
        reasons: List[str] = []

        # --- No criteria: rely on the result's own success signal. ---
        if not criteria:
            flag = self._result_success_flag(result)
            if flag is True:
                reasons.append("no criteria: result marked successful -> VERIFIED")
                return {"verdict": Verdict.VERIFIED, "score": 1.0, "reasons": reasons}
            if flag is False:
                reasons.append("no criteria: result marked failed -> FAILED")
                return {"verdict": Verdict.FAILED, "score": 0.0, "reasons": reasons}
            # Neither clearly success nor clearly failed and nothing to match.
            reasons.append("no criteria and result success is ambiguous -> UNKNOWN")
            return {"verdict": Verdict.UNKNOWN, "score": 0.0, "reasons": reasons}

        # --- Criteria provided: reuse the kernel Verifier for real matching. ---
        output = result.get("output", result) if isinstance(result, dict) else result
        success = self._result_success_flag(result)
        # A result that explicitly failed is FAILED regardless of criteria match.
        if success is False:
            reasons.append("criteria provided but result marked failed -> FAILED")
            return {"verdict": Verdict.FAILED, "score": 0.0, "reasons": reasons}

        # Build minimal kernel objects so we delegate matching to the kernel.
        task = Task(
            id="verify",
            goal_id="verify",
            name="verify",
            description="verification",
            capability_id="verify",
        )
        action_result = ActionResult(
            action_id="verify",
            success=True,
            output=output,
        )
        vresult = self._verifier.verify(task, action_result, criteria)

        score = float(vresult.score)
        if score >= 1.0:
            verdict = Verdict.VERIFIED
        elif score >= _PARTIAL_THRESHOLD:
            verdict = Verdict.PARTIAL
        else:
            verdict = Verdict.FAILED

        reasons.append(
            f"criteria matched {int(round(score * len(criteria)))}/{len(criteria)} "
            f"(score={score:.2f}) -> {verdict.value}"
        )
        if vresult.details:
            for key, detail in vresult.details.items():
                if detail != "match":
                    reasons.append(f"{key}: {detail}")

        return {"verdict": verdict, "score": score, "reasons": reasons}


# ---------------------------------------------------------------------------
# S79 Experience Engine
# ---------------------------------------------------------------------------
@dataclass
class ExperienceEntry:
    """A single learned experience extracted from a verified outcome (S79)."""

    id: str
    summary: str
    verdict: str
    context: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    owner: str = "system"

    def to_payload(self) -> Dict[str, Any]:
        """Serialize to a JSON-friendly dict for the memory kernel."""
        return {
            "__type__": "experience",
            "id": self.id,
            "summary": self.summary,
            "verdict": self.verdict,
            "context": self.context,
            "tags": list(self.tags),
            "owner": self.owner,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "ExperienceEntry":
        """Reconstruct an ExperienceEntry from a memory-kernel payload."""
        return cls(
            id=payload["id"],
            summary=payload["summary"],
            verdict=payload["verdict"],
            context=payload.get("context", {}),
            tags=list(payload.get("tags", [])),
            created_at=datetime.fromisoformat(payload["created_at"]),
            owner=payload.get("owner", "system"),
        )


class ExperienceEngine:
    """Extracts and persists experiences via the real Memory Kernel (S79)."""

    # Tag automatically applied to every stored experience so retrieval can
    # distinguish experience entries from other memory content.
    _TYPE_TAG = "experience"

    def __init__(self, memory_kernel: Optional[MemoryKernel] = None):
        # Real kernel; defaults to the global singleton but is injectable for
        # isolated, deterministic tests. Either way store/recall go through
        # the actual MemoryKernel implementation (never a fake dict).
        self._memory = memory_kernel or get_memory_kernel()

    @observe("experience.extract")
    def extract(
        self, verdict_result: Dict[str, Any], context: Dict[str, Any]
    ) -> ExperienceEntry:
        """Extract an ExperienceEntry from a verification result + context."""
        verdict = verdict_result.get("verdict")
        if isinstance(verdict, Verdict):
            verdict = verdict.value
        summary = context.get("summary") or f"verdict={verdict}"
        return ExperienceEntry(
            id=context.get("id") or f"exp-{uuid.uuid4().hex[:12]}",
            summary=summary,
            verdict=verdict if verdict is not None else Verdict.UNKNOWN.value,
            context=context,
            tags=list(context.get("tags", [])),
            created_at=datetime.now(timezone.utc),
            owner=context.get("owner", "system"),
        )

    @observe("experience.store")
    def store(self, entry: ExperienceEntry, owner: Optional[str] = None, tags: Optional[List[str]] = None) -> bool:
        """Persist an experience to the memory kernel.

        The entry is tagged with its own tags plus a derived ``owner:<name>``
        tag and the ``experience`` type tag. Returns True on successful store.
        """
        owner = owner or entry.owner or "system"
        merged_tags = set(entry.tags)
        if tags:
            merged_tags.update(tags)
        merged_tags.add(self._TYPE_TAG)
        merged_tags.add(f"owner:{owner}")

        # Persist the full tag set (incl. owner:/experience) in the payload so
        # a later retrieve() reconstructs the complete tag set, not just the
        # entry's own tags.
        entry.tags = sorted(merged_tags)
        self._memory.store(
            key=f"experience:{entry.id}",
            value=entry.to_payload(),
            tier=MemoryTier.LONG_TERM,
            scope=MemoryScope.L1,
            tags=merged_tags,
        )
        return True

    def retrieve(self, tags: Optional[List[str]] = None) -> List[ExperienceEntry]:
        """Retrieve experiences, optionally filtered by tag (subset match).

        Reads the real memory kernel via ``scope_filter`` (L0 = all scopes) and
        deserializes every entry tagged as an experience. When ``tags`` are
        supplied, only entries whose tags are a superset are returned.
        """
        wanted = set(tags) if tags else set()
        entries = self._memory.scope_filter(MemoryScope.L0)
        results: List[ExperienceEntry] = []
        for entry in entries:
            value = entry.value
            if not isinstance(value, dict) or value.get("__type__") != "experience":
                continue
            if wanted and not wanted.issubset(entry.tags):
                continue
            results.append(ExperienceEntry.from_payload(value))
        # Newest first.
        results.sort(key=lambda e: e.created_at, reverse=True)
        return results


__all__ = [
    "Verdict",
    "VerificationEngine",
    "ExperienceEntry",
    "ExperienceEngine",
]
