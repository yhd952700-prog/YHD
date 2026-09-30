"""Tests for ``src.kernels/audit/root_of_trust`` -- the U40/U51/U53 Root of Trust primitive.

These prove the primitive's fail-closed contract WITHOUT touching the live
HC-01 chain: a signed head verifies, a tampered head fails, a wrong key fails,
an unknown sig_alg fails, and an unconfigured instance refuses to sign (opt-in).
"""
from __future__ import annotations

from src.kernels.audit.dual_signature import DEFAULT_SIGN_ALG
from src.kernels.audit.root_of_trust import (
    ChainRootOfTrust,
    RootOfTrustAttestation,
    generate_keypair,
)


def _configured() -> tuple[ChainRootOfTrust, object]:
    kp = generate_keypair()
    return (
        ChainRootOfTrust(private_pem=kp.private_pem, public_pem=kp.public_pem),
        kp,
    )


def test_round_trip_ok() -> None:
    rot, kp = _configured()
    att = rot.sign_head(seq=42, head_hash="h" * 64, link_hash="l" * 64)
    ok, reason = rot.verify_attestation(att)
    assert ok, reason
    assert att.sig_alg == DEFAULT_SIGN_ALG
    assert att.key_id == kp.key_id


def test_tampered_head_fails() -> None:
    rot, _ = _configured()
    att = rot.sign_head(seq=42, head_hash="h" * 64, link_hash="l" * 64)
    tampered = RootOfTrustAttestation(
        sig_alg=att.sig_alg,
        key_id=att.key_id,
        signature=att.signature,
        covers_seq=att.covers_seq,
        covers_hash="X" * 64,
        covers_link_hash=att.covers_link_hash,
    )
    ok, reason = rot.verify_attestation(tampered)
    assert ok is False
    assert "tampered" in reason or "signature" in reason


def test_wrong_key_fails() -> None:
    rot, _ = _configured()
    att = rot.sign_head(seq=1, head_hash="h" * 64, link_hash="l" * 64)
    other = generate_keypair()
    rot_other = ChainRootOfTrust(
        private_pem=other.private_pem, public_pem=other.public_pem
    )
    ok, reason = rot_other.verify_attestation(att)
    assert ok is False
    assert "key_id" in reason or "signature" in reason


def test_unknown_sig_alg_fails() -> None:
    rot, _ = _configured()
    att = rot.sign_head(seq=1, head_hash="h" * 64, link_hash="l" * 64)
    bad = RootOfTrustAttestation(
        sig_alg="BOGUS-ALG",
        key_id=att.key_id,
        signature=att.signature,
        covers_seq=att.covers_seq,
        covers_hash=att.covers_hash,
        covers_link_hash=att.covers_link_hash,
    )
    ok, reason = rot.verify_attestation(bad)
    assert ok is False
    assert "unknown sig_alg" in reason


def test_unconfigured_sign_raises() -> None:
    rot = ChainRootOfTrust(private_pem=None, public_pem=None)
    assert rot.configured is False
    raised = False
    try:
        rot.sign_head(seq=1, head_hash="h", link_hash="l")
    except RuntimeError:
        raised = True
    assert raised, "expected RuntimeError when Root of Trust is not configured"


def test_from_env_unconfigured_is_opt_in() -> None:
    rot = ChainRootOfTrust.from_env()
    assert rot.configured is False
