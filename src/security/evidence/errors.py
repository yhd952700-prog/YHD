"""HD-05 — error hierarchy for the provider-neutral evidence / trusted-timestamp subsystem.

Fail-closed discipline: verification failures are *reported*, never silently
swallowed. A ``VerificationResult`` (see ``interfaces``) carries the reasons; the
exceptions here are for operational errors (unknown provider, unparseable key)
where the only honest action is to stop.
"""
from __future__ import annotations


class EvidenceError(Exception):
    """Base class for all evidence-subsystem errors."""


class UnknownProviderError(EvidenceError):
    """Raised when ``LIUHAO_TSA_PROVIDER`` (or an explicit name) selects a
    provider that is not registered. Fail-closed: an unconfigured provider is
    rejected, never silently defaulted to a working one."""


class TimestampVerificationError(EvidenceError):
    """Raised when a timestamp token cannot be verified (bad signature, unknown
    algorithm, digest mismatch, or unparseable response)."""


class TrustError(EvidenceError):
    """Raised when a signer / key is not trusted or cannot be loaded."""


class KeyLifecycleError(EvidenceError):
    """Raised on key generation / rotation / load failures."""


class AdapterError(EvidenceError):
    """Raised when an evidence artifact cannot be sealed / opened."""
