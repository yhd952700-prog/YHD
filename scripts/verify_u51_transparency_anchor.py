#!/usr/bin/env python3
"""verify_u51_transparency_anchor.py -- CI gate for the transparency-log anchor (U51 external anchor).

U51 (no single value commits its own history => cheap notarization/inclusion-proof
infeasible): the chain-head notary (inclusion_proof.py) gives every historical
signed head a trustless inclusion proof against the notary's PUBLISHED ROOT. But
that root -- and the notary file -- lives under the operator's control, so an
operator could rewrite the root itself. This gate proves the transparency-log
anchor ('src/kernels/audit/transparency_anchor.py') actually works -- it is not a
no-op. It is a SELF-TEST of the primitive: a published notary root is committed
to an operator-independent, signed, append-only log; the receipt verifies; a
tampered root / forged signature / broken chain linkage / tampered persisted file
is rejected. It does NOT assert the live HC-01 chain is anchored in production
(that integration is the next step, gated on HC-01 stabilisation); it asserts the
capability EXISTS and is fail-closed.

Exit 0 = capability verified. Exit 1 = a capability claim failed.
"""
from __future__ import annotations

import json
import sys
import pathlib
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))  # required by tests/test_guardrail_scripts.py

from src.kernels.audit.inclusion_proof import (  # noqa: E402
    ChainHeadNotary,
    head_digest_of,
)
from src.kernels.audit.root_of_trust import (  # noqa: E402
    ChainRootOfTrust,
    generate_keypair,
)
from src.kernels.audit.transparency_anchor import (  # noqa: E402
    AnchorReceipt,
    TransparencyAnchorLog,
)


def _fail(msg: str) -> None:
    print("U51 ANCHOR GATE FAIL: " + msg)
    sys.exit(1)


def main() -> int:
    # 1. Compose U51 with the anchor: notary publishes a root; the anchor log
    #    commits it under an independent key; the receipt verifies.
    kp = generate_keypair()
    rot = ChainRootOfTrust(private_pem=kp.private_pem, public_pem=kp.public_pem)
    notary = ChainHeadNotary(checkpoint_interval=3)
    for seq in range(5):
        att = rot.sign_head(seq=seq, head_hash="h" * 64, link_hash="l" * 64)
        notary.append_head(head_digest_of(att))
    root = notary.publish_root()
    anchor = TransparencyAnchorLog()
    receipt = anchor.publish(root.root_hash)
    ok, reason = anchor.verify_receipt(receipt, root.root_hash)
    if not ok:
        _fail("anchor receipt failed verification: %s" % reason)

    # 2. Tampered root must not verify against the receipt.
    ok, _ = anchor.verify_receipt(receipt, "c" * 64)
    if ok:
        _fail("tampered notary root verified against the anchor receipt (fail-closed broken)")

    # 3. Forged signature must not verify.
    from src.kernels.audit.transparency_anchor import AnchorLogEntry  # noqa: E402

    forged = AnchorReceipt(
        entry=AnchorLogEntry(
            seq=receipt.entry.seq,
            prev=receipt.entry.prev,
            notary_root_hash=receipt.entry.notary_root_hash,
            gen_time=receipt.entry.gen_time,
            sig_alg=receipt.entry.sig_alg,
            key_id=receipt.entry.key_id,
            signature="A" + receipt.entry.signature[1:],
            digest=receipt.entry.digest,
        ),
        prev_entry=receipt.prev_entry,
    )
    ok, _ = anchor.verify_receipt(forged, root.root_hash)
    if ok:
        _fail("forged anchor signature verified (fail-closed broken)")

    # 4. Consistency must detect a tampered entry in the log.
    anchor._entries[0].notary_root_hash = "z" * 64
    ok, _ = anchor.verify_consistency()
    if ok:
        _fail("consistency check accepted a tampered anchor entry (fail-closed broken)")

    # 5. Clean save/load round-trip; a tampered persisted file must be refused.
    clean = TransparencyAnchorLog()
    clean.publish("a" * 64)
    r1 = clean.publish("b" * 64)
    ok, _ = clean.verify_receipt(r1, "b" * 64)
    if not ok:
        _fail("fresh anchor receipt failed verification")
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    tmp.close()
    clean.save(tmp.name)
    loaded = TransparencyAnchorLog.load(tmp.name, anchor_pub=clean.anchor_public_pem)
    ok, _ = loaded.verify_consistency()
    if not ok:
        _fail("loaded anchor log failed its own consistency check")

    # Rewrite the file with a tampered anchored root -> load must refuse.
    with open(tmp.name, "r", encoding="utf-8") as fh:
        lines = fh.readlines()
    obj = json.loads(lines[0])
    obj["notary_root_hash"] = "z" * 64
    lines[0] = json.dumps(obj) + "\n"
    with open(tmp.name, "w", encoding="utf-8") as fh:
        fh.writelines(lines)
    refused = False
    try:
        TransparencyAnchorLog.load(tmp.name, anchor_pub=clean.anchor_public_pem)
    except ValueError:
        refused = True
    if not refused:
        _fail("load accepted a tampered anchor file (fail-closed broken)")

    print(
        "U51 ANCHOR OK: transparency-log anchor verified -- anchor a published "
        "notary root under an independent key, receipt verifies, tampered root "
        "rejected, forged signature rejected, consistency detects tampered entry, "
        "clean save/load round-trip, and a tampered persisted file is refused."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
