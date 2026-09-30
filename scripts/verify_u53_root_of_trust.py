#!/usr/bin/env python3
"""verify_u53_root_of_trust.py -- CI gate for the Root-of-Trust chain-head signing primitive (U53).

U53 (no one signs the chain head): this gate proves the Root-of-Trust primitive
in ``src/kernels/audit/root_of_trust.py`` actually works -- it is not a
no-op. It is a SELF-TEST of the primitive (sign + verify round trip, tamper
rejection, wrong-key rejection, unknown-algorithm fail-closed, opt-in
unconfigured). It does NOT assert that the live HC-01 chain is signed in
production (that integration is the next step, gated on HC-01 stabilisation);
it asserts that the capability EXISTS and is fail-closed.

Exit 0 = primitive verified. Exit 1 = a capability claim failed.
"""
from __future__ import annotations

import sys
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))  # required by tests/test_guardrail_scripts.py

from src.kernels.audit.root_of_trust import (  # noqa: E402
    ChainRootOfTrust,
    KeyRing,
    RootOfTrustAttestation,
    generate_keypair,
    key_id_of,
)
from src.kernels.audit.trusted_timestamp import (  # noqa: E402
    TrustedTimestampAuthority,
    generate_test_tsa_keypair,
)


def _fail(msg: str) -> None:
    print("U53 RoT GATE FAIL: " + msg)
    sys.exit(1)


def main() -> int:
    # 1. Round-trip: sign a head with a generated key, verify it.
    kp = generate_keypair()
    rot = ChainRootOfTrust(private_pem=kp.private_pem, public_pem=kp.public_pem)
    att = rot.sign_head(seq=100, head_hash="h" * 64, link_hash="l" * 64)
    ok, reason = rot.verify_attestation(att)
    if not ok:
        _fail("round-trip verify failed: %s" % reason)

    # 2. Tamper rejection: a changed hash must fail (not silently pass).
    tampered = RootOfTrustAttestation(
        sig_alg=att.sig_alg,
        key_id=att.key_id,
        signature=att.signature,
        covers_seq=att.covers_seq,
        covers_hash="X" * 64,
        covers_link_hash=att.covers_link_hash,
    )
    ok, _ = rot.verify_attestation(tampered)
    if ok:
        _fail("tampered head verified (fail-closed broken)")

    # 3. Wrong-key rejection: signed by key A, verified by key B must fail.
    other = generate_keypair()
    rot_other = ChainRootOfTrust(
        private_pem=other.private_pem, public_pem=other.public_pem
    )
    ok, _ = rot_other.verify_attestation(att)
    if ok:
        _fail("attestation from a different key verified (key-id check broken)")

    # 4. Unknown sig_alg must fail-closed (no default fallback).
    bad = RootOfTrustAttestation(
        sig_alg="BOGUS-ALG",
        key_id=att.key_id,
        signature=att.signature,
        covers_seq=att.covers_seq,
        covers_hash=att.covers_hash,
        covers_link_hash=att.covers_link_hash,
    )
    ok, reason = rot.verify_attestation(bad)
    if ok or "unknown sig_alg" not in reason:
        _fail("unknown sig_alg not rejected fail-closed: %s" % reason)

    # 5. Opt-in: an unconfigured instance refuses to sign (cannot silently skip).
    rot_none = ChainRootOfTrust(private_pem=None, public_pem=None)
    if rot_none.configured:
        _fail("unconfigured instance reported configured")
    raised = False
    try:
        rot_none.sign_head(seq=1, head_hash="h", link_hash="l")
    except RuntimeError:
        raised = True
    if not raised:
        _fail("unconfigured instance signed without a key (opt-in broken)")

    print(
        "U53 OK: Root-of-Trust primitive verified -- sign/verify round-trip, "
        "tampered rejected, wrong-key rejected, unknown sig_alg fail-closed, "
        "opt-in unconfigured refuses to sign."
    )

    # 6. Rotation safety: an attestation signed by key A must STILL verify after
    #    key B becomes the active signer, so long as the trusted ring retains A.
    #    (A chain that orphans historical attestations on rotation is unsafe.)
    kp_a = generate_keypair()
    kp_b = generate_keypair()
    ring_ab = KeyRing(
        {
            key_id_of(kp_a.public_pem): kp_a.public_pem,
            key_id_of(kp_b.public_pem): kp_b.public_pem,
        }
    )
    rot_a = ChainRootOfTrust(private_pem=kp_a.private_pem, key_ring=ring_ab)
    att_a = rot_a.sign_head(seq=200, head_hash="h" * 64, link_hash="l" * 64)
    rot_b = ChainRootOfTrust(private_pem=kp_b.private_pem, key_ring=ring_ab)
    ok, reason = rot_b.verify_attestation(att_a)
    if not ok:
        _fail("historical attestation (key A) failed after rotation to B: %s" % reason)
    # And a key fully rotated OUT must no longer verify.
    ring_b = KeyRing({key_id_of(kp_b.public_pem): kp_b.public_pem})
    rot_b_only = ChainRootOfTrust(private_pem=kp_b.private_pem, key_ring=ring_b)
    ok, _ = rot_b_only.verify_attestation(att_a)
    if ok:
        _fail("attestation from rotated-out key A still verified (rotation failed-closed)")

    # 7. RFC 3161 trusted timestamp (evidence-grade time binding). Requires the
    #    system openssl ts engine; skip the assertion if it is unavailable but
    #    NEVER fabricate a pass.
    import shutil

    if shutil.which("openssl") is not None:
        signer_key, signer_cert, ca_cert = generate_test_tsa_keypair()
        tsa = TrustedTimestampAuthority(
            tsa_cert_pem=ca_cert,
            signer_cert_pem=signer_cert,
            signer_key_pem=signer_key,
        )
        rot_ts = ChainRootOfTrust(
            private_pem=kp.private_pem, public_pem=kp.public_pem, tsa=tsa
        )
        att_ts = rot_ts.sign_head(seq=300, head_hash="h" * 64, link_hash="l" * 64)
        if att_ts.tsa_token is None:
            _fail("RoT attestation was not timestamped by the configured TSA")
        ok, reason = rot_ts.verify_timestamp(att_ts)
        if not ok:
            _fail("RFC 3161 timestamp failed verification: %s" % reason)
        # Tampered payload must break the timestamp binding.
        tampered_ts = RootOfTrustAttestation(
            sig_alg=att_ts.sig_alg,
            key_id=att_ts.key_id,
            signature=att_ts.signature,
            covers_seq=att_ts.covers_seq,
            covers_hash="Z" * 64,
            covers_link_hash=att_ts.covers_link_hash,
            tsa_token=att_ts.tsa_token,
            tsa_gen_time=att_ts.tsa_gen_time,
            tsa_cert_id=att_ts.tsa_cert_id,
        )
        ok, _ = rot_ts.verify_timestamp(tampered_ts)
        if ok:
            _fail("tampered payload passed RFC 3161 timestamp verification")
        print(
            "U53 OK+: rotation-safe key ring + RFC 3161 trusted timestamp "
            "verified (timestamp present, verifies, tamper rejected)."
        )
    else:
        print(
            "U53 NOTE: openssl ts engine not available -- RFC 3161 timestamp "
            "assertion skipped (not fabricated)."
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
