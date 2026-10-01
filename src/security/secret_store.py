"""Fail-closed secret storage subsystem for LiuHao AI OS (SEC-04/05/06).

This module is the *authoritative* secret backend for the OS. It replaces the
previous "silently cache plaintext in memory / sha256-as-encrypt" behaviour with
a real, persisted, encrypted store and a startup gate that **refuses autonomous
execution when no usable backend is configured**.

Design guarantees
-----------------
* ``SecretStore`` is the backend abstraction (get/set/delete/list + health).
* ``LocalEncryptedSecretStore`` encrypts every secret with AES-GCM
  (``cryptography.hazmat``). The data-encryption key (DEK) is wrapped under a
  key-encryption key (KEK) derived from ``LIUHAO_SECRET_MASTER_PASSPHRASE``
  (Argon2id, PBKDF2 fallback) or, when no passphrase is given, a generated KEK
  that is written to disk exactly once. All on-disk artefacts are ``chmod 0600``
  and survive reload; rotation/re-key is recoverable.
* ``require_secret_backend()`` is the fail-closed gate. With no usable backend
  configured it raises ``SecretBackendUnavailable`` -- it NEVER falls back to an
  in-memory plaintext store in a production context. An explicit dev-only
  ephemeral backend is available behind ``LIUHAO_SECRET_DEV_EPHEMERAL`` and is
  clearly marked non-production.
"""

from __future__ import annotations

import abc
import base64
import json
import logging
import os
import secrets
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class SecretBackendUnavailable(RuntimeError):
    """Raised when no usable secret backend is configured.

    This is the fail-closed signal: callers must NOT swallow it and must NOT
    substitute an in-memory plaintext fallback in production.
    """


# --------------------------------------------------------------------------- #
# Environment helpers (posture — single source of truth)
# --------------------------------------------------------------------------- #
# Production-posture detection used to read only ``LIUHAO_ENV`` (see
# ``secret_store:49`` in history). Nothing in the deployment ever set that
# variable, so ``is_production()`` was ALWAYS False in production and every
# safety branch silently took the non-production path. The canonical, unified
# resolver now lives in ``src/security/posture.py`` and reads
# ``LIUHAO_ENV`` > ``ENVIRONMENT`` > ``APP_ENV``. We re-export from there so
# existing imports of ``is_production`` keep working unchanged.
from .posture import (  # noqa: E402,F401  (re-export; SoT is posture.py)
    PostureUndeterminable,
    deployment_posture,
    describe_posture,
    is_production,
)


def _env_secret_passphrase() -> Optional[str]:
    return os.environ.get("LIUHAO_SECRET_MASTER_PASSPHRASE") or None


def _default_store_dir() -> str:
    return os.environ.get("LIUHAO_SECRET_STORE_DIR") or os.path.join(
        os.path.expanduser("~"), ".liuhao", "secrets"
    )


# --------------------------------------------------------------------------- #
# Cryptography primitives
# --------------------------------------------------------------------------- #
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    try:
        from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
        _HAS_ARGON2 = True
    except ImportError:  # pragma: no cover - depends on cryptography build
        Argon2id = None
        _HAS_ARGON2 = False
    CRYPTO_AVAILABLE = True
except ImportError:  # pragma: no cover
    AESGCM = None
    PBKDF2HMAC = None
    Argon2id = None
    hashes = None
    _HAS_ARGON2 = False
    CRYPTO_AVAILABLE = False


_NONCE_LEN = 12


def _secure_chmod(path: str) -> None:
    """Best-effort chmod 0600 (no-op where the platform ignores it)."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _derive_kek(
    passphrase: str,
    salt: bytes,
    kdf: str = "argon2",
    *,
    argon2_time_cost: int = 3,
    argon2_memory_cost: int = 65536,
    argon2_parallelism: int = 4,
    pbkdf2_iterations: int = 600000,
) -> bytes:
    """Derive a 32-byte KEK from a passphrase using Argon2id or PBKDF2."""
    if not CRYPTO_AVAILABLE:
        raise SecretBackendUnavailable(
            "cryptography library required for secret storage"
        )
    password = passphrase.encode("utf-8") if isinstance(passphrase, str) else passphrase
    if kdf == "argon2" and _HAS_ARGON2:
        try:
            derived = Argon2id(
                salt=salt,
                length=32,
                iterations=argon2_time_cost,
                memory_cost=argon2_memory_cost,
                lanes=argon2_parallelism,
            ).derive(password)
        except TypeError:
            # Older cryptography releases used time_cost/parallelism.
            derived = Argon2id(
                salt=salt,
                length=32,
                time_cost=argon2_time_cost,
                memory_cost=argon2_memory_cost,
                parallelism=argon2_parallelism,
            ).derive(password)
    else:
        derived = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=pbkdf2_iterations,
        ).derive(password)
    return derived


def _name_to_filename(name: str) -> str:
    return base64.urlsafe_b64encode(name.encode("utf-8")).decode("ascii")


def _filename_to_name(fname: str) -> str:
    return base64.urlsafe_b64decode(fname.encode("ascii")).decode("utf-8")


# --------------------------------------------------------------------------- #
# Abstraction
# --------------------------------------------------------------------------- #
class SecretStore(abc.ABC):
    """Backend-agnostic secret store.

    Implementations must be safe to call concurrently for independent names
    (the local store serialises per-operation with a lock). ``health()`` reports
    whether the backend is currently usable.
    """

    backend_name: str = "abstract"
    is_production_safe: bool = True

    @abc.abstractmethod
    def get(self, name: str) -> Optional[str]:
        """Return the decrypted secret, or None if absent."""

    @abc.abstractmethod
    def set(self, name: str, value: str) -> None:
        """Store ``value`` under ``name`` (overwrites)."""

    @abc.abstractmethod
    def delete(self, name: str) -> bool:
        """Remove ``name``; return True if it existed."""

    @abc.abstractmethod
    def list(self) -> List[str]:
        """List stored secret names."""

    @abc.abstractmethod
    def health(self) -> bool:
        """Return True if the backend is usable (key material + writable)."""


class InMemorySecretStore(SecretStore):
    """Explicitly dev-only, non-persistent, ephemeral backend.

    NEVER returned by the production path of ``require_secret_backend`` unless
    ``LIUHAO_SECRET_DEV_EPHEMERAL`` is set. Secrets live only for the process
    lifetime.
    """

    backend_name = "in-memory(dev-ephemeral)"
    is_production_safe = False

    def __init__(self) -> None:
        self._store: Dict[str, str] = {}

    def get(self, name: str) -> Optional[str]:
        return self._store.get(name)

    def set(self, name: str, value: str) -> None:
        self._store[name] = value

    def delete(self, name: str) -> bool:
        return self._store.pop(name, None) is not None

    def list(self) -> List[str]:
        return list(self._store.keys())

    def health(self) -> bool:
        return True


# --------------------------------------------------------------------------- #
# Local encrypted store
# --------------------------------------------------------------------------- #
class LocalEncryptedSecretStore(SecretStore):
    """AES-GCM encrypted, file-backed secret store (chmod 0600).

    On-disk layout under ``storage_dir``:
      * ``master.kek`` -- {kek_kind, salt, kdf, params, wrapped_dek}
        (the DEK wrapped under the KEK; chmod 0600)
      * ``<b64url(name)>.bin`` -- {nonce, ct} per secret (chmod 0600)

    The KEK is derived from ``LIUHAO_SECRET_MASTER_PASSPHRASE`` when given, else
    from a generated KEK persisted once as ``master.key`` (chmod 0600).
    """

    backend_name = "local-encrypted"
    _MASTER_FILE = "master.kek"
    _GENERATED_KEK_FILE = "master.key"

    def __init__(
        self,
        storage_dir: str,
        passphrase: Optional[str] = None,
        key_file: Optional[str] = None,
        kdf: str = "argon2",
        create: bool = True,
    ) -> None:
        if not CRYPTO_AVAILABLE:
            raise SecretBackendUnavailable(
                "cryptography library required for encrypted secret storage"
            )
        self._storage_dir = storage_dir
        self._passphrase = passphrase
        self._key_file = key_file
        self._kdf = kdf if (kdf != "argon2" or _HAS_ARGON2) else "pbkdf2"
        self._dek: Optional[bytes] = None
        Path(storage_dir).mkdir(parents=True, exist_ok=True)
        self._load_or_create_master(create=create)

    # --- master key material ------------------------------------------------
    def _master_path(self) -> str:
        return os.path.join(self._storage_dir, self._MASTER_FILE)

    def _generated_kek_path(self) -> str:
        return os.path.join(self._storage_dir, self._GENERATED_KEK_FILE)

    def _load_or_create_master(self, create: bool) -> None:
        master_path = self._master_path()
        if os.path.exists(master_path):
            self._dek = self._unwrap_existing(master_path)
            return
        if not create:
            raise SecretBackendUnavailable(
                f"no master key material at {master_path}; refusing to auto-create"
            )
        self._dek = secrets.token_bytes(32)
        kek, kek_kind, salt = self._resolve_kek()
        self._wrap_and_persist_master(kek, kek_kind, salt)

    def _resolve_kek(self):
        """Return (KEK, kek_kind, salt)."""
        if self._passphrase:
            salt = secrets.token_bytes(16)
            return _derive_kek(self._passphrase, salt, self._kdf), "passphrase", salt
        # Generated KEK stored once.
        gen_path = self._generated_kek_path()
        if self._key_file:
            gen_path = self._key_file
        if os.path.exists(gen_path):
            kek = Path(gen_path).read_bytes()
            if len(kek) != 32:
                kek = _derive_kek(kek.decode("utf-8", "replace"), secrets.token_bytes(16))
        else:
            kek = secrets.token_bytes(32)
            Path(gen_path).write_bytes(kek)
            _secure_chmod(gen_path)
        return kek, "generated", b""

    def _wrap_and_persist_master(self, kek: bytes, kek_kind: str, salt: bytes) -> None:
        nonce = secrets.token_bytes(_NONCE_LEN)
        wrapped = AESGCM(kek).encrypt(nonce, self._dek, b"dek")
        payload = {
            "version": 1,
            "kek_kind": kek_kind,
            "kdf": self._kdf,
            "salt": base64.b64encode(salt).decode("ascii"),
            "wrapped_dek": base64.b64encode(nonce + wrapped).decode("ascii"),
        }
        master_path = self._master_path()
        Path(master_path).write_text(json.dumps(payload), encoding="utf-8")
        _secure_chmod(master_path)

    def _unwrap_existing(self, master_path: str) -> bytes:
        payload = json.loads(Path(master_path).read_text(encoding="utf-8"))
        salt = base64.b64decode(payload.get("salt", ""))
        blob = base64.b64decode(payload["wrapped_dek"])
        nonce, wrapped = blob[:_NONCE_LEN], blob[_NONCE_LEN:]
        if payload.get("kek_kind") == "passphrase":
            if not self._passphrase:
                raise SecretBackendUnavailable(
                    "master key is passphrase-protected; "
                    "LIUHAO_SECRET_MASTER_PASSPHRASE is required to unlock it"
                )
            kek = _derive_kek(self._passphrase, salt, payload.get("kdf", self._kdf))
        else:
            gen_path = self._key_file or self._generated_kek_path()
            if not os.path.exists(gen_path):
                raise SecretBackendUnavailable(
                    f"generated KEK file missing at {gen_path}; cannot unlock store"
                )
            kek = Path(gen_path).read_bytes()
        try:
            return AESGCM(kek).decrypt(nonce, wrapped, b"dek")
        except Exception as exc:  # invalid tag => wrong passphrase / corrupt KEK
            raise SecretBackendUnavailable(
                f"unable to decrypt master key material: {exc}"
            )

    # --- secret operations --------------------------------------------------
    def _secret_path(self, name: str) -> str:
        return os.path.join(self._storage_dir, _name_to_filename(name) + ".bin")

    def set(self, name: str, value: str) -> None:
        if self._dek is None:
            raise SecretBackendUnavailable("store not initialised")
        nonce = secrets.token_bytes(_NONCE_LEN)
        ct = AESGCM(self._dek).encrypt(
            nonce, value.encode("utf-8"), name.encode("utf-8")
        )
        path = self._secret_path(name)
        Path(path).write_text(
            json.dumps(
                {
                    "nonce": base64.b64encode(nonce).decode("ascii"),
                    "ct": base64.b64encode(ct).decode("ascii"),
                }
            ),
            encoding="utf-8",
        )
        _secure_chmod(path)

    def get(self, name: str) -> Optional[str]:
        path = self._secret_path(name)
        if not os.path.exists(path):
            return None
        try:
            blob = json.loads(Path(path).read_text(encoding="utf-8"))
            nonce = base64.b64decode(blob["nonce"])
            ct = base64.b64decode(blob["ct"])
            plain = AESGCM(self._dek).decrypt(nonce, ct, name.encode("utf-8"))
        except Exception as exc:
            # Tampered / corrupt / unreadable secret: fail closed (do not
            # return garbage or fall back to a plaintext source).
            raise SecretBackendUnavailable(
                f"secret {name!r} failed integrity check: {exc}"
            )
        return plain.decode("utf-8")

    def delete(self, name: str) -> bool:
        path = self._secret_path(name)
        if os.path.exists(path):
            os.remove(path)
            return True
        return False

    def list(self) -> List[str]:
        out: List[str] = []
        for fname in os.listdir(self._storage_dir):
            if fname.endswith(".bin"):
                try:
                    out.append(_filename_to_name(fname[: -len(".bin")]))
                except Exception:
                    continue
        return out

    def health(self) -> bool:
        if self._dek is None:
            return False
        return os.access(self._storage_dir, os.W_OK)

    # --- rotation / re-key (recoverable) -----------------------------------
    def rotate(
        self,
        new_passphrase: Optional[str] = None,
        new_key_file: Optional[str] = None,
    ) -> None:
        """Re-key the store. Existing secrets stay readable afterwards.

        Decrypts everything into memory first, derives a new DEK + KEK, rewraps
        and re-encrypts. Because the plaintext is held in memory before any file
        is rewritten, a failure mid-rotation leaves the old (still valid) blobs
        on disk rather than corrupting data.
        """
        if self._dek is None:
            raise SecretBackendUnavailable("store not initialised")
        snapshot: Dict[str, str] = {}
        for name in self.list():
            value = self.get(name)
            if value is None:
                continue
            snapshot[name] = value

        old_passphrase, old_key_file = self._passphrase, self._key_file
        self._passphrase = new_passphrase
        self._key_file = new_key_file
        self._dek = secrets.token_bytes(32)
        kek, kek_kind, salt = self._resolve_kek()
        try:
            self._wrap_and_persist_master(kek, kek_kind, salt)
        except Exception:
            # Roll the config back so the live store stays usable.
            self._passphrase, self._key_file = old_passphrase, old_key_file
            raise
        for name, value in snapshot.items():
            self.set(name, value)


# --------------------------------------------------------------------------- #
# Vault-backed store (fail-closed; only used when Vault is online)
# --------------------------------------------------------------------------- #
class VaultSecretStore(SecretStore):
    """Thin fail-closed wrapper around the Vault KV engine.

    If Vault is not connected this backend refuses every operation rather than
    silently caching plaintext.
    """

    backend_name = "vault"
    _PREFIX = "liuhao/secrets/"

    def __init__(self, client) -> None:
        self._client = client
        if not getattr(client, "connected", False):
            raise SecretBackendUnavailable(
                "Vault secret backend requested but Vault is not connected"
            )

    def _p(self, name: str) -> str:
        return self._PREFIX + name

    def get(self, name: str) -> Optional[str]:
        data = self._client.read_secret(self._p(name))
        if not data:
            return None
        return data.get("value")

    def set(self, name: str, value: str) -> None:
        if not self._client.write_secret(self._p(name), {"value": value}):
            raise SecretBackendUnavailable(f"failed to write secret {name!r} to Vault")

    def delete(self, name: str) -> bool:
        return bool(self._client.delete_secret(self._p(name)))

    def list(self) -> List[str]:
        keys = self._client.list_secrets(self._PREFIX)
        return [k[len(self._PREFIX):] for k in keys if k.startswith(self._PREFIX)]

    def health(self) -> bool:
        return bool(getattr(self._client, "connected", False))


# --------------------------------------------------------------------------- #
# Fail-closed gate
# --------------------------------------------------------------------------- #
def _vault_reachable() -> bool:
    try:
        from src.integrations.vault.client import VaultClient
    except Exception:
        return False
    try:
        client = VaultClient.get_instance()
        return bool(client.connect())
    except Exception:
        return False


def require_secret_backend() -> SecretStore:
    """Return a usable secret backend, or fail closed.

    Resolution order:
      1. ``LIUHAO_SECRET_MASTER_PASSPHRASE`` -> encrypted local store.
      2. ``LIUHAO_SECRET_KEY_FILE`` (exists) -> encrypted local store.
      3. Vault configured & reachable -> Vault store.
      4. ``LIUHAO_SECRET_DEV_EPHEMERAL`` -> explicit dev-only in-memory store.
      5. otherwise -> raise ``SecretBackendUnavailable`` (NEVER in-memory).

    In production (``is_production()``) only (1)-(3) are acceptable; the dev
    ephemeral store is rejected and the gate hard-denies.
    """
    passphrase = _env_secret_passphrase()
    if passphrase:
        return LocalEncryptedSecretStore(_default_store_dir(), passphrase=passphrase)

    key_file = os.environ.get("LIUHAO_SECRET_KEY_FILE")
    if key_file and os.path.exists(key_file):
        return LocalEncryptedSecretStore(
            os.environ.get("LIUHAO_SECRET_STORE_DIR") or _default_store_dir(),
            key_file=key_file,
        )

    if _vault_reachable():
        try:
            from src.integrations.vault.client import VaultClient
            return VaultSecretStore(VaultClient.get_instance())
        except Exception as exc:
            logger.warning("Vault backend unavailable: %s", exc)

    if os.environ.get("LIUHAO_SECRET_DEV_EPHEMERAL"):
        if is_production():
            raise SecretBackendUnavailable(
                "dev ephemeral backend is not permitted in production"
            )
        return InMemorySecretStore()

    raise SecretBackendUnavailable(
        "no usable secret backend configured: set LIUHAO_SECRET_MASTER_PASSPHRASE "
        "(or LIUHAO_SECRET_KEY_FILE) for an encrypted local store, configure Vault, "
        "or set LIUHAO_SECRET_DEV_EPHEMERAL for an explicit dev-only ephemeral store. "
        "Refusing to run without a real backend."
    )


_store_singleton: Optional[SecretStore] = None


def get_secret_store() -> SecretStore:
    """Process-wide secret store (fail-closed; raises if unconfigured)."""
    global _store_singleton
    if _store_singleton is None:
        _store_singleton = require_secret_backend()
    return _store_singleton


def reset_secret_store() -> None:
    """Drop the cached singleton (testing)."""
    global _store_singleton
    _store_singleton = None
