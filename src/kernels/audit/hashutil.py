"""One canonical serialization for the audit chain, shared by writer and verifier.

The writer hashes an event to produce ``event_hash``; the verifier re-hashes the
stored fields and compares. If those two ever compute their canonical form
differently, the verifier silently stops detecting tampering -- it would compare
two different bytes and report a mismatch on every row, or worse, be "fixed" by
loosening the comparison.

So the canonical form lives in exactly one function and both sides call it.
"""
from __future__ import annotations

import json
from typing import Any, Dict


def canonical_json(data: Dict[str, Any]) -> str:
    """Deterministic JSON: sorted keys, no incidental whitespace.

    Changing this function invalidates every hash already written. That is
    intentional and must never be done casually -- it is a chain migration, not
    a formatting tweak.
    """
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def event_payload(event) -> Dict[str, Any]:
    """The exact field set that ``event_hash`` commits to.

    Kept next to the canonicalization so the two cannot drift apart.
    """
    return {
        "event_id": event.event_id,
        "event_type": event.event_type.value,
        "principal_id": event.principal_id,
        "scope": event.scope.value,
        "timestamp": event.timestamp,
        "correlation_id": event.correlation_id,
        "outcome": event.outcome,
        "details": event.details,
    }
