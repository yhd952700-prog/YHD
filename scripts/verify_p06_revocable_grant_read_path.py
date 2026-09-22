#!/usr/bin/env python
"""Runtime probe: prove P0-6 revocable-grant read-path (F33 real-time re-resolution).

PHASE 3.6 / P0-6 (F33 / D-2): a sovereignty window carries only a ``grant_id``
snapshot. The authorization it grants must be RE-RESOLVED from the grant
registry at every adjudication -- a grant revoked or expired AFTER the window
opened must stop authorising, and a grant issued to principal A must not be
spent by principal B. The window's frozen dataclass must not keep working.

This probe drives the REAL CRITICAL ``@kernel_action`` ``capability.retire``
against a REAL on-disk hash-chain audit store, issues/revokes REAL grants, and
re-reads the durable record INDEPENDENTLY. It covers the boss's P0-6 matrix:

  1. Valid grant       -- a live grant escalates the holder to a human (grant
                          provenance works, not just the bare C-3 channel).
  2. Revoked grant     -- the SAME grant_id, revoked after the window opened,
                          now fails-closed to SERVICE (no human, no approval).
  3. Expired grant     -- a TTL-expired grant also fails-closed to SERVICE.
  4. Principal mismatch-- a grant issued to A cannot be spent by B (B stays
                          SERVICE; no stolen-human approval).
  5. Real-time contrast-- the identical grant_id escalates BEFORE revocation and
                          does NOT escalate AFTER (re-resolution is live).
  6. No collapse       -- no scenario records the literal "kernel".

Exit codes:
  0  every assertion passed
  1  a security assertion FAILED (regression -- must not ship)
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_TMP = tempfile.mkdtemp(prefix="p06_probe_")
_AUDIT_DB = os.path.join(_TMP, "audit_store.db")
_REG_FILE = os.path.join(_TMP, "human_identities.json")
os.environ["AUDIT_DB_PATH"] = _AUDIT_DB
os.environ["LIUHAO_HUMAN_IDENTITIES_FILE"] = _REG_FILE
os.environ.pop("LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY", None)

from src.kernels._sovereignty import (  # noqa: E402
    clear_active_sovereignty,
    human_sovereign,
    issue_grant,
    revoke_grant,
)
from src.kernels.audit import get_audit_store  # noqa: E402
from src.kernels.capability import get_capability_registry  # noqa: E402
from src.kernels.identity import get_identity_manager  # noqa: E402

_RESULTS: list[tuple[bool, str]] = []
KERNEL_PRINCIPAL = "liuhao-internal-service"


def _record(ok: bool, label: str) -> None:
    _RESULTS.append((ok, label))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")


def _latest_event() -> dict:
    events = get_audit_store().query_events(reverse=True)
    assert events, "no audit event found"
    return events[0]


def _check(corr_event: dict, *, expect_kind: str, expect_principal: str,
           expect_source: str | None = None, expect_claim: str | None = None) -> None:
    d = corr_event["details"]
    _record(
        corr_event["principal_id"] == expect_principal,
        f"principal_id == {expect_principal!r}",
    )
    _record(
        d.get("actor_kind") == expect_kind,
        f"actor_kind == {expect_kind!r}",
    )
    if expect_source is not None:
        _record(
            d.get("actor_source") == expect_source,
            f"actor_source == {expect_source!r}",
        )
    if expect_claim is not None:
        _record(
            d.get("sovereignty_claim") == expect_claim,
            f"sovereignty_claim == {expect_claim!r} (refusal reason recorded)",
        )
    _record(
        corr_event["principal_id"] != "kernel",
        "principal is NOT the literal 'kernel' (no collapse)",
    )


def main() -> int:
    print("P0-6 revocable-grant read-path (F33) runtime probe")
    print("  (real CRITICAL @kernel_action + real grants + real hash-chain store)\n")

    clear_active_sovereignty()
    mgr = get_identity_manager()
    bob = mgr.create_human_identity("bob", permissions={"admin"})
    alice = mgr.create_human_identity("alice", permissions={"admin"})
    assert bob is not None and alice is not None
    bob_id, alice_id = bob.id, alice.id  # human:bob / human:alice
    reg = get_capability_registry()
    print(f"  humans: {bob_id}, {alice_id}\n")

    # --- (1) Valid grant: live grant escalates the holder to human ----------
    grant = issue_grant(bob_id, ["capability.retire"], ttl_seconds=3600)
    with human_sovereign(bob_id, ["capability.retire"], grant_id=grant.grant_id):
        reg.retire("p06_cap_valid", "core")
    evt_valid = _latest_event()
    _record(
        evt_valid["principal_id"] == bob_id
        and evt_valid["details"].get("actor_kind") == "human"
        and evt_valid["details"].get("actor_source") == "sovereignty-window",
        "valid grant: live grant escalates holder to HUMAN (grant provenance works)",
    )
    _record(
        evt_valid["details"].get("actor_identity_id") == bob_id,
        "valid grant: actor_identity_id == human id (same key space)",
    )

    # --- (2) Revoked grant: SAME grant_id after revocation -> fail-closed ----
    revoke_grant(grant.grant_id)
    with human_sovereign(bob_id, ["capability.retire"], grant_id=grant.grant_id):
        reg.retire("p06_cap_revoked", "core")
    evt_revoked = _latest_event()
    _check(
        evt_revoked, expect_kind="service", expect_principal=KERNEL_PRINCIPAL,
        expect_source="sovereignty-window-rejected", expect_claim="grant-revoked",
    )
    _record(
        evt_revoked["details"].get("actor_kind") != "human",
        "revoked grant: NOT recorded as human (no stolen approval)",
    )

    # --- (5) Real-time contrast: identical grant_id, human before, service after
    _record(
        evt_valid["details"].get("actor_kind") == "human"
        and evt_revoked["details"].get("actor_kind") == "service",
        "real-time re-resolution: same grant_id escalates BEFORE revoke, "
        "fails-closed AFTER revoke (window snapshot does not keep working)",
    )

    # --- (3) Expired grant: TTL-expired grant -> fail-closed -----------------
    grant2 = issue_grant(bob_id, ["capability.retire"], ttl_seconds=0.02)
    time.sleep(0.1)  # let the TTL elapse
    with human_sovereign(bob_id, ["capability.retire"], grant_id=grant2.grant_id):
        reg.retire("p06_cap_expired", "core")
    evt_expired = _latest_event()
    _check(
        evt_expired, expect_kind="service", expect_principal=KERNEL_PRINCIPAL,
        expect_source="sovereignty-window-rejected", expect_claim="grant-expired",
    )

    # --- (4) Principal mismatch: A's grant cannot be spent by B -------------
    grant3 = issue_grant(alice_id, ["capability.retire"], ttl_seconds=3600)
    # bob opens a window carrying alice's grant id -> must NOT escalate bob.
    with human_sovereign(bob_id, ["capability.retire"], grant_id=grant3.grant_id):
        reg.retire("p06_cap_mismatch", "core")
    evt_mismatch = _latest_event()
    _check(
        evt_mismatch, expect_kind="service", expect_principal=KERNEL_PRINCIPAL,
        expect_source="sovereignty-window-rejected",
        expect_claim="grant-principal-mismatch",
    )
    _record(
        evt_mismatch["details"].get("actor_kind") != "human",
        "principal mismatch: B cannot spend A's grant (no stolen-human approval)",
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
    print("P0-6 Revocable-Grant Read-Path (F33) is CONTAINED (grant revocation / "
          "expiry / mismatch re-resolved at adjudication; no stolen approval; no "
          "collapse to 'kernel').")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
