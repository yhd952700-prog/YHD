"""Tests for ``src.kernels/audit/root_of_trust`` -- the U40/U51/U53 Root of Trust primitive.

These prove the primitive's fail-closed contract WITHOUT touching the live
HC-01 chain: a signed head verifies, a tampered head fails, a wrong key fails,
an unknown sig_alg fails, and an unconfigured instance refuses to sign (opt-in).
"""
from __future__ import annotations

from src.kernels.audit.dual_signature import DEFAULT_SIGN_ALG, key_id_of
from src.kernels.audit.root_of_trust import (
    ChainRootOfTrust,
    KeyRing,
    RootOfTrustAttestation,
    generate_keypair,
)
from src.kernels.audit.trusted_timestamp import (
    TrustedTimestampAuthority,
    generate_test_tsa_keypair,
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


def _two_key_ring() -> tuple:
    kp_a = generate_keypair()
    kp_b = generate_keypair()
    ring = KeyRing(
        {
            key_id_of(kp_a.public_pem): kp_a.public_pem,
            key_id_of(kp_b.public_pem): kp_b.public_pem,
        }
    )
    return kp_a, kp_b, ring


def test_rotation_historical_attestation_still_verifies() -> None:
    kp_a, kp_b, ring = _two_key_ring()
    # Key A is active; signs an attestation.
    rot_a = ChainRootOfTrust(private_pem=kp_a.private_pem, key_ring=ring)
    att_a = rot_a.sign_head(seq=7, head_hash="h" * 64, link_hash="l" * 64)
    assert rot_a.verify_attestation(att_a)[0]
    # Now key B becomes active (rotation). The ring still trusts BOTH keys.
    rot_b = ChainRootOfTrust(private_pem=kp_b.private_pem, key_ring=ring)
    att_b = rot_b.sign_head(seq=8, head_hash="h" * 64, link_hash="l" * 64)
    # New attestation by B verifies under the rotated signer.
    assert rot_b.verify_attestation(att_b)[0]
    # The OLD attestation by A STILL verifies (rotation-safe, no orphaning).
    ok, reason = rot_b.verify_attestation(att_a)
    assert ok, reason


def test_rotation_key_rotated_out_fails() -> None:
    kp_a, kp_b, ring = _two_key_ring()
    # Sign with A under the full ring.
    rot_a = ChainRootOfTrust(private_pem=kp_a.private_pem, key_ring=ring)
    att_a = rot_a.sign_head(seq=9, head_hash="h" * 64, link_hash="l" * 64)
    # Verify with a ring that has rotated A OUT (only B trusted).
    ring_b = KeyRing({key_id_of(kp_b.public_pem): kp_b.public_pem})
    rot_b = ChainRootOfTrust(private_pem=kp_b.private_pem, key_ring=ring_b)
    ok, reason = rot_b.verify_attestation(att_a)
    assert ok is False
    assert "not in trusted key ring" in reason


def test_signing_key_not_in_ring_is_refused() -> None:
    kp_a, kp_b, _ = _two_key_ring()
    # A's private key but a ring that trusts only B -> misconfiguration.
    ring_b = KeyRing({key_id_of(kp_b.public_pem): kp_b.public_pem})
    rot = ChainRootOfTrust(private_pem=kp_a.private_pem, key_ring=ring_b)
    raised = False
    try:
        rot.sign_head(seq=1, head_hash="h" * 64, link_hash="l" * 64)
    except RuntimeError:
        raised = True
    assert raised, "signed with a key absent from the trusted ring (fail-closed broken)"


def test_timestamp_attached_and_verified() -> None:
    import shutil

    if shutil.which("openssl") is None:
        import pytest

        pytest.skip("openssl ts engine not available")
    signer_key, signer_cert, ca_cert = generate_test_tsa_keypair()
    tsa = TrustedTimestampAuthority(
        tsa_cert_pem=ca_cert,
        signer_cert_pem=signer_cert,
        signer_key_pem=signer_key,
    )
    kp = generate_keypair()
    rot = ChainRootOfTrust(
        private_pem=kp.private_pem, public_pem=kp.public_pem, tsa=tsa
    )
    att = rot.sign_head(seq=11, head_hash="h" * 64, link_hash="l" * 64)
    assert att.tsa_token is not None, "timestamp not attached"
    assert att.tsa_gen_time is not None
    assert rot.verify_attestation(att)[0]
    ok, reason = rot.verify_timestamp(att)
    assert ok, reason


def test_timestamp_tamper_fails() -> None:
    import shutil

    if shutil.which("openssl") is None:
        import pytest

        pytest.skip("openssl ts engine not available")
    signer_key, signer_cert, ca_cert = generate_test_tsa_keypair()
    tsa = TrustedTimestampAuthority(
        tsa_cert_pem=ca_cert,
        signer_cert_pem=signer_cert,
        signer_key_pem=signer_key,
    )
    kp = generate_keypair()
    rot = ChainRootOfTrust(
        private_pem=kp.private_pem, public_pem=kp.public_pem, tsa=tsa
    )
    att = rot.sign_head(seq=12, head_hash="h" * 64, link_hash="l" * 64)
    # Tamper the covered hash but keep the (valid-for-old-payload) token.
    tampered = RootOfTrustAttestation(
        sig_alg=att.sig_alg,
        key_id=att.key_id,
        signature=att.signature,
        covers_seq=att.covers_seq,
        covers_hash="Z" * 64,
        covers_link_hash=att.covers_link_hash,
        tsa_token=att.tsa_token,
        tsa_gen_time=att.tsa_gen_time,
        tsa_cert_id=att.tsa_cert_id,
    )
    ok, _ = rot.verify_timestamp(tampered)
    assert ok is False


def test_timestamp_present_but_no_authority_fails() -> None:
    import shutil

    if shutil.which("openssl") is None:
        import pytest

        pytest.skip("openssl ts engine not available")
    signer_key, signer_cert, ca_cert = generate_test_tsa_keypair()
    tsa = TrustedTimestampAuthority(
        tsa_cert_pem=ca_cert,
        signer_cert_pem=signer_cert,
        signer_key_pem=signer_key,
    )
    kp = generate_keypair()
    rot = ChainRootOfTrust(
        private_pem=kp.private_pem, public_pem=kp.public_pem, tsa=tsa
    )
    att = rot.sign_head(seq=13, head_hash="h" * 64, link_hash="l" * 64)
    # A RoT instance with NO TSA configured must reject a timestamped att.
    rot_no_tsa = ChainRootOfTrust(private_pem=kp.private_pem, public_pem=kp.public_pem)
    ok, _ = rot_no_tsa.verify_timestamp(att)
    assert ok is False
