"""Tests for the K-of-M quorum TSA (``QuorumTimestampAuthority``, U53 HA).

These prove the quorum layer is fail-closed WITHOUT touching the live HC-01
chain: a 3-member / quorum-2 authority mints + verifies; falling below quorum
rejects; a token from an unknown TSA is ignored; a tampered payload fails every
member; a member outage is tolerated (unless require_full); and the quorum
bundle composes with the U53 Root-of-Trust attestation end-to-end.
"""
from __future__ import annotations

import shutil

import pytest

from src.kernels.audit.root_of_trust import ChainRootOfTrust, generate_keypair
from src.kernels.audit.trusted_timestamp import (
    QuorumTimestampAuthority,
    TimestampToken,
    TimestampTokenBundle,
    TrustedTimestampAuthority,
    generate_test_tsa_keypair,
)

pytestmark = pytest.mark.skipif(
    shutil.which("openssl") is None, reason="openssl ts engine not available"
)


def _member(kp):
    signer_key, signer_cert, ca_cert = kp
    return TrustedTimestampAuthority(
        tsa_cert_pem=ca_cert,
        signer_cert_pem=signer_cert,
        signer_key_pem=signer_key,
    )


class _DownTSA:
    """Stub member that is unreachable (simulates a TSA outage)."""

    _cert_id = "down-member"
    cert_id = "down-member"

    def request_token(self, data):
        raise RuntimeError("TSA unreachable (simulated outage)")

    def verify_token(self, token, data):
        return False, "down"


@pytest.fixture(scope="module")
def members():
    kps = [generate_test_tsa_keypair() for _ in range(3)]
    return [_member(kp) for kp in kps]


def test_quorum_mint_and_verify_ok(members) -> None:
    q = QuorumTimestampAuthority(members, quorum=2)
    data = b"attestation-payload"
    bundle = q.request_token(data)
    assert isinstance(bundle, TimestampTokenBundle)
    assert bundle.members == 3
    assert bundle.threshold == 2
    assert len(bundle.tokens) == 3
    ok, reason = q.verify_bundle(bundle, data)
    assert ok, reason


def test_quorum_below_threshold_rejected(members) -> None:
    q = QuorumTimestampAuthority(members, quorum=2)
    data = b"attestation-payload"
    full = q.request_token(data)
    # Keep only ONE token but keep threshold=2 -> must be rejected.
    reduced = TimestampTokenBundle(
        threshold=2, members=3, tokens=[full.tokens[0]]
    )
    ok, reason = q.verify_bundle(reduced, data)
    assert ok is False
    assert "threshold" in reason


def test_quorum_unknown_tsa_token_ignored(members) -> None:
    q = QuorumTimestampAuthority(members, quorum=2)
    data = b"attestation-payload"
    real = q.request_token(data)
    # Forge an EXTRA token whose cert_id is not a member of the quorum.
    forged = TimestampToken(
        token_der=b"\x30\x05\x02\x03\x01\x00\x00",  # garbage, would fail anyway
        gen_time=None,
        tsa_cert_id="not-a-member-cert",
    )
    mixed = TimestampTokenBundle(
        threshold=2, members=3, tokens=[real.tokens[0], forged.to_record()]
    )
    ok, reason = q.verify_bundle(mixed, data)
    assert ok is False
    assert "verified" in reason


def test_quorum_tampered_payload_rejected(members) -> None:
    q = QuorumTimestampAuthority(members, quorum=2)
    bundle = q.request_token(b"good-payload")
    # Verify against a DIFFERENT payload -> every member's token fails.
    ok, reason = q.verify_bundle(bundle, b"tampered-payload")
    assert ok is False
    assert "verified" in reason


def test_quorum_member_outage_tolerated() -> None:
    good1 = _member(generate_test_tsa_keypair())
    good2 = _member(generate_test_tsa_keypair())
    q = QuorumTimestampAuthority([good1, good2, _DownTSA()], quorum=2)
    data = b"payload"
    bundle = q.request_token(data, require_full=False)
    assert len(bundle.tokens) == 2
    ok, reason = q.verify_bundle(bundle, data)
    assert ok, reason


def test_quorum_require_full_fails_when_member_down() -> None:
    good1 = _member(generate_test_tsa_keypair())
    good2 = _member(generate_test_tsa_keypair())
    q = QuorumTimestampAuthority([good1, good2, _DownTSA()], quorum=2)
    raised = False
    try:
        q.request_token(b"payload", require_full=True)
    except RuntimeError:
        raised = True
    assert raised, "require_full should raise when a member is down"


def test_quorum_bundle_round_trip_record(members) -> None:
    q = QuorumTimestampAuthority(members, quorum=2)
    bundle = q.request_token(b"x")
    rec = bundle.to_record()
    restored = TimestampTokenBundle.from_record(rec)
    assert restored.threshold == bundle.threshold
    assert restored.members == bundle.members
    assert len(restored.tokens) == len(bundle.tokens)
    ok, _ = q.verify_bundle(restored, b"x")
    assert ok


def test_quorum_integration_with_root_of_trust(members) -> None:
    # U53 signed attestation now carries a quorum timestamp bundle.
    kp = generate_keypair()
    rot = ChainRootOfTrust(
        private_pem=kp.private_pem, public_pem=kp.public_pem, tsa=QuorumTimestampAuthority(members, quorum=2)
    )
    att = rot.sign_head(seq=1, head_hash="h" * 64, link_hash="l" * 64)
    assert att.tsa_bundle is not None, "quorum timestamp bundle not attached"
    assert att.tsa_token is not None  # primary token retained for backward-compat
    assert rot.verify_attestation(att)[0]
    ok, reason = rot.verify_timestamp(att, tsa=QuorumTimestampAuthority(members, quorum=2))
    assert ok, reason
    # Tamper the covered hash -> the quorum timestamp must reject it.
    from src.kernels.audit.root_of_trust import RootOfTrustAttestation

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
        tsa_bundle=att.tsa_bundle,
    )
    ok, _ = rot.verify_timestamp(tampered, tsa=QuorumTimestampAuthority(members, quorum=2))
    assert ok is False
