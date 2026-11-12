"""HD-05 — TPM / hardware-root-of-trust timestamp provider (INTERFACE ONLY).

Phase 1 = interface only. The intended design: a TPM-backed (or HSM/PKCS#11)
persistent key signs the (artifact_digest, genTime) binding, so the timestamp
proof is rooted in hardware the operator controls. This requires a TPM / PKCS#11
binding this module does NOT depend on, so :meth:`timestamp` / :meth:`verify`
raise ``NotImplementedError`` in this phase. Final hardware-root selection is a
reserved human decision — see docs/autonomous/HUMAN-DECISION-BACKLOG.md (HD-05).

This module performs no hardware calls and no network calls.
"""
from __future__ import annotations

from typing import Optional

from .interfaces import KeyLifecycle, Signer, TimestampProvider, TimestampToken, TrustAnchor


class TpmTimestampProvider(Signer, TimestampProvider, KeyLifecycle):
    """Hardware TPM-attested timestamp backend — interface only (HD-05 reserved).

    Requiring a TPM / PKCS#11 binding this module does NOT depend on, every
    issuing/verification method raises ``NotImplementedError``. The FINAL hardware
    root selection is a reserved human decision (HD-05). This module performs no
    hardware calls and no network calls.
    """

    def __init__(self, tpm_handle: Optional[str] = None):
        # Intentionally stored but unused (e.g. a TPM persistent key handle /
        # PKCS#11 token URI). Wiring it requires a TPM stack.
        self.tpm_handle = tpm_handle

    @property
    def source_name(self) -> str:
        return "tpm"

    @property
    def alg(self) -> str:
        return "tpm"

    @property
    def authority(self) -> str:
        # No authority can be named until a hardware root is wired (HD-05).
        return "tpm-unconfigured"

    # -- Signer / KeyLifecycle are interface-only here ---------------------

    def public_key_id(self) -> str:
        raise NotImplementedError("TpmTimestampProvider key handling is phase 2 (TPM/PKCS#11).")

    def sign(self, data: bytes) -> object:
        raise NotImplementedError("TpmTimestampProvider.sign is phase 2 (TPM/PKCS#11).")

    def generate(self) -> str:
        raise NotImplementedError("TpmTimestampProvider key generation is phase 2 (TPM).")

    def rotate(self) -> str:
        raise NotImplementedError("TpmTimestampProvider key rotation is phase 2 (TPM).")

    def public_key_pem(self) -> str:
        raise NotImplementedError("TpmTimestampProvider public key export is phase 2 (TPM).")

    def load(self, pem: str) -> None:
        raise NotImplementedError("TpmTimestampProvider key load is phase 2 (TPM).")

    # -- TimestampProvider not wired in phase 1 ----------------------------

    def timestamp(self, data: bytes) -> TimestampToken:
        raise NotImplementedError(
            "TpmTimestampProvider.timestamp is not wired: no TPM/PKCS#11 "
            "binding is configured. Provide a hardware signer before use (HD-05)."
        )

    def verify(self, data: bytes, token: TimestampToken,
               anchor: Optional[TrustAnchor] = None) -> bool:
        raise NotImplementedError("TpmTimestampProvider.verify is not implemented (HD-05).")
