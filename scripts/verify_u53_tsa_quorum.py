#!/usr/bin/env python3
"""verify_u53_tsa_quorum.py -- CI gate for the K-of-M quorum TSA (U53 high availability).

U53 (no one signs the chain head, and a single TSA is a SPOF): this gate proves
the quorum timestamp layer in ``src/kernels/audit/trusted_timestamp.py`` actually
works -- it is not a no-op. It is a SELF-TEST of the primitive:

  * a 3-member / quorum-2 authority mints a bundle and verifies (>= threshold
    DISTINCT members attest the same payload);
  * falling below quorum rejects (fail-closed);
  * a token from an UNKNOWN / untrusted TSA is ignored (never trusted);
  * a tampered payload fails every member;
  * a member outage is tolerated (unless require_full);
  * the quorum bundle composes with the U53 Root-of-Trust attestation
    end-to-end and is fail-closed against tamper.

It does NOT assert the live HC-01 chain is timestamped in production (that
integration is the next step, gated on HC-01 stabilisation); it asserts the
capability EXISTS and is fail-closed.

Exit 0 = capability verified. Exit 1 = a capability claim failed.
"""
from __future__ import annotations

import base64
import secrets
import shutil
import sys
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))  # required by tests/test_guardrail_scripts.py

from src.kernels.audit.root_of_trust import (  # noqa: E402
    ChainRootOfTrust,
    generate_keypair,
)
from src.kernels.audit.trusted_timestamp import (  # noqa: E402
    QuorumTimestampAuthority,
    TimestampTokenBundle,
    TrustedTimestampAuthority,
    generate_test_tsa_keypair,
)


def _fail(msg: str) -> None:
    print("U53 QUORUM GATE FAIL: " + msg)
    sys.exit(1)


def _member() -> TrustedTimestampAuthority:
    """A distinct, fully-operational TSA member (own CA + leaf)."""
    key, leaf, ca = generate_test_tsa_keypair()
    return TrustedTimestampAuthority(
        tsa_cert_pem=ca, signer_cert_pem=leaf, signer_key_pem=key
    )


def _down_member() -> TrustedTimestampAuthority:
    """A TSA member that is UNREACHABLE / misconfigured (no signer key).

    ``request_token`` on it raises, simulating an outage -- used to prove the
    quorum tolerates member loss.
    """
    key, leaf, ca = generate_test_tsa_keypair()
    return TrustedTimestampAuthority(tsa_cert_pem=ca, signer_cert_pem=leaf)


def main() -> int:
    if shutil.which("openssl") is None:
        print(
            "U53 QUORUM NOTE: openssl ts engine not available -- quorum "
            "assertions skipped (not fabricated)."
        )
        return 0

    # 1. Mint + verify a quorum bundle (3 members, quorum 2).
    m1, m2, m3 = _member(), _member(), _member()
    quorum = QuorumTimestampAuthority([m1, m2, m3], quorum=2)
    data = secrets.token_bytes(32)
    bundle = quorum.request_token(data)
    if len(bundle.tokens) != 3:
        _fail("quorum mint produced %d tokens, expected 3" % len(bundle.tokens))
    ok, reason = quorum.verify_bundle(bundle, data)
    if not ok:
        _fail("quorum bundle failed verification: %s" % reason)

    # 2. Below-threshold rejection: a bundle carrying only 1 token must fail.
    thin = TimestampTokenBundle(
        threshold=2, members=2, tokens=bundle.tokens[:1]
    )
    ok, _ = quorum.verify_bundle(thin, data)
    if ok:
        _fail("below-threshold bundle verified (fail-closed broken)")

    # 3. Unknown-TSA token ignored: a bundle whose only token comes from an
    #    untrusted TSA must be rejected (never trusted).
    bogus = TimestampTokenBundle(
        threshold=1,
        members=1,
        tokens=[
            {
                "tsa_token": base64.b64encode(b"dummy").decode("ascii"),
                "tsa_gen_time": None,
                "tsa_cert_id": "unknown-tsa-not-in-member-set",
            }
        ],
    )
    ok, _ = quorum.verify_bundle(bogus, data)
    if ok:
        _fail("bundle from unknown TSA verified (fail-closed broken)")

    # 4. Tampered payload rejected: the same bundle over DIFFERENT data fails.
    other = secrets.token_bytes(32)
    ok, _ = quorum.verify_bundle(bundle, other)
    if ok:
        _fail("quorum bundle verified against a tampered payload (fail-closed broken)")

    # 5. Member outage tolerated: one member down, quorum still collectable.
    down = _down_member()
    tolerant = QuorumTimestampAuthority([m1, m2, down], quorum=2)
    bundle_out = tolerant.request_token(data)
    if len(bundle_out.tokens) != 2:
        _fail("outage-tolerant mint collected %d tokens, expected 2" % len(bundle_out.tokens))
    ok, reason = tolerant.verify_bundle(bundle_out, data)
    if not ok:
        _fail("quorum with one member down failed verification: %s" % reason)

    # 6. require_full fails when a member is down (strict availability policy).
    raised = False
    try:
        tolerant.request_token(data, require_full=True)
    except RuntimeError:
        raised = True
    if not raised:
        _fail("require_full did not raise when a member was down (policy broken)")

    # 7. Composition with U53 Root-of-Trust: sign a head through the quorum TSA,
    #    then verify the resulting attestation's timestamp bundle end-to-end.
    kp = generate_keypair()
    rot = ChainRootOfTrust(
        private_pem=kp.private_pem, public_pem=kp.public_pem, tsa=quorum
    )
    att = rot.sign_head(seq=700, head_hash="h" * 64, link_hash="l" * 64)
    if att.tsa_bundle is None:
        _fail("RoT attestation was not stamped by the configured quorum TSA")
    ok, reason = rot.verify_timestamp(att)
    if not ok:
        _fail("quorum-stamped RoT attestation failed verification: %s" % reason)

    # 8. Tamper the composed attestation's payload -> the bundle must reject.
    from src.kernels.audit.root_of_trust import RootOfTrustAttestation

    tampered = RootOfTrustAttestation(
        sig_alg=att.sig_alg,
        key_id=att.key_id,
        signature=att.signature,
        covers_seq=att.covers_seq,
        covers_hash="Z" * 64,  # changed
        covers_link_hash=att.covers_link_hash,
        tsa_bundle=att.tsa_bundle,
    )
    ok, _ = rot.verify_timestamp(tampered)
    if ok:
        _fail("tampered quorum-stamped attestation passed verification (fail-closed broken)")

    print(
        "U53 QUORUM OK: K-of-M TSA verified -- 3-member/quorum-2 mint+verify, "
        "below-threshold rejected, unknown-TSA token ignored, tampered payload "
        "rejected, single-member outage tolerated (require_full still fails), "
        "and the quorum bundle composes with the U53 RoT attestation "
        "fail-closed against tamper."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
