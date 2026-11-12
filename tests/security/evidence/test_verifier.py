"""HD-05 — the Verifier interface contract, exercised via EvidenceVerifier.

The Verifier is the 5th of the five subsystem interfaces. End-to-end verification
must be fail-closed: ``ok`` is True only when the digest matches AND the
timestamp verifies AND any attached signature verifies; any failure (or any
exception raised during verification) is recorded and turns ``ok`` False.
"""
from __future__ import annotations

import json

from src.security.evidence import (
    EvidenceVerifier,
    LocalRfc3161LikeProvider,
    ManifestEvidenceAdapter,
    Verifier,
)


def _seal() -> tuple:
    adapter = ManifestEvidenceAdapter(LocalRfc3161LikeProvider(), signer=LocalRfc3161LikeProvider())
    manifest = {"artifact": "audit_view", "sha256": "deadbeef"}
    bundle = adapter.seal(json.dumps(manifest).encode("utf-8"))
    return adapter, bundle


def test_verifier_implements_interface() -> None:
    v = EvidenceVerifier(LocalRfc3161LikeProvider())
    assert isinstance(v, Verifier)


def test_verify_bundle_ok_for_sealed_artifact() -> None:
    adapter, bundle = _seal()
    verifier = EvidenceVerifier(adapter._provider, adapter._signer)
    res = verifier.verify_bundle(bundle)
    assert res.ok is True
    assert res.failures == []
    assert "artifact_digest" in res.checked
    assert "timestamp" in res.checked
    assert "signature" in res.checked
    # The default (local) provider is self-attested: the result must be flagged,
    # never silently presented as independently verified / production-grade.
    assert res.self_attested is True


def test_verify_bundle_detects_tampered_manifest_fail_closed() -> None:
    adapter, bundle = _seal()
    bundle.manifest["sha256"] = "tampered"
    verifier = EvidenceVerifier(adapter._provider, adapter._signer)
    res = verifier.verify_bundle(bundle)
    assert res.ok is False
    # The digest-mismatch failure must be recorded (never silently swallowed).
    assert any("digest" in f.lower() for f in res.failures)


def test_verify_bundle_detects_timestamp_mismatch_fail_closed() -> None:
    adapter, bundle = _seal()
    # Swap in a timestamp token from a different artifact.
    other = adapter.seal(json.dumps({"other": True}).encode("utf-8"))
    bundle.timestamp = other.timestamp
    verifier = EvidenceVerifier(adapter._provider, adapter._signer)
    res = verifier.verify_bundle(bundle)
    assert res.ok is False
    assert any("timestamp" in f.lower() for f in res.failures)


def test_verify_bundle_with_signature_but_no_signer_configured_fails() -> None:
    adapter, bundle = _seal()
    # Verifier built with no signer, but the bundle carries a signature.
    verifier = EvidenceVerifier(adapter._provider, None)
    res = verifier.verify_bundle(bundle)
    assert res.ok is False
    assert any("signature" in f.lower() for f in res.failures)
