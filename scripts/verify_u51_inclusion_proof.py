#!/usr/bin/env python3
"""verify_u51_inclusion_proof.py -- CI gate for the chain-head notary (U51).

U51 (no single value commits its own history => cheap notarization/inclusion-proof
infeasible): this gate proves the evidence-grade inclusion-proof capability in
``src/kernels/audit/inclusion_proof.py`` actually works -- it is not a no-op. It
is a SELF-TEST of the primitive (Merkle inclusion proof round-trip, tamper
rejection, uncheckpointed-head refusal, tampered-persisted-file refusal, and the
U53+U51 composition where a published root is itself RoT-attested). It does NOT
assert the live HC-01 chain is notarised in production (that integration is the
next step, gated on HC-01 stabilisation); it asserts the capability EXISTS and is
fail-closed.

Exit 0 = capability verified. Exit 1 = a capability claim failed.
"""
from __future__ import annotations

import sys
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))  # required by tests/test_guardrail_scripts.py

from src.kernels.audit.inclusion_proof import (  # noqa: E402
    ChainHeadNotary,
    PublishedRoot,
    head_digest_of,
)
from src.kernels.audit.root_of_trust import (  # noqa: E402
    ChainRootOfTrust,
    generate_keypair,
)


def _fail(msg: str) -> None:
    print("U51 NOTARY GATE FAIL: " + msg)
    sys.exit(1)


def main() -> int:
    # 1. Compose U51 with U53: each signed head yields a digest that goes into
    #    the notary; the published root is itself RoT-attested (production
    #    anchoring). Prove a historical signed head is included in the timeline.
    kp = generate_keypair()
    rot = ChainRootOfTrust(private_pem=kp.private_pem, public_pem=kp.public_pem)
    notary = ChainHeadNotary(checkpoint_interval=4)
    digests = []
    for seq in range(7):
        att = rot.sign_head(seq=seq, head_hash="h" * 64, link_hash="l" * 64)
        digest = head_digest_of(att)
        if len(digest) != 32:
            _fail("head_digest_of did not produce a 32-byte digest")
        notary.append_head(digest)
        digests.append(digest)
    root = notary.publish_root()
    if root.leaf_count != 7:
        _fail("published root leaf_count wrong: %d" % root.leaf_count)
    # Verify inclusion of the EARLIEST head (the whole point: cheap proof that a
    # historical signed head is in the canonical timeline).
    proof = notary.prove_inclusion(0)
    ok, reason = notary.verify_inclusion(digests[0], proof)
    if not ok:
        _fail("inclusion proof for historical head failed: %s" % reason)
    # And a middle head.
    proof_mid = notary.prove_inclusion(5)
    ok, reason = notary.verify_inclusion(digests[5], proof_mid)
    if not ok:
        _fail("inclusion proof for mid head failed: %s" % reason)

    # 2. Tamper rejection: a different digest must NOT verify against the proof.
    forged = bytes((b + 1) % 256 for b in digests[0])
    ok, _ = notary.verify_inclusion(forged, proof)
    if ok:
        _fail("tampered head verified (fail-closed broken)")

    # 3. Uncheckpointed head cannot be proven (fail-closed: never claim
    #    inclusion of a head that has not been checkpointed).
    fresh = ChainHeadNotary()
    fresh.append_head(b"a" * 32)
    raised = False
    try:
        fresh.prove_inclusion(0)
    except ValueError:
        raised = True
    if not raised:
        _fail("uncheckpointed head was provable (fail-closed broken)")

    # 4. Tampered persisted file must be refused on load (fail-closed).
    import json
    import tempfile

    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    tmp.close()
    notary.save(tmp.name)
    with open(tmp.name, "r", encoding="utf-8") as fh:
        lines = fh.readlines()
    for i, line in enumerate(lines):
        obj = json.loads(line)
        if obj.get("t") == "root":
            obj["root_hash"] = "ab" * 32
            lines[i] = json.dumps(obj) + "\n"
            break
    with open(tmp.name, "w", encoding="utf-8") as fh:
        fh.writelines(lines)
    refused = False
    try:
        ChainHeadNotary.load(tmp.name)
    except ValueError:
        refused = True
    if not refused:
        _fail("load accepted a tampered notary file (fail-closed broken)")

    # 5. Self-consistency: a forged recorded root is detected by re-computation.
    notary2 = ChainHeadNotary()
    for seq in range(4):
        att = rot.sign_head(seq=100 + seq, head_hash="h" * 64, link_hash="l" * 64)
        notary2.append_head(head_digest_of(att))
    notary2.publish_root()
    ok, _ = notary2.verify_self_consistency()
    if not ok:
        _fail("fresh notary failed its own consistency check")
    # Forge a recorded root hash.
    forged_root = notary2.published_root(0)
    notary2._roots[0] = PublishedRoot(
        root_index=forged_root.root_index,
        leaf_count=forged_root.leaf_count,
        root_hash="00" * 32,
        provenance=forged_root.provenance,
    )
    ok, reason = notary2.verify_self_consistency()
    if ok or "mismatch" not in reason:
        _fail("forged root not detected by self-consistency check")

    # 6. U53+U51 composition: the published root is itself RoT-attested
    #    (provenance), proving the two evidence layers stack.
    root_att = rot.sign_head(
        seq=root.root_index, head_hash=root.root_hash, link_hash=root.root_hash
    )
    ok, reason = rot.verify_attestation(root_att)
    if not ok:
        _fail("RoT attestation of the published root failed: %s" % reason)

    print(
        "U51 OK: chain-head notary verified -- Merkle inclusion proof round-trip "
        "(historical + mid head), tampered head rejected, uncheckpointed head "
        "refused, tampered persisted file refused, forged root detected by "
        "self-consistency, and the published root is RoT-attested (U53+U51)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
