"""D26 — unit tests for the timestamp authority abstraction.

Covers:
  * ``stamp()`` adds a ``timestamp`` field to the manifest (schema extension).
  * the LOCAL fallback runs fully OFFLINE (no network) and produces a VERIFIABLE
    local token (``verify_manifest`` round-trips).
  * the RFC3161 / TPM backends are INTERFACE-ONLY and refuse to stamp in phase 1.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "timestamp_authority.py"

_spec = importlib.util.spec_from_file_location("timestamp_authority_under_test", SCRIPT)
mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = mod  # required before exec_module so decorators (dataclass) resolve
_spec.loader.exec_module(mod)


SAMPLE_MANIFEST = {
    "artifact": "audit_store.original.sha256",
    "algorithm": "sha256",
    "sha256": "deadbeef" * 8,
    "size_bytes": 12345,
}


def test_stamp_adds_timestamp_field() -> None:
    authority = mod.LocalTimestampAuthority(key=b"test-key")
    stamped = authority.stamp(json.dumps(SAMPLE_MANIFEST).encode("utf-8"))
    assert "timestamp" in stamped
    ts = stamped["timestamp"]
    assert set(ts.keys()) == {"ts", "source", "token", "alg"}
    assert ts["source"] == "local"
    assert ts["alg"] == "hmac-sha256"
    assert ts["token"].startswith("hmac-sha256:")
    # The original manifest content is preserved.
    assert stamped["sha256"] == SAMPLE_MANIFEST["sha256"]
    assert stamped["size_bytes"] == SAMPLE_MANIFEST["size_bytes"]


def test_module_level_stamp_adds_timestamp() -> None:
    stamped = mod.stamp(json.dumps(SAMPLE_MANIFEST).encode("utf-8"))
    assert stamped["timestamp"]["source"] == "local"


def test_local_token_verifiable_offline() -> None:
    """The local fallback must verify its own token without any network."""
    authority = mod.LocalTimestampAuthority(key=b"stable-key")
    manifest_bytes = json.dumps(SAMPLE_MANIFEST).encode("utf-8")
    stamped = authority.stamp(manifest_bytes)
    assert mod.verify_manifest(stamped, manifest_bytes, authority=authority) is True


def test_local_token_binding_is_tamper_evident() -> None:
    authority = mod.LocalTimestampAuthority(key=b"stable-key")
    manifest_bytes = json.dumps(SAMPLE_MANIFEST).encode("utf-8")
    stamped = authority.stamp(manifest_bytes)
    # Changing the manifest bytes must break verification.
    tampered = json.dumps({**SAMPLE_MANIFEST, "size_bytes": 99999}).encode("utf-8")
    assert mod.verify_manifest(stamped, tampered, authority=authority) is False
    # A different key must not verify.
    other = mod.LocalTimestampAuthority(key=b"different-key")
    assert other.verify(manifest_bytes, stamped) is False


def test_rfc3161_is_interface_only() -> None:
    authority = mod.Rfc3161TimestampAuthority(tsa_url="https://example.invalid/tsa")
    with pytest.raises(NotImplementedError):
        authority.stamp(json.dumps(SAMPLE_MANIFEST).encode("utf-8"))


def test_tpm_is_interface_only() -> None:
    authority = mod.TpmTimestampAuthority(tpm_handle="0x81000001")
    with pytest.raises(NotImplementedError):
        authority.stamp(json.dumps(SAMPLE_MANIFEST).encode("utf-8"))


def test_local_authority_uses_injected_key() -> None:
    a1 = mod.LocalTimestampAuthority(key=b"k")
    a2 = mod.LocalTimestampAuthority(key=b"k")
    mb = json.dumps(SAMPLE_MANIFEST).encode("utf-8")
    s1 = a1.stamp(mb)
    # Two authorities with the same explicit key verify each other.
    assert a2.verify(mb, s1) is True
