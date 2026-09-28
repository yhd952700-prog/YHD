"""Fail-closed secret storage subsystem tests (SEC-04/05/06).

All tests use temporary directories and temporary passphrases only; none touch
a real deployment or weaken the security posture. They prove:

* AES-GCM encrypt/decrypt roundtrip
* persistence across process reload (and wrong passphrase fails closed)
* chmod 0600 on disk artefacts (POSIX-strict; best-effort on Windows)
* ``require_secret_backend()`` hard-denies when no usable backend is configured
* rotation / re-key keeps data readable and invalidates the old key
* the in-memory store is NEVER selected on the production path
* the fail-closed gate is wired into VaultClient and the JWT handler
"""

from __future__ import annotations

import base64
import os
import stat
import sys

import pytest

from src.security.secret_store import (
    SecretStore,
    LocalEncryptedSecretStore,
    InMemorySecretStore,
    SecretBackendUnavailable,
    require_secret_backend,
    get_secret_store,
    reset_secret_store,
)


def _clear_env(monkeypatch):
    for var in (
        "LIUHAO_SECRET_MASTER_PASSPHRASE",
        "LIUHAO_SECRET_KEY_FILE",
        "LIUHAO_SECRET_STORE_DIR",
        "LIUHAO_SECRET_DEV_EPHEMERAL",
        "LIUHAO_ENV",
        "VAULT_ADDR",
    ):
        monkeypatch.delenv(var, raising=False)


# --------------------------------------------------------------------------- #
# LocalEncryptedSecretStore behaviour
# --------------------------------------------------------------------------- #
def test_roundtrip():
    import tempfile
    d = tempfile.mkdtemp()
    store = LocalEncryptedSecretStore(d, passphrase="tmp-passphrase")
    store.set("db/password", "s3cr3t-value")
    assert store.get("db/password") == "s3cr3t-value"
    assert "db/password" in store.list()
    assert store.health() is True
    assert store.get("does/not/exist") is None
    assert store.delete("db/password") is True
    assert store.get("db/password") is None
    assert store.delete("db/password") is False


def test_persistence_across_reload():
    import tempfile
    d = tempfile.mkdtemp()
    s1 = LocalEncryptedSecretStore(d, passphrase="tmp-passphrase")
    s1.set("alpha", "one")
    s1.set("beta", "two")
    # New instance, same dir + passphrase -> data survives.
    s2 = LocalEncryptedSecretStore(d, passphrase="tmp-passphrase")
    assert s2.get("alpha") == "one"
    assert s2.get("beta") == "two"
    # Wrong passphrase must fail closed (cannot decrypt master material).
    with pytest.raises(SecretBackendUnavailable):
        LocalEncryptedSecretStore(d, passphrase="wrong-passphrase")


def test_chmod_0600():
    import tempfile
    d = tempfile.mkdtemp()
    store = LocalEncryptedSecretStore(d, passphrase="tmp-passphrase")
    store.set("k", "v")
    master = os.path.join(d, "master.kek")
    secret = os.path.join(d, base64.urlsafe_b64encode(b"k").decode() + ".bin")
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(master).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(secret).st_mode) == 0o600
    else:
        # On Windows chmod is best-effort; just confirm the files exist.
        assert os.path.exists(master) and os.path.exists(secret)


def test_tampered_secret_fails_closed():
    import tempfile
    d = tempfile.mkdtemp()
    store = LocalEncryptedSecretStore(d, passphrase="tmp-passphrase")
    store.set("k", "v")
    secret_path = os.path.join(d, base64.urlsafe_b64encode(b"k").decode() + ".bin")
    # Corrupt the ciphertext on disk.
    with open(secret_path, "rb") as f:
        blob = f.read()
    with open(secret_path, "wb") as f:
        f.write(blob[:5] + b"\x00" + blob[6:])
    with pytest.raises(SecretBackendUnavailable):
        store.get("k")


def test_rotation_rekey():
    import tempfile
    d = tempfile.mkdtemp()
    store = LocalEncryptedSecretStore(d, passphrase="old-pass")
    store.set("a", "x")
    store.set("b", "y")
    store.rotate(new_passphrase="new-pass")
    # Old passphrase no longer unlocks the store.
    with pytest.raises(SecretBackendUnavailable):
        LocalEncryptedSecretStore(d, passphrase="old-pass")
    # New passphrase works and data is intact / recoverable.
    s2 = LocalEncryptedSecretStore(d, passphrase="new-pass")
    assert s2.get("a") == "x"
    assert s2.get("b") == "y"


# --------------------------------------------------------------------------- #
# Fail-closed gate: require_secret_backend()
# --------------------------------------------------------------------------- #
def test_fail_closed_when_backend_missing(monkeypatch):
    _clear_env(monkeypatch)
    reset_secret_store()
    with pytest.raises(SecretBackendUnavailable):
        require_secret_backend()
    # The cached accessor must also hard-deny (no in-memory fallback).
    with pytest.raises(SecretBackendUnavailable):
        get_secret_store()


def test_passphrase_store_is_production_safe(monkeypatch):
    import tempfile
    d = tempfile.mkdtemp()
    _clear_env(monkeypatch)
    monkeypatch.setenv("LIUHAO_SECRET_MASTER_PASSPHRASE", "prod-pass")
    monkeypatch.setenv("LIUHAO_SECRET_STORE_DIR", d)
    reset_secret_store()
    store = require_secret_backend()
    assert store.is_production_safe is True
    assert isinstance(store, LocalEncryptedSecretStore)
    store.set("x", "y")
    assert store.get("x") == "y"


def test_dev_ephemeral_returns_in_memory(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("LIUHAO_SECRET_DEV_EPHEMERAL", "1")
    reset_secret_store()
    store = require_secret_backend()
    assert isinstance(store, InMemorySecretStore)
    assert store.is_production_safe is False


def test_in_memory_not_used_in_production(monkeypatch):
    _clear_env(monkeypatch)
    # Explicitly request dev ephemeral BUT declare production: must hard-deny.
    monkeypatch.setenv("LIUHAO_SECRET_DEV_EPHEMERAL", "1")
    monkeypatch.setenv("LIUHAO_ENV", "production")
    reset_secret_store()
    with pytest.raises(SecretBackendUnavailable):
        require_secret_backend()


def test_is_production_default_is_dev():
    # Default (no LIUHAO_ENV) must be treated as non-production so dev/tests
    # keep working; only an explicit production declaration triggers fail-closed.
    import src.security.secret_store as ss
    saved = os.environ.get("LIUHAO_ENV"), os.environ.get("LIUHAO_SECRET_DEV_EPHEMERAL")
    os.environ.pop("LIUHAO_ENV", None)
    os.environ.pop("LIUHAO_SECRET_DEV_EPHEMERAL", None)
    try:
        assert ss.is_production() is False
    finally:
        if saved[0] is not None:
            os.environ["LIUHAO_ENV"] = saved[0]
        if saved[1] is not None:
            os.environ["LIUHAO_SECRET_DEV_EPHEMERAL"] = saved[1]


# --------------------------------------------------------------------------- #
# Wiring: the gate is enforced inside the existing clients
# --------------------------------------------------------------------------- #
@pytest.fixture
def vault_reset():
    from src.integrations.vault.client import VaultClient
    VaultClient.reset_instance()
    yield
    VaultClient.reset_instance()


def test_vault_client_refuses_offline_in_production(monkeypatch, vault_reset):
    _clear_env(monkeypatch)
    monkeypatch.setenv("LIUHAO_ENV", "production")
    from src.integrations.vault.client import VaultClient
    client = VaultClient.get_instance()
    client.connect()  # offline (hvac not installed in this environment)
    with pytest.raises(SecretBackendUnavailable):
        client.write_secret("secret/path", {"value": "plaintext"})
    with pytest.raises(SecretBackendUnavailable):
        client.read_secret("secret/path")
    with pytest.raises(SecretBackendUnavailable):
        client.encrypt("key", "plaintext")


def test_vault_client_offline_allowed_in_dev(monkeypatch, vault_reset):
    _clear_env(monkeypatch)
    monkeypatch.setenv("VAULT_DEV_ROOT_TOKEN", "dev-only-secret-token")
    monkeypatch.setenv("VAULT_ADDR", "http://127.0.0.1:8200")
    from src.integrations.vault.client import VaultClient
    client = VaultClient.get_instance()
    client.connect()
    assert client.write_secret("secret/path", {"value": "v"}) is True
    assert client.read_secret("secret/path")["value"] == "v"
    # Offline encrypt is now REAL AES-GCM, not sha256, and round-trips.
    ct = client.encrypt("key", "plaintext")
    assert ct.startswith("offline-encrypted:")
    assert client.decrypt("key", ct) == "plaintext"


def test_jwt_handler_refuses_ephemeral_in_production(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("LIUHAO_ENV", "production")
    import src.security.jwt_handler as jh
    saved = jh._default_handler
    jh._default_handler = None
    try:
        with pytest.raises(SecretBackendUnavailable):
            jh.get_jwt_handler()
    finally:
        jh._default_handler = saved


def test_jwt_handler_ephemeral_allowed_in_dev(monkeypatch):
    _clear_env(monkeypatch)
    import src.security.jwt_handler as jh
    saved = jh._default_handler
    jh._default_handler = None
    try:
        handler = jh.get_jwt_handler()
        assert handler is not None
    finally:
        jh._default_handler = saved


def test_secret_store_is_abstract_contract():
    assert issubclass(LocalEncryptedSecretStore, SecretStore)
    assert issubclass(InMemorySecretStore, SecretStore)
    # ABC cannot be instantiated.
    with pytest.raises(TypeError):
        SecretStore()
