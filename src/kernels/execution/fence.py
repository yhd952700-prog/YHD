"""Agent-Safety Execution Fence (P0 / D19-D21).

A real **executor identity / lease / fencing** system for the agent action
gate. This is the human-sovereignty-critical counterpart to the audit
single-writer fence (``src/kernels/audit/fencing.py``): where that one makes
"exactly one audit writer" an invariant, this one makes "every autonomous
action is performed by an identified, leased, non-stale, non-replayed
executor with no capability escalation" an invariant.

Design (see docs/blueprint-upgrades/UBX-001-executor-fence/):
  - ``executor_id`` is a stable per-process uuid (NEVER a PID) so PID reuse
    (concern 9) cannot be confused.
  - ``boot_gen`` is the host uptime; a new boot_gen invalidates every prior
    lease (concerns 8 / restart / container restart).
  - ``epoch`` is a global logical era; ``force_new_era`` invalidates every
    lease at once (split-brain recovery, concern 14).
  - a strictly-monotonic global token seq rejects replays & stale tokens
    (concerns 4 / 10).
  - per-executor lease rows allow N concurrent legit executors; the (N+1)th
    new executor is denied (concern 11).
  - heartbeat liveness ⇒ missing heartbeat ⇒ stale (concern 6).

SECURITY POSTURE (non-negotiable, concerns 7/12/16 + audit fail-closed):
  Default-DENY. An action is allowed only if EVERY check passes. Any missing
  / unknown / expired / stale / replayed / escalated / unauthorized executor
  is denied. If the audit channel is required and unavailable, execution is
  REFUSED (fail-closed), never silently allowed.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import sys
import threading
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence, Tuple
import contextvars  # noqa: E402  (placed late only for readability; imported at module load)


# Fence (denial) event counter -- fail-closed observability.
_FENCE_DENIALS = 0


# --------------------------------------------------------------------------- #
# Fence observability hooks (best-effort; never break the gate)
# --------------------------------------------------------------------------- #
# The executor fence is human-sovereignty-critical: it must NEVER fail open or
# stall because an observability sink is unavailable. Metrics are therefore
# lazy-imported and every emit is wrapped so a metrics error is invisible to the
# security path. The functions below are the only things the gate calls.
_FENCE_METRICS: Any = None  # cached module, or False once import is known-bad.


def _fence_metrics() -> Any:
    global _FENCE_METRICS
    if _FENCE_METRICS is None:
        try:
            import src.observability.metrics as _m  # lazy: keep fence import-safe

            _FENCE_METRICS = _m
        except Exception:  # pragma: no cover - metrics are optional
            _FENCE_METRICS = False
    return _FENCE_METRICS


def _emit_fence_metric(fn_name: str, *args: Any) -> None:
    m = _fence_metrics()
    if m is False:
        return
    try:
        getattr(m, fn_name)(*args)
    except Exception:  # pragma: no cover - metrics must never raise
        pass


def _record_fence_denial(reason: str) -> None:
    _emit_fence_metric("record_fence_denial", reason)


def _observe_fence_enforce(duration: float) -> None:
    _emit_fence_metric("observe_fence_enforce", duration)


def _set_fence_active_leases(n: int) -> None:
    _emit_fence_metric("set_fence_active_leases", n)


def _set_fence_epoch(epoch: int) -> None:
    _emit_fence_metric("set_fence_epoch", epoch)


def _record_fence_heartbeat_failure() -> None:
    _emit_fence_metric("record_fence_heartbeat_failure")


def _record_fence_backend_error() -> None:
    _emit_fence_metric("record_fence_backend_error")


def _record_fence_renew_failure() -> None:
    _emit_fence_metric("record_fence_renew_failure")


# --------------------------------------------------------------------------- #
# Process identity + boot generation (mirrors audit/fencing.py, NOT pid-based)
# --------------------------------------------------------------------------- #
_EXECUTOR_ID: Optional[str] = None
_BOOT_GEN: Optional[int] = None


def _process_executor_id() -> Optional[str]:
    """This process's stable executor identity (uuid4 hex), or None.

    Keyed on a uuid, never on ``os.getpid()`` -- so a later process reusing
    the same PID cannot impersonate this one (concern 9 / PID reuse).
    """
    global _EXECUTOR_ID
    if _EXECUTOR_ID is None:
        try:
            _EXECUTOR_ID = uuid.uuid4().hex
        except Exception:  # noqa: BLE001 - identity must never break the path
            return None
    return _EXECUTOR_ID


def _process_boot_gen() -> Optional[int]:
    """This process's host uptime in ms, or None if it cannot be read."""
    global _BOOT_GEN
    if _BOOT_GEN is None:
        _BOOT_GEN = _read_boot_gen_ms()
    return _BOOT_GEN


def _read_boot_gen_ms() -> Optional[int]:
    """Host-local monotonic uptime in milliseconds (best-effort)."""
    try:
        if sys.platform == "win32":
            import ctypes

            lib = ctypes.WinDLL("kernel32", use_last_error=True)
            lib.GetTickCount64.restype = ctypes.c_ulonglong
            lib.GetTickCount64.argtypes = []
            return int(lib.GetTickCount64())
        clk = getattr(time, "CLOCK_BOOTTIME", None)
        if clk is None:
            clk = getattr(time, "CLOCK_MONOTONIC", None)
        if clk is not None:
            return int(time.clock_gettime(clk) * 1000.0)
        return int(time.monotonic() * 1000.0)
    except Exception:  # noqa: BLE001
        return None


_BOOT_TOL_MS = 1000
_SKEW_BUDGET_SEC = 5.0


# --------------------------------------------------------------------------- #
# Errors (every deny signal subclasses ExecutorFenceDenied -> fail-closed)
# --------------------------------------------------------------------------- #
class ExecutorFenceError(Exception):
    """Base class for executor-fence failures."""


class ExecutorFenceDenied(ExecutorFenceError):
    """A default-deny outcome. The action MUST NOT proceed."""


class ExecutorUnknownError(ExecutorFenceDenied):
    """No executor identity / unknown executor (concerns 1, 9, 11)."""


class LeaseExpiredError(ExecutorFenceDenied):
    """The executor's lease TTL has elapsed (concern 5)."""


class StaleExecutorError(ExecutorFenceDenied):
    """The executor is stale: heartbeat lost or boot_gen changed (concerns 7, 8)."""


class FencedExecutorError(ExecutorFenceDenied):
    """This executor's token was superseded -- it was explicitly fenced
    (split-brain victim, concerns 3, 14)."""


class ReplayDetectedError(ExecutorFenceDenied):
    """The same (executor_id, token, correlation_id) was already used
    (concern 10 / replay)."""


class CapabilityEscalationError(ExecutorFenceDenied):
    """The action requests a capability not in the lease grant (concern 12)."""


class ExecutorLimitExceeded(ExecutorFenceDenied):
    """A new executor would exceed the configured concurrent-executor cap
    (concern 11, the N+1th)."""


class AuditUnavailableError(ExecutorFenceDenied):
    """The audit channel is required but unavailable -- fail-closed (concern 16)."""


# --------------------------------------------------------------------------- #
# Context + views
# --------------------------------------------------------------------------- #
@dataclass
class FenceContext:
    """The identity/lease proof threaded through the execution chain.

    Every gate checks this. Absence of a context = anonymous = deny.
    """

    executor_id: str
    token: int
    epoch: int
    granted_capabilities: Tuple[str, ...] = ()
    boot_gen: Optional[int] = None


@dataclass
class ExecutorLeaseView:
    executor_id: Optional[str]
    token: Optional[int]
    epoch: Optional[int]
    owner: Optional[str]
    acquired_at: Optional[float]
    expires_at: Optional[float]
    last_heartbeat_at: Optional[float]
    boot_gen: Optional[int]
    granted_capabilities: Tuple[str, ...]
    released_at: Optional[float]
    state: str  # "held" | "stale" | "released" | "unknown"


# --------------------------------------------------------------------------- #
# Lease store (pluggable; single source of truth for identity + fencing)
# --------------------------------------------------------------------------- #
class ExecutorLease(ABC):
    """Pluggable per-executor lease with a strictly-monotonic global token."""

    @abstractmethod
    def acquire(
        self,
        executor_id: str,
        owner: str,
        ttl_sec: float,
        granted_capabilities: Sequence[str],
        boot_gen: Optional[int] = None,
        my_last_token: Optional[int] = None,
    ) -> int:
        ...

    @abstractmethod
    def acquire_within(
        self,
        executor_id: str,
        owner: str,
        ttl_sec: float,
        granted_capabilities: Sequence[str],
        boot_gen: Optional[int] = None,
        my_last_token: Optional[int] = None,
    ) -> int:
        ...

    @abstractmethod
    def validate(
        self,
        executor_id: str,
        token: int,
        capabilities: Optional[Sequence[str]] = None,
    ) -> bool:
        ...

    @abstractmethod
    def is_stale(self, executor_id: str, token: int) -> bool:
        ...

    @abstractmethod
    def heartbeat(self, executor_id: str, token: int) -> None:
        ...

    @abstractmethod
    def renew(self, executor_id: str, token: int, ttl_sec: float) -> int:
        ...

    @abstractmethod
    def release(self, executor_id: str, token: int) -> bool:
        ...

    @abstractmethod
    def consume_token(self, executor_id: str, token: int, correlation_id: str) -> None:
        ...

    @abstractmethod
    def force_new_era(self) -> int:
        ...

    @abstractmethod
    def current(self, executor_id: str) -> ExecutorLeaseView:
        ...

    @abstractmethod
    def count_active(self, now: Optional[float] = None) -> int:
        ...

    @abstractmethod
    def known_executor(self, executor_id: str) -> bool:
        ...


class InMemoryExecutorLease(ExecutorLease):
    """Process-local lease store for single-node / testing."""

    def __init__(self, heartbeat_timeout: float = 60.0) -> None:
        self._lock = threading.RLock()
        self._leases: dict = {}
        self._consumed: set = set()
        self._global_token: int = 0
        self._global_epoch: int = 0
        self._heartbeat_timeout = heartbeat_timeout

    def acquire(self, executor_id, owner, ttl_sec, granted_capabilities, boot_gen=None, my_last_token=None):
        with self._lock:
            return self._acquire_locked(
                executor_id, owner, ttl_sec, granted_capabilities, boot_gen, my_last_token
            )

    def acquire_within(self, executor_id, owner, ttl_sec, granted_capabilities, boot_gen=None, my_last_token=None):
        with self._lock:
            return self._acquire_locked(
                executor_id, owner, ttl_sec, granted_capabilities, boot_gen, my_last_token
            )

    def _acquire_locked(self, executor_id, owner, ttl_sec, granted_capabilities, boot_gen, my_last_token):
        now = time.time()
        row = self._leases.get(executor_id)
        if row is None or row["released_at"] is not None:
            # No live lease (nonexistent OR explicitly released). A release +
            # re-acquire yields a brand-new token (concern 4: the old token is
            # invalidated) but is the same executor reclaiming, so no epoch bump.
            return self._install(executor_id, owner, now, ttl_sec, granted_capabilities, boot_gen)
        if row["executor_id"] == executor_id:
            if my_last_token is not None and row["token"] > my_last_token:
                raise FencedExecutorError(
                    f"executor {executor_id!r} was fenced: token "
                    f"{row['token']} > my last {my_last_token}"
                )
            same_boot = (row["boot_gen"] is None and boot_gen is None) or (
                row["boot_gen"] == boot_gen
            )
            same_era = row["epoch"] == self._global_epoch
            if same_boot and same_era:
                # Same executor, same process, same era: self-renew, keep token + epoch.
                row["expires_at"] = now + ttl_sec
                row["last_heartbeat_at"] = now
                return row["token"]
            # boot_gen changed, or a new era started (force_new_era / takeover):
            # forced takeover. Bump the epoch (the fence event, concern 8/14).
            self._global_epoch += 1
            return self._install(executor_id, owner, now, ttl_sec, granted_capabilities, boot_gen)
        # Different executor holding.
        if self._is_live(row, now, boot_gen):
            raise StaleExecutorError(
                f"lease held by {row['executor_id']!r} until {row['expires_at']}"
            )
        self._global_epoch += 1
        return self._install(executor_id, owner, now, ttl_sec, granted_capabilities, boot_gen)

    def _install(self, executor_id, owner, now, ttl_sec, granted_capabilities, boot_gen):
        self._global_token += 1
        self._leases[executor_id] = {
            "executor_id": executor_id,
            "token": self._global_token,
            "epoch": self._global_epoch,
            "owner": owner,
            "acquired_at": now,
            "expires_at": now + ttl_sec,
            "last_heartbeat_at": now,
            "boot_gen": boot_gen,
            "granted_capabilities": tuple(granted_capabilities or ()),
            "released_at": None,
        }
        return self._global_token

    def _is_live(self, row, now, my_boot_gen):
        if row.get("last_heartbeat_at") is not None and (
            now - row["last_heartbeat_at"]
        ) > self._heartbeat_timeout:
            return False  # concern 6: missing heartbeat => stale
        if row["released_at"] is not None:
            return False
        if row["expires_at"] is not None and row["expires_at"] <= now - _SKEW_BUDGET_SEC:
            return False
        rb = row.get("boot_gen")
        if rb is None or my_boot_gen is None:
            return True  # unknown boot gen -> refuse takeover (fail-closed)
        if rb > (my_boot_gen or 0) + _BOOT_TOL_MS:
            return True
        if rb < (my_boot_gen or 0) - _BOOT_TOL_MS:
            return False
        return row["expires_at"] is not None and row["expires_at"] > now - _SKEW_BUDGET_SEC

    def validate(self, executor_id, token, capabilities=None):
        with self._lock:
            row = self._leases.get(executor_id)
            if row is None or row["released_at"] is not None:
                return False
            now = time.time()
            if row["token"] != token:
                return False
            if row["expires_at"] is not None and row["expires_at"] <= now:
                return False
            my_boot = _process_boot_gen()
            if self._is_live(row, now, my_boot) is False:
                return False
            if row["epoch"] != self._global_epoch:
                return False
            if capabilities is not None:
                if not set(capabilities).issubset(set(row["granted_capabilities"])):
                    return False
            return True

    def is_stale(self, executor_id, token):
        return not self.validate(executor_id, token)

    def heartbeat(self, executor_id, token):
        with self._lock:
            row = self._leases.get(executor_id)
            if row is not None and row["token"] == token:
                row["last_heartbeat_at"] = time.time()

    def renew(self, executor_id, token, ttl_sec):
        with self._lock:
            row = self._leases.get(executor_id)
            if row is not None and row["token"] == token:
                row["expires_at"] = time.time() + ttl_sec
                row["last_heartbeat_at"] = time.time()
                return token
            raise StaleExecutorError(f"cannot renew: no live lease for {executor_id!r}")

    def release(self, executor_id, token):
        with self._lock:
            row = self._leases.get(executor_id)
            if row is not None and row["token"] == token:
                row["released_at"] = time.time()
                row["owner"] = None
                return True
            return False

    def consume_token(self, executor_id, token, correlation_id):
        with self._lock:
            key = (executor_id, token, correlation_id)
            if key in self._consumed:
                raise ReplayDetectedError(
                    f"replay of (executor={executor_id}, token={token}, "
                    f"corr={correlation_id})"
                )
            self._consumed.add(key)

    def force_new_era(self):
        with self._lock:
            self._global_epoch += 1
            return self._global_epoch

    def current(self, executor_id):
        with self._lock:
            row = self._leases.get(executor_id)
            if row is None:
                return ExecutorLeaseView(None, None, None, None, None, None, None, None, (), None, "unknown")
            now = time.time()
            if row["released_at"] is not None:
                state = "released"
            elif row["expires_at"] is not None and row["expires_at"] <= now:
                state = "stale"
            elif self._is_live(row, now, row.get("boot_gen")) is False:
                state = "stale"
            else:
                state = "held"
            return ExecutorLeaseView(
                row["executor_id"], row["token"], row["epoch"], row["owner"],
                row["acquired_at"], row["expires_at"], row["last_heartbeat_at"],
                row["boot_gen"], row["granted_capabilities"], row["released_at"], state,
            )

    def count_active(self, now=None):
        with self._lock:
            now = now or time.time()
            return sum(
                1 for r in self._leases.values()
                if r["released_at"] is None
                and r["expires_at"] is not None and r["expires_at"] > now
                and self._is_live(r, now, r.get("boot_gen")) is not False
            )

    def known_executor(self, executor_id):
        with self._lock:
            return executor_id in self._leases


class SqliteExecutorLease(ExecutorLease):
    """Fencing token lease backed by a SQLite connection (production).

    The lease tables live in the SAME database as the audit events when this is
    constructed from the audit store's connection, so the fence check and the
    audit append can be atomic under one transaction. No external dependency.
    """

    def __init__(self, conn: sqlite3.Connection, heartbeat_timeout: float = 60.0) -> None:
        self._conn = conn
        self._heartbeat_timeout = heartbeat_timeout
        conn.executescript(_FENCE_SCHEMA)

    def acquire(self, executor_id, owner, ttl_sec, granted_capabilities, boot_gen=None, my_last_token=None):
        if self._conn.in_transaction:
            raise sqlite3.OperationalError("cannot start a transaction within a transaction")
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            token = self._acquire_within_locked(
                executor_id, owner, ttl_sec, granted_capabilities, boot_gen, my_last_token
            )
            self._conn.commit()
            return token
        except Exception:
            self._conn.rollback()
            raise

    def acquire_within(self, executor_id, owner, ttl_sec, granted_capabilities, boot_gen=None, my_last_token=None):
        # Caller holds the write transaction (e.g. the audit append's BEGIN IMMEDIATE).
        return self._acquire_within_locked(
            executor_id, owner, ttl_sec, granted_capabilities, boot_gen, my_last_token
        )

    def _global_epoch_value(self) -> int:
        ep = self._conn.execute(
            "SELECT global_epoch FROM fence_epoch WHERE id = 1"
        ).fetchone()
        return ep[0] if ep else 0

    def _acquire_within_locked(self, executor_id, owner, ttl_sec, granted_capabilities, boot_gen, my_last_token):
        now = time.time()
        row = self._conn.execute(
            "SELECT token, executor_id, epoch, expires_at, boot_gen, "
            "last_heartbeat_at, released_at "
            "FROM executor_fence WHERE executor_id = ?",
            (executor_id,),
        ).fetchone()
        last = my_last_token if my_last_token is not None else -1
        if row is None or row[6] is not None:
            # No live lease (nonexistent OR released): fresh grant -> new token.
            return self._install(executor_id, owner, now, ttl_sec, granted_capabilities, boot_gen)
        cur_token, cur_eid, cur_epoch, cur_exp, cur_boot, cur_hb, rel = row
        if cur_eid == executor_id:
            # Only treat a newer token as a fence signal while the current lease
            # is still LIVE (has not reached its expires_at). An EXPIRED lease's
            # holder is no longer authoritative -- its token already fails
            # validate() -- so a re-acquire must be allowed. Otherwise a
            # crashed/restarted executor whose row was never released would be
            # locked out forever. Note: we use STRICT expiry (expires_at <= now),
            # matching validate(), not _is_live()'s clock-skew grace budget.
            expired = cur_exp is not None and cur_exp <= now
            if last is not None and cur_token > last and not expired:
                raise FencedExecutorError(
                    f"executor {executor_id!r} was fenced: token "
                    f"{cur_token} > my last {last}"
                )
            same_boot = (cur_boot is None and boot_gen is None) or (cur_boot == boot_gen)
            same_era = cur_epoch == self._global_epoch_value()
            if same_boot and same_era:
                self._conn.execute(
                    "UPDATE executor_fence SET expires_at = ?, last_heartbeat_at = ?, "
                    "boot_gen = ?, released_at = NULL WHERE executor_id = ?",
                    (now + ttl_sec, now, boot_gen, executor_id),
                )
                return cur_token
            # boot_gen changed, or a new era started: forced takeover (epoch+1).
            return self._install(executor_id, owner, now, ttl_sec, granted_capabilities, boot_gen, bump_epoch=True)
        if self._is_live(cur_exp, cur_hb, cur_boot, now, boot_gen):
            raise StaleExecutorError(
                f"lease held by {cur_eid!r} (boot_gen={cur_boot}) until {cur_exp}"
            )
        return self._install(executor_id, owner, now, ttl_sec, granted_capabilities, boot_gen, bump_epoch=True)

    def _install(self, executor_id, owner, now, ttl_sec, granted_capabilities, boot_gen, bump_epoch=False):
        ep = self._conn.execute(
            "SELECT global_token_seq, global_epoch FROM fence_epoch WHERE id = 1"
        ).fetchone()
        seq = (ep[0] if ep else 0) + 1
        epoch = (ep[1] if ep else 0) + (1 if bump_epoch else 0)
        self._conn.execute(
            "INSERT INTO fence_epoch (id, global_token_seq, global_epoch) VALUES (1, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET global_token_seq = excluded.global_token_seq, "
            "global_epoch = excluded.global_epoch",
            (seq, epoch),
        )
        self._conn.execute(
            "INSERT INTO executor_fence "
            "(executor_id, token, epoch, owner, acquired_at, expires_at, "
            " last_heartbeat_at, boot_gen, granted_capabilities, released_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL) "
            "ON CONFLICT(executor_id) DO UPDATE SET "
            "token = excluded.token, epoch = excluded.epoch, owner = excluded.owner, "
            "acquired_at = excluded.acquired_at, expires_at = excluded.expires_at, "
            "last_heartbeat_at = excluded.last_heartbeat_at, "
            "boot_gen = excluded.boot_gen, "
            "granted_capabilities = excluded.granted_capabilities, "
            "released_at = excluded.released_at",
            (
                executor_id, seq, epoch, owner, now, now + ttl_sec, now, boot_gen,
                json.dumps(list(granted_capabilities or ())),
            ),
        )
        return seq

    def _is_live(self, expires_at, last_heartbeat_at, row_boot_gen, now, my_boot_gen):
        if last_heartbeat_at is not None and (
            now - last_heartbeat_at
        ) > self._heartbeat_timeout:
            return False  # concern 6: missing heartbeat => stale
        if expires_at is not None and expires_at <= now - _SKEW_BUDGET_SEC:
            return False
        if row_boot_gen is None or my_boot_gen is None:
            return True
        if row_boot_gen > (my_boot_gen or 0) + _BOOT_TOL_MS:
            return True
        if row_boot_gen < (my_boot_gen or 0) - _BOOT_TOL_MS:
            return False
        return expires_at is not None and expires_at > now - _SKEW_BUDGET_SEC

    def validate(self, executor_id, token, capabilities=None):
        now = time.time()
        row = self._conn.execute(
            "SELECT token, epoch, expires_at, boot_gen, released_at, "
            "granted_capabilities, last_heartbeat_at "
            "FROM executor_fence WHERE executor_id = ?",
            (executor_id,),
        ).fetchone()
        if row is None or row[4] is not None:
            return False
        cur_token, cur_epoch, cur_exp, cur_boot, _rel, caps_json, cur_hb = row
        ep = self._conn.execute(
            "SELECT global_epoch FROM fence_epoch WHERE id = 1"
        ).fetchone()
        global_epoch = ep[0] if ep else 0
        if cur_token != token:
            return False
        if cur_exp is not None and cur_exp <= now:
            return False
        if self._is_live(cur_exp, cur_hb, cur_boot, now, _process_boot_gen()) is False:
            return False
        if cur_epoch != global_epoch:
            return False
        if capabilities is not None:
            granted = set(json.loads(caps_json or "[]"))
            if not set(capabilities).issubset(granted):
                return False
        return True

    def is_stale(self, executor_id, token):
        return not self.validate(executor_id, token)

    def heartbeat(self, executor_id, token):
        # Self-commit when not already inside a caller's transaction (mirrors
        # consume_token); fold into the caller's BEGIN IMMEDIATE otherwise so the
        # write is durable and the audit-atomicity guarantee is preserved.
        own = not self._conn.in_transaction
        try:
            if own:
                self._conn.execute("BEGIN IMMEDIATE")
            self._conn.execute(
                "UPDATE executor_fence SET last_heartbeat_at = ? "
                "WHERE executor_id = ? AND token = ? AND released_at IS NULL",
                (time.time(), executor_id, token),
            )
        except Exception:
            if own and self._conn.in_transaction:
                self._conn.rollback()
            raise
        if own:
            self._conn.commit()

    def renew(self, executor_id, token, ttl_sec):
        own = not self._conn.in_transaction
        try:
            if own:
                self._conn.execute("BEGIN IMMEDIATE")
            cur = self._conn.execute(
                "SELECT token FROM executor_fence WHERE executor_id = ? AND released_at IS NULL",
                (executor_id,),
            ).fetchone()
            if cur is None or cur[0] != token:
                raise StaleExecutorError(f"cannot renew: no live lease for {executor_id!r}")
            self._conn.execute(
                "UPDATE executor_fence SET expires_at = ?, last_heartbeat_at = ? "
                "WHERE executor_id = ? AND token = ?",
                (time.time() + ttl_sec, time.time(), executor_id, token),
            )
        except Exception:
            if own and self._conn.in_transaction:
                self._conn.rollback()
            raise
        if own:
            self._conn.commit()
        return token

    def release(self, executor_id, token):
        own = not self._conn.in_transaction
        released = False
        try:
            if own:
                self._conn.execute("BEGIN IMMEDIATE")
            cur = self._conn.execute(
                "SELECT token FROM executor_fence WHERE executor_id = ? AND released_at IS NULL",
                (executor_id,),
            ).fetchone()
            if cur is not None and cur[0] == token:
                self._conn.execute(
                    "UPDATE executor_fence SET owner = NULL, released_at = ? "
                    "WHERE executor_id = ? AND token = ?",
                    (time.time(), executor_id, token),
                )
                released = True
        except Exception:
            if own and self._conn.in_transaction:
                self._conn.rollback()
            raise
        if own:
            self._conn.commit()
        return released

    def consume_token(self, executor_id, token, correlation_id):
        # Manage our own transaction so the replay marker is durable even when
        # the connection is in the sqlite3 module's implicit-transaction mode
        # (otherwise the INSERT auto-opens a txn, the commit below is skipped,
        # and a later conn.close() rolls the write back). When we are ALREADY
        # inside a caller's write transaction (e.g. the audit append's
        # BEGIN IMMEDIATE) we fold into it and let the outer commit flush us.
        own_txn = not self._conn.in_transaction
        try:
            if own_txn:
                self._conn.execute("BEGIN IMMEDIATE")
            self._conn.execute(
                "INSERT INTO executor_fence_consumed "
                "(executor_id, token, correlation_id) VALUES (?, ?, ?)",
                (executor_id, token, correlation_id),
            )
        except sqlite3.IntegrityError:
            if own_txn and self._conn.in_transaction:
                self._conn.rollback()
            raise ReplayDetectedError(
                f"replay of (executor={executor_id}, token={token}, corr={correlation_id})"
            )
        if own_txn:
            self._conn.commit()

    def force_new_era(self):
        # Self-commit when standalone; fold into a caller's transaction otherwise
        # (see heartbeat/renew/release for the same durability pattern).
        own = not self._conn.in_transaction
        try:
            if own:
                self._conn.execute("BEGIN IMMEDIATE")
            ep = self._conn.execute(
                "SELECT global_epoch FROM fence_epoch WHERE id = 1"
            ).fetchone()
            new_epoch = (ep[0] if ep else 0) + 1
            self._conn.execute(
                "UPDATE fence_epoch SET global_epoch = ? WHERE id = 1", (new_epoch,)
            )
        except Exception:
            if own and self._conn.in_transaction:
                self._conn.rollback()
            raise
        if own:
            self._conn.commit()
        return new_epoch

    def current(self, executor_id):
        now = time.time()
        row = self._conn.execute(
            "SELECT executor_id, token, epoch, owner, acquired_at, expires_at, "
            "last_heartbeat_at, boot_gen, granted_capabilities, released_at "
            "FROM executor_fence WHERE executor_id = ?",
            (executor_id,),
        ).fetchone()
        if row is None:
            return ExecutorLeaseView(None, None, None, None, None, None, None, None, (), None, "unknown")
        eid, tok, ep, owner, acq, exp, hb, boot, caps_json, rel = row
        if rel is not None:
            state = "released"
        elif exp is not None and exp <= now:
            state = "stale"
        elif self._is_live(exp, hb, boot, now, boot) is False:
            state = "stale"
        else:
            state = "held"
        return ExecutorLeaseView(
            eid, tok, ep, owner, acq, exp, hb, boot,
            tuple(json.loads(caps_json or "[]")), rel, state,
        )

    def count_active(self, now=None):
        now = now or time.time()
        rows = self._conn.execute(
            "SELECT executor_id, expires_at, boot_gen, last_heartbeat_at, released_at "
            "FROM executor_fence",
            (),
        ).fetchall()
        active = 0
        for eid, exp, boot, hb, rel in rows:
            if rel is not None:
                continue
            if exp is not None and exp <= now:
                continue
            if self._is_live(exp, hb, boot, now, boot) is False:
                continue
            active += 1
        return active

    def known_executor(self, executor_id):
        row = self._conn.execute(
            "SELECT 1 FROM executor_fence WHERE executor_id = ?", (executor_id,)
        ).fetchone()
        return row is not None


_FENCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS executor_fence (
    executor_id        TEXT PRIMARY KEY,
    token              INTEGER NOT NULL,
    epoch              INTEGER NOT NULL DEFAULT 0,
    owner              TEXT,
    acquired_at        REAL,
    expires_at         REAL,
    last_heartbeat_at  REAL,
    boot_gen           INTEGER,
    granted_capabilities TEXT,
    released_at        REAL
);
CREATE TABLE IF NOT EXISTS fence_epoch (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    global_token_seq INTEGER NOT NULL DEFAULT 0,
    global_epoch     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS executor_fence_consumed (
    executor_id   TEXT NOT NULL,
    token         INTEGER NOT NULL,
    correlation_id TEXT NOT NULL,
    PRIMARY KEY (executor_id, token, correlation_id)
);
"""


# --------------------------------------------------------------------------- #
# Orchestrator: single default-DENY entry point
# --------------------------------------------------------------------------- #
class ExecutorFence:
    """Enforces the full fence policy at any gate. Default-DENY."""

    def __init__(
        self,
        lease: ExecutorLease,
        registry=None,
        audit_available: Optional[Callable[[], bool]] = None,
        heartbeat_timeout: float = 60.0,
        max_executors: Optional[int] = None,
    ) -> None:
        self.lease = lease
        self.registry = registry
        self.audit_available = audit_available
        self.heartbeat_timeout = heartbeat_timeout
        self.max_executors = max_executors

    def acquire_for(
        self, executor_id: str, owner: str, capabilities: Sequence[str], ttl_sec: float = 30.0
    ) -> FenceContext:
        """Acquire (or renew) a lease and return a usable FenceContext."""
        boot_gen = _process_boot_gen()
        # Concern 11: N+1 cap. Only a BRAND-NEW executor (an executor_id with no
        # currently-held lease) counts; renewals of an existing lease are exempt.
        if self.max_executors is not None and self.lease.current(executor_id).state != "held":
            if self.lease.count_active() >= self.max_executors:
                _record_fence_denial("ExecutorLimitExceeded")
                raise ExecutorLimitExceeded(
                    f"concurrent executor cap {self.max_executors} reached"
                )
        try:
            token = self.lease.acquire(
                executor_id, owner, ttl_sec, capabilities, boot_gen=boot_gen
            )
        except ExecutorFenceDenied as exc:
            # A backend failure (CoordinationUnavailableError) surfaces here as
            # ExecutorFenceBackendError -> count it as a deny + a backend error.
            reason = type(exc).__name__
            _record_fence_denial(reason)
            if reason == "ExecutorFenceBackendError":
                _record_fence_backend_error()
            raise
        epoch = self.lease.current(executor_id).epoch or 0
        return FenceContext(
            executor_id=executor_id, token=token, epoch=epoch,
            granted_capabilities=tuple(capabilities), boot_gen=boot_gen,
        )

    def enforce(
        self,
        ctx: Optional[FenceContext],
        action: str,
        required_capabilities: Sequence[str] = (),
        correlation_id: Optional[str] = None,
    ) -> None:
        """Raise :class:`ExecutorFenceDenied` on ANY fail-closed condition."""
        start = time.time()
        try:
            self._enforce(ctx, action, required_capabilities, correlation_id)
        except ExecutorFenceDenied as exc:
            global _FENCE_DENIALS
            _FENCE_DENIALS += 1
            reason = type(exc).__name__
            _record_fence_denial(reason)
            if reason == "ExecutorFenceBackendError":
                _record_fence_backend_error()
            raise
        else:
            # Success path: keep the operating gauges fresh (best-effort). These
            # must not be able to turn a successful allow into a failure.
            try:
                if ctx is not None:
                    _set_fence_epoch(ctx.epoch or 0)
                _set_fence_active_leases(self.lease.count_active())
            except Exception:  # noqa: BLE001 - observability must never break the gate
                pass
        finally:
            _observe_fence_enforce(time.time() - start)

    def _enforce(
        self,
        ctx: Optional[FenceContext],
        action: str,
        required_capabilities: Sequence[str],
        correlation_id: Optional[str],
    ) -> None:
        if ctx is None:
            raise ExecutorUnknownError("no executor identity bound (anonymous action)")
        # Concern 16 / audit fail-closed: if the audit channel is required and
        # down, refuse rather than risk an unrecorded autonomous action.
        if self.audit_available is not None and not self.audit_available():
            raise AuditUnavailableError("audit channel unavailable; refusing execution")
        # Concern 9/11: unknown executor (no lease row at all).
        view = self.lease.current(ctx.executor_id)
        if view.executor_id is None:
            raise ExecutorUnknownError(f"unknown executor {ctx.executor_id!r}")
        # Concerns 4/5/7/8/14: validate the lease (token, expiry, liveness,
        # boot_gen, epoch). Capability scope is NOT checked here -- that is a
        # separate concern (12) decided below -- so a wider capability request
        # surfaces as CapabilityEscalationError, never as a mislabeled stale.
        if not self.lease.validate(ctx.executor_id, ctx.token):
            if view.released_at is not None:
                raise StaleExecutorError(f"executor {ctx.executor_id!r} lease released")
            if view.expires_at is not None and view.expires_at <= time.time():
                raise LeaseExpiredError(f"executor {ctx.executor_id!r} lease expired")
            # token / liveness / boot_gen / epoch mismatch -> stale/invalid.
            raise StaleExecutorError(f"executor {ctx.executor_id!r} lease invalid/stale")
        # Concern 12: capability escalation.
        if not set(required_capabilities).issubset(set(ctx.granted_capabilities)):
            raise CapabilityEscalationError(
                f"action {action!r} requires {set(required_capabilities)} but lease "
                f"grants {set(ctx.granted_capabilities)}"
            )
        # Concern 16: authorization freshness -- live registry recheck.
        if self.registry is not None and not self.registry.is_known(ctx.executor_id):
            raise ExecutorUnknownError(f"executor {ctx.executor_id!r} no longer authorized")
        # Concern 10: replay -- mark this exact action attempt consumed.
        cid = correlation_id or uuid.uuid4().hex
        self.lease.consume_token(ctx.executor_id, ctx.token, f"{action}:{cid}")

    def heartbeat(self, ctx: FenceContext) -> None:
        try:
            self.lease.heartbeat(ctx.executor_id, ctx.token)
        except ExecutorFenceDenied as exc:
            # Liveness lost (StaleExecutorError) OR a coordination-backend
            # failure (ExecutorFenceBackendError) both mean the executor can no
            # longer prove it is the live, authorized one -> the heartbeat is a
            # failure and must be visible.
            _record_fence_heartbeat_failure()
            if type(exc).__name__ == "ExecutorFenceBackendError":
                _record_fence_backend_error()
            raise

    def release(self, ctx: FenceContext) -> bool:
        return self.lease.release(ctx.executor_id, ctx.token)

    def force_new_era(self) -> int:
        return self.lease.force_new_era()


def require_executor_lease(fence: ExecutorFence, ctx: Optional[FenceContext], action: str,
                           required_capabilities: Sequence[str] = (),
                           correlation_id: Optional[str] = None) -> None:
    """Convenience wrapper: enforce or raise."""
    fence.enforce(ctx, action, required_capabilities, correlation_id)


# --------------------------------------------------------------------------- #
# Module-level default wiring (override at assembly time)
# --------------------------------------------------------------------------- #
_DEFAULT_FENCE: Optional[ExecutorFence] = None
_FENCE_LOCK = threading.Lock()


def get_executor_fence() -> ExecutorFence:
    """Return the active executor fence (a safe InMemory-backed default)."""
    global _DEFAULT_FENCE
    if _DEFAULT_FENCE is None:
        with _FENCE_LOCK:
            if _DEFAULT_FENCE is None:
                lease = InMemoryExecutorLease()
                _DEFAULT_FENCE = ExecutorFence(lease)
    return _DEFAULT_FENCE


def set_executor_fence(fence: ExecutorFence) -> None:
    """Install a production fence (e.g. Sqlite-backed, sharing the audit conn)."""
    global _DEFAULT_FENCE
    with _FENCE_LOCK:
        _DEFAULT_FENCE = fence


def attach_default_executor_fence(
    backend: Optional[str] = None,
    lease_dir: Optional[str] = None,
    max_executors: Optional[int] = None,
) -> ExecutorFence:
    """Build the production fence and install it as the active one.

    Backend selection is opt-in; the default is **byte-for-byte identical** to
    before (single-node SQLite sharing the audit DB connection):

      * unset / ``"sqlite"`` -> :class:`SqliteExecutorLease` sharing the audit
        connection (no new behaviour).
      * ``"file"``           -> :class:`~src.distribution.coordination.
        DistributedExecutorLease` over a SQLite-WAL coordination backend on a
        **persistent** directory (``LIUHAO_EXECUTOR_FENCE_LEASE_DIR``). This is
        real cross-process fencing that survives restarts. It REFUSES to fall
        back to a temp dir (that would silently lose fencing across restarts),
        so if no dir is configured it stays on sqlite rather than degrade
        silently.
      * ``"redis"``          -> :class:`DistributedExecutorLease` over the
        Lua-based Redis backend. A connect failure is fail-closed
        (``CoordinationUnavailableError``), never silent.

    ``max_executors`` (or ``LIUHAO_EXECUTOR_FENCE_MAX_EXECUTORS``) enforces the
    N+1 concurrent-executor cap (concern 11). Idempotent.
    """
    if max_executors is None:
        env_max = os.environ.get("LIUHAO_EXECUTOR_FENCE_MAX_EXECUTORS")
        if env_max:
            try:
                max_executors = int(env_max)
            except ValueError:
                max_executors = None

    backend = (
        backend
        or os.environ.get("LIUHAO_DISTRIBUTED_LEASE_BACKEND")
        or "sqlite"
    ).strip().lower()

    if backend in ("sqlite", "none", ""):
        from src.kernels.audit import get_audit_connection

        conn = get_audit_connection()
        lease = SqliteExecutorLease(conn)
        fence = ExecutorFence(
            lease, audit_available=lambda: True, max_executors=max_executors
        )
        set_executor_fence(fence)
        return fence

    if backend == "file":
        from src.distribution import coordination as _coord

        if lease_dir is None:
            lease_dir = os.environ.get("LIUHAO_EXECUTOR_FENCE_LEASE_DIR")
        if not lease_dir:
            # Fail-safe: no persistent dir -> keep single-node sqlite rather
            # than silently use a temp dir (which would lose fencing on restart
            # and make "two processes both execute" undetectable).
            from src.kernels.audit import get_audit_connection

            conn = get_audit_connection()
            lease = SqliteExecutorLease(conn)
            fence = ExecutorFence(
                lease, audit_available=lambda: True, max_executors=max_executors
            )
            set_executor_fence(fence)
            return fence

        resolved_dir = os.path.abspath(lease_dir)

        def _factory(name: str) -> "_coord.DistributedLease":
            return _coord.get_distributed_lease(
                name, backend="file", config={"directory": resolved_dir}
            )

        dist = _coord.DistributedExecutorLease(
            lease_factory=_factory,
            max_executors=max_executors,
            admission_ttl=10.0,
        )
        fence = ExecutorFence(
            dist, audit_available=lambda: True, max_executors=max_executors
        )
        set_executor_fence(fence)
        return fence

    if backend == "redis":
        from src.distribution import coordination as _coord

        redis_url = os.environ.get("LIUHAO_EXECUTOR_FENCE_REDIS_URL")

        def _factory(name: str) -> "_coord.DistributedLease":  # noqa: F811
            return _coord.get_distributed_lease(
                name,
                backend="redis",
                config={"url": redis_url} if redis_url else {},
            )

        dist = _coord.DistributedExecutorLease(
            lease_factory=_factory,
            max_executors=max_executors,
            admission_ttl=10.0,
        )
        fence = ExecutorFence(
            dist, audit_available=lambda: True, max_executors=max_executors
        )
        set_executor_fence(fence)
        return fence

    # Unknown backend -> refuse loudly rather than silently degrade.
    raise ValueError(f"Unsupported executor-fence backend: {backend!r}")


# --------------------------------------------------------------------------- #
# Per-call binding (contextvar): the current executor identity on this call stack
# --------------------------------------------------------------------------- #
_FENCE_CONTEXT_VAR: contextvars.ContextVar = contextvars.ContextVar(
    "executor_fence_context", default=None
)


def bind_executor_fence(ctx: Optional[FenceContext]) -> contextvars.Token:
    """Bind an executor fence context for the duration of the call stack."""
    return _FENCE_CONTEXT_VAR.set(ctx)


def current_executor_fence() -> Optional[FenceContext]:
    return _FENCE_CONTEXT_VAR.get()


def unbind_executor_fence(token: contextvars.Token) -> None:
    _FENCE_CONTEXT_VAR.reset(token)


# --------------------------------------------------------------------------- #
# Fence event counter (fail-closed observability)
# --------------------------------------------------------------------------- #
def get_executor_fence_total() -> int:
    """Expose the cumulative executor-fence (denial) count for observability."""
    return _FENCE_DENIALS


# --------------------------------------------------------------------------- #
# Deployment arming + autonomous execution boundary wiring (UBX-005)
# --------------------------------------------------------------------------- #
def executor_fence_armed() -> bool:
    """True iff the executor fence gate is armed for this deployment.

    Mirrors the action-specific check in ``src.kernels._crosscutting`` but is
    not action-specific: the production wiring uses it to decide whether to
    acquire and bind an executor identity around an autonomous action.
    """
    env = os.environ.get("LIUHAO_EXECUTOR_FENCE", "").strip().lower()
    return env in ("on", "1", "true", "yes")


# An executor (process) holds ONE lease for its lifetime; per-action we just
# (re)bind the same context and refresh the heartbeat so the autonomous agent
# keeps a stable identity and two processes cannot both be valid under
# max_executors. Held here (not on the fence) because the fence is intentionally
# stateless about *who* is currently acting.
_PROC_EXECUTOR_CTX: Optional[FenceContext] = None
_PROC_EXECUTOR_LOCK = threading.Lock()


def _acquire_process_executor_lease(
    fence: ExecutorFence,
    executor_id: str,
    owner: str,
    capabilities: Sequence[str],
    ttl_sec: float,
) -> FenceContext:
    global _PROC_EXECUTOR_CTX
    with _PROC_EXECUTOR_LOCK:
        if _PROC_EXECUTOR_CTX is not None:
            # Refresh liveness so the executor does not go stale between actions.
            try:
                fence.heartbeat(_PROC_EXECUTOR_CTX)
            except Exception:  # noqa: BLE001 - heartbeat must never break the action
                pass
            return _PROC_EXECUTOR_CTX
        ctx = fence.acquire_for(executor_id, owner, capabilities, ttl_sec=ttl_sec)
        _PROC_EXECUTOR_CTX = ctx
        return ctx


def release_process_executor_lease() -> None:
    """Release the process's executor lease (call at shutdown / process exit)."""
    global _PROC_EXECUTOR_CTX
    with _PROC_EXECUTOR_LOCK:
        ctx = _PROC_EXECUTOR_CTX
        _PROC_EXECUTOR_CTX = None
    if ctx is not None:
        try:
            get_executor_fence().release(ctx)
        except Exception:  # noqa: BLE001 - best-effort
            pass


def reset_process_executor_lease_for_testing() -> None:
    """Drop the cached process lease WITHOUT releasing (test isolation only)."""
    global _PROC_EXECUTOR_CTX
    with _PROC_EXECUTOR_LOCK:
        _PROC_EXECUTOR_CTX = None


@contextlib.contextmanager
def executor_session(
    action: str = "",
    capabilities: Sequence[str] = (),
    ttl_sec: float = 30.0,
    owner: Optional[str] = None,
):
    """Acquire an executor lease and bind a fence context for the call duration.

    Wrap the autonomous action execution boundary with this. When the executor
    fence gate is ARMED, the action gets a valid executor identity so the
    central default-DENY gate (``@kernel_action``) and the defense-in-depth
    checks in ``world_interface`` / ``host_command.broker`` ALLOW it instead of
    default-denying it. When the gate is NOT armed, this is a pass-through and
    default behaviour is byte-identical.

    Fail-closed: if the gate is armed and a lease cannot be acquired (cap
    reached, backend down, ...) :class:`ExecutorFenceDenied` propagates and the
    boundary MUST refuse the action.
    """
    if not executor_fence_armed():
        yield None
        return
    fence = get_executor_fence()
    executor_id = _process_executor_id()
    if executor_id is None:
        raise ExecutorUnknownError("cannot establish an executor identity")
    owner = owner or executor_id
    ctx = _acquire_process_executor_lease(
        fence, executor_id, owner, capabilities, ttl_sec
    )
    token = bind_executor_fence(ctx)
    try:
        yield ctx
    finally:
        unbind_executor_fence(token)
