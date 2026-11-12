"""Fail-closed tests for the API-key registry on-disk permission lockdown.

The registry (src/security/api_keys.py) holds only key HASHES (never raw
secrets), but it must still not be world-readable. The save path used to
swallow ``os.chmod(..., 0o600)`` failures silently (fail-open / silent-success
anti-pattern, finding F3). These tests prove:

  * positive: a normal save writes a loadable registry AND the file is created
    with 0600 on POSIX (no chmod TOCTOU window);
  * negative (fail-closed): if ``os.chmod`` fails, the save raises
    ``PermissionLockdownError`` instead of reporting a clean save — so a
    possibly-world-readable registry is never silently persisted.
"""
from __future__ import annotations

import os
import stat

import pytest

from src.security.api_keys import APIKeyManager, PermissionLockdownError


def _tmp_store(tmp_path) -> str:
    return str(tmp_path / "api_keys.json")


def test_normal_save_roundtrips_and_is_owner_only(tmp_path):
    """A normal save must persist a loadable registry and lock it to 0600."""
    store = _tmp_store(tmp_path)
    mgr = APIKeyManager(storage_path=store)
    key, raw = mgr.create_key(name="svc", scopes=["read", "execute"])
    assert os.path.exists(store)

    # Round-trip: a fresh manager reloads the persisted (hash-only) registry.
    reloaded = APIKeyManager(storage_path=store)
    assert reloaded.get_key(key.id) is not None
    assert reloaded.validate_key(raw) is not None

    # On POSIX the file must be created 0600 (no world-readable window).
    if os.name == "posix":
        mode = stat.S_IMODE(os.stat(store).st_mode)
        assert mode == 0o600, f"registry mode {oct(mode)} != 0600"


def test_save_fails_closed_when_chmod_fails(monkeypatch, tmp_path):
    """If the permission lockdown cannot be enforced, the save must FAIL
    (raise) rather than silently persisting a possibly world-readable file."""
    store = _tmp_store(tmp_path)

    def _raising_chmod(path, mode):  # noqa: ANN001 - matches os.chmod signature
        raise OSError(13, "Permission denied (simulated lockdown failure)")

    monkeypatch.setattr(os, "chmod", _raising_chmod)

    mgr = APIKeyManager(storage_path=store)
    with pytest.raises(PermissionLockdownError):
        mgr.create_key(name="svc", scopes=["read"])
