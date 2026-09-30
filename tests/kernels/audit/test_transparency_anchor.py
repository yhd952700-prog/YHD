"""Tests for the operator-independent transparency-log anchor (U51 external anchor).

These prove the anchor log is fail-closed WITHOUT touching the live HC-01 chain:
a published notary root is anchored in a signed, append-only log held by an
independent key; the receipt verifies; a tampered root / forged signature /
broken chain linkage / sequence gap is rejected; a tampered persisted file is
refused on load; and the anchor composes with the U51 chain-head notary.
"""
from __future__ import annotations

import json
import tempfile

import pytest

from src.kernels.audit.inclusion_proof import ChainHeadNotary, head_digest_of
from src.kernels.audit.root_of_trust import ChainRootOfTrust, generate_keypair
from src.kernels.audit.transparency_anchor import (
    AnchorLogEntry,
    AnchorReceipt,
    TransparencyAnchorLog,
)


def _fresh_log():
    return TransparencyAnchorLog()


def _anchor_a_root(log, root_hash="a" * 64):
    return log.publish(root_hash)


def test_anchor_publish_and_verify_round_trip():
    log = _fresh_log()
    receipt = _anchor_a_root(log, "deadbeef" * 8)
    ok, reason = log.verify_receipt(receipt, "deadbeef" * 8)
    assert ok, reason
    # The receipt's entry is the only one and is genesis (prev == zeros).
    assert receipt.entry.seq == 0
    assert receipt.prev_entry is None
    assert log.size == 1


def test_anchor_tampered_root_rejected():
    log = _fresh_log()
    receipt = _anchor_a_root(log, "deadbeef" * 8)
    ok, _ = log.verify_receipt(receipt, "cafebabe" * 8)
    assert not ok


def test_anchor_forged_signature_rejected():
    log = _fresh_log()
    receipt = _anchor_a_root(log, "deadbeef" * 8)
    # Flip the signature so it no longer validates against the anchor key.
    bad = AnchorReceipt(
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
    ok, _ = log.verify_receipt(bad, "deadbeef" * 8)
    assert not ok


def test_anchor_consistency_detects_tampered_entry():
    log = _fresh_log()
    _anchor_a_root(log, "a" * 64)
    _anchor_a_root(log, "b" * 64)
    # Tamper the first entry's anchored root AFTER publish (digest now mismatches).
    log._entries[0].notary_root_hash = "z" * 64
    ok, _ = log.verify_consistency()
    assert not ok


def test_anchor_consistency_detects_broken_linkage():
    log = _fresh_log()
    e0 = log.publish("a" * 64).entry
    e1 = log.publish("b" * 64).entry
    assert e1.prev == e0.digest
    # Break the linkage: point e1.prev at the wrong digest.
    e1.prev = "f" * 64
    ok, reason = log.verify_consistency()
    assert not ok
    assert "prev linkage" in reason


def test_anchor_consistency_detects_sequence_gap():
    log = _fresh_log()
    log.publish("a" * 64)
    e1 = log.publish("b" * 64).entry
    # Duplicate seq 0 (gap-at-0 semantics): make e1.seq == 0 too.
    e1.seq = 0
    ok, _ = log.verify_consistency()
    assert not ok


def test_anchor_non_genesis_receipt_requires_predecessor():
    log = _fresh_log()
    _anchor_a_root(log, "a" * 64)
    receipt = _anchor_a_root(log, "b" * 64)
    # Drop the predecessor from the receipt; verification must refuse.
    stripped = AnchorReceipt(entry=receipt.entry, prev_entry=None)
    ok, reason = log.verify_receipt(stripped, "b" * 64)
    assert not ok
    assert "predecessor" in reason


def test_anchor_save_load_round_trip():
    log = _fresh_log()
    _anchor_a_root(log, "a" * 64)
    _anchor_a_root(log, "b" * 64)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as fh:
        path = fh.name
    log.save(path)
    loaded = TransparencyAnchorLog.load(path, anchor_pub=log.anchor_public_pem)
    assert loaded.size == 2
    ok, reason = loaded.verify_consistency()
    assert ok, reason
    # The loaded log still verifies a receipt.
    ok, _ = loaded.verify_receipt(
        AnchorReceipt(entry=loaded.entries[1], prev_entry=loaded.entries[0]),
        "b" * 64,
    )
    assert ok


def test_anchor_load_refuses_tampered_file():
    log = _fresh_log()
    _anchor_a_root(log, "a" * 64)
    _anchor_a_root(log, "b" * 64)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as fh:
        path = fh.name
    log.save(path)
    # Rewrite the file with a tampered anchored root on the first entry.
    with open(path, "r", encoding="utf-8") as fh:
        lines = fh.readlines()
    obj = json.loads(lines[0])
    obj["notary_root_hash"] = "z" * 64
    lines[0] = json.dumps(obj) + "\n"
    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(lines)
    with pytest.raises(ValueError):
        TransparencyAnchorLog.load(path, anchor_pub=log.anchor_public_pem)


def test_anchor_composes_with_u51_notary():
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
    assert ok, reason
    # And a tampered notary root must not verify against the receipt.
    ok, _ = anchor.verify_receipt(receipt, "c" * 64)
    assert not ok
