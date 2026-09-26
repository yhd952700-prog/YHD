"""HD-05 — interfaces and value types for the provider-neutral evidence subsystem.

The subsystem is built around five narrow interfaces so that the *final* trusted
timestamp / platform-root-of-trust provider (a reserved human decision — see
docs/autonomous/EXECUTION-QUEUE.md and HUMAN-DECISION-BACKLOG.md) can be swapped
by CONFIG without touching call sites:

  * :class:`Signer`          — produces / verifies a signature over arbitrary bytes.
  * :class:`TimestampProvider` — binds a (digest, time) proof to an artifact.
  * :class:`KeyLifecycle`    — generate / rotate / export / load signing keys.
  * :class:`EvidenceAdapter` — seals a manifest into a verifiable bundle and opens it.
  * :class:`Verifier`        — verifies a bundle end-to-end, fail-closed.

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
    (``digest``), the *genTime* (``ts``), and the TSA's signature over them
    (``token``). ``alg`` / ``source`` make the proof self-describing and let the
    verifier dispatch on the declared algorithm (fail-closed on unknown).
    """

    source: str
    alg: str
    ts: str
    digest: str
    token: str
    pubkey_id: Optional[str] = None

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
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TimestampToken":
        return cls(
            source=d["source"], alg=d["alg"], ts=d["ts"], digest=d["digest"],
            token=d["token"], pubkey_id=d.get("pubkey_id"),
        )


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
    ``ok`` is False (empty when ``ok`` is True)."""

    ok: bool
    checked: list = field(default_factory=list)
    failures: list = field(default_factory=list)

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


class TimestampProvider(abc.ABC):
    """Binds a (digest, time) trusted-timestamp proof to an artifact."""

    @property
    @abc.abstractmethod
    def source_name(self) -> str:
        """Backend name carried inside the token (``local`` / ``rfc3161`` / ``tpm``)."""

    @property
    @abc.abstractmethod
    def alg(self) -> str:
        """Self-describing algorithm identifier (travels inside the token)."""

    @abc.abstractmethod
    def timestamp(self, data: bytes) -> TimestampToken:
        """Return a trusted-timestamp token for ``data`` (binds sha256(data)+now)."""

    @abc.abstractmethod
    def verify(self, data: bytes, token: TimestampToken) -> bool:
        """Return True iff ``token`` is a valid timestamp for ``data``."""


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
