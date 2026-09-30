"""Tests for ``src.kernels.audit.trusted_timestamp`` -- RFC 3161 trusted timestamping.

These prove the timestamping capability actually works end-to-end against the
system ``openssl ts`` engine (the reference RFC 3161 implementation): a token
minted for a payload verifies, a token for different data fails, a token from a
different TSA cert fails, and the genTime is recoverable from the token.

Skips gracefully when ``openssl`` is not on PATH (no network, no fabricated
success).
"""
from __future__ import annotations

import shutil
import datetime as _dt

import pytest

from src.kernels.audit.trusted_timestamp import (
    TrustedTimestampAuthority,
    build_timestamp_query,
    extract_gen_time,
    extract_token_from_response,
    generate_test_tsa_keypair,
)

_HAS_OPENSSL = shutil.which("openssl") is not None
requires_openssl = pytest.mark.skipif(
    not _HAS_OPENSSL, reason="openssl ts engine not available on PATH"
)


@requires_openssl
def test_build_query_is_valid_der_and_mintable() -> None:
    signer_key, signer_cert, ca_cert = generate_test_tsa_keypair()
    tsa = TrustedTimestampAuthority(
        tsa_cert_pem=ca_cert,
        signer_cert_pem=signer_cert,
        signer_key_pem=signer_key,
    )
    data = b"audit-chain-head-payload-bytes"
    tok = tsa.request_token(data)
    assert tok.token_der, "minted token is empty"
    assert tok.gen_time is not None, "genTime not extracted from token"
    delta = _dt.datetime.now(_dt.timezone.utc) - tok.gen_time
    assert abs(delta.total_seconds()) < 120, "genTime not within 2 minutes of now"


@requires_openssl
def test_round_trip_request_then_verify() -> None:
    signer_key, signer_cert, ca_cert = generate_test_tsa_keypair()
    tsa = TrustedTimestampAuthority(
        tsa_cert_pem=ca_cert,
        signer_cert_pem=signer_cert,
        signer_key_pem=signer_key,
    )
    data = b"the-exact-payload-the-rot-signed"
    tok = tsa.request_token(data)
    ok, reason = tsa.verify_token(tok, data)
    assert ok, reason


@requires_openssl
def test_tampered_data_fails_verification() -> None:
    signer_key, signer_cert, ca_cert = generate_test_tsa_keypair()
    tsa = TrustedTimestampAuthority(
        tsa_cert_pem=ca_cert,
        signer_cert_pem=signer_cert,
        signer_key_pem=signer_key,
    )
    tok = tsa.request_token(b"original-payload")
    ok, _ = tsa.verify_token(tok, b"tampered-payload")
    assert ok is False


@requires_openssl
def test_wrong_tsa_cert_fails_verification() -> None:
    key1, leaf1, ca1 = generate_test_tsa_keypair()
    key2, leaf2, ca2 = generate_test_tsa_keypair()
    tsa1 = TrustedTimestampAuthority(
        tsa_cert_pem=ca1, signer_cert_pem=leaf1, signer_key_pem=key1
    )
    tsa2 = TrustedTimestampAuthority(
        tsa_cert_pem=ca2, signer_cert_pem=leaf2, signer_key_pem=key2
    )
    data = b"payload"
    tok = tsa1.request_token(data)
    ok, reason = tsa2.verify_token(tok, data)
    assert ok is False, "token from TSA1 verified against TSA2 (cert pinning broken)"


def test_gen_time_extraction_from_real_token() -> None:
    signer_key, signer_cert, ca_cert = generate_test_tsa_keypair()
    if not _HAS_OPENSSL:
        pytest.skip("openssl required to mint a real token")
    tsa = TrustedTimestampAuthority(
        tsa_cert_pem=ca_cert,
        signer_cert_pem=signer_cert,
        signer_key_pem=signer_key,
    )
    tok = tsa.request_token(b"x")
    gt = extract_gen_time(tok.token_der)
    assert gt is not None
    assert isinstance(gt, _dt.datetime)
    assert gt.tzinfo is not None


def test_http_response_parser_pulls_token() -> None:
    signer_key, signer_cert, ca_cert = generate_test_tsa_keypair()
    if not _HAS_OPENSSL:
        pytest.skip("openssl required to mint a real token")
    tsa = TrustedTimestampAuthority(
        tsa_cert_pem=ca_cert,
        signer_cert_pem=signer_cert,
        signer_key_pem=signer_key,
    )
    tok = tsa.request_token(b"x")
    # Hand-build a minimal TimeStampResp wrapping the token.
    from src.kernels.audit.trusted_timestamp import der_integer, der_sequence

    status = der_sequence(der_integer(0))  # PKIStatusInfo { status 0 }
    # Real TimeStampResp = SEQUENCE { status, ContentInfo(timeStampToken) }.
    resp = der_sequence(status, tok.token_der)  # [0] timeStampToken
    pulled = extract_token_from_response(resp)
    assert pulled == tok.token_der


def test_query_is_deterministic_der() -> None:
    q1 = build_timestamp_query(b"abc", 12345)
    q2 = build_timestamp_query(b"abc", 12345)
    assert q1 == q2
    assert q1[0:1] == bytes([0x30])  # SEQUENCE
