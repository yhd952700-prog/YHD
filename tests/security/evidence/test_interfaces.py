"""HD-05 — the local mock satisfies every interface contract.

The local provider must be a drop-in for any of the five interfaces so the
subsystem is complete and testable without a real TSA/TPM.
"""
from __future__ import annotations

from src.security.evidence import (
    EvidenceAdapter,
    KeyLifecycle,
    LocalRfc3161LikeProvider,
    Signer,
    TimestampProvider,
    TimestampToken,
)


def test_local_provider_implements_all_interfaces() -> None:
    p = LocalRfc3161LikeProvider()
    assert isinstance(p, Signer)
    assert isinstance(p, TimestampProvider)
    assert isinstance(p, KeyLifecycle)
    # EvidenceAdapter is not implemented by the provider itself; the adapter
    # wraps it (see test_adapter). The provider is the timestamp/sign/key role.
    assert not isinstance(p, EvidenceAdapter)


def test_timestamp_token_is_self_describing() -> None:
    p = LocalRfc3161LikeProvider()
    token = p.timestamp(b"artifact-bytes")
    assert isinstance(token, TimestampToken)
    # Algorithm identifier travels INSIDE the token (fail-closed dispatch).
    assert token.alg == "RS256-RSA3072"
    assert token.source == "local"
    assert token.ts  # ISO-8601 UTC genTime
    assert token.digest  # messageImprint (sha256 hex)
    assert token.token  # base64 signature
    assert token.pubkey_id  # key binding
    # Root-of-trust discipline: a local token is explicitly self-attested.
    assert token.authority == "local"
    assert token.self_attested is True


def test_token_round_trips_through_dict() -> None:
    p = LocalRfc3161LikeProvider()
    token = p.timestamp(b"x")
    again = TimestampToken.from_dict(token.to_dict())
    assert again == token
    assert p.verify(b"x", again) is True
