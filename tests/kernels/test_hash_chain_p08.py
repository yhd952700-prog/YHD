"""Independent pytest layer for P0-8b / P0-8c hash-chain integrity.

This is the same ground-truth proof as ``scripts/verify_p08_hash_chain_sig_alg.py``
but expressed as real pytest cases so it runs inside the normal suite (and, on CI,
alongside every other test). The per-chain table ``CHAIN_CONFIGS`` is reused from
the standalone gate script to keep the two layers from drifting apart.

For every on-disk JSON hash chain (HC-02..HC-08) we perform a *real* round-trip
against the filesystem with a freshly constructed store instance (an independent
disk read), then prove:

  * the persisted ``hash_alg`` is present and == ``DEFAULT_HASH_ALG``;
  * ``verify_integrity()`` is True on reload;
  * tampering a record on disk makes ``verify_integrity()`` False;
  * flipping the declared ``hash_alg`` to an unknown value makes it False
    (fail-closed -- there is NO default fallback).
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile

import pytest

# Make the repo root importable so ``src`` and the gate script resolve.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.common.hash_chain import (  # noqa: E402
    compute_hash,
    verify_declared_hash,
    DEFAULT_HASH_ALG,
)

# Load the gate script by file path (its module name contains hyphens, so it is
# not importable via a normal ``import`` statement). Reusing its CHAIN_CONFIGS
# keeps this layer and the standalone gate from diverging.
_GATE_PATH = os.path.join(_REPO_ROOT, "scripts", "verify_p08_hash_chain_sig_alg.py")
_spec = importlib.util.spec_from_file_location("p08_hash_chain_gate", _GATE_PATH)
_gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_gate)
CHAIN_CONFIGS = _gate.CHAIN_CONFIGS


def test_shared_registry_fail_closed() -> None:
    """The single source of truth dispatches on a declared algorithm and is
    fail-closed: an unknown algorithm is NEVER silently recomputed with a default.
    """
    import hashlib

    digest = hashlib.sha256(b"liuhao").hexdigest()
    assert compute_hash("sha256", b"liuhao") == digest
    assert verify_declared_hash("sha256", b"liuhao", digest) == (True, "ok")
    assert verify_declared_hash("sha256", b"liuhao", "deadbeef") == (False, "mismatch")
    assert verify_declared_hash("sha512-unknown", b"liuhao", digest) == (False, "unknown")


def _roundtrip(cfg: dict) -> None:
    cls = cfg["cls"]
    records_key = cfg["records_key"]
    tamper_field = cfg["tamper_field"]
    hc = cfg["hc"]

    tmp = tempfile.mkdtemp(prefix=f"p08_{hc}_")
    path = os.path.join(tmp, "store.json")

    store = cls(storage_path=path)
    cfg["factory"](store)

    # 1) Explicit algorithm persisted on disk.
    raw = json.load(open(path, encoding="utf-8"))
    assert raw.get("hash_alg") == DEFAULT_HASH_ALG, f"{hc}: hash_alg not persisted"

    # 2) Independent reload from disk must verify.
    reloaded = cls(storage_path=path)
    assert reloaded.verify_integrity() is True, f"{hc}: integrity failed on reload"

    # 3) Tamper a record on disk -> must be detected.
    recs = raw[records_key]
    first_key = next(iter(recs))
    recs[first_key][tamper_field] = "TAMPERED"
    json.dump(raw, open(path, "w", encoding="utf-8"), indent=2)
    assert cls(storage_path=path).verify_integrity() is False, f"{hc}: tamper not detected"

    # 4) Unknown declared algorithm -> fail-closed False (no fallback).
    raw2 = json.load(open(path, encoding="utf-8"))
    raw2["hash_alg"] = "sha512-unknown"
    json.dump(raw2, open(path, "w", encoding="utf-8"), indent=2)
    assert cls(storage_path=path).verify_integrity() is False, (
        f"{hc}: unknown alg not fail-closed"
    )


@pytest.mark.parametrize("cfg", CHAIN_CONFIGS, ids=lambda c: c["hc"])
def test_chain_roundtrip_and_fail_closed(cfg: dict) -> None:
    """Parametrised over every JSON-file hash chain (HC-02..HC-08)."""
    _roundtrip(cfg)
