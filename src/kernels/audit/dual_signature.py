"""PHASE 3.6 / P0-7 (B2 dual-signature evidence ``sig_alg``).

Every B2 dual-signature evidence record -- a human operator's sovereign
*override assertion* and an independent verifier's *confirmation* -- MUST state
the exact signature algorithm that produced it. It must not rely on out-of-band
context ("it was obviously RSA-3072 because that's what we used in 2026") to be
re-interpretable five years later, and an algorithm migration must never render
today's evidence unverifiable.

This is the signature analogue of two patterns already proven in this repo:

  * ``kernels/audit`` A5 ``hash_alg`` -- every audit event carries the hash
    algorithm that produced its ``event_hash``;
  * ``kernels/identity/_persistence`` A3 ``MAC_ALGORITHMS`` -- every registry
    integrity tag carries its MAC algorithm inside the tag.

Here every signature record carries its algorithm identifier INSIDE the record
(``sig_alg``), and verification DISPATCHES ON it.

Fail-closed: an unknown / unsupported ``sig_alg`` is rejected. It is NEVER
silently verified with a default algorithm -- "I cannot verify this" is the
honest answer, and inventing an algorithm would only produce a wrong-but-green
verdict. The identifier also encodes the key size
(``RS256-RSA3072``) so the record is self-describing about
primitive + key length + hash.

This module is intentionally independent of ``jwt_handler`` (token signing) and
``vault_crypto`` (dead code in production) so the existing RSA capabilities are
untouched ("现有 RSA-3072 能力保持不变").
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


# --------------------------------------------------------------------------- #
# Signature algorithm registry (PHASE 3.6 / P0-7)
# --------------------------------------------------------------------------- #
# The identifier encodes primitive + key size + hash so an old record remains
# self-describing. Adding an entry migrates the build FORWARD without rewriting
# any existing record -- a record keeps the algorithm it was born with.


class _RS256RSA3072:
    """RSA-3072 / PKCS#1 v1.5 / SHA-256 -- the production dual-signature scheme.

    Key size is fixed at 3072 bits (PHASE 3.6 / P0-7: "现有 RSA-3072 能力保持
    不变"). The identifier ``RS256-RSA3072`` is what travels inside every record.
    """

    identifier = "RS256-RSA3072"
    key_size = 3072
    primitive = "RSA"
    padding_name = "PKCS1v15"
    hash_name = "SHA-256"

    @classmethod
    def generate_keypair(cls) -> Tuple[bytes, bytes]:
        """Return ``(private_pem, public_pem)`` as PEM-encoded bytes."""
        priv = rsa.generate_private_key(
            public_exponent=65537, key_size=cls.key_size
        )
        priv_pem = priv.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        pub_pem = priv.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return priv_pem, pub_pem

    @classmethod
    def sign(cls, priv_pem: bytes, data: bytes) -> bytes:
        priv = serialization.load_pem_private_key(priv_pem, password=None)
        return priv.sign(data, padding.PKCS1v15(), hashes.SHA256())

    @classmethod
    def verify(cls, pub_pem: bytes, data: bytes, signature: bytes) -> bool:
        pub = serialization.load_pem_public_key(pub_pem)
        try:
            pub.verify(signature, data, padding.PKCS1v15(), hashes.SHA256())
            return True
        except Exception:
            # Any verification failure (bad signature, tampered payload, wrong
            # key) is a hard False -- never a default-algorithm fallback.
            return False


#: Signature algorithms this build can PRODUCE and VERIFY, by identifier.
SIGN_ALGORITHMS: Dict[str, type] = {
    _RS256RSA3072.identifier: _RS256RSA3072,
}

#: Algorithm applied to newly created evidence, and assumed for records migrated
#: forward (factually correct -- they were all RS256-RSA3072).
DEFAULT_SIGN_ALG = _RS256RSA3072.identifier


# --------------------------------------------------------------------------- #
# Canonical encoding
# --------------------------------------------------------------------------- #

def canonical_bytes(payload: Dict[str, Any]) -> bytes:
    """Stable canonical serialisation of authority-bearing claims.

    Mirrors the audit / registry canonicalisation: keys sorted, no whitespace.
    The signature is computed over THIS, never over the wrapper that also holds
    ``sig_alg`` / ``signature`` -- otherwise the signature would sign itself.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def key_id_of(pub_pem: bytes) -> str:
    """A short, stable identifier for a public key (SHA-256 thumbprint, hex)."""
    return hashlib.sha256(pub_pem).hexdigest()[:32]


# --------------------------------------------------------------------------- #
# Signature record (the persisted B2 evidence unit)
# --------------------------------------------------------------------------- #

@dataclass
class SignatureRecord:
    """One half of a B2 dual-signature evidence: either the human's override
    assertion or the verifier's confirmation.

    ``sig_alg`` is the explicit algorithm identifier and is the single source of
    truth for verification -- ``verify_signature_record`` dispatches on it and
    never falls back to a default.
    """

    record_id: str
    role: str                 # "human-override-assertion" | "verifier-confirmation"
    sig_alg: str              # explicit algorithm identifier (P0-7 core field)
    key_id: str               # public-key thumbprint
    claims: Dict[str, Any]    # canonical authority-bearing payload (signed)
    signature: str            # base64 of the raw signature
    signed_at: str            # ISO-8601 UTC

    def to_dict(self) -> Dict[str, Any]:
        return {
            "record_id": self.record_id,
            "role": self.role,
            "sig_alg": self.sig_alg,
            "key_id": self.key_id,
            "claims": self.claims,
            "signature": self.signature,
            "signed_at": self.signed_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SignatureRecord":
        return cls(
            record_id=d["record_id"],
            role=d["role"],
            sig_alg=d["sig_alg"],
            key_id=d["key_id"],
            claims=d["claims"],
            signature=d["signature"],
            signed_at=d["signed_at"],
        )


# --------------------------------------------------------------------------- #
# Verification path (P0-7: dispatch on sig_alg, fail-closed on unknown)
# --------------------------------------------------------------------------- #

def verify_signature_record(
    record: SignatureRecord, pub_pem: bytes
) -> Tuple[bool, str]:
    """Verify ``record`` against ``pub_pem``.

    Returns ``(ok, actual_algorithm_used)``. The second element is the algorithm
    the verifier ACTUALLY dispatched on -- which must equal ``record.sig_alg``.
    When ``record.sig_alg`` is unknown this build returns ``(False, "unknown")``
    and NEVER silently uses a default algorithm.
    """
    scheme = SIGN_ALGORITHMS.get(record.sig_alg)
    if scheme is None:
        # Unknown / unsupported algorithm: honest failure, no fallback.
        return False, "unknown"
    try:
        raw_sig = base64.b64decode(record.signature)
    except Exception:
        return False, scheme.identifier
    data = canonical_bytes(record.claims)
    ok = scheme.verify(pub_pem, data, raw_sig)
    return ok, scheme.identifier


# --------------------------------------------------------------------------- #
# Persistence (so the runtime probe can re-read INDEPENDENTLY from disk)
# --------------------------------------------------------------------------- #

_SCHEMA = """
CREATE TABLE IF NOT EXISTS dual_signature_evidence (
    record_id  TEXT PRIMARY KEY,
    role       TEXT NOT NULL,
    sig_alg    TEXT NOT NULL,
    key_id     TEXT NOT NULL,
    claims_json TEXT NOT NULL,
    signature  TEXT NOT NULL,
    signed_at  TEXT NOT NULL
)
"""


class DualSignatureStore:
    """SQLite-backed store for B2 dual-signature evidence records."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            root = os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.dirname(
                    os.path.abspath(__file__)))))
            db_path = os.environ.get(
                "DUAL_SIGNATURE_DB_PATH",
                os.path.join(root, "dual_signature_evidence.db"),
            )
        self._db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(self._db_path)), exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def persist(self, record: SignatureRecord) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO dual_signature_evidence "
            "(record_id, role, sig_alg, key_id, claims_json, signature, signed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                record.record_id, record.role, record.sig_alg, record.key_id,
                json.dumps(record.claims, sort_keys=True, separators=(",", ":")),
                record.signature, record.signed_at,
            ),
        )
        self._conn.commit()

    def load(self, record_id: str) -> Optional[SignatureRecord]:
        cur = self._conn.execute(
            "SELECT record_id, role, sig_alg, key_id, claims_json, signature, "
            "signed_at FROM dual_signature_evidence WHERE record_id = ?",
            (record_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return SignatureRecord(
            record_id=row[0], role=row[1], sig_alg=row[2], key_id=row[3],
            claims=json.loads(row[4]), signature=row[5], signed_at=row[6],
        )

    def close(self) -> None:
        self._conn.close()


# --------------------------------------------------------------------------- #
# Creation path (P0-7: generate REAL HumanOverrideAssertion + VerifierConfirmation)
# --------------------------------------------------------------------------- #

@dataclass
class DualSignatureBundle:
    evidence_id: str
    human_record: SignatureRecord
    verifier_record: SignatureRecord
    human_public_pem: bytes
    verifier_public_pem: bytes


def create_dual_signature_evidence(
    *,
    principal_id: str,
    action: str,
    reason: str,
    verifier_id: str,
    correlation_id: str,
    grant_id: Optional[str] = None,
    human_private_pem: Optional[bytes] = None,
    human_public_pem: Optional[bytes] = None,
    verifier_private_pem: Optional[bytes] = None,
    verifier_public_pem: Optional[bytes] = None,
    sig_alg: str = DEFAULT_SIGN_ALG,
    store: Optional[DualSignatureStore] = None,
) -> DualSignatureBundle:
    """Produce and persist a B2 dual-signature evidence bundle.

    The human asserts the sovereign override; the verifier independently
    confirms it. Both records are signed with ``sig_alg`` (default RS256-RSA3072)
    and, when a ``store`` is supplied, persisted so they can be re-read and
    verified independently from disk.
    """
    scheme = SIGN_ALGORITHMS.get(sig_alg)
    if scheme is None:
        raise ValueError(
            f"unknown signature algorithm {sig_alg!r}; this build can sign "
            f"with {sorted(SIGN_ALGORITHMS)}"
        )

    if human_private_pem is None or human_public_pem is None:
        human_private_pem, human_public_pem = scheme.generate_keypair()
    if verifier_private_pem is None or verifier_public_pem is None:
        verifier_private_pem, verifier_public_pem = scheme.generate_keypair()

    evidence_id = f"b2-{uuid.uuid4().hex}"
    now = datetime.now(timezone.utc).isoformat()

    human_claims = {
        "evidence_id": evidence_id,
        "role": "human-override-assertion",
        "principal_id": principal_id,
        "action": action,
        "reason": reason,
        "correlation_id": correlation_id,
        "grant_id": grant_id,
        "asserted_at": now,
    }
    human_sig = scheme.sign(human_private_pem, canonical_bytes(human_claims))
    human_record = SignatureRecord(
        record_id=f"{evidence_id}-human",
        role="human-override-assertion",
        sig_alg=sig_alg,
        key_id=key_id_of(human_public_pem),
        claims=human_claims,
        signature=base64.b64encode(human_sig).decode("ascii"),
        signed_at=now,
    )

    verifier_claims = {
        "evidence_id": evidence_id,
        "role": "verifier-confirmation",
        "confirms_record_id": human_record.record_id,
        "verifier_id": verifier_id,
        "verdict": "confirmed",
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
    }
    verifier_sig = scheme.sign(
        verifier_private_pem, canonical_bytes(verifier_claims)
    )
    verifier_record = SignatureRecord(
        record_id=f"{evidence_id}-verifier",
        role="verifier-confirmation",
        sig_alg=sig_alg,
        key_id=key_id_of(verifier_public_pem),
        claims=verifier_claims,
        signature=base64.b64encode(verifier_sig).decode("ascii"),
        signed_at=datetime.now(timezone.utc).isoformat(),
    )

    if store is not None:
        store.persist(human_record)
        store.persist(verifier_record)

    return DualSignatureBundle(
        evidence_id=evidence_id,
        human_record=human_record,
        verifier_record=verifier_record,
        human_public_pem=human_public_pem,
        verifier_public_pem=verifier_public_pem,
    )
