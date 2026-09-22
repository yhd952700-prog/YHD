#!/usr/bin/env python
"""Runtime probe: prove A2 audit principal identity does not collapse.

PHASE 3.6 / A2 (P0-4): before the fix, every ``@kernel_action`` wrote the
literal constant ``"kernel"`` as the audit subject, so 43 production action
points recorded the same indistinguishable principal -- a human approval and an
agent action were indistinguishable in the only durable record. A2 resolved the
subject through the Identity Kernel so the audit record and the authorization
decision name the *same* identity in the *same* key space.

This probe drives the REAL ``@kernel_action`` execution path against a REAL
on-disk audit store (SQLite, hash-chained) and re-reads the durable record
INDEPENDENTLY -- it does not assert on field presence, it asserts the recorded
subject is the identity that actually performed the action. It covers the boss's
P0-4 acceptance matrix:

  1. Human action    -- a real human principal is traceable in the audit.
  2. Agent action    -- a real agent identity is traceable.
  3. Distinct        -- two different subjects yield different, verifiable
                        audit identities (no collapse to "kernel").
  4. Correlation     -- principal_id == actor_identity_id in the same record.
  5. Collision       -- no legacy / short-id / key-space confusion.
  6. Persistence     -- restart does not silently change the representation.
  7. Runtime probe   -- real execution path + real audit store, re-read; no
                        static-only proof.

Exit codes:
  0  every assertion passed
  1  a security assertion FAILED (regression -- must not ship)
  2  partial / known gap (unused)
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Point the audit store and the human registry at isolated temp files BEFORE any
# kernel module is imported, so the singletons build against them.
_TMP = tempfile.mkdtemp(prefix="a2_probe_")
_AUDIT_DB = os.path.join(_TMP, "audit_store.db")
_REG_FILE = os.path.join(_TMP, "human_identities.json")
os.environ["AUDIT_DB_PATH"] = _AUDIT_DB
os.environ["LIUHAO_HUMAN_IDENTITIES_FILE"] = _REG_FILE
os.environ.pop("LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY", None)

from src.kernels._crosscutting import bind_acting_principal  # noqa: E402
from src.kernels.audit import get_audit_store  # noqa: E402
from src.kernels.identity import (  # noqa: E402
    IdentityManager,
    get_identity_manager,
    is_human_identity,
)

_RESULTS: list[tuple[bool, str]] = []


def _record(ok: bool, label: str) -> None:
    _RESULTS.append((ok, label))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")


def _latest_event_for(principal_id: str) -> dict:
    """Independent re-read of the durable audit record for a subject."""
    events = get_audit_store().query_events(principal_id=principal_id, reverse=True)
    assert events, f"no audit event found for principal_id={principal_id!r}"
    return events[0]


def main() -> int:
    print("A2 audit principal identity runtime probe")
    print("  (real @kernel_action + real SQLite hash-chain audit store)\n")

    mgr = get_identity_manager()

    # Register a real HUMAN and a real AGENT through the live identity kernel.
    human = mgr.create_human_identity("bob", permissions={"admin"})
    agent = mgr.create_identity("agent_x", permissions={"read"})
    assert human is not None and agent is not None, "precondition: identities create"
    human_id = human.id            # "human:bob"
    agent_id = agent.id            # 8-hex agent id
    human_fp = human.fingerprint   # canonical, derived from namespace+principal
    agent_fp = agent.fingerprint
    print(f"  human identity: id={human_id} fingerprint={human_fp}")
    print(f"  agent identity: id={agent_id} fingerprint={agent_fp}\n")

    _record(
        is_human_identity(human) and human.namespace == "human",
        "human 'bob' resolves as namespace=human (not collapsed)",
    )
    _record(
        agent.namespace == "agent" and agent.id == agent_id,
        "agent 'agent_x' resolves as namespace=agent",
    )

    # --- (1)(4) Human action: real principal traceable + correlation --------
    with bind_acting_principal("bob", kind="human"):
        mgr.create_identity("a2_human_action")
    evt_human = _latest_event_for(human_id)
    d_human = evt_human["details"]
    _record(
        evt_human["principal_id"] == human_id,
        f"human action: principal_id == {human_id!r} (real human traced)",
    )
    _record(
        d_human.get("actor_kind") == "human",
        "human action: actor_kind == 'human'",
    )
    _record(
        d_human.get("actor_identity_id") == human_id,
        "human action: actor_identity_id == human id (same key space)",
    )
    _record(
        d_human.get("actor_fingerprint") == human_fp,
        "human action: actor_fingerprint == the identity's canonical fingerprint",
    )
    # Correlation: in the one durable record, the written principal and the
    # recorded identity id are the same subject (no split between action /
    # decision / enforcement / evidence).
    _record(
        evt_human["principal_id"] == d_human.get("actor_identity_id"),
        "human action: principal_id == actor_identity_id (correlated, no collapse)",
    )
    _record(
        bool(evt_human["correlation_id"]),
        "human action: correlation_id emitted (real per-action UUID for "
        "cross-stage / cross-event linkage)",
    )
    # Correlation: across every event that shares this action's correlation_id
    # (action / decision / enforcement / evidence stages), the SAME principal
    # must be named -- no principal drift within one correlation span.
    human_corr_events = get_audit_store().query_events(
        correlation_id=evt_human["correlation_id"], reverse=True
    )
    _record(
        bool(human_corr_events)
        and all(
            e["principal_id"] == human_id
            and (e["details"].get("actor_identity_id") or e["principal_id"]) == human_id
            for e in human_corr_events
        ),
        "human action: all events sharing correlation_id name the same human "
        "principal (no principal drift across stages)",
    )

    # --- (2)(4) Agent action: real agent identity traceable + correlation ----
    with bind_acting_principal("agent_x", kind="agent"):
        mgr.create_identity("a2_agent_action")
    evt_agent = _latest_event_for(agent_id)
    d_agent = evt_agent["details"]
    _record(
        evt_agent["principal_id"] == agent_id,
        f"agent action: principal_id == {agent_id!r} (real agent traced)",
    )
    _record(
        d_agent.get("actor_kind") == "agent",
        "agent action: actor_kind == 'agent'",
    )
    _record(
        d_agent.get("actor_identity_id") == agent_id,
        "agent action: actor_identity_id == agent id",
    )
    _record(
        d_agent.get("actor_fingerprint") == agent_fp,
        "agent action: actor_fingerprint == the agent's canonical fingerprint",
    )
    _record(
        evt_agent["principal_id"] == d_agent.get("actor_identity_id"),
        "agent action: principal_id == actor_identity_id (correlated, no collapse)",
    )
    _record(
        bool(evt_agent["correlation_id"]),
        "agent action: correlation_id emitted (real per-action UUID)",
    )
    agent_corr_events = get_audit_store().query_events(
        correlation_id=evt_agent["correlation_id"], reverse=True
    )
    _record(
        bool(agent_corr_events)
        and all(
            e["principal_id"] == agent_id
            and (e["details"].get("actor_identity_id") or e["principal_id"]) == agent_id
            for e in agent_corr_events
        ),
        "agent action: all events sharing correlation_id name the same agent "
        "principal (no principal drift across stages)",
    )

    # --- (3) Distinct principals: no collapse to "kernel" -------------------
    _record(
        evt_human["principal_id"] != evt_agent["principal_id"],
        "distinct subjects produce distinct audit principal_id",
    )
    _record(
        d_human.get("actor_fingerprint") != d_agent.get("actor_fingerprint"),
        "distinct subjects produce distinct actor_fingerprint",
    )
    for label, ev in (("human", evt_human), ("agent", evt_agent)):
        _record(
            ev["principal_id"] not in ("kernel", "liuhao-internal-service"),
            f"{label} action: principal is NOT the literal 'kernel' / service "
            f"principal (no collapse)",
        )

    # --- (5) Collision: no legacy / short-id / key-space confusion ----------
    # A human and an agent must not resolve to the same principal, and a
    # duplicate principal must be refused (principal uniqueness holds).
    dup_human = mgr.create_human_identity("bob")
    dup_agent = mgr.create_identity("bob")
    _record(
        dup_human is None and dup_agent is None,
        "collision guard: re-registering principal 'bob' (human or agent) is "
        "refused -- no key-space confusion",
    )
    # The human resolves to the human namespace, never an agent slot.
    resolved_bob = mgr.get_identity_by_principal("bob")
    _record(
        resolved_bob is not None
        and resolved_bob.namespace == "human"
        and resolved_bob.id == human_id,
        "collision guard: 'bob' still resolves to the human identity "
        "(human:bob), no ambiguity",
    )

    # --- (6) Persistence: restart must not silently change representation ---
    # A fresh manager reading the SAME registry file (simulating a restart)
    # must resolve 'bob' to the identical id and fingerprint.
    restarted = IdentityManager()
    restarted_bob = restarted.get_identity_by_principal("bob")
    _record(
        restarted_bob is not None
        and restarted_bob.id == human_id
        and restarted_bob.fingerprint == human_fp,
        "persistence: after restart, 'bob' keeps id + fingerprint unchanged "
        "(no silent representation change)",
    )

    # ----------------------------------------------------------------------
    passed = sum(1 for ok, _ in _RESULTS if ok)
    total = len(_RESULTS)
    print(f"\n{passed}/{total} assertions passed")
    failed = [label for ok, label in _RESULTS if not ok]
    if failed:
        print("FAILED:")
        for label in failed:
            print(f"  - {label}")
        return 1
    print("A2 audit principal identity is CONTAINED (different subjects stay "
          "distinct; none collapse to 'kernel').")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
