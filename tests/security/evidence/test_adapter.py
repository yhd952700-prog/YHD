"""HD-05 — evidence adapter: seal a manifest into a bundle, open it back, fail-closed."""
from __future__ import annotations

import json

from src.security.evidence import LocalRfc3161LikeProvider, ManifestEvidenceAdapter


def test_seal_and_open_round_trip() -> None:
    adapter = ManifestEvidenceAdapter(LocalRfc3161LikeProvider())
    manifest = {"artifact": "audit_view", "sha256": "deadbeef", "size": 123}
    bundle = adapter.seal(json.dumps(manifest).encode("utf-8"))
    out_bytes, verified = adapter.open(bundle)
    assert verified is True
    assert json.loads(out_bytes) == manifest


def test_open_detects_tampered_manifest_fail_closed() -> None:
    adapter = ManifestEvidenceAdapter(LocalRfc3161LikeProvider())
    manifest = {"a": 1}
    bundle = adapter.seal(json.dumps(manifest).encode("utf-8"))
    # Tamper the stored manifest after sealing.
    bundle.manifest["a"] = 2
    _out, verified = adapter.open(bundle)
    assert verified is False


def test_adapter_serializes_to_json() -> None:
    adapter = ManifestEvidenceAdapter(LocalRfc3161LikeProvider())
    bundle = adapter.seal(json.dumps({"k": "v"}).encode("utf-8"))
    data = bundle.serialize()
    assert isinstance(data, bytes)
    # Re-parses into an equivalent bundle.
    again = bundle.__class__.deserialize(data)
    assert again.artifact_digest == bundle.artifact_digest
    assert again.timestamp.to_dict() == bundle.timestamp.to_dict()


def test_adapter_with_signer_attaches_signature() -> None:
    provider = LocalRfc3161LikeProvider()
    adapter = ManifestEvidenceAdapter(provider, signer=provider)
    manifest = {"signed": True}
    bundle = adapter.seal(json.dumps(manifest).encode("utf-8"))
    assert bundle.signature is not None
    _out, verified = adapter.open(bundle)
    assert verified is True
