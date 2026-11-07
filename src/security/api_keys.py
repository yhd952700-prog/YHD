"""API key lifecycle management for LiuHao AI OS (SEC-04/05/06).

Design:
- Keys are identified by an opaque ``lhao_``-prefixed id; the raw secret is a
  ``lhao_``-prefixed token shown exactly once at creation time.
- Only the SHA-256 hash of the raw key is stored; the raw key never persists,
  so at-rest leakage of the secret is impossible (NO FAKE: real hashing, not a
  placeholder).
- Optional file persistence via ``storage_path``: the registry (hashes only,
  never raw keys) is serialized to JSON on every mutation and reloaded on
  construction. ``master_key`` is reserved for future at-rest encryption of the
  registry and is accepted for API compatibility.

The old Vault-backed ``APIKeyStorageBackend`` was removed: nothing outside this
module referenced it, and the file-backed design is what the SEC test suite
specifies.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from src.common.atomic_write import (
    atomic_write_bytes,
    exclusive_file_lock,
    read_text_with_retry,
)

logger = logging.getLogger(__name__)


class APIKeyStoreError(RuntimeError):
    """API key 注册表读写事务失败（调用方不得把它当成"保存成功"）。"""


#: 同进程内所有 manager 实例共享的写锁。每个请求 new 一个 manager 时，
#: 实例锁无效；Windows ``msvcrt`` region lock 又是进程级的，进程内必须先串行。
_PROCESS_WRITE_LOCK = threading.Lock()



class PermissionLockdownError(OSError):
    """Raised when the API-key registry file could not be locked down to 0600.

    Fail-closed: we must NOT report a clean save when the on-disk registry
    (hash-only, but still sensitive — it holds key hashes) may be left
    world-readable. The old code silently swallowed the ``os.chmod`` failure,
    which is a fail-open / silent-success security anti-pattern.
    """


class KeyScope(str, Enum):
    """API key access scopes."""

    READ = "read"
    WRITE = "write"
    ADMIN = "admin"
    EXECUTE = "execute"
    READONLY = "readonly"
    AGENT = "agent"
    WORKFLOW = "workflow"


class KeyStatus(str, Enum):
    """API key lifecycle status."""

    ACTIVE = "active"
    ROTATING = "rotating"
    PENDING_ROTATION = "pending_rotation"
    EXPIRED = "expired"
    REVOKED = "revoked"


@dataclass
class APIKey:
    """A single API key.

    ``raw_key`` is present only on freshly created in-memory keys (the creation
    return value); it is never persisted.
    """

    id: str
    name: str
    key_hash: str
    scopes: List[KeyScope] = field(default_factory=lambda: [KeyScope.READ])
    status: KeyStatus = KeyStatus.ACTIVE
    created_at: float = field(default_factory=time.time)
    expires_at: Optional[float] = None
    rotation_count: int = 0
    rotated_from: Optional[str] = None
    usage_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    raw_key: Optional[str] = None

    def has_scope(self, scope: KeyScope) -> bool:
        return scope in self.scopes or KeyScope.ADMIN in self.scopes

    def is_valid(self) -> bool:
        if self.status != KeyStatus.ACTIVE:
            return False
        if self.expires_at is not None and time.time() > self.expires_at:
            return False
        return True


class APIKeyManager:
    """API key lifecycle manager (file-backed, hash-only at rest)."""

    def __init__(
        self,
        config: Optional[Any] = None,
        storage_path: Optional[str] = None,
        master_key: Optional[bytes] = None,
    ) -> None:
        self._config = config
        self._storage_path = storage_path
        self._master_key = master_key
        self._keys: Dict[str, APIKey] = {}
        self._lock = threading.RLock()
        if storage_path:
            self._load()

    # --- creation -----------------------------------------------------------
    def create_key(
        self,
        name: str,
        scopes: Optional[List[KeyScope]] = None,
        expires_in_days: Optional[int] = None,
        expires_at: Optional[datetime] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> tuple[APIKey, str]:
        """Create a new API key. Returns ``(key, raw_key)``.

        The raw key is returned exactly once and never stored.
        """
        key_id = f"lhao_{secrets.token_urlsafe(16)}"
        raw_key = f"lhao_{secrets.token_urlsafe(32)}"
        # Normalise scopes to KeyScope enums so the in-memory representation is
        # consistent with _load (which coerces strings -> enums) and with
        # _save / has_scope (which assume enum members). Accepts either
        # KeyScope enums or their string values.
        normalized_scopes = (
            [s if isinstance(s, KeyScope) else KeyScope(s) for s in scopes]
            if scopes
            else [KeyScope.READ]
        )
        key = APIKey(
            id=key_id,
            name=name,
            key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
            scopes=normalized_scopes,
            status=KeyStatus.ACTIVE,
            created_at=time.time(),
            expires_at=self._expiry_ts(expires_in_days, expires_at),
            metadata=dict(metadata) if metadata else {},
            raw_key=raw_key,
        )
        self._keys[key_id] = key
        self._save()
        return key, raw_key

    # --- validation ---------------------------------------------------------
    def validate_key(self, raw_key: str) -> Optional[APIKey]:
        """Validate a raw key and return the matching active key (or None)."""
        raw_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        for key in self._keys.values():
            if key.key_hash == raw_hash and key.status == KeyStatus.ACTIVE:
                key.usage_count += 1
                self._save()
                return key
        return None

    # --- lifecycle ----------------------------------------------------------
    def rotate_key(
        self,
        key_id: str,
        expires_in_days: Optional[int] = None,
    ) -> tuple[APIKey, str]:
        """Rotate a key: old key becomes PENDING_ROTATION, a new key is issued."""
        old = self._keys.get(key_id)
        if old is None:
            raise KeyError(f"unknown key id: {key_id}")
        old.status = KeyStatus.PENDING_ROTATION
        new_key, new_raw = self.create_key(
            name=old.name,
            scopes=list(old.scopes),
            expires_in_days=expires_in_days,
            metadata=dict(old.metadata),
        )
        new_key.rotation_count = old.rotation_count + 1
        new_key.rotated_from = old.id
        self._keys[new_key.id] = new_key
        self._save()
        return new_key, new_raw

    def revoke_key(self, key_id: str) -> bool:
        key = self._keys.get(key_id)
        if key is None:
            return False
        key.status = KeyStatus.REVOKED
        self._save()
        return True

    # --- queries ------------------------------------------------------------
    def get_key(self, key_id: str) -> Optional[APIKey]:
        return self._keys.get(key_id)

    def list_keys(self, scope: Optional[KeyScope] = None) -> List[APIKey]:
        keys = list(self._keys.values())
        if scope is None:
            return keys
        return [k for k in keys if scope in k.scopes]

    def get_key_stats(self) -> Dict[str, Any]:
        by_status: Dict[str, int] = {}
        by_scope: Dict[str, int] = {}
        for key in self._keys.values():
            by_status[key.status.value] = by_status.get(key.status.value, 0) + 1
            for s in key.scopes:
                by_scope[s.value] = by_scope.get(s.value, 0) + 1
        return {
            "total": len(self._keys),
            "by_status": by_status,
            "by_scope": by_scope,
        }

    # --- persistence (hash-only; raw keys never written) --------------------
    def _expiry_ts(
        self,
        expires_in_days: Optional[int],
        expires_at: Optional[datetime],
    ) -> Optional[float]:
        if expires_in_days is not None:
            return time.time() + expires_in_days * 86400.0
        if expires_at is not None:
            return expires_at.timestamp()
        return None

    @staticmethod
    def _serialize_key(k: APIKey) -> Dict[str, Any]:
        return {
            "id": k.id,
            "name": k.name,
            "key_hash": k.key_hash,
            "scopes": [s.value for s in k.scopes],
            "status": k.status.value,
            "created_at": k.created_at,
            "expires_at": k.expires_at,
            "rotation_count": k.rotation_count,
            "rotated_from": k.rotated_from,
            "usage_count": k.usage_count,
            "metadata": k.metadata,
        }

    def _read_disk_keys(self) -> Dict[str, APIKey]:
        """写事务内重读磁盘注册表。

        与 ``_load`` 的"读失败静默跳过"不同：写前读损坏注册表再整份覆盖，
        会让所有已发出的 key 一起消失，因此这里必须抛错。
        """
        if not self._storage_path or not os.path.exists(self._storage_path):
            return {}
        try:
            payload = json.loads(read_text_with_retry(self._storage_path))
        except Exception as exc:  # noqa: BLE001 - corrupt registry must fail closed
            raise APIKeyStoreError(
                f"API key 注册表读取失败（{self._storage_path}）："
                f"{type(exc).__name__}: {exc}；为避免覆盖唯一副本，本次写入已拒绝"
            ) from exc
        keys: Dict[str, APIKey] = {}
        try:
            for item in payload.get("keys", []):
                key = APIKey(
                    id=item["id"],
                    name=item["name"],
                    key_hash=item["key_hash"],
                    scopes=[KeyScope(v) for v in item.get("scopes", [])],
                    status=KeyStatus(item["status"]),
                    created_at=item.get("created_at", time.time()),
                    expires_at=item.get("expires_at"),
                    rotation_count=item.get("rotation_count", 0),
                    rotated_from=item.get("rotated_from"),
                    usage_count=item.get("usage_count", 0),
                    metadata=item.get("metadata", {}),
                )
                keys[key.id] = key
        except Exception as exc:  # noqa: BLE001
            raise APIKeyStoreError(
                f"API key 注册表结构不合法（{self._storage_path}）："
                f"{type(exc).__name__}: {exc}；本次写入已拒绝"
            ) from exc
        return keys

    def _save(self) -> None:
        """读-改-写事务：进程锁 → 实例锁 → OS 锁 → 重读磁盘合并 → 原子替换。

        并发 ``create_key`` / ``rotate_key`` 各自只知道自己新增的 key；如果
        直接用内存快照整份覆盖，后写者会抹掉先写者刚发出的 key。锁内把磁盘
        上已有的 key 并回来（同 id 以内存为准，usage_count 取最大值避免并发
        校验把计数改小）后，再原子替换。
        """
        if not self._storage_path:
            return
        with _PROCESS_WRITE_LOCK:
            with self._lock:
                with exclusive_file_lock(
                    f"{self._storage_path}.lock", description="API Key 注册表"
                ):
                    disk_keys = self._read_disk_keys()
                    for key_id, disk_key in disk_keys.items():
                        current = self._keys.get(key_id)
                        if current is None:
                            self._keys[key_id] = disk_key
                        else:
                            current.usage_count = max(
                                current.usage_count, disk_key.usage_count
                            )
                    payload = {
                        "keys": [
                            self._serialize_key(k) for k in self._keys.values()
                        ]
                    }
                    data = json.dumps(payload).encode("utf-8")
                    try:
                        atomic_write_bytes(self._storage_path, data)
                    except Exception as exc:  # noqa: BLE001
                        raise APIKeyStoreError(
                            f"API key 注册表写入失败（{self._storage_path}）："
                            f"{type(exc).__name__}: {exc}；本次修改未落盘"
                        ) from exc
                    # Defense-in-depth: re-assert 0600 in case the path already
                    # existed with looser permissions. mkstemp already creates
                    # the temp file 0600, so this is the re-assert, not the
                    # primary protection.
                    try:
                        os.chmod(self._storage_path, 0o600)
                    except OSError as exc:
                        logger.warning(
                            "SECURITY: failed to enforce 0600 on API key registry %r "
                            "(registry may be world-readable): %s",
                            self._storage_path, exc,
                        )
                        raise PermissionLockdownError(
                            f"API key registry {self._storage_path!r} could not be locked "
                            f"down to 0600 ({exc}); refusing to report a clean save"
                        ) from exc

    def _load(self) -> None:
        if not self._storage_path or not os.path.exists(self._storage_path):
            return
        try:
            payload = json.loads(read_text_with_retry(self._storage_path))
        except (OSError, json.JSONDecodeError):
            return
        for item in payload.get("keys", []):
            key = APIKey(
                id=item["id"],
                name=item["name"],
                key_hash=item["key_hash"],
                scopes=[KeyScope(v) for v in item.get("scopes", [])],
                status=KeyStatus(item["status"]),
                created_at=item.get("created_at", time.time()),
                expires_at=item.get("expires_at"),
                rotation_count=item.get("rotation_count", 0),
                rotated_from=item.get("rotated_from"),
                usage_count=item.get("usage_count", 0),
                metadata=item.get("metadata", {}),
            )
            self._keys[key.id] = key


# Module-level singleton
_api_key_manager: Optional[APIKeyManager] = None


def get_api_key_manager() -> APIKeyManager:
    global _api_key_manager
    if _api_key_manager is None:
        # Production persists the (hash-only) registry to disk via
        # LIUHAO_API_KEY_STORE. When unset we stay in-memory (dev/test); the
        # fail-closed secret backend gate governs durable secret storage.
        storage_path = os.environ.get("LIUHAO_API_KEY_STORE")
        _api_key_manager = APIKeyManager(storage_path=storage_path)
    return _api_key_manager


def create_api_key(
    name: str,
    scopes: Optional[List[KeyScope]] = None,
    expires_in_days: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> tuple[APIKey, str]:
    return get_api_key_manager().create_key(
        name=name, scopes=scopes, expires_in_days=expires_in_days, metadata=metadata
    )


def validate_api_key(raw_key: str) -> Optional[APIKey]:
    return get_api_key_manager().validate_key(raw_key)
