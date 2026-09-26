"""HD-05 — RFC 3161 Time-Stamp Protocol provider (INTERFACE ONLY / phase 1).

This provider declares the RFC 3161 contract and implements the parts that are
possible OFFLINE without a live TSA:

  * :meth:`message_imprint` — the SHA-256 messageImprint (the controllable part
    of every TimeStampReq);
  * :meth:`build_timestamp_request` — a *valid DER* ``TimeStampReq`` (version=1,
    messageImprint) built by hand below, so the request shape is real and
    testable, not a placeholder;
  * :meth:`parse_response` — a best-effort, guarded extractor of ``genTime`` from
    a ``TimeStampResp`` DER (full CMS/PKCS#7 trust validation is phase 2).

It is deliberately NOT wired to a live TSA. :meth:`timestamp` / :meth:`verify`
raise ``NotImplementedError`` so a misconfigured caller fails loudly instead of
emitting an unsigned token. Final provider selection (which TSA, which trust
root) is a reserved human decision — see docs/autonomous/HUMAN-DECISION-BACKLOG.md
(HD-05). This module never performs a network call on its own.
"""
from __future__ import annotations

import hashlib
from typing import Optional

from .errors import TimestampVerificationError
from .interfaces import TimestampProvider, TimestampToken


# SHA-256 AlgorithmIdentifier (OID 2.16.840.1.101.3.4.2.1, NULL parameters).
_SHA256_OID_DER = b"\x06\x09\x60\x86\x48\x01\x65\x03\x04\x02\x01"
_SHA256_OID_LEN = len(_SHA256_OID_DER) + 2  # + SEQUENCE tag/len (15 bytes)


def _der_len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    # RFC 3161 requests here are tiny; only support short form.
    raise TimestampVerificationError(f"DER length {n} exceeds short form")


def _der_seq(content: bytes) -> bytes:
    return b"\x30" + _der_len(len(content)) + content


def _der_octet_string(data: bytes) -> bytes:
    return b"\x04" + _der_len(len(data)) + data


def _der_algid_sha256() -> bytes:
    # AlgorithmIdentifier ::= SEQUENCE { OID, NULL }
    return _der_seq(_SHA256_OID_DER + b"\x05\x00")


def _der_int1() -> bytes:
    return b"\x02\x01\x01"  # INTEGER 1


def _extract_octet_string(der: bytes, at: int) -> bytes:
    """Minimal DER OCTET STRING reader (short form only)."""
    if der[at] != 0x04:
        raise TimestampVerificationError("expected OCTET STRING tag")
    length = der[at + 1]
    start = at + 2
    return der[start:start + length]


class Rfc3161TimestampProvider(TimestampProvider):
    """RFC 3161 (Time-Stamp Protocol) backend — interface only in phase 1."""

    def __init__(self, tsa_url: Optional[str] = None):
        # Stored, not used: wiring a live TSA (request/response over HTTP, CMS
        # trust validation) is phase 2. Setting it does not enable network use.
        self.tsa_url = tsa_url

    @property
    def source_name(self) -> str:
        return "rfc3161"

    @property
    def alg(self) -> str:
        return "rfc3161"

    # -- offline, real parts -------------------------------------------------

    def message_imprint(self, artifact_bytes: bytes) -> bytes:
        """The RFC 3161 messageImprint: SHA-256 of the artifact (raw bytes)."""
        return hashlib.sha256(artifact_bytes).digest()

    def build_timestamp_request(self, artifact_bytes: bytes) -> bytes:
        """Build a valid DER ``TimeStampReq`` (version=1, messageImprint).

        The structure is:
            TimeStampReq ::= SEQUENCE {
                version        INTEGER { v1(1) },
                messageImprint MessageImprint }
            MessageImprint ::= SEQUENCE {
                hashAlgorithm  AlgorithmIdentifier,
                hashedMessage  OCTET STRING }
        This is exactly what a conformant TSA expects (minus optional
        reqPolicy/nonce/certReq, which default sensibly). The digest embedded
        round-trips via :meth:`_extract_message_imprint_digest`.
        """
        digest = self.message_imprint(artifact_bytes)
        message_imprint = _der_seq(_der_algid_sha256() + _der_octet_string(digest))
        return _der_seq(_der_int1() + message_imprint)

    def _extract_message_imprint_digest(self, req_der: bytes) -> bytes:
        """Decode the hashedMessage OCTET STRING inside a request built by
        :meth:`build_timestamp_request` (minimal, short-form DER).

        Navigation is by explicit tag+len (not by ``bytes.index`` for the inner
        OCTET STRING, because the SHA-256 OID 2.16.840.1.101.3.4.2.1 itself
        contains ``\\x04`` bytes that would otherwise be mis-matched). SEQUENCEs
        are *descended into* (skip tag+len only); the INTEGER version, OID and
        NULL are *skipped* entirely.
        """
        try:
            at = 2                       # skip outer TimeStampReq SEQUENCE tag+len
            # INTEGER 1 (version) — skip it entirely.
            if req_der[at] != 0x02:
                raise TimestampVerificationError("expected INTEGER version")
            at += 2 + req_der[at + 1]
            # MessageImprint SEQUENCE — descend into it.
            if req_der[at] != 0x30:
                raise TimestampVerificationError("expected MessageImprint SEQUENCE")
            at += 2
            # AlgorithmIdentifier SEQUENCE — descend into it.
            if req_der[at] != 0x30:
                raise TimestampVerificationError("expected AlgorithmIdentifier SEQUENCE")
            at += 2
            # OID — skip it entirely.
            if req_der[at] != 0x06:
                raise TimestampVerificationError("expected OID")
            at += 2 + req_der[at + 1]
            # NULL parameters (0x05 0x00) — skip it entirely.
            if req_der[at] != 0x05:
                raise TimestampVerificationError("expected NULL parameters")
            at += 2
            # hashedMessage OCTET STRING — this is the messageImprint.
            return _extract_octet_string(req_der, at)
        except (ValueError, IndexError) as exc:
            raise TimestampVerificationError(f"cannot decode TimeStampReq: {exc}") from exc

    def parse_response(self, der_bytes: bytes) -> dict:
        """Best-effort extractor of ``genTime`` from a TimeStampResp DER.

        A real TimeStampToken is a CMS/PKCS#7 ``SignedData``; full trust
        validation (certificate chain, ESSCertID, signature over the imprint)
        is phase 2. Here we locate the first ``GeneralizedTime`` (tag 0x18) or
        ``UTCTime`` (tag 0x17) and return it, so the response-parsing path is
        wired and testable against a fixture. Any malformed input raises
        :class:`TimestampVerificationError` (fail-closed).
        """
        for tag in (0x18, 0x17):
            idx = der_bytes.find(bytes([tag]))
            if idx >= 0:
                try:
                    length = der_bytes[idx + 1]
                    value = der_bytes[idx + 2:idx + 2 + length].decode("ascii")
                    return {"genTime": value, "time_tag": "GeneralizedTime" if tag == 0x18 else "UTCTime"}
                except (IndexError, UnicodeDecodeError) as exc:
                    raise TimestampVerificationError(f"malformed time in response: {exc}") from exc
        raise TimestampVerificationError("no genTime found in TimeStampResp")

    # -- not wired in phase 1 ----------------------------------------------

    def timestamp(self, data: bytes) -> TimestampToken:
        raise NotImplementedError(
            "Rfc3161TimestampProvider.timestamp is not wired in phase 1: no live "
            "TSA is configured. Set tsa_url AND implement the HTTP TimeStampReq/"
            "TimeStampResp round-trip + CMS trust validation before use."
        )

    def verify(self, data: bytes, token: TimestampToken) -> bool:
        raise NotImplementedError(
            "Rfc3161TimestampProvider.verify is not implemented in phase 1."
        )
