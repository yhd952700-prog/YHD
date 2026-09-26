"""HD-05 — local RFC 3161-shaped provider: offline, tamper-evident, fail-closed."""
from __future__ import annotations

import hashlib

import pytest

from src.security.evidence import LocalRfc3161LikeProvider, SignatureToken, TimestampToken


def test_timestamp_stamp_and_verify_offline() -> None:
    p = LocalRfc3161LikeProvider()
    data = b"manifest-bytes"
    token = p.timestamp(data)
    assert p.verify(data, token) is True


def test_timestamp_detects_tampered_artifact() -> None:
    p = LocalRfc3161LikeProvider()
    token = p.timestamp(b"original")
    assert p.verify(b"tampered", token) is False


def test_timestamp_rejects_unknown_algorithm_fail_closed() -> None:
    p = LocalRfc3161LikeProvider()
    token = p.timestamp(b"x")
    bogus = TimestampToken(
        source=token.source, alg="BOGUS-ALG", ts=token.ts,
        digest=token.digest, token=token.token, pubkey_id=token.pubkey_id,
    )
    # Unknown alg must be rejected, never silently verified.
    assert p.verify(b"x", bogus) is False


def test_timestamp_binds_digest_to_token() -> None:
    p = LocalRfc3161LikeProvider()
    data = b"some-artifact"
    token = p.timestamp(data)
    assert token.digest == hashlib.sha256(data).hexdigest()


def test_sign_and_verify_signature_offline() -> None:
    p = LocalRfc3161LikeProvider()
    data = b"bytes-to-sign"
    sig = p.sign(data)
    assert isinstance(sig, SignatureToken)
    assert p.verify(data, sig) is True
    assert p.verify(b"other", sig) is False


def test_key_lifecycle_generate_and_public_pem() -> None:
    p = LocalRfc3161LikeProvider()
    first_id = p.public_key_id()
    new_id = p.generate()
    assert new_id != first_id  # rotation changed the key
    pem = p.public_key_pem()
    assert pem.startswith("-----BEGIN PUBLIC KEY-----")
    # A token stamped before rotation must not verify under the new key.
    old = LocalRfc3161LikeProvider()
    token = old.timestamp(b"x")
    assert p.verify(b"x", token) is False  # different in-process key


def test_two_providers_with_same_explicit_key_verify_each_other() -> None:
    p1 = LocalRfc3161LikeProvider()
    pem = p1.public_key_pem()
    # Share the public key so cross-verification is possible; p2 signs nothing
    # private, just verifies with the shared public key.
    p2 = LocalRfc3161LikeProvider()
    p2.load(pem)
    token = p1.timestamp(b"shared")
    assert p2.verify(b"shared", token) is True
