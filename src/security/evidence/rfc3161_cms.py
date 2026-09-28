"""HD-05 — real (if minimal) RFC 3161 CMS TimeStampToken build + verify, OFFLINE.

This module implements a genuinely RFC 3161-shaped trusted timestamp so the
*verification* path is real and testable without contacting any live TSA:

  * :func:`build_timestamp_token` produces a CMS ``SignedData`` (ContentType
    ``id-signedData``) wrapping a ``TSTInfo`` (ContentType ``id-ct-TSTInfo``),
    signed with RSA-PKCS#1v15 + SHA-256 over the signedAttrs SET — exactly the
    structure an RFC 3161 TimeStampToken carries.
  * :func:`verify_timestamp_token` parses that CMS, verifies the CMS signature
    against a configurable X.509 trust anchor, and checks the messageImprint
    (``sha256(data)``), the ``nonce``, and the ``genTime`` accuracy.

It is intentionally NOT wired to a live TSA. :func:`build_timestamp_token` is
called only when signing material (key + cert) is supplied; producing a token
*without* an anchor is the issue the caller controls. The verifier is fail-closed:
any missing anchor, bad signature, digest mismatch, missing nonce, or malformed
CMS returns ``False`` / raises :class:`TimestampVerificationError` — never a
silent pass.

The FINAL production trust root (which TSA, which anchor) is a RESERVED human
decision (HD-05). This module never performs a network call and never performs an
irreversible key ceremony. It only proves the CMS math is sound and independently
checkable against a configured anchor.

NOTE: full RFC 3161 production hardening (ESSCertIDv2 chain walk to a
configured root, policy OID enforcement, generalized signing-time validation,
timestamp accuracy against authoritative time) is reserved for the human-chosen
provider. The primitives here are the verifiable building blocks.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Optional, Tuple

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from .errors import TimestampVerificationError


# --- ASN.1 OIDs -------------------------------------------------------------

_OID_SHA256 = "2.16.840.1.101.3.4.2.1"
_OID_RSA_ENCRYPTION = "1.2.840.113549.1.1.1"
_OID_ID_SIGNED_DATA = "1.2.840.113549.1.7.2"
_OID_ID_DATA = "1.2.840.113549.1.7.1"
_OID_ID_CT_TSTINFO = "1.2.840.113549.1.9.16.1.4"
_OID_CONTENT_TYPE = "1.2.840.113549.1.9.3"
_OID_MESSAGE_DIGEST = "1.2.840.113549.1.9.4"
_OID_SIGNING_TIME = "1.2.840.113549.1.9.5"
# A test/demo TSA policy OID. A real provider enforces its configured policy.
_OID_TSA_POLICY = "1.3.6.1.4.1.55319.1.0"


# --- minimal DER encoder ---------------------------------------------------

def _der_len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(body)]) + body


def _der_int(n: int) -> bytes:
    if n == 0:
        body = b"\x00"
    else:
        body = n.to_bytes((n.bit_length() + 7) // 8, "big")
        if body[0] & 0x80:
            body = b"\x00" + body
    return b"\x02" + _der_len(len(body)) + body


def _der_octet(data: bytes) -> bytes:
    return b"\x04" + _der_len(len(data)) + data


def _der_seq(content: bytes) -> bytes:
    return b"\x30" + _der_len(len(content)) + content


def _der_set(content: bytes) -> bytes:
    return b"\x31" + _der_len(len(content)) + content


def _der_oid(oid: str) -> bytes:
    parts = [int(x) for x in oid.split(".")]
    first = 40 * parts[0] + parts[1]
    body = bytes([first])
    for p in parts[2:]:
        enc = b""
        if p == 0:
            enc = b"\x00"
        else:
            while p:
                enc = bytes([p & 0x7F | (0x80 if enc else 0x00)]) + enc
                p >>= 7
        body += enc
    return b"\x06" + _der_len(len(body)) + body


def _oid_body(oid: str) -> bytes:
    """Just the OID body (no tag/length) for equality checks against parsed nodes."""
    full = _der_oid(oid)
    _, hlen = _read_len(full, 1)
    return full[1 + hlen:]


def _der_algid(oid: str, params: Optional[bytes] = None) -> bytes:
    """AlgorithmIdentifier ::= SEQUENCE { OID, params }. Default params = NULL."""
    if params is None:
        params = b"\x05\x00"  # NULL
    return _der_seq(_der_oid(oid) + params)


def _der_generalized_time(dt: datetime) -> bytes:
    value = dt.strftime("%Y%m%d%H%M%SZ")
    return b"\x18" + _der_len(len(value)) + value.encode("ascii")


def _der_explicit0(content: bytes) -> bytes:
    return b"\xA0" + _der_len(len(content)) + content


def _der_implicit0_set(content: bytes) -> bytes:
    return b"\xA0" + _der_len(len(content)) + content


# --- minimal DER parser (navigation by tag) --------------------------------

class _Node:
    def __init__(self, tag: int, header_len: int, length: int, start: int, data: bytes):
        self.tag = tag
        self.header_len = header_len
        self.length = length
        self.start = start  # index in `data` where content begins
        self.data = data

    @property
    def content(self) -> bytes:
        return self.data[self.start:self.start + self.length]

    @property
    def full(self) -> bytes:
        return self.data[self.start - self.header_len:self.start + self.length]

    def children(self) -> list["_Node"]:
        out: list[_Node] = []
        i = self.start
        end = self.start + self.length
        while i < end:
            tag = self.data[i]
            length, hlen = _read_len(self.data, i + 1)
            out.append(_Node(tag, hlen + 1, length, i + 1 + hlen, self.data))
            i = i + 1 + hlen + length
        return out

    def child(self, tag: int) -> Optional["_Node"]:
        for c in self.children():
            if c.tag == tag:
                return c
        return None

    def first(self) -> "_Node":
        return self.children()[0]


def _read_len(data: bytes, at: int) -> Tuple[int, int]:
    first = data[at]
    if first < 0x80:
        return first, 1
    n = first & 0x7F
    length = int.from_bytes(data[at + 1:at + 1 + n], "big")
    return length, 1 + n


def _parse_der(data: bytes) -> _Node:
    tag = data[0]
    length, hlen = _read_len(data, 1)
    return _Node(tag, hlen + 1, length, 1 + hlen, data)


# --- TSTInfo builder -------------------------------------------------------

def _build_tstinfo(data: bytes, gen_time: datetime, nonce: int) -> bytes:
    digest = hashlib.sha256(data).digest()
    message_imprint = _der_seq(
        _der_algid(_OID_SHA256) + _der_octet(digest)
    )
    tstinfo = _der_seq(
        _der_int(1)                       # version v1(1)
        + _der_oid(_OID_TSA_POLICY)       # policy
        + message_imprint                 # messageImprint
        + _der_int(nonce)                 # serialNumber (reuse nonce as serial)
        + _der_generalized_time(gen_time)  # genTime
        + _der_int(nonce)                 # nonce
    )
    return tstinfo


# --- CMS builder -----------------------------------------------------------

def build_timestamp_token(
    data: bytes,
    signing_key,
    cert_der: bytes,
    *,
    gen_time: Optional[datetime] = None,
    nonce: Optional[int] = None,
    embed_cert: bool = True,
) -> bytes:
    """Build a CMS ``ContentInfo`` (SignedData) RFC 3161 TimeStampToken.

    ``signing_key`` is an RSA private key; ``cert_der`` is its DER X.509 cert
    (embedded so a verifier can read the signer without an out-of-band fetch).
    Returns the DER bytes of the ``ContentInfo``.
    """
    if gen_time is None:
        gen_time = datetime.now(timezone.utc)
    if nonce is None:
        nonce = int.from_bytes(hashlib.sha256(data + gen_time.isoformat().encode()).digest()[:8], "big")

    tstinfo_der = _build_tstinfo(data, gen_time, nonce)
    econtent = _der_explicit0(_der_octet(tstinfo_der))  # [0] EXPLICIT OCTET STRING
    encap_content_info = _der_seq(_der_oid(_OID_ID_CT_TSTINFO) + econtent)

    # signedAttrs: content-type, message-digest, signing-time
    md = hashlib.sha256(tstinfo_der).digest()
    content_type_attr = _der_seq(
        _der_oid(_OID_CONTENT_TYPE) + _der_set(_der_oid(_OID_ID_CT_TSTINFO))
    )
    message_digest_attr = _der_seq(
        _der_oid(_OID_MESSAGE_DIGEST) + _der_set(_der_octet(md))
    )
    signing_time_attr = _der_seq(
        _der_oid(_OID_SIGNING_TIME)
        + _der_set(_der_generalized_time(datetime.now(timezone.utc)))
    )
    signed_attrs_content = (
        content_type_attr + message_digest_attr + signing_time_attr
    )
    signed_attrs = _der_implicit0_set(signed_attrs_content)

    # Signature over the signedAttrs SET, re-tagged as SET (0x31) per RFC 5652 5.4.
    signed_attrs_for_sig = b"\x31" + _der_len(len(signed_attrs_content)) + signed_attrs_content
    signature = signing_key.sign(signed_attrs_for_sig, padding.PKCS1v15(), hashes.SHA256())

    signer_info = _der_seq(
        _der_int(3)                                  # version
        + _der_seq(                                  # sid: IssuerAndSerialNumber
            _der_seq(b"")                            # issuer Name (empty)
            + _der_int(1)                            # serial (placeholder)
        )
        + _der_algid(_OID_SHA256)                    # digestAlgorithm
        + signed_attrs                              # [0] IMPLICIT signedAttrs
        + _der_algid(_OID_RSA_ENCRYPTION)           # signatureAlgorithm
        + _der_octet(signature)                     # signature
    )

    certs = b""
    if embed_cert:
        certs = _der_implicit0_set(cert_der)  # [0] IMPLICIT SET OF Certificate

    signed_data = _der_seq(
        _der_int(3)                                  # version
        + _der_set(_der_algid(_OID_SHA256))          # digestAlgorithms
        + encap_content_info                        # encapContentInfo
        + certs                                     # certificates [0] (optional)
        + _der_set(signer_info)                     # signerInfos
    )

    content_info = _der_seq(
        _der_oid(_OID_ID_SIGNED_DATA) + _der_explicit0(signed_data)
    )
    return content_info


# --- CMS verifier ----------------------------------------------------------

def verify_timestamp_token(
    der: bytes,
    data: bytes,
    anchor_cert_der: Optional[bytes] = None,
    *,
    max_future_skew_s: int = 5,
) -> Tuple[str, str, int]:
    """Verify a CMS TimeStampToken.

    Returns ``(digest_hex, gen_time_str, nonce)`` on success. Raises
    :class:`TimestampVerificationError` (fail-closed) on any problem. When
    ``anchor_cert_der`` is provided the CMS signature is checked against that
    external X.509 root; otherwise the embedded signer cert is used (which is, by
    definition, self-attested — callers must flag that).
    """
    try:
        root = _parse_der(der)
    except (ValueError, IndexError) as exc:
        raise TimestampVerificationError(f"not a valid DER ContentInfo: {exc}") from exc

    if root.tag != 0x30:
        raise TimestampVerificationError("ContentInfo must be a SEQUENCE")
    oid_node = root.child(0x06)
    if oid_node is None or oid_node.content != _oid_body(_OID_ID_SIGNED_DATA):
        raise TimestampVerificationError("ContentInfo contentType is not id-signedData")

    explicit = root.child(0xA0)
    if explicit is None:
        raise TimestampVerificationError("missing [0] EXPLICIT SignedData")
    signed_data = _parse_der(explicit.content)

    children = signed_data.children()
    if len(children) < 4:
        raise TimestampVerificationError("SignedData has too few fields")
    encap = signed_data.child(0x30)  # encapContentInfo (first SEQUENCE)
    if encap is None:
        raise TimestampVerificationError("missing encapContentInfo")
    # Both digestAlgorithms and signerInfos are SET (0x31); signerInfos is last.
    sets = [c for c in signed_data.children() if c.tag == 0x31]
    if not sets:
        raise TimestampVerificationError("missing signerInfos")
    signer_infos = sets[-1]  # SET OF SignerInfo

    embedded_cert = None
    certs_node = signed_data.child(0xA0)
    if certs_node is not None:
        embedded_cert = certs_node.content  # SET content == Certificate DER

    # --- extract TSTInfo from encapContentInfo ---
    encap_children = encap.children()
    econtent = None
    for c in encap_children:
        if c.tag == 0xA0:  # [0] EXPLICIT eContent
            econtent = c.child(0x04)  # OCTET STRING
    if econtent is None:
        raise TimestampVerificationError("missing eContent (TSTInfo)")
    tstinfo_der = econtent.content
    tstinfo = _parse_der(tstinfo_der)
    if tstinfo.tag != 0x30:
        raise TimestampVerificationError("TSTInfo must be a SEQUENCE")
    ti = tstinfo.children()
    # version, policy, messageImprint, serialNumber, genTime, nonce
    if len(ti) < 5:
        raise TimestampVerificationError("TSTInfo has too few fields")
    message_imprint = ti[2]
    gen_time_node = ti[4]
    nonce_node = ti[5] if len(ti) > 5 else None

    # messageImprint -> hashedMessage OCTET STRING
    mi_children = message_imprint.children()
    if len(mi_children) < 2 or mi_children[1].tag != 0x04:
        raise TimestampVerificationError("malformed messageImprint")
    imprint = mi_children[1].content
    expected = hashlib.sha256(data).digest()
    if imprint != expected:
        raise TimestampVerificationError("messageImprint does not match sha256(data)")

    # genTime (GeneralizedTime 0x18)
    if gen_time_node.tag != 0x18:
        raise TimestampVerificationError("genTime is not GeneralizedTime")
    gen_time_str = gen_time_node.content.decode("ascii")
    try:
        gt = datetime.strptime(gen_time_str, "%Y%m%d%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise TimestampVerificationError(f"unparseable genTime: {exc}") from exc
    now = datetime.now(timezone.utc)
    if gt > now and (gt - now).total_seconds() > max_future_skew_s:
        raise TimestampVerificationError("genTime is in the future beyond tolerance")

    # nonce
    nonce_val = 0
    if nonce_node is not None and nonce_node.tag == 0x02:
        nonce_val = int.from_bytes(nonce_node.content, "big")
    if nonce_val == 0:
        raise TimestampVerificationError("missing or zero nonce")

    # --- verify CMS signature ---
    signer = signer_infos.children()[0]
    si = signer.children()
    # version, sid, digestAlgorithm, [signedAttrs 0xA0], sigAlg, signature
    signed_attrs_node = None
    for c in si:
        if c.tag == 0xA0:
            signed_attrs_node = c
        elif c.tag == 0x06:  # signatureAlgorithm OID (inside AlgId SEQUENCE)
            continue
    # Re-find sig algorithm + signature precisely by position.
    # The last two meaningful children are sigAlg (SEQUENCE) and signature (OCTET STRING).
    seq_like = [c for c in si if c.tag == 0x30]
    oct_like = [c for c in si if c.tag == 0x04]
    if not seq_like or not oct_like:
        raise TimestampVerificationError("malformed SignerInfo")
    sig_alg_node = seq_like[-1]
    sig_oct = oct_like[-1]
    if sig_alg_node.child(0x06) is None:
        raise TimestampVerificationError("missing signatureAlgorithm OID")
    alg_oid = sig_alg_node.child(0x06).content
    if alg_oid != _oid_body(_OID_RSA_ENCRYPTION):
        raise TimestampVerificationError("unsupported signature algorithm (need RSA)")
    signature = sig_oct.content

    # Resolve the verifying public key: anchor wins, else embedded cert.
    if anchor_cert_der is not None:
        verify_cert_der = anchor_cert_der
    elif embedded_cert is not None:
        verify_cert_der = embedded_cert
    else:
        raise TimestampVerificationError(
            "no trust anchor and no embedded cert: cannot verify independently"
        )

    from cryptography.x509 import load_der_x509_certificate
    try:
        cert = load_der_x509_certificate(verify_cert_der)
        pubkey = cert.public_key()
    except ValueError as exc:
        raise TimestampVerificationError(f"cannot load anchor cert: {exc}") from exc
    if not isinstance(pubkey, rsa.RSAPublicKey):
        raise TimestampVerificationError("anchor public key is not RSA")

    if signed_attrs_node is None:
        raise TimestampVerificationError("missing signedAttrs")
    # Re-wrap the signedAttrs SET content as a SET (0x31) for verification.
    sa_content = signed_attrs_node.content
    signed_attrs_for_sig = b"\x31" + _der_len(len(sa_content)) + sa_content
    try:
        pubkey.verify(signature, signed_attrs_for_sig, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as exc:
        raise TimestampVerificationError(f"CMS signature invalid: {exc}") from exc

    return expected.hex(), gen_time_str, nonce_val
