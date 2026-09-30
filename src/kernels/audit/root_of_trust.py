"""Root-of-Trust: signed attestation of the audit chain head (U40 / U51 / U53).

The hash chain proves internal *consistency* -- replay from genesis detects a
bit-flip. It does NOT prove *attribution*: anyone who can read the DB can
recompute the chain head, so a tampered-then-replayed chain looks internally
consistent to a naive reader. For an evidence-grade audit trail (this is a
human-sovereignty OS), the chain HEAD must be cryptographically attested by a
Root of Trust key that lives OUTSIDE the audit DB, so an external party can
verify "this head was signed by the authority that owns the chain" without
trusting the DB itself.

Design -- additive, fail-closed, opt-in:

  * We sign the cumulative C2 ``link_hash`` together with ``(seq, head_hash)`` so
    the attestation is unambiguous about which head it covers.
  * Verification DISPATCHES on ``sig_alg`` (mirrors ``dual_signature`` and
    ``hash_chain``): an unknown algorithm is rejected, never silently defaulted.
  * If no RoT key is configured, signing / verification are SKIPPED -- the chain
    works exactly as before. This feature is opt-in and cannot break the default
    path.
  * If a key IS configured but the stored attestation is missing / does not cover
    the current head / fails to verify -> verification FAILS (fail-closed). It is
    never silently accepted.

This module is storage-agnostic: it produces / verifies a
``RootOfTrustAttestation``. The HC-01 store integrates it by persisting that
record in a dedicated table INSIDE the same append transaction (so the
signature can never disagree with the head it commits). That integration is the
next step after the HC-01 chain is stabilised; the primitive here is
independently testable without touching the live chain.
"""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Optional, Tuple

from .dual_signature import (
    DEFAULT_SIGN_ALG,
    SIGN_ALGORITHMS,
    canonical_bytes,
    key_id_of,
)

#: Path (PEM private key) that, when set, arms the Root of Trust. Opt-in.
ROOT_TRUST_KEY_ENV = "LIUHAO_AUDIT_ROT_KEY"
#: Path (PEM public key) used to verify attestations. Defaults to the private
#: key's sibling logic; explicit override is ``<PRIVATE>_PUB``.
ROOT_TRUST_PUBKEY_ENV = "LIUHAO_AUDIT_ROT_KEY_PUB"


@dataclass(frozen=True)
class RootOfTrustAttestation:
    """A signed attestation of a chain head. Self-describing (carries sig_alg + key_id)."""

    sig_alg: str
    key_id: str
    signature: str  # base64 (ascii)
    covers_seq: int
    covers_hash: str
    covers_link_hash: str

    def to_payload(self) -> dict:
        """The authority-bearing claims the signature is computed over."""
        return {
            "covers_seq": self.covers_seq,
            "covers_hash": self.covers_hash,
            "covers_link_hash": self.covers_link_hash,
        }

    def to_record(self) -> dict:
        """The full persisted record (payload + provenance)."""
        return {
            "sig_alg": self.sig_alg,
            "key_id": self.key_id,
            "signature": self.signature,
            "covers_seq": self.covers_seq,
            "covers_hash": self.covers_hash,
            "covers_link_hash": self.covers_link_hash,
        }


@dataclass(frozen=True)
class KeyPair:
    private_pem: bytes
    public_pem: bytes

    @property
    def key_id(self) -> str:
        return key_id_of(self.public_pem)


def generate_keypair() -> KeyPair:
    """Generate a fresh Root of Trust keypair (RSA-3072 via dual_signature)."""
    alg = SIGN_ALGORITHMS[DEFAULT_SIGN_ALG]
    priv, pub = alg.generate_keypair()
    return KeyPair(private_pem=priv, public_pem=pub)


def load_rot_private_key(path: Optional[str] = None) -> Optional[bytes]:
    """Load the RoT private key PEM. Returns None when not configured (opt-in)."""
    path = path or os.environ.get(ROOT_TRUST_KEY_ENV)
    if not path:
        return None
    if not os.path.exists(path):
        raise FileNotFoundError(
            "%s points to a missing key: %s" % (ROOT_TRUST_KEY_ENV, path)
        )
    with open(path, "rb") as fh:
        return fh.read()


def load_rot_public_key(path: Optional[str] = None) -> Optional[bytes]:
    path = path or os.environ.get(ROOT_TRUST_PUBKEY_ENV)
    if not path:
        return None
    if not os.path.exists(path):
        raise FileNotFoundError(
            "%s points to a missing key: %s" % (ROOT_TRUST_PUBKEY_ENV, path)
        )
    with open(path, "rb") as fh:
        return fh.read()


class ChainRootOfTrust:
    """Sign / verify the audit chain head against a Root of Trust key (fail-closed)."""

    def __init__(self, private_pem: Optional[bytes], public_pem: Optional[bytes]):
        self._priv = private_pem
        self._pub = public_pem

    @classmethod
    def from_env(
        cls, priv_path: Optional[str] = None, pub_path: Optional[str] = None
    ) -> "ChainRootOfTrust":
        return cls(
            private_pem=load_rot_private_key(priv_path),
            public_pem=load_rot_public_key(pub_path),
        )

    @property
    def configured(self) -> bool:
        return self._priv is not None and self._pub is not None

    def sign_head(
        self, seq: int, head_hash: str, link_hash: str, sig_alg: str = DEFAULT_SIGN_ALG
    ) -> RootOfTrustAttestation:
        """Attest the chain head. Raises if not configured or unknown sig_alg."""
        if not self.configured:
            raise RuntimeError("ChainRootOfTrust not configured (no RoT key loaded)")
        alg = SIGN_ALGORITHMS.get(sig_alg)
        if alg is None:
            raise ValueError("unknown sig_alg: %s" % sig_alg)  # fail-closed
        payload = {
            "covers_seq": seq,
            "covers_hash": head_hash,
            "covers_link_hash": link_hash,
        }
        data = canonical_bytes(payload)
        signature = alg.sign(self._priv, data)
        return RootOfTrustAttestation(
            sig_alg=sig_alg,
            key_id=key_id_of(self._pub),
            signature=base64.b64encode(signature).decode("ascii"),
            covers_seq=seq,
            covers_hash=head_hash,
            covers_link_hash=link_hash,
        )

    def verify_attestation(self, att: RootOfTrustAttestation) -> Tuple[bool, str]:
        """Fail-closed verification of an attestation against the configured key.

        Returns (ok, reason). Unknown sig_alg, missing public key, key-id
        mismatch, or a bad signature all return (False, reason) -- never a
        silent acceptance.
        """
        alg = SIGN_ALGORITHMS.get(att.sig_alg)
        if alg is None:
            return False, "unknown sig_alg: %s" % att.sig_alg  # fail-closed
        if self._pub is None:
            return False, "RoT public key not available for verification"
        if att.key_id != key_id_of(self._pub):
            return False, "attestation key_id does not match configured RoT public key"
        try:
            signature = base64.b64decode(att.signature)
        except Exception:
            return False, "attestation signature is not valid base64"
        data = canonical_bytes(att.to_payload())
        ok = alg.verify(self._pub, data, signature)
        if not ok:
            return False, "signature verification failed (tampered head or wrong key)"
        return True, "ok"
