"""HD-05 — provider-neutral trusted-timestamp / platform-root-of-trust subsystem.

A complete, offline-testable evidence-timestamping stack with five narrow
interfaces (see :mod:`src.security.evidence.interfaces`):

  * :class:`Signer`            — sign / verify arbitrary bytes.
  * :class:`TimestampProvider` — bind a (digest, time) proof to an artifact.
  * :class:`KeyLifecycle`      — generate / rotate / export / load keys.
  * :class:`EvidenceAdapter`   — seal a manifest into a bundle; open it back.
  * :class:`Verifier`          — verify a bundle end-to-end, fail-closed.

The TEMPORARY adapter is :class:`LocalRfc3161LikeProvider` — an RFC 3161-shaped,
RSA-3072, OFFLINE mock that makes the whole stack complete and testable without
any external/paid TSA or hardware root. Real RFC 3161 / TPM providers are
interface-only stubs (see :mod:`rfc3161_provider`, :mod:`tpm_provider`).

FINAL PROVIDER SELECTION IS A RESERVED HUMAN DECISION (docs/autonomous/
HUMAN-DECISION-BACKLOG.md HD-05). Until then it is a CONFIG knob:

    LIUHAO_TSA_PROVIDER=local | rfc3161 | tpm   (default: local)

Convention: the algorithm identifier travels INSIDE every token and verification
dispatches on it; an unknown algorithm is rejected (fail-closed), never silently
defaulted. No network call and no irreversible key ceremony happen in this module.
"""
from __future__ import annotations

from .adapter import ManifestEvidenceAdapter
from .errors import (
    AdapterError,
    EvidenceError,
    KeyLifecycleError,
    TimestampVerificationError,
    TrustError,
    UnknownProviderError,
)
from .factory import (
    DEFAULT_PROVIDER,
    PROVIDERS,
    build_default_subsystem,
    get_key_lifecycle,
    get_signer,
    get_timestamp_provider,
    get_verifier,
)
from .interfaces import (
    EvidenceAdapter,
    EvidenceBundle,
    KeyLifecycle,
    SignatureToken,
    Signer,
    TimestampProvider,
    TimestampToken,
    VerificationResult,
    Verifier,
    artifact_digest,
)
from .local_provider import LocalRfc3161LikeProvider
from .rfc3161_provider import Rfc3161TimestampProvider
from .tpm_provider import TpmTimestampProvider
from .verifier import EvidenceVerifier

__all__ = [
    # value types
    "SignatureToken",
    "TimestampToken",
    "EvidenceBundle",
    "VerificationResult",
    "artifact_digest",
    # interfaces
    "Signer",
    "TimestampProvider",
    "KeyLifecycle",
    "EvidenceAdapter",
    "Verifier",
    # providers
    "LocalRfc3161LikeProvider",
    "Rfc3161TimestampProvider",
    "TpmTimestampProvider",
    # concrete wiring
    "ManifestEvidenceAdapter",
    "EvidenceVerifier",
    # factory
    "PROVIDERS",
    "DEFAULT_PROVIDER",
    "get_timestamp_provider",
    "get_signer",
    "get_key_lifecycle",
    "get_verifier",
    "build_default_subsystem",
    # errors
    "EvidenceError",
    "UnknownProviderError",
    "TimestampVerificationError",
    "TrustError",
    "KeyLifecycleError",
    "AdapterError",
]
