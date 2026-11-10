"""Audit Kernel — Tamper-evident audit trail enforcement

The Audit Kernel provides tamper-evident audit logging with hash-chain integrity,
correlation-aware querying, and full event lifecycle management.

依据 Definition Lock §113: Audit Kernel 必须能够
- log() — log events with correlation IDs and scope
- verify_integrity() — verify audit log integrity with SHA256 hash chains
- query() — query audit entries by principal, scope, time range
- Immutable audit chain — no modification of past events
- Human sovereignty audit override logging
- Scope enforcement L0-L7 for audit entries
"""
from __future__ import annotations
from src.kernels._base import KernelLifecycle, KernelStateError
from src.common.hash_chain import HASH_ALGORITHMS, DEFAULT_HASH_ALG
from .durability import configure_audit_durability
from .hashutil import canonical_json, event_payload
from . import verification as _verification
from . import chain_key as _chain_key
from .fencing import (
    SqliteWriterLease,
    require_writer_lease,
    _process_writer_id,
    _process_boot_gen,
    _lease_identity_mode,
    get_writer_fence_total,
    _get_last_token,
    _set_last_token,
)
from src.reliability.metrics import counter, gauge

import json
import logging
import time
import sqlite3
import os
import pathlib
import threading
from src._time import stable_now_epoch
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
import uuid

from src.common.redaction import redact_text
from src.common.data_paths import default_data_file


def _redact_details(value: Any) -> Any:
    """审计 ``details`` 落库前的脱敏（递归，只改字符串值）。

    审计库是"证据"，但不能因为一次上游 401 的错误体里回显了 API Key，就把
    明文永久固化进 SQLite。键保持不变，值走与日志同一套 ``redact_text``，
    让"日志通道"和"审计通道"的判定一致（例如 ``?key=`` 两边都遮）。
    """
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {key: _redact_details(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_details(item) for item in value]
    return value


logger = logging.getLogger("liuhao.kernel.audit")


# --- storage-level faults (distinct from hash-chain integrity) --------------
# These are raised by the storage-fault resilience layer below, so callers and
# tests can tell a "disk / I/O / corruption" fault apart from a generic
# ``sqlite3`` error or a "kernel not initialised" error.
class AuditStorageError(Exception):
    """Base class for audit-store storage faults that are NOT hash-chain
    problems (disk full, I/O error, corruption of the DB/WAL files)."""


class AuditDiskFullError(AuditStorageError):
    """Raised when a write fails because the database or its disk is full
    (SQLITE_FULL). The write is fail-closed: no event was persisted and the
    error is reported loudly so evidence is never silently dropped."""


class AuditCorruptionError(AuditStorageError):
    """Raised when the audit DB or WAL is found corrupt or suffers an I/O error
    (SQLITE_CORRUPT / SQLITE_IOERR). The faulty files are quarantined for
    forensics (never deleted) and -- if a transaction-consistent backup exists --
    restored, but the triggering write is never blindly retried into a worse
    state."""


# How long SQLite waits on the write lock before reporting SQLITE_BUSY.
# Raised from the driver default (5s) because initialisation now holds the
# write lock across the whole schema migration: with N processes starting at
# once, the losers must WAIT for the winner rather than fail.
_SQLITE_BUSY_TIMEOUT_SEC = 30.0

# --- durability: does a "committed" event survive POWER loss? ---------------
# WAL + synchronous=NORMAL survives a process crash but NOT a power cut: a
# transaction that already returned "committed" can be lost. For an evidence
# store whose entire promise is "the record exists", that is the wrong default,
# so it is FULL now.
#
# The reason this is affordable: the fsync cost is per TRANSACTION, not per
# event, so it collapses once appends are batched (Option C1). MEASURED on this
# machine (scripts/bench_audit_append.py methodology, 2000 events, temp DB):
#
#     batch=1    NORMAL 2,435 eps -> FULL 741 eps   = 3.29x
#     batch=50   NORMAL 19,722    -> FULL 13,969    = 1.41x
#     batch=250  NORMAL 21,434    -> FULL 20,249    = 1.06x
#
# So the honest framing is not "durability costs 3x", it is "durability costs
# 3x if you append one at a time and 6% if you batch". Callers that genuinely
# need NORMAL throughput can opt out with LIUHAO_AUDIT_SYNCHRONOUS=NORMAL,
# knowing exactly what they are giving up.
_DEFAULT_SYNCHRONOUS = "FULL"
_VALID_SYNCHRONOUS = ("OFF", "NORMAL", "FULL", "EXTRA")


def _synchronous_level() -> str:
    """The durability grade this store opens with.

    Read per store (not at import time) so an operator can choose the grade
    per deployment and so the choice is testable. An unrecognised value falls
    back to the safe grade rather than to whatever SQLite defaults to.
    """
    level = os.environ.get(
        "LIUHAO_AUDIT_SYNCHRONOUS", _DEFAULT_SYNCHRONOUS).upper()
    return level if level in _VALID_SYNCHRONOUS else _DEFAULT_SYNCHRONOUS


# --- retention: why an evidence store deletes anything at all ---------------
# The audit DB grew by 21.5 MB/h during the 2-hour soak (1.77 -> 44.53 MB,
# +2,000+ rows/h) with zero reclaim, and the full-chain re-verify got slower
# every hour. Unbounded growth is itself availability risk: a full disk turns
# every append into SQLITE_FULL, which the fail-closed gate turns into a denial
# of every HIGH/CRITICAL action.
#
# The policy is deliberately conservative and configurable:
#   * age wins: keep 30 days by default;
#   * a row ceiling (500k) and a byte ceiling (256 MB) are SAFETY VALVES for a
#     pathological producer, not the primary policy;
#   * the newest ``MIN_KEEP`` events are never touched, so the live tail the
#     gateway read paths depend on is always present;
#   * a single pass deletes at most ``MAX_DELETE`` rows so pruning never turns
#     one request into a multi-second stall.
#
# Deletion is a PREFIX delete anchored by ``retention_state``: the surviving
# chain still verifies from the retention floor onward (see verification.py).
#: 默认按时间保留 90 天：覆盖“用户投诉 → 我们开始排查”的常见时间窗。
#: 注意：10 万行 / 100MB 是极强写入下的**应急上限**，触发时会早于 90 天裁剪；
#: 真实桌面负载（每小时数百条）不会碰到，界面会如实展示当前生效策略。
_DEFAULT_RETENTION_DAYS = 90
#: 行数/字节上限才是**绑定约束**（时间策略在单机桌面场景 30 天内几乎不触发）。
#: 长跑实测：合成强负载下调用链每秒写 ~7 条审计事件（~1KB/行），折合
#: 20.5MB/h、约 2 万行/h。256MB 的旧默认意味着文件要长到 12 小时才第一次
#: 裁剪，对单机产品太大；100MB 在同样的极端速率下约 5 小时触顶并进入
#: "裁剪 + 增量回收"的稳态（实测裁剪期斜率 +2.97MB/h）。真实桌面负载
#: （每小时数百条事件）永远碰不到这个上限，时间策略默认保留 90 天。
_DEFAULT_RETENTION_MAX_ROWS = 100_000
_DEFAULT_RETENTION_MAX_BYTES = 104_857_600  # 100 MB
_DEFAULT_RETENTION_MIN_KEEP = 1_000
_DEFAULT_RETENTION_INTERVAL_SEC = 60.0
_DEFAULT_RETENTION_MAX_DELETE = 5_000


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return default
    return max(minimum, value)


def _retention_settings() -> Dict[str, Any]:
    """Resolve the retention policy for this store (env-configurable).

    All thresholds are read once per store; changing them takes effect on
    restart, which keeps a running pruner's behaviour predictable.
    """
    enabled_raw = os.environ.get("LIUHAO_AUDIT_RETENTION_ENABLED", "1").strip()
    enabled = enabled_raw.lower() not in ("0", "false", "no", "off")
    return {
        "enabled": enabled,
        "days": _env_int("LIUHAO_AUDIT_RETENTION_DAYS", _DEFAULT_RETENTION_DAYS),
        "max_rows": _env_int(
            "LIUHAO_AUDIT_RETENTION_MAX_ROWS", _DEFAULT_RETENTION_MAX_ROWS),
        "max_bytes": _env_int(
            "LIUHAO_AUDIT_RETENTION_MAX_BYTES", _DEFAULT_RETENTION_MAX_BYTES),
        "min_keep": max(
            1, _env_int(
                "LIUHAO_AUDIT_RETENTION_MIN_KEEP",
                _DEFAULT_RETENTION_MIN_KEEP, minimum=1)),
        "interval": float(_env_int(
            "LIUHAO_AUDIT_RETENTION_INTERVAL_SEC",
            int(_DEFAULT_RETENTION_INTERVAL_SEC), minimum=1)),
        "max_delete": max(
            1, _env_int(
                "LIUHAO_AUDIT_RETENTION_MAX_DELETE",
                _DEFAULT_RETENTION_MAX_DELETE, minimum=1)),
    }


# --- storage reclamation: deleting rows must actually return disk -----------
# The 22-minute soak measured retention deleting 12,115 rows while the database
# file kept ALL of them as freelist pages: ``PRAGMA auto_vacuum`` was 0, so
# ``PRAGMA incremental_vacuum`` was a no-op; the WAL was never truncated. The
# file went 2.10 -> 9.26 MB in 22 minutes (+21.5 MB/h) and the default policy
# thresholds (30 days / 500k rows / 256 MB) do not trigger in a short window,
# so an installed desktop build grows forever.
#
# Policy:
#   * a NEW database is opened with ``auto_vacuum=INCREMENTAL`` (which must be
#     set BEFORE ``journal_mode=WAL`` -- see ``_init_db``);
#   * after a prune, ``PRAGMA incremental_vacuum`` hands freed pages back and
#     the WAL is truncated;
#   * WAL checkpoint(TRUNCATE) also runs periodically (throttled with the
#     retention interval) so an append-only workload cannot leave a large
#     ``-wal`` behind;
#   * an EXISTING (legacy, auto_vacuum=NONE) database cannot change mode online
#     -- it needs ``VACUUM`` -- so the migration is deferred to the first
#     maintenance tick (never inside the open path), bounded by a size guard,
#     and retried with backoff if it fails.
_DEFAULT_AUTO_VACUUM_MIGRATE = 1
_DEFAULT_AUTO_VACUUM_RETRY_SEC = 3600
_DEFAULT_AUTO_VACUUM_MAX_MIGRATE_BYTES = 1_073_741_824  # 1 GiB
_DEFAULT_WAL_TRUNCATE_MIN_BYTES = 1_048_576  # 1 MB
_AUTO_VACUUM_NONE = 0
_AUTO_VACUUM_INCREMENTAL = 2


def _storage_settings() -> Dict[str, Any]:
    """Resolve storage-reclamation settings once per store (env-configurable).

    ``migrate`` enables the one-time VACUUM migration for legacy databases;
    ``max_migrate_bytes`` keeps an unexpectedly huge evidence store from
    triggering a surprise multi-GB VACUUM (operators can raise it explicitly);
    ``retry_sec`` is the backoff after a failed migration (e.g. disk full);
    ``wal_truncate_min_bytes`` is the WAL size above which the periodic
    checkpoint(TRUNCATE) is attempted.
    """
    migrate_raw = os.environ.get(
        "LIUHAO_AUDIT_AUTO_VACUUM_MIGRATE", "1").strip()
    migrate = migrate_raw.lower() not in ("0", "false", "no", "off")
    return {
        "migrate": migrate,
        "retry_sec": float(_env_int(
            "LIUHAO_AUDIT_AUTO_VACUUM_RETRY_SEC",
            _DEFAULT_AUTO_VACUUM_RETRY_SEC, minimum=1)),
        "max_migrate_bytes": _env_int(
            "LIUHAO_AUDIT_AUTO_VACUUM_MAX_MIGRATE_BYTES",
            _DEFAULT_AUTO_VACUUM_MAX_MIGRATE_BYTES, minimum=1),
        "wal_truncate_min_bytes": _env_int(
            "LIUHAO_AUDIT_WAL_TRUNCATE_MIN_BYTES",
            _DEFAULT_WAL_TRUNCATE_MIN_BYTES, minimum=1),
    }


# --- transient-fault recovery (see AuditStore._write_with_retry) ------------
# SQLite primary result codes, per https://sqlite.org/rescode.html
_SQLITE_BUSY = 5
_SQLITE_LOCKED = 6
_SQLITE_READONLY = 8
# Storage faults the store KNOWS how to handle (as opposed to a generic data
# error). FULL is retried (bounded) then fails closed; IOERR and CORRUPT are
# handled by quarantine + clear error and are deliberately NOT blind-retried --
# a corrupt DB retried in a loop can be driven into a *worse* state, so the
# first sight of corruption moves the evidence aside and reports loudly.
_SQLITE_IOERR = 10
_SQLITE_CORRUPT = 11
_SQLITE_FULL = 13
_RETRYABLE_SQLITE_CODES = frozenset({
    _SQLITE_BUSY, _SQLITE_LOCKED, _SQLITE_READONLY,
    _SQLITE_FULL, _SQLITE_IOERR, _SQLITE_CORRUPT,
})
# Rows per executemany batch when backfilling the cumulative link chain. Only
# a constant factor -- the backfill stays in one transaction, see the comment
# on _attempt_backfill_link_hashes.
_BACKFILL_CHUNK = 5000

_MAX_WRITE_ATTEMPTS = 8
_WRITE_RETRY_BASE_SEC = 0.02
_WRITE_RETRY_MAX_DELAY_SEC = 0.5


def _quarantine_and_maybe_restore(
    db_path: str, *, reason: str, backup_path: Optional[str] = None
) -> Dict[str, Any]:
    """Move the live audit DB / WAL / SHM files aside (preserving them for
    forensics) and optionally restore from a transaction-consistent backup.

    This is the fail-closed half of corruption recovery: the bad files are
    NEVER deleted -- an operator must be able to inspect them -- they are merely
    renamed into a ``.quarantine-<ts>`` directory. If ``backup_path`` points at
    an existing file (e.g. one produced by ``snapshot_audit_db`` / ``VACUUM
    INTO``), it is copied back into place so the store can resume on known-good
    evidence; otherwise the store is left with no DB files and the caller must
    decide policy (it still has the quarantined evidence to fall back to).

    Returns a dict the caller turns into a clear error message.
    """
    import shutil  # local import keeps the hot path import-light

    quarantine_dir = f"{db_path}.quarantine-{int(time.time() * 1000)}"
    os.makedirs(quarantine_dir, exist_ok=True)
    quarantined: List[str] = []
    for candidate in (db_path, db_path + "-wal", db_path + "-shm"):
        if os.path.exists(candidate):
            dest = os.path.join(quarantine_dir, os.path.basename(candidate))
            try:
                shutil.move(candidate, dest)
                quarantined.append(candidate)
            except OSError:  # noqa: BLE001 - best effort; keep going
                pass
    restored_from: Optional[str] = None
    if backup_path and os.path.exists(backup_path):
        try:
            shutil.copyfile(backup_path, db_path)
            restored_from = backup_path
        except OSError:  # noqa: BLE001 - restore failed; leave empty, report
            pass
    return {
        "quarantine_dir": quarantine_dir,
        "quarantined": quarantined,
        "restored_from": restored_from,
    }


# F-04: a dashboard or replay that asks for "everything" must not be able to
# pull the entire multi-million-row chain into memory on the read path. Cap any
# unbounded query_events() at this many rows; a caller-supplied smaller limit is
# still honoured. This is a safety ceiling, not a pagination contract.
_QUERY_EVENTS_MAX_ROWS = 10_000

# Schema applied as individual statements (never executescript()) so it can
# run inside one BEGIN IMMEDIATE -- see the cold-start race note in _init_db.
_SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS audit_events (
        event_id TEXT PRIMARY KEY,
        event_type TEXT NOT NULL,
        principal_id TEXT NOT NULL,
        scope TEXT NOT NULL,
        timestamp REAL NOT NULL,
        correlation_id TEXT NOT NULL,
        outcome TEXT NOT NULL,
        details TEXT,
        event_hash TEXT NOT NULL,
        prev_event_hash TEXT,
        seq INTEGER NOT NULL DEFAULT 0,
        hash_alg TEXT NOT NULL DEFAULT 'sha256',
        created_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
    )""",
    "CREATE INDEX IF NOT EXISTS idx_principal ON audit_events(principal_id)",
    "CREATE INDEX IF NOT EXISTS idx_scope ON audit_events(scope)",
    "CREATE INDEX IF NOT EXISTS idx_timestamp ON audit_events(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_correlation ON audit_events(correlation_id)",
    """CREATE TABLE IF NOT EXISTS chain_state (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        last_seq INTEGER NOT NULL,
        last_hash TEXT
    )""",
    # #99 — single-writer lease table created INSIDE the _init_db transaction so
    # it is covered by the same cold-start write lock as the rest of the schema
    # (ADR-audit-writer-lease-identity §6.1). The four new columns are additive;
    # legacy 5-column rows are migrated by the ALTER below.
    """CREATE TABLE IF NOT EXISTS writer_lease (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        token INTEGER NOT NULL,
        owner TEXT,
        acquired_at REAL,
        expires_at REAL,
        writer_id TEXT,
        writer_epoch INTEGER NOT NULL DEFAULT 0,
        boot_gen INTEGER,
        released_at REAL
    )""",
    # --- retention floor -----------------------------------------------------
    # Audit retention deletes the OLDEST prefix by policy (age / row count /
    # byte size). The hash chain alone cannot survive that: event P+1's
    # prev_event_hash points at a row that no longer exists. This one row
    # records the retained floor so verification can anchor at P+1 instead of
    # pretending seq 1 still exists. It is derived/policy state, not evidence:
    # the surviving rows are always fully recomputed from P+1.
    """CREATE TABLE IF NOT EXISTS retention_state (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        pruned_through_seq INTEGER NOT NULL DEFAULT 0,
        pruned_through_hash TEXT,
        pruned_through_link_hash TEXT,
        pruned_events INTEGER NOT NULL DEFAULT 0,
        pruned_at REAL
    )""",
    # --- key epoch -----------------------------------------------------------
    # The seq at which this database first started using a keyed chain. Without
    # it, an attacker who downgraded the WHOLE chain to sha256 and then waited
    # for one legitimate keyed append would look exactly like a legacy DB being
    # migrated (unkeyed prefix + keyed tail) and verify green. With it, every
    # event at or after this seq MUST be keyed; anything else is a downgrade.
    # Absence of a row means "no key epoch was ever recorded".
    """CREATE TABLE IF NOT EXISTS chain_key_state (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        enabled_from_seq INTEGER NOT NULL
    )""",
    "INSERT OR IGNORE INTO retention_state (id, pruned_through_seq, pruned_events) "
    "VALUES (1, 0, 0)",
)

# --- U39 / ADR "audit single-writer lease" Option B -------------------------
# The writer lease is now taken INSIDE the append's write transaction and
# yielded when that transaction commits (see ``_log_event_locked``). Contention
# is therefore absorbed by SQLite's write lock -- a competing process waits for
# the current append -- instead of being reported as "evidence channel
# unavailable", which the fail-closed gate turned into a denial of every
# governed action. A genuinely fenced writer is still refused immediately.


# --------------------------------------------------------------------------- #
# PHASE 3.6 / A5 — cryptographic algorithm identifiers
# --------------------------------------------------------------------------- #
#
# The chain has always used SHA-256, but never *said so*: ``compute_hash``
# called ``hashlib.sha256`` directly and nothing recorded which algorithm
# produced a given ``event_hash``. Two consequences:
#
#   * an auditor reading a five-year-old event cannot state what verifies it --
#     they can only infer it from the source revision that happened to write it;
#   * migrating to a stronger algorithm becomes a silent, all-or-nothing act,
#     because there is no per-event record to migrate against.
#
# The requirement is: "今天生成的证据，到未来仍然明确知道用什么算法验证".
# Each event therefore carries its algorithm name.
#
# The name is deliberately NOT folded into the hashed payload: that would change
# the hash domain and invalidate every event already written. It travels beside
# the hash, and verification dispatches on it. An event naming an algorithm this
# build does not implement **fails** verification (fail-closed) rather than being
# hashed with whatever the default happens to be -- "I cannot verify this" is
# the honest answer, and the wrong algorithm would only produce a wrong-but-green
# one.

# P0-8: this module now consumes the shared algorithm registry
# (``src.common.hash_chain``) instead of keeping a private copy, so the audit
# chain can never drift from the rest of the system's algorithm set and verifies
# fail-closed through the same dispatch used by HC-02..HC-08. The write path
# (``AuditEvent.compute_hash``) and the verify path (``_verify_integrity_locked``)
# both resolve the declared algorithm through this single source of truth.


class AuditEventType(str, Enum):
    """Types of audit events."""
    ACCESS_CHECK = "access_check"
    ROLE_GRANT = "role_grant"
    ROLE_REVOKE = "role_revoke"
    PERMISSION_CHECK = "permission_check"
    POLICY_EVAL = "policy_eval"
    ACCESS_DENIED = "access_denied"
    ACCESS_ALLOWED = "access_allowed"
    CONDITIONAL_ACCESS = "conditional_access"
    DEFERRED_ACCESS = "deferred_access"
    HUMAN_SOVEREIGNTY_OVERRIDE = "human_sovereignty_override"
    GOAL_CONTROL = "goal_control"
    KERNEL_IMPLEMENTATION = "kernel_implementation"
    KERNEL_STATUS = "kernel_status"
    PHASE_GATE = "phase_gate"
    STATE_CHANGE = "state_change"
    #: 自动化动作审计（自动化模块只记哈希，不记明文）。
    AUTOMATION_ACTION = "automation_action"
    #: 自动化只读观察审计（同样只记哈希，不记明文）。
    AUTOMATION_OBSERVATION = "automation_observation"


class AuditScope(str, Enum):
    """Audit scope levels L0-L7."""
    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"
    L5 = "L5"
    L6 = "L6"
    L7 = "L7"


@dataclass
class AuditBatchResult:
    """Outcome of one :meth:`AuditStore.log_event_batch` call.

    ``appended``   -- events that were actually committed, in batch order.
    ``duplicates`` -- event_ids that were already committed, so they were
                      skipped (retry-safe idempotency). They consumed no seq.

    A returned result means the batch COMMITTED: there is no partial batch.
    If the batch could not commit, the call raised instead.
    """

    appended: List["AuditEvent"]
    duplicates: List[str]


@dataclass
class AuditEvent:
    """Immutable audit event with hash-chain linkage."""
    event_id: str
    event_type: AuditEventType
    principal_id: str
    scope: AuditScope
    timestamp: float
    correlation_id: str
    outcome: str  # "allow", "deny", "conditional", "defer"
    details: Dict[str, Any] = field(default_factory=dict)
    event_hash: Optional[str] = None
    prev_event_hash: Optional[str] = None
    #: Cumulative commitment to the whole ordered prefix (C2).
    #: ``link_hash_i = H(link_hash_{i-1} || event_hash_i)``. This is what makes
    #: a segment verifiable on its own without re-reading the history, and it
    #: is what lets a checkpoint be re-derived instead of trusted.
    link_hash: Optional[str] = None
    #: Name of the algorithm that produced :attr:`event_hash` (PHASE 3.6 / A5).
    hash_alg: str = DEFAULT_HASH_ALG

    def compute_hash(self) -> str:
        """Compute this event's hash using the algorithm it declares.

        Raises :class:`ValueError` when the declared algorithm is not one this
        build implements. Callers that verify integrity treat that as a broken
        event -- see :meth:`AuditStore._verify_integrity_locked` -- so an
        unverifiable event can never be mistaken for a verified one.
        """
        algorithm = HASH_ALGORITHMS.get(self.hash_alg)
        if algorithm is None:
            raise ValueError(
                f"unknown hash algorithm {self.hash_alg!r}; this build can "
                f"verify {sorted(HASH_ALGORITHMS)}"
            )
        # Canonical form comes from one shared function: if the writer and
        # the verifier ever canonicalise differently, verification stops
        # meaning anything -- so they cannot have separate copies.
        return algorithm(canonical_json(event_payload(self)).encode())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "principal_id": self.principal_id,
            "scope": self.scope.value,
            "timestamp": self.timestamp,
            "correlation_id": self.correlation_id,
            "outcome": self.outcome,
            "details": self.details,
            "event_hash": self.event_hash,
            "prev_event_hash": self.prev_event_hash,
            "hash_alg": self.hash_alg,
        }


class AuditStore:
    """Persistent audit store with SQLite backend and hash-chain integrity."""
    lifecycle: KernelLifecycle = KernelLifecycle.UNINITIALIZED

    def __init__(self, db_path: str = None, backup_path: str = None):
        if db_path is None:
            # ③ / evidence-store isolation (config-layer, NOT file attributes):
            # the frozen HC-01 evidence store lives at <project_root>/audit_store.db
            # and must NEVER be written by a caller that forgot to configure a
            # path. The previous default fell back to exactly that file, so any
            # process running without AUDIT_DB_PATH silently polluted the frozen
            # evidence. Read-only forensics
            # (scripts/forensics_evidence_store_pollution.py, run against the live
            # store with AUDIT_DB_PATH redirected to a temp dir and the store
            # only ever copied+read) confirmed the damage: 40,482 events from 40
            # distinct writer identities and ~29k independent correlation_ids all
            # landed in the single shared root file (test-liuhao / mem-isolated /
            # restart-check alongside the canonical liuhao-internal-service).
            # We now FAIL CLOSED: the path must be explicit.
            #   - production sets AUDIT_DB_PATH in docker-compose (prod + staging)
            #     to /app/data/audit_store.db;
            #   - the test suite redirects it via tests/conftest.py.
            # Anything else must make a conscious configuration decision instead
            # of clobbering evidence.
            explicit = os.environ.get("AUDIT_DB_PATH")
            if not explicit:
                # 隔离运行（测试/冒烟）通常只设 ``LIUHAO_DATA_DIR``；此时把审计库
                # 落在数据根里，既满足隔离，也不会碰冰封的证据库。数据根未设置时
                # 仍然 fail-closed（见 :func:`src.common.data_paths.data_root`）。
                data_path = default_data_file("audit_store.db")
                if data_path:
                    explicit = data_path
            if not explicit:
                raise RuntimeError(
                    "AUDIT_DB_PATH is not set and no db_path was supplied. "
                    "Refusing to default to the project-root audit_store.db, which "
                    "is the frozen HC-01 evidence store. Set AUDIT_DB_PATH "
                    "explicitly (production: docker-compose sets "
                    "/app/data/audit_store.db; tests: conftest redirects to a temp "
                    "file) or pass db_path to AuditStore()."
                )
            db_path = explicit
        self._db_path = db_path
        # Transaction-consistent backup used by corruption recovery. Defaults to
        # ``<db>.backup`` or the AUDIT_DB_BACKUP_PATH env var; ``None`` disables
        # auto-restore (quarantine-only, fail-closed).
        if backup_path is None:
            backup_path = os.environ.get("AUDIT_DB_BACKUP_PATH")
        if backup_path is None:
            backup_path = db_path + ".backup"
        self._backup_path: str = backup_path
        # Last corruption-quarantine location, for operators/tests to inspect.
        self._last_quarantine_dir: Optional[str] = None
        self._writer_token: Optional[int] = None
        # NOTE: the "last fencing token this writer_id held" is process-global
        # (see fencing._LEASE_LAST_TOKEN), NOT a per-instance attribute, because
        # two AuditStore instances in one process share the same writer_id and the
        # same lease row (U38). A per-instance copy would fabricate a false
        # FencedWriterError on the second instance.
        # Created before _init_db() and NEVER recreated: _write_with_retry()
        # can re-open the connection while this lock is held, and swapping the
        # lock object underneath a held lock would open a window in which a
        # second thread serialises against a different lock.
        self._lock = threading.RLock()
        # Retention policy is resolved once per store; the pruner is throttled
        # to at most one pass per ``interval`` seconds from the append path.
        self._retention = _retention_settings()
        self._retention_last_check = 0.0
        # Storage-reclamation policy + one-time auto_vacuum migration state.
        # ``_auto_vacuum_pending`` is (re)computed by ``_init_db`` from the
        # database header: a legacy file created with auto_vacuum=NONE stays
        # pending until a VACUUM migration succeeds.
        self._storage = _storage_settings()
        self._auto_vacuum_pending = False
        self._auto_vacuum_next_attempt = 0.0
        self._auto_vacuum_warned = False
        # 惰性判断：库里是否已有 HMAC 事件。它决定“密钥文件不见了”时能不能
        # 生成新钥匙——有带密钥历史时绝不能生成，否则新钥匙签出的事件旧库
        # 永远验不过，且会把“缺密钥”伪装成“可用”。
        self._keyed_history: Optional[bool] = None
        self._init_db()

    def _init_db(self) -> None:
        """Initialize the audit store database with hash-chain schema.

        The schema carries:
        - audit_events.seq: monotonic sequence number assigned at log
          time (immune to timestamp ties on coarse clocks)
        - chain_state: single-row anchor storing the last seq/hash so
          that truncation of the log tail is detectable
        Legacy databases without the seq column are migrated in place.
        """
        os.makedirs(os.path.dirname(os.path.abspath(self._db_path)), exist_ok=True)
        # check_same_thread=False：审计是全局单例，而 FastAPI 的同步端点跑在
        # 线程池里 —— 连接会被多线程复用（此前缺此参数，导致
        # "SQLite objects created in a thread can only be used in that same thread"）。
        # 线程安全由 self._lock 保证（hash-chain 的 seq/prev_hash 必须串行推进）。
        # ---- cold-start race (measured, not guessed) ----------------------
        # When several processes open the SAME brand-new database at once, the
        # first write is also what materialises the -wal / -shm files. That
        # moment loses the race often enough to matter: 6 processes x 12
        # rounds produced "attempt to write a readonly database"
        # (SQLITE_READONLY) in 2-4 of the 12 rounds, while staggering the
        # process starts produced 0/12. Two things follow:
        #   * hold the write lock across the DDL, so whoever arrives first
        #     creates the WAL files and the rest wait on the busy handler;
        #   * retry the whole open+initialise, because the connect/BEGIN pair
        #     itself can be the thing that loses the race -- and unlike a write
        #     it has no transaction to roll back, so it simply reconnects.
        last: Optional[BaseException] = None
        for attempt in range(_MAX_WRITE_ATTEMPTS):
            try:
                self._conn = sqlite3.connect(
                    self._db_path, timeout=_SQLITE_BUSY_TIMEOUT_SEC,
                    check_same_thread=False,
                )
                # ORDER MATTERS (measured): journal mode WAL materialises the
                # DB header, freezing auto_vacuum at NONE. Set it first.
                try:
                    self._conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
                except sqlite3.Error:
                    pass
                # NOTE: PRAGMA journal_mode cannot be changed from inside a
                # transaction, so it must stay above the BEGIN IMMEDIATE below.
                self._conn.execute("PRAGMA journal_mode=WAL")
                # Wired through the durability helper (it used to be dead
                # code -- built and never called), so the level is
                # validated and the applied value is observable.
                configure_audit_durability(self._conn, _synchronous_level())
                self._conn.execute("BEGIN IMMEDIATE")
                self._apply_schema()
                break
            except sqlite3.OperationalError as exc:
                code = getattr(exc, "sqlite_errorcode", None)
                # sqlite_errorcode may be an EXTENDED result code (e.g. 1546 =
                # SQLITE_IOERR_*), while _RETRYABLE_SQLITE_CODES holds the
                # primary codes (10 = SQLITE_IOERR). Comparing the raw value
                # made every IOERR skip the retry loop below: measured while
                # re-opening a crash.db left by a hard-killed writer, where the
                # first PRAGMA journal_mode=WAL raises IOERR 1546 and the same
                # open succeeds ~0.5s later. Mask to the primary code so the
                # bounded retry actually applies (it still re-raises after the
                # last attempt, so a genuinely broken disk is not hidden).
                if code is not None:
                    code &= 0xFF
                if code not in _RETRYABLE_SQLITE_CODES:
                    raise
                last = exc
                try:
                    self._conn.close()
                except Exception:  # noqa: BLE001
                    pass
                if attempt + 1 < _MAX_WRITE_ATTEMPTS:
                    time.sleep(
                        min(_WRITE_RETRY_BASE_SEC * (2 ** attempt),
                            _WRITE_RETRY_MAX_DELAY_SEC)
                    )
        else:
            raise last

        # Q3.5 (fencing): the single-writer lease lives in the audit DB itself,
        # so the fence check and the append are atomic on the same connection.
        self._lease = SqliteWriterLease(self._conn)
        self._detect_auto_vacuum()

    def _detect_auto_vacuum(self) -> None:
        """Record whether this database can actually reclaim freed pages.

        A fresh database opened by the reordered pragmas above reports
        ``auto_vacuum == 2`` (INCREMENTAL). A legacy file created with the old
        "WAL first" order reports ``0`` and cannot be changed online: SQLite
        requires a full ``VACUUM`` to rewrite the header. Such a store is
        marked pending and migrated from the maintenance path, never here.
        """
        mode = self._read_auto_vacuum()
        if mode == _AUTO_VACUUM_INCREMENTAL:
            self._auto_vacuum_pending = False
            return
        self._auto_vacuum_pending = True
        # The VACUUM itself is deferred until reclamation is actually needed
        # (a prune or an existing freelist): rewriting a large legacy file on
        # the open path would delay startup for nothing.
        self._auto_vacuum_next_attempt = 0.0

    def _read_auto_vacuum(self) -> int:
        """Return the current ``PRAGMA auto_vacuum`` mode (0 if unreadable)."""
        try:
            row = self._conn.execute("PRAGMA auto_vacuum").fetchone()
            return int(row[0]) if row else _AUTO_VACUUM_NONE
        except sqlite3.Error:
            return _AUTO_VACUUM_NONE

    def _apply_schema(self) -> None:
        """Create/migrate the schema inside the transaction opened by _init_db."""
        try:
            # executescript() implicitly commits, so the schema is applied as
            # individual statements inside the transaction instead.
            for statement in _SCHEMA_STATEMENTS:
                self._conn.execute(statement)

            # Migration for legacy databases created before the seq column
            columns = [row[1] for row in
                       self._conn.execute("PRAGMA table_info(audit_events)").fetchall()]
            if "seq" not in columns:
                self._conn.execute(
                    "ALTER TABLE audit_events ADD COLUMN seq INTEGER NOT NULL DEFAULT 0"
                )
                # Backfill seq in insertion order for existing rows
                legacy_rows = self._conn.execute(
                    "SELECT event_id FROM audit_events ORDER BY rowid"
                ).fetchall()
                for index, row in enumerate(legacy_rows, start=1):
                    self._conn.execute(
                        "UPDATE audit_events SET seq = ? WHERE event_id = ?",
                        (index, row[0]),
                    )
            # Ensure the seq index exists. This must run AFTER the seq column is
            # guaranteed to exist (either fresh schema or migrated legacy schema),
            # otherwise SQLite raises "no such column: seq".
            # C2/UNIQUE(seq): a database-level backstop against a duplicate or
            # skipped sequence number -- the hash chain's monotonic seq is the
            # audit-order axis, so a duplicate seq would silently merge two
            # events and a gap would break the prev_hash chain. The unique index
            # is additive: it refuses to open a store whose seq is not already
            # strictly unique (fail-closed), which is exactly what we want.
            self._conn.execute("DROP INDEX IF EXISTS idx_seq")
            self._conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uidx_seq ON audit_events(seq)"
            )
            # PHASE 3.6 / A5: bring a legacy table forward. Every row that
            # predates the column was hashed with SHA-256, so the column
            # default is factually correct for them -- this only records what
            # was already true, it does not reinterpret any hash.
            if "hash_alg" not in columns:
                self._conn.execute(
                    "ALTER TABLE audit_events ADD COLUMN hash_alg TEXT NOT NULL "
                    "DEFAULT 'sha256'"
                )
            # C2: cumulative link hash, additive and NULLable so a legacy
            # database keeps verifying until it is backfilled.
            if "link_hash" not in columns:
                self._conn.execute(
                    "ALTER TABLE audit_events ADD COLUMN link_hash TEXT"
                )
                columns.append("link_hash")
            cs_columns = [row[1] for row in self._conn.execute(
                "PRAGMA table_info(chain_state)").fetchall()]
            if "last_link_hash" not in cs_columns:
                self._conn.execute(
                    "ALTER TABLE chain_state ADD COLUMN last_link_hash TEXT")
            _verification.create_tables(self._conn)
            # Seed the chain_state anchor for pre-existing data so integrity
            # verification covers it.
            #
            # INSERT OR IGNORE (not a plain INSERT): this read-then-insert pair
            # runs on every process that opens the store, and it is NOT atomic
            # on its own -- two processes can both observe "no anchor yet" and
            # both try to insert id=1, which raises
            # "UNIQUE constraint failed: chain_state.id" (observed 1/12 rounds
            # with 6 concurrent processes). Losing that race is benign: the row
            # is already correct.
            state = self._conn.execute(
                "SELECT last_seq FROM chain_state WHERE id = 1"
            ).fetchone()
            if state is None:
                last = self._conn.execute(
                    "SELECT seq, event_hash FROM audit_events ORDER BY seq DESC LIMIT 1"
                ).fetchone()
                if last:
                    self._insert_chain_anchor(last[0], last[1])
            # #99 — additive migration of the single-writer lease to the
            # writer-identity model (ADR-audit-writer-lease-identity §4.2/§6.1).
            # New columns are NULL-tolerant and only consulted when non-NULL, so a
            # legacy 5-column row keeps working exactly as before (pid mode). The
            # migration runs inside this BEGIN IMMEDIATE, not as a second
            # unguarded ALTER (Gen-2 ADR finding #8).
            _lease_cols = [r[1] for r in self._conn.execute(
                "PRAGMA table_info(writer_lease)").fetchall()]
            _lease_alters = [
                ("writer_id",
                 "ALTER TABLE writer_lease ADD COLUMN writer_id TEXT"),
                ("writer_epoch",
                 "ALTER TABLE writer_lease ADD COLUMN writer_epoch "
                 "INTEGER NOT NULL DEFAULT 0"),
                ("boot_gen",
                 "ALTER TABLE writer_lease ADD COLUMN boot_gen INTEGER"),
                ("released_at",
                 "ALTER TABLE writer_lease ADD COLUMN released_at REAL"),
            ]
            for _col, _stmt in _lease_alters:
                if _col not in _lease_cols:
                    self._conn.execute(_stmt)
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # ------------------------------------------------------------------ #
    # Q3.5 — single-writer fencing helpers
    # ------------------------------------------------------------------ #
    def _lease_owner(self) -> str:
        """Stable owner id for this store instance's writer lease."""
        return f"audit-store:{os.getpid()}"

    def _ensure_writer_lease(self) -> None:
        """Deprecated shim: the lease is now taken *inside* the append
        transaction (see ``_log_event_locked``). Kept only because it is part
        of the historical internal API; it intentionally does nothing.
        """

    def _insert_chain_anchor(self, last_seq: int, last_hash: Optional[str]) -> None:
        """Write the chain_state anchor, tolerating a peer that got there first.

        INSERT OR IGNORE (never a plain INSERT): this is the tail of a
        read-then-write pair that every process performs when it opens the
        store. Two processes can both read "no anchor yet" and both try to
        write id=1 -- observed once in 12 runs with 6 concurrent processes as
        "UNIQUE constraint failed: chain_state.id". Losing that race is
        harmless: the row a peer wrote is already correct.
        """
        self._conn.execute(
            "INSERT OR IGNORE INTO chain_state (id, last_seq, last_hash) "
            "VALUES (1, ?, ?)",
            (last_seq, last_hash),
        )

    def _reopen(self) -> None:
        """Drop the connection and establish a fresh one.

        Needed because a SQLITE_READONLY fault of the "cannot initialise the
        WAL shared-memory file" kind poisons the *connection*, not the
        statement: retrying on the same handle keeps failing (measured), while
        a new handle succeeds. Schema creation is idempotent, so re-running
        ``_init_db`` is safe -- this never rewrites existing evidence.
        """
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001 - the handle may already be unusable
            pass
        self._writer_token = None
        self._init_db()

    def _handle_storage_corruption(self, code: int, exc: BaseException) -> None:
        """Handle a SQLITE_CORRUPT / SQLITE_IOERR fault WITHOUT blind retry.

        Called from ``_write_with_retry`` the FIRST time corruption is seen. It
        closes the poisoned handle, quarantines the evidence files, attempts a
        restore-from-backup, and then RAISES :class:`AuditCorruptionError` so the
        caller's fail-closed policy decides what to do next. It never retries the
        transaction (that could deepen the corruption) and never silently
        succeeds -- a corrupt store must be made visible, not looped over.
        """
        label = {
            _SQLITE_CORRUPT: "SQLITE_CORRUPT",
            _SQLITE_IOERR: "SQLITE_IOERR",
        }.get(code, f"sqlite-code-{code}")
        try:
            self._conn.rollback()
        except Exception:  # noqa: BLE001 - best-effort
            pass
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001 - handle may already be unusable
            pass
        self._writer_token = None
        info = _quarantine_and_maybe_restore(
            self._db_path, reason=label, backup_path=self._backup_path)
        self._last_quarantine_dir = info.get("quarantine_dir")
        if info.get("restored_from"):
            # Best-effort: bring the store back online on the restored backup so
            # a still-running process can keep appending instead of dying.
            try:
                self._reopen()
            except sqlite3.Error:
                pass
        raise AuditCorruptionError(
            f"audit store corruption detected ({label}) on {self._db_path}. "
            f"The faulty files were quarantined (NOT deleted) under "
            f"{info.get('quarantine_dir')!r} for forensics; "
            f"restored_from_backup={info.get('restored_from')!r}. "
            f"AuditCorruptionError means the triggering write was NOT applied "
            f"and was NOT silently retried."
        ) from exc

    def check_storage_health(self) -> bool:
        """Detect DB/WAL corruption WITHOUT writing.

        Runs ``PRAGMA integrity_check(1)``. Returns True on a healthy database.
        Raises :class:`AuditCorruptionError` if SQLite reports corruption or an
        I/O error -- either as an exception or as non-"ok" ``integrity_check``
        output -- so a corrupt store is surfaced as a CLEAR error rather than a
        raw ``sqlite3`` exception or a silent ``ok=True``.
        """
        try:
            rows = self._conn.execute("PRAGMA integrity_check(1)").fetchall()
        except sqlite3.DatabaseError as exc:
            code = getattr(exc, "sqlite_errorcode", None)
            if code in (_SQLITE_CORRUPT, _SQLITE_IOERR):
                raise AuditCorruptionError(
                    f"audit store corruption detected on {self._db_path} "
                    f"(sqlite code {code})."
                ) from exc
            raise
        if rows != [("ok",)]:
            raise AuditCorruptionError(
                f"audit store corruption detected on {self._db_path} "
                f"(PRAGMA integrity_check={rows!r})."
            )
        return True

    def _write_with_retry(self, attempt_fn, name=None, continuous_lock=False):
        """Run one write transaction, recovering from transient SQLite faults.

        Why this exists (measured, not guessed): with several processes opening
        the same database at once, SQLite intermittently answers
        SQLITE_READONLY ("attempt to write a readonly database", code 8) while
        the WAL shared-memory file is being created or torn down by a peer.
        Six concurrent processes hit it in 2-4 runs out of 12. It is NOT
        recoverable by retrying on the same connection -- the handle is
        poisoned -- so the retry re-opens it.

        Why retrying cannot duplicate evidence:
          * only BUSY / LOCKED / READONLY are retried, and SQLite raises all
            three while acquiring a lock or preparing a statement, i.e. before
            any row of this transaction has been applied;
          * the transaction is still rolled back first, so if it was open at
            all it is discarded whole;
          * therefore a retry re-runs a transaction that provably committed
            nothing -- it cannot create a duplicate event or burn a seq number.

        Any other failure (a data error, a disk error, a denied lease) still
        propagates on the first attempt, unchanged and fail-closed.

        LOCK HANDLING (F-05): the append lock is held for the duration of ONE
        attempt and for the connection clean-up that follows a retryable error,
        then RELEASED during the backoff sleep. Previously the lock was held for
        the whole backoff window, so a busy/locked storm froze every other
        writer -- and every governed action that needs the evidence channel --
        for the entire retry. Callers that must hold the lock continuously for
        the whole operation (the cumulative-link backfill, which must not let a
        concurrent append write a broken chain mid-flight) pass
        ``continuous_lock=True``; that preserves the invariant at the cost of
        blocking other writers during a (rare) backoff.
        """
        last: Optional[BaseException] = None
        label = name or getattr(attempt_fn, "__name__", "attempt")
        lock_held = False
        try:
            if continuous_lock:
                self._lock.acquire()
                lock_held = True
            for attempt in range(_MAX_WRITE_ATTEMPTS):
                if not continuous_lock:
                    self._lock.acquire()
                    lock_held = True
                try:
                    _t0 = time.perf_counter()
                    try:
                        result = attempt_fn()
                    finally:
                        _record_lock_hold(time.perf_counter() - _t0, label)
                    return result
                except sqlite3.OperationalError as exc:
                    code = getattr(exc, "sqlite_errorcode", None)
                    # Same extended-vs-primary trap as _init_db: 1546 is
                    # SQLITE_IOERR_* (primary 10). Unmasked, corruption handling
                    # below was skipped entirely.
                    if code is not None:
                        code &= 0xFF
                    if code not in _RETRYABLE_SQLITE_CODES:
                        raise
                    last = exc
                    # Connection clean-up happens WHILE the lock is held, so no
                    # other thread can touch self._conn mid-recovery.
                    try:
                        self._conn.rollback()
                    except Exception:  # noqa: BLE001 - best-effort cleanup
                        pass
                    self._writer_token = None
                    if code in (_SQLITE_CORRUPT, _SQLITE_IOERR):
                        # Careful handling: a corrupt / I/O-errored database must
                        # NOT be blind-retried into a worse state. Quarantine the
                        # evidence and raise a clear error instead of looping.
                        self._handle_storage_corruption(code, exc)
                    if code == _SQLITE_READONLY:
                        try:
                            self._reopen()
                        except sqlite3.Error:
                            pass  # the next attempt will try to re-open again
                finally:
                    if lock_held and not continuous_lock:
                        self._lock.release()
                        lock_held = False
                if attempt + 1 < _MAX_WRITE_ATTEMPTS:
                    time.sleep(
                        min(_WRITE_RETRY_BASE_SEC * (2 ** attempt),
                            _WRITE_RETRY_MAX_DELAY_SEC)
                    )
        finally:
            if lock_held:
                self._lock.release()
        # Exhausted the retry budget. FULL means "the disk is genuinely full":
        # we retried (in case space freed up) and now fail closed with a CLEAR
        # error so evidence is never silently dropped.
        code = getattr(last, "sqlite_errorcode", None) if last is not None else None
        if code is not None:
            code &= 0xFF  # primary code, see the mask note in _init_db
        if code == _SQLITE_FULL:
            raise AuditDiskFullError(
                "audit store write failed: the database or its disk is full "
                f"(SQLITE_FULL). No event was persisted, so the audit chain is "
                f"incomplete and this failure is reported fail-closed rather "
                f"than silently dropping evidence. db={self._db_path}"
            ) from last
        raise last

    def log_event(
        self,
        event_type: AuditEventType,
        principal_id: str,
        scope: AuditScope,
        outcome: str,
        details: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
    ) -> AuditEvent:
        """Log an audit event with hash-chain linkage.

        线程安全：审计 store 是全局单例，而 FastAPI 同步端点跑在线程池里，
        hash-chain 的 seq / prev_event_hash 必须串行推进，故整个写路径加锁。
        """
        # Bracket the request BEFORE taking the append lock: a thread waiting
        # behind a blocked writer is already "an audit write that has not
        # landed", which is exactly the backlog the health metric reports.
        token = _begin_audit_write()
        try:
            # 来源（本机 / 局域网手机）由网关入口的中间件写进 ContextVar，
            # 这里在落盘前合并。调用方显式给的字段优先，不被覆盖。
            merged = details
            try:
                from ...common.request_context import origin_details

                merged = {**origin_details(), **(details or {})}
            except Exception:  # noqa: BLE001 - 审计不能因为来源标注失败而丢事件
                merged = details
            event = self._log_event_locked(
                event_type, principal_id, scope, outcome, merged, correlation_id
            )
        except BaseException as exc:
            _end_audit_write(token, False, exc)
            raise
        else:
            _end_audit_write(token, True)
            self._maybe_prune()
            return event

    def _log_event_locked(
        self,
        event_type: AuditEventType,
        principal_id: str,
        scope: AuditScope,
        outcome: str,
        details: Optional[Dict[str, Any]],
        correlation_id: Optional[str],
    ) -> AuditEvent:
        try:
            return self._write_with_retry(
                lambda: self._attempt_log_event(
                    event_type, principal_id, scope, outcome, details, correlation_id
                ),
                name="_attempt_log_event",
            )
        except Exception:
            # CRIT-1C / D17 (Layer 1): do NOT swallow silently. Record the
            # failure so operators can observe "Evidence=missing" via
            # audit_stats() / /v1/ready, then re-raise so the *caller's* own
            # policy (allow-and-swallow for LOW, or block for critical) decides.
            record_audit_failure()
            raise

    def _append_hash_alg(self) -> str:
        """本次追加应使用的哈希算法（并在首次调用时判定能否生成密钥）。"""
        if self._keyed_history is None:
            try:
                row = self._conn.execute(
                    "SELECT 1 FROM audit_events WHERE hash_alg = ? LIMIT 1",
                    (_chain_key.HMAC_ALG,),
                ).fetchone()
                self._keyed_history = row is not None
            except sqlite3.Error:
                self._keyed_history = False
        alg = _chain_key.active_hash_alg(create=not self._keyed_history)
        if alg == _chain_key.HMAC_ALG:
            self._keyed_history = True
        return alg

    def _attempt_log_event(
        self,
        event_type: AuditEventType,
        principal_id: str,
        scope: AuditScope,
        outcome: str,
        details: Optional[Dict[str, Any]],
        correlation_id: Optional[str],
    ) -> AuditEvent:
        # 先决定本次追加用哪种哈希：生产默认带密钥（HMAC-SHA256）。密钥文件
        # 的首次生成要落盘，放在事务外做，避免在 BEGIN IMMEDIATE 里做文件 IO。
        hash_alg = self._append_hash_alg()
        if correlation_id is None:
            # Full width: 8 hex chars (32 bits) collide within any large
            # deployment, and a collision silently conflates unrelated events
            # in every correlation-based query.
            correlation_id = uuid.uuid4().hex

        timestamp = time.time()

        # U39 / ADR Option B: ONE atomic transaction for the whole
        # read-modify-write. Previously the SELECT of chain_state ran OUTSIDE
        # any transaction (only the INSERTs were implicitly wrapped), so two
        # writers could read the same last_seq and both append it -- the HC-01
        # fork (duplicate seq / broken joins). The process-lifetime lease used
        # to paper over that; now the transaction is the real guarantee and the
        # lease is an additional, explicit fence on top of it.
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                # Q3.5 / #99 fencing, now genuinely atomic with the append: take
                # the lease INSIDE the write transaction. A competing process
                # blocks on SQLite's write lock instead of being told the evidence
                # channel is unavailable; a genuinely fenced writer (a newer live
                # owner) is still refused immediately. #99 passes the process
                # writer_id / boot_gen / my_last_token so the uuid identity model
                # (opt-in via LIUHAO_AUDIT_LEASE_IDENTITY=uuid) can detect a
                # superseded token (FencedWriterError) and record forced
                # takeovers (writer_epoch).
                self._writer_token = self._lease.acquire_within(
                    owner=self._lease_owner(),
                    writer_id=_process_writer_id(),
                    boot_gen=_process_boot_gen(),
                    my_last_token=_get_last_token(),
                )
                _set_last_token(self._writer_token)
                require_writer_lease(self._lease, self._writer_token)

                # The chain_state anchor is the single source of truth for the
                # chain head: it assigns the next monotonic sequence number and
                # provides the previous event hash. Unlike timestamp ordering,
                # this is immune to clock granularity ties.
                state = self._conn.execute(
                    "SELECT last_seq, last_hash, last_link_hash "
                    "FROM chain_state WHERE id = 1"
                ).fetchone()
                if state:
                    last_seq, prev_hash = state[0], state[1]
                    prev_link = state[2] if len(state) > 2 else None
                else:
                    last_seq, prev_hash, prev_link = 0, None, None
                seq = last_seq + 1

                # Full 128-bit id, never a truncation. It used to be
                # str(uuid.uuid4())[:12] -- 48 bits, which by the birthday
                # bound collides with ~18% probability at 10^7 events. In the
                # batch path a collision is classified as a duplicate, so real
                # evidence would be dropped with no error at all.
                event_id = uuid.uuid4().hex
                event = AuditEvent(
                    event_id=event_id,
                    event_type=event_type,
                    principal_id=principal_id,
                    scope=scope,
                    timestamp=timestamp,
                    correlation_id=correlation_id,
                    outcome=outcome,
                    details=_redact_details(details or {}),
                    prev_event_hash=prev_hash,
                    hash_alg=hash_alg,
                )

                event.event_hash = event.compute_hash()
                # C2: cumulative commitment to the whole prefix. Computed and
                # stored in the SAME transaction as the event, so it can never
                # disagree with the row it summarises.
                event.link_hash = _verification.link_hash(
                    prev_link, event.event_hash, event.hash_alg)

                self._conn.execute(
                    """INSERT INTO audit_events
                       (event_id, event_type, principal_id, scope, timestamp,
                        correlation_id, outcome, details, event_hash, prev_event_hash,
                        seq, hash_alg, link_hash)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        event.event_id,
                        event.event_type.value,
                        event.principal_id,
                        event.scope.value,
                        event.timestamp,
                        event.correlation_id,
                        event.outcome,
                        json.dumps(event.details, sort_keys=True) if event.details else None,
                        event.event_hash,
                        event.prev_event_hash,
                        seq,
                        event.hash_alg,
                        event.link_hash,
                    ),
                )
                if event.hash_alg == _chain_key.HMAC_ALG:
                    # 记录密钥链起点（只在第一条带密钥事件时落一次）。没有
                    # 这条标记，攻击者把整链降级成 sha256 后再等一条正常带
                    # 密钥追加，就会把降级洗成“旧库迁移”并重新变绿。
                    self._conn.execute(
                        "INSERT OR IGNORE INTO chain_key_state "
                        "(id, enabled_from_seq) VALUES (1, ?)",
                        (seq,),
                    )
                self._conn.execute(
                    """INSERT INTO chain_state (id, last_seq, last_hash,
                        last_link_hash) VALUES (1, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET
                           last_seq = excluded.last_seq,
                           last_hash = excluded.last_hash,
                           last_link_hash = excluded.last_link_hash""",
                    (seq, event.event_hash, event.link_hash),
                )
                # Per-append lease (U39): yield it in the SAME transaction, so
                # the moment this append commits another process may take over.
                # Rolling back the append also rolls back the yield -- the two
                # can never disagree.
                self._lease.release_within(self._writer_token)
                self._conn.commit()
                # Keep the process-global last token: after a successful release
                # the row still shows OUR writer_id, so the next append renews
                # instead of fabricating a false fence. Never reset it (a reset
                # would make a reopen look like a superseded token).
                self._writer_token = None
            except Exception:
                self._conn.rollback()
                self._writer_token = None
                raise
        except Exception:
            # Belt and braces: _write_with_retry() also rolls back before it
            # retries, but the connection must never be left mid-transaction
            # on the way out.
            try:
                self._conn.rollback()
            except Exception:  # noqa: BLE001 - the handle may already be dead
                pass
            self._writer_token = None
            raise

        return event

    def log_event_batch(self, events: List[AuditEvent]) -> "AuditBatchResult":
        """Append N events in ONE atomic transaction (Option C1).

        This exists because measurement showed the append ceiling is
        per-transaction cost, not hashing: single appends run ~2k/sec while a
        batch of 250 runs ~23k/sec (see ADR-audit-single-writer-lease.md §8).

        Semantics -- every one of these is deliberate and tested:

        * **Ordering** -- events are appended in list order; ``seq`` increases by
          exactly one per appended event, and ``prev_event_hash`` chains through
          the batch and onto the previously committed tail. Order within a batch
          is therefore the caller's list order, which is the only defensible
          definition for a scheduler-independent pipeline.
        * **Atomicity** -- all-or-nothing. "Committed" means every non-duplicate
          event in the batch is durable. A crash mid-batch rolls the whole batch
          back: never a partial batch, never a gap in ``seq``.
        * **Idempotency** -- a caller-supplied ``event_id`` that is already
          committed is skipped: it consumes no seq and creates no duplicate.
          This is what makes retry-after-uncertain-outcome safe, and it is the
          reason a batched pipeline does not silently duplicate evidence.
        * **Fencing** -- the lease is taken once per batch, inside the same
          transaction, so a fenced writer is still refused.
        * **Fail-closed** -- if the batch cannot commit the error propagates and
          the mandatory-evidence gate denies the governed action.

        Returns :class:`AuditBatchResult`.
        """
        if not events:
            return AuditBatchResult(appended=[], duplicates=[])

        token = _begin_audit_write()
        try:
            result = self._log_event_batch_locked(events)
        except BaseException as exc:
            _end_audit_write(token, False, exc)
            raise
        else:
            _end_audit_write(token, True)
            self._maybe_prune()
            return result

    def _log_event_batch_locked(self, events: List[AuditEvent]) -> "AuditBatchResult":
        try:
            return self._write_with_retry(
                lambda: self._attempt_log_event_batch(events),
                name="_attempt_log_event_batch",
            )
        except Exception:
            # Same fail-closed contract as the single append (CRIT-1C / D17).
            record_audit_failure()
            raise

    def _attempt_log_event_batch(self, events: List[AuditEvent]) -> "AuditBatchResult":
        conn = self._conn
        # 同一批事件必须使用同一种算法；密钥首次生成在事务外完成。
        batch_alg = self._append_hash_alg()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._writer_token = self._lease.acquire_within(
                    owner=self._lease_owner(),
                    writer_id=_process_writer_id(),
                    boot_gen=_process_boot_gen(),
                    my_last_token=_get_last_token(),
                )
                _set_last_token(self._writer_token)
                require_writer_lease(self._lease, self._writer_token)

                state = conn.execute(
                    "SELECT last_seq, last_hash, last_link_hash "
                    "FROM chain_state WHERE id = 1"
                ).fetchone()
                if state:
                    last_seq, prev_hash = state[0], state[1]
                    prev_link = state[2] if len(state) > 2 else None
                else:
                    last_seq, prev_hash, prev_link = 0, None, None

                # Idempotency: which of these event_ids are already committed?
                existing: set = set()
                ids = [e.event_id for e in events if e.event_id]
                for start in range(0, len(ids), 500):
                    chunk = ids[start:start + 500]
                    placeholders = ",".join("?" * len(chunk))
                    rows = conn.execute(
                        f"SELECT event_id FROM audit_events "
                        f"WHERE event_id IN ({placeholders})",
                        chunk,
                    ).fetchall()
                    existing.update(row[0] for row in rows)

                appended: List[AuditEvent] = []
                duplicates: List[str] = []
                seen: set = set()
                # 密钥链起点标记整批只写一次。逐事件 INSERT OR IGNORE 会让每
                # 条带密钥事件都多一次 B-tree 查找，10 万条实测多付 ~30% 耗时。
                epoch_marked = conn.execute(
                    "SELECT 1 FROM chain_key_state WHERE id = 1"
                ).fetchone() is not None

                for event in events:
                    if not event.event_id:
                        event.event_id = uuid.uuid4().hex
                    # Same defaulting rule as the single append: a caller that
                    # omits correlation_id gets a fresh one, instead of being
                    # rejected by the NOT NULL column.
                    if not event.correlation_id:
                        event.correlation_id = uuid.uuid4().hex
                    if event.event_id in existing or event.event_id in seen:
                        duplicates.append(event.event_id)
                        continue
                    seen.add(event.event_id)

                    last_seq += 1
                    event.prev_event_hash = prev_hash
                    # 只给“没指定算法”的事件套用本批算法；调用方显式写入的
                    # 算法保持原样（未知算法仍会在 compute_hash 处报错并整批
                    # 回滚——批量追加必须是全有或全无）。
                    if event.hash_alg in (None, DEFAULT_HASH_ALG):
                        event.hash_alg = batch_alg
                    event.event_hash = event.compute_hash()
                    event.link_hash = _verification.link_hash(
                        prev_link, event.event_hash, event.hash_alg)

                    conn.execute(
                        """INSERT INTO audit_events
                           (event_id, event_type, principal_id, scope, timestamp,
                            correlation_id, outcome, details, event_hash,
                            prev_event_hash, seq, hash_alg, link_hash)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            event.event_id,
                            event.event_type.value,
                            event.principal_id,
                            event.scope.value,
                            event.timestamp,
                            event.correlation_id,
                            event.outcome,
                            json.dumps(event.details, sort_keys=True)
                            if event.details else None,
                            event.event_hash,
                            event.prev_event_hash,
                            last_seq,
                            event.hash_alg,
                            event.link_hash,
                        ),
                    )
                    if event.hash_alg == _chain_key.HMAC_ALG and not epoch_marked:
                        # 同单条路径：记录密钥链起点，防止“整链降级后再等
                        # 一条正常追加”把降级洗成旧库迁移。
                        conn.execute(
                            "INSERT OR IGNORE INTO chain_key_state "
                            "(id, enabled_from_seq) VALUES (1, ?)",
                            (last_seq,),
                        )
                        epoch_marked = True
                    prev_hash = event.event_hash
                    prev_link = event.link_hash
                    appended.append(event)

                if appended:
                    conn.execute(
                        """INSERT INTO chain_state (id, last_seq, last_hash,
                            last_link_hash)
                           VALUES (1, ?, ?, ?)
                           ON CONFLICT(id) DO UPDATE SET
                               last_seq = excluded.last_seq,
                               last_hash = excluded.last_hash,
                               last_link_hash = excluded.last_link_hash""",
                        (last_seq, prev_hash, prev_link),
                    )
                self._lease.release_within(self._writer_token)
                conn.commit()
                self._writer_token = None
            except Exception:
                conn.rollback()
                self._writer_token = None
                raise
        except Exception:
            # Same belt-and-braces guarantee as the single-append path.
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001 - the handle may already be dead
                pass
            self._writer_token = None
            raise

        return AuditBatchResult(appended=appended, duplicates=duplicates)

    # ------------------------------------------------------------------ #
    # Retention (bounded storage, still-verifiable retained chain)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _retention_state(conn) -> Tuple[int, Optional[str], Optional[str]]:
        """The retained floor: (pruned_through_seq, hash, link_hash)."""
        try:
            row = conn.execute(
                "SELECT pruned_through_seq, pruned_through_hash, "
                "pruned_through_link_hash FROM retention_state WHERE id = 1"
            ).fetchone()
        except sqlite3.Error:
            # A database created before retention existed: floor is genesis.
            return 0, None, None
        if row is None:
            return 0, None, None
        return int(row[0] or 0), row[1], row[2]

    def _audit_disk_bytes(self) -> int:
        """Bytes currently held by the audit DB family (db + wal + shm)."""
        total = 0
        for suffix in ("", "-wal", "-shm"):
            try:
                total += os.path.getsize(self._db_path + suffix)
            except OSError:
                pass
        return total

    def _retention_plan(self, conn) -> Tuple[int, int, int]:
        """Compute one bounded prune pass: (cut_seq, floor_seq, tail_seq).

        Read-only. ``cut_seq == floor_seq`` means nothing is due this pass.
        The pass is bounded in TWO independent ways so an append is never
        stalled by a multi-second DELETE:
          * the newest ``min_keep`` events are untouchable;
          * at most ``max_delete`` rows are removed per pass.
        """
        cfg = self._retention
        floor, _hash, _link = self._retention_state(conn)
        tail = _verification.tail_seq(conn)
        if tail <= floor:
            return floor, floor, tail
        cut = floor
        retained = int(conn.execute(
            "SELECT COUNT(*) FROM audit_events").fetchone()[0])
        if cfg["max_rows"] and retained > cfg["max_rows"]:
            cut = max(cut, floor + (retained - cfg["max_rows"]))
        if cfg["days"]:
            # 用防跳变时钟：系统时间被前跳（手动改时间/VM 恢复）时不能把
            # 仍在保留期内的审计证据一次性当"过期"删掉。
            age_cutoff = stable_now_epoch() - cfg["days"] * 86400
            row = conn.execute(
                "SELECT MAX(seq) FROM audit_events WHERE timestamp < ?",
                (age_cutoff,)).fetchone()
            if row and row[0]:
                cut = max(cut, int(row[0]))
        if cfg["max_bytes"]:
            # Free pages are ALREADY reclaimed space, so only live bytes count
            # toward the ceiling -- otherwise every pass would keep deleting
            # while the file itself no longer grows.
            page = int(conn.execute("PRAGMA page_size").fetchone()[0] or 4096)
            free_pages = int(
                conn.execute("PRAGMA freelist_count").fetchone()[0] or 0)
            live = max(0, self._audit_disk_bytes() - free_pages * page)
            if live > cfg["max_bytes"]:
                avg = max(1, live // max(1, retained))
                over = (live - cfg["max_bytes"] + avg - 1) // avg
                cut = max(cut, floor + over)
        keep_from = tail - cfg["min_keep"]
        cut = min(cut, max(floor, keep_from))
        cut = min(cut, floor + cfg["max_delete"])
        return cut, floor, tail

    def _maybe_prune(self) -> None:
        """Throttled retention + storage maintenance (never fails writes)."""
        cfg = self._retention
        now = time.time()
        if now - self._retention_last_check < cfg["interval"]:
            return
        self._retention_last_check = now
        if cfg["enabled"]:
            try:
                cut, floor, _tail = self._read_only(self._retention_plan)
            except Exception:  # noqa: BLE001 - retention must not break appends
                logger.warning(
                    "audit retention planning failed on %s; evidence retained",
                    self._db_path, exc_info=True)
                cut = floor = 0
            if cut > floor:
                try:
                    self._prune_once()
                except Exception:  # noqa: BLE001 - already-logged evidence stays
                    logger.warning(
                        "audit retention prune failed on %s; evidence retained",
                        self._db_path, exc_info=True)
        # Storage maintenance runs even when retention is disabled: an
        # append-only store still leaves a growing ``-wal`` and a legacy DB still
        # needs its one-time auto_vacuum migration.
        self._maybe_maintain_storage()

    # -- storage reclamation helpers ----------------------------------------
    def _wal_bytes(self) -> int:
        """Current size of the ``-wal`` sidecar (0 when it does not exist)."""
        try:
            return os.path.getsize(self._db_path + "-wal")
        except OSError:
            return 0

    def _wal_truncate(self) -> None:
        """Best-effort ``wal_checkpoint(TRUNCATE)`` that never blocks writers.

        ``busy_timeout`` is temporarily zeroed so a concurrently held read
        snapshot makes the checkpoint return "busy" immediately instead of
        stalling the append path for the full 30s timeout. The previous value
        is restored in ``finally``.
        """
        try:
            previous = int(
                self._conn.execute("PRAGMA busy_timeout").fetchone()[0])
        except (sqlite3.Error, TypeError, IndexError):
            previous = None
        try:
            self._conn.execute("PRAGMA busy_timeout=0")
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass
        finally:
            if previous is not None:
                try:
                    self._conn.execute(f"PRAGMA busy_timeout={previous}")
                except sqlite3.Error:
                    pass

    def _migrate_auto_vacuum(self) -> bool:
        """One-time legacy migration: ``auto_vacuum`` NONE -> INCREMENTAL.

        SQLite cannot change auto_vacuum online on a non-empty database; it
        requires setting the pragma and then a full ``VACUUM``. Timing: the
        first maintenance tick after open (not the open path, not a request),
        outside any transaction. Failure handling: this never raises into the
        append path; it backs off ``retry_sec`` (e.g. disk full, DB busy) and
        logs one warning instead of retrying every write.
        """
        if not self._auto_vacuum_pending:
            return True
        cfg = self._storage
        if not cfg["migrate"]:
            return False
        now = time.time()
        if now < self._auto_vacuum_next_attempt:
            return False
        # A VACUUM needs roughly the DB size in temporary space; do not
        # surprise an operator with a multi-GB rewrite on an oversized store.
        size = self._audit_disk_bytes()
        if size > cfg["max_migrate_bytes"]:
            self._auto_vacuum_next_attempt = now + cfg["retry_sec"]
            if not self._auto_vacuum_warned:
                logger.warning(
                    "audit db %s is %d MB with auto_vacuum=NONE; automatic "
                    "reclamation stays off until an operator runs VACUUM "
                    "manually or raises LIUHAO_AUDIT_AUTO_VACUUM_MAX_MIGRATE_BYTES",
                    self._db_path, size // (1024 * 1024))
                self._auto_vacuum_warned = True
            return False
        with self._lock:
            if self._conn.in_transaction:
                # Cannot VACUUM inside an open transaction; retry next tick.
                return False
            try:
                # Truncate the WAL first so VACUUM does not copy a large one.
                self._wal_truncate()
                self._conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
                self._conn.execute("VACUUM")
            except Exception:  # noqa: BLE001 - storage maintenance is best-effort
                self._auto_vacuum_next_attempt = now + cfg["retry_sec"]
                if not self._auto_vacuum_warned:
                    logger.warning(
                        "audit db %s auto_vacuum migration (VACUUM) failed; "
                        "freed pages will not be returned until it succeeds "
                        "(retrying in %ds)", self._db_path, int(cfg["retry_sec"]),
                        exc_info=True)
                    self._auto_vacuum_warned = True
                return False
            finally:
                # VACUUM writes new pages through the WAL; truncate again so the
                # migration itself leaves no residual -wal behind.
                self._wal_truncate()
        if self._read_auto_vacuum() == _AUTO_VACUUM_INCREMENTAL:
            self._auto_vacuum_pending = False
            logger.info(
                "audit db %s migrated to incremental auto-vacuum", self._db_path)
            return True
        # VACUUM reported success but the header did not change: retry later
        # rather than pretending this store can reclaim.
        self._auto_vacuum_next_attempt = now + cfg["retry_sec"]
        return False

    def _reclaim_freed_pages(self) -> None:
        """Hand freed pages back to the filesystem and truncate the WAL."""
        with self._lock:
            self._migrate_auto_vacuum()
            try:
                if not self._conn.in_transaction:
                    # No-op if auto_vacuum is still NONE (legacy migration
                    # failed): the file then stops growing instead of shrinking.
                    # NB: without a page argument SQLite returns one row per
                    # freed page and performs the work lazily while stepping,
                    # so the result MUST be drained (a bare execute() would
                    # reclaim exactly one page and leave the rest in the
                    # freelist).
                    self._conn.execute("PRAGMA incremental_vacuum").fetchall()
            except sqlite3.Error:
                pass
            self._wal_truncate()

    def _maybe_maintain_storage(self) -> None:
        """Periodic bounded reclamation (throttled by the retention interval)."""
        try:
            with self._lock:
                if self._conn.in_transaction:
                    return
                free_pages = int(
                    self._conn.execute(
                        "PRAGMA freelist_count").fetchone()[0] or 0)
                # A legacy DB is only rewritten once there is something to
                # reclaim; this keeps an oversized store from being VACUUMed on
                # a plain append when nothing was ever deleted.
                if self._auto_vacuum_pending and free_pages:
                    self._migrate_auto_vacuum()
                if free_pages:
                    try:
                        self._conn.execute(
                            "PRAGMA incremental_vacuum").fetchall()
                    except sqlite3.Error:
                        pass
                if free_pages or (
                        self._wal_bytes()
                        >= self._storage["wal_truncate_min_bytes"]):
                    self._wal_truncate()
        except Exception:  # noqa: BLE001 - maintenance must never fail appends
            logger.warning(
                "audit storage maintenance failed on %s", self._db_path,
                exc_info=True)

    def _prune_once(self) -> None:
        """Delete the oldest prefix (bounded) and record the retention floor.

        Runs under the append lock in its OWN transaction so the floor row and
        the DELETE can never disagree: a crash either leaves the prefix intact
        or leaves both the prefix and its floor anchor removed.
        """
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                try:
                    cut, floor, _tail = self._retention_plan(self._conn)
                    if cut <= floor:
                        self._conn.commit()
                        return
                    anchor = self._conn.execute(
                        "SELECT event_hash, link_hash FROM audit_events "
                        "WHERE seq = ?", (cut,)).fetchone()
                    if anchor is None:
                        self._conn.rollback()
                        return
                    deleted = self._conn.execute(
                        "DELETE FROM audit_events WHERE seq <= ?",
                        (cut,)).rowcount
                    # Checkpoints are DERIVED data. Once a prefix is gone they
                    # can no longer be recomputed, so they must not masquerade
                    # as still-verifiable coverage.
                    self._conn.execute(
                        "DELETE FROM verification_checkpoints "
                        "WHERE start_seq <= ?", (cut,))
                    self._conn.execute(
                        """UPDATE retention_state SET
                               pruned_through_seq = ?,
                               pruned_through_hash = ?,
                               pruned_through_link_hash = ?,
                               pruned_events = pruned_events + ?,
                               pruned_at = ?
                           WHERE id = 1""",
                        (cut, anchor[0], anchor[1], int(deleted or 0),
                         time.time()))
                    self._conn.commit()
                except Exception:
                    try:
                        self._conn.rollback()
                    except Exception:  # noqa: BLE001
                        pass
                    raise
            except Exception:
                raise
            # Hand freed pages back to the filesystem and truncate the WAL.
            # On a legacy DB whose one-time VACUUM migration has not succeeded
            # yet, incremental_vacuum is a no-op: the file then stops growing
            # instead of shrinking (never silently claims reclaimed space).
            self._reclaim_freed_pages()

    def verify_integrity(self) -> Tuple[bool, int]:
        """Verify the integrity of the audit event hash chain.

        Checks performed per Definition Lock section 113:
        1. Chain linkage: each event's prev_event_hash equals the
           previous event's event_hash (first event has none).
        2. Sequence continuity: seq numbers increment by exactly one.
        3. Content integrity: each event's stored hash is recomputed
           from its stored fields (outcome, details, ...) and compared,
           so tampering with content is detected.
        4. Tail integrity: the chain_state anchor (last seq/hash) must
           match the last stored event, so deleting the newest events
           is detected.

        Returns:
            (is_integrity_ok, total_events)
        """
        # Verification runs on a DEDICATED read-only snapshot connection, NOT
        # under the append lock.
        #
        # It used to hold self._lock -- the same lock every append needs -- for
        # the whole scan, which is a full fetchall() at ~23 us/row (measured).
        # On the production-sized store (317,383 rows) that is ~7-8 seconds in
        # which no HIGH/CRITICAL governed action can be recorded, because the
        # mandatory-evidence path needs that lock; at ten million rows it is
        # minutes. Reading a committed snapshot instead costs nothing and is
        # what an auditor actually wants: a consistent point-in-time view.
        conn = self._open_snapshot()
        if conn is None:
            # A read-only handle cannot recover a WAL that needs recovery, so
            # fall back to the shared connection (correct, but blocking).
            with self._lock:
                return self._verify_integrity_on(self._conn)
        try:
            return self._verify_integrity_on(conn)
        except sqlite3.DatabaseError as exc:
            # A corrupt / I/O-errored DB must surface as a CLEAR error, not a raw
            # sqlite3 exception and not a silent "ok". This is the read-only
            # mirror of the write-path corruption handling: detected, reported,
            # and (crucially) not retried in a loop.
            code = getattr(exc, "sqlite_errorcode", None)
            if code in (_SQLITE_CORRUPT, _SQLITE_IOERR):
                raise AuditCorruptionError(
                    f"audit store corruption detected during verify_integrity "
                    f"on {self._db_path} (sqlite code {code})."
                ) from exc
            raise
        finally:
            conn.close()

    def chain_key_profile(self) -> Dict[str, Any]:
        """当前密钥状态 + 是否存在带密钥事件（不含密钥本身）。

        这是“恢复后到底能不能校验”的唯一判据：库里有 HMAC 事件、本机却
        没有对应密钥 -> ``degraded=True``，只能做结构检查。
        """
        key_status = _chain_key.status()
        key_mode = _chain_key.key_mode()
        keyed = 0
        total = 0
        max_seq = 0
        first_keyed_seq = 0
        last_unkeyed_seq = 0
        key_epoch: Optional[int] = None
        unkeyed_after_epoch = 0
        conn = self._open_snapshot()
        try:
            handle = conn if conn is not None else self._conn
            row = handle.execute(
                "SELECT COUNT(*), "
                "COALESCE(SUM(CASE WHEN hash_alg = ? THEN 1 ELSE 0 END), 0), "
                "COALESCE(MIN(CASE WHEN hash_alg = ? THEN seq END), 0), "
                "COALESCE(MAX(CASE WHEN hash_alg <> ? THEN seq END), 0), "
                "COALESCE(MAX(seq), 0) "
                "FROM audit_events",
                (_chain_key.HMAC_ALG, _chain_key.HMAC_ALG, _chain_key.HMAC_ALG),
            ).fetchone()
            if row:
                total, keyed = int(row[0]), int(row[1])
                first_keyed_seq = int(row[2] or 0)
                last_unkeyed_seq = int(row[3] or 0)
                max_seq = int(row[4] or 0)
            epoch_row = handle.execute(
                "SELECT enabled_from_seq FROM chain_key_state WHERE id = 1"
            ).fetchone()
            if epoch_row:
                key_epoch = int(epoch_row[0])
                unkeyed_after_epoch = int(handle.execute(
                    "SELECT COUNT(*) FROM audit_events "
                    "WHERE seq >= ? AND hash_alg <> ?",
                    (key_epoch, _chain_key.HMAC_ALG),
                ).fetchone()[0] or 0)
        except sqlite3.Error:
            keyed = 0
            total = 0
        finally:
            if conn is not None:
                conn.close()

        # ``hash_alg`` 不在签名负载里（见 hashutil.event_payload），所以拿到库
        # 写权限的人可以把整条链（或密钥链之后的部分）翻成 ``sha256`` 再重算，
        # 无密钥校验依然全绿。两种情况都必须暴露成“无法用密钥证明”，而不是
        # 绿灯：``keyed == 0`` 是整链降级/旧库；密钥链之后出现无密钥行是局部
        # 降级（后者也可能是运维显式关掉密钥模式，因此同样按“降级”而非“篡改”
        # 报告，颜色是琥珀而不是红）。
        unkeyed_after_keyed = (
            keyed > 0 and last_unkeyed_seq > first_keyed_seq
        )
        # 起点标记是“降级后再等一条正常追加就洗白”的补丁：标记之后必须全是
        # 带密钥行，否则就是降级（哪怕现在有新的带密钥尾巴把 keyed>0 凑出来）。
        epoch_breached = key_epoch is not None and (
            unkeyed_after_epoch > 0
            or (keyed == 0 and max_seq >= key_epoch)
        )
        missing_epoch = keyed > 0 and key_epoch is None
        reason = None
        message = None
        if keyed and key_status in ("missing", "unreadable"):
            reason = "chain_key_missing" if key_status == "missing" else "chain_key_unreadable"
            message = "缺少校验密钥，完整性只能做结构检查"
        elif epoch_breached:
            reason = "chain_key_downgraded"
            message = (
                "密钥链起点之后出现了无密钥记录，完整性无法用密钥证明"
                "（可能被降级重算）"
            )
        elif missing_epoch:
            reason = "chain_key_downgraded"
            message = (
                "带密钥记录缺少起点标记，完整性无法用密钥证明"
                "（标记可能被删除）"
            )
        elif unkeyed_after_keyed:
            reason = "chain_key_downgraded"
            message = (
                "密钥链之后出现了无密钥记录，完整性无法用密钥证明"
                "（可能被降级重算）"
            )
        elif total and not keyed and key_mode == "on":
            # 密钥模式开着，库里却一条 HMAC 事件都没有：可能是旧库、被整链降级，
            # 也可能是本机密钥根本不可用（写入时建不出来）。三种都不能给绿灯。
            if key_status == "available":
                reason = "chain_key_downgraded"
                message = (
                    "库中没有带密钥的记录，完整性无法用密钥证明"
                    "（可能是旧库或被降级）"
                )
            else:
                reason = (
                    "chain_key_missing" if key_status == "missing"
                    else "chain_key_unreadable"
                )
                message = "缺少校验密钥，完整性只能做结构检查"

        # 第三种（非降级）状态：旧版本写入的无密钥前缀 + 升级后的带密钥尾巴。
        # 这正是所有 0.1.0 老用户升级后的真实形态：尾巴能用密钥证明，前缀只能
        # 做结构检查（拿到写权限的人可以重算前缀并顺手改掉边界 link_hash，密钥
        # 保护不到它）。既不能说“完好”，也不能说“损坏”。
        partial = reason is None and keyed > 0 and keyed < total
        partial_message = None
        if partial:
            partial_message = (
                f"部分可证明：前 {total - keyed} 条是升级前写入的无密钥记录，"
                f"只能做结构检查；从第 {first_keyed_seq} 条起可用密钥证明未被改写"
            )

        return {
            "status": key_status,
            "mode": key_mode,
            "keyed_events": keyed,
            "total_events": total,
            "key_epoch_seq": key_epoch,
            "degraded": reason is not None,
            "degraded_reason": reason,
            "message": message,
            "partial": partial,
            "partial_message": partial_message,
        }

    def verify_structure_only(self) -> Tuple[bool, int]:
        """只查结构（序号、prev 链接、尾锚点），不重算密码学哈希。

        仅在密钥缺失时使用：明确告诉调用方“这不是完整校验”。
        """
        conn = self._open_snapshot()
        if conn is None:
            with self._lock:
                return self._verify_integrity_on(self._conn, skip_crypto=True)
        try:
            return self._verify_integrity_on(conn, skip_crypto=True)
        finally:
            conn.close()

    def retention_policy(self) -> Dict[str, Any]:
        """当前生效的保留策略（供界面/文档如实展示，不猜默认值）。"""
        cfg = dict(self._retention)
        cfg["interval"] = int(cfg.get("interval") or 0)
        return cfg

    def duplicate_seq_count(self) -> int:
        """Count rows that share a ``seq`` with another row (RCA-1 fork metric).

        A healthy audit chain has every ``seq`` unique, so this count MUST be
        zero. The figure returned is ``total_rows - distinct_seqs`` -- the
        number of "extra" rows beyond the first occurrence of each seq -- which
        is exactly what :meth:`_verify_integrity_on` increments in its explicit
        fork detection (``dup_seq``).

        This is an independent, precise signal used by the fail-closed live-DB
        no-fork monitor (F6) and by tests that inject a forked chain. It runs on
        a dedicated read-only snapshot and never modifies the store.
        """
        def _count(conn) -> int:
            row = conn.execute(
                "SELECT COUNT(*) - COUNT(DISTINCT seq) FROM audit_events"
            ).fetchone()
            return int(row[0]) if row else 0

        conn = self._open_snapshot()
        if conn is None:
            with self._lock:
                return _count(self._conn)
        try:
            return _count(conn)
        finally:
            conn.close()

    def _open_snapshot(self) -> Optional[sqlite3.Connection]:
        """Open a private read-only connection, or None if that is impossible.

        The connection is returned with ONE read snapshot already pinned, so
        every read a caller performs through it -- tail seq, event rows,
        checkpoints, coverage -- comes from the same consistent point in time.
        Pinning here rather than per-caller is deliberate: the read entry
        points (verify_integrity / verify_segment / verify_incremental /
        audit_checkpoints / recompute_checkpoint / verification_coverage) each
        issue several SELECTs, and without a pinned snapshot each SELECT gets
        its own -- which is how a healthy chain gets reported broken.
        """
        try:
            uri = pathlib.Path(os.path.abspath(self._db_path)).as_uri() + "?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=_SQLITE_BUSY_TIMEOUT_SEC)
            conn.execute("PRAGMA query_only = ON")
            try:
                conn.execute("BEGIN DEFERRED")
            except sqlite3.Error:
                # Degrade to the old per-statement behaviour rather than
                # refusing to read at all; closing still cleans up.
                pass
            return conn
        except (sqlite3.Error, ValueError):
            return None

    def _read_only(self, fn):
        """Run ``fn(conn)`` against a private pinned snapshot.

        Every read path should come through here. Reading straight off
        ``self._conn`` is not a shortcut, it is a different (and wrong) answer:
        that is the WRITE connection, so a reader can observe rows from a
        transaction that has not committed -- and in an audit store, observing
        an event that is later rolled back is a false statement about history.
        The snapshot is pinned, so multiple SELECTs inside ``fn`` agree.

        Falls back to the shared connection under the append lock only when a
        read-only handle cannot be opened at all (e.g. a WAL that needs
        recovery) -- correct, but it blocks appends, hence last resort.
        """
        conn = self._open_snapshot()
        if conn is None:
            with self._lock:
                return fn(self._conn)
        try:
            return fn(conn)
        finally:
            conn.close()

    @staticmethod
    def _begin_read_transaction(conn) -> bool:
        """Pin one read snapshot. True if WE opened it and must close it.

        Written as raw SQL rather than relying on the driver's implicit
        transaction handling, because python's sqlite3 in legacy autocommit
        mode only opens a transaction for DML -- two bare SELECTs would get
        two different snapshots, which is exactly the bug this prevents.
        """
        try:
            if getattr(conn, "in_transaction", False):
                return False
            conn.execute("BEGIN DEFERRED")
            return True
        except sqlite3.Error:
            # A read-only or already-transactional connection is a legitimate
            # state; failing to pin the snapshot degrades to the old behaviour
            # rather than breaking verification outright.
            return False

    @staticmethod
    def _end_read_transaction(conn) -> None:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass

    def _verify_integrity_locked(self) -> Tuple[bool, int]:
        """Deprecated: kept for callers that already hold the append lock."""
        return self._verify_integrity_on(self._conn)

    @staticmethod
    def _verify_integrity_on(
        conn, *, skip_crypto: bool = False
    ) -> Tuple[bool, int]:
        """Verify the chain over an explicit connection. See verify_chain_integrity.

        Declared a ``staticmethod`` deliberately: it never touches instance
        state, so it can be invoked as ``AuditStore._verify_integrity_on(conn)``
        by callers that must NOT construct an AuditStore (constructing one runs
        ``_init_db``, which issues and COMMITS DDL -- see
        :func:`verify_chain_integrity`). Existing ``self._verify_integrity_on(...)``
        call sites keep working unchanged.
        """
        # The event scan and the tail anchor MUST be read from one read
        # snapshot. Without an explicit transaction, SQLite gives each SELECT
        # its own snapshot, so an append committing between the two makes the
        # scan end at seq N while the anchor already says N+1 -- a perfectly
        # healthy chain reported as broken, which under the mandatory-evidence
        # fail-closed gate denies every HIGH/CRITICAL action.
        began = AuditStore._begin_read_transaction(conn)
        try:
            rows = conn.execute(
                "SELECT seq, event_id, event_type, principal_id, scope, "
                "timestamp, correlation_id, outcome, details, event_hash, "
                "prev_event_hash, hash_alg, link_hash FROM audit_events "
                "ORDER BY seq ASC, rowid ASC"
            ).fetchall()

            state = conn.execute(
                "SELECT last_seq, last_hash, last_link_hash FROM chain_state "
                "WHERE id = 1"
            ).fetchone()
            try:
                retention = conn.execute(
                    "SELECT pruned_through_seq, pruned_through_hash, "
                    "pruned_through_link_hash FROM retention_state WHERE id = 1"
                ).fetchone()
            except sqlite3.Error:
                # Databases created before retention existed have no row.
                retention = None
        finally:
            if began:
                AuditStore._end_read_transaction(conn)

        floor = int(retention[0] or 0) if retention else 0
        floor_hash = retention[1] if retention else None
        floor_link = retention[2] if retention else None

        if not rows:
            # Empty log: valid only if the anchor agrees that nothing was
            # ever logged (or points at zero events). With a retention floor
            # it must agree that everything UP TO THE FLOOR was pruned.
            if floor > 0:
                if state is None:
                    return False, 0
                anchor_link = state[2] if len(state) > 2 else None
                return (
                    int(state[0]) == floor
                    and state[1] == floor_hash
                    and anchor_link == floor_link
                ), 0
            if state is not None and state[0] > 0:
                return False, 0
            return True, 0

        total = len(rows)
        broken = 0
        dup_seq = 0
        # The retained suffix accumulates from the retention anchor, not from
        # a fabricated None: event P+1's link_hash was computed over link(P).
        running_link: Optional[str] = floor_link

        for i, row in enumerate(rows):
            (seq, event_id, event_type, principal_id, scope, timestamp,
             correlation_id, outcome, details_json, event_hash,
             prev_event_hash, row_hash_alg, stored_link) = row

            # 1. Chain linkage to the previous event
            if i == 0:
                # The first retained event must link to the retention floor
                # (prev=None only when nothing was pruned) and its seq must be
                # exactly floor+1 -- otherwise a whole prefix was silently
                # dropped without a floor record.
                if seq != floor + 1:
                    broken += 1
                if prev_event_hash != floor_hash:
                    broken += 1
            else:
                prev_row = rows[i - 1]
                # Subsequent events must reference previous event's hash
                if prev_event_hash != prev_row[9]:
                    broken += 1
                # 2. Sequence numbers must be contiguous AND unique.
                # A duplicate seq is the RCA-1 fork signature: concurrent
                # writers assigned the same sequence number because seq
                # allocation was not atomic under a single-writer fence. Detect
                # it explicitly and count it on its own branch so a recurrence
                # is visible and the broken count is deterministic (the scan is
                # now ordered by ``seq ASC, rowid ASC`` -- a stable tie-break),
                # rather than being silently folded into the contiguity check.
                # A gap (seq skipping a value) is a different failure and is
                # counted on its own branch below.
                if seq == prev_row[0]:
                    dup_seq += 1
                    broken += 1
                    logger.error(
                        "audit chain fork signature: duplicate seq=%s "
                        "(event_id=%s) -- RCA-1 concurrency defect",
                        seq, event_id,
                    )
                elif seq != prev_row[0] + 1:
                    broken += 1

            # 3. Content integrity: recompute the canonical hash from
            # the stored fields and compare with the stored hash.
            # P0-8c: NO default fallback. A row that declares no algorithm is
            # UNVERIFIED -- it is counted broken rather than silently being
            # read as the default algorithm.
            declared_alg = row_hash_alg
            if not skip_crypto:
                if not declared_alg:
                    logger.error(
                        "audit event %s carries no hash_alg declaration; this row "
                        "is UNVERIFIED and is counted as broken rather than "
                        "assuming %s", event_id, DEFAULT_HASH_ALG,
                    )
                    broken += 1
                    continue
                if declared_alg not in HASH_ALGORITHMS:
                    # An event whose algorithm this build cannot perform is not
                    # "probably fine" -- it is unverifiable, and unverifiable must
                    # never be read as verified (PHASE 3.6 / A5, fail-closed).
                    logger.error(
                        "audit event %s declares hash algorithm %r, which this "
                        "build cannot verify; counting it as broken rather than "
                        "assuming a default algorithm", event_id, declared_alg,
                    )
                    broken += 1
                    continue
                try:
                    details = json.loads(details_json) if details_json else {}
                    reconstructed = AuditEvent(
                        event_id=event_id,
                        event_type=AuditEventType(event_type),
                        principal_id=principal_id,
                        scope=AuditScope(scope),
                        timestamp=timestamp,
                        correlation_id=correlation_id,
                        outcome=outcome,
                        details=details,
                        hash_alg=declared_alg,
                    )
                    if reconstructed.compute_hash() != event_hash:
                        broken += 1
                except (ValueError, KeyError):
                    # Corrupted enum value or malformed details JSON
                    broken += 1

                # 5. Cumulative commitment (C2). A row written before C2 has no
                # link_hash and is simply skipped -- but skipping breaks the
                # running accumulation, so any row AFTER a gap is checked against
                # a fresh start and will mismatch if the history was edited. The
                # tail comparison below catches the case where only the newest
                # link_hash values were stripped.
                if stored_link is None:
                    running_link = None
                else:
                    try:
                        expected_link = _verification.link_hash(
                            running_link, event_hash, declared_alg)
                    except ValueError:
                        broken += 1
                        expected_link = None
                    if expected_link is not None and expected_link != stored_link:
                        broken += 1
                    running_link = stored_link
            elif stored_link is not None:
                # 结构模式：只推进链接指针，不做密码学校验。
                running_link = stored_link

        # 4. Tail integrity: the anchor must match the last stored event
        if state is not None:
            if state[0] != rows[-1][0] or state[1] != rows[-1][9]:
                broken += 1
            # The anchor's cumulative hash must agree with the tail's. Without
            # this, stripping link_hash from the newest rows would go
            # undetected (they would merely look like "pre-C2" rows).
            tail_link = rows[-1][12]
            anchor_link = state[2] if len(state) > 2 else None
            if (tail_link is None) != (anchor_link is None):
                broken += 1
            elif tail_link is not None and tail_link != anchor_link:
                broken += 1

        is_ok = broken == 0
        return is_ok, total

    # ------------------------------------------------------------------ #
    # C2 — segmented / incremental verification
    # ------------------------------------------------------------------ #
    def link_hash_at(self, seq: int) -> Optional[str]:
        """Stored cumulative link hash as of `seq` (None before any event).

        Convenience for callers that want to anchor a segment. It reads a
        stored value, so it is a *pointer*, never a proof -- use
        :meth:`verify_segment` for that.
        """
        if seq <= 0:
            return None
        row = self._conn.execute(
            "SELECT link_hash FROM audit_events WHERE seq = ?", (seq,)
        ).fetchone()
        return row[0] if row else None

    def verify_segment(self, start_seq: int, end_seq: int,
                       start_link_hash: Optional[str] = None) -> dict:
        """Recompute every event in [start_seq, end_seq] from raw storage.

        Trusts nothing: no checkpoint, no chain_state row, no cached value.
        If ``start_link_hash`` is omitted it is read from the previous event --
        from the SAME snapshot the range is verified against, so a concurrent
        (or later-rolled-back) append cannot make the result unreproducible.
        It is still only a pointer: the range itself is always fully recomputed,
        and ``anchor_source``/``rooted_at_genesis`` say what it proves.
        """
        # An anchor we read ourselves is a POINTER, not a proof. Label it so a
        # caller can never mistake "this range re-derives" for "the history
        # behind this range was proven".
        auto_anchor = start_link_hash is None and start_seq > 1
        anchor_source = (
            _verification.ANCHOR_STORED if auto_anchor else None)

        conn = self._open_snapshot()
        if conn is None:
            with self._lock:
                if auto_anchor:
                    start_link_hash = self.link_hash_at(start_seq - 1)
                return _verification.verify_segment(
                    self._conn, start_seq, end_seq, start_link_hash,
                    anchor_source=anchor_source).as_dict()
        try:
            if auto_anchor:
                row = conn.execute(
                    "SELECT link_hash FROM audit_events WHERE seq = ?",
                    (start_seq - 1,),
                ).fetchone()
                start_link_hash = row[0] if row else None
            return _verification.verify_segment(
                conn, start_seq, end_seq, start_link_hash,
                anchor_source=anchor_source).as_dict()
        finally:
            conn.close()

    def verify_incremental(self, max_events: Optional[int] = None) -> dict:
        """Verify only the events appended since the last checkpoint.

        Cost is proportional to the NEW events, not to the history -- that is
        the entire point of C2.

        Read the result honestly:

        * ``segment_verified``  — the newly appended events re-verify.
        * ``rooted_at_genesis`` — the anchor traces back to seq 1 through an
          unbroken cover of checkpoints.

        An incremental run that is not rooted at genesis has NOT verified the
        whole chain; it has verified a suffix against a checkpoint. Reporting
        those two as "verified" is exactly the error this API exists to
        prevent -- a correct checkpoint does not make the history correct.
        """
        conn = self._open_snapshot()
        if conn is None:
            with self._lock:
                return self._verify_incremental_on(self._conn, max_events)
        try:
            return self._verify_incremental_on(conn, max_events)
        finally:
            conn.close()

    def _verify_incremental_on(self, conn, max_events: Optional[int]) -> dict:
        row = conn.execute(
            "SELECT last_seq FROM chain_state WHERE id = 1").fetchone()
        tail_seq = row[0] if row else 0

        floor, floor_hash, floor_link = self._retention_state(conn)
        ckpt = _verification.latest_checkpoint(conn)
        # A checkpoint whose range sits at or below the retention floor cannot
        # anchor the retained suffix: its events were deleted by policy.
        if ckpt is not None and int(ckpt[2] or 0) <= floor:
            ckpt = None
        if ckpt is None:
            start_seq, start_link, anchor_id = floor + 1, (
                floor_link if floor > 0 else None), None
            anchor_source = (
                _verification.ANCHOR_RETENTION if floor > 0
                else _verification.ANCHOR_GENESIS)
        else:
            anchor_id, start_seq, start_link = ckpt[0], ckpt[2] + 1, ckpt[4]
            # The anchor is only "proven" if the checkpoint it came from is
            # itself reachable from the chain floor. Otherwise it is a stored
            # value and must be reported as such.
            anchor_end = ckpt[2]
            rooted_anchor = (
                _verification.coverage_frontier(
                    conn, anchor_end, floor) >= anchor_end)
            anchor_source = (
                _verification.ANCHOR_CHECKPOINT if rooted_anchor
                else _verification.ANCHOR_STORED)

        end_seq = tail_seq
        truncated = False
        if max_events is not None and end_seq - start_seq + 1 > max_events:
            end_seq = start_seq + max_events - 1
            truncated = True
        pending = max(0, int(tail_seq) - int(end_seq))

        if end_seq < start_seq:
            rooted_floor = _verification.rooted_at_genesis(conn, tail_seq, floor)
            return _verification.IncrementalResult(
                segment_verified=True,
                rooted_at_genesis=(floor == 0 and rooted_floor),
                from_seq=start_seq, to_seq=end_seq, events_checked=0,
                anchor_checkpoint_id=anchor_id, new_checkpoint_id=None,
                failures=[], anchor_source=anchor_source,
                retention_floor_seq=floor,
                rooted_at_retention_floor=rooted_floor,
                tail_seq=int(tail_seq), complete=True, pending_events=0,
            ).as_dict()

        segment = _verification.verify_segment(
            conn, start_seq, end_seq, start_link, anchor_source=anchor_source)

        new_id = None
        if segment.verified:
            # Written only AFTER the segment verified, and only as derived
            # data: it can always be recomputed from the events themselves.
            new_id = self._write_checkpoint(
                segment, method="incremental" if ckpt is not None else "full")

        # Rooted means "covered from the chain floor through the tail we just
        # verified". It must be read AFTER the checkpoint write and from a
        # FRESH connection: the verification connection is pinned to a
        # snapshot from before the write, so asking it would report the new
        # checkpoint as missing.
        #
        # Two distinct claims, deliberately not merged (D-4):
        #   rooted_at_genesis / rooted_at_retention_floor -- the whole TABLE
        #     TAIL is covered. A capped pass must report False here, otherwise
        #     "verified through 45 of 70" would masquerade as "chain verified".
        #   rooted_through_verified -- the range this pass actually re-derived
        #     is anchored to the floor. This is what lets a capped pass be
        #     USED (and continued next call) instead of falling back to a
        #     full 20k-row rescan on every request.
        rooted_floor_full = self._rooted_at(tail_seq, floor)
        rooted_verified = rooted_floor_full
        if truncated and not rooted_verified:
            rooted_verified = self._rooted_at(end_seq, floor)
        return _verification.IncrementalResult(
            segment_verified=segment.verified,
            rooted_at_genesis=(floor == 0 and rooted_floor_full),
            from_seq=start_seq, to_seq=end_seq,
            events_checked=segment.event_count,
            anchor_checkpoint_id=anchor_id, new_checkpoint_id=new_id,
            failures=segment.failures, anchor_source=anchor_source,
            retention_floor_seq=floor,
            rooted_at_retention_floor=rooted_floor_full,
            tail_seq=int(tail_seq), complete=not truncated,
            pending_events=pending,
            rooted_through_verified=bool(rooted_verified),
        ).as_dict()

    # ------------------------------------------------------------------ #
    # C2 — rolling re-verification
    # ------------------------------------------------------------------ #
    def verify_rolling(
        self,
        budget_events: int = _verification.DEFAULT_SEGMENT_EVENTS,
        max_age_sec: Optional[float] = None,
    ) -> dict:
        """Re-verify the least-recently-verified regions, within a budget.

        Why this has to exist: `verify_incremental` only ever looks at NEW
        events. A region that was verified once and then corrupted would
        therefore never be looked at again, and "verified" would slowly decay
        from a statement about the present into a statement about the day the
        checkpoint happened to be written. Rolling verification is what stops
        that decay: it keeps re-deriving old regions on a budget, oldest first.

        The budget is a work bound, not a correctness bound: a single region
        larger than the budget is still re-verified rather than skipped, and
        the result says so (`budget_exceeded`).

        Returns a dict with:
          * ``reverified``      — regions re-derived clean, now re-stamped;
          * ``failures``        — regions that no longer re-derive, each with
                                  the pinpointed seq values;
          * ``events_reverified``, ``budget_events``, ``budget_exceeded``;
          * ``remaining_stale`` — regions still awaiting a pass.
        """
        conn = self._open_snapshot()
        if conn is None:
            with self._lock:
                return self._verify_rolling_on(self._conn, budget_events,
                                               max_age_sec)
        try:
            floor, _floor_hash, _floor_link = self._retention_state(conn)
            queue = _verification.stale_checkpoints(
                conn, max_age_sec, floor_seq=floor)
        finally:
            conn.close()

        if not queue:
            return {
                "reverified": [], "failures": [], "events_reverified": 0,
                "budget_events": budget_events, "budget_exceeded": False,
                "remaining_stale": 0,
            }

        reverified: List[dict] = []
        failures: List[dict] = []
        used = 0
        exceeded = False

        for row in queue:
            (ckpt_id, start_seq, end_seq, start_link, end_link,
             _end_event_hash, event_count, _verified_at, _method) = row
            if used > 0 and used + event_count > budget_events:
                # Budget spent -- the rest waits for the next pass. Deferring
                # is allowed; pretending it was verified is not.
                break
            if event_count > budget_events:
                exceeded = True

            segment = self.verify_segment(start_seq, end_seq, start_link)
            used += segment["event_count"]
            entry = {
                "checkpoint_id": ckpt_id,
                "start_seq": start_seq,
                "end_seq": end_seq,
                "event_count": segment["event_count"],
                "failures": segment["failures"],
                "anchor_source": segment["anchor_source"],
            }
            if segment["verified"]:
                stamped = self._stamp_checkpoint(ckpt_id, end_link, event_count)
                entry["restamped"] = stamped
                reverified.append(entry)
            else:
                # Not deleted here: a failed re-derivation is EVIDENCE, and
                # discarding it would destroy the record of what was found.
                # `audit_checkpoints()` will keep reporting it as broken.
                entry["restamped"] = False
                failures.append(entry)

        remaining = len(queue) - len(reverified) - len(failures)
        return {
            "reverified": reverified,
            "failures": failures,
            "events_reverified": used,
            "budget_events": budget_events,
            "budget_exceeded": exceeded,
            "remaining_stale": remaining,
        }

    def _stamp_checkpoint(self, checkpoint_id: int, end_link_hash: str,
                          event_count: int) -> bool:
        """Re-stamp a checkpoint as re-verified now, guarded against movement."""
        try:
            return self._write_with_retry(
                lambda: self._attempt_stamp_checkpoint(
                    checkpoint_id, end_link_hash, event_count),
                name="_attempt_stamp_checkpoint",
            )
        except Exception:
            record_audit_failure()
            raise

    def _attempt_stamp_checkpoint(self, checkpoint_id: int, end_link_hash: str,
                                  event_count: int) -> bool:
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                stamped = _verification.refresh_checkpoint(
                    self._conn, checkpoint_id, end_link_hash, event_count)
                self._conn.commit()
                return stamped
            except Exception:
                self._conn.rollback()
                raise
        except Exception:
            record_audit_failure()
            raise

    def verification_coverage(self) -> dict:
        """How much of the chain a genesis-rooted cover currently reaches.

        Numbers, not a verdict -- but the numbers include ``uncovered_events``
        and ``oldest_verified_at``, so "we have not re-derived this in N days"
        cannot be hidden behind a green light.
        """
        conn = self._open_snapshot()
        if conn is None:
            with self._lock:
                floor = self._retention_state(self._conn)[0]
                return _verification.verification_coverage(self._conn, floor)
        try:
            floor = self._retention_state(conn)[0]
            return _verification.verification_coverage(conn, floor)
        finally:
            conn.close()

    def _rooted_at(self, upto_seq: int, floor_seq: int = 0) -> bool:
        """Coverage through `upto_seq`, read from a fresh snapshot.

        Fresh rather than the caller's pinned connection, because coverage is a
        statement about the derived state as it stands now -- including a
        checkpoint the caller just committed. ``floor_seq`` is the retention
        floor; coverage is then required from floor+1, not from genesis.
        """
        conn = self._open_snapshot()
        if conn is None:
            with self._lock:
                return _verification.rooted_at_genesis(
                    self._conn, upto_seq, floor_seq)
        try:
            return _verification.rooted_at_genesis(conn, upto_seq, floor_seq)
        finally:
            conn.close()

    def _write_checkpoint(self, segment, method: str) -> int:
        """Persist a derived checkpoint inside its own transaction."""
        return self._write_with_retry(
            lambda: self._attempt_write_checkpoint(segment, method),
            name="_attempt_write_checkpoint",
        )

    def _attempt_write_checkpoint(self, segment, method: str) -> int:
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                new_id = _verification.write_checkpoint(
                    self._conn, segment.start_seq, segment.end_seq,
                    segment.start_link_hash, segment.end_link_hash,
                    segment.end_event_hash, segment.event_count, method,
                )
                self._conn.commit()
                return new_id
            except Exception:
                self._conn.rollback()
                raise
        except Exception:
            record_audit_failure()
            raise

    def audit_checkpoints(self) -> Tuple[bool, List[int]]:
        """Re-derive EVERY checkpoint from raw events.

        This is the operation that stops a checkpoint from becoming a second
        trust root: a checkpoint that cannot be recomputed from the evidence is
        discarded, never believed.
        """
        conn = self._open_snapshot()
        if conn is None:
            with self._lock:
                floor = self._retention_state(self._conn)[0]
                return _verification.audit_checkpoints(self._conn, floor)
        try:
            floor = self._retention_state(conn)[0]
            return _verification.audit_checkpoints(conn, floor)
        finally:
            conn.close()

    def recompute_checkpoint(self, checkpoint_id: int) -> bool:
        """True if one stored checkpoint still matches a recomputation."""
        conn = self._open_snapshot()
        if conn is None:
            with self._lock:
                return _verification.recompute_checkpoint(
                    self._conn, checkpoint_id)
        try:
            return _verification.recompute_checkpoint(conn, checkpoint_id)
        finally:
            conn.close()

    def invalidate_checkpoints_from(self, seq: int) -> int:
        """Drop derived checkpoints covering `seq` onwards.

        Called when an event at or after `seq` turns out to be wrong: the
        derived state that covered it is no longer true.
        """
        return self._write_with_retry(
            lambda: self._attempt_invalidate_checkpoints(seq),
            name="_attempt_invalidate_checkpoints",
        )

    def _attempt_invalidate_checkpoints(self, seq: int) -> int:
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                n = _verification.discard_checkpoints_from(self._conn, seq)
                self._conn.commit()
                return n
            except Exception:
                self._conn.rollback()
                raise
        except Exception:
            record_audit_failure()
            raise

    def backfill_link_hashes(self) -> int:
        """Populate link_hash for rows written before C2 existed.

        One-time, O(n), and explicitly invoked -- it is never run implicitly.
        After it completes, the cumulative chain covers the whole history and
        segmented verification is available for it.
        """
        return self._write_with_retry(
            self._attempt_backfill_link_hashes,
            name="_attempt_backfill_link_hashes",
            continuous_lock=True,
        )

    def _attempt_backfill_link_hashes(self) -> int:
        # Why the append lock is held for the whole backfill, and why that is
        # correct rather than merely conservative: an appending writer derives
        # its link_hash from chain_state.last_link_hash, and during a backfill
        # that value is NULL or partial. Releasing the lock mid-backfill would
        # therefore let a concurrent append write a PERMANENTLY broken chain.
        # The operation is made fast (chunked executemany, one transaction)
        # instead of being made concurrent. Do not "optimise" the lock away.
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            rows = self._conn.execute(
                "SELECT seq, event_hash, hash_alg FROM audit_events "
                "ORDER BY seq ASC").fetchall()
            if not rows:
                self._conn.commit()
                return 0

            running = None
            batch: List[Tuple[str, int]] = []
            updated = 0
            for seq, event_hash, hash_alg in rows:
                running = _verification.link_hash(
                    running, event_hash, hash_alg or DEFAULT_HASH_ALG)
                batch.append((running, seq))
                if len(batch) >= _BACKFILL_CHUNK:
                    self._conn.executemany(
                        "UPDATE audit_events SET link_hash = ? WHERE seq = ?",
                        batch)
                    updated += len(batch)
                    batch.clear()
            if batch:
                self._conn.executemany(
                    "UPDATE audit_events SET link_hash = ? WHERE seq = ?",
                    batch)
                updated += len(batch)

            self._conn.execute(
                """INSERT INTO chain_state (id, last_seq, last_hash,
                    last_link_hash) VALUES (1, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       last_link_hash = excluded.last_link_hash""",
                (rows[-1][0], rows[-1][1], running),
            )
            self._conn.commit()
            return updated
        except Exception:
            self._conn.rollback()
            raise

    def query_events(
        self,
        principal_id: Optional[str] = None,
        scope: Optional[AuditScope] = None,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        outcome: Optional[str] = None,
        event_type: Optional[AuditEventType] = None,
        correlation_id: Optional[str] = None,
        limit: Optional[int] = None,
        reverse: bool = False,
    ) -> List[Dict[str, Any]]:
        """Query audit events with filter support.

        Events can be filtered by correlation_id for correlation-aware
        querying (Definition Lock section 113). Returns list of event
        dicts ordered by the monotonic sequence number (ascending by
        default, descending when reverse=True).

        Reads from a pinned snapshot (never the write connection, never the
        append lock): a reader must not observe an uncommitted append, and it
        must not block the writer. An unbounded call is capped at
        ``_QUERY_EVENTS_MAX_ROWS`` so a dashboard or replay cannot drag the
        whole multi-million-row chain into memory.
        """
        cap = (
            _QUERY_EVENTS_MAX_ROWS
            if limit is None
            else min(int(limit), _QUERY_EVENTS_MAX_ROWS)
        )
        return self._read_only(
            lambda conn: self._query_events_on(
                conn,
                principal_id=principal_id,
                scope=scope,
                start_time=start_time,
                end_time=end_time,
                outcome=outcome,
                event_type=event_type,
                correlation_id=correlation_id,
                limit=cap,
                reverse=reverse,
            )
        )

    def count_events(
        self,
        principal_id: Optional[str] = None,
        scope: Optional[AuditScope] = None,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        outcome: Optional[str] = None,
        event_type: Optional[AuditEventType] = None,
        correlation_id: Optional[str] = None,
    ) -> int:
        """Count events matching the same filters as ``query_events``.

        ``query_events`` caps every read at ``_QUERY_EVENTS_MAX_ROWS``. A caller
        that renders "N of M" (dashboard analytics) therefore cannot infer M
        from ``len(list)`` -- it would silently undercount everything past the
        cap and report ``truncated=False`` forever. This runs the equivalent
        COUNT(*) on the same pinned snapshot, without loading any rows.
        """
        where, params = self._events_where(
            principal_id=principal_id,
            scope=scope,
            start_time=start_time,
            end_time=end_time,
            outcome=outcome,
            event_type=event_type,
            correlation_id=correlation_id,
        )
        return int(
            self._read_only(
                lambda conn: conn.execute(
                    "SELECT COUNT(*) FROM audit_events WHERE 1=1" + where, params
                ).fetchone()[0]
            )
        )

    @staticmethod
    def _events_where(
        principal_id: Optional[str] = None,
        scope: Optional[AuditScope] = None,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        outcome: Optional[str] = None,
        event_type: Optional[AuditEventType] = None,
        correlation_id: Optional[str] = None,
    ):
        """Build the shared ``AND ...`` filter clause for event queries/counts."""
        where = ""
        params = []

        if principal_id:
            where += " AND principal_id = ?"
            params.append(principal_id)

        if scope:
            where += " AND scope = ?"
            params.append(scope.value)

        if start_time is not None:
            where += " AND timestamp >= ?"
            params.append(start_time)

        if end_time is not None:
            where += " AND timestamp <= ?"
            params.append(end_time)

        if outcome:
            where += " AND outcome = ?"
            params.append(outcome)

        if event_type:
            where += " AND event_type = ?"
            params.append(event_type.value)

        if correlation_id:
            where += " AND correlation_id = ?"
            params.append(correlation_id)

        return where, params

    def _query_events_on(
        self,
        conn,
        principal_id: Optional[str] = None,
        scope: Optional[AuditScope] = None,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        outcome: Optional[str] = None,
        event_type: Optional[AuditEventType] = None,
        correlation_id: Optional[str] = None,
        limit: Optional[int] = None,
        reverse: bool = False,
    ) -> List[Dict[str, Any]]:
        query = """SELECT event_id, event_type, principal_id, scope,
                   timestamp, correlation_id, outcome, details,
                   event_hash, prev_event_hash, hash_alg
                   FROM audit_events WHERE 1=1"""
        where, params = self._events_where(
            principal_id=principal_id,
            scope=scope,
            start_time=start_time,
            end_time=end_time,
            outcome=outcome,
            event_type=event_type,
            correlation_id=correlation_id,
        )
        query += where

        # Order by the monotonic sequence number so the result order is
        # deterministic even when timestamps tie.
        query += " ORDER BY seq DESC" if reverse else " ORDER BY seq ASC"

        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)

        cursor = conn.execute(query, params)
        results = []

        for row in cursor.fetchall():
            event_dict = {
                "event_id": row[0],
                "event_type": row[1],
                "principal_id": row[2],
                "scope": AuditScope(row[3]).value if isinstance(row[3], AuditScope) else row[3],
                "timestamp": row[4],
                "correlation_id": row[5],
                "outcome": row[6],
                "details": json.loads(row[7]) if row[7] else {},
                "event_hash": row[8],
                "prev_event_hash": row[9],
                # A5: the algorithm that produced event_hash. Without it on the
                # *read* surface the field would be write-only, and "what was
                # this verified with" would be unanswerable from the record --
                # which is the whole point of storing it.
                # P0-8c: report the declared algorithm verbatim. An absent
                # declaration must surface as None (UNVERIFIED), never as an
                # assumed default -- a reader must be able to tell the
                # difference between "sha256" and "nothing was declared".
                "hash_alg": row[10] if len(row) > 10 else None,
            }
            results.append(event_dict)

        return results

    def get_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        """Get a single audit event by ID.

        Read from a pinned snapshot, never from the write connection: an
        uncommitted append must not be observable, because an event that is
        later rolled back is not part of the history.
        """
        row = self._read_only(lambda conn: conn.execute(
            """SELECT event_id, event_type, principal_id, scope,
               timestamp, correlation_id, outcome, details,
               event_hash, prev_event_hash, hash_alg
               FROM audit_events WHERE event_id = ?""",
            (event_id,),
        ).fetchone())
        if not row:
            return None

        return {
            "event_id": row[0],
            "event_type": row[1],
            "principal_id": row[2],
            "scope": AuditScope(row[3]).value if isinstance(row[3], AuditScope) else row[3],
            "timestamp": row[4],
            "correlation_id": row[5],
            "outcome": row[6],
            "details": json.loads(row[7]) if row[7] else {},
            "event_hash": row[8],
            "prev_event_hash": row[9],
            "hash_alg": row[10] if len(row) > 10 else DEFAULT_HASH_ALG,
        }

    def get_stats(self) -> Dict[str, Any]:
        """Get audit store statistics.

        Both SELECTs run on ONE pinned snapshot, so the breakdown and the total
        are counts of the same set of rows -- otherwise a concurrent append
        between them yields a breakdown that does not sum to the total.
        """
        def _read(conn):
            breakdown = {}
            for row in conn.execute(
                "SELECT event_type, outcome, COUNT(*) as cnt "
                "FROM audit_events GROUP BY event_type, outcome"
            ).fetchall():
                breakdown[f"{row[0]}:{row[1]}"] = row[2]
            total = conn.execute(
                "SELECT COUNT(*) FROM audit_events").fetchone()[0]
            lease = conn.execute(
                "SELECT writer_id, writer_epoch FROM writer_lease WHERE id = 1"
            ).fetchone()
            writer_id = lease[0] if lease else None
            writer_epoch = lease[1] if lease else 0
            return breakdown, total, writer_id, writer_epoch

        breakdown, total, writer_id, writer_epoch = self._read_only(_read)

        return {
            "total_events": total,
            "breakdown": breakdown,
            "db_path": self._db_path,
            "failures": get_audit_failure_count(),
            # #99 — surfaced so an operator can see whether the writer-identity
            # model is active and how many forced takeovers have occurred.
            "writer_id": writer_id,
            "writer_epoch": writer_epoch,
            "lease_identity_mode": _lease_identity_mode(),
            "writer_fence_total": get_writer_fence_total(),
        }

    def initialize(self) -> None:
        self.lifecycle = KernelLifecycle.READY

    def shutdown(self) -> None:
        self.lifecycle = KernelLifecycle.STOPPED

    def pause(self) -> None:
        if self.lifecycle not in (KernelLifecycle.READY, KernelLifecycle.UNINITIALIZED):
            raise KernelStateError(f"cannot pause from {self.lifecycle}")
        self.lifecycle = KernelLifecycle.PAUSED

    def resume(self) -> None:
        if self.lifecycle is not KernelLifecycle.PAUSED:
            raise KernelStateError(f"cannot resume from {self.lifecycle}")
        self.lifecycle = KernelLifecycle.READY


# Global audit store instance
_audit_store: Optional[AuditStore] = None
#: 冷会话构造并发化后，多个线程可能同时首次写审计。不加锁会各自构造
#: 一个 AuditStore 并打开同一个 sqlite 库，既浪费连接也可能撞锁。
_audit_store_lock = threading.Lock()

# CRIT-1C (PHASE 3.6 / D17): audit write failures must not be silently swallowed.
# Central failure counter -- incremented whenever an audit write to the
# authoritative store raises, so operators can observe "Evidence=missing"
# instead of a green dashboard. This is Layer 1 of the two-layer fix; it makes
# the audit Evidence layer *observable* WITHOUT changing availability semantics
# (LOW tier is still allowed to keep running, but the failure is no longer
# silent). Layer 2 (blocking for critical/sovereignty-sensitive actions) is a
# separate, deliberate decision owned by the human (see H-D17 :163).
_audit_failure_total = 0
_audit_failure_lock = threading.Lock()
_audit_failure_ever = False


def verify_chain_integrity(conn) -> Tuple[bool, int]:
    """Verify the audit hash chain over an ALREADY-OPEN connection.

    This is the read-only entry point for monitoring and CI gates that must
    inspect a store WITHOUT constructing an :class:`AuditStore`. Constructing a
    store is **not** read-only: ``_init_db`` issues DDL and COMMITS it (schema
    creation, additive migrations, ``CREATE UNIQUE INDEX``, anchor seeding),
    which silently mutates the very file we claim to be only inspecting. Against
    a deployed -- or forensically frozen -- audit store that is unacceptable, so
    callers open their own connection (normally ``file:...?mode=ro`` plus
    ``PRAGMA query_only=ON``) and hand it here.

    Returns ``(is_ok, total_events)``; semantics are identical to
    :meth:`AuditStore.verify_integrity`.
    """
    return AuditStore._verify_integrity_on(conn)


def record_audit_failure() -> None:
    """Increment the global audit-failure counter (CRIT-1C / D17 LOW tier signal)."""
    global _audit_failure_total, _audit_failure_ever
    with _audit_failure_lock:
        _audit_failure_total += 1
        _audit_failure_ever = True


def get_audit_failure_count() -> int:
    """Return the number of audit writes that failed since process start."""
    return _audit_failure_total


def audit_failure_occurred() -> bool:
    """True once any audit write has failed this process (monotonic)."""
    return _audit_failure_ever


# --- write-health watermarks -------------------------------------------------
# Why this exists (and why it is NOT "time since the last event"):
# an idle system has no audit traffic at all, so "time since MAX(timestamp)"
# grows forever and says nothing about write health. The honest question is
# "is a write request that some caller already issued still not committed?".
# Every public event append therefore brackets itself with begin/end here:
#
#   * ``inflight``      -> writes issued but not yet committed (the real backlog)
#   * ``failure_streak``-> a write failed and no later write has succeeded
#
# That yields: idle -> 0; blocked/stalled writer -> oldest in-flight age;
# failing writer -> failing signal. It is process-local by design: audit writes
# are synchronous in the writing process, so this is the health of THIS
# process's write path. If the whole process dies, its own metrics die with
# it and the service-heartbeat rule is the honest detector.
#
# Cost is one uncontended lock + a dict insert/remove per append (or per batch,
# not per event), which is noise next to the SQLite transaction itself.
@dataclass
class _WriteHealthState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    # token -> start timestamp of each append request not yet finished.
    inflight: Dict[int, float] = field(default_factory=dict)
    next_token: int = 0
    attempted: int = 0
    succeeded: int = 0
    failed: int = 0
    last_attempt_ts: Optional[float] = None
    last_success_ts: Optional[float] = None
    last_failure_ts: Optional[float] = None
    # Consecutive failed appends since the last success. Non-zero == "failing":
    # we have positive proof of a failed write and no proof of recovery yet.
    failure_streak: int = 0
    last_failure_kind: Optional[str] = None


_write_health_state = _WriteHealthState()


def _begin_audit_write() -> int:
    """Mark one event-append request as in-flight. Never raises."""
    try:
        state = _write_health_state
        now = time.time()
        with state.lock:
            token = state.next_token
            state.next_token += 1
            state.inflight[token] = now
            state.attempted += 1
            state.last_attempt_ts = now
            return token
    except Exception:  # noqa: BLE001 - observability must not fail writes
        return -1


def _end_audit_write(token: int, ok: bool, error: Optional[BaseException] = None) -> None:
    """Mark one event-append request as finished. Never raises."""
    if token < 0:
        return
    try:
        state = _write_health_state
        now = time.time()
        with state.lock:
            state.inflight.pop(token, None)
            if ok:
                state.succeeded += 1
                state.last_success_ts = now
                state.failure_streak = 0
                state.last_failure_kind = None
            else:
                state.failed += 1
                state.last_failure_ts = now
                state.failure_streak += 1
                state.last_failure_kind = _classify_write_error(error)
    except Exception:  # noqa: BLE001 - observability must not fail writes
        pass


def _classify_write_error(error: Optional[BaseException]) -> str:
    """Name the failure mode so an operator can tell disk from lock from fence."""
    if isinstance(error, AuditDiskFullError):
        return "disk_full"
    if isinstance(error, AuditCorruptionError):
        return "corruption"
    code = getattr(error, "sqlite_errorcode", None) if error is not None else None
    if code in (_SQLITE_BUSY, _SQLITE_LOCKED, _SQLITE_READONLY):
        return "db_locked"
    if code == _SQLITE_IOERR:
        return "io_error"
    return type(error).__name__ if error is not None else "unknown"


def get_audit_write_health(now: Optional[float] = None) -> Dict[str, Any]:
    """Read-only snapshot of the audit write path's health watermarks.

    ``now`` exists so diagnostics/tests can evaluate ages against a chosen
    instant; production callers omit it and get real wall-clock ages.

    Keys:
      inflight / oldest_inflight_age_seconds -- issued-but-uncommitted backlog
      failing / failure_streak / last_failure_kind -- unproven-recovery failures
      attempted / succeeded / failed -- cumulative outcomes since process start
      last_attempt_ts / last_success_ts / last_failure_ts -- raw watermarks
    """
    state = _write_health_state
    now_ts = time.time() if now is None else float(now)
    with state.lock:
        oldest = min(state.inflight.values()) if state.inflight else None
        return {
            "inflight": len(state.inflight),
            "oldest_inflight_started_ts": oldest,
            "oldest_inflight_age_seconds": (
                max(0.0, now_ts - oldest) if oldest is not None else 0.0
            ),
            "attempted": state.attempted,
            "succeeded": state.succeeded,
            "failed": state.failed,
            "last_attempt_ts": state.last_attempt_ts,
            "last_success_ts": state.last_success_ts,
            "last_failure_ts": state.last_failure_ts,
            "failure_streak": state.failure_streak,
            "failing": state.failure_streak > 0,
            "last_failure_kind": state.last_failure_kind,
        }


# A-01: append-lock hold duration -- a root-cause signal for R-G2-07c (a backfill
# can hold the append lock for minutes) and U50 (full-chain verify_integrity holds
# it ~264 s at 1e7 rows). One "last hold" gauge + one cumulative counter answers
# "is the lock stuck, and who is holding it" without a histogram.
audit_lock_hold_seconds_last = gauge(
    "audit_lock_hold_seconds_last",
    "Duration the AuditStore append lock was last held (seconds).",
    "seconds",
)
audit_lock_hold_seconds_total = counter(
    "audit_lock_hold_seconds_total",
    "Cumulative time the AuditStore append lock has been held (seconds).",
    "seconds",
)


def _record_lock_hold(duration: float, name: str) -> None:
    """Record an append-lock hold and warn if it was unusually long.

    Wrapped in try/except because a metric must never break the write path.
    """
    try:
        audit_lock_hold_seconds_last.set(duration)
        audit_lock_hold_seconds_total.inc(duration)
        if duration > 1.0:
            logger.warning(
                "audit append-lock held %.3fs in %s", duration, name)
    except Exception:  # noqa: BLE001 - observability must not fail writes
        pass


def get_audit_store() -> AuditStore:
    """Get or create the global audit store instance.

    构造/打开失败时先写一条 ``evt=db.open.failed subsystem=audit``（带相对
    数据目录的路径与 sqlite 原文），再原样抛出——调用方据此决定降级，而日志
    不会丢失"是哪个库、为什么"。
    """
    global _audit_store
    if _audit_store is None:
        with _audit_store_lock:
            if _audit_store is None:
                try:
                    store = AuditStore()
                    store.initialize()  # 存在即 READY：构造完成即视为就绪
                except Exception as exc:
                    try:
                        from ...observability import boot_report

                        boot_report.record_db_open_failure(
                            "audit",
                            os.environ.get("AUDIT_DB_PATH") or "<AUDIT_DB_PATH 未配置>",
                            exc,
                            action=(
                                "degraded: 审计链停止写入检查点；修复数据目录写权限、"
                                "隔离/重建损坏库或配置 AUDIT_DB_PATH 后重启"
                            ),
                        )
                    except Exception:  # noqa: BLE001 - 日志失败不能掩盖原始异常
                        pass
                    raise
                _audit_store = store
    return _audit_store


def get_audit_connection() -> "sqlite3.Connection":
    """Expose the audit store's SQLite connection.

    Used by the agent-safety execution fence (``src/kernels/execution/fence.py``)
    so the fence check and the audit append share ONE connection/transaction and
    are therefore atomic. The fence tables live in the same database file.
    """
    return get_audit_store()._conn


def log_event(
    event_type: AuditEventType,
    principal_id: str,
    scope: AuditScope,
    outcome: str,
    details: Optional[Dict[str, Any]] = None,
    correlation_id: Optional[str] = None,
) -> AuditEvent:
    """Log an audit event."""
    return get_audit_store().log_event(event_type, principal_id, scope, outcome, details, correlation_id)


def verify_audit_integrity() -> Tuple[bool, int]:
    """Verify audit log integrity."""
    return get_audit_store().verify_integrity()


def audit_query(
    principal_id: Optional[str] = None,
    scope: Optional[AuditScope] = None,
    start_time: Optional[float] = None,
    end_time: Optional[float] = None,
    outcome: Optional[str] = None,
    event_type: Optional[AuditEventType] = None,
    correlation_id: Optional[str] = None,
    limit: Optional[int] = None,
    reverse: bool = False,
) -> List[Dict[str, Any]]:
    """Query audit events."""
    return get_audit_store().query_events(
        principal_id=principal_id,
        scope=scope,
        start_time=start_time,
        end_time=end_time,
        outcome=outcome,
        event_type=event_type,
        correlation_id=correlation_id,
        limit=limit,
        reverse=reverse,
    )


def audit_count(
    principal_id: Optional[str] = None,
    scope: Optional[AuditScope] = None,
    start_time: Optional[float] = None,
    end_time: Optional[float] = None,
    outcome: Optional[str] = None,
    event_type: Optional[AuditEventType] = None,
    correlation_id: Optional[str] = None,
) -> int:
    """Count audit events matching the same filters as ``audit_query``."""
    return get_audit_store().count_events(
        principal_id=principal_id,
        scope=scope,
        start_time=start_time,
        end_time=end_time,
        outcome=outcome,
        event_type=event_type,
        correlation_id=correlation_id,
    )


def audit_get_event(event_id: str) -> Optional[Dict[str, Any]]:
    """Get a single audit event."""
    return get_audit_store().get_event(event_id)


def audit_stats() -> Dict[str, Any]:
    """Get audit store statistics."""
    return get_audit_store().get_stats()


def audit_verification_coverage() -> Dict[str, Any]:
    """Audit hash-chain verification coverage (C2).

    Surfaced via the /v1/ready health endpoint so that "the chain has not been
    re-derived in N days" or "the history is only partially covered by
    checkpoints" cannot hide behind a green dashboard. Returns the dict produced
    by AuditStore.verification_coverage(): tail_seq, covered_through,
    uncovered_events, coverage_ratio, checkpoint_count, oldest_verified_at,
    newest_verified_at, rooted_at_genesis. Read-only: opens a snapshot
    connection and re-derives checkpoints from raw events.
    """
    return get_audit_store().verification_coverage()


def audit_failure_count() -> int:
    """Return the number of audit writes that failed since process start.

    CRIT-1C / D17 (Layer 1): this is the observable "Evidence=missing" signal.
    It is monotonically increasing for the life of the process and is surfaced
    via audit_stats() and the /v1/ready health endpoint.
    """
    return get_audit_failure_count()
