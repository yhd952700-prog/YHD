"""Chain-Head Notary: trustless inclusion proofs over signed audit heads (U51).

Context -- why this module exists
---------------------------------
U40/U53 give the audit chain head a *signed* (Root-of-Trust) and *timestamped*
(RFC 3161) attestation. Together they prove *attribution* and *time*: "this head
was signed by the authority that owns the chain, at this trusted time." They do
NOT prove *position*: an external auditor who holds a signed head cannot cheaply
prove that head is part of the canonical, contiguous historical sequence without
replaying the entire chain (U51's root cause: "no single value committing its own
history => cheap notarization/inclusion-proof infeasible").

This module closes that gap with an **append-only Merkle accumulator** over the
sequence of signed-head digests:

  * Every signed head yields a 32-byte ``head_digest`` (sha256 of its canonical
    record). The notary appends it as a Merkle leaf.
  * Periodically the notary *publishes* a **notary root** = Merkle root over all
    leaves accumulated so far. (In production that root is itself signed +
    timestamped by the Root of Trust -- see the integration note below -- so the
    root is anchored to the authority and time too.)
  * An auditor holding any historical signed head can request an **inclusion
    proof**: the Merkle path from that leaf to a published root. Verifying the
    proof against the published root proves the head was committed into the
    canonical timeline at that checkpoint -- *without trusting the operator and
    without downloading the whole chain*. That is exactly U51's "cheap inclusion
    proof".

Because every published root commits (via the Merkle structure) to all prior
heads, each head now "commits its own history": the root cause of U51 is removed.

Fail-closed guarantees
----------------------
  * Merkle inclusion verification recomputes the root from the leaf + siblings
    and compares to the *published* root; any tampered leaf, tampered sibling,
    or wrong root returns ``False`` -- never a silent accept.
  * A published root is ALWAYS recomputed by the notary from its leaves; an
    externally-supplied root is never trusted.
  * ``save``/``load`` are fail-closed: on load the notary recomputes every
    recorded root from its re-derived leaves and refuses (raises) to load a file
    whose recorded root does not match -- a corrupted or forged notary file
    cannot be silently trusted.
  * Leaves must be 32-byte sha256 digests; a malformed leaf is rejected.

This module is storage-agnostic and independent of the live HC-01 chain: it
operates on abstract head digests, so it is testable without touching the frozen
HC-01 data. The live integration (persist each published root inside the append
transaction, sign it via ``ChainRootOfTrust``) is the next step after HC-01 is
stabilised -- the same pattern as the U53 primitive.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .dual_signature import canonical_bytes
from .root_of_trust import RootOfTrustAttestation


# --------------------------------------------------------------------------- #
# Pure Merkle primitives (sha256, deterministic, fail-closed)
# --------------------------------------------------------------------------- #

def _pair_hash(left: bytes, right: bytes) -> bytes:
    """sha256(left || right)."""
    return hashlib.sha256(left + right).digest()


def _validate_leaf(leaf: bytes) -> None:
    if not isinstance(leaf, bytes) or len(leaf) != 32:
        raise ValueError("merkle leaves must be 32-byte sha256 digests")


def merkle_root(leaves: List[bytes]) -> bytes:
    """Compute the Merkle root over ``leaves``.

    Odd levels are resolved by duplicating the last node (deterministic, matches
    the proof construction). A single leaf has root == the leaf itself.
    """
    if not leaves:
        raise ValueError("merkle_root: empty leaf set")
    for lf in leaves:
        _validate_leaf(lf)
    level: List[bytes] = list(leaves)
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])  # duplicate last for odd count
        level = [
            _pair_hash(level[i], level[i + 1])
            for i in range(0, len(level), 2)
        ]
    return level[0]


def merkle_proof(leaves: List[bytes], index: int, root_hash: Optional[bytes] = None) -> "InclusionProof":
    """Build an inclusion proof for ``leaves[index]``.

    ``root_hash`` is the published root the proof binds to (informational; the
    verifier still checks against the *trusted* published root, not this field).
    """
    if not leaves:
        raise ValueError("merkle_proof: empty leaf set")
    if index < 0 or index >= len(leaves):
        raise IndexError("leaf index out of range: %d" % index)
    for lf in leaves:
        _validate_leaf(lf)

    level: List[bytes] = list(leaves)
    idx = index
    siblings: List[bytes] = []
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])  # duplicate last for odd count
        if idx % 2 == 1:
            sib = level[idx - 1]
        else:
            # even index: sibling is the right node (or self, when odd-dup last)
            sib = level[idx + 1] if idx + 1 < len(level) else level[idx]
        siblings.append(sib)
        next_level: List[bytes] = []
        for i in range(0, len(level), 2):
            next_level.append(_pair_hash(level[i], level[i + 1]))
        idx = idx // 2
        level = next_level

    root = root_hash if root_hash is not None else merkle_root(leaves)
    return InclusionProof(leaf_index=index, siblings=siblings, root=root)


def verify_merkle_proof(
    leaf: bytes, proof: "InclusionProof", expected_root: bytes
) -> bool:
    """Fail-closed verification: recompute the root from ``leaf`` + siblings.

    ``expected_root`` is the TRUSTED published root (not the proof's embedded
    ``root`` field). Returns ``False`` on any tampered leaf, tampered sibling,
    or mismatch -- never raises for a bad proof (caller distinguishes).
    """
    try:
        _validate_leaf(leaf)
        if len(proof.siblings) == 0:
            # single-leaf tree: root is the leaf itself
            return leaf == expected_root
        h = leaf
        idx = proof.leaf_index
        for depth, sib in enumerate(proof.siblings):
            if not isinstance(sib, bytes) or len(sib) != 32:
                return False
            if (idx >> depth) & 1 == 1:
                h = _pair_hash(sib, h)  # sibling on the left
            else:
                h = _pair_hash(h, sib)  # sibling on the right
        return h == expected_root
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Inclusion proof record
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class InclusionProof:
    """A Merkle inclusion proof for one leaf against a published root.

    ``root`` is informational (the published root the proof was generated
    against); trusted verification always passes an explicit ``expected_root``
    from the notary's own published-root records.
    """

    leaf_index: int
    siblings: List[bytes]
    root: bytes

    def verify(self, leaf: bytes, expected_root: bytes) -> bool:
        """Convenience: verify this proof for ``leaf`` against a trusted root."""
        return verify_merkle_proof(leaf, self, expected_root)

    def to_record(self) -> dict:
        return {
            "leaf_index": self.leaf_index,
            "siblings": [s.hex() for s in self.siblings],
            "root": self.root.hex(),
        }

    @classmethod
    def from_record(cls, d: dict) -> "InclusionProof":
        return cls(
            leaf_index=int(d["leaf_index"]),
            siblings=[bytes.fromhex(s) for s in d["siblings"]],
            root=bytes.fromhex(d["root"]),
        )


@dataclass(frozen=True)
class PublishedRoot:
    """A notary root published at a point in the accumulator's history.

    ``root_hash`` is the hex Merkle root over the first ``leaf_count`` leaves.
    ``provenance`` optionally carries the Root-of-Trust attestation that signed
    this root in production (U53), making the root itself authority-anchored.
    """

    root_index: int
    leaf_count: int
    root_hash: str  # hex
    provenance: Optional[dict] = None

    def to_record(self) -> dict:
        return {
            "root_index": self.root_index,
            "leaf_count": self.leaf_count,
            "root_hash": self.root_hash,
            "provenance": self.provenance,
        }

    @classmethod
    def from_record(cls, d: dict) -> "PublishedRoot":
        return cls(
            root_index=int(d["root_index"]),
            leaf_count=int(d["leaf_count"]),
            root_hash=str(d["root_hash"]),
            provenance=d.get("provenance"),
        )


# --------------------------------------------------------------------------- #
# Chain-Head Notary (append-only Merkle accumulator)
# --------------------------------------------------------------------------- #

class ChainHeadNotary:
    """Accumulate signed-head digests into an append-only Merkle timeline.

    The notary maintains an in-memory (and optionally persisted) list of head
    digests plus a list of published roots. It is fail-closed (see module doc):
    roots are always recomputed, inclusion proofs bind to trusted roots, and a
    tampered persisted file is refused on load.
    """

    def __init__(self, checkpoint_interval: int = 64):
        if checkpoint_interval < 1:
            raise ValueError("checkpoint_interval must be >= 1")
        self._leaves: List[bytes] = []
        self._roots: List[PublishedRoot] = []
        self._checkpoint_interval = checkpoint_interval

    # -- accumulation ------------------------------------------------------- #

    @property
    def leaf_count(self) -> int:
        return len(self._leaves)

    @property
    def published_root_count(self) -> int:
        return len(self._roots)

    @property
    def checkpoint_interval(self) -> int:
        return self._checkpoint_interval

    def append_head(self, head_digest: bytes) -> int:
        """Append a signed-head digest. Returns its leaf index.

        Raises on a malformed (non-32-byte) digest -- the notary only accepts
        real sha256 digests, so it cannot be fed garbage.
        """
        _validate_leaf(head_digest)
        idx = len(self._leaves)
        self._leaves.append(head_digest)
        return idx

    # -- publication -------------------------------------------------------- #

    def publish_root(self, provenance: Optional[dict] = None) -> PublishedRoot:
        """Publish a Merkle root over ALL accumulated leaves (recomputed).

        The root is always recomputed from the leaves -- an externally supplied
        root is never trusted. ``provenance`` is an optional U53 attestation
        record binding this root to the authority (production anchoring).
        """
        if not self._leaves:
            raise ValueError("cannot publish a root over zero leaves")
        root = merkle_root(self._leaves)
        rec = PublishedRoot(
            root_index=len(self._roots),
            leaf_count=len(self._leaves),
            root_hash=root.hex(),
            provenance=provenance,
        )
        self._roots.append(rec)
        return rec

    def latest_published_root(self) -> Optional[PublishedRoot]:
        return self._roots[-1] if self._roots else None

    def published_root(self, root_index: int) -> PublishedRoot:
        return self._roots[root_index]

    # -- proof / verify (fail-closed) --------------------------------------- #

    def prove_inclusion(
        self, leaf_index: int, root_index: Optional[int] = None
    ) -> InclusionProof:
        """Produce an inclusion proof for ``leaf_index`` against a published root.

        ``root_index`` selects the published root (defaults to the latest). The
        chosen root MUST cover ``leaf_index`` (have ``leaf_count > leaf_index``),
        otherwise this raises -- a head that has not yet been checkpointed cannot
        be proven (fail-closed: we never claim inclusion of an uncheckpointed
        head).
        """
        if leaf_index < 0 or leaf_index >= len(self._leaves):
            raise IndexError("leaf index out of range: %d" % leaf_index)
        root = (
            self._roots[root_index]
            if root_index is not None
            else self.latest_published_root()
        )
        if root is None:
            raise ValueError("no published root; cannot prove inclusion")
        if leaf_index >= root.leaf_count:
            raise ValueError(
                "leaf %d not covered by published root %d (covers %d leaves)"
                % (leaf_index, root.root_index, root.leaf_count)
            )
        return merkle_proof(
            self._leaves, leaf_index, root_hash=bytes.fromhex(root.root_hash)
        )

    def resolve_trusted_root(self, proof: InclusionProof) -> Optional[bytes]:
        """Return the published root bytes matching the proof's declared root.

        Returns ``None`` if the proof binds to a root the notary never published
        (a forged/tampered proof) -- the caller must treat ``None`` as a reject.
        """
        target = proof.root.hex()
        for r in self._roots:
            if r.root_hash == target:
                return bytes.fromhex(r.root_hash)
        return None

    def verify_inclusion(self, leaf: bytes, proof: InclusionProof) -> Tuple[bool, str]:
        """Fail-closed inclusion verification of ``leaf`` under ``proof``.

        The proof is only trusted if it binds to a root this notary actually
        published; then the Merkle proof is checked against that trusted root.
        """
        trusted = self.resolve_trusted_root(proof)
        if trusted is None:
            return False, "proof binds to an unknown/unpublished root (forged?)"
        if proof.leaf_index >= self.leaf_count:
            return False, "proof leaf_index out of range"
        # confirm the trusted root actually covers this leaf
        covers = any(
            r.root_hash == proof.root.hex() and proof.leaf_index < r.leaf_count
            for r in self._roots
        )
        if not covers:
            return False, "published root does not cover this leaf index"
        ok = verify_merkle_proof(leaf, proof, trusted)
        if not ok:
            return False, "merkle inclusion proof failed (tampered leaf or sibling)"
        return True, "ok"

    def verify_self_consistency(self) -> Tuple[bool, str]:
        """Recompute every published root from the leaves and confirm a match.

        Catches a corrupted/forged recorded root (the recorded hash disagrees
        with what the leaves actually produce). Fail-closed: a mismatch is
        reported, not hidden.
        """
        for r in self._roots:
            if r.leaf_count < 0 or r.leaf_count > len(self._leaves):
                return False, "published root %d leaf_count out of range" % r.root_index
            actual = merkle_root(self._leaves[: r.leaf_count]).hex()
            if actual != r.root_hash:
                return (
                    False,
                    "published root %d mismatch (recorded %s, recomputed %s)"
                    % (r.root_index, r.root_hash, actual),
                )
        return True, "ok"

    # -- persistence (fail-closed load) ------------------------------------- #

    def save(self, path: str) -> None:
        """Persist leaves + published roots to a JSONL file (append-friendly)."""
        # Atomic-ish rewrite: write leaves then roots in order.
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            for i, leaf in enumerate(self._leaves):
                fh.write(
                    json.dumps({"t": "leaf", "i": i, "h": leaf.hex()}) + "\n"
                )
            for r in self._roots:
                fh.write(json.dumps({"t": "root", **r.to_record()}) + "\n")
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: str, checkpoint_interval: int = 64) -> "ChainHeadNotary":
        """Load a notary file. Fail-closed: recompute every root and refuse a
        file whose recorded root disagrees with the re-derived leaves."""
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        notary = cls(checkpoint_interval=checkpoint_interval)
        leaves: List[bytes] = []
        roots: List[PublishedRoot] = []
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                if obj["t"] == "leaf":
                    leaves.append(bytes.fromhex(obj["h"]))
                elif obj["t"] == "root":
                    roots.append(PublishedRoot.from_record(obj))
                else:
                    raise ValueError("unknown notary record type: %r" % obj.get("t"))
        # Recompute-and-refuse: every recorded root must match the leaves.
        for r in roots:
            if r.leaf_count > len(leaves):
                raise ValueError(
                    "corrupted notary file: root %d leaf_count %d > leaves %d"
                    % (r.root_index, r.leaf_count, len(leaves))
                )
            actual = merkle_root(leaves[: r.leaf_count]).hex()
            if actual != r.root_hash:
                raise ValueError(
                    "refusing to load tampered notary file: published root %d "
                    "recorded %s but leaves recompute %s"
                    % (r.root_index, r.root_hash, actual)
                )
        notary._leaves = leaves
        notary._roots = roots
        return notary


# --------------------------------------------------------------------------- #
# Integration helper: derive a stable head digest from a U53 attestation
# --------------------------------------------------------------------------- #

def head_digest_of(att: RootOfTrustAttestation) -> bytes:
    """Deterministic 32-byte digest of a signed audit-head attestation.

    This is the value a notary leaf should carry: it commits to the exact
    attested head (seq + hashes) plus the authority/algorithm/key provenance,
    so an inclusion proof proves "this signed head was in the canonical
    timeline" rather than merely "some 32 bytes were included".
    """
    return hashlib.sha256(canonical_bytes(att.to_record())).digest()
