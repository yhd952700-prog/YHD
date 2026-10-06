"""OS-ARCH PROBE: does the kernel lifecycle state machine actually GATE anything,
and does the sovereignty DENY land in the audit chain?

Claim under test (from src/kernels/_base.py + src/kernels/_registry.py +
src/gateway/kernels.py): a sovereign can POST /v1/kernels/{name}/pause and the
kernel becomes PAUSED -- an operational control.

This probe measures the actual runtime behavior of a PAUSED / STOPPED kernel
across the kernels that now carry the gate, checks the refusal is visible
rather than silent, AND proves the lifecycle-gate DENY verdict is actually
recorded in the authoritative audit chain (not lost to an early return).

Read-only w.r.t. the frozen HC-01 evidence store: it points the audit store at a
throwaway temp DB via AUDIT_DB_PATH and NEVER touches the repository-root
audit_store.db.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The audit store reads AUDIT_DB_PATH and refuses to default to the frozen
# project-root audit_store.db. Point it at a throwaway temp DB BEFORE importing
# any kernel (the audit store is a lazily-created process singleton).
_PROBE_DIR = tempfile.mkdtemp(prefix="liuhao_lifecycle_probe_")
os.environ["AUDIT_DB_PATH"] = os.path.join(_PROBE_DIR, "probe_audit_store.db")
os.environ["LIUHAO_AUDIT_DB_PATH"] = os.environ["AUDIT_DB_PATH"]
os.environ["LIUHAO_WORKSPACE_ROOT"] = _PROBE_DIR
os.environ.pop("LIUHAO_AUDIT_DB", None)  # legacy name; must not shadow the real var

from src.kernels._base import (  # noqa: E402
    KernelLifecycle,
    KernelNotServableError,
    is_servable,
)
from src.kernels.event import Event, EventBus  # noqa: E402
from src.kernels.policy import PolicyEngine  # noqa: E402
from src.kernels.security import (  # noqa: E402
    AccessDecision,
    RBACRole,
    RBACRule,
    SecurityEngine,
)

results = {}

# ---------------------------------------------------------------- event bus
bus = EventBus()
bus.initialize()
received = []
bus.subscribe("probe.event", lambda ev: received.append(ev))

results["event_ready_delivers"] = (
    bus.publish(Event(type="probe.event", source="probe")) is not None
)
results["event_ready_subscriber_calls"] = len(received)

bus.pause()
results["event_lifecycle_after_pause"] = bus.lifecycle.value
try:
    bus.publish(Event(type="probe.event", source="probe"))
    results["event_publish_while_paused"] = "ACCEPTED (gate missing)"
except KernelNotServableError as exc:
    results["event_publish_while_paused"] = f"REFUSED ({type(exc).__name__})"
results["event_subscriber_calls_while_paused"] = len(received)  # must stay at 1
results["event_dead_letters_after_refusal"] = len(bus._dead_letters)

bus.resume()
bus.publish(Event(type="probe.event", source="probe"))
results["event_resume_restores_delivery"] = len(received) == 2

# ---------------------------------------------------------------- security
# No colon permissions are seeded by default (deliberate fail-closed, see
# SecurityEngine._seed_default_rules), so register one explicitly. Without a
# real ALLOW baseline the probe could not tell "the gate denied it" apart from
# "it was denied anyway".
sec = SecurityEngine()
sec.initialize()
sec._rbac_rules["probe:read"] = RBACRule(
    id="probe-rule",
    role=RBACRole.ADMIN,
    permission="probe:read",
)
sec.grant_rbac_role("alice", RBACRole.ADMIN, reason="probe")
results["security_ready_allows_admin"] = (
    sec.check_rbac("alice", "probe:read", "L1") == AccessDecision.ALLOW
)

sec.pause()
results["security_ready_check_while_paused"] = sec.check_rbac(
    "alice", "probe:read", "L1"
).value
results["security_decide_while_paused"] = sec.decide_access(
    "alice", "probe:read", "L1"
)["decision"]
sec.resume()
results["security_resume_restores_allow"] = (
    sec.check_rbac("alice", "probe:read", "L1") == AccessDecision.ALLOW
)

# ---------------------------------------------------------------- policy
pol = PolicyEngine()
pol.initialize()
ctx = {"actor": {"type": "human", "id": "probe", "verified": True}}
results["policy_ready_decision"] = pol.evaluate(dict(ctx)).decision.value

pol.pause()
results["policy_decision_while_paused"] = pol.evaluate(dict(ctx)).decision.value
results["policy_traceability_while_paused"] = pol.evaluate(dict(ctx)).traceability

# PROOF THAT THE LIFECYCLE DENY LANDS IN THE AUDIT CHAIN (not a silent early
# return). Read the throwaway audit DB and confirm a denied policy_eval row was
# actually written while the engine was paused.
try:
    _con = sqlite3.connect(os.environ["AUDIT_DB_PATH"])
    _rows = _con.execute(
        "SELECT outcome, details FROM audit_events "
        "WHERE event_type LIKE '%policy%' ORDER BY seq"
    ).fetchall()
    _con.close()
    _deny_rows = [
        (o, d) for (o, d) in _rows
        if o == "denied" and "lifecycle-gate" in (d or "")
    ]
    results["policy_audit_rows_total"] = len(_rows)
    results["policy_lifecycle_deny_in_audit_chain"] = len(_deny_rows)
    results["policy_lifecycle_deny_audited"] = len(_deny_rows) >= 1
except Exception as exc:  # pragma: no cover - probe must report, not crash
    results["policy_lifecycle_deny_audited"] = False
    results["policy_audit_error"] = f"{type(exc).__name__}: {exc}"

pol.resume()

# ---------------------------------------------------------------- verdict
results["verdict"] = (
    "GATE WORKS: paused/stopped kernels refuse AND the lifecycle DENY is "
    "recorded in the audit chain"
    if (
        results["event_publish_while_paused"].startswith("REFUSED")
        and results["event_subscriber_calls_while_paused"] == 1
        and results["security_ready_check_while_paused"] == "deny"
        and results["policy_decision_while_paused"] == "deny"
        and results["event_resume_restores_delivery"]
        and results["security_resume_restores_allow"]
        and results.get("policy_lifecycle_deny_audited") is True
    )
    else "GATE INCOMPLETE"
)
results["is_servable_matrix"] = {
    st.value: is_servable(type("K", (), {"lifecycle": st})())
    for st in KernelLifecycle
}

print(json.dumps(results, indent=2, ensure_ascii=False, default=str))
