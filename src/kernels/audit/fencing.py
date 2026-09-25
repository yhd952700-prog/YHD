"""Single-writer fencing primitive for the audit chain (Q3.5 / F2 / HD-02=A).

Makes "exactly one writer" an INVARIANT, not a hope — this closes RCA-1's
cross-process gap (the per-instance ``RLock`` only serialises within one
process) and the C3 split-brain risk (a fenced old writer must not append).

A writer must hold the CURRENT lease token to append. Tokens are strictly
monotonic, so acquiring a new lease invalidates every older token — even if the
old writer never explicitly released. ``require_writer_lease`` rejects a stale
or expired token.

The audit store's write path will acquire/validate this lease immediately
before the ``seq``/``prev_hash`` assignment (that wiring is the migration step,
Q3.5-later).
"""

from __future__ import annotations

import sqlite3
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


class StaleWriterError(Exception):
    """Raised when a writer attempts to append with a stale/expired lease token."""


@dataclass
class LeaseView:
    token: Optional[int]
    owner: Optional[str]
    acquired_at: Optional[float]
    expires_at: Optional[float]


class WriterLease(ABC):
    """Pluggable single-writer lease. Implementations must guarantee a strictly
    monotonic token so a newer lease fences all older ones."""

    @abstractmethod
    def acquire(self, owner: str, ttl_sec: float = 30.0) -> int: ...

    @abstractmethod
    def validate(self, token: int) -> bool: ...

    @abstractmethod
    def is_stale(self, token: int) -> bool: ...

    @abstractmethod
    def release(self, token: int) -> bool: ...

    @abstractmethod
    def current(self) -> LeaseView: ...


def require_writer_lease(lease: WriterLease, token: int) -> None:
    """Raise :class:`StaleWriterError` unless ``token`` is the live, unexpired lease."""
    if lease.is_stale(token) or not lease.validate(token):
        raise StaleWriterError(
            f"writer token={token} is not the current valid lease; "
            f"current={lease.current()}"
        )


class InMemoryWriterLease(WriterLease):
    """Process-local lease for single-node / testing (no external dependency)."""

    def __init__(self) -> None:
        self._token = 0
        self._owner: Optional[str] = None
        self._acquired_at: Optional[float] = None
        self._expires_at: Optional[float] = None

    def acquire(self, owner: str, ttl_sec: float = 30.0) -> int:
        now = time.time()
        if self._token and self._expires_at and self._expires_at > now:
            raise StaleWriterError(
                f"lease already held by {self._owner!r} until {self._expires_at}"
            )
        self._token += 1
        self._owner = owner
        self._acquired_at = now
        self._expires_at = now + ttl_sec
        return self._token

    def validate(self, token: int) -> bool:
        now = time.time()
        if self._owner is None:  # released or never acquired
            return False
        return (
            self._token == token
            and self._expires_at is not None
            and self._expires_at > now
        )

    def is_stale(self, token: int) -> bool:
        # A token is stale iff it is not the current, valid lease.
        return not self.validate(token)

    def release(self, token: int) -> bool:
        # Keep the token counter monotonic; only invalidate the active holder.
        if token == self._token:
            self._owner = None
            self._acquired_at = None
            self._expires_at = None
            return True
        return False

    def current(self) -> LeaseView:
        return LeaseView(
            self._token or None, self._owner, self._acquired_at, self._expires_at
        )


_LEASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS writer_lease (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    token INTEGER NOT NULL,
    owner TEXT,
    acquired_at REAL,
    expires_at REAL
);
"""


class SqliteWriterLease(WriterLease):
    """Fencing token lease backed by the audit DB itself (single source of truth).

    The lease table lives in the same database as the audit events, so the lease
    check and the append are atomic under the same connection/transaction — there
    is no external dependency (no Redis required).
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        conn.executescript(_LEASE_SCHEMA)

    def acquire(self, owner: str, ttl_sec: float = 30.0) -> int:
        now = time.time()
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._conn.execute(
                "SELECT token, owner, expires_at FROM writer_lease WHERE id = 1"
            ).fetchone()
            if row is not None:
                cur_token, cur_owner, cur_exp = row
                if cur_exp is not None and cur_exp > now:
                    raise StaleWriterError(
                        f"lease already held by {cur_owner!r} until {cur_exp}"
                    )
                new_token = (cur_token or 0) + 1
            else:
                new_token = 1
            self._conn.execute(
                "INSERT INTO writer_lease (id, token, owner, acquired_at, expires_at) "
                "VALUES (1, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "token = excluded.token, owner = excluded.owner, "
                "acquired_at = excluded.acquired_at, expires_at = excluded.expires_at",
                (new_token, owner, now, now + ttl_sec),
            )
            self._conn.commit()
            return new_token
        except Exception:
            self._conn.rollback()
            raise

    def validate(self, token: int) -> bool:
        now = time.time()
        row = self._conn.execute(
            "SELECT token, owner, expires_at FROM writer_lease WHERE id = 1"
        ).fetchone()
        if row is None or row[1] is None:  # no lease / released
            return False
        cur_token, _owner, cur_exp = row
        return cur_token == token and (cur_exp is None or cur_exp > now)

    def is_stale(self, token: int) -> bool:
        # A token is stale iff it is not the current, valid lease.
        return not self.validate(token)

    def release(self, token: int) -> bool:
        row = self._conn.execute(
            "SELECT token FROM writer_lease WHERE id = 1"
        ).fetchone()
        if row is not None and row[0] == token:
            # Keep the token counter monotonic; only invalidate the active holder.
            self._conn.execute(
                "UPDATE writer_lease SET owner = NULL, expires_at = 0 "
                "WHERE id = 1"
            )
            self._conn.commit()
            return True
        return False

    def current(self) -> LeaseView:
        row = self._conn.execute(
            "SELECT token, owner, acquired_at, expires_at FROM writer_lease WHERE id = 1"
        ).fetchone()
        if row is None:
            return LeaseView(None, None, None, None)
        return LeaseView(row[0], row[1], row[2], row[3])
