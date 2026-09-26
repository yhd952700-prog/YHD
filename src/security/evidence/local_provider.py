"""HD-05 — LOCAL mock / RFC 3161-shaped trusted-timestamp provider (OFFLINE).

This is the TEMPORARY adapter the build uses so the entire technical stack is
complete and testable *without* any external/paid TSA or hardware root of trust.
It is deliberately RFC 3161-shaped:

  * it binds the artifact's SHA-256 *messageImprint* (``digest``) and a *genTime*
    (``ts``) exactly the way an RFC 3161 TimeStampToken does;
  * it signs that binding with a locally-generated RSA-3072 key (the "TSA" key),
    self-describing via ``alg = "RS256-RSA3072"`` (primitive + key size + hash,
    matching the repo's ``dual_signature`` convention);
  * verification dispatches on the declared ``alg`` and rejects unknowns
    (fail-closed) — the same discipline as ``kernels/audit`` ``hash_alg``.

It performs NO network call and NO irreversible key ceremony. The key is
ephemeral in-process by default (verifiable within the process); operators may
set ``LIUHAO_TSA_LOCAL_KEY_PEM`` (or pass ``private_key_pem``) to a PEM for
cross-run stability. Final provider selection (real RFC 3161 TSA / TPM) is a
reserved human decision — see docs/autonomous/HUMAN-DECISION-BACKLOG.md (HD-05)
and EXECUTION-QUEUE.md.

This module is independent of ``jwt_handler`` / ``vault_crypto`` so existing
RSA capabilities are untouched.
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from typing import Optional

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.exceptions import InvalidSignature

from .errors import KeyLifecycleError, TimestampVerificationError
from .interfaces import (
    KeyLifecycle,
    SignatureToken,
    Signer,
    TimestampProvider,
    TimestampToken,
    artifact_digest,
)


LOCAL_TSA_KEY_ENV = "LIUHAO_TSA_LOCAL_KEY_PEM"
ALG = "RS256-RSA3072"


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pubkey_id_from_pem(pem: str) -> str:
    return hashlib.sha256(pem.encode("utf-8")).hexdigest()[:16]


class LocalRfc3161LikeProvider(Signer, TimestampProvider, KeyLifecycle):
    """Offline, RSA-3072, RFC 3161-shaped trusted-timestamp provider.

    Implements :class:`Signer`, :class:`TimestampProvider` and
    :class:`KeyLifecycle` so it can stand in for any of them in the subsystem.
    """

    def __init__(self, private_key_pem: Optional[str] = None, key_file: Optional[str] = None):
        self._private_key: Optional[rsa.RSAPrivateKey] = None
        self._public_key: Optional[rsa.RSAPublicKey] = None
        pem = private_key_pem or os.environ.get(LOCAL_TSA_KEY_ENV)
        if pem:
            self.load(pem)
        elif key_file and os.path.isfile(key_file):
            with open(key_file, "r", encoding="utf-8") as fh:
                self.load(fh.read())
        else:
            # Ephemeral in-process key (verifiable within this process only).
            self._private_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
            self._public_key = self._private_key.public_key()

    # -- KeyLifecycle -------------------------------------------------------

    def generate(self) -> str:
        self._private_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
        self._public_key = self._private_key.public_key()
        return self.public_key_id()

    def rotate(self) -> str:
        # Same as generate for the mock; a real provider would keep history.
        return self.generate()

    def public_key_pem(self) -> str:
        if self._public_key is None:
            raise KeyLifecycleError("no public key loaded")
        return self._public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")

    def load(self, pem: str) -> None:
        try:
            key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
        except ValueError as exc:
            # Maybe it's a public-only PEM (trust binding use-case).
            try:
                pub = serialization.load_pem_public_key(pem.encode("utf-8"))
            except ValueError as exc2:
                raise KeyLifecycleError(f"cannot load key PEM: {exc2}") from exc2
            self._private_key = None
            self._public_key = pub
            return
        if not isinstance(key, rsa.RSAPrivateKey):
            raise KeyLifecycleError("loaded key is not an RSA private key")
        self._private_key = key
        self._public_key = key.public_key()

    # -- Signer -------------------------------------------------------------

    def public_key_id(self) -> str:
        return _pubkey_id_from_pem(self.public_key_pem())

    def _sign_bytes(self, payload: bytes) -> str:
        if self._private_key is None:
            raise KeyLifecycleError("no private key available for signing")
        sig = self._private_key.sign(payload, padding.PKCS1v15(), hashes.SHA256())
        return _b64_local(sig)

    def sign(self, data: bytes) -> SignatureToken:
        token = self._sign_bytes(data)
        return SignatureToken(alg=ALG, source=self.source_name, token=token,
                              pubkey_id=self.public_key_id())

    def verify(self, data: bytes, token) -> bool:
        """Unified verify for both :class:`SignatureToken` and
        :class:`TimestampToken` (the two interfaces share the ``verify`` name but
        different token types, so dispatch on the runtime type)."""
        if isinstance(token, TimestampToken):
            return self._verify_timestamp(data, token)
        if isinstance(token, SignatureToken):
            return self._verify_signature(data, token)
        return False

    def _verify_signature(self, data: bytes, token: SignatureToken) -> bool:
        if token.alg != ALG:
            # Unknown / unsupported algorithm: fail-closed, never default.
            return False
        pub = self._resolve_pubkey(token.pubkey_id)
        if pub is None:
            return False
        try:
            pub.verify(_unb64_local(token.token), data, padding.PKCS1v15(), hashes.SHA256())
            return True
        except (InvalidSignature, ValueError, TypeError):
            return False

    # -- TimestampProvider --------------------------------------------------

    @property
    def source_name(self) -> str:
        return "local"

    @property
    def alg(self) -> str:
        return ALG

    def _resolve_pubkey(self, pubkey_id: Optional[str]):
        if self._public_key is None:
            return None
        if pubkey_id is not None and pubkey_id != self.public_key_id():
            # Token bound to a different key than the one currently loaded.
            return None
        return self._public_key

    def timestamp(self, data: bytes) -> TimestampToken:
        digest = artifact_digest(data)
        ts = _now_utc()
        # RFC 3161 binds (messageImprint, genTime); we sign that binding.
        payload = digest.encode("utf-8") + b"|" + ts.encode("utf-8")
        token = self._sign_bytes(payload)
        return TimestampToken(
            source=self.source_name, alg=ALG, ts=ts, digest=digest, token=token,
            pubkey_id=self.public_key_id(),
        )

    def _verify_timestamp(self, data: bytes, token: TimestampToken) -> bool:
        """Verify a timestamp token for ``data`` (fail-closed)."""
        if token.alg != ALG:
            return False
        if token.source != self.source_name:
            return False
        if token.digest != artifact_digest(data):
            return False
        pub = self._resolve_pubkey(token.pubkey_id)
        if pub is None:
            return False
        payload = token.digest.encode("utf-8") + b"|" + token.ts.encode("utf-8")
        try:
            pub.verify(_unb64_local(token.token), payload, padding.PKCS1v15(), hashes.SHA256())
            return True
        except (InvalidSignature, ValueError, TypeError):
            return False


def _b64_local(b: bytes) -> str:
    import base64
    return base64.b64encode(b).decode("ascii")


def _unb64_local(s: str) -> bytes:
    import base64
    return base64.b64decode(s)
