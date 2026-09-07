"""Governance Layer — MASTER-SPEC Phase 16 (§70-73, §81-82).

This module sits on top of the existing Security and Trust kernels and adds
the *governance* surface that the master spec requires:

§70 Trust Engine       -> ReputationEngine (reputation ranking + delegation rec)
§71 Threat Model       -> ThreatCategory / ThreatFinding / ThreatDetector
§72 Security Chain     -> SecurityChain (detect -> block -> audit)
§82 Emergency Control  -> EmergencyControl (kill-switch)

Design rule (NO FAKE):
- Threat detection is a set of transparent, deterministic, regex/structure
  heuristics. There is NO machine-learning claim; every finding carries the
  concrete evidence string that triggered it so tests can assert behaviour.
- We never re-implement RBAC/ABAC or trust scoring. The Security Kernel and
  the Trust Kernel remain the single source of truth; this layer only
  orchestrates and interprets them.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from src.kernels.security import get_security_engine
from src.kernels.trust import get_trust_manager, TrustScope


# ---------------------------------------------------------------------------
# §71 Threat Model
# ---------------------------------------------------------------------------
class ThreatCategory(str, Enum):
    """The eleven threat categories mandated by MASTER-SPEC §71."""

    PROMPT_INJECTION = "prompt_injection"
    TOOL_ABUSE = "tool_abuse"
    CREDENTIAL_THEFT = "credential_theft"
    PRIVILEGE_ESCALATION = "privilege_escalation"
    DATA_EXFILTRATION = "data_exfiltration"
    MALICIOUS_AGENT = "malicious_agent"
    UNAUTHORIZED_DELEGATION = "unauthorized_delegation"
    SUPPLY_CHAIN = "supply_chain"
    MEMORY_LEAKAGE = "memory_leakage"
    POLICY_BYPASS = "policy_bypass"
    NETWORK_ABUSE = "network_abuse"


class Severity(str, Enum):
    """Finding severity levels. HIGH/CRITICAL findings cause a block."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# Categories whose findings are severe enough to hard-block a request.
_BLOCKING_SEVERITIES = frozenset({Severity.HIGH, Severity.CRITICAL})

# Per-category default severity. Deterministic and reviewable.
_CATEGORY_SEVERITY: Dict[ThreatCategory, Severity] = {
    ThreatCategory.PROMPT_INJECTION: Severity.CRITICAL,
    ThreatCategory.TOOL_ABUSE: Severity.MEDIUM,
    ThreatCategory.CREDENTIAL_THEFT: Severity.HIGH,
    ThreatCategory.PRIVILEGE_ESCALATION: Severity.HIGH,
    ThreatCategory.DATA_EXFILTRATION: Severity.CRITICAL,
    ThreatCategory.MALICIOUS_AGENT: Severity.HIGH,
    ThreatCategory.UNAUTHORIZED_DELEGATION: Severity.MEDIUM,
    ThreatCategory.SUPPLY_CHAIN: Severity.HIGH,
    ThreatCategory.MEMORY_LEAKAGE: Severity.MEDIUM,
    ThreatCategory.POLICY_BYPASS: Severity.HIGH,
    ThreatCategory.NETWORK_ABUSE: Severity.HIGH,
}


@dataclass
class ThreatFinding:
    """A single detected threat. ``evidence`` is the concrete trigger so the
    detection rule is always auditable (NO FAKE)."""

    category: ThreatCategory
    severity: Severity
    detail: str
    evidence: str


# MemoryScope tier levels used for privilege-escalation comparison (L0-L7).
_SCOPE_LEVEL = {f"L{i}": i for i in range(8)}


def _is_valid_scope(scope: Any) -> bool:
    return isinstance(scope, str) and scope in _SCOPE_LEVEL


class ThreatDetector:
    """Deterministic, transparent threat scanner (§71).

    The scanner combines:
    * keyword/regex heuristics over the request's textual fields, and
    * structural checks (privilege-escalation scope comparison,
      unauthorized-delegation flag).

    No model is involved; every rule emits the matching evidence text.
    """

    # category -> list of compiled regexes (case-insensitive)
    _PATTERNS: Dict[ThreatCategory, List[re.Pattern[str]]] = {
        ThreatCategory.PROMPT_INJECTION: [
            re.compile(r"ignore (all )?(previous|prior|above) (instructions|prompt|messages)"),
            re.compile(r"disregard (the )?(system|previous) (prompt|instructions)"),
            re.compile(r"jailbreak"),
            re.compile(r"developer mode"),
            re.compile(r"\bDAN mode\b"),
            re.compile(r"do anything now"),
        ],
        ThreatCategory.TOOL_ABUSE: [
            re.compile(r"rm -rf"),
            re.compile(r"\bos\.system\b"),
            re.compile(r"\bsubprocess\b"),
            re.compile(r"\beval\("),
            re.compile(r"\bexec\("),
            re.compile(r"drop table"),
            re.compile(r"truncate table"),
            re.compile(r"delete (all|from) (records|database|table)"),
        ],
        ThreatCategory.CREDENTIAL_THEFT: [
            re.compile(r"reveal (the )?(password|api[_ ]?key|secret|token|credential)"),
            re.compile(r"(print|return|dump) (the )?(password|secret|api[_ ]?key|token)"),
            re.compile(r"\.env (file|contents)"),
            re.compile(r"private key"),
        ],
        ThreatCategory.DATA_EXFILTRATION: [
            re.compile(r"exfiltrate"),
            re.compile(r"leak (the )?(data|database)"),
            re.compile(r"export (all )?(users|records|data) to (external|remote)"),
            re.compile(r"(upload|send|post) (all |the )?(data|records|users) (to|at) external"),
        ],
        ThreatCategory.MALICIOUS_AGENT: [
            re.compile(r"self-?propagat"),
            re.compile(r"recursive(ly)? (spawn|fork)"),
            re.compile(r"fork ?bomb"),
            re.compile(r"take over (the )?(system|network|agents?)"),
            re.compile(r"infect (other|the) (agents|systems?)"),
        ],
        ThreatCategory.SUPPLY_CHAIN: [
            re.compile(r"pip(3)? install"),
            re.compile(r"npm install"),
            re.compile(r"curl .* \| ?(bash|sh)"),
            re.compile(r"apt(-get)? install"),
            re.compile(r"go get "),
        ],
        ThreatCategory.MEMORY_LEAKAGE: [
            re.compile(r"dump (the )?(memory|context)"),
            re.compile(r"reveal (the )?(full )?(memory|context|history)"),
            re.compile(r"print (all )?(memory|history)"),
            re.compile(r"export (the )?memory"),
        ],
        ThreatCategory.POLICY_BYPASS: [
            re.compile(r"bypass (the )?(policy|guardrail|guard)"),
            re.compile(r"ignore (the )?(policy|guardrail|rule)"),
            re.compile(r"disable (the )?(guardrail|policy|safety)"),
            re.compile(r"circumvent (the )?(policy|control)"),
            re.compile(r"skip (the )?(safety|security) checks"),
        ],
        ThreatCategory.NETWORK_ABUSE: [
            re.compile(r"port scan"),
            re.compile(r"\bnmap\b"),
            re.compile(r"ddos"),
            re.compile(r"(flood|brute[- ]force) (the )?(network|server|login)"),
            re.compile(r"scan (all )?ports"),
        ],
    }

    def scan(self, request: dict) -> List[ThreatFinding]:
        """Scan *request* and return all detected threats (transparent rules)."""
        findings: List[ThreatFinding] = []

        blob = self._extract_text(request)
        for category, patterns in self._PATTERNS.items():
            for pat in patterns:
                m = pat.search(blob)
                if m:
                    findings.append(ThreatFinding(
                        category=category,
                        severity=_CATEGORY_SEVERITY[category],
                        detail=f"request text matches {category.value} pattern",
                        evidence=m.group(0),
                    ))
                    break  # one finding per category is enough

        # Structural: privilege escalation (requested scope > granted scope).
        esc = self._detect_privilege_escalation(request)
        if esc is not None:
            findings.append(esc)

        # Structural: unauthorized delegation (delegation requested w/o auth).
        unauth = self._detect_unauthorized_delegation(request)
        if unauth is not None:
            findings.append(unauth)

        return findings

    # -- helpers ----------------------------------------------------------
    def _extract_text(self, request: dict) -> str:
        """Recursively flatten every str value in the request into one blob."""
        parts: List[str] = []

        def walk(value: Any) -> None:
            if isinstance(value, str):
                parts.append(value)
            elif isinstance(value, dict):
                for v in value.values():
                    walk(v)
            elif isinstance(value, (list, tuple, set)):
                for v in value:
                    walk(v)
            else:
                parts.append(str(value))

        walk(request)
        return "\n".join(parts).lower()

    def _detect_privilege_escalation(
        self, request: dict
    ) -> Optional[ThreatFinding]:
        requested = request.get("requested_scope")
        granted = request.get("granted_scope")
        if not _is_valid_scope(requested) or not _is_valid_scope(granted):
            return None
        if _SCOPE_LEVEL[requested] > _SCOPE_LEVEL[granted]:
            return ThreatFinding(
                category=ThreatCategory.PRIVILEGE_ESCALATION,
                severity=_CATEGORY_SEVERITY[ThreatCategory.PRIVILEGE_ESCALATION],
                detail=(
                    f"requested scope {requested} exceeds granted scope {granted}"
                ),
                evidence=f"requested_scope={requested}; granted_scope={granted}",
            )
        return None

    def _detect_unauthorized_delegation(
        self, request: dict
    ) -> Optional[ThreatFinding]:
        has_delegation = (
            "delegation" in request
            or "delegate_target" in request
            or request.get("delegate") is True
        )
        if not has_delegation:
            return None
        if request.get("delegate_authorized") is True:
            return None
        target = request.get("delegate_target") or request.get("delegation", {})
        if isinstance(target, dict):
            target = target.get("target", target)
        return ThreatFinding(
            category=ThreatCategory.UNAUTHORIZED_DELEGATION,
            severity=_CATEGORY_SEVERITY[ThreatCategory.UNAUTHORIZED_DELEGATION],
            detail="delegation requested without explicit authorization",
            evidence=f"delegate_target={target!r}",
        )


# ---------------------------------------------------------------------------
# §72 Security Chain : detect -> block -> audit
# ---------------------------------------------------------------------------
class SecurityChain:
    """Orchestrates the §72 detect -> block -> audit security chain.

    evaluate():
      1. detect  : run ThreatDetector over the request
      2. block   : if any finding is HIGH/CRITICAL, block immediately (no
                   access decision is consulted) and record the audit trail
      3. audit   : otherwise delegate to the Security Kernel's RBAC/ABAC
                   decide_access and record its result in the audit trail
    """

    def __init__(self, security_engine=None, threat_detector: Optional[ThreatDetector] = None):
        self._security = security_engine or get_security_engine()
        self._detector = threat_detector or ThreatDetector()
        self.audit: List[Dict[str, Any]] = []

    def evaluate(self, request: dict, required_permission: str) -> Dict[str, Any]:
        principal_id = request.get("principal_id") or request.get("principal", "anonymous")
        scope = request.get("scope", "L1")
        attributes = request.get("attributes")

        # Step 1: detect
        findings = self._detector.scan(request)
        self.audit.append({
            "step": "detect",
            "outcome": "threats_found" if findings else "clean",
            "findings": [f.category.value for f in findings],
            "principal_id": principal_id,
            "timestamp": datetime.now(timezone.utc),
        })

        # Step 2: block (any HIGH/CRITICAL finding)
        if any(f.severity in _BLOCKING_SEVERITIES for f in findings):
            self.audit.append({
                "step": "block",
                "outcome": "blocked",
                "reason": "high-severity threat detected",
                "categories": [f.category.value for f in findings
                               if f.severity in _BLOCKING_SEVERITIES],
                "principal_id": principal_id,
                "timestamp": datetime.now(timezone.utc),
            })
            return {
                "status": "blocked",
                "findings": findings,
                "audit": list(self.audit),
                "decision": None,
            }

        # Step 3: audit + access decision via the Security Kernel
        decision = self._security.decide_access(
            principal_id, required_permission, scope, attributes
        )
        status = "allowed" if decision["decision"] == "allow" else "denied"
        self.audit.append({
            "step": "access",
            "outcome": status,
            "permission": required_permission,
            "scope": scope,
            "decision": decision["decision"],
            "principal_id": principal_id,
            "timestamp": datetime.now(timezone.utc),
        })
        self.audit.append({
            "step": "audit",
            "outcome": "logged",
            "principal_id": principal_id,
            "timestamp": datetime.now(timezone.utc),
        })
        return {
            "status": status,
            "findings": findings,
            "audit": list(self.audit),
            "decision": decision,
        }


# ---------------------------------------------------------------------------
# §82 Emergency Control (kill-switch)
# ---------------------------------------------------------------------------
class EmergencyControl:
    """Global kill-switch (§82). When active, ``guard`` refuses to run any
    protected callable and returns a blocked signal instead."""

    def __init__(self):
        self._active = False
        self._reason: Optional[str] = None
        self._activated_at: Optional[datetime] = None
        self._lock = threading.RLock()

    def activate(self, reason: str = "") -> Dict[str, Any]:
        with self._lock:
            self._active = True
            self._reason = reason
            self._activated_at = datetime.now(timezone.utc)
            return {
                "active": True,
                "reason": reason,
                "activated_at": self._activated_at,
            }

    def deactivate(self) -> Dict[str, Any]:
        with self._lock:
            self._active = False
            self._reason = None
            self._activated_at = None
            return {"active": False}

    def is_active(self) -> bool:
        with self._lock:
            return self._active

    def reason(self) -> Optional[str]:
        with self._lock:
            return self._reason

    def guard(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Run *fn* only when the kill-switch is NOT active.

        Returns the callable's result on success, or a blocked signal dict
        ``{"blocked": True, "reason": ...}`` when the switch is engaged.
        """
        with self._lock:
            if self._active:
                return {
                    "blocked": True,
                    "reason": self._reason or "emergency control active",
                }
        return fn(*args, **kwargs)


# ---------------------------------------------------------------------------
# §70 Trust Engine : reputation ranking + delegation recommendation
# ---------------------------------------------------------------------------
class ReputationEngine:
    """Reputation services built on the Trust Kernel (§70)."""

    def __init__(self, trust_manager=None):
        self._trust = trust_manager or get_trust_manager()

    def rank(self, agent_ids: List[str]) -> List[str]:
        """Return *agent_ids* sorted by trust score descending, excluding any
        revoked agent. Agents with no stored score sort as 0.0."""
        scored: List[tuple] = []
        for aid in agent_ids:
            if self._trust.is_revoked(aid):
                continue
            score = self._trust.get_score(aid, TrustScope.L0)
            value = score.score if score is not None else 0.0
            scored.append((aid, value))
        scored.sort(key=lambda t: (-t[1], t[0]))
        return [aid for aid, _ in scored]

    def recommend(
        self, agent_ids: List[str], task_difficulty: str = "medium"
    ) -> Optional[str]:
        """Recommend the highest-trust, non-revoked agent for a task.

        *task_difficulty* is retained for transparency; the deterministic rule
        is "highest trust score among non-revoked agents". Returns None when no
        eligible agent remains.
        """
        ranked = self.rank(agent_ids)
        if not ranked:
            return None
        return ranked[0]


# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------
_global_emergency: Optional[EmergencyControl] = None
_global_emergency_lock = threading.Lock()


def get_emergency_control() -> EmergencyControl:
    """Get or create the global EmergencyControl (kill-switch) singleton."""
    global _global_emergency
    if _global_emergency is None:
        with _global_emergency_lock:
            if _global_emergency is None:
                _global_emergency = EmergencyControl()
    return _global_emergency


def get_threat_detector() -> ThreatDetector:
    """Convenience factory for a ThreatDetector (stateless, safe to reuse)."""
    return ThreatDetector()


def get_security_chain() -> SecurityChain:
    """Convenience factory for a SecurityChain bound to the global kernels."""
    return SecurityChain()


def get_reputation_engine() -> ReputationEngine:
    """Convenience factory for a ReputationEngine bound to the Trust Kernel."""
    return ReputationEngine()
