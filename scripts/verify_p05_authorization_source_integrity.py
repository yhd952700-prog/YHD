#!/usr/bin/env python
"""Runtime probe: prove P0-5 Authorization Source Integrity.

PHASE 3.6 / P0-5 (A2 + F33): the *authorization source* -- the actor
``_adjudicate`` returns as "who did / authorised this" -- must be the *same*
subject that ends up in the durable audit record, with no drift and no
sovereignty-washing. Before A2/F33 a service / default actor could be recorded
under a human-looking attribution, or a refused sovereignty claim could read as
a live human window.

This probe drives the REAL ``@kernel_action`` execution path (the CRITICAL action
``capability.retire``) against a REAL on-disk hash-chain audit store, opens REAL
sovereignty windows, and re-reads the durable record INDEPENDENTLY. It covers
the boss's P0-5 acceptance matrix:

  1. Service default (no window)  -- recorded actor is the INTERNAL SERVICE
                                     principal, NEVER a human, NEVER "kernel".
  2. Valid human escalation       -- a real, verified human window escalates the
                                     actor to that human and the audit records it.
  3. Rejected claim (grant 404)   -- a refused sovereignty claim stays on the
                                     service actor (fail-closed); NO false-human.
  4. False-human guard            -- a window naming a principal that is NOT a
                                     real, verified human is recorded as SERVICE,
                                     not human (no sovereignty-washing).
  5. Convergence                 -- the actor ``_adjudicate`` authorised is the
                                     actor the audit recorded (same principal_id
                                     and actor_kind); intent + final events of one
                                     correlation span agree (no cross-stage drift).
  6. No collapse                 -- no scenario records the literal "kernel".

Exit codes:
  0  every assertion passed
  1  a security assertion FAILED (regression -- must not ship)
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Isolate the audit store and human registry at import time so the singletons
# build against temp files (never the production stores).
_TMP = tempfile.mkdtemp(prefix="p05_probe_")
_AUDIT_DB = os.path.join(_TMP, "audit_store.db")
_REG_FILE = os.path.join(_TMP, "human_identities.json")
os.environ["AUDIT_DB_PATH"] = _AUDIT_DB
os.environ["LIUHAO_HUMAN_IDENTITIES_FILE"] = _REG_FILE
os.environ.pop("LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY", None)

from src.kernels._crosscutting import _adjudicate, _resolve_actor_identity  # noqa: E402
from src.kernels._sovereignty import (  # noqa: E402
    clear_active_sovereignty,
    human_sovereign,
)
from src.kernels.audit import get_audit_store  # noqa: E402
from src.kernels.capability import get_capability_registry  # noqa: E402
from src.kernels.identity import IdentityManager, get_identity_manager  # noqa: E402

_RESULTS: list[tuple[bool, str]] = []
KERNEL_PRINCIPAL = "liuhao-internal-service"


def _record(ok: bool, label: str) -> None:
    _RESULTS.append((ok, label))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")


def _latest_event() -> dict:
    events = get_audit_store().query_events(reverse=True)
    assert events, "no audit event found"
    return events[0]


def _events_for_corr(corr_id: str) -> list[dict]:
    return get_audit_store().query_events(correlation_id=corr_id, reverse=True)


def main() -> int:
    print("P0-5 authorization source integrity runtime probe")
    print("  (real CRITICAL @kernel_action + real SQLite hash-chain store)\n")

    clear_active_sovereignty()
    mgr = get_identity_manager()
    human = mgr.create_human_identity("bob", permissions={"admin"})
    assert human is not None, "precondition: human 'bob' creates"
    human_id = human.id  # "human:bob"
    assert human_id == "human:bob", human_id
    reg = get_capability_registry()
    print(f"  human identity: id={human_id}\n")

    # --- (1) Service default: no sovereignty window -------------------------
    clear_active_sovereignty()
    reg.retire("p05_cap_a", "core")
    evt_a = _latest_event()
    d_a = evt_a["details"]
    corr_a = evt_a["correlation_id"]
    _record(
        evt_a["principal_id"] == KERNEL_PRINCIPAL,
        f"service-default: principal_id == {KERNEL_PRINCIPAL!r} (not human, not kernel)",
    )
    _record(
        d_a.get("actor_kind") == "service" and d_a.get("actor_source") == "service-default",
        "service-default: actor_kind=='service', source=='service-default'",
    )
    _record(
        evt_a["principal_id"] != "kernel" and d_a.get("actor_kind") != "human",
        "service-default: NOT recorded as a human (no sovereignty-washing)",
    )

    # --- (2) Valid human escalation (bare C-3, grant_id=None) ---------------
    with human_sovereign(human_id, ["capability.retire"]):
        verdict_b, rule_b, auth_actor_b = _adjudicate("capability.retire", "CRITICAL")
        reg.retire("p05_cap_b", "core")
    evt_b = _latest_event()
    d_b = evt_b["details"]
    corr_b = evt_b["correlation_id"]
    _record(
        auth_actor_b["type"] == "human" and auth_actor_b["principal"] == human_id,
        "human-escalation: _adjudicate authorised the human (source == human)",
    )
    _record(
        evt_b["principal_id"] == human_id,
        f"human-escalation: principal_id == {human_id!r} (real human traced)",
    )
    _record(
        d_b.get("actor_kind") == "human" and d_b.get("actor_source") == "sovereignty-window",
        "human-escalation: actor_kind=='human', source=='sovereignty-window'",
    )
    _record(
        d_b.get("actor_identity_id") == human_id,
        "human-escalation: actor_identity_id == human id (same key space)",
    )
    # Convergence: authorization source == recorded principal (the P0-5 core).
    _record(
        evt_b["principal_id"] == _resolve_actor_identity(auth_actor_b["principal"])["identity_id"]
        and evt_b["principal_id"] == d_b.get("actor_identity_id"),
        "human-escalation: authorization source CONVERGES with audit record "
        "(principal_id == actor_identity_id)",
    )

    # --- (3) Rejected claim: window carries a non-existent grant_id ---------
    with human_sovereign(human_id, ["capability.retire"], grant_id="ghost-grant-404"):
        reg.retire("p05_cap_c", "core")
    evt_c = _latest_event()
    d_c = evt_c["details"]
    corr_c = evt_c["correlation_id"]
    _record(
        d_c.get("actor_kind") == "service"
        and d_c.get("actor_source") == "sovereignty-window-rejected",
        "rejected-claim: actor stays SERVICE (fail-closed), source rejected",
    )
    _record(
        evt_c["principal_id"] == KERNEL_PRINCIPAL
        and d_c.get("actor_kind") != "human",
        "rejected-claim: NOT recorded as human (no false-human approval)",
    )
    _record(
        bool(d_c.get("sovereignty_claim")),
        "rejected-claim: sovereignty_claim explains WHY it was refused",
    )

    # --- (4) False-human guard: window names a principal that is NOT a ------
    #         real, verified human -> must be recorded as SERVICE, not human. --
    with human_sovereign("human:ghost", ["capability.retire"]):
        reg.retire("p05_cap_d", "core")
    evt_d = _latest_event()
    d_d = evt_d["details"]
    corr_d = evt_d["correlation_id"]
    _record(
        d_d.get("actor_kind") == "service"
        and d_d.get("sovereignty_claim") == "claim-human-not-verified",
        "false-human guard: unverified principal recorded as SERVICE "
        "(claim-human-not-verified), not human",
    )
    _record(
        d_d.get("actor_kind") != "human",
        "false-human guard: NO sovereignty-washing for a non-existent human",
    )

    # --- (5) Correlation-span consistency: intent + final agree ------------
    for tag, corr in (("human", corr_b), ("rejected", corr_c)):
        span = _events_for_corr(corr)
        _record(
            bool(span)
            and all(
                e["principal_id"] == span[0]["principal_id"]
                and e["details"].get("actor_kind") == span[0]["details"].get("actor_kind")
                for e in span
            ),
            f"{tag} action: every event in the correlation span (intent + final) "
            "names the SAME principal + actor_kind (no cross-stage drift)",
        )

    # --- (6) No collapse to "kernel" across all four scenarios -------------
    for tag, evt in (("service", evt_a), ("human", evt_b), ("rejected", evt_c), ("guard", evt_d)):
        _record(
            evt["principal_id"] != "kernel",
            f"{tag} action: principal is NOT the literal 'kernel' (no collapse)",
        )

    passed = sum(1 for ok, _ in _RESULTS if ok)
    total = len(_RESULTS)
    print(f"\n{passed}/{total} assertions passed")
    failed = [label for ok, label in _RESULTS if not ok]
    if failed:
        print("FAILED:")
        for label in failed:
            print(f"  - {label}")
        return 1
    print("P0-5 Authorization Source Integrity is CONTAINED (authorization source "
          "== audit record; no sovereignty-washing; no collapse to 'kernel').")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
