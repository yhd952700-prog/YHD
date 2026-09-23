"""P0-8b — unit tests for the per-chain matrix facts (HC-09 / HC-10 / HC-11).

These complement ``scripts/verify_p08b_chain_matrix.py`` (the runtime probe):
they are fast, pure in-memory unit tests over the two memory-only chains and the
MAC registry, so the matrix facts are ALSO asserted inside the normal pytest run
and not only by the standalone guard.

Facts asserted here are the ones the containment requires per chain:
  * canonicalization (which fields are hashed, and how they are serialised);
  * prev_hash participation (included in, or excluded from, the hash input);
  * algorithm in use;
  * fail-closed behaviour where a dispatch mechanism exists.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from enum import Enum

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.security.audit_logger import CryptoAuditLogger, CryptoOperation  # noqa: E402
from src.security.audit_policy import (  # noqa: E402
    AuditKernel,
    AuditEventType as PolicyEventType,
)
from src.kernels.identity._persistence import (  # noqa: E402
    compute_row_tag,
    tag_matches,
    DEFAULT_MAC_ALG,
)


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# HC-09 — security/audit_logger (memory-only)
# ---------------------------------------------------------------------------
def test_hc09_algorithm_and_canonical_exclude_prev_hash() -> None:
    op = next(iter(CryptoOperation))
    logger = CryptoAuditLogger(component_name="pytest-p08b")
    e1 = logger.log(op, key_name="k1", success=True)
    e2 = logger.log(op, key_name="k2", success=True)

    # Canonical form: to_dict() minus event_hash/prev_event_hash.
    canonical = e2.to_dict()
    canonical.pop("event_hash", None)
    canonical.pop("prev_event_hash", None)
    assert _sha256(_canon(canonical)) == e2.event_hash, (
        "HC-09 canonicalization changed: hash no longer equals "
        "sha256(json(to_dict minus event_hash/prev_event_hash))"
    )
    # prev_event_hash is linkage only -- never part of the hash input.
    assert e2.prev_event_hash == e1.event_hash
    assert logger.verify_chain() is True


def test_hc09_has_no_algorithm_declaration() -> None:
    """Records the matrix fact: HC-09 declares no hash_alg (hard-coded sha256).

    If a ``hash_alg`` field is ever added, this test fails on purpose so the
    matrix is re-verified rather than silently drifting.
    """
    op = next(iter(CryptoOperation))
    e = CryptoAuditLogger(component_name="pytest-p08b").log(op, key_name="k")
    fields = getattr(e, "__dataclass_fields__", {})
    assert not any(n in ("hash_alg", "alg", "algorithm") for n in fields), (
        "HC-09 now declares an algorithm -- re-verify the matrix entry"
    )


# ---------------------------------------------------------------------------
# HC-10 — security/audit_policy (memory-only)
# ---------------------------------------------------------------------------
def test_hc10_canonical_includes_prev_hash() -> None:
    etype = next(iter(PolicyEventType))
    kernel = AuditKernel()
    a1 = kernel.log(etype, "principal-1", result="ok")
    a2 = kernel.log(etype, "principal-2", result="ok")

    et = a2.event_type.value if isinstance(a2.event_type, Enum) else a2.event_type
    chain_data = {
        "id": a2.id,
        "timestamp": a2.timestamp.isoformat(),
        "event_type": et,
        "principal_id": a2.principal_id,
        "permission": a2.permission,
        "scope": a2.scope,
        "result": a2.result,
        "reason": a2.reason,
        "correlation_id": a2.correlation_id,
        "prev_hash": a2.prev_hash,
    }
    assert _sha256(_canon(chain_data)) == a2.hash

    # prev_hash really participates: dropping it changes the hash.
    without_prev = {k: v for k, v in chain_data.items() if k != "prev_hash"}
    assert _sha256(_canon(without_prev)) != a2.hash

    # HC-10's genesis seed differs from every JSON chain ("genesis").
    assert a1.prev_hash == "genesis_hash_0"


def test_hc10_metadata_is_not_covered_by_the_hash() -> None:
    """Records the confirmed finding: metadata tampering is undetectable.

    Extending coverage would change every historical hash, so this is a human
    decision -- the test pins the CURRENT truth so a change cannot slip in
    unnoticed.
    """
    etype = next(iter(PolicyEventType))
    kernel = AuditKernel()
    entry = kernel.log(etype, "principal-1", result="ok")
    entry.metadata["injected"] = "TAMPERED"
    ok, _broken = kernel.verify_integrity()
    assert ok is True, "metadata coverage changed -- re-open the human decision"


# ---------------------------------------------------------------------------
# HC-11 — kernels/identity MAC (A3 template; NOT a hash chain)
# ---------------------------------------------------------------------------
def test_hc11_mac_declares_its_algorithm_and_is_fail_closed() -> None:
    entry = {"principal": "ceo", "kind": "human"}
    key = "pytest-integrity-key"

    tag = compute_row_tag(entry, key)
    # The algorithm travels inside the tag, so the record says how to verify it.
    assert tag is not None and tag.startswith(DEFAULT_MAC_ALG + ":"), tag

    assert tag_matches(entry, key, tag) is True

    # Unknown algorithm -> refused, NOT recomputed with the default.
    assert tag_matches(entry, key, "sha512-unknown:" + tag.split(":")[-1]) is False

    # Bare digest with no algorithm identifier -> refused.
    assert tag_matches(entry, key, tag.split(":")[-1]) is False

    # Tampered row -> rejected.
    assert tag_matches(dict(entry, principal="attacker"), key, tag) is False
