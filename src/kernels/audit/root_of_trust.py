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
import glob
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from cryptography.hazmat.primitives import serialization

from .dual_signature import (
    DEFAULT_SIGN_ALG,
    SIGN_ALGORITHMS,
    canonical_bytes,
    key_id_of,
)
from .trusted_timestamp import (
    QuorumTimestampAuthority,
    TimestampToken,
    TimestampTokenBundle,
    TrustedTimestampAuthority,
)

#: Path (PEM private key) that, when set, arms the Root of Trust. Opt-in.
ROOT_TRUST_KEY_ENV = "LIUHAO_AUDIT_ROT_KEY"
#: Path (PEM public key) used to verify attestations. Defaults to the private
#: key's sibling logic; explicit override is ``<PRIVATE>_PUB``.
ROOT_TRUST_PUBKEY_ENV = "LIUHAO_AUDIT_ROT_KEY_PUB"


@dataclass(frozen=True)
class RootOfTrustAttestation:
    """A signed attestation of a chain head. Self-describing (carries sig_alg + key_id).

    Optional RFC 3161 trusted-timestamp fields (``tsa_*``) are attached only
    when a Timestamp Authority is configured -- they are opt-in and default to
    None, so an attestation without a timestamp is still fully valid.
    """

    sig_alg: str
    key_id: str
    signature: str  # base64 (ascii)
    covers_seq: int
    covers_hash: str
    covers_link_hash: str
    # -- optional RFC 3161 trusted timestamp (U53 evidence grade) --
    tsa_token: Optional[str] = None  # base64 DER TimeStampToken
    tsa_gen_time: Optional[str] = None  # ISO-8601 UTC from the TSA
    tsa_cert_id: Optional[str] = None  # sha256 pin of the TSA cert
    tsa_bundle: Optional[List[dict]] = None  # K-of-M TSA token set (U53 HA)

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
            "tsa_token": self.tsa_token,
            "tsa_gen_time": self.tsa_gen_time,
            "tsa_cert_id": self.tsa_cert_id,
            "tsa_bundle": self.tsa_bundle,
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


@dataclass
class KeyRing:
    """Rotation-aware set of trusted RoT PUBLIC keys, keyed by ``key_id``.

    Verification looks the attestation's ``key_id`` UP in the ring, so an
    attestation signed by a now-rotated key still verifies against its original
    public key -- while new attestations are signed by the active private key.
    This is what makes key rotation safe for a long-lived evidence chain:
    rotating the signing key never orphans historical attestations.
    """

    _by_id: Dict[str, bytes]

    @classmethod
    def from_pem(cls, pub_pem: bytes) -> "KeyRing":
        return cls({key_id_of(pub_pem): pub_pem})

    @classmethod
    def from_directory(cls, dir_path: str) -> "KeyRing":
        ring: Dict[str, bytes] = {}
        for p in sorted(glob.glob(os.path.join(dir_path, "*.pem"))):
            with open(p, "rb") as fh:
                ring[key_id_of(fh.read())] = fh.read()
        return cls(ring)

    @classmethod
    def from_env(cls) -> "KeyRing":
        ring_dir = os.environ.get("LIUHAO_AUDIT_ROT_PUBRING")
        if ring_dir and os.path.isdir(ring_dir):
            return cls.from_directory(ring_dir)
        single = os.environ.get("LIUHAO_AUDIT_ROT_KEY_PUB")
        if single and os.path.exists(single):
            with open(single, "rb") as fh:
                return cls.from_pem(fh.read())
        return cls({})

    def get(self, key_id: str) -> Optional[bytes]:
        return self._by_id.get(key_id)

    def __contains__(self, key_id: str) -> bool:
        return key_id in self._by_id

    def key_ids(self) -> list:
        return list(self._by_id.keys())


class ChainRootOfTrust:
    """Sign / verify the audit chain head against a Root of Trust key (fail-closed).

    Rotation-aware: verification is keyed by ``key_id`` against a ``KeyRing`` of
    trusted public keys. Optional RFC 3161 trusted timestamping is attached when
    a ``TrustedTimestampAuthority`` is supplied to the constructor.
    """

    def __init__(
        self,
        private_pem: Optional[bytes] = None,
        public_pem: Optional[bytes] = None,
        key_ring: Optional[KeyRing] = None,
        tsa: Optional[TrustedTimestampAuthority] = None,
    ):
        self._priv = private_pem
        if key_ring is not None:
            self._ring = key_ring
        elif public_pem is not None:
            self._ring = KeyRing.from_pem(public_pem)
        else:
            self._ring = KeyRing({})
        self._tsa = tsa

    @classmethod
    def from_env(
        cls, priv_path: Optional[str] = None, pub_path: Optional[str] = None
    ) -> "ChainRootOfTrust":
        priv = load_rot_private_key(priv_path)
        if pub_path:
            ring = KeyRing.from_pem(load_rot_public_key(pub_path))
        else:
            ring = KeyRing.from_env()
        tsa = TrustedTimestampAuthority.from_env()
        return cls(private_pem=priv, key_ring=ring, tsa=tsa)

    @property
    def configured(self) -> bool:
        return self._priv is not None and len(self._ring.key_ids()) > 0

    def _active_key_id(self) -> str:
        priv = serialization.load_pem_private_key(self._priv, password=None)
        pub_pem = priv.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return key_id_of(pub_pem)

    def sign_head(
        self, seq: int, head_hash: str, link_hash: str, sig_alg: str = DEFAULT_SIGN_ALG
    ) -> RootOfTrustAttestation:
        """Attest the chain head. Raises if not configured or unknown sig_alg."""
        if not self.configured:
            raise RuntimeError("ChainRootOfTrust not configured (no RoT key loaded)")
        alg = SIGN_ALGORITHMS.get(sig_alg)
        if alg is None:
            raise ValueError("unknown sig_alg: %s" % sig_alg)  # fail-closed
        active_id = self._active_key_id()
        if active_id not in self._ring:
            raise RuntimeError(
                "RoT signing key id %s is not in the trusted key ring; "
                "refusing to sign an attestation that cannot be verified" % active_id
            )
        payload = {
            "covers_seq": seq,
            "covers_hash": head_hash,
            "covers_link_hash": link_hash,
        }
        data = canonical_bytes(payload)
        signature = alg.sign(self._priv, data)
        # Optional RFC 3161 trusted timestamp (evidence-grade time binding).
        tsa_token = None
        tsa_gen_time = None
        tsa_cert_id = None
        tsa_bundle = None
        if self._tsa is not None:
            if isinstance(self._tsa, QuorumTimestampAuthority):
                bundle = self._tsa.request_token(data)
                tsa_bundle = bundle.to_record()
                tsa_token = bundle.token_b64
                tsa_gen_time = bundle.gen_time.isoformat() if bundle.gen_time else None
                tsa_cert_id = bundle.tsa_cert_id
            else:
                tok = self._tsa.request_token(data)
                tsa_token = tok.token_b64
                tsa_gen_time = tok.gen_time.isoformat() if tok.gen_time else None
                tsa_cert_id = tok.tsa_cert_id
        return RootOfTrustAttestation(
            sig_alg=sig_alg,
            key_id=active_id,
            signature=base64.b64encode(signature).decode("ascii"),
            covers_seq=seq,
            covers_hash=head_hash,
            covers_link_hash=link_hash,
            tsa_token=tsa_token,
            tsa_gen_time=tsa_gen_time,
            tsa_cert_id=tsa_cert_id,
            tsa_bundle=tsa_bundle,
        )

    def verify_attestation(self, att: RootOfTrustAttestation) -> Tuple[bool, str]:
        """Fail-closed verification of an attestation against the trusted ring.

        Returns (ok, reason). Unknown sig_alg, an attestation whose ``key_id``
        is not in the trusted ring (rotated out / forged), or a bad signature
        all return (False, reason) -- never a silent acceptance.
        """
        alg = SIGN_ALGORITHMS.get(att.sig_alg)
        if alg is None:
            return False, "unknown sig_alg: %s" % att.sig_alg  # fail-closed
        pub = self._ring.get(att.key_id)
        if pub is None:
            return False, (
                "attestation key_id not in trusted key ring "
                "(rotated out or unknown): %s" % att.key_id
            )
        try:
            signature = base64.b64decode(att.signature)
        except Exception:
            return False, "attestation signature is not valid base64"
        data = canonical_bytes(att.to_payload())
        ok = alg.verify(pub, data, signature)
        if not ok:
            return False, "signature verification failed (tampered head or wrong key)"
        return True, "ok"

    def verify_timestamp(
        self, att: RootOfTrustAttestation, tsa: Optional[TrustedTimestampAuthority] = None
    ) -> Tuple[bool, str]:
        """Verify the RFC 3161 timestamp attached to an attestation (fail-closed).

        An attestation without a timestamp is accepted (opt-in skipped). An
        attestation WITH a timestamp but no configured TSA, or whose token fails
        cryptographic verification against the pinned TSA cert, is REJECTED.
        """
        data = canonical_bytes(att.to_payload())
        # Quorum (K-of-M) timestamp: verify against a QuorumTimestampAuthority.
        if att.tsa_bundle is not None:
            authority = tsa or self._tsa
            if not isinstance(authority, QuorumTimestampAuthority):
                return False, (
                    "attestation carries a TSA bundle but no "
                    "QuorumTimestampAuthority is configured"
                )
            bundle = TimestampTokenBundle.from_record(att.tsa_bundle)
            return authority.verify_bundle(bundle, data)
        if att.tsa_token is None:
            return True, "no timestamp attached (opt-in skipped)"
        authority = tsa or self._tsa
        if authority is None:
            return (
                False,
                "attestation carries a timestamp but no TSA authority is configured",
            )
        token = TimestampToken(
            token_der=base64.b64decode(att.tsa_token),
            gen_time=None,
            tsa_cert_id=att.tsa_cert_id or "",
        )
        return authority.verify_token(token, data)
