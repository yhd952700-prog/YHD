"""HD-05 — end-to-end verifier (fail-closed).

Verifies an :class:`EvidenceBundle`: the manifest digest matches, the timestamp
token verifies under the configured provider, and any attached signature
verifies. Verification is fail-closed — any error (including an unexpected
exception during verification) is recorded as a failure and ``ok`` becomes False.
"""
from __future__ import annotations

from typing import Optional

from .interfaces import (
    EvidenceBundle,
    Signer,
    TimestampProvider,
    VerificationResult,
    Verifier,
    artifact_digest,
)


class EvidenceVerifier(Verifier):
    """Verifies bundles end-to-end. Holds the provider (and optional signer)
    needed to check the embedded trust proofs. Concrete impl of :class:`Verifier`."""

    def __init__(self, provider: TimestampProvider, signer: Optional[Signer] = None):
        self._provider = provider
        self._signer = signer

    def verify_bundle(self, bundle: EvidenceBundle) -> VerificationResult:
        res = VerificationResult(ok=True)
        canonical = None
        try:
            canonical = _canonical(bundle.manifest)
        except (ValueError, TypeError, UnicodeDecodeError) as exc:
            return res.with_failure(f"manifest is not serializable: {exc}")

        res.with_check("artifact_digest")
        if artifact_digest(canonical) != bundle.artifact_digest:
            res.with_failure("artifact digest mismatch (manifest altered after sealing)")

        res.with_check("timestamp")
        try:
            if not self._provider.verify(canonical, bundle.timestamp):
                res.with_failure("timestamp token invalid for this artifact")
        except Exception as exc:  # fail-closed: never let a verify error look like success
            res.with_failure(f"timestamp verification error: {exc}")

        if bundle.signature is not None:
            res.with_check("signature")
            if self._signer is None:
                res.with_failure("bundle carries a signature but no signer is configured")
            elif not self._signer.verify(canonical, bundle.signature):
                res.with_failure("signature invalid for this artifact")

        return res


def _canonical(manifest) -> bytes:
    import json
    return json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
