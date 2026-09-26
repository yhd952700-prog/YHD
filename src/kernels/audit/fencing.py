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

import ctypes
import os
import sqlite3
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


def _owner_pid(owner: Optional[str]) -> Optional[int]:
    """Best-effort parse of a process id from a lease ``owner`` string.

    The live audit store uses ``"audit-store:<pid>"`` (see
    ``AuditStore._lease_owner``); other owners parse to ``None`` so we never
    *assume* liveness we cannot establish.
    """
    if not owner or ":" not in owner:
        return None
    tail = owner.rsplit(":", 1)[1]
    try:
        pid = int(tail)
    except ValueError:
        return None
    return pid if pid > 0 else None


def _is_process_alive(pid: int) -> Optional[bool]:
    """Probe whether ``pid`` is still running.

    Returns ``True`` if the process is alive, ``False`` if it is known dead, and
    ``None`` if liveness cannot be determined (no permission / unsupported).

    Platform notes (this matters: the previous implementation used
    ``os.kill(pid, 0)`` everywhere, which is reliable on POSIX but **not** on
    Windows -- on Windows that call returns without error for a process whose
    kernel object still exists after exit, and raises ``OSError`` (not
    ``ProcessLookupError``) for a pid that never existed, so it could neither
    confirm death nor confirm liveness. That made the dead-owner lease takeover
    (Fix A) silently ineffective on the Windows deployment target).

    * POSIX: ``os.kill(pid, 0)`` -- ``ProcessLookupError`` means the pid is gone.
    * Windows: ``OpenProcess`` + ``GetExitCodeProcess``; ``STILL_ACTIVE`` (259)
      means the process is still running. ``ERROR_ACCESS_DENIED`` (5) means the
      pid exists but we cannot query it -> "unknown" (stay conservative). Any
      other failure (e.g. ``ERROR_INVALID_PARAMETER``/87) means the pid does not
      exist -> dead.
    """
    if sys.platform == "win32":
        return _is_process_alive_win32(pid)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        # PermissionError (alive but unprivileged) or any other OSError: we
        # cannot *confirm* death, so report "unknown" and let the caller fall
        # back to the TTL check rather than stealing an unconfirmed-orphan lease.
        return None


def _is_process_alive_win32(pid: int) -> Optional[bool]:
    """Windows liveness probe via ``GetExitCodeProcess`` (``STILL_ACTIVE``=259)."""
    kernel32 = _win32_kernel32()
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        err = ctypes.get_last_error()
        if err == 5:  # ERROR_ACCESS_DENIED -> exists but unqueryable -> unknown
            return None
        return False  # ERROR_INVALID_PARAMETER (87) etc -> pid does not exist
    try:
        exit_code = ctypes.c_ulong()
        if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return exit_code.value == _STILL_ACTIVE
        return None
    finally:
        kernel32.CloseHandle(handle)


_win32_kernel32_cache: Optional[object] = None


def _win32_kernel32():
    global _win32_kernel32_cache
    if _win32_kernel32_cache is None:
        lib = ctypes.WinDLL("kernel32", use_last_error=True)
        lib.OpenProcess.restype = ctypes.c_void_p
        lib.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_uint]
        lib.GetExitCodeProcess.restype = ctypes.c_int
        lib.GetExitCodeProcess.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ulong),
        ]
        lib.CloseHandle.restype = ctypes.c_int
        lib.CloseHandle.argtypes = [ctypes.c_void_p]
        _win32_kernel32_cache = lib
    return _win32_kernel32_cache


_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259


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
            if self._owner == owner:
                # FIX (U38, self-fence): same writer renewing the lease it already
                # holds -- legitimate renewal, not a competing writer. See the
                # equivalent fix in ``SqliteWriterLease.acquire`` for the full
                # rationale. The token is NOT bumped, so a co-existing same-owner
                # holder is not fenced by our renewal (no mutual-fence thrash).
                self._expires_at = now + ttl_sec
                return self._token
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
                    if cur_owner == owner:
                        # FIX (U38, self-fence): this is the *same* writer
                        # re-acquiring a lease it already holds -- a legitimate
                        # RENEWAL, not a competing writer.  ``_ensure_writer_lease``
                        # documents exactly this case ("a legitimate, still-active
                        # single writer refreshing its own lease"), but the branch
                        # below used to reject it, because liveness was judged
                        # only from the *pid* embedded in the owner string -- and
                        # that pid is of course alive for our own process.
                        #
                        # Consequence of the bug: any AuditStore re-created inside
                        # one process while its own lease was still live (singleton
                        # reset, re-connect, per-test isolation) fenced ITSELF out.
                        # Because the mandatory-evidence gate is fail-closed, that
                        # denied *every* HIGH/CRITICAL action for up to the full
                        # lease TTL -- a self-inflicted availability outage.
                        #
                        # Split-brain protection is deliberately untouched: renewal
                        # applies ONLY when the owner string is identical, so a
                        # *different* live owner is still refused just below.
                        #
                        # The token is deliberately NOT bumped: this writer keeps
                        # its existing fence token.  Bumping would invalidate the
                        # token held by any co-existing same-owner instance and
                        # make them fight -- each renewal fencing the other, with
                        # every subsequent write re-acquiring (thrash).
                        self._conn.execute(
                            "UPDATE writer_lease SET expires_at = ? WHERE id = 1",
                            (now + ttl_sec,),
                        )
                        self._conn.commit()
                        return cur_token
                    # Lease not yet TTL-expired and held by a *different* owner.
                    # A *live* owner keeps it (split brain prevention). A *dead*
                    # owner must be fenced over: the previous writer process
                    # exited without releasing, and leaving its lease live for the
                    # full TTL is exactly what caused the C-6 NOT-INERT self-lock
                    # (verify_armed's probe process found the breadth app-suite's
                    # lease still valid). When liveness cannot be determined we
                    # stay conservative and refuse (do not steal an
                    # unconfirmed-orphan lease).
                    pid = _owner_pid(cur_owner)
                    alive = _is_process_alive(pid) if pid is not None else None
                    if alive is not False:
                        raise StaleWriterError(
                            f"lease held by {cur_owner!r} (alive={alive}) until {cur_exp}"
                        )
                    # alive is False -> owner process has exited; take over.
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

    def acquire_within(self, owner: str, ttl_sec: float = 30.0) -> int:
        """Same rules as :meth:`acquire`, but WITHOUT transaction control.

        The caller must already hold a write transaction (``BEGIN IMMEDIATE``).
        This is what makes the fence check and the append share ONE atomic
        transaction -- the property the class docstring claims, and which the
        previous code did not actually deliver (the lease committed its own
        transaction and the append then ran separately).

        Contention is therefore resolved by SQLite's write lock (a competing
        process blocks until the current append commits) rather than by
        surfacing ``StaleWriterError`` to the caller, so a busy peer no longer
        reads as "evidence channel unavailable" -- which the fail-closed gate
        turned into a denial of every governed action (U39).
        """
        now = time.time()
        row = self._conn.execute(
            "SELECT token, owner, expires_at FROM writer_lease WHERE id = 1"
        ).fetchone()
        if row is not None:
            cur_token, cur_owner, cur_exp = row
            if cur_exp is not None and cur_exp > now:
                if cur_owner == owner:
                    # Same writer renewing its own live lease (U38).
                    self._conn.execute(
                        "UPDATE writer_lease SET expires_at = ? WHERE id = 1",
                        (now + ttl_sec,),
                    )
                    return cur_token
                pid = _owner_pid(cur_owner)
                alive = _is_process_alive(pid) if pid is not None else None
                if alive is not False:
                    raise StaleWriterError(
                        f"lease held by {cur_owner!r} (alive={alive}) until {cur_exp}"
                    )
                # alive is False -> owner process exited; take over.
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
        return new_token

    def release_within(self, token: int) -> bool:
        """Release the lease WITHOUT transaction control (caller holds the txn).

        Used to yield the lease as part of the append's commit, so a peer can
        take over the instant the append lands.
        """
        row = self._conn.execute(
            "SELECT token FROM writer_lease WHERE id = 1"
        ).fetchone()
        if row is not None and row[0] == token:
            # Keep the token counter monotonic; only invalidate the active holder.
            self._conn.execute(
                "UPDATE writer_lease SET owner = NULL, expires_at = 0 WHERE id = 1"
            )
            return True
        return False

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
