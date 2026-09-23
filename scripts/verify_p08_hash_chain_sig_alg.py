#!/usr/bin/env python
"""P0-8b / P0-8c CI gate — hash-chain explicit algorithm + fail-closed verification.

Ground-truth proof (not prose) for the LIUHAO hash-chain integrity containment:

  * The shared registry (src/common/hash_chain.py) dispatches on a *declared*
    algorithm name and is fail-closed: an unknown algorithm returns
    (False, "unknown") and is NEVER silently recomputed with a default.
  * Every on-disk JSON hash chain (HC-02..HC-08) now persists an explicit
    ``hash_alg`` envelope and verifies through that registry.
  * For EACH chain we perform a real round-trip against the filesystem with a
    freshly constructed store instance (independent disk read), then:
      - assert the persisted ``hash_alg`` is present and == "sha256";
      - assert verify_integrity() is True on reload;
      - tamper a record on disk and assert verify_integrity() becomes False;
      - flip the declared ``hash_alg`` to an unknown value and assert
        verify_integrity() is False (fail-closed — no fallback).

This script is the deployment / bundle-rebuild verification gate (P0-8c): a
non-zero exit blocks the bundle from being considered integrity-verified.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile

# Make the repo root importable when run directly from CI.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.common.hash_chain import (  # noqa: E402
    compute_hash,
    verify_declared_hash,
    DEFAULT_HASH_ALG,
)

# Chain implementations
from src.audit.store import AuditStore  # noqa: E402
from src.observability.store import ObservabilityStore  # noqa: E402
from src.observability.alerts.store import AlertStore  # noqa: E402
from src.plugins.dependencies.store import PluginDependenciesStore  # noqa: E402
from src.plugins.sandbox.store import PluginSandboxStore  # noqa: E402
from src.storage.backends import JSONFileBackend  # noqa: E402
from src.plugins.marketplace.store import PluginMarketplaceStore  # noqa: E402

# Models needed to build valid records
from src.observability.models import Span  # noqa: E402
from src.observability.alerts.models import (  # noqa: E402
    Alert,
    AlertType,
    AlertSeverity,
)
from src.plugins.dependencies.models import DependencySpec  # noqa: E402
from src.plugins.sandbox.models import SandboxExecutionContext  # noqa: E402
from src.storage.backends import StorageEntry  # noqa: E402
from src.plugins.marketplace.models import (  # noqa: E402
    Plugin,
    PluginMetadata,
    PluginVersion,
)


_RESULTS: list = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    _RESULTS.append((bool(ok), name, detail))
    status = "PASS" if ok else "FAIL"
    line = f"  [{status}] {name}"
    if detail:
        line += f"  -- {detail}"
    print(line)
    return bool(ok)


def _load_raw(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _dump_raw(path: str, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def test_shared_registry() -> None:
    print("\n== Shared registry (src/common/hash_chain.py) ==")
    digest = hashlib.sha256(b"liuhao").hexdigest()
    check(
        "compute_hash(sha256) == hashlib.sha256",
        compute_hash("sha256", b"liuhao") == digest,
    )
    ok, reason = verify_declared_hash("sha256", b"liuhao", digest)
    check("verify_declared_hash(sha256, correct) -> (True, 'ok')", ok and reason == "ok")
    ok, reason = verify_declared_hash("sha256", b"liuhao", "deadbeef")
    check("verify_declared_hash(sha256, wrong) -> (False, 'mismatch')", (not ok) and reason == "mismatch")
    # The core P0-8b property: unknown algorithm is fail-closed, never a fallback.
    ok, reason = verify_declared_hash("sha512-unknown", b"liuhao", digest)
    check(
        "verify_declared_hash(unknown) -> (False, 'unknown') [fail-closed]",
        (not ok) and reason == "unknown",
    )


# Per-chain configuration: how to build one valid record, where records live
# in the persisted JSON, and which field to flip for the tamper test.
CHAIN_CONFIGS = [
    {
        "hc": "HC-02",
        "cls": AuditStore,
        "records_key": "events",
        "tamper_field": "event_type",
        "factory": lambda s: s.emit_simple("system_action", "verify_p08", message="p08"),
    },
    {
        "hc": "HC-03",
        "cls": ObservabilityStore,
        "records_key": "spans",
        "tamper_field": "name",
        "factory": lambda s: s.emit_span(Span(span_id="s1", trace_id="t1", name="n1")),
    },
    {
        "hc": "HC-04",
        "cls": AlertStore,
        "records_key": "alerts",
        "tamper_field": "message",
        "factory": lambda s: s.emit_alert(
            Alert(
                id="a1",
                alert_type=AlertType.METRIC_THRESHOLD,
                name="n1",
                severity=AlertSeverity.LOW,
                message="m1",
                source="verify_p08",
            )
        ),
    },
    {
        "hc": "HC-05",
        "cls": PluginDependenciesStore,
        "records_key": "dependencies",
        "tamper_field": "plugin_id",
        "factory": lambda s: s.register("p1", DependencySpec(name="d1")),
    },
    {
        "hc": "HC-06",
        "cls": PluginSandboxStore,
        "records_key": "contexts",
        "tamper_field": "plugin_id",
        "factory": lambda s: s.create_context(
            SandboxExecutionContext(plugin_id="p1", sandbox_id="s1")
        ),
    },
    {
        "hc": "HC-07",
        "cls": JSONFileBackend,
        "records_key": "entries",
        "tamper_field": "value",
        "factory": lambda s: s.put(
            "k1", StorageEntry(key="k1", value="v1", created_at=1000.0, updated_at=1000.0)
        ),
    },
    {
        "hc": "HC-08",
        "cls": PluginMarketplaceStore,
        "records_key": "plugins",
        "tamper_field": "name",
        "factory": lambda s: s.register(
            Plugin(
                plugin_id="p1",
                name="n1",
                description="d1",
                metadata=PluginMetadata(name="n1", version="1.0", description="d1"),
                current_version=PluginVersion(
                    version="1.0",
                    version_id="vid",
                    release_notes="rn",
                    changelog="cl",
                    upload_url="u",
                ),
            )
        ),
    },
]


def main() -> int:
    print("P0-8b / P0-8c — hash-chain explicit algorithm + fail-closed verification")
    test_shared_registry()

    for cfg in CHAIN_CONFIGS:
        hc = cfg["hc"]
        cls = cfg["cls"]
        records_key = cfg["records_key"]
        tamper_field = cfg["tamper_field"]
        print(f"\n== {hc} — {cls.__module__}.{cls.__name__} ==")

        tmp = tempfile.mkdtemp(prefix=f"p08_{hc}_")
        path = os.path.join(tmp, "store.json")

        # 1) Build a real store, write one record to disk.
        store = cls(storage_path=path)
        cfg["factory"](store)

        # 2) hash_alg must be persisted explicitly.
        raw = _load_raw(path)
        check(
            f"{hc}: hash_alg persisted and == '{DEFAULT_HASH_ALG}'",
            raw.get("hash_alg") == DEFAULT_HASH_ALG,
            f"got {raw.get('hash_alg')!r}",
        )

        # 3) Independent reload from disk → integrity must hold.
        reloaded = cls(storage_path=path)
        check(f"{hc}: verify_integrity() == True on reload", reloaded.verify_integrity() is True)

        # 4) Tamper a record on disk → integrity must detect it.
        recs = raw[records_key]
        first_key = next(iter(recs))
        recs[first_key][tamper_field] = "TAMPERED"
        _dump_raw(path, raw)
        tampered = cls(storage_path=path)
        check(
            f"{hc}: verify_integrity() == False after tamper",
            tampered.verify_integrity() is False,
        )

        # 5) Flip declared algorithm to an unknown value → fail-closed False.
        raw2 = _load_raw(path)
        raw2["hash_alg"] = "sha512-unknown"
        _dump_raw(path, raw2)
        unknown = cls(storage_path=path)
        check(
            f"{hc}: verify_integrity() == False on unknown hash_alg (fail-closed)",
            unknown.verify_integrity() is False,
        )

    total = len(_RESULTS)
    passed = sum(1 for ok, _, _ in _RESULTS if ok)
    failed = total - passed
    print("\n" + "=" * 64)
    print(f"P0-8b / P0-8c SUMMARY: {passed}/{total} checks passed, {failed} failed")
    print("=" * 64)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
