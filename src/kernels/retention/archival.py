"""HD-06 — archival interface and cold-storage backends.

Archival is **copy-only**: :meth:`ArchivalTarget.archive` writes a byte payload
to cold storage and MUST NOT remove or mutate the original. The original's
metadata stays in the manager; only a *copy* lands in cold storage.

Cold storage is provider-neutral: :class:`LocalFsColdStorage` ships today; an
:class:`ObjectStorageArchiveTarget` adapter is provided for object storage
(S3/GCS/...) and is wired through dependency injection so the swap is a config
change, not a rewrite.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from .lifecycle import LifecycleMetadata


class ColdStorageBackend(ABC):
    """Provider-neutral cold-storage primitive (put/get/exists/delete of bytes)."""

    @abstractmethod
    def put(self, key: str, data: bytes) -> str:
        """Store ``data`` under ``key``; return a stable locator string."""
        raise NotImplementedError

    @abstractmethod
    def get(self, key: str) -> bytes:
        raise NotImplementedError

    @abstractmethod
    def exists(self, key: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def delete(self, key: str) -> bool:
        """Remove a stored copy. Only ever used for DERIVATIVE purges."""
        raise NotImplementedError


class LocalFsColdStorage(ColdStorageBackend):
    """Filesystem-backed cold storage (the provider that ships today)."""

    def __init__(self, root: str) -> None:
        self.root = root
        os.makedirs(root, exist_ok=True)

    def _path(self, key: str) -> str:
        # Namespace keys into a flat, safe filename space.
        safe = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        return os.path.join(self.root, safe + ".cold")

    def put(self, key: str, data: bytes) -> str:
        path = self._path(key)
        tmp = path + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
        shutil.move(tmp, path)  # atomic replace
        return f"file://{path}"

    def get(self, key: str) -> bytes:
        with open(self._path(key), "rb") as fh:
            return fh.read()

    def exists(self, key: str) -> bool:
        return os.path.exists(self._path(key))

    def delete(self, key: str) -> bool:
        path = self._path(key)
        if os.path.exists(path):
            os.remove(path)
            return True
        return False


class ArchivalTarget(ABC):
    """Copies a record's payload to cold storage; never mutates the original."""

    @abstractmethod
    def archive(self, record_id: str, payload: bytes, meta: LifecycleMetadata) -> str:
        """Write ``payload`` to cold storage under ``record_id``; return locator."""
        raise NotImplementedError


class LocalFsArchiveTarget(ArchivalTarget):
    """Local-filesystem archival target (copy-only)."""

    def __init__(self, root: str) -> None:
        self._backend = LocalFsColdStorage(root)

    def archive(self, record_id: str, payload: bytes, meta: LifecycleMetadata) -> str:
        # Copy-only: the caller retains the original bytes; this just stores a copy.
        return self._backend.put(record_id, payload)

    def backend(self) -> ColdStorageBackend:
        return self._backend


@dataclass
class ObjectStorageConfig:
    """Connection config for a future object-storage backend (adapter-ready)."""

    bucket: str
    prefix: str = "retention/"
    endpoint: Optional[str] = None  # None => provider default
    region: Optional[str] = None


class ObjectStorageArchiveTarget(ArchivalTarget):
    """Adapter for object storage (S3/GCS/...).

    Ships as an injection point, NOT a live integration: it accepts a
    ``ColdStorageBackend`` (e.g. a boto3-backed implementation supplied at
    wiring time) so the provider swap is a config change. Without a backend it
    fails closed rather than silently dropping data.
    """

    def __init__(
        self, config: ObjectStorageConfig, backend: Optional[ColdStorageBackend] = None
    ) -> None:
        self.config = config
        self._backend = backend

    def archive(self, record_id: str, payload: bytes, meta: LifecycleMetadata) -> str:
        if self._backend is None:
            raise RuntimeError(
                "ObjectStorageArchiveTarget has no ColdStorageBackend wired; "
                "inject a provider client before use (fail-closed, not silent)"
            )
        key = f"{self.config.prefix}{record_id}"
        return self._backend.put(key, payload)
