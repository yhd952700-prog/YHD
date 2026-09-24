#!/usr/bin/env python3
"""D26 (phase 1) — RFC 3161 timestamp authority abstraction (local fallback, no
external dependency).

WHY THIS EXISTS
---------------
Forensic / safeguard scripts in this repo emit MANIFEST files (e.g. the D22
derived-view MANIFEST, build / deployment digests). Those MANIFESTs need a
*timestamp* field so a verifier can later prove *when* a manifest was produced,
independent of filesystem mtimes. D26 introduces a ``TimestampAuthority``
abstraction with three backends:

  * ``Rfc3161TimestampAuthority`` — the RFC 3161 (Time-Stamp Protocol) backend.
    INTERFACE ONLY in this phase: it declares the request/response contract but
    is **not wired to a live TSA**. ``stamp()`` raises ``NotImplementedError``
    so nobody mistakes it for a working service.
  * ``TpmTimestampAuthority`` — a hardware TPM-attested timestamp backend.
    INTERFACE ONLY in this phase (requires a TPM / PKCS#11 stack we do not
    depend on). ``stamp()`` raises ``NotImplementedError``.
  * ``LocalTimestampAuthority`` — a LOCAL, OFFLINE fallback that produces a
    locally-signed timestamp token. No network call is ever made. It is the
    default and is fully usable today.

MANIFEST SCHEMA EXTENSION (the ``timestamp`` field)
--------------------------------------------------
Any MANIFEST produced/consumed by this module gains one field:

    "timestamp": {
        "ts":     "<ISO-8601 UTC, second granularity, e.g. 2026-09-24T12:00:00Z>",
        "source": "local" | "rfc3161" | "tpm",
        "token":  "<alg>:<hex>",        # backend-signed proof bound to the bytes
        "alg":    "hmac-sha256"         # signature algorithm in use
    }

``stamp(manifest_bytes) -> manifest_with_ts`` takes the serialized MANIFEST
bytes, parses them as JSON, and returns a NEW dict equal to the parsed manifest
plus the ``timestamp`` field. ``verify_manifest(manifest_with_ts, manifest_bytes)``
re-derives the token and returns ``True`` iff it matches (proving the manifest
bytes and the timestamp were bound together by a key the verifier holds).

SAFETY / NON-REGRESSION
-----------------------
* This module is PURELY ADDITIVE. It imports only the standard library and does
  NOT modify, monkey-patch, or import any existing safeguard script.
* The local fallback NEVER touches the network (no ``socket``/``requests`` use).
* Existing safeguard scripts are unaffected; integration is OPTIONAL and
  documented below. To adopt it, a safeguard script may, at its own pace:

      from scripts.timestamp_authority import stamp, verify_manifest
      manifest = stamp(json.dumps(my_manifest).encode("utf-8"))
      json.dump(manifest, open("MANIFEST.json", "w"))
      # later: verify_manifest(manifest, json.dumps(my_manifest).encode())

  Nothing in this module auto-injects itself into those scripts.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

TOOL_NAME = "timestamp_authority.py"
TOOL_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# Token carrier
# ---------------------------------------------------------------------------

@dataclass
class TimestampToken:
    """A timestamp proof bound to a set of manifest bytes.

    ``ts`` is the asserted time, ``source`` names the authority backend, and
    ``token`` is the backend's signature over (manifest_bytes || ts).
    """

    ts: str
    source: str
    token: str
    alg: str = "hmac-sha256"

    def to_dict(self) -> dict:
        return {"ts": self.ts, "source": self.source, "token": self.token, "alg": self.alg}


# ---------------------------------------------------------------------------
# Abstract authority
# ---------------------------------------------------------------------------

class TimestampAuthority:
    """Common contract for every timestamp backend.

    Subclasses implement :meth:`_sign` / :meth:`_verify_token`. The public
    :meth:`stamp` / :meth:`verify` are final and shared.
    """

    source_name: str = "abstract"

    # -- public, final ------------------------------------------------------

    def stamp(self, manifest_bytes: bytes) -> dict:
        """Return the parsed MANIFEST plus a bound ``timestamp`` field.

        ``manifest_bytes`` must be JSON. The returned dict is the manifest with
        one added key: ``timestamp`` (see module docstring for the schema).
        """
        try:
            parsed = json.loads(manifest_bytes.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError(
                "timestamp_authority.stamp requires JSON-encoded manifest bytes"
            ) from exc
        if not isinstance(parsed, dict):
            raise ValueError("timestamp_authority.stamp requires a JSON object manifest")

        ts = self._now_utc()
        token = self._sign(manifest_bytes, ts)
        parsed["timestamp"] = TimestampToken(
            ts=ts, source=self.source_name, token=token, alg=self._alg()
        ).to_dict()
        return parsed

    def verify(self, manifest_bytes: bytes, manifest_with_ts: dict) -> bool:
        """Return True iff ``manifest_with_ts``'s timestamp token is valid for
        ``manifest_bytes`` under this authority's key/state."""
        ts_field = manifest_with_ts.get("timestamp")
        if not isinstance(ts_field, dict):
            return False
        if ts_field.get("source") != self.source_name:
            return False
        expected = self._sign(manifest_bytes, ts_field.get("ts", ""))
        return hmac.compare_digest(expected, ts_field.get("token", ""))

    # -- backend hooks (overridden) ----------------------------------------

    def _now_utc(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _alg(self) -> str:
        raise NotImplementedError

    def _sign(self, manifest_bytes: bytes, ts: str) -> str:
        raise NotImplementedError

    def _verify_token(self, manifest_bytes: bytes, token: str, ts: str) -> bool:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# RFC 3161 backend — INTERFACE ONLY (no live TSA wired in phase 1)
# ---------------------------------------------------------------------------

class Rfc3161TimestampAuthority(TimestampAuthority):
    """RFC 3161 Time-Stamp Protocol backend.

    PHASE 1 = INTERFACE ONLY. The contract below documents the intended request
    (a ``TimeStampReq`` carrying the manifest digest) and response (a
    ``TimeStampResp`` carrying the signed ``TSTInfo``). It is deliberately NOT
    connected to any live TSA, and :meth:`stamp` raises ``NotImplementedError``
    so a misconfigured caller fails loudly instead of silently emitting an
    unsigned token.
    """

    source_name = "rfc3161"

    def __init__(self, tsa_url: Optional[str] = None, cert_pem: Optional[str] = None):
        # Intentionally stored but unused in phase 1. When wired later, tsa_url
        # is the RFC 3161 endpoint and cert_pem is the TSA's signing cert used
        # to verify the returned ``TSTInfo`` against a trusted root.
        self.tsa_url = tsa_url
        self.cert_pem = cert_pem

    def _alg(self) -> str:
        return "rfc3161-tst-info"

    def _sign(self, manifest_bytes: bytes, ts: str) -> str:
        raise NotImplementedError(
            "Rfc3161TimestampAuthority is interface-only in D26 phase 1: no live "
            "TSA is wired. Configure tsa_url + cert_pem and implement the "
            "TimeStampReq/TimeStampResp round-trip before using stamp()."
        )

    def _verify_token(self, manifest_bytes: bytes, token: str, ts: str) -> bool:
        raise NotImplementedError(
            "Rfc3161TimestampAuthority verification is not implemented in phase 1."
        )


# ---------------------------------------------------------------------------
# TPM backend — INTERFACE ONLY (requires a TPM / PKCS#11 stack)
# ---------------------------------------------------------------------------

class TpmTimestampAuthority(TimestampAuthority):
    """Hardware TPM-attested timestamp backend.

    PHASE 1 = INTERFACE ONLY. The intended design: a TPM-backed key (e.g. an
    NV-stored or persistent key) signs (manifest_digest || ts) so the timestamp
    proof is rooted in hardware the operator controls. It requires a TPM /
    PKCS#11 binding this module does NOT depend on, so :meth:`stamp` raises
    ``NotImplementedError`` in this phase.
    """

    source_name = "tpm"

    def __init__(self, tpm_handle: Optional[str] = None):
        # Intentionally stored but unused in phase 1.
        self.tpm_handle = tpm_handle

    def _alg(self) -> str:
        return "tpm-attestation"

    def _sign(self, manifest_bytes: bytes, ts: str) -> str:
        raise NotImplementedError(
            "TpmTimestampAuthority is interface-only in D26 phase 1: no TPM "
            "binding is wired. Provide a TPM/PKCS#11 signer before using stamp()."
        )

    def _verify_token(self, manifest_bytes: bytes, token: str, ts: str) -> bool:
        raise NotImplementedError(
            "TpmTimestampAuthority verification is not implemented in phase 1."
        )


# ---------------------------------------------------------------------------
# LOCAL fallback — OFFLINE, no network, fully usable
# ---------------------------------------------------------------------------

#: Env var holding a stable local signing key. Operators SHOULD set this so
#: tokens remain verifiable across processes/runs. If unset, an ephemeral
#: in-process key is used (verifiable within the same process only).
LOCAL_TSA_KEY_ENV = "LIUHAO_LOCAL_TSA_KEY"

#: Optional on-disk key location; only consulted when the env var is unset and
#: kept out of the repo (gitignored path under config/). If it does not exist,
#: an ephemeral key is used.
LOCAL_TSA_KEY_FILE = os.path.join("config", "local_tsa_key")


class LocalTimestampAuthority(TimestampAuthority):
    """Local, offline timestamp authority.

    Produces a locally-signed token = ``hmac-sha256(key, manifest_bytes || "|" || ts)``.
    No network is ever touched. The token is verifiable by any process that holds
    the same ``key`` (env var, key file, or an explicitly injected key).
    """

    source_name = "local"

    def __init__(self, key: Optional[bytes] = None, key_file: Optional[str] = None):
        if key is not None:
            self._key = key if isinstance(key, bytes) else key.encode("utf-8")
        elif os.environ.get(LOCAL_TSA_KEY_ENV):
            self._key = os.environ[LOCAL_TSA_KEY_ENV].encode("utf-8")
        elif key_file and os.path.isfile(key_file):
            with open(key_file, "rb") as fh:
                self._key = fh.read()
        else:
            # Ephemeral in-process key. Verifiable within this process only;
            # operators should set LIUHAO_LOCAL_TSA_KEY for cross-run stability.
            self._key = os.urandom(32)

    def _alg(self) -> str:
        return "hmac-sha256"

    def _sign(self, manifest_bytes: bytes, ts: str) -> str:
        mac = hmac.new(
            self._key,
            manifest_bytes + b"|" + ts.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return f"hmac-sha256:{mac.hex()}"

    def _verify_token(self, manifest_bytes: bytes, token: str, ts: str) -> bool:
        expected = self._sign(manifest_bytes, ts)
        return hmac.compare_digest(expected, token)


# ---------------------------------------------------------------------------
# Module-level convenience (used by safeguard / forensics scripts)
# ---------------------------------------------------------------------------

_default_local_authority: Optional[LocalTimestampAuthority] = None


def _default_authority() -> LocalTimestampAuthority:
    global _default_local_authority
    if _default_local_authority is None:
        _default_local_authority = LocalTimestampAuthority()
    return _default_local_authority


def stamp(manifest_bytes: bytes, *, authority: Optional[TimestampAuthority] = None) -> dict:
    """Bind a timestamp to ``manifest_bytes`` and return the manifest-with-ts.

    Defaults to the offline :class:`LocalTimestampAuthority`. Pass an explicit
    ``authority`` (e.g. an ``Rfc3161TimestampAuthority`` once wired) to use a
    different backend. See the module docstring for the MANIFEST schema.
    """
    auth = authority if authority is not None else _default_authority()
    return auth.stamp(manifest_bytes)


def verify_manifest(
    manifest_with_ts: dict,
    manifest_bytes: bytes,
    *,
    authority: Optional[TimestampAuthority] = None,
) -> bool:
    """Verify the ``timestamp`` field of ``manifest_with_ts`` for the given
    manifest bytes. Defaults to the local authority."""
    auth = authority if authority is not None else _default_authority()
    return auth.verify(manifest_bytes, manifest_with_ts)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="D26 timestamp authority (local offline fallback by default)."
    )
    parser.add_argument(
        "--manifest",
        required=True,
        help="Path to a JSON MANIFEST file to stamp with a local timestamp.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output path for the stamped manifest (default: <manifest>.stamped.json).",
    )
    args = parser.parse_args(argv)

    with open(args.manifest, "rb") as fh:
        manifest_bytes = fh.read()
    stamped = stamp(manifest_bytes)
    out_path = args.out or (args.manifest + ".stamped.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(stamped, fh, sort_keys=True, indent=2)
        fh.write("\n")
    ok = verify_manifest(stamped, manifest_bytes)
    print(f"D26 stamp -> {out_path} (verify={ok})")
    return 0 if ok else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
