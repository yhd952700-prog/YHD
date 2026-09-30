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
    RootOfTrustAttestation,
    generate_keypair,
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
