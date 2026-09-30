"""Trusted timestamping for Root-of-Trust audit-head attestations (U53 evidence grade).

WHY THIS EXISTS
---------------
``root_of_trust.py`` lets a Root-of-Trust (RoT) key SIGN the audit chain head,
so an external party can verify "this head was attested by the authority that
owns the chain" without trusting the DB. But a RoT signature alone is
*self-asserted in time*: nothing stops an attacker who later compromises the RoT
key from back-dating a forged attestation to "yesterday". A **trusted
timestamp** from an independent Timestamp Authority (TSA, RFC 3161) binds the
attestation to a point in time certified by a THIRD party -- turning the
attestation from "signed at some unknown time" into "signed and timestamped by
TSA at 2026-09-30T12:00:00Z". That is the evidence-grade property a
human-sovereignty audit trail needs.

DESIGN -- honest, fail-closed, opt-in, dependency-declared
---------------------------------------------------------
  * The timestamp request (``TimeStampReq``) is built with a SMALL, self-contained
    pure-Python DER encoder (no third-party ASN.1 dependency). The attestation
    payload bytes (the same bytes the RoT signature covers) are hashed
    (SHA-256) and sent to the TSA.
  * The TSA crypto operation (minting + verifying the RFC 3161 token) is
    delegated to the system ``openssl ts`` engine -- the reference
    implementation. This is a DECLARED dependency: if ``openssl`` is absent,
    verification FAILS CLOSED (never silently accepts). A real deployment may
    instead point ``tsa_url`` at an HTTP RFC 3161 authority; the request is
    identical, only transport changes.
  * Token parsing (extracting ``genTime`` and pulling the token out of a
    ``TimeStampResp``) is done with the same small DER walker -- no fragile
    text scraping of ``openssl`` output.
  * Verification dispatches the cryptographic check to ``openssl ts -verify``;
    we never claim "timestamped" unless that command returns 0.

This module is storage-agnostic and independently testable. The live HC-01
integration decides *when* to call it (after HC-01 stabilises); this code just
provides the capability.

ENVIRONMENT (opt-in, all optional)
  LIUHAO_AUDIT_TSA_CERT  path to TSA cert PEM (pinned, used to verify tokens)
  LIUHAO_AUDIT_TSA_URL   optional HTTP RFC 3161 endpoint (production)
"""
from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import os
import subprocess
import tempfile
from typing import Optional, Tuple

#: SHA-256 OBJECT IDENTIFIER (2.16.840.1.101.3.4.2.1).
_OID_SHA256 = "2.16.840.1.101.3.4.2.1"
#: id-ct-TSTInfo content type (1.2.840.113549.1.9.16.1.4) -- used to spot the
#: TimeStampToken inside a TimeStampResp.
_OID_TST_INFO = "1.2.840.113549.1.9.16.1.4"


# --------------------------------------------------------------------------- #
# Minimal DER encoder (only what RFC 3161 TimeStampReq needs)
# --------------------------------------------------------------------------- #
def _der_len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    out: list[int] = []
    while n:
        out.insert(0, n & 0xFF)
        n >>= 8
    return bytes([0x80 | len(out)]) + bytes(out)


def _der(tag: int, content: bytes) -> bytes:
    return bytes([tag]) + _der_len(len(content)) + content


def der_integer(i: int) -> bytes:
    if i == 0:
        body = b"\x00"
    else:
        body = i.to_bytes((i.bit_length() + 7) // 8 or 1, "big")
        if body[0] & 0x80:
            body = b"\x00" + body
    return _der(0x02, body)


def der_octet_string(s: bytes) -> bytes:
    return _der(0x04, s)


def der_sequence(*items: bytes) -> bytes:
    return _der(0x30, b"".join(items))


def der_null() -> bytes:
    return _der(0x05, b"")


def der_boolean(b: bool) -> bytes:
    return _der(0x01, b"\xff" if b else b"\x00")


def der_oid(oid: str) -> bytes:
    parts = [int(x) for x in oid.split(".")]
    first = 40 * parts[0] + parts[1]
    enc: list[int] = []
    remainder = [first] + parts[2:]
    for p in remainder:
        if p == 0:
            enc.append(0)
            continue
        buf: list[int] = []
        v = p
        while v:
            buf.insert(0, v & 0x7F)
            v >>= 7
        # Set the high bit on every byte EXCEPT the last (base-128).
        for i in range(len(buf) - 1):
            buf[i] |= 0x80
        enc.extend(buf)
    return _der(0x06, bytes(enc))


def der_generalized_time(dt: _dt.datetime) -> bytes:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    s = dt.astimezone(_dt.timezone.utc).strftime("%Y%m%d%H%M%SZ")
    return _der(0x18, s.encode("ascii"))


# --------------------------------------------------------------------------- #
# Minimal DER parser (to extract genTime from a token / token from a response)
# --------------------------------------------------------------------------- #
def _read_der_length(data: bytes, i: int) -> Tuple[int, int]:
    first = data[i]
    if first < 0x80:
        return first, i + 1
    nbytes = first & 0x7F
    length = 0
    for j in range(nbytes):
        length = (length << 8) | data[i + 1 + j]
    return length, i + 1 + nbytes


def _parse_der(data: bytes, i: int = 0):
    tag = data[i]
    length, j = _read_der_length(data, i + 1)
    content_start = j
    content_end = j + length
    children = []
    if tag & 0x20:  # constructed -> recurse
        k = content_start
        while k < content_end:
            child, k = _parse_der(data, k)
            children.append(child)
    return (
        {
            "tag": tag,
            "start": content_start,
            "len": length,
            "end": content_end,
            "children": children,
            "raw": data[content_start:content_end],
            "full": data[i:content_end],
        },
        content_end,
    )


def _find_node(node: dict, pred) -> Optional[dict]:
    if pred(node):
        return node
    for c in node.get("children", []):
        r = _find_node(c, pred)
        if r:
            return r
    return None


def _extract_tstinfo(token_der: bytes) -> Optional[dict]:
    """Locate the TSTInfo SEQUENCE inside an RFC 3161 TimeStampToken.

    Token = ContentInfo -> SignedData -> EncapsulatedContentInfo
    (eContent is [0] IMPLICIT OCTET STRING whose value is the TSTInfo DER).
    """
    root, _ = _parse_der(token_der)

    def pred(n: dict) -> bool:
        # An [0] EXPLICIT container (0xA0) holding an OCTET STRING (0x04) that
        # itself parses as a SEQUENCE (0x30) == the TSTInfo.
        if n["tag"] != 0xA0:
            return False
        for cc in n["children"]:
            if cc["tag"] == 0x04:
                try:
                    inner, _ = _parse_der(cc["raw"])
                    if inner["tag"] == 0x30:
                        return True
                except Exception:
                    return False
        return False

    container = _find_node(root, pred)
    if container is None:
        return None
    for cc in container["children"]:
        if cc["tag"] == 0x04:
            inner, _ = _parse_der(cc["raw"])
            return inner
    return None


def _find_generalized_time(node: dict) -> Optional[_dt.datetime]:
    for c in node.get("children", []):
        if c["tag"] == 0x18:
            return _parse_generalized_time(c["raw"])
        if c["tag"] & 0x20:
            r = _find_generalized_time(c)
            if r:
                return r
    return None


def _parse_generalized_time(raw: bytes) -> _dt.datetime:
    s = raw.decode("ascii").strip()
    # Accept 'YYYYMMDDHHMMSSZ' (and tolerate fractional seconds).
    if s.endswith("Z"):
        s = s[:-1]
    if "." in s:
        head, frac = s.split(".", 1)
        frac = (frac + "000000")[:6]
        s = head + "." + frac
        return _dt.datetime.strptime(s, "%Y%m%d%H%M%S.%f").replace(
            tzinfo=_dt.timezone.utc
        )
    return _dt.datetime.strptime(s, "%Y%m%d%H%M%S").replace(
        tzinfo=_dt.timezone.utc
    )


def extract_gen_time(token_der: bytes) -> Optional[_dt.datetime]:
    """Extract the TSTInfo genTime from a TimeStampToken, or None if not found."""
    tstinfo = _extract_tstinfo(token_der)
    if tstinfo is None:
        return None
    return _find_generalized_time(tstinfo)


def _find_content_info(node: dict) -> Optional[dict]:
    """Find the ContentInfo SEQUENCE (TimeStampToken) anywhere in a DER tree.

    A ContentInfo is a SEQUENCE whose first child is an OID (the contentType).
    This is robust to whether the token is wrapped in a ``[0]`` context tag or
    appears directly, as both real and hand-built responses do.
    """
    if node["tag"] == 0x30 and node["children"] and node["children"][0]["tag"] == 0x06:
        return node
    for c in node.get("children", []):
        r = _find_content_info(c)
        if r:
            return r
    return None


def extract_token_from_response(resp_der: bytes) -> Optional[bytes]:
    """Pull the TimeStampToken (ContentInfo) out of a TimeStampResp DER blob."""
    root, _ = _parse_der(resp_der)
    node = _find_content_info(root)
    if node is None:
        return None
    return node["full"]


# --------------------------------------------------------------------------- #
# TimeStampReq construction
# --------------------------------------------------------------------------- #
def build_timestamp_query(data: bytes, nonce: int) -> bytes:
    """Build an RFC 3161 TimeStampReq (version 1) over SHA-256(data)."""
    digest = hashlib.sha256(data).digest()
    algorithm = der_sequence(der_oid(_OID_SHA256), der_null())
    message_imprint = der_sequence(algorithm, der_octet_string(digest))
    # SEQUENCE { version INTEGER 1, messageImprint, nonce INTEGER, certReq TRUE }
    return der_sequence(
        der_integer(1),
        message_imprint,
        der_integer(nonce),
        der_boolean(True),
    )


# --------------------------------------------------------------------------- #
# TSA engine -- openssl ts (reference implementation), fail-closed
# --------------------------------------------------------------------------- #
def _openssl() -> str:
    exe = shutil_which("openssl")
    if exe is None:
        raise RuntimeError(
            "openssl not found on PATH; RFC 3161 timestamping requires the "
            "system openssl 'ts' engine (or set LIUHAO_AUDIT_TSA_URL)"
        )
    return exe


def shutil_which(name: str) -> Optional[str]:
    import shutil

    return shutil.which(name)


def _mint_via_openssl(
    query_der: bytes,
    signer_cert_pem: bytes,
    signer_key_pem: bytes,
    chain_cert_pem: bytes,
) -> bytes:
    exe = _openssl()
    with tempfile.TemporaryDirectory() as d:
        import pathlib

        q = pathlib.Path(d) / "query.der"
        tok = pathlib.Path(d) / "token.der"
        cert = pathlib.Path(d) / "signer.pem"
        key = pathlib.Path(d) / "signer.key"
        chain = pathlib.Path(d) / "chain.pem"
        q.write_bytes(query_der)
        cert.write_bytes(signer_cert_pem)
        key.write_bytes(signer_key_pem)
        chain.write_bytes(chain_cert_pem)
        r = subprocess.run(
            [
                exe,
                "ts",
                "-reply",
                "-queryfile",
                str(q),
                "-signer",
                str(cert),
                "-inkey",
                str(key),
                "-chain",
                str(chain),
                "-out",
                str(tok),
                "-token_out",
            ],
            capture_output=True,
        )
        if r.returncode != 0:
            raise RuntimeError(
                "openssl ts -reply failed: "
                + (r.stderr.decode("utf-8", "replace") or r.stdout.decode("utf-8", "replace"))
            )
        return tok.read_bytes()


def _verify_via_openssl(
    token_der: bytes,
    data: bytes,
    ca_cert_pem: bytes,
    untrusted_cert_pem: Optional[bytes] = None,
) -> Tuple[bool, str]:
    exe = _openssl()
    with tempfile.TemporaryDirectory() as d:
        import pathlib

        tok = pathlib.Path(d) / "token.der"
        dataf = pathlib.Path(d) / "data.bin"
        ca = pathlib.Path(d) / "ca.pem"
        tok.write_bytes(token_der)
        dataf.write_bytes(data)
        ca.write_bytes(ca_cert_pem)
        cmd = [
            exe,
            "ts",
            "-verify",
            "-token_in",
            "-in",
            str(tok),
            "-data",
            str(dataf),
            "-CAfile",
            str(ca),
            "-partial_chain",
        ]
        if untrusted_cert_pem is not None and untrusted_cert_pem != ca_cert_pem:
            untrusted = pathlib.Path(d) / "untrusted.pem"
            untrusted.write_bytes(untrusted_cert_pem)
            cmd += ["-untrusted", str(untrusted)]
        r = subprocess.run(cmd, capture_output=True)
        reason = (r.stderr or r.stdout).decode("utf-8", "replace").strip()
        return r.returncode == 0, reason


class TimestampToken:
    """A verified-or-pending RFC 3161 token plus provenance we persist."""

    __slots__ = ("token_der", "gen_time", "tsa_cert_id", "token_b64")

    def __init__(
        self,
        token_der: bytes,
        gen_time: Optional[_dt.datetime],
        tsa_cert_id: str,
    ):
        self.token_der = token_der
        self.gen_time = gen_time
        self.tsa_cert_id = tsa_cert_id
        self.token_b64 = base64.b64encode(token_der).decode("ascii")

    def to_record(self) -> dict:
        return {
            "tsa_token": self.token_b64,
            "tsa_gen_time": self.gen_time.isoformat() if self.gen_time else None,
            "tsa_cert_id": self.tsa_cert_id,
        }


class TrustedTimestampAuthority:
    """Obtain + verify RFC 3161 trusted timestamps for attestation payloads.

    ``tsa_cert_pem`` is the PINNED TSA certificate used to verify tokens
    (fail-closed: a token signed by any other cert is rejected). If ``tsa_url``
    is set, tokens are fetched over HTTP from that RFC 3161 endpoint; otherwise
    a local ``openssl ts`` signer (``signer_cert_pem``/``signer_key_pem``) mints
    them -- used for self-contained testing and air-gapped operation.
    """

    def __init__(
        self,
        tsa_cert_pem: bytes,
        tsa_url: Optional[str] = None,
        signer_cert_pem: Optional[bytes] = None,
        signer_key_pem: Optional[bytes] = None,
    ):
        self._ca = tsa_cert_pem
        self._url = tsa_url
        self._signer_cert = signer_cert_pem or tsa_cert_pem
        self._signer_key = signer_key_pem
        self._cert_id = hashlib.sha256(tsa_cert_pem).hexdigest()[:32]

    @classmethod
    def from_env(cls) -> Optional["TrustedTimestampAuthority"]:
        cert_path = os.environ.get("LIUHAO_AUDIT_TSA_CERT")
        if not cert_path:
            return None
        if not os.path.exists(cert_path):
            raise FileNotFoundError(
                "LIUHAO_AUDIT_TSA_CERT points to a missing cert: %s" % cert_path
            )
        with open(cert_path, "rb") as fh:
            cert = fh.read()
        url = os.environ.get("LIUHAO_AUDIT_TSA_URL")
        return cls(tsa_cert_pem=cert, tsa_url=url)

    def request_token(self, data: bytes) -> TimestampToken:
        """Obtain an RFC 3161 token binding the SHA-256 of ``data`` to a time."""
        import secrets

        query = build_timestamp_query(data, nonce=secrets.randbits(63))
        if self._url:
            token_der = self._fetch_http(query)
        else:
            if self._signer_key is None:
                raise RuntimeError(
                    "no TSA URL and no local signer key configured; cannot mint token"
                )
            token_der = _mint_via_openssl(
                query, self._signer_cert, self._signer_key, self._ca
            )
        gen_time = extract_gen_time(token_der)
        return TimestampToken(token_der, gen_time, self._cert_id)

    def _fetch_http(self, query_der: bytes) -> bytes:
        import urllib.request

        req = urllib.request.Request(
            self._url,
            data=query_der,
            headers={
                "Content-Type": "application/timestamp-query",
                "Accept": "application/timestamp-reply",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:  # nosec B310
            body = resp.read()
        token = extract_token_from_response(body)
        if token is None:
            # Some TSAs return the bare token directly.
            token = body
        return token

    def verify_token(self, token: TimestampToken, data: bytes) -> Tuple[bool, str]:
        """Verify a token cryptographically against the pinned TSA cert.

        Fail-closed: returns (False, reason) on ANY failure -- bad signature,
        hash mismatch, wrong/untrusted TSA cert, or missing openssl. Never
        returns (True, ...) unless ``openssl ts -verify`` returns 0.
        """
        ok, reason = _verify_via_openssl(token.token_der, data, self._ca, self._signer_cert)
        if not ok:
            return False, reason or "openssl ts -verify failed"
        if token.tsa_cert_id != self._cert_id:
            return False, "token cert_id does not match pinned TSA cert"
        return True, "ok"


# --------------------------------------------------------------------------- #
# Test / air-gap helper: generate a CA + TSA-leaf keypair (EKU timeStamping)
# --------------------------------------------------------------------------- #
def generate_test_tsa_keypair(key_size: int = 2048) -> Tuple[bytes, bytes, bytes]:
    """Create a TEST timestamp authority: (tsa_key, tsa_leaf_cert, ca_cert).

    RFC 3161 requires the signer cert to be issued by a CA -- a self-signed TSA
    cert fails ``openssl ts``'s purpose check. This mirrors a REAL TSA: a CA
    root plus a leaf cert with ExtendedKeyUsage=timeStamping. The leaf is the
    signer; the CA is the pinned, trusted verification anchor.

    Intended for LOCAL testing and air-gapped operation only. Production must
    use a real, independently operated TSA whose cert is pinned via
    LIUHAO_AUDIT_TSA_CERT.
    """
    exe = _openssl()
    with tempfile.TemporaryDirectory() as d:
        import pathlib

        p = pathlib.Path(d)
        ca_key = p / "ca.key"
        ca_crt = p / "ca.crt"
        tsa_key = p / "tsa.key"
        tsa_csr = p / "tsa.csr"
        tsa_crt = p / "tsa.crt"
        extfile = p / "tsa.ext"
        extfile.write_text(
            "basicConstraints=critical,CA:FALSE\n"
            "keyUsage=critical,digitalSignature\n"
            "extendedKeyUsage=critical,timeStamping\n"
        )
        # 1. CA root.
        r = subprocess.run(
            [
                exe, "req", "-x509", "-newkey", "rsa:%d" % key_size,
                "-keyout", str(ca_key), "-out", str(ca_crt), "-days", "3650",
                "-nodes", "-subj", "/CN=LIUHAO-Test-TSA-CA",
                "-addext", "basicConstraints=critical,CA:TRUE",
                "-addext", "keyUsage=critical,keyCertSign,cRLSign",
            ],
            capture_output=True,
        )
        if r.returncode != 0:
            raise RuntimeError(
                "openssl req (test CA) failed: "
                + (r.stderr.decode("utf-8", "replace") or r.stdout.decode("utf-8", "replace"))
            )
        # 2. TSA leaf key + CSR.
        r = subprocess.run(
            [
                exe, "req", "-newkey", "rsa:%d" % key_size,
                "-keyout", str(tsa_key), "-out", str(tsa_csr), "-nodes",
                "-subj", "/CN=LIUHAO-Test-TSA",
            ],
            capture_output=True,
        )
        if r.returncode != 0:
            raise RuntimeError(
                "openssl req (test TSA CSR) failed: "
                + (r.stderr.decode("utf-8", "replace") or r.stdout.decode("utf-8", "replace"))
            )
        # 3. Sign the leaf with the CA (EKU timeStamping).
        r = subprocess.run(
            [
                exe, "x509", "-req", "-in", str(tsa_csr), "-CA", str(ca_crt),
                "-CAkey", str(ca_key), "-CAcreateserial", "-out", str(tsa_crt),
                "-days", "3650", "-extfile", str(extfile),
            ],
            capture_output=True,
        )
        if r.returncode != 0:
            raise RuntimeError(
                "openssl x509 (test TSA leaf) failed: "
                + (r.stderr.decode("utf-8", "replace") or r.stdout.decode("utf-8", "replace"))
            )
        return tsa_key.read_bytes(), tsa_crt.read_bytes(), ca_crt.read_bytes()
