"""Tests for ``src.kernels/audit/inclusion_proof`` -- the U51 chain-head notary.

These prove the fail-closed contract WITHOUT touching the live HC-01 chain:
a valid inclusion proof verifies, tampered leaves / siblings / indices / roots
are rejected, an uncheckpointed head cannot be proven, and a tampered persisted
notary file is refused on load. The final test shows the notary composes with
the U53 Root-of-Trust attestation (head_digest_of).
"""
from __future__ import annotations

import json
import os

from src.kernels.audit.inclusion_proof import (
    ChainHeadNotary,
    InclusionProof,
    PublishedRoot,
    head_digest_of,
    merkle_proof,
    merkle_root,
    verify_merkle_proof,
)
from src.kernels.audit.root_of_trust import ChainRootOfTrust, generate_keypair


def _leaves(n: int):
    return [os.urandom(32) for _ in range(n)]


def test_merkle_root_deterministic_and_order_sensitive() -> None:
    leaves = _leaves(7)
    r1 = merkle_root(leaves)
    assert isinstance(r1, bytes) and len(r1) == 32
    # same input -> identical root
    assert merkle_root(list(leaves)) == r1
    # reordered input -> different root
    reordered = list(leaves)
    reordered[0], reordered[-1] = reordered[-1], reordered[0]
    assert merkle_root(reordered) != r1


def test_merkle_root_single_leaf_is_self() -> None:
    leaf = os.urandom(32)
    assert merkle_root([leaf]) == leaf


def test_merkle_proof_valid() -> None:
    leaves = _leaves(8)
    root = merkle_root(leaves)
    for idx in (0, 3, 7):
        proof = merkle_proof(leaves, idx)
        assert proof.leaf_index == idx
        assert verify_merkle_proof(leaves[idx], proof, root) is True


def test_merkle_proof_tampered_leaf_fails() -> None:
    leaves = _leaves(8)
    root = merkle_root(leaves)
    proof = merkle_proof(leaves, 2)
    tampered = bytes((b + 1) % 256 for b in leaves[2])
    assert verify_merkle_proof(tampered, proof, root) is False


def test_merkle_proof_tampered_sibling_fails() -> None:
    leaves = _leaves(8)
    root = merkle_root(leaves)
    proof = merkle_proof(leaves, 4)
    bad_sib = list(proof.siblings)
    bad_sib[0] = bytes((b + 1) % 256 for b in bad_sib[0])
    bad = InclusionProof(leaf_index=proof.leaf_index, siblings=bad_sib, root=proof.root)
    assert verify_merkle_proof(leaves[4], bad, root) is False


def test_merkle_proof_wrong_root_fails() -> None:
    leaves = _leaves(8)
    proof = merkle_proof(leaves, 1)
    wrong = bytes(32)  # not the real root
    assert verify_merkle_proof(leaves[1], proof, wrong) is False


def test_merkle_proof_tampered_index_fails() -> None:
    leaves = _leaves(8)
    root = merkle_root(leaves)
    proof = merkle_proof(leaves, 5)
    # Forging a different leaf_index changes the sibling-side bit pattern;
    # the recomputed hash must not match the trusted root.
    forged = InclusionProof(
        leaf_index=0, siblings=list(proof.siblings), root=proof.root
    )
    assert verify_merkle_proof(leaves[5], forged, root) is False


def test_notary_append_and_prove_inclusion() -> None:
    notary = ChainHeadNotary(checkpoint_interval=4)
    leaves = _leaves(10)
    for lf in leaves:
        notary.append_head(lf)
    root = notary.publish_root()
    assert root.leaf_count == 10
    assert root.root_hash == merkle_root(leaves).hex()
    for idx in (0, 5, 9):
        proof = notary.prove_inclusion(idx)
        ok, reason = notary.verify_inclusion(leaves[idx], proof)
        assert ok, reason


def test_notary_uncheckpointed_head_cannot_be_proven() -> None:
    notary = ChainHeadNotary()
    idx = notary.append_head(os.urandom(32))
    assert idx == 0
    raised = False
    try:
        notary.prove_inclusion(0)
    except ValueError:
        raised = True
    assert raised, "uncheckpointed head must NOT be provable (fail-closed)"


def test_notary_verify_self_consistency_detects_tampered_root() -> None:
    notary = ChainHeadNotary()
    for lf in _leaves(6):
        notary.append_head(lf)
    notary.publish_root()
    ok, _ = notary.verify_self_consistency()
    assert ok
    # Forge a recorded root hash (simulating persistence corruption).
    tampered = notary.published_root(0)
    notary._roots[0] = PublishedRoot(
        root_index=tampered.root_index,
        leaf_count=tampered.leaf_count,
        root_hash="00" * 32,
        provenance=tampered.provenance,
    )
    ok, reason = notary.verify_self_consistency()
    assert ok is False
    assert "mismatch" in reason


def test_notary_load_tampered_file_refused(tmp_path) -> None:
    notary = ChainHeadNotary()
    for lf in _leaves(5):
        notary.append_head(lf)
    notary.publish_root()
    path = str(tmp_path / "notary.jsonl")
    notary.save(path)
    # Corrupt the recorded root hash in the file.
    with open(path, "r", encoding="utf-8") as fh:
        lines = fh.readlines()
    for i, line in enumerate(lines):
        obj = json.loads(line)
        if obj.get("t") == "root":
            obj["root_hash"] = "ff" * 32
            lines[i] = json.dumps(obj) + "\n"
            break
    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(lines)
    raised = False
    try:
        ChainHeadNotary.load(path)
    except ValueError:
        raised = True
    assert raised, "load must refuse a tampered notary file (fail-closed)"


def test_notary_load_clean_round_trip(tmp_path) -> None:
    notary = ChainHeadNotary(checkpoint_interval=3)
    leaves = _leaves(9)
    for lf in leaves:
        notary.append_head(lf)
    notary.publish_root(provenance={"note": "signed-by-rot"})
    path = str(tmp_path / "notary.jsonl")
    notary.save(path)
    loaded = ChainHeadNotary.load(path)
    assert loaded.leaf_count == 9
    assert loaded.published_root_count == 1
    assert loaded.latest_published_root().provenance == {"note": "signed-by-rot"}
    ok, _ = loaded.verify_self_consistency()
    assert ok
    # a proven inclusion on the loaded notary still verifies
    proof = loaded.prove_inclusion(4)
    ok2, reason = loaded.verify_inclusion(leaves[4], proof)
    assert ok2, reason


def test_integration_head_digest_of_attestation() -> None:
    # U53 signed head -> deterministic digest -> notary inclusion proof.
    kp = generate_keypair()
    rot = ChainRootOfTrust(private_pem=kp.private_pem, public_pem=kp.public_pem)
    notary = ChainHeadNotary()
    digests = []
    for seq in range(5):
        att = rot.sign_head(
            seq=seq, head_hash="h" * 64, link_hash="l" * 64,
        )
        digest = head_digest_of(att)
        assert len(digest) == 32
        digests.append(digest)
        notary.append_head(digest)
    root = notary.publish_root()
    # verify the whole chain from the notary side
    for seq, digest in enumerate(digests):
        proof = notary.prove_inclusion(seq)
        ok, reason = notary.verify_inclusion(digest, proof)
        assert ok, reason
    assert root.leaf_count == 5


def test_notary_verify_rejects_unknown_root() -> None:
    notary = ChainHeadNotary()
    leaves = _leaves(4)
    for lf in leaves:
        notary.append_head(lf)
    notary.publish_root()
    # Craft a proof whose declared root never matched a published root.
    proof = InclusionProof(
        leaf_index=0, siblings=[], root=b"deadbeef" * 4
    )
    ok, reason = notary.verify_inclusion(leaves[0], proof)
    assert ok is False
    assert "unknown" in reason or "forged" in reason
