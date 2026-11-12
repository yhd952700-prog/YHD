"""Transparency-log anchoring for published notary roots (U51 external anchor).

WHY THIS EXISTS
---------------
``inclusion_proof.py`` (U51) gives every historical signed head a trustless
inclusion proof against the notary's PUBLISHED ROOT. But that published root --
and the notary file that holds it -- lives entirely under the operator's control.
An auditor verifying a Merkle inclusion proof is only protected against the
operator *silently rewriting the leaves between two checkpoints*; they are NOT
protected against the operator rewriting the published root ITSELF (and the
surrounding notary file) at the same time, because the root is trusted-by-itself.

A **transparency log** removes that residual trust: each published notary root is
committed to an *operator-independent*, append-only, signed log. The log is a
hash chain where every entry signs (with a key NOT controlled by the audit
operator) its linkage (prev entry hash) + the notary root it anchors. Once a root
is in the log, the operator cannot rewrite it (or drop/reorder it) without
breaking the chain -- and any auditor holding the log's public key + any
checkpoint can detect the break.

DESIGN -- honest, fail-closed, operator-independent, self-contained
---------------------------------------------------------------
  * The anchor log is signed by its OWN key (``anchor_priv``), deliberately
    distinct from the audit operator's Root-of-Trust key. This models an
    independent transparency authority. In production this local independent log
    is replaced by a REAL public log (a Certificate-Transparency-style log,
    OpenTimestamps, or a Bitcoin/blockchain anchor) -- the interface is
    identical, only the anchor key+transport change.
  * Each entry commits (via sha256) to: its sequence number, the previous entry's
    digest, the notary root it anchors, and its own signature. Tampering with a
    root (or a signature, or reordering entries) breaks the chain and is caught
    by ``verify_consistency``.
  * ``verify_receipt`` proves a single published root is correctly signed AND
    chained to a presented predecessor (or genesis). ``verify_consistency``
    recomputes the whole chain from genesis and refuses any forgery.
  * ``save`` / ``load`` are fail-closed: on load every entry's signature and
    linkage is re-verified; a tampered or truncated log file is refused.

This module is storage-agnostic and independently testable. The live HC-01
integration decides *when* to anchor a published root (after HC-01 stabilises);
this code just provides the capability.
"""
from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import json
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .dual_signature import (
    DEFAULT_SIGN_ALG,
    SIGN_ALGORITHMS,
    canonical_bytes,
    key_id_of,
)

_GENESIS_PREV = "0" * 64


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


@dataclass
class AnchorLogEntry:
    """One signed, chained entry in the transparency log.

    Commits to ``notary_root_hash`` plus the chain linkage (``seq`` + ``prev``).
    ``digest`` is the sha256 of the FULL entry (including signature + key_id),
    so any change -- including a re-signed forgery -- changes the next entry's
    expected ``prev`` and breaks the chain.
    """

    seq: int
    prev: str  # sha256 hex of the previous entry's digest (genesis == zeros)
    notary_root_hash: str  # the notary published-root being anchored
    gen_time: str  # ISO-8601 UTC when anchored
    sig_alg: str
    key_id: str
    signature: str  # base64 (ascii)
    digest: str = ""  # computed on construction / load-verify

    def _linkage_payload(self) -> Dict[str, object]:
        """The bytes the anchor key signs (linkage + the anchored root)."""
        return {
            "seq": self.seq,
            "prev": self.prev,
            "notary_root_hash": self.notary_root_hash,
            "gen_time": self.gen_time,
        }

    def _full_payload(self) -> Dict[str, object]:
        """The bytes the digest commits to (linkage + signature + provenance)."""
        p = self._linkage_payload()
        p["sig_alg"] = self.sig_alg
        p["key_id"] = self.key_id
        p["signature"] = self.signature
        return p

    def compute_digest(self) -> str:
        return hashlib.sha256(canonical_bytes(self._full_payload())).hexdigest()

    def to_record(self) -> dict:
        return {
            "seq": self.seq,
            "prev": self.prev,
            "notary_root_hash": self.notary_root_hash,
            "gen_time": self.gen_time,
            "sig_alg": self.sig_alg,
            "key_id": self.key_id,
            "signature": self.signature,
            "digest": self.digest,
        }

    @classmethod
    def from_record(cls, d: dict) -> "AnchorLogEntry":
        return cls(
            seq=int(d["seq"]),
            prev=d["prev"],
            notary_root_hash=d["notary_root_hash"],
            gen_time=d["gen_time"],
            sig_alg=d["sig_alg"],
            key_id=d["key_id"],
            signature=d["signature"],
            digest=d.get("digest", ""),
        )


@dataclass
class AnchorReceipt:
    """A verifiable proof that a notary root was anchored in the log.

    Carries the anchoring entry plus, optionally, the predecessor entry it chains
    to (needed to verify linkage locally without the whole log).
    """

    entry: AnchorLogEntry
    prev_entry: Optional[AnchorLogEntry] = None

    def to_record(self) -> dict:
        return {
            "entry": self.entry.to_record(),
            "prev_entry": self.prev_entry.to_record() if self.prev_entry else None,
        }

    @classmethod
    def from_record(cls, d: dict) -> "AnchorReceipt":
        prev = AnchorLogEntry.from_record(d["prev_entry"]) if d.get("prev_entry") else None
        return cls(entry=AnchorLogEntry.from_record(d["entry"]), prev_entry=prev)


class TransparencyAnchorLog:
    """An operator-independent, signed, append-only log of notary roots.

    Fail-closed: every publish signs with the anchor key; every verify re-checks
    signatures and chain linkage; load refuses a tampered log.
    """

    def __init__(
        self,
        anchor_priv: Optional[bytes] = None,
        anchor_pub: Optional[bytes] = None,
        sig_alg: str = DEFAULT_SIGN_ALG,
    ):
        self._sig_alg = sig_alg
        if anchor_priv is None and anchor_pub is None:
            anchor_priv, anchor_pub = SIGN_ALGORITHMS[self._sig_alg].generate_keypair()
        self._priv = anchor_priv
        self._pub = anchor_pub
        self._key_id = key_id_of(self._pub) if self._pub else ""
        self._entries: List[AnchorLogEntry] = []

    @property
    def anchor_key_id(self) -> str:
        return self._key_id

    @property
    def anchor_public_pem(self) -> Optional[bytes]:
        return self._pub

    @property
    def entries(self) -> List[AnchorLogEntry]:
        return list(self._entries)

    @property
    def size(self) -> int:
        return len(self._entries)

    def publish(self, notary_root_hash: str, gen_time: Optional[str] = None) -> AnchorReceipt:
        """Anchor a published notary root; returns a verifiable receipt.

        Raises if the log is not configured with an anchor key.
        """
        if self._priv is None:
            raise RuntimeError("TransparencyAnchorLog not configured (no anchor key)")
        seq = len(self._entries)
        prev = self._entries[-1].digest if self._entries else _GENESIS_PREV
        gt = gen_time or _now_iso()
        entry = AnchorLogEntry(
            seq=seq,
            prev=prev,
            notary_root_hash=notary_root_hash,
            gen_time=gt,
            sig_alg=self._sig_alg,
            key_id=self._key_id,
            signature="",
            digest="",
        )
        payload = canonical_bytes(entry._linkage_payload())
        sig = SIGN_ALGORITHMS[self._sig_alg].sign(self._priv, payload)
        entry.signature = base64.b64encode(sig).decode("ascii")
        entry.digest = entry.compute_digest()
        self._entries.append(entry)
        return AnchorReceipt(entry=entry, prev_entry=self._entries[-2] if seq > 0 else None)

    def verify_receipt(
        self, receipt: AnchorReceipt, notary_root_hash: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Fail-closed single-receipt verification.

        Checks: (1) the signature verifies against the anchor public key; (2) the
        entry's digest is internally consistent (signature matches the digest);
        (3) the notary root matches (if a value is supplied); (4) the chain
        linkage to the presented predecessor (or genesis) holds.
        """
        if self._pub is None:
            return False, "no anchor public key configured"
        entry = receipt.entry
        alg = SIGN_ALGORITHMS.get(entry.sig_alg)
        if alg is None:
            return False, "unknown sig_alg: %s" % entry.sig_alg
        if entry.key_id != self._key_id:
            return False, "entry key_id does not match the anchor key"
        payload = canonical_bytes(entry._linkage_payload())
        ok = alg.verify(self._pub, payload, base64.b64decode(entry.signature))
        if not ok:
            return False, "anchor signature invalid (entry forged or tampered)"
        if entry.compute_digest() != entry.digest:
            return False, "entry digest mismatch (tampered after signing)"
        # Linkage: genesis expects zeros; otherwise expect the prev entry's digest.
        expected_prev = _GENESIS_PREV
        if receipt.prev_entry is not None:
            if receipt.prev_entry.digest != entry.prev:
                return False, "chain linkage broken (prev entry digest mismatch)"
            expected_prev = receipt.prev_entry.digest
        elif entry.seq > 0:
            return False, "non-genesis entry presented without its predecessor"
        if entry.prev != expected_prev:
            return False, "chain linkage broken (prev hash mismatch)"
        if notary_root_hash is not None and entry.notary_root_hash != notary_root_hash:
            return False, "anchored root does not match the supplied notary root"
        return True, "ok"

    def verify_consistency(self) -> Tuple[bool, str]:
        """Recompute the whole chain from genesis; refuse any forgery.

        Detects: invalid signature on any entry, tampered digest, broken prev
        linkage, and sequence gaps/reordering.
        """
        if self._pub is None:
            return False, "no anchor public key configured"
        alg = SIGN_ALGORITHMS[self._sig_alg]
        prev = _GENESIS_PREV
        for i, entry in enumerate(self._entries):
            if entry.seq != i:
                return False, "sequence gap/reorder at position %d" % i
            if entry.prev != prev:
                return False, "prev linkage mismatch at seq %d" % i
            if entry.key_id != self._key_id:
                return False, "key_id mismatch at seq %d" % i
            payload = canonical_bytes(entry._linkage_payload())
            if not alg.verify(self._pub, payload, base64.b64decode(entry.signature)):
                return False, "invalid anchor signature at seq %d" % i
            if entry.compute_digest() != entry.digest:
                return False, "digest mismatch at seq %d" % i
            prev = entry.digest
        return True, "ok (%d entries)" % len(self._entries)

    # -- persistence (fail-closed) ---------------------------------------- #
    def save(self, path: str) -> None:
        ok, reason = self.verify_consistency()
        if not ok:
            raise ValueError("refusing to save an inconsistent anchor log: %s" % reason)
        with open(path, "w", encoding="utf-8") as fh:
            for entry in self._entries:
                fh.write(json.dumps({"t": "anchor", **entry.to_record()}) + "\n")

    @classmethod
    def load(cls, path: str, anchor_pub: bytes, sig_alg: str = DEFAULT_SIGN_ALG) -> "TransparencyAnchorLog":
        log = cls(anchor_priv=None, anchor_pub=anchor_pub, sig_alg=sig_alg)
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                if obj.get("t") != "anchor":
                    continue
                entry = AnchorLogEntry.from_record(obj)
                log._entries.append(entry)
        ok, reason = log.verify_consistency()
        if not ok:
            raise ValueError("refusing to load a tampered anchor log: %s" % reason)
        return log
