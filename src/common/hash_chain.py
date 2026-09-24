"""Explicit hash-algorithm registry for hash-chain integrity (P0-8b).

Every hash chain in LIUHAO must (1) *declare* which algorithm was used to
compute each link, and (2) verify by *dispatching* on that declared name with
**fail-closed** semantics — an unknown algorithm must NEVER silently fall back
to a default. This mirrors the already-compliant A5 audit chain
(``kernels/audit``) and the A3 MAC registry (``kernels/identity``).

This module is the single source of truth. The JSON-file chains (HC-02..HC-08)
now persist an explicit ``hash_alg`` envelope field and verify through
``verify_declared_hash``. Legacy on-disk files written before this change carry
no ``hash_alg``; on load they default to ``"sha256"`` (the algorithm that was
previously hard-coded silently), so existing data keeps verifying — this change
is backward compatible and non-breaking.

Canonicalization is intentionally *not* unified here: each chain keeps its own
existing canonical form (some include ``prev_hash`` in the hash input, the main
audit chain excludes it). Unifying canonicalization is a separate, breaking
change that requires a data-migration plan and is deliberately out of scope for
this round.

``canon_version`` (see D21 / HC-09 / HC-10) is a VERSION LABEL / contract
selector carried by an event, NOT a durability claim. It names which canonical
contract produced the chain link; it confers NO authority and NO persistence on
the chain. Durable, authoritative audit is the SQLite ``src.kernels.audit`` store
(HC-01), whose runtime chain-of-custody integrity is currently UNVERIFIED. A
non-empty ``canon_version`` therefore proves nothing about evidence quality.
"""

from __future__ import annotations

import hashlib
import json
from typing import Callable, Dict, Tuple

# Registry: algorithm identifier -> function returning the hex digest of bytes.
# Add new algorithms here; never remove one that may exist in persisted data
# without a migration plan.
HASH_ALGORITHMS: Dict[str, Callable[[bytes], str]] = {
    "sha256": lambda data: hashlib.sha256(data).hexdigest(),
}

DEFAULT_HASH_ALG = "sha256"


def canonical_bytes(payload: dict) -> bytes:
    """Deterministic canonical JSON encoding (sorted keys, compact separators)."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def compute_hash(alg: str, data: bytes) -> str:
    """Compute the hash of *data* using the named algorithm.

    Raises ``ValueError`` if the algorithm is not registered. Use this at *write*
    time, where the algorithm is always a known constant.
    """
    fn = HASH_ALGORITHMS.get(alg)
    if fn is None:
        raise ValueError(f"unknown hash algorithm: {alg!r}")
    return fn(data)


def verify_declared_hash(
    declared_alg: str, canonical: bytes, expected: str
) -> Tuple[bool, str]:
    """Verify *expected* equals the hash of *canonical* under *declared_alg*.

    Fail-closed: an unknown declared algorithm returns ``(False, "unknown")`` and
    is NEVER silently recomputed with a default algorithm. A hash that does not
    match returns ``(False, "mismatch")``.
    """
    fn = HASH_ALGORITHMS.get(declared_alg)
    if fn is None:
        return (False, "unknown")
    actual = fn(canonical)
    if actual != expected:
        return (False, "mismatch")
    return (True, "ok")


# ---------------------------------------------------------------------------
# D21 — canon-version dispatch (label / contract selector, NOT a durability claim)
# ---------------------------------------------------------------------------
KNOWN_CANON_VERSIONS = frozenset({"HC-FROZEN-v1", "CURRENT", "LEGACY"})


def verify_by_canon_version(
    declared_version: str, canonical_bytes: bytes, expected_hash: str, alg: str
) -> Tuple[bool, str]:
    """Verify *expected_hash* of *canonical_bytes* under *alg*, gated by version.

    ``declared_version`` is a version LABEL / contract selector (e.g. "CURRENT",
    "LEGACY", "HC-FROZEN-v1"). It is NOT a durability claim and confers no
    authority on the chain. If the label is unknown the verification is refused
    with ``(False, "unknown_version")``. Otherwise dispatch to
    ``verify_declared_hash`` (fail-closed: an unknown *alg* returns
    ``(False, "unknown")`` -- there is NO default fallback to sha256).
    """
    if declared_version not in KNOWN_CANON_VERSIONS:
        return (False, "unknown_version")
    return verify_declared_hash(alg, canonical_bytes, expected_hash)
