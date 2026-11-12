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
import uuid
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
        # #99 — boot generation (host uptime in ms) for the lease liveness test.
        lib.GetTickCount64.restype = ctypes.c_ulonglong
        lib.GetTickCount64.argtypes = []
        _win32_kernel32_cache = lib
    return _win32_kernel32_cache


_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259


# --------------------------------------------------------------------------- #
# #99 — writer identity upgrade (ADR-audit-writer-lease-identity)
# --------------------------------------------------------------------------- #
# Per-process identity so a fenced writer can PROVE it was fenced, and so PID
# reuse / host restart / cross-host collision (T1/T2/T6) can no longer masquerade
# as "a healthy live writer". The uuid is generated once per process and shared by
# every AuditStore in that process (U38: two instances in one process must share
# an identity, or they would fence each other). boot_gen is a host-local monotonic
# uptime in ms, captured once per process; it is the only liveness signal that
# needs no shared wall clock and the only one that can prove a reboot happened.
_LEASE_WRITER_ID: Optional[str] = None
_LEASE_BOOT_GEN: Optional[int] = None
# #99 — the fencing token this process's writer_id last KNEW it held. It is
# process-global (keyed to writer_id), NOT per-AuditStore-instance, because two
# AuditStore instances in one process share the same writer_id and the same lease
# row (U38). A per-instance value would fabricate a false FencedWriterError when a
# second instance re-acquires. It is never reset to None: a reset would make a
# reopen look like a superseded token; the comparison only fires when a peer has
# actually raised the row's token above what we last held.
_LEASE_LAST_TOKEN: Optional[int] = None


def _get_last_token() -> Optional[int]:
    return _LEASE_LAST_TOKEN


def _set_last_token(token: Optional[int]) -> None:
    global _LEASE_LAST_TOKEN
    _LEASE_LAST_TOKEN = token


def _process_writer_id() -> Optional[str]:
    """This process's stable audit-writer identity (uuid4 hex), or None."""
    global _LEASE_WRITER_ID
    if _LEASE_WRITER_ID is None:
        try:
            _LEASE_WRITER_ID = uuid.uuid4().hex
        except Exception:  # noqa: BLE001 - identity must never break the path
            return None
    return _LEASE_WRITER_ID


def _process_boot_gen() -> Optional[int]:
    """This process's host uptime in ms, or None if it cannot be read."""
    global _LEASE_BOOT_GEN
    if _LEASE_BOOT_GEN is None:
        _LEASE_BOOT_GEN = _read_boot_gen_ms()
    return _LEASE_BOOT_GEN


def _read_boot_gen_ms() -> Optional[int]:
    """Host-local monotonic uptime in milliseconds.

    Windows: GetTickCount64. POSIX: CLOCK_BOOTTIME if available (counts
    suspend), else CLOCK_MONOTONIC, else time.monotonic(). Any failure returns
    None so the caller can fall back to "unknown -> refuse" rather than crash.
    """
    try:
        if sys.platform == "win32":
            return int(_win32_kernel32().GetTickCount64())
        clk = getattr(time, "CLOCK_BOOTTIME", None)
        if clk is None:
            clk = getattr(time, "CLOCK_MONOTONIC", None)
        if clk is not None:
            return int(time.clock_gettime(clk) * 1000.0)
        return int(time.monotonic() * 1000.0)
    except Exception:  # noqa: BLE001
        return None


def _lease_identity_mode() -> str:
    """Which identity model governs lease acquisition.

    "pid"  (default) — the legacy owner/PID liveness model, unchanged.
    "uuid" — the ADR-audit-writer-lease-identity model: writer_id + boot_gen +
             writer_epoch, with FencedWriterError for a superseded writer.

    The default is "pid" for this release so the new columns are purely additive
    and the fleet can be proven safe before the switch (ADR §6.5).
    """
    return os.environ.get("LIUHAO_AUDIT_LEASE_IDENTITY", "pid").strip().lower() or "pid"


# SKEW_BUDGET_SEC / BOOT_TOL_MS: tolerances for the liveness test. Every branch
# that cannot PROVE staleness returns "live" (refuse), per the ADR's fail-closed
# rule (a fork is never an acceptable outcome of a fence decision).
_SKEW_BUDGET_SEC = 5.0
_BOOT_TOL_MS = 1000


def _fence_metric():
    """Lazily resolve the writer-fence counter (avoids import-time coupling)."""
    from src.reliability.metrics import counter
    return counter(
        "audit_writer_fence_total",
        "Count of writer-fence events detected by the audit single-writer "
        "lease (a forced takeover or a superseded-token detection).",
        "events",
    )


def get_writer_fence_total() -> float:
    """Expose the cumulative writer-fence count (for audit_stats())."""
    return _fence_metric().sample()


class StaleWriterError(Exception):
    """Raised when a writer attempts to append with a stale/expired lease token."""


class FencedWriterError(StaleWriterError):
    """A writer whose own token was superseded while it believed it still held
    the lease.

    Subclasses :class:`StaleWriterError` so every existing ``except
    StaleWriterError`` site keeps working, but it is a distinct, louder signal:
    it means "you were explicitly fenced", not merely "the lease is held by
    someone else". Carries the epoch of the fence event so the victim (and an
    operator) can prove a takeover occurred (ADR §4.6).
    """

    def __init__(
        self,
        message: str,
        fenced_epoch: Optional[int] = None,
        holder: Optional[str] = None,
        token: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.fenced_epoch = fenced_epoch
        self.fenced_holder = holder
        self.fenced_token = token


@dataclass
class LeaseView:
    token: Optional[int]
    owner: Optional[str]
    acquired_at: Optional[float]
    expires_at: Optional[float]
    writer_id: Optional[str] = None
    writer_epoch: Optional[int] = None
    boot_gen: Optional[int] = None
    released_at: Optional[float] = None


class WriterLease(ABC):
    """Pluggable single-writer lease. Implementations must guarantee a strictly
    monotonic token so a newer lease fences all older ones."""

    @abstractmethod
    def acquire(self, owner: str, ttl_sec: float = 30.0) -> int:
        ...

    @abstractmethod
    def validate(self, token: int) -> bool:
        ...

    @abstractmethod
    def is_stale(self, token: int) -> bool:
        ...

    @abstractmethod
    def release(self, token: int) -> bool:
        ...

    @abstractmethod
    def current(self) -> LeaseView:
        ...


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


# #99 — the lease table gains four additive columns (writer_id, writer_epoch,
# boot_gen, released_at). Existing 5-column rows still work: the new columns are
# NULL-tolerant and are only consulted when non-NULL, so a legacy row falls back
# to the wall-clock TTL (pid mode). See ADR-audit-writer-lease-identity §4.2/§6.
_LEASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS writer_lease (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    token INTEGER NOT NULL,
    owner TEXT,
    acquired_at REAL,
    expires_at REAL,
    writer_id TEXT,
    writer_epoch INTEGER NOT NULL DEFAULT 0,
    boot_gen INTEGER,
    released_at REAL
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

    def acquire(
        self, owner: str, ttl_sec: float = 30.0,
        writer_id: Optional[str] = None, boot_gen: Optional[int] = None,
        my_last_token: Optional[int] = None,
    ) -> int:
        # #99 / finding #7: an exported API must not start a transaction inside
        # one. The previous code unconditionally executed BEGIN IMMEDIATE, which
        # raised "cannot start a transaction within a transaction" for any caller
        # (e.g. a future heartbeat) that already held one.
        if self._conn.in_transaction:
            raise sqlite3.OperationalError(
                "cannot start a transaction within a transaction")
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            if writer_id is not None and _lease_identity_mode() == "uuid":
                token = self._acquire_within_uuid(
                    owner, ttl_sec, writer_id, boot_gen, my_last_token)
            else:
                token = self._acquire_within_pid(owner, ttl_sec)
            self._conn.commit()
            return token
        except Exception:
            self._conn.rollback()
            raise

    def acquire_within(
        self, owner: str, ttl_sec: float = 30.0,
        writer_id: Optional[str] = None,
        boot_gen: Optional[int] = None,
        my_last_token: Optional[int] = None,
    ) -> int:
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

        #99: when ``writer_id`` is supplied AND the process opted into the uuid
        identity model (``LIUHAO_AUDIT_LEASE_IDENTITY=uuid``), the decision uses
        writer_id / boot_gen / writer_epoch and can raise :class:`FencedWriterError`
        (ADR-audit-writer-lease-identity §4). Otherwise the legacy owner/PID model
        is used exactly as before; pid mode is the default for this release.
        """
        if writer_id is not None and _lease_identity_mode() == "uuid":
            return self._acquire_within_uuid(
                owner, ttl_sec, writer_id, boot_gen, my_last_token)
        return self._acquire_within_pid(owner, ttl_sec)

    def _acquire_within_pid(self, owner: str, ttl_sec: float = 30.0) -> int:
        """Legacy owner/PID liveness decision -- unchanged behaviour (pid mode)."""
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

    def _acquire_within_uuid(self, owner, ttl_sec, writer_id, boot_gen, my_last_token):
        """#99 uuid-mode decision (ADR §4.3). One SELECT, one UPDATE/INSERT, zero
        extra round trips -- identical cost profile to the pid path.

        Decision table (every ambiguous cell resolves to REFUSE / fail-closed):
          * no row            -> install, token=1, epoch=0
          * holder == me, token <= my last -> renew (U38 preserved), epoch unchanged
          * holder == me, token  > my last -> FencedWriterError (we were fenced)
          * different holder, lease live    -> StaleWriterError (refuse)
          * different holder, lease not live -> forced takeover, token+1, epoch+1
          * different holder, liveness unknown -> refuse
        """
        now = time.time()
        row = self._conn.execute(
            "SELECT token, writer_id, writer_epoch, expires_at, boot_gen, "
            "released_at FROM writer_lease WHERE id = 1"
        ).fetchone()
        if row is None:
            return self._install_lease(
                1, 0, writer_id, boot_gen, owner, now, ttl_sec)
        cur_token, holder, cur_epoch, cur_exp, cur_boot, _rel = row
        if holder is not None and holder == writer_id:
            # Same writer. If the row token is HIGHER than the token we last
            # held, someone took our token out from under us -> we were fenced.
            # Fail closed and loudly; do NOT silently renew.
            last = my_last_token if my_last_token is not None else -1
            if cur_token > last:
                _fence_metric().inc()
                raise FencedWriterError(
                    f"writer {writer_id!r} was fenced: current token "
                    f"{cur_token} > my last token {last}",
                    fenced_epoch=cur_epoch,
                    holder=holder,
                    token=cur_token,
                )
            # Still ours (released/expired, nobody stole it) -> renew, no epoch bump.
            return self._renew_lease(
                cur_token, (cur_epoch or 0), writer_id, boot_gen, owner,
                now, ttl_sec)
        # Different writer (or a legacy NULL-holder row).
        if self._lease_is_live(cur_exp, cur_boot, now, boot_gen):
            raise StaleWriterError(
                f"lease held by writer {holder!r} (boot_gen={cur_boot}) "
                f"until {cur_exp}"
            )
        # Not live -> forced takeover: token +1 AND epoch +1. This is the event
        # that proves a fence happened (ADR §4.5), so it is counted.
        _fence_metric().inc()
        return self._install_lease(
            (cur_token or 0) + 1, (cur_epoch or 0) + 1,
            writer_id, boot_gen, owner, now, ttl_sec)

    def _lease_is_live(self, expires_at, row_boot_gen, now, my_boot_gen):
        """#99 liveness test replacing _is_process_alive(pid) (ADR §4.4).

        Returns True (live -> refuse takeover / fail closed) unless staleness is
        PROVEN. Every ambiguous branch returns True.
        """
        if expires_at is not None and expires_at <= now - _SKEW_BUDGET_SEC:
            return False  # plainly expired (with skew budget) -> not live
        if row_boot_gen is None or my_boot_gen is None:
            return True  # unknown boot gen -> REFUSE (never steal on ambiguity)
        if row_boot_gen > my_boot_gen + _BOOT_TOL_MS:
            return True  # uptime went backwards or a FOREIGN host (T6) -> REFUSE
        if row_boot_gen < my_boot_gen - _BOOT_TOL_MS:
            return False  # the host rebooted since -> stale, takeover allowed (T2)
        return expires_at is not None and expires_at > now - _SKEW_BUDGET_SEC

    def _install_lease(self, token, epoch, writer_id, boot_gen, owner, now, ttl_sec):
        """Write (or overwrite) the lease row with a NEW token + epoch."""
        self._conn.execute(
            "INSERT INTO writer_lease "
            "(id, token, owner, acquired_at, expires_at, writer_id, writer_epoch, "
            " boot_gen, released_at) "
            "VALUES (1, ?, ?, ?, ?, ?, ?, ?, NULL) "
            "ON CONFLICT(id) DO UPDATE SET "
            "token = excluded.token, owner = excluded.owner, "
            "acquired_at = excluded.acquired_at, expires_at = excluded.expires_at, "
            "writer_id = excluded.writer_id, writer_epoch = excluded.writer_epoch, "
            "boot_gen = excluded.boot_gen, released_at = excluded.released_at",
            (token, owner, now, now + ttl_sec, writer_id, epoch, boot_gen),
        )
        return token

    def _renew_lease(self, token, epoch, writer_id, boot_gen, owner, now, ttl_sec):
        """Renew our own live/released lease: extend the TTL, keep token + epoch."""
        self._conn.execute(
            "UPDATE writer_lease SET expires_at = ?, owner = ?, acquired_at = ?, "
            "writer_id = ?, boot_gen = ?, released_at = NULL WHERE id = 1",
            (now + ttl_sec, owner, now, writer_id, boot_gen),
        )
        return token

    def release_within(self, token: int) -> bool:
        """Release the lease WITHOUT transaction control (caller holds the txn).

        Used to yield the lease as part of the append's commit, so a peer can
        take over the instant the append lands.

        #99: keep ``token`` / ``writer_id`` / ``writer_epoch``. Nulling
        ``writer_id`` would make every subsequent append look like a takeover and
        bump the epoch once per append, destroying its meaning (ADR §4.5). Only
        the live markers are cleared, and ``released_at`` records the yield.
        """
        row = self._conn.execute(
            "SELECT token FROM writer_lease WHERE id = 1"
        ).fetchone()
        if row is not None and row[0] == token:
            self._conn.execute(
                "UPDATE writer_lease SET owner = NULL, expires_at = 0, "
                "released_at = ? WHERE id = 1",
                (time.time(),),
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
            # Keep token / writer_id / writer_epoch; record the yield.
            self._conn.execute(
                "UPDATE writer_lease SET owner = NULL, expires_at = 0, "
                "released_at = ? WHERE id = 1",
                (time.time(),),
            )
            self._conn.commit()
            return True
        return False

    def current(self) -> LeaseView:
        row = self._conn.execute(
            "SELECT token, owner, acquired_at, expires_at, writer_id, "
            "writer_epoch, boot_gen, released_at FROM writer_lease WHERE id = 1"
        ).fetchone()
        if row is None:
            return LeaseView(None, None, None, None)
        return LeaseView(
            row[0], row[1], row[2], row[3],
            row[4], row[5], row[6], row[7])
