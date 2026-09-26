"""HD-05 — provider registry + factory.

The FINAL trusted-timestamp / platform-root-of-trust provider is a reserved
human decision (see docs/autonomous/HUMAN-DECISION-BACKLOG.md HD-05). Until that
decision lands, selection is a CONFIG knob (``LIUHAO_TSA_PROVIDER``) and the
system defaults to the offline local mock so nothing blocks on an external/paid
provider or an irreversible key ceremony.

Selecting an unregistered provider is fail-closed: it raises
:class:`UnknownProviderError` rather than silently falling back to a working one.
"""
from __future__ import annotations

import os
from typing import Dict, Optional

from .errors import UnknownProviderError
from .interfaces import KeyLifecycle, Signer, TimestampProvider, Verifier
from .local_provider import LocalRfc3161LikeProvider
from .rfc3161_provider import Rfc3161TimestampProvider
from .tpm_provider import TpmTimestampProvider

#: Registered providers. Add a real RFC 3161 TSA / TPM binding here in phase 2;
#: do NOT remove the local mock (it keeps the stack testable and offline).
PROVIDERS: Dict[str, type] = {
    "local": LocalRfc3161LikeProvider,
    "rfc3161": Rfc3161TimestampProvider,
    "tpm": TpmTimestampProvider,
}

DEFAULT_PROVIDER_ENV = "LIUHAO_TSA_PROVIDER"
DEFAULT_PROVIDER = "local"


def get_timestamp_provider(name: Optional[str] = None) -> TimestampProvider:
    name = name or os.environ.get(DEFAULT_PROVIDER_ENV, DEFAULT_PROVIDER)
    cls = PROVIDERS.get(name)
    if cls is None:
        raise UnknownProviderError(
            f"unknown TSA provider {name!r}; registered={sorted(PROVIDERS)}"
        )
    return cls()


def get_signer(name: Optional[str] = None) -> Signer:
    provider = get_timestamp_provider(name)
    if not isinstance(provider, Signer):
        raise NotImplementedError(
            f"provider {type(provider).__name__} does not implement Signer in phase 1"
        )
    return provider


def get_key_lifecycle(name: Optional[str] = None) -> KeyLifecycle:
    provider = get_timestamp_provider(name)
    if not isinstance(provider, KeyLifecycle):
        raise NotImplementedError(
            f"provider {type(provider).__name__} does not implement KeyLifecycle in phase 1"
        )
    return provider


def get_verifier(name: Optional[str] = None) -> Verifier:
    from .verifier import EvidenceVerifier
    provider = get_timestamp_provider(name)
    signer = provider if isinstance(provider, Signer) else None
    return EvidenceVerifier(provider, signer)


def build_default_subsystem(name: Optional[str] = None) -> dict:
    """Wire a complete, provider-neutral evidence subsystem.

    Returns ``{provider, signer, key_lifecycle, verifier, adapter}``. The local
    mock satisfies all five roles, so the returned subsystem is fully usable and
    offline. Real providers plug in by name via ``LIUHAO_TSA_PROVIDER``.
    """
    from .adapter import ManifestEvidenceAdapter

    provider = get_timestamp_provider(name)
    signer = provider if isinstance(provider, Signer) else None
    key_lifecycle = provider if isinstance(provider, KeyLifecycle) else None
    verifier = get_verifier(name)
    adapter = ManifestEvidenceAdapter(provider, signer)
    return {
        "provider": provider,
        "signer": signer,
        "key_lifecycle": key_lifecycle,
        "verifier": verifier,
        "adapter": adapter,
    }
