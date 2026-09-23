"""Independent verification layer for P0-7 B2 dual-signature `sig_alg`.

These pytest assertions are the SECOND layer of QA (the runtime probe in
scripts/ is the first). They must pass in CI's `pip install -e .` environment,
where `cryptography` is a declared dependency.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.kernels.audit.dual_signature import (  # noqa: E402
    DEFAULT_SIGN_ALG,
    DualSignatureStore,
    SignatureRecord,
    SIGN_ALGORITHMS,
    canonical_bytes,
    create_dual_signature_evidence,
    verify_signature_record,
)


@pytest.fixture()
def db_path(tmp_path):
    p = str(tmp_path / "ds.db")
    os.environ["DUAL_SIGNATURE_DB_PATH"] = p
    return p


def test_sig_alg_registry_has_rsa3072():
    assert DEFAULT_SIGN_ALG == "RS256-RSA3072"
    assert "RS256-RSA3072" in SIGN_ALGORITHMS


def test_create_bundle_records_sig_alg(db_path):
    store = DualSignatureStore(db_path)
    b = create_dual_signature_evidence(
        principal_id="human:bob",
        action="capability.retire",
        reason="test",
        verifier_id="verifier:carol",
        correlation_id="corr-unit-1",
        store=store,
    )
    assert b.human_record.sig_alg == "RS256-RSA3072"
    assert b.verifier_record.sig_alg == "RS256-RSA3072"
    assert b.human_record.role == "human-override-assertion"
    assert b.verifier_record.role == "verifier-confirmation"
    store.close()


def test_verify_uses_declared_alg(db_path):
    store = DualSignatureStore(db_path)
    b = create_dual_signature_evidence(
        principal_id="human:bob",
        action="capability.retire",
        reason="test",
        verifier_id="verifier:carol",
        correlation_id="corr-unit-2",
        store=store,
    )
    store.close()
    ok, actual = verify_signature_record(b.human_record, b.human_public_pem)
    assert ok is True
    assert actual == b.human_record.sig_alg == "RS256-RSA3072"


def test_unknown_sig_alg_fails_closed_no_fallback(db_path):
    store = DualSignatureStore(db_path)
    b = create_dual_signature_evidence(
        principal_id="human:bob",
        action="capability.retire",
        reason="test",
        verifier_id="verifier:carol",
        correlation_id="corr-unit-3",
        store=store,
    )
    store.close()
    # Genuine RSA-3072 bytes, but declared algorithm is unknown.
    forged = SignatureRecord(
        record_id="x", role=b.human_record.role, sig_alg="RS999-UNKNOWN",
        key_id=b.human_record.key_id, claims=b.human_record.claims,
        signature=b.human_record.signature, signed_at=b.human_record.signed_at,
    )
    ok, actual = verify_signature_record(forged, b.human_public_pem)
    assert ok is False
    assert actual == "unknown"  # rejected, NOT verified with default


def test_tampered_claim_fails(db_path):
    store = DualSignatureStore(db_path)
    b = create_dual_signature_evidence(
        principal_id="human:bob",
        action="capability.retire",
        reason="test",
        verifier_id="verifier:carol",
        correlation_id="corr-unit-4",
        store=store,
    )
    store.close()
    claims = dict(b.human_record.claims)
    claims["principal_id"] = "human:eve"
    tampered = SignatureRecord(
        record_id=b.human_record.record_id, role=b.human_record.role,
        sig_alg=b.human_record.sig_alg, key_id=b.human_record.key_id,
        claims=claims, signature=b.human_record.signature,
        signed_at=b.human_record.signed_at,
    )
    ok, _ = verify_signature_record(tampered, b.human_public_pem)
    assert ok is False


def test_canonical_is_stable():
    payload = {"b": 1, "a": 2, "c": [3, 1]}
    assert canonical_bytes(payload) == canonical_bytes(dict(payload))
