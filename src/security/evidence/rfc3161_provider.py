"""HD-05 — RFC 3161 Time-Stamp Protocol provider (REAL CMS verify, offline-capable).

Unlike phase 1 (which only declared the contract and raised ``NotImplementedError``
for every operation), this provider implements a genuinely RFC 3161-shaped
trusted timestamp:

  * :meth:`timestamp` builds a real CMS ``SignedData`` (``id-signedData``) wrapping
    a ``TSTInfo`` (``id-ct-TSTInfo``), signed with RSA-PKCS#1v15 + SHA-256 over
    the signedAttrs SET — the same structure an RFC 3161 TimeStampToken carries.
  * :meth:`verify` parses that CMS and checks the signature against a configurable
    X.509 trust anchor, plus the messageImprint (``sha256(data)``), the ``nonce``,
    and the ``genTime`` accuracy. The verification logic is REAL and testable
    OFFLINE (no live TSA) against a generated anchor.

WHY NO ``NotImplementedError``:
------------------------------
The verification path is the part that matters for trust, and it is fully
implemented here. Issuing a token still needs signing material; when none is
supplied the provider generates an ephemeral self-signed key+cert so the stack
stays usable offline — but the resulting token is explicitly ``self_attested``.
Contacting a *live* external TSA (HTTP TimeStampReq/TimeStampResp round-trip) is
NOT done here; that is a wiring detail for the human-chosen production provider.

ROOT-OF-TRUST DISCIPLINE (HD-05):
--------------------------------
If no real TSA URL is configured (``tsa_url is None``), every token this provider
issues is **self-attested** (its own key is the "authority") and is flagged
``self_attested=True`` with ``authority`` naming the local anchor. It is VERIFIED
cryptographically but is NOT independently rooted in a third party, so it must
never be presented as production-grade. Only a human decision (HD-05) picks the
real, independently-verified TSA + trust root.

This module never performs a network call on its own.
"""
from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timezone
from typing import Optional

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from .errors import TimestampVerificationError
from .interfaces import TimestampProvider, TimestampToken, TrustAnchor, artifact_digest
from . import rfc3161_cms as _cms


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


def _pem_cert_to_der(pem: str) -> bytes:
    cert = x509.load_pem_x509_certificate(pem.encode("utf-8"))
    return cert.public_bytes(serialization.Encoding.DER)


class Rfc3161TimestampProvider(TimestampProvider):
    """RFC 3161 (Time-Stamp Protocol) backend — real CMS verify, offline-capable.

    The verification logic is fully implemented and testable offline against a
    generated trust anchor. Issuance works offline too: when no ``signing_key_pem``
    / ``signing_cert_pem`` is supplied, an ephemeral self-signed key+cert is
    generated so the stack remains usable — but such tokens are ``self_attested``.
    """

    def __init__(self, tsa_url: Optional[str] = None, trust_anchor_pem: Optional[str] = None,
                 signing_key_pem: Optional[str] = None, signing_cert_pem: Optional[str] = None):
        # Stored, not used for network: wiring a live TSA (request/response over
        # HTTP) is a detail for the human-chosen production provider (HD-05).
        self.tsa_url = tsa_url

        # Resolve signing material. Prefer explicit key+cert; else generate an
        # ephemeral self-signed key+cert (offline, self-attested).
        if signing_key_pem is not None and signing_cert_pem is not None:
            self._key = serialization.load_pem_private_key(
                signing_key_pem.encode("utf-8"), password=None
            )
            if not isinstance(self._key, rsa.RSAPrivateKey):
                raise TimestampVerificationError("signing_key_pem is not an RSA private key")
            cert = x509.load_pem_x509_certificate(signing_cert_pem.encode("utf-8"))
        else:
            self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            cert = _self_signed_cert(self._key)

        self._cert = cert
        self._cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode("utf-8")
        self._cert_der = cert.public_bytes(serialization.Encoding.DER)

        # The trust anchor defaults to our own cert (self-attested). A real
        # provider overrides this with the configured external TSA root.
        self._trust_anchor_pem = trust_anchor_pem or self._cert_pem

    # -- authority / self-attestation --------------------------------------

    @property
    def source_name(self) -> str:
        return "rfc3161"

    @property
    def alg(self) -> str:
        return "rfc3161-cms"

    @property
    def authority(self) -> str:
        """Name of the attesting authority.

        A real TSA host if configured, otherwise an explicit self-attested label
        so callers never mistake the offline mock for a live TSA.
        """
        return self.tsa_url or "rfc3161-self-attested"

    @property
    def is_self_attested(self) -> bool:
        """True when no real external TSA is configured (HD-05 reserved)."""
        return self.tsa_url is None

    def trust_anchor(self) -> TrustAnchor:
        """The trust anchor this provider verifies against (for offline tests)."""
        return TrustAnchor(
            authority=self.authority,
            verifying_pem=self._trust_anchor_pem,
            self_attested=self.is_self_attested,
            source="rfc3161",
        )

    # -- issuer -------------------------------------------------------------

    def timestamp(self, data: bytes) -> TimestampToken:
        """Issue a real CMS TimeStampToken (offline).

        The token is ``self_attested`` iff no real TSA URL is configured. The
        signature is verifiable offline against the trust anchor; the flag is what
        stops anyone presenting a self-attested token as independently verified.
        """
        cms_der = _cms.build_timestamp_token(data, self._key, self._cert_der)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return TimestampToken(
            source=self.source_name, alg=self.alg, ts=ts,
            digest=artifact_digest(data), token=base64.b64encode(cms_der).decode("ascii"),
            pubkey_id=self._cert_thumbprint(),
            authority=self.authority,
            self_attested=self.is_self_attested,
        )

    def _cert_thumbprint(self) -> str:
        return hashlib.sha256(self._cert_der).hexdigest()[:16]

    # -- verifier (REAL, offline-capable) -----------------------------------

    def verify(self, data: bytes, token: TimestampToken,
               anchor: Optional[TrustAnchor] = None) -> bool:
        """Verify a CMS TimeStampToken against a trust anchor (fail-closed).

        Provider mismatch (``token.source != "rfc3161"``) is rejected. When
        ``anchor`` is supplied its cert is the external root of trust; otherwise
        the token's embedded signer cert is used (which is, by definition,
        self-attested). Any malformed CMS / bad signature / digest mismatch /
        missing nonce returns False — never a silent pass.
        """
        if token.source != self.source_name:
            return False
        if token.alg != self.alg:
            return False
        try:
            cms_der = base64.b64decode(token.token)
        except (ValueError, TypeError):
            return False
        anchor_cert_der = None
        if anchor is not None:
            try:
                anchor_cert_der = _pem_cert_to_der(anchor.verifying_pem)
            except ValueError:
                return False
        try:
            _cms.verify_timestamp_token(cms_der, data, anchor_cert_der=anchor_cert_der)
            return True
        except TimestampVerificationError:
            return False

    # -- offline, real parts (request shaping, kept for compatibility) ------

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
        validation is implemented in :mod:`rfc3161_cms`. Here we locate the first
        ``GeneralizedTime`` (tag 0x18) or ``UTCTime`` (tag 0x17) and return it, so
        the response-parsing path is wired and testable against a fixture. Any
        malformed input raises :class:`TimestampVerificationError` (fail-closed).
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


def _self_signed_cert(key: rsa.RSAPrivateKey) -> x509.Certificate:
    """Generate an ephemeral self-signed cert (offline anchor / signing cert)."""
    from cryptography.x509.oid import NameOID

    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "local-rfc3161-anchor")])
    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime(2020, 1, 1))
        .not_valid_after(datetime(2035, 1, 1))
        .sign(key, hashes.SHA256())
    )
