"""HD-05 — evidence adapter: seals a manifest into a verifiable bundle and opens it.

The adapter is the call-site-friendly front end. It binds a trusted timestamp to
a manifest and (optionally) an independent signature, producing an
:class:`EvidenceBundle`. On open it recomputes the artifact digest from the
*canonical* JSON form of the manifest, so verification is independent of how the
manifest was originally serialized.

Fail-closed: ``open`` returns ``verified=False`` on any failed check (digest
mismatch, bad timestamp, bad signature) — it never reports a bundle as good on a
false basis.
"""
from __future__ import annotations

import json
from typing import Optional, Tuple

from .interfaces import (
    EvidenceAdapter,
    EvidenceBundle,
    Signer,
    TimestampProvider,
    artifact_digest,
)


def _canonical(manifest: dict) -> bytes:
    return json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")


class ManifestEvidenceAdapter(EvidenceAdapter):
    """Seals / opens manifest evidence using a configured timestamp provider."""

    def __init__(self, provider: TimestampProvider, signer: Optional[Signer] = None):
        self._provider = provider
        self._signer = signer

    def seal(self, manifest_bytes: bytes) -> EvidenceBundle:
        try:
            manifest = json.loads(manifest_bytes.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            from .errors import AdapterError
            raise AdapterError(f"manifest is not valid JSON: {exc}") from exc
        # Timestamp and digest the CANONICAL form, so verification is independent
        # of how the manifest was originally serialized (matches :meth:`open` and
        # :class:`EvidenceVerifier`). The raw bytes are never bound on their own.
        canonical = _canonical(manifest)
        token = self._provider.timestamp(canonical)
        bundle = EvidenceBundle(
            manifest=manifest,
            artifact_digest=artifact_digest(canonical),
            timestamp=token,
        )
        if self._signer is not None:
            bundle.signature = self._signer.sign(canonical)
        return bundle

    def open(self, bundle: EvidenceBundle) -> Tuple[bytes, bool]:
        canonical = _canonical(bundle.manifest)
        digest_ok = artifact_digest(canonical) == bundle.artifact_digest
        try:
            ts_ok = self._provider.verify(canonical, bundle.timestamp)
        except Exception:
            ts_ok = False
        sig_ok = True
        if bundle.signature is not None:
            sig_ok = self._signer is not None and self._signer.verify(canonical, bundle.signature)
        verified = bool(digest_ok and ts_ok and sig_ok)
        return canonical, verified
