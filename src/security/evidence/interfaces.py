"""HD-05 — interfaces and value types for the provider-neutral evidence subsystem.

The subsystem is built around five narrow interfaces so that the *final* trusted
timestamp / platform-root-of-trust provider (a reserved human decision — see
docs/adr/ADR-root-of-trust-hd05.md and HUMAN-DECISION-BACKLOG.md HD-05) can be
swapped by CONFIG without touching call sites:

  * :class:`Signer`            — produces / verifies a signature over arbitrary bytes.
  * :class:`TimestampIssuer`   — produces a (digest, time) trusted-timestamp proof.
  * :class:`TimestampVerifier` — independently verifies a proof against a trust anchor.
  * :class:`KeyLifecycle`      — generate / rotate / export / load signing keys.
  * :class:`EvidenceAdapter`   — seals a manifest into a verifiable bundle; opens it.
  * :class:`Verifier`          — verifies a bundle end-to-end, fail-closed.

ROOT-OF-TRUST DISCIPLINE (this is the core of HD-05):
-----------------------------------------------------
Issuing a timestamp and *verifying* it are SEPARATE concerns. A timestamp is
either:

  * INDEPENDENTLY VERIFIED — its authenticity is established by a trust anchor
    (X.509 cert / public key) that is *external* to the entity that produced the
    artifact (e.g. a real RFC 3161 TSA whose root is configured out-of-band);
  * SELF-ATTESTED — the same local/mock key both signs and "verifies". This is
    fine for dev/test and for tamper-evidence, but it is NOT third-party trust
    and MUST NEVER be presented as production-grade / independently verified.

Every :class:`TimestampToken` therefore carries two explicit fields:

  * ``authority``     — who attests the timestamp (e.g. ``"local"``, a TSA host,
    or a configured anchor id).
  * ``self_attested`` — ``True`` iff the issuer is the same entity that verifies.
    A self-attested token is flagged so upper layers cannot claim it is
    independently verified.

The local mock is ALWAYS self-attested. Selecting a real, independently-verified
provider (real RFC 3161 TSA / TPM / quorum) is the RESERVED human decision HD-05;
the code makes the local self-attested mode explicit and refuses to present it as
production-grade.

CONVENTION (matches ``kernels/audit`` A5 ``hash_alg`` and ``dual_signature``
``sig_alg``): the algorithm identifier travels INSIDE the token, and verification
DISPATCHES ON it. An unknown / unsupported algorithm is rejected — never silently
verified with a default. This keeps every token self-describing five years later.
"""
from __future__ import annotations

import abc
import base64
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple


# ---------------------------------------------------------------------------
# Value types
# ---------------------------------------------------------------------------

def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _unb64(s: str) -> bytes:
    return base64.b64decode(s)


def artifact_digest(data: bytes) -> str:
    """SHA-256 hex digest of an evidence artifact (the RFC 3161 messageImprint)."""
    return hashlib.sha256(data).hexdigest()


@dataclass
class SignatureToken:
    """A signature over arbitrary bytes.

    ``alg`` is the self-describing algorithm identifier (e.g. ``RS256-RSA3072``);
    ``source`` names the signer backend; ``token`` is the base64 signature;
    ``pubkey_id`` optionally identifies the key/cert that produced it.
    """

    alg: str
    source: str
    token: str
    pubkey_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"alg": self.alg, "source": self.source,
                             "token": self.token}
        if self.pubkey_id is not None:
            d["pubkey_id"] = self.pubkey_id
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SignatureToken":
        return cls(alg=d["alg"], source=d["source"], token=d["token"],
                   pubkey_id=d.get("pubkey_id"))


@dataclass
class TimestampToken:
    """An RFC 3161-shaped trusted-timestamp proof bound to an artifact digest.

    Models the three things a TimeStampToken conveys: the *messageImprint*
    (``digest``), the *genTime* (``ts``), and the issuer's signature over them
    (``token``). ``alg`` / ``source`` make the proof self-describing and let the
    verifier dispatch on the declared algorithm (fail-closed on unknown).

    ROOT-OF-TRUST FIELDS (HD-05):
      * ``authority``     — who attests this timestamp (see module docstring).
      * ``self_attested`` — True iff the issuer == verifier (no independent root).
        A self-attested token MUST NOT be presented as independently verified.
    """

    source: str
    alg: str
    ts: str
    digest: str
    token: str
    pubkey_id: Optional[str] = None
    authority: str = ""
    self_attested: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "source": self.source,
            "alg": self.alg,
            "ts": self.ts,
            "digest": self.digest,
            "token": self.token,
        }
        if self.pubkey_id is not None:
            d["pubkey_id"] = self.pubkey_id
        d["authority"] = self.authority
        d["self_attested"] = self.self_attested
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TimestampToken":
        return cls(
            source=d["source"], alg=d["alg"], ts=d["ts"], digest=d["digest"],
            token=d["token"], pubkey_id=d.get("pubkey_id"),
            authority=d.get("authority", d.get("source", "")),
            self_attested=bool(d.get("self_attested", False)),
        )


@dataclass
class TrustAnchor:
    """A configured root of trust used to INDEPENDENTLY verify a timestamp.

    Holds the verifying material (PEM X.509 cert or public key) and the
    authority name it represents. ``self_attested`` marks anchors that are the
    same key that signed (e.g. a locally-generated self-signed cert used for
    offline testing) — they verify the CMS math but do NOT constitute
    third-party trust.

    Importing :mod:`cryptography` is deferred to the load methods so this value
    type stays import-light.
    """

    authority: str
    verifying_pem: str
    self_attested: bool = False
    source: str = "local"

    def load_public_key(self):
        """Return the :class:`cryptography` public key for this anchor."""
        from cryptography.hazmat.primitives.serialization import (
            load_pem_public_key,
        )
        from cryptography.x509 import load_pem_x509_certificate

        try:
            cert = load_pem_x509_certificate(self.verifying_pem.encode("utf-8"))
            return cert.public_key()
        except ValueError:
            return load_pem_public_key(self.verifying_pem.encode("utf-8"))

    def load_cert(self):
        """Return the :class:`cryptography` X.509 cert, or None if a bare key."""
        from cryptography.x509 import load_pem_x509_certificate

        try:
            return load_pem_x509_certificate(self.verifying_pem.encode("utf-8"))
        except ValueError:
            return None


@dataclass
class EvidenceBundle:
    """A timestamped evidence artifact: a manifest plus its trust proof.

    Serializes to JSON for storage / transport. ``manifest`` is the original
    manifest object; ``artifact_digest`` lets a verifier confirm the manifest was
    not altered after sealing; ``timestamp`` is the trusted-timestamp proof.
    """

    manifest: Dict[str, Any]
    artifact_digest: str
    timestamp: TimestampToken
    signature: Optional[SignatureToken] = None

    def serialize(self) -> bytes:
        return json.dumps(self.to_dict(), sort_keys=True, indent=2).encode("utf-8")

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "manifest": self.manifest,
            "artifact_digest": self.artifact_digest,
            "timestamp": self.timestamp.to_dict(),
        }
        if self.signature is not None:
            d["signature"] = self.signature.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "EvidenceBundle":
        return cls(
            manifest=d["manifest"],
            artifact_digest=d["artifact_digest"],
            timestamp=TimestampToken.from_dict(d["timestamp"]),
            signature=(SignatureToken.from_dict(d["signature"])
                       if d.get("signature") is not None else None),
        )

    @classmethod
    def deserialize(cls, data: bytes) -> "EvidenceBundle":
        return cls.from_dict(json.loads(data.decode("utf-8")))


@dataclass
class VerificationResult:
    """End-to-end verification outcome. Fail-closed: ``ok`` is True only when no
    check failed. ``checked`` lists what was examined; ``failures`` lists why
    ``ok`` is False (empty when ``ok`` is True).

    ``self_attested`` mirrors the bundle's timestamp: True when the proof was
    asserted by the same entity that produced it (no independent root). Upper
    layers MUST NOT present a ``self_attested`` result as production-grade /
    independently verified.
    """

    ok: bool
    checked: list = field(default_factory=list)
    failures: list = field(default_factory=list)
    self_attested: bool = False

    def with_check(self, name: str) -> "VerificationResult":
        self.checked.append(name)
        return self

    def with_failure(self, reason: str) -> "VerificationResult":
        self.failures.append(reason)
        self.ok = False
        return self


# ---------------------------------------------------------------------------
# Interfaces (provider-neutral; the final provider is a config choice)
# ---------------------------------------------------------------------------

class Signer(abc.ABC):
    """Produces and verifies a signature over arbitrary bytes."""

    @abc.abstractmethod
    def public_key_id(self) -> str:
        """Stable identifier of the current public key (for trust binding)."""

    @abc.abstractmethod
    def sign(self, data: bytes) -> SignatureToken:
        """Sign ``data``; returns a self-describing :class:`SignatureToken`."""

    @abc.abstractmethod
    def verify(self, data: bytes, token: SignatureToken) -> bool:
        """Return True iff ``token`` is a valid signature over ``data``."""


class TimestampIssuer(abc.ABC):
    """Produces a (digest, time) trusted-timestamp proof bound to an artifact.

    The issuer MAY be self-attested (the local mock). The returned
    :class:`TimestampToken` MUST carry ``authority`` and ``self_attested`` so
    callers can never mistake a self-attested proof for an independently-verified
    one.
    """

    @property
    @abc.abstractmethod
    def source_name(self) -> str:
        """Backend name carried inside the token (``local`` / ``rfc3161`` / ``tpm``)."""

    @property
    @abc.abstractmethod
    def alg(self) -> str:
        """Self-describing algorithm identifier (travels inside the token)."""

    @property
    @abc.abstractmethod
    def authority(self) -> str:
        """Name of the authority that attests the timestamp.

        For self-attested providers this is the same entity that signs (e.g.
        ``"local"``). For an independently-verified provider it names the trust
        anchor / TSA host configured out-of-band.
        """

    @abc.abstractmethod
    def timestamp(self, data: bytes) -> TimestampToken:
        """Return a trusted-timestamp token for ``data`` (binds sha256(data)+now).

        The token MUST set ``authority`` and ``self_attested`` explicitly.
        """


class TimestampVerifier(abc.ABC):
    """Independently verifies a :class:`TimestampToken` against a trust anchor.

    Verification DISPATCHES ON the token's declared ``alg`` and ``source``; an
    unknown / mismatched provider is rejected (fail-closed). When ``anchor`` is
    provided, the proof is checked against that external root of trust; when it
    is None the verifier may fall back to embedded material (which is, by
    definition, self-attested and MUST be flagged as such upstream).
    """

    @property
    @abc.abstractmethod
    def source_name(self) -> str:
        """Backend name this verifier accepts (``local`` / ``rfc3161`` / ``tpm``)."""

    @abc.abstractmethod
    def verify(self, data: bytes, token: TimestampToken,
               anchor: Optional[TrustAnchor] = None) -> bool:
        """Return True iff ``token`` is a valid, anchored timestamp for ``data``.

        Fail-closed: any missing anchor, provider mismatch, bad signature, digest
        mismatch, or unparseable CMS returns False — never a silent pass.
        """


class TimestampProvider(TimestampIssuer, TimestampVerifier):
    """Combined issuer+verifier role (kept for backward-compatible call sites).

    Concrete providers implement both sides. Note that for a self-attested
    provider the same key issues AND verifies — the verifier is NOT independent
    of the issuer, and the token's ``self_attested`` flag makes that explicit.
    """


class KeyLifecycle(abc.ABC):
    """Generate / rotate / export / load signing keys.

    Implementations MUST NOT perform any irreversible key ceremony (no external
    CA request, no hardware provisioning). Local/mock keys are ephemeral or stored
    in a gitignored path; the final provider selection is a human decision.
    """

    @abc.abstractmethod
    def generate(self) -> str:
        """Generate a fresh key pair; return its ``pubkey_id``."""

    @abc.abstractmethod
    def rotate(self) -> str:
        """Rotate to a new key pair; return the new ``pubkey_id``."""

    @abc.abstractmethod
    def public_key_pem(self) -> str:
        """Return the current public key in PEM form (for trust binding)."""

    @abc.abstractmethod
    def load(self, pem: str) -> None:
        """Load a public (or private) key from PEM."""


class EvidenceAdapter(abc.ABC):
    """Seals a manifest into a verifiable bundle and opens it back."""

    @abc.abstractmethod
    def seal(self, manifest_bytes: bytes) -> EvidenceBundle:
        """Bind a trusted timestamp to ``manifest_bytes``; return the bundle."""

    @abc.abstractmethod
    def open(self, bundle: EvidenceBundle) -> Tuple[bytes, bool]:
        """Return (manifest_bytes, verified). ``verified`` is fail-closed."""


class Verifier(abc.ABC):
    """Verifies an :class:`EvidenceBundle` end-to-end, fail-closed."""

    @abc.abstractmethod
    def verify_bundle(self, bundle: EvidenceBundle) -> VerificationResult:
        """Return a :class:`VerificationResult`; ``ok`` is True only if every
        check passed (digest matches AND timestamp verifies AND alg known)."""
