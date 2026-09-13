"""Durable storage for registered human identities (Policy C-7 / D-3).

Why this module exists
----------------------
``IdentityManager`` holds identities in memory. Until now the only reason a
registration survived a restart was ``LIUHAO_HUMAN_IDENTITIES_FILE``: a JSON
seed file re-read in ``__init__``. That is enough for exactly one process on
exactly one machine -- and nothing more:

* two processes registering at once race on read-modify-write, and one of the
  two registrations is silently lost;
* there is no transactional update, so a crash mid-write can leave a file that
  parses but is wrong;
* answering "who is registered, and since when" needs a file parser instead of
  a query.

This module introduces a **store** abstraction with two interchangeable
backends:

``file``
    The original JSON seed file. This stays the **default**, so every existing
    deployment keeps byte-identical behaviour.
``sqlite``
    A real table with WAL and a single-statement upsert. Still one file on
    disk and still no server, which matters twice over: the Identity Kernel
    stays dependency-free (stdlib ``sqlite3`` only -- importing
    ``src.integrations`` from a kernel would invert the layering and drag
    SQLAlchemy into every kernel consumer), and it stays portable into
    single-port environments that provide no database service.

Fail-closed is preserved on both backends: an unreadable, missing or
unconfigured store yields **zero humans**, never a machine identity (OD-010).
A store is only ever *written* by an explicit registration; simply booting the
kernel never creates one.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import sqlite3
import threading
from typing import Any, Dict, Iterator, List, Optional

from src._time import utc_now

logger = logging.getLogger("liuhao.kernel.identity")

#: Which backend to use: ``"file"`` (default) or ``"sqlite"``.
HUMAN_IDENTITIES_BACKEND_ENV = "LIUHAO_HUMAN_IDENTITIES_BACKEND"

#: Path to the SQLite database, used when the backend is ``sqlite``.
HUMAN_IDENTITIES_DB_ENV = "LIUHAO_HUMAN_IDENTITIES_DB"

#: Path to the JSON seed file, used when the backend is ``file``.
#:
#: ``IdentityManager`` keeps identities in memory, so a human registered
#: through a one-off call would vanish on restart -- which would make the
#: approval channel look wired while having nobody able to approve. Pointing
#: this at a JSON file is what used to make registration durable. Unset means
#: **no humans are registered**: fail-closed and honest, rather than falling
#: back to a machine identity.
HUMAN_IDENTITIES_FILE_ENV = "LIUHAO_HUMAN_IDENTITIES_FILE"

BACKEND_FILE = "file"
BACKEND_SQLITE = "sqlite"

#: Serialised field names. These are *storage* keys, deliberately spelled the
#: same as the kernel's metadata keys (``principal`` / ``display_name`` /
#: ``permissions`` / ``scope`` / ``registered_at``) so one dictionary shape
#: flows from disk to ``AgentIdentity.metadata`` without translation.
FIELD_PRINCIPAL = "principal"
FIELD_DISPLAY_NAME = "display_name"
FIELD_PERMISSIONS = "permissions"
FIELD_SCOPE = "scope"
FIELD_REGISTERED_AT = "registered_at"
FIELD_SOURCE = "source"

#: Default location for the SQLite backend when only the backend is chosen.
#: Anchored to the repository root so it does not depend on the CWD.
DEFAULT_SQLITE_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "human_identities.sqlite3"
)

_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS human_identities (
    principal     TEXT PRIMARY KEY,
    display_name  TEXT,
    permissions   TEXT NOT NULL DEFAULT '[]',
    scope         TEXT NOT NULL DEFAULT 'L0',
    registered_at TEXT NOT NULL DEFAULT '',
    source        TEXT NOT NULL DEFAULT ''
)
"""

# Deliberately does NOT overwrite ``registered_at`` on conflict: that column
# records when the principal was *first* registered, and an upsert is an edit,
# not a re-registration.
_SQLITE_UPSERT = """
INSERT INTO human_identities
    (principal, display_name, permissions, scope, registered_at, source)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(principal) DO UPDATE SET
    display_name = excluded.display_name,
    permissions  = excluded.permissions,
    scope        = excluded.scope,
    source       = excluded.source
"""


def _decode_permissions(raw: Any) -> List[str]:
    """Decode the ``permissions`` column, tolerating hand-edited rows."""
    if isinstance(raw, list):
        return [str(p) for p in raw]
    text = (raw or "").strip()
    if not text:
        return []
    try:
        decoded = json.loads(text)
    except ValueError:
        # A comma-separated list is what a human would type into a DB browser.
        return [p.strip() for p in text.split(",") if p.strip()]
    if isinstance(decoded, list):
        return [str(p) for p in decoded]
    return []


def _stamp_registered_at(entry: Dict[str, Any]) -> str:
    """Registration instant, generated when the caller did not supply one.

    Stamping here rather than in the kernel is deliberate: ``registered_at`` is
    a property of the *record*, and a caller that writes through the store
    directly (operator tooling) must not be able to leave it blank. An empty
    value therefore means "stamp now", while an existing value is handed back
    untouched so an edit never rewrites the original registration time.
    """
    provided = entry.get(FIELD_REGISTERED_AT)
    if isinstance(provided, str) and provided.strip():
        return provided
    return utc_now().isoformat()


class JsonFileStore:
    """The original JSON seed file, unchanged in behaviour.

    Tolerates the two shapes the kernel already accepted -- a bare list, or
    ``{"humans": [...]}`` -- and refuses to invent one when the path is unset.
    """

    backend_name = BACKEND_FILE

    def __init__(self, location: str):
        self.location = location or ""

    def load_all(self) -> List[Dict[str, Any]]:
        if not self.location:
            # Unconfigured is the fail-closed default, not an error.
            return []
        if not os.path.isfile(self.location):
            logger.warning(
                "%s is set to %r but no such file exists -- no human identity "
                "can approve. Create it with scripts/register_human_identity.py.",
                HUMAN_IDENTITIES_FILE_ENV, self.location,
            )
            return []
        try:
            with open(self.location, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception as exc:  # noqa: BLE001 - config problem, not a crash
            logger.error(
                "could not read %s (%r): %s -- no human identity loaded",
                HUMAN_IDENTITIES_FILE_ENV, self.location, exc,
            )
            return []

        if isinstance(payload, dict):
            entries = list(payload.get("humans") or [])
        elif isinstance(payload, list):
            entries = list(payload)
        else:
            logger.error(
                "%s (%r) must be a JSON list or {'humans': [...]} -- got %s",
                HUMAN_IDENTITIES_FILE_ENV, self.location, type(payload).__name__,
            )
            return []
        kept: List[Dict[str, Any]] = []
        for entry in entries:
            if isinstance(entry, dict):
                kept.append(entry)
            else:
                logger.error(
                    "skipping non-object entry in %r: %r", self.location, entry,
                )
        return kept

    def _read_document(self) -> Dict[str, Any]:
        if not self.location:
            return {"humans": []}
        try:
            with open(self.location, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except FileNotFoundError:
            return {"humans": []}
        except Exception as exc:  # noqa: BLE001
            logger.error("could not read %r for update: %s", self.location, exc)
            return {"humans": []}
        if isinstance(payload, list):
            return {"humans": [e for e in payload if isinstance(e, dict)]}
        if isinstance(payload, dict):
            payload["humans"] = [
                e for e in (payload.get("humans") or []) if isinstance(e, dict)
            ]
            return payload
        logger.error("%r is neither a JSON object nor a list", self.location)
        return {"humans": []}

    def _write_document(self, document: Dict[str, Any]) -> bool:
        """Atomically replace the file -- never leave a half-written seed."""
        if not self.location:
            logger.debug("no %s configured -- registration is in-memory only",
                         HUMAN_IDENTITIES_FILE_ENV)
            return False
        try:
            path = Path(self.location)
            if path.parent and str(path.parent):
                path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(document, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(tmp, path)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("could not write %r: %s", self.location, exc)
            return False

    def upsert(self, entry: Dict[str, Any]) -> bool:
        principal = str(entry.get(FIELD_PRINCIPAL) or "").strip()
        if not principal:
            return False
        document = self._read_document()
        humans = document.setdefault("humans", [])
        stored = {
            FIELD_PRINCIPAL: principal,
            FIELD_DISPLAY_NAME: entry.get(FIELD_DISPLAY_NAME),
            FIELD_PERMISSIONS: sorted(set(entry.get(FIELD_PERMISSIONS) or [])),
            FIELD_SCOPE: entry.get(FIELD_SCOPE) or "L0",
            FIELD_REGISTERED_AT: _stamp_registered_at(entry),
            FIELD_SOURCE: entry.get(FIELD_SOURCE) or "",
        }
        for index, existing in enumerate(humans):
            if existing.get(FIELD_PRINCIPAL) == principal:
                # Keep the original registration instant, same as SQLite.
                stored[FIELD_REGISTERED_AT] = (
                    existing.get(FIELD_REGISTERED_AT)
                    or stored[FIELD_REGISTERED_AT]
                )
                humans[index] = stored
                break
        else:
            humans.append(stored)
        return self._write_document(document)

    def remove(self, principal: str) -> bool:
        if not self.location:
            return False
        document = self._read_document()
        humans = document.get("humans") or []
        kept = [h for h in humans if h.get(FIELD_PRINCIPAL) != principal]
        if len(kept) == len(humans):
            return False
        document["humans"] = kept
        return self._write_document(document)


class SqliteHumanIdentityStore:
    """A single-file SQLite table of registered humans.

    No server, no driver beyond the stdlib, and safe for several processes: the
    upsert is one statement, so concurrent registrations cannot clobber each
    other the way read-modify-write on a JSON file can.
    """

    backend_name = BACKEND_SQLITE

    def __init__(self, location: str):
        self.location = str(location)
        self._schema_ready = False
        self._lock = threading.Lock()

    # ---- connection plumbing -------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.location, timeout=10.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _prepare(self) -> None:
        """Create the table and enable WAL once, before any caller connects.

        Ordering matters and is not cosmetic. ``PRAGMA journal_mode=WAL`` is a
        database-header change, so a connection opened *before* it took effect
        is still in rollback-journal mode while the file says WAL. On Windows
        that mismatch surfaces as ``attempt to write a readonly database`` --
        measured: 24 concurrent writers lost 2 registrations with the naive
        "connect, then set up" ordering. Handing out connections only after WAL
        is established removes the mixed-mode connection entirely.
        """
        if self._schema_ready:
            return
        with self._lock:
            if self._schema_ready:
                return
            connection = self._connect()
            try:
                connection.execute(_SQLITE_SCHEMA)
                # WAL keeps a reader (the kernel booting) from blocking the
                # writer (an operator registering) and survives a crash.
                connection.execute("PRAGMA journal_mode=WAL")
                connection.commit()
            finally:
                connection.close()
            self._schema_ready = True

    @contextmanager
    def _session(self) -> Iterator[sqlite3.Connection]:
        # The directory must exist *before* connect(): sqlite3 cannot create a
        # database inside a missing directory and fails with the opaque
        # "unable to open database file".
        Path(self.location).parent.mkdir(parents=True, exist_ok=True)
        self._prepare()
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    # ---- store protocol -------------------------------------------------

    def load_all(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.location):
            # Read paths never create a store: booting the kernel must not
            # manufacture an empty registration database.
            return []
        try:
            with self._session() as connection:
                rows = connection.execute(
                    "SELECT principal, display_name, permissions, scope, "
                    "registered_at FROM human_identities ORDER BY principal"
                ).fetchall()
        except Exception as exc:  # noqa: BLE001 - config problem, not a crash
            logger.error(
                "could not read %s (%r): %s -- no human identity loaded",
                HUMAN_IDENTITIES_DB_ENV, self.location, exc,
            )
            return []
        return [
            {
                FIELD_PRINCIPAL: row["principal"],
                FIELD_DISPLAY_NAME: row["display_name"],
                FIELD_PERMISSIONS: _decode_permissions(row["permissions"]),
                FIELD_SCOPE: row["scope"],
                FIELD_REGISTERED_AT: row["registered_at"],
            }
            for row in rows
        ]

    def upsert(self, entry: Dict[str, Any]) -> bool:
        principal = str(entry.get(FIELD_PRINCIPAL) or "").strip()
        if not principal:
            return False
        try:
            with self._session() as connection:
                connection.execute(
                    _SQLITE_UPSERT,
                    (
                        principal,
                        entry.get(FIELD_DISPLAY_NAME),
                        json.dumps(sorted(set(entry.get(FIELD_PERMISSIONS) or []))),
                        entry.get(FIELD_SCOPE) or "L0",
                        _stamp_registered_at(entry),
                        entry.get(FIELD_SOURCE) or "",
                    ),
                )
        except Exception as exc:  # noqa: BLE001
            logger.error("could not write %r: %s", self.location, exc)
            return False
        return True

    def remove(self, principal: str) -> bool:
        if not os.path.exists(self.location):
            return False
        try:
            with self._session() as connection:
                cursor = connection.execute(
                    "DELETE FROM human_identities WHERE principal = ?", (principal,)
                )
                return cursor.rowcount > 0
        except Exception as exc:  # noqa: BLE001
            logger.error("could not update %r: %s", self.location, exc)
            return False


def resolve_human_identity_store() -> JsonFileStore | SqliteHumanIdentityStore:
    """Pick a backend from the environment.

    Resolution order, designed so that adding the new backend cannot change the
    behaviour of an existing deployment:

    1. ``LIUHAO_HUMAN_IDENTITIES_BACKEND`` names the backend explicitly.
    2. Otherwise, merely *setting* ``LIUHAO_HUMAN_IDENTITIES_DB`` selects
       SQLite -- configuring a database path is an unambiguous intent.
    3. Otherwise the original JSON seed file, exactly as before.
    """
    backend = (os.environ.get(HUMAN_IDENTITIES_BACKEND_ENV) or "").strip().lower()
    configured_db = (os.environ.get(HUMAN_IDENTITIES_DB_ENV) or "").strip()
    if not backend:
        backend = BACKEND_SQLITE if configured_db else BACKEND_FILE

    if backend == BACKEND_SQLITE:
        return SqliteHumanIdentityStore(configured_db or str(DEFAULT_SQLITE_PATH))

    if backend != BACKEND_FILE:
        logger.warning(
            "%s=%r is not a known backend -- falling back to %r",
            HUMAN_IDENTITIES_BACKEND_ENV, backend, BACKEND_FILE,
        )

    return JsonFileStore((os.environ.get(HUMAN_IDENTITIES_FILE_ENV) or "").strip())


def describe_human_identity_store(
    store: Optional[JsonFileStore | SqliteHumanIdentityStore] = None,
) -> Dict[str, Any]:
    """Report which store is in use -- for boot logs and operator tooling."""
    resolved = store if store is not None else resolve_human_identity_store()
    return {
        "backend": resolved.backend_name,
        "location": resolved.location,
        "configured": bool(resolved.location),
    }
