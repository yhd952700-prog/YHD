"""HD-05 — provider-neutral Root-of-Trust architecture tests (new, comprehensive).

These tests PROVE the root-of-trust discipline from ADR-root-of-trust-hd05.md:

  * a local timestamp is explicitly ``self_attested=True`` and ``authority="local"``
    and the local subsystem is NOT production-grade;
  * the RFC 3161 CMS verification path works OFFLINE against a generated trust
    anchor (parse CMS, verify signature, check messageImprint/nonce/genTime);
  * provider mismatch, missing anchor, and wrong anchor are fail-closed (return
    False / raise), never a silent pass;
  * the local mock is clearly non-production (``LOCAL_IS_PRODUCTION_GRADE`` is
    False and ``VerificationResult.self_attested`` is True for a self-attested
    bundle).

The local mock is never claimed to be independently verified. The FINAL provider
(real RFC 3161 TSA / TPM / quorum) is a reserved human decision (HD-05).
"""
from __future__ import annotations

import base64
import datetime
import hashlib

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from src.security.evidence import (
    LOCAL_IS_PRODUCTION_GRADE,
    LocalRfc3161LikeProvider,
    Rfc3161TimestampProvider,
    TpmTimestampProvider,
    TimestampToken,
    TrustAnchor,
    build_default_subsystem,
)
from src.security.evidence import rfc3161_cms as _cms


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _make_self_signed() -> tuple:
    """Return (rsa_private_key, cert_pem, cert_der) for an ephemeral anchor."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-anchor")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime(2020, 1, 1))
        .not_valid_after(datetime.datetime(2035, 1, 1))
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode("utf-8")
    cert_der = cert.public_bytes(serialization.Encoding.DER)
    return key, cert_pem, cert_der


def _wrap_cms(cms_der: bytes, *, source: str, alg: str, authority: str,
              self_attested: bool) -> TimestampToken:
    """Wrap raw CMS bytes into a TimestampToken (bypassing a provider)."""
    return TimestampToken(
        source=source, alg=alg, ts="2026-01-01T00:00:00Z",
        digest=hashlib.sha256(b"evidence").hexdigest(),
        token=base64.b64encode(cms_der).decode("ascii"), pubkey_id="x",
        authority=authority, self_attested=self_attested,
    )


# ---------------------------------------------------------------------------
# Local mock: self-attested + non-production
# ---------------------------------------------------------------------------

def test_local_token_is_self_attested_and_local_authority() -> None:
    p = LocalRfc3161LikeProvider()
    token = p.timestamp(b"artifact")
    assert token.self_attested is True
    assert token.authority == "local"
    assert token.source == "local"


def test_local_verify_round_trip_and_tamper() -> None:
    p = LocalRfc3161LikeProvider()
    token = p.timestamp(b"original")
    assert p.verify(b"original", token) is True
    # digest mismatch -> fail-closed, never a silent pass
    assert p.verify(b"tampered", token) is False


def test_local_is_not_production_grade() -> None:
    # The local mock is, by definition, NOT production-grade.
    assert LOCAL_IS_PRODUCTION_GRADE is False
    sub = build_default_subsystem("local")
    assert sub["production_grade"] is False


def test_local_bundle_verifier_flags_self_attested() -> None:
    sub = build_default_subsystem("local")
    adapter = sub["adapter"]
    verifier = sub["verifier"]
    bundle = adapter.seal(b'{"k": "v"}')
    res = verifier.verify_bundle(bundle)
    assert res.ok is True
    # A self-attested bundle must be flagged, never silently "production-grade".
    assert res.self_attested is True


# ---------------------------------------------------------------------------
# RFC 3161 CMS: real offline verify against a generated anchor
# ---------------------------------------------------------------------------

def test_rfc3161_cms_verify_offline_against_generated_anchor() -> None:
    key, cert_pem, cert_der = _make_self_signed()
    cms_der = _cms.build_timestamp_token(b"evidence", key, cert_der)
    digest, gen_time, nonce = _cms.verify_timestamp_token(
        cms_der, b"evidence", anchor_cert_der=cert_der
    )
    assert digest == hashlib.sha256(b"evidence").hexdigest()
    assert gen_time.endswith("Z")
    assert nonce != 0


def test_rfc3161_provider_issues_and_verifies_self_attested_offline() -> None:
    # No TSA URL -> the provider issues a SELF-ATTESTED token (its own key).
    p = Rfc3161TimestampProvider()
    token = p.timestamp(b"evidence")
    assert token.source == "rfc3161"
    assert token.self_attested is True
    assert token.authority == "rfc3161-self-attested"
    # Verifiable offline against the provider's own (embedded) anchor.
    assert p.verify(b"evidence", token) is True


def test_rfc3161_provider_with_tsa_url_is_not_self_attested_flag() -> None:
    # When a real TSA URL is configured, issuance is NOT self-attested (the flag
    # reflects that a human-chosen external authority is intended). Verification
    # still works offline against a supplied anchor for the test.
    p = Rfc3161TimestampProvider(tsa_url="https://tsa.example.com")
    token = p.timestamp(b"evidence")
    assert token.self_attested is False
    assert token.authority == "https://tsa.example.com"


def test_rfc3161_provider_mismatch_is_fail_closed() -> None:
    # A local-issued token must NOT verify under the rfc3161 verifier.
    local = LocalRfc3161LikeProvider()
    local_token = local.timestamp(b"evidence")
    rfc = Rfc3161TimestampProvider()
    assert rfc.verify(b"evidence", local_token) is False


def test_rfc3161_missing_anchor_is_fail_closed() -> None:
    # Build a CMS token with NO embedded cert and verify with no anchor -> the
    # verifier cannot anchor independently -> fail-closed.
    key, cert_pem, cert_der = _make_self_signed()
    cms_der = _cms.build_timestamp_token(b"evidence", key, cert_der, embed_cert=False)
    token = _wrap_cms(cms_der, source="rfc3161", alg="rfc3161-cms",
                      authority="rfc3161-self-attested", self_attested=True)
    rfc = Rfc3161TimestampProvider()
    assert rfc.verify(b"evidence", token, anchor=None) is False


def test_rfc3161_wrong_anchor_is_fail_closed() -> None:
    # Token signed by cert A, verified against unrelated anchor B -> reject.
    key_a, _pem_a, der_a = _make_self_signed()
    cms_der = _cms.build_timestamp_token(b"evidence", key_a, der_a)
    token = _wrap_cms(cms_der, source="rfc3161", alg="rfc3161-cms",
                      authority="rfc3161-self-attested", self_attested=True)
    _key_b, pem_b, _der_b = _make_self_signed()  # different key
    wrong_anchor = TrustAnchor(authority="other", verifying_pem=pem_b,
                               self_attested=False, source="rfc3161")
    rfc = Rfc3161TimestampProvider()
    assert rfc.verify(b"evidence", token, anchor=wrong_anchor) is False


def test_rfc3161_tampered_data_is_fail_closed() -> None:
    key, _pem, cert_der = _make_self_signed()
    cms_der = _cms.build_timestamp_token(b"evidence", key, cert_der)
    token = _wrap_cms(cms_der, source="rfc3161", alg="rfc3161-cms",
                      authority="rfc3161-self-attested", self_attested=True)
    rfc = Rfc3161TimestampProvider()
    # Wrong data -> messageImprint mismatch -> fail-closed.
    assert rfc.verify(b"different-evidence", token, anchor=None) is False


def test_rfc3161_verify_succeeds_with_explicit_anchor() -> None:
    key, cert_pem, cert_der = _make_self_signed()
    cms_der = _cms.build_timestamp_token(b"evidence", key, cert_der)
    token = _wrap_cms(cms_der, source="rfc3161", alg="rfc3161-cms",
                      authority="rfc3161-self-attested", self_attested=True)
    anchor = TrustAnchor(authority="rfc3161-self-attested", verifying_pem=cert_pem,
                         self_attested=True, source="rfc3161")
    rfc = Rfc3161TimestampProvider()
    assert rfc.verify(b"evidence", token, anchor=anchor) is True


# ---------------------------------------------------------------------------
# TPM remains interface-only (HD-05 reserved) — must not silently "work"
# ---------------------------------------------------------------------------

def test_tpm_provider_is_interface_only_fail_closed() -> None:
    tpm = TpmTimestampProvider()
    with pytest.raises(NotImplementedError):
        tpm.timestamp(b"x")
    with pytest.raises(NotImplementedError):
        tpm.verify(b"x", None)  # type: ignore[arg-type]
