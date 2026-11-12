"""P0-8c — fail-closed on an undeclared algorithm, and the legacy migration.

The property under test: an envelope that declares no ``hash_alg`` must be
UNVERIFIED (``verify_integrity()`` False) -- it may never be silently read as
sha256. The migration script is the sanctioned way to make such legacy data
verifiable again, and it must not touch a single existing hash or record.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.plugins.marketplace.store import PluginMarketplaceStore  # noqa: E402
from src.plugins.marketplace.models import (  # noqa: E402
    Plugin, PluginMetadata, PluginVersion,
)

_MIGRATION_PATH = os.path.join(_REPO_ROOT, "scripts", "migrate_p08_hash_alg.py")


def _load_migration():
    spec = importlib.util.spec_from_file_location("p08c_migration", _MIGRATION_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _store(path):
    return PluginMarketplaceStore(storage_path=path)


def _plugin():
    return Plugin(
        plugin_id="p1",
        name="n1",
        description="d1",
        metadata=PluginMetadata(name="n1", version="1.0", description="d1"),
        current_version=PluginVersion(
            version="1.0", version_id="vid",
            release_notes="rn", changelog="cl", upload_url="u",
        ),
    )


def test_declared_store_verifies() -> None:
    d = tempfile.mkdtemp()
    p = os.path.join(d, "plugins.json")
    _store(p).register(_plugin())
    assert _store(p).verify_integrity() is True


def test_undeclared_envelope_is_unverified_not_assumed() -> None:
    """The core P0-8c property: no default sha256 fallback."""
    d = tempfile.mkdtemp()
    p = os.path.join(d, "plugins.json")
    _store(p).register(_plugin())

    raw = json.load(open(p, encoding="utf-8"))
    raw.pop("hash_alg")                      # simulate pre-P0-8 on-disk data
    json.dump(raw, open(p, "w", encoding="utf-8"), indent=2)

    assert _store(p).verify_integrity() is False


def test_unknown_algorithm_is_refused() -> None:
    d = tempfile.mkdtemp()
    p = os.path.join(d, "plugins.json")
    _store(p).register(_plugin())

    raw = json.load(open(p, encoding="utf-8"))
    raw["hash_alg"] = "sha512-unknown"
    json.dump(raw, open(p, "w", encoding="utf-8"), indent=2)

    assert _store(p).verify_integrity() is False


def test_migration_stamps_declaration_and_preserves_evidence() -> None:
    mig = _load_migration()
    d = tempfile.mkdtemp()
    p = os.path.join(d, "plugins.json")
    _store(p).register(_plugin())

    good = json.load(open(p, encoding="utf-8"))
    chain_before = good["hash_chain"]
    records_before = json.dumps(good["plugins"], sort_keys=True)

    # Make it legacy, then migrate it.
    legacy = dict(good)
    legacy.pop("hash_alg")
    json.dump(legacy, open(p, "w", encoding="utf-8"), indent=2)
    assert _store(p).verify_integrity() is False

    assert "STAMPED" in mig.migrate_file(p, apply=True)

    after = json.load(open(p, encoding="utf-8"))
    assert after["hash_alg"] == "sha256"
    # NOTHING else may change: same chain, same records, backup kept.
    assert after["hash_chain"] == chain_before
    assert json.dumps(after["plugins"], sort_keys=True) == records_before
    assert os.path.exists(p + ".bak")
    assert _store(p).verify_integrity() is True


def test_migration_dry_run_does_not_write() -> None:
    mig = _load_migration()
    d = tempfile.mkdtemp()
    p = os.path.join(d, "plugins.json")
    _store(p).register(_plugin())

    raw = json.load(open(p, encoding="utf-8"))
    raw.pop("hash_alg")
    json.dump(raw, open(p, "w", encoding="utf-8"), indent=2)

    assert "WOULD-STAMP" in mig.migrate_file(p, apply=False)
    assert "hash_alg" not in json.load(open(p, encoding="utf-8"))


def test_migration_is_idempotent() -> None:
    mig = _load_migration()
    d = tempfile.mkdtemp()
    p = os.path.join(d, "plugins.json")
    _store(p).register(_plugin())

    mig.migrate_file(p, apply=True)          # already declared -> no-op
    assert "ALREADY-DECLARED" in mig.migrate_file(p, apply=True)
