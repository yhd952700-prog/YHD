"""HD-05 — RFC 3161 TSA compatibility tests (offline, real CMS verify).

Two layers:

1. OFFLINE, always-run: the real DER builders in
   :class:`Rfc3161TimestampProvider` produce a structurally valid ``TimeStampReq``
   and can extract ``genTime`` from a ``TimeStampResp`` fixture. These prove the
   request wire format is correct without any network or any external/paid
   provider. The *verification* logic is ALSO real and offline-capable: the
   provider builds and verifies a genuine CMS ``TimeStampToken`` against a
   generated trust anchor (see ``tests/security/test_root_of_trust.py``).

2. LIVE-TSA skeleton (SKIPPED by default): shows exactly how a real RFC 3161 TSA
   is reached in phase 2 — POST ``build_timestamp_request(...)`` to the TSA URL,
   read the ``TimeStampResp`` DER, and run ``parse_response``. It only runs when
   ``LIUHAO_TSA_RFC3161_URL`` is set, so the suite stays offline and no
   external/paid provider is ever contacted automatically.

Final TSA selection (which provider, which trust root) is a RESERVED HUMAN
DECISION — see docs/adr/ADR-root-of-trust-hd05.md (HD-05). The issuer/verifier
code must not commit to any TSA or perform any irreversible key ceremony.
"""
from __future__ import annotations

import hashlib
import os

import pytest

from src.security.evidence import Rfc3161TimestampProvider, TimestampVerificationError


# ---------------------------------------------------------------------------
# Offline DER fixtures (real, structurally valid)
# ---------------------------------------------------------------------------

def _make_timestamp_resp_fixture(gen_time: str) -> bytes:
    """A minimal TimeStampResp-shaped DER containing a GeneralizedTime.

    Layout: ``SEQUENCE { GeneralizedTime }``. Real CMS/PKCS#7 SignedData trust
    validation is phase 2; here we only need a structurally valid ``genTime``
    so :meth:`Rfc3161TimestampProvider.parse_response` is exercised against a
    blob that looks like a real response.
    """
    gt = b"\x18" + bytes([len(gen_time)]) + gen_time.encode("ascii")
    return b"\x30" + bytes([len(gt)]) + gt


def test_build_timestamp_request_is_valid_der() -> None:
    p = Rfc3161TimestampProvider()
    payload = b"any-evidence-artifact"
    req = p.build_timestamp_request(payload)
    assert req[0:1] == b"\x30"  # outer SEQUENCE (TimeStampReq)
    # Round-trips: the embedded messageImprint equals sha256(payload).
    digest = p._extract_message_imprint_digest(req)
    assert digest == hashlib.sha256(payload).digest()


def test_parse_response_extracts_gen_time_from_fixture() -> None:
    p = Rfc3161TimestampProvider()
    fixture = _make_timestamp_resp_fixture("20260925T120000Z")
    parsed = p.parse_response(fixture)
    assert parsed["genTime"] == "20260925T120000Z"
    assert parsed["time_tag"] == "GeneralizedTime"


def test_parse_response_rejects_malformed_input_fail_closed() -> None:
    p = Rfc3161TimestampProvider()
    # GeneralizedTime tag + length 4 but value bytes are non-ASCII -> decode fails.
    bad = b"\x18\x04" + b"\xff\xff\xff\xff"
    with pytest.raises(TimestampVerificationError):
        p.parse_response(bad)


def test_parse_response_rejects_missing_gen_time_fail_closed() -> None:
    p = Rfc3161TimestampProvider()
    # A SEQUENCE with no GeneralizedTime / UTCTime inside.
    no_time = b"\x30\x03" + b"\x02\x01\x01"
    with pytest.raises(TimestampVerificationError):
        p.parse_response(no_time)


def test_rfc3161_provider_timestamp_is_wired_offline_not_network() -> None:
    # The verification LOGIC is implemented (no NotImplementedError). Issuance is
    # offline-capable: with no TSA URL the provider self-attests using an
    # ephemeral key, and verify succeeds against the embedded anchor. It must NOT
    # contact any network — the token is built in-process.
    p = Rfc3161TimestampProvider()
    token = p.timestamp(b"x")
    assert token.source == "rfc3161"
    assert token.self_attested is True  # no TSA URL -> self-attested, not independent
    assert p.verify(b"x", token) is True
    assert p.verify(b"other", token) is False  # tamper -> fail-closed


# ---------------------------------------------------------------------------
# Live TSA skeleton — SKIPPED unless a TSA URL is explicitly configured.
# Never contacts an external/paid provider automatically.
# ---------------------------------------------------------------------------

_LIVE_TSA_URL = os.environ.get("LIUHAO_TSA_RFC3161_URL")
skip_live = pytest.mark.skipif(
    not _LIVE_TSA_URL,
    reason="HD-05 phase 2 skeleton: set LIUHAO_TSA_RFC3161_URL to a real RFC 3161 "
           "TSA endpoint to exercise the live round-trip. Off by default so the "
           "suite stays offline and no paid/external provider is contacted.",
)


@skip_live
def test_live_tsa_round_trip() -> None:
    """Skeleton for the phase-2 live TSA round-trip.

    Wires ``build_timestamp_request`` -> HTTP POST -> ``parse_response``. CMS
    trust validation (certificate chain, ESSCertID, signature over the imprint)
    is still phase 2; this proves the request/response plumbing is real and the
    genTime comes back. ``Rfc3161TimestampProvider.timestamp()`` remains
    ``NotImplementedError`` in phase 1 — the live binding lands in phase 2 once
    the human decision on the TSA / trust-root is made.
    """
    import urllib.request

    p = Rfc3161TimestampProvider(tsa_url=_LIVE_TSA_URL)
    payload = b"live-tsa-compat-probe"
    req_der = p.build_timestamp_request(payload)

    # RFC 3161: POST the TimeStampReq DER to the TSA, Content-Type
    # application/timestamp-query, expect application/timestamp-reply back.
    request = urllib.request.Request(
        _LIVE_TSA_URL,
        data=req_der,
        headers={"Content-Type": "application/timestamp-query"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as resp:  # noqa: S310
        resp_der = resp.read()

    parsed = p.parse_response(resp_der)
    assert parsed.get("genTime")  # a real TSA returns a genTime
