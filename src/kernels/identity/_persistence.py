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
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from src._time import utc_now

logger = logging.getLogger("liuhao.kernel.identity")

#: Bounded retry budget for transient SQLite write failures.
#:
#: Measured 2026-09-15: with 24 concurrent writers on Windows, ~25-50% of runs
#: lost 1-4 registrations, every one of them raising
#: ``attempt to write a readonly database`` from a connection opened *after*
#: the WAL switch. The failure is transient (it clears on a follow-up
#: statement) and is not a lock-wait, so ``busy_timeout`` does not prevent it;
#: a bounded retry does (0/12 runs lost rows, vs 6/12 without).
_SQLITE_WRITE_ATTEMPTS = 5
_SQLITE_WRITE_RETRY_DELAY = 0.02

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

# --------------------------------------------------------------------------- #
# PHASE 3.6 / A3 containment — authorization source integrity
# --------------------------------------------------------------------------- #
#
# "Who may hold sovereignty" is decided by the rows in this store, and the store
# is a plain JSON file (or a plain SQLite table) that anything with filesystem
# access -- including an in-process plugin, which Spec §2 B-11 records as loaded
# without a sandbox -- can edit.
#
# Measured 2026-09-20 on the pre-containment tree:
#   * ``grep -cE "hmac|sign\\(|signature|chmod|0600" src/kernels/identity/_persistence.py`` -> 0
#   * ``:126`` docstring: "Decode the ``permissions`` column, **tolerating
#     hand-edited rows**"
#   * ``:135`` fallback branch comment: "A comma-separated list is what a human
#     would type into a DB browser."
#
# So a hand edit is not an anomaly this code has a concept for -- it is an
# interaction the code implements deliberately. The consequence is that the
# charter redlines D1-D4 have their gate *judged* inside the policy kernel (hard
# to bypass, precedence-guarded) while the question "who is allowed to pass the
# double-signature as grantor" bottoms out in a hand-editable text file.
#
# Containment: an optional per-row authentication tag. When the key below is
# configured, a row that does not authenticate is **refused** (fail-closed) and
# the refusal is reported. When the key is NOT configured, verification cannot
# be performed, so the store refuses every row (fail-closed, HC-11 / U6) rather
# than admitting unverifiable, attacker-writable rows; ``integrity_enforced`` is
# False and ``last_load_report["rejected"]`` records what was refused. This is the
# "enforce key" posture: a registry without a key holds no admitted humans.
#
# Deliberately NOT provided: a "skip verification" switch. A switch that
# disables the check would let the check be neutralised by changing one string
# in one environment, which is the failure mode this whole phase is about.

#: Optional per-row trust score. Absent means the kernel's documented default
#: applies -- the kernel no longer hardcodes the number.
FIELD_TRUST_SCORE = "trust_score"

#: Serialised name of the authentication tag column.
FIELD_INTEGRITY = "integrity"

#: Integrity verdict attached to an *extended* row.
FIELD_INTEGRITY_STATUS = "integrity_status"

INTEGRITY_VERIFIED = "verified"
INTEGRITY_UNVERIFIED = "unverified"

#: Env var holding the key used to authenticate registry rows.
HUMAN_IDENTITIES_INTEGRITY_KEY_ENV = "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY"

#: Fields covered by the tag, in the canonical order :func:`_canonical_row`
#: serialises them. ``registered_at`` is deliberately excluded: it is bookkeeping
#: (and is preserved across edits by design), whereas these five decide authority.
_TAGGED_FIELDS = (
    FIELD_PRINCIPAL,
    FIELD_DISPLAY_NAME,
    FIELD_PERMISSIONS,
    FIELD_SCOPE,
    FIELD_TRUST_SCORE,
)

# PHASE 3.6 / A5 -- algorithm identifiers on *this* evidence too
# ---------------------------------------------------------------
# A5 fixed the audit chain so an event states which hash produced it. The row
# authentication tag written here has the identical problem, and fixing only the
# audit side would leave the registry as the one piece of evidence whose
# verification recipe lives out-of-band: a bare hex digest does not say whether
# it is an HMAC or a plain hash, which primitive, or how long the key was, so a
# future verifier must trust a note (or the spec version) instead of the record.
# The tag is therefore written as ``"<alg>:<hexdigest>"`` and verification
# dispatches on the declared name.

#: MAC algorithms this build can produce and verify, by identifier.
MAC_ALGORITHMS: Dict[str, Callable[[bytes, bytes], str]] = {
    "hmac-sha256": lambda key, msg: hmac.new(key, msg, hashlib.sha256).hexdigest(),
}

#: Identifier written on every tag this build produces.
DEFAULT_MAC_ALG = "hmac-sha256"

#: Separator between the algorithm identifier and the digest.
_MAC_ALG_SEPARATOR = ":"

#: Verdict for a row loaded without a configured key.
DEFAULT_TRUST_SCORE_SENTINEL = "default"

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
    source        TEXT NOT NULL DEFAULT '',
    trust_score   REAL,
    integrity     TEXT
)
"""

#: Columns added after the original schema shipped. Migrated in place by
#: ``_prepare`` so a pre-existing registration database keeps working.
_SQLITE_ADDED_COLUMNS = (
    ("trust_score", "REAL"),
    ("integrity", "TEXT"),
)

# Deliberately does NOT overwrite ``registered_at`` on conflict: that column
# records when the principal was *first* registered, and an upsert is an edit,
# not a re-registration.
_SQLITE_UPSERT = """
INSERT INTO human_identities
    (principal, display_name, permissions, scope, registered_at, source,
     trust_score, integrity)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(principal) DO UPDATE SET
    display_name = excluded.display_name,
    permissions  = excluded.permissions,
    scope        = excluded.scope,
    source       = excluded.source,
    trust_score  = excluded.trust_score,
    integrity    = excluded.integrity
"""


# --------------------------------------------------------------------------- #
# 账号树（单机单根）
# --------------------------------------------------------------------------- #
#
# 账号树刻意放在**独立表 / 独立文档键**里，而不是给 human_identities 加列：
#
# * 旧库的 ``human_identities`` 行有既有 integrity tag，改动其 canonical 形状会
#   让所有已签名行立即失效（等于把老部署锁在门外）；
# * 迁移是"派生"而非"改写"：旧身份原样保留，树只是把它标记为根账号。回滚
#   时删掉 account_tree / 文档里的 accounts 键即可，身份一个都不丢。
#
# 每一行同样带算法标识的 HMAC tag（复用 human 行的 MAC 原语），覆盖所有决定
# 权限位置的字段（父、深度、根、作用域、Key 共享授权、状态、epoch、一次性
# 码哈希）。

FIELD_PARENT_ID = "parent_id"
FIELD_DEPTH = "depth"
FIELD_ROOT_ID = "root_id"
FIELD_ALLOW_SUPERIOR_KEY_USE = "allow_superior_key_use"
FIELD_KEY_AUTH_SOURCE = "key_auth_source"
FIELD_ACCOUNT_STATUS = "status"
FIELD_SECURITY_EPOCH = "security_epoch"
FIELD_ACTIVATION_HASH = "activation_hash"
FIELD_ACTIVATION_EXPIRES_AT = "activation_expires_at"
FIELD_ACTIVATION_ATTEMPTS = "activation_attempts"
FIELD_RECOVERY_HASH = "recovery_hash"
FIELD_CREATED_AT = "created_at"
FIELD_UPDATED_AT = "updated_at"

#: 账号树行里参与 tag 的字段（bookkeeping 的 created/updated/source 不参与）。
_ACCOUNT_TAGGED_FIELDS = (
    "principal",
    FIELD_PARENT_ID,
    FIELD_DEPTH,
    FIELD_ROOT_ID,
    FIELD_PERMISSIONS,
    FIELD_ALLOW_SUPERIOR_KEY_USE,
    FIELD_KEY_AUTH_SOURCE,
    FIELD_ACCOUNT_STATUS,
    FIELD_SECURITY_EPOCH,
    FIELD_ACTIVATION_HASH,
    FIELD_ACTIVATION_EXPIRES_AT,
    FIELD_ACTIVATION_ATTEMPTS,
    FIELD_RECOVERY_HASH,
)

_SQLITE_ACCOUNT_SCHEMA = """
CREATE TABLE IF NOT EXISTS account_tree (
    principal                TEXT PRIMARY KEY,
    parent_id                TEXT,
    depth                    INTEGER NOT NULL DEFAULT 0,
    root_id                  TEXT NOT NULL DEFAULT '',
    permissions              TEXT NOT NULL DEFAULT '[]',
    allow_superior_key_use   INTEGER NOT NULL DEFAULT 1,
    key_auth_source          TEXT NOT NULL DEFAULT '',
    status                   TEXT NOT NULL DEFAULT 'active',
    security_epoch           INTEGER NOT NULL DEFAULT 0,
    activation_hash          TEXT NOT NULL DEFAULT '',
    activation_expires_at    TEXT,
    activation_attempts      INTEGER NOT NULL DEFAULT 0,
    recovery_hash            TEXT NOT NULL DEFAULT '',
    created_at               TEXT NOT NULL DEFAULT '',
    updated_at               TEXT NOT NULL DEFAULT '',
    source                   TEXT NOT NULL DEFAULT '',
    integrity                TEXT
)
"""

_SQLITE_ACCOUNT_UPSERT = """
INSERT INTO account_tree
    (principal, parent_id, depth, root_id, permissions,
     allow_superior_key_use, key_auth_source, status, security_epoch,
     activation_hash, activation_expires_at, activation_attempts, recovery_hash,
     created_at, updated_at, source, integrity)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(principal) DO UPDATE SET
    parent_id              = excluded.parent_id,
    depth                  = excluded.depth,
    root_id                = excluded.root_id,
    permissions            = excluded.permissions,
    allow_superior_key_use = excluded.allow_superior_key_use,
    key_auth_source        = excluded.key_auth_source,
    status                 = excluded.status,
    security_epoch         = excluded.security_epoch,
    activation_hash        = excluded.activation_hash,
    activation_expires_at  = excluded.activation_expires_at,
    activation_attempts    = excluded.activation_attempts,
    recovery_hash          = excluded.recovery_hash,
    updated_at             = excluded.updated_at,
    source                 = excluded.source,
    integrity              = excluded.integrity
"""

_SQLITE_IMPORT_RECEIPT_SCHEMA = """
CREATE TABLE IF NOT EXISTS account_import_receipts (
    bundle_id  TEXT PRIMARY KEY,
    imported_at TEXT NOT NULL DEFAULT ''
)
"""


def _coerce_bool_int(raw: Any) -> int:
    return 1 if bool(raw) else 0


def _canonical_account(record: Dict[str, Any]) -> str:
    """账号树行参与 tag 的权威字段的规范化序列化。"""
    try:
        depth = int(record.get(FIELD_DEPTH) or 0)
    except (TypeError, ValueError):
        depth = 0
    try:
        epoch = int(record.get(FIELD_SECURITY_EPOCH) or 0)
    except (TypeError, ValueError):
        epoch = 0
    try:
        attempts = int(record.get(FIELD_ACTIVATION_ATTEMPTS) or 0)
    except (TypeError, ValueError):
        attempts = 0
    payload = {
        "principal": str(record.get(FIELD_PRINCIPAL) or "").strip(),
        FIELD_PARENT_ID: (
            str(record.get(FIELD_PARENT_ID)).strip()
            if record.get(FIELD_PARENT_ID)
            else None
        ),
        FIELD_DEPTH: depth,
        FIELD_ROOT_ID: str(record.get(FIELD_ROOT_ID) or "").strip(),
        FIELD_PERMISSIONS: sorted(
            {str(p) for p in (record.get(FIELD_PERMISSIONS) or [])}
        ),
        FIELD_ALLOW_SUPERIOR_KEY_USE: bool(
            record.get(FIELD_ALLOW_SUPERIOR_KEY_USE, True)
        ),
        FIELD_KEY_AUTH_SOURCE: str(record.get(FIELD_KEY_AUTH_SOURCE) or ""),
        FIELD_ACCOUNT_STATUS: str(record.get(FIELD_ACCOUNT_STATUS) or "active"),
        FIELD_SECURITY_EPOCH: epoch,
        FIELD_ACTIVATION_HASH: str(record.get(FIELD_ACTIVATION_HASH) or ""),
        FIELD_ACTIVATION_EXPIRES_AT: (
            str(record.get(FIELD_ACTIVATION_EXPIRES_AT))
            if record.get(FIELD_ACTIVATION_EXPIRES_AT)
            else None
        ),
        FIELD_ACTIVATION_ATTEMPTS: attempts,
        FIELD_RECOVERY_HASH: str(record.get(FIELD_RECOVERY_HASH) or ""),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def compute_account_tag(record: Dict[str, Any], key: str) -> Optional[str]:
    if not key:
        return None
    digest = MAC_ALGORITHMS[DEFAULT_MAC_ALG](
        key.encode("utf-8"), _canonical_account(record).encode("utf-8")
    )
    return f"{DEFAULT_MAC_ALG}{_MAC_ALG_SEPARATOR}{digest}"


def account_tag_matches(record: Dict[str, Any], key: str, tag: Any) -> bool:
    """账号树行认证。语义与 :func:`tag_matches` 完全一致（fail-closed）。"""
    if not key or not isinstance(tag, str) or not tag:
        return False
    alg_name, sep, _declared = tag.partition(_MAC_ALG_SEPARATOR)
    if not sep:
        return False
    produce = MAC_ALGORITHMS.get(alg_name)
    if produce is None:
        return False
    expected = f"{alg_name}{_MAC_ALG_SEPARATOR}" + produce(
        key.encode("utf-8"), _canonical_account(record).encode("utf-8")
    )
    return hmac.compare_digest(tag, expected)


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


# --------------------------------------------------------------------------- #
# PHASE 3.6 / A3: row authentication
# --------------------------------------------------------------------------- #

def _coerce_trust_number(raw: Any) -> Optional[float]:
    """Normalise a trust score to ``[0, 1]``, or ``None`` when absent/invalid."""
    if raw is None or raw == "":
        return None
    try:
        return max(0.0, min(1.0, float(raw)))
    except (TypeError, ValueError):
        return None


def _canonical_trust(raw: Any) -> str:
    """Stable string form of a trust score, used *inside the tag input*.

    Normalising here (rather than comparing floats) is what lets the tag be
    recomputed identically at write time and at verify time, across a JSON
    round-trip and a SQLite REAL round-trip.
    """
    number = _coerce_trust_number(raw)
    if number is None:
        return DEFAULT_TRUST_SCORE_SENTINEL
    return f"{number:.6f}"


def _canonical_row(entry: Dict[str, Any]) -> str:
    """Canonical serialisation of the authority-bearing fields of a row."""
    payload = {
        FIELD_PRINCIPAL: str(entry.get(FIELD_PRINCIPAL) or "").strip(),
        FIELD_DISPLAY_NAME: str(entry.get(FIELD_DISPLAY_NAME) or ""),
        FIELD_PERMISSIONS: sorted(
            {str(p) for p in (entry.get(FIELD_PERMISSIONS) or [])}
        ),
        FIELD_SCOPE: str(entry.get(FIELD_SCOPE) or "L0"),
        FIELD_TRUST_SCORE: _canonical_trust(entry.get(FIELD_TRUST_SCORE)),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def integrity_key() -> str:
    """The configured registry authentication key, or ``""`` when unset."""
    return (os.environ.get(HUMAN_IDENTITIES_INTEGRITY_KEY_ENV) or "").strip()


def integrity_enforced() -> bool:
    """True when registry rows must authenticate before they are admitted."""
    return bool(integrity_key())


#: Integrity posture reported to readiness / observability / deployment gates.
#:
#: PHASE 3.6 / P0-3 (boss decision 2026-09-22, id 9c1k2m / 9r0n4s / 6t0p1h): when a
#: human registry is in use but no integrity key is configured, the registry is
#: NOT "fully sovereign" -- it must be reported as explicitly DEGRADED /
#: UNVERIFIED, observable, auditable and deployment-gate aware. The previous
#: warn-only behaviour (missing key -> warning -> system appears fully sovereign)
#: is forbidden by that decision.
#:
#: UPDATED by HC-11 / U6 (2026-09-25): escalated from "report degraded" to
#: "enforce key" -- with no key configured the store REFUSES every row
#: (fail-closed) rather than admitting unverified rows. Refused rows surface via
#: ``last_load_report["rejected"]`` and ``integrity_enforced`` stays False.
INTEGRITY_STATE_ENFORCED = "enforced"            # key configured -> fail-closed
INTEGRITY_STATE_DEGRADED = "degraded_unverified"  # registry in use, no key
INTEGRITY_STATE_NA = "not_applicable"            # no humans to protect


def registry_integrity_state(*, admitted: int, configured: bool) -> str:
    """The honest integrity posture of the human registry.

    ``configured`` is True when a store location is set; ``admitted`` is how many
    humans the last load actually admitted. With no humans admitted there is
    nothing whose integrity can be attacked, so the posture is
    ``not_applicable`` rather than degraded. With humans present and a key set the
    rows authenticate fail-closed (``enforced``); with humans present and no key
    the rows are attacker-writable (``degraded_unverified``) -- which must never
    be surfaced as a clean pass.
    """
    if not configured or admitted == 0:
        return INTEGRITY_STATE_NA
    return INTEGRITY_STATE_ENFORCED if integrity_enforced() else INTEGRITY_STATE_DEGRADED


def compute_row_tag(entry: Dict[str, Any], key: str) -> Optional[str]:
    """``"<alg>:<hexdigest>"`` authenticating the authority-bearing fields.

    The algorithm name travels **inside** the tag (PHASE 3.6 / A5), so the record
    says how to verify itself. ``None`` when no key is configured -- and because
    verification cannot be performed without the key, callers refuse the row
    (fail-closed, HC-11 / U6) rather than admitting it unverified.
    """
    if not key:
        return None
    produce = MAC_ALGORITHMS[DEFAULT_MAC_ALG]
    digest = produce(key.encode("utf-8"), _canonical_row(entry).encode("utf-8"))
    return f"{DEFAULT_MAC_ALG}{_MAC_ALG_SEPARATOR}{digest}"


def tag_matches(entry: Dict[str, Any], key: str, tag: Any) -> bool:
    """Constant-time check that ``tag`` authenticates ``entry``.

    Fail-closed on an unrecognised or absent algorithm identifier: a tag this
    build cannot *verify* is not a tag this build may *accept*, and silently
    falling back to the default algorithm would make the identifier decorative.
    """
    if not key:
        # No key configured -> verification CANNOT be performed, so the row
        # cannot be confirmed authentic. Fail-closed: refuse. Admitting an
        # unverifiable (and therefore attacker-writable) row would let it decide
        # who holds sovereignty. HC-11 / U6: the previous ``return True`` here
        # was a bypass -- "nothing to verify" is NOT "verified".
        return False
    if not isinstance(tag, str) or not tag:
        return False
    alg_name, sep, declared = tag.partition(_MAC_ALG_SEPARATOR)
    if not sep:
        # A bare digest states no algorithm. Since the tag column did not exist
        # before A3 there is no legitimate legacy shape to stay compatible with,
        # so this is an unverifiable tag rather than an old one.
        logger.error(
            "registry integrity tag %r carries no algorithm identifier; refusing "
            "it rather than assuming %s", tag, DEFAULT_MAC_ALG,
        )
        return False
    produce = MAC_ALGORITHMS.get(alg_name)
    if produce is None:
        logger.error(
            "registry integrity tag declares algorithm %r, which this build "
            "cannot verify; refusing it rather than assuming %s",
            alg_name, DEFAULT_MAC_ALG,
        )
        return False
    expected = f"{alg_name}{_MAC_ALG_SEPARATOR}" + produce(
        key.encode("utf-8"), _canonical_row(entry).encode("utf-8")
    )
    return hmac.compare_digest(tag, expected)


def _normalize_account_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """把一条账号树记录收敛成存储形状（缺省值集中在一处）。"""
    principal = str(record.get(FIELD_PRINCIPAL) or "").strip()
    parent = record.get(FIELD_PARENT_ID)
    stamp = utc_now().isoformat()
    try:
        depth = max(0, int(record.get(FIELD_DEPTH) or 0))
    except (TypeError, ValueError):
        depth = 0
    try:
        epoch = max(0, int(record.get(FIELD_SECURITY_EPOCH) or 0))
    except (TypeError, ValueError):
        epoch = 0
    try:
        attempts = max(0, int(record.get(FIELD_ACTIVATION_ATTEMPTS) or 0))
    except (TypeError, ValueError):
        attempts = 0
    return {
        FIELD_PRINCIPAL: principal,
        FIELD_PARENT_ID: str(parent).strip() if parent else None,
        FIELD_DEPTH: depth,
        FIELD_ROOT_ID: str(record.get(FIELD_ROOT_ID) or principal).strip() or principal,
        FIELD_PERMISSIONS: sorted(
            {str(p) for p in (record.get(FIELD_PERMISSIONS) or []) if str(p).strip()}
        ),
        FIELD_ALLOW_SUPERIOR_KEY_USE: bool(
            record.get(FIELD_ALLOW_SUPERIOR_KEY_USE, True)
        ),
        FIELD_KEY_AUTH_SOURCE: str(
            record.get(FIELD_KEY_AUTH_SOURCE) or "created_default"
        ),
        FIELD_ACCOUNT_STATUS: str(record.get(FIELD_ACCOUNT_STATUS) or "active"),
        FIELD_SECURITY_EPOCH: epoch,
        FIELD_ACTIVATION_HASH: str(record.get(FIELD_ACTIVATION_HASH) or ""),
        FIELD_ACTIVATION_EXPIRES_AT: (
            str(record.get(FIELD_ACTIVATION_EXPIRES_AT))
            if record.get(FIELD_ACTIVATION_EXPIRES_AT)
            else None
        ),
        FIELD_ACTIVATION_ATTEMPTS: attempts,
        FIELD_RECOVERY_HASH: str(record.get(FIELD_RECOVERY_HASH) or ""),
        FIELD_CREATED_AT: str(record.get(FIELD_CREATED_AT) or stamp),
        FIELD_UPDATED_AT: str(record.get(FIELD_UPDATED_AT) or stamp),
        FIELD_SOURCE: str(record.get(FIELD_SOURCE) or "runtime"),
    }


class JsonFileStore:
    """The original JSON seed file, unchanged in behaviour.

    Tolerates the two shapes the kernel already accepted -- a bare list, or
    ``{"humans": [...]}`` -- and refuses to invent one when the path is unset.
    """

    backend_name = BACKEND_FILE

    def __init__(self, location: str):
        self.location = location or ""
        #: Verdict of the most recent :meth:`load_all`, so a caller can tell
        #: "the store offered 3 rows and 1 was refused" without parsing logs.
        self.last_load_report: Dict[str, Any] = {
            "backend": BACKEND_FILE,
            "integrity_enforced": integrity_enforced(),
            "admitted": 0,
            "rejected": [],
        }
        #: Verdict of the most recent :meth:`load_accounts` (separate report so
        #: the pinned human-registry shape stays untouched).
        self.last_account_report: Dict[str, Any] = {
            "backend": BACKEND_FILE,
            "integrity_enforced": integrity_enforced(),
            "admitted": 0,
            "rejected": [],
        }

    def load_all(self, include_extended: bool = False) -> List[Dict[str, Any]]:
        """Return admitted rows.

        ``include_extended=False`` (the default) keeps the historical shape
        exactly -- ``principal`` / ``display_name`` / ``permissions`` /
        ``scope`` / ``registered_at`` -- because tests and operator tooling read
        that shape. The containment columns travel only when
        ``include_extended=True`` is asked for explicitly.

        When :data:`HUMAN_IDENTITIES_INTEGRITY_KEY_ENV` is configured, a row
        that does not authenticate against the key is **refused** (fail-closed)
        and listed in ``last_load_report["rejected"]``.
        """
        self.last_load_report = {
            "backend": BACKEND_FILE,
            "integrity_enforced": integrity_enforced(),
            "admitted": 0,
            "rejected": [],
        }
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

        key = integrity_key()
        kept: List[Dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                logger.error(
                    "skipping non-object entry in %r: %r", self.location, entry,
                )
                continue
            if not tag_matches(entry, key, entry.get(FIELD_INTEGRITY)):
                rejected = entry.get(FIELD_PRINCIPAL) or "<no principal>"
                self.last_load_report["rejected"].append(rejected)
                logger.error(
                    "REFUSING registry row %r in %r: the row does not "
                    "authenticate against %s (missing or mismatched %r tag). A "
                    "row that cannot be authenticated cannot decide who holds "
                    "sovereignty. Remedy: set that variable and re-register the "
                    "human with scripts/register_human_identity.py so the row "
                    "carries a valid tag; no human is recognised until then.",
                    rejected, self.location, HUMAN_IDENTITIES_INTEGRITY_KEY_ENV,
                    FIELD_INTEGRITY,
                )
                continue
            if include_extended:
                extended = dict(entry)
                extended[FIELD_TRUST_SCORE] = entry.get(FIELD_TRUST_SCORE)
                extended[FIELD_INTEGRITY_STATUS] = (
                    INTEGRITY_VERIFIED if key else INTEGRITY_UNVERIFIED
                )
                kept.append(extended)
            else:
                kept.append(entry)
        self.last_load_report["admitted"] = len(kept)
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
        trust = _coerce_trust_number(entry.get(FIELD_TRUST_SCORE))
        if trust is not None:
            stored[FIELD_TRUST_SCORE] = trust
        key = integrity_key()
        if key:
            # Stamped with the key configured *now*. A row written before the
            # key existed will be refused at load time until it is
            # re-registered -- which is the honest outcome: an unauthenticated
            # row cannot be retroactively authenticated.
            stored[FIELD_INTEGRITY] = compute_row_tag(stored, key)
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

    # ---- 账号树 -------------------------------------------------------

    def load_accounts(self) -> List[Dict[str, Any]]:
        """返回通过完整性校验的账号树行；未配置密钥时全部拒收（fail-closed）。"""
        self.last_account_report = {
            "backend": BACKEND_FILE,
            "integrity_enforced": integrity_enforced(),
            "admitted": 0,
            "rejected": [],
        }
        if not self.location:
            return []
        document = self._read_document()
        raw = document.get("accounts") or {}
        if not isinstance(raw, dict):
            return []
        key = integrity_key()
        admitted: List[Dict[str, Any]] = []
        for principal, record in raw.items():
            if not isinstance(record, dict):
                self.last_account_report["rejected"].append(
                    {"principal": str(principal), "reason": "not_object"}
                )
                continue
            normalized = _normalize_account_record({**record, FIELD_PRINCIPAL: principal})
            tag = record.get(FIELD_INTEGRITY)
            if not account_tag_matches(normalized, key, tag):
                self.last_account_report["rejected"].append(
                    {"principal": normalized[FIELD_PRINCIPAL], "reason": "integrity"}
                )
                logger.warning(
                    "account tree row %r failed integrity verification; refusing it",
                    normalized[FIELD_PRINCIPAL],
                )
                continue
            admitted.append(normalized)
        self.last_account_report["admitted"] = len(admitted)
        return admitted

    def upsert_account(self, record: Dict[str, Any]) -> bool:
        if not self.location:
            return False
        document = self._read_document()
        accounts = document.get("accounts")
        if not isinstance(accounts, dict):
            accounts = {}
        normalized = _normalize_account_record(record)
        principal = normalized[FIELD_PRINCIPAL]
        existing = accounts.get(principal)
        if isinstance(existing, dict) and existing.get(FIELD_CREATED_AT):
            normalized[FIELD_CREATED_AT] = existing[FIELD_CREATED_AT]
        if isinstance(existing, dict):
            normalized[FIELD_SOURCE] = existing.get(FIELD_SOURCE) or normalized[FIELD_SOURCE]
        key = integrity_key()
        if key:
            normalized[FIELD_INTEGRITY] = compute_account_tag(normalized, key)
        else:
            normalized.pop(FIELD_INTEGRITY, None)
        accounts[principal] = normalized
        document["accounts"] = accounts
        return self._write_document(document)

    def remove_account(self, principal: str) -> bool:
        if not self.location:
            return False
        document = self._read_document()
        accounts = document.get("accounts")
        if not isinstance(accounts, dict) or principal not in accounts:
            return False
        accounts.pop(principal, None)
        document["accounts"] = accounts
        return self._write_document(document)

    def drop_accounts(self) -> bool:
        """回滚：移除账号树（身份行保持原样）。"""
        if not self.location:
            return False
        document = self._read_document()
        if "accounts" in document:
            document.pop("accounts", None)
            return self._write_document(document)
        return False

    def record_import_receipt(self, bundle_id: str) -> bool:
        if not self.location or not bundle_id:
            return False
        document = self._read_document()
        receipts = document.get("import_receipts")
        if not isinstance(receipts, dict):
            receipts = {}
        if bundle_id in receipts:
            return False
        receipts[bundle_id] = utc_now().isoformat()
        document["import_receipts"] = receipts
        return self._write_document(document)

    def has_import_receipt(self, bundle_id: str) -> bool:
        if not self.location or not bundle_id:
            return False
        document = self._read_document()
        receipts = document.get("import_receipts")
        return bool(isinstance(receipts, dict) and bundle_id in receipts)


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
        #: Verdict of the most recent :meth:`load_all`; see the file backend.
        self.last_load_report: Dict[str, Any] = {
            "backend": BACKEND_SQLITE,
            "integrity_enforced": integrity_enforced(),
            "admitted": 0,
            "rejected": [],
        }
        self.last_account_report: Dict[str, Any] = {
            "backend": BACKEND_SQLITE,
            "integrity_enforced": integrity_enforced(),
            "admitted": 0,
            "rejected": [],
        }

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
        is established removes that mixed-mode connection.

        It does **not**, on its own, make concurrent writes lossless: measured
        2026-09-15, 24 concurrent writers still lost 1-4 registrations in
        ~25-50% of runs, each raising ``attempt to write a readonly database``
        from a connection opened *after* this switch. See ``_write_with_retry``,
        which is what actually closes that hole.
        """
        if self._schema_ready:
            return
        with self._lock:
            if self._schema_ready:
                return
            connection = self._connect()
            try:
                connection.execute(_SQLITE_SCHEMA)
                # 账号树与导入回执：独立的表，旧库升级只是"建表 + 派生根节点"，
                # 不改写既有 human_identities 行（回滚时删表即可，不丢身份）。
                connection.execute(_SQLITE_ACCOUNT_SCHEMA)
                connection.execute(_SQLITE_IMPORT_RECEIPT_SCHEMA)
                # In-place migration for databases created before the
                # containment columns existed. ``CREATE TABLE IF NOT EXISTS``
                # is a no-op on an existing table, so these ALTERs are what
                # actually carry a legacy registration database forward.
                existing_columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(human_identities)"
                    ).fetchall()
                }
                for column, column_type in _SQLITE_ADDED_COLUMNS:
                    if column not in existing_columns:
                        connection.execute(
                            f"ALTER TABLE human_identities ADD COLUMN "
                            f"{column} {column_type}"
                        )
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

    def load_all(self, include_extended: bool = False) -> List[Dict[str, Any]]:
        """Return admitted rows; see :meth:`JsonFileStore.load_all` for the
        ``include_extended`` contract and the integrity semantics."""
        self.last_load_report = {
            "backend": BACKEND_SQLITE,
            "integrity_enforced": integrity_enforced(),
            "admitted": 0,
            "rejected": [],
        }
        if not os.path.exists(self.location):
            # Read paths never create a store: booting the kernel must not
            # manufacture an empty registration database.
            return []
        try:
            with self._session() as connection:
                rows = connection.execute(
                    "SELECT principal, display_name, permissions, scope, "
                    "registered_at, trust_score, integrity "
                    "FROM human_identities ORDER BY principal"
                ).fetchall()
        except Exception as exc:  # noqa: BLE001 - config problem, not a crash
            logger.error(
                "could not read %s (%r): %s -- no human identity loaded",
                HUMAN_IDENTITIES_DB_ENV, self.location, exc,
            )
            return []

        key = integrity_key()
        kept: List[Dict[str, Any]] = []
        for row in rows:
            full = {
                FIELD_PRINCIPAL: row["principal"],
                FIELD_DISPLAY_NAME: row["display_name"],
                FIELD_PERMISSIONS: _decode_permissions(row["permissions"]),
                FIELD_SCOPE: row["scope"],
                FIELD_REGISTERED_AT: row["registered_at"],
                FIELD_TRUST_SCORE: row["trust_score"],
            }
            if not tag_matches(full, key, row["integrity"]):
                self.last_load_report["rejected"].append(row["principal"])
                logger.error(
                    "REFUSING registry row %r in %r: the row does not "
                    "authenticate against %s (missing or mismatched %r tag). A "
                    "row that cannot be authenticated cannot decide who holds "
                    "sovereignty. Remedy: set that variable and re-register the "
                    "human with scripts/register_human_identity.py so the row "
                    "carries a valid tag; no human is recognised until then.",
                    row["principal"], self.location,
                    HUMAN_IDENTITIES_INTEGRITY_KEY_ENV, FIELD_INTEGRITY,
                )
                continue
            if include_extended:
                full[FIELD_INTEGRITY_STATUS] = (
                    INTEGRITY_VERIFIED if key else INTEGRITY_UNVERIFIED
                )
                kept.append(full)
            else:
                kept.append({
                    FIELD_PRINCIPAL: full[FIELD_PRINCIPAL],
                    FIELD_DISPLAY_NAME: full[FIELD_DISPLAY_NAME],
                    FIELD_PERMISSIONS: full[FIELD_PERMISSIONS],
                    FIELD_SCOPE: full[FIELD_SCOPE],
                    FIELD_REGISTERED_AT: full[FIELD_REGISTERED_AT],
                })
        self.last_load_report["admitted"] = len(kept)
        return kept

    def _write_with_retry(
        self, statement: Callable[[sqlite3.Connection], Any]
    ) -> Tuple[Any, Optional[BaseException]]:
        """Run one write statement, retrying transient SQLite errors.

        Returns ``(result, None)`` on success and ``(None, error)`` if the
        write never went through. ``sqlite3.OperationalError`` is retried: the
        ``attempt to write a readonly database`` failure under concurrent WAL
        writers is transient, and a bounded retry is what makes the write
        lossless. Any other exception (corrupt file, bad schema) is permanent
        and is reported immediately -- retrying it would only waste time.
        """
        last: Optional[BaseException] = None
        for attempt in range(_SQLITE_WRITE_ATTEMPTS):
            try:
                with self._session() as connection:
                    return statement(connection), None
            except sqlite3.OperationalError as exc:  # transient under concurrency
                last = exc
                if attempt + 1 < _SQLITE_WRITE_ATTEMPTS:
                    time.sleep(_SQLITE_WRITE_RETRY_DELAY)
            except Exception as exc:  # noqa: BLE001 - permanent config problem
                return None, exc
        return None, last

    def upsert(self, entry: Dict[str, Any]) -> bool:
        principal = str(entry.get(FIELD_PRINCIPAL) or "").strip()
        if not principal:
            return False
        trust = _coerce_trust_number(entry.get(FIELD_TRUST_SCORE))
        tag_input = {
            FIELD_PRINCIPAL: principal,
            FIELD_DISPLAY_NAME: entry.get(FIELD_DISPLAY_NAME),
            FIELD_PERMISSIONS: sorted(set(entry.get(FIELD_PERMISSIONS) or [])),
            FIELD_SCOPE: entry.get(FIELD_SCOPE) or "L0",
            FIELD_TRUST_SCORE: trust,
        }
        key = integrity_key()
        params = (
            principal,
            entry.get(FIELD_DISPLAY_NAME),
            json.dumps(tag_input[FIELD_PERMISSIONS]),
            tag_input[FIELD_SCOPE],
            _stamp_registered_at(entry),
            entry.get(FIELD_SOURCE) or "",
            trust,
            compute_row_tag(tag_input, key) if key else None,
        )
        _, error = self._write_with_retry(
            lambda connection: connection.execute(_SQLITE_UPSERT, params)
        )
        if error is not None:
            logger.error("could not write %r: %s", self.location, error)
            return False
        return True

    def remove(self, principal: str) -> bool:
        if not os.path.exists(self.location):
            return False
        rowcount, error = self._write_with_retry(
            lambda connection: connection.execute(
                "DELETE FROM human_identities WHERE principal = ?", (principal,)
            ).rowcount
        )
        if error is not None:
            logger.error("could not update %r: %s", self.location, error)
            return False
        return rowcount > 0

    # ---- 账号树 -------------------------------------------------------

    def load_accounts(self) -> List[Dict[str, Any]]:
        self.last_account_report = {
            "backend": BACKEND_SQLITE,
            "integrity_enforced": integrity_enforced(),
            "admitted": 0,
            "rejected": [],
        }
        if not os.path.exists(self.location):
            return []
        try:
            with self._session() as connection:
                rows = connection.execute(
                    "SELECT principal, parent_id, depth, root_id, permissions, "
                    "allow_superior_key_use, key_auth_source, status, "
                    "security_epoch, activation_hash, activation_expires_at, "
                    "activation_attempts, recovery_hash, created_at, updated_at, "
                    "source, integrity FROM account_tree ORDER BY principal"
                ).fetchall()
        except Exception as exc:  # noqa: BLE001 - config problem, not a crash
            logger.error(
                "could not read account tree from %r: %s -- no member loaded",
                self.location, exc,
            )
            return []
        key = integrity_key()
        admitted: List[Dict[str, Any]] = []
        for row in rows:
            record = _normalize_account_record(
                {
                    FIELD_PRINCIPAL: row["principal"],
                    FIELD_PARENT_ID: row["parent_id"],
                    FIELD_DEPTH: row["depth"],
                    FIELD_ROOT_ID: row["root_id"],
                    FIELD_PERMISSIONS: _decode_permissions(row["permissions"]),
                    FIELD_ALLOW_SUPERIOR_KEY_USE: bool(row["allow_superior_key_use"]),
                    FIELD_KEY_AUTH_SOURCE: row["key_auth_source"],
                    FIELD_ACCOUNT_STATUS: row["status"],
                    FIELD_SECURITY_EPOCH: row["security_epoch"],
                    FIELD_ACTIVATION_HASH: row["activation_hash"],
                    FIELD_ACTIVATION_EXPIRES_AT: row["activation_expires_at"],
                    FIELD_ACTIVATION_ATTEMPTS: row["activation_attempts"],
                    FIELD_RECOVERY_HASH: row["recovery_hash"],
                    FIELD_CREATED_AT: row["created_at"],
                    FIELD_UPDATED_AT: row["updated_at"],
                    FIELD_SOURCE: row["source"],
                }
            )
            if not account_tag_matches(record, key, row["integrity"]):
                self.last_account_report["rejected"].append(
                    {"principal": record[FIELD_PRINCIPAL], "reason": "integrity"}
                )
                logger.error(
                    "REFUSING account tree row %r: it does not authenticate "
                    "against %s",
                    record[FIELD_PRINCIPAL], HUMAN_IDENTITIES_INTEGRITY_KEY_ENV,
                )
                continue
            admitted.append(record)
        self.last_account_report["admitted"] = len(admitted)
        return admitted

    def upsert_account(self, record: Dict[str, Any]) -> bool:
        normalized = _normalize_account_record(record)
        principal = normalized[FIELD_PRINCIPAL]
        if not principal:
            return False
        key = integrity_key()
        tag = compute_account_tag(normalized, key) if key else None
        existing_created = self._account_created_at(principal)
        if existing_created:
            normalized[FIELD_CREATED_AT] = existing_created
        params = (
            principal,
            normalized[FIELD_PARENT_ID],
            normalized[FIELD_DEPTH],
            normalized[FIELD_ROOT_ID],
            json.dumps(normalized[FIELD_PERMISSIONS]),
            _coerce_bool_int(normalized[FIELD_ALLOW_SUPERIOR_KEY_USE]),
            normalized[FIELD_KEY_AUTH_SOURCE],
            normalized[FIELD_ACCOUNT_STATUS],
            normalized[FIELD_SECURITY_EPOCH],
            normalized[FIELD_ACTIVATION_HASH],
            normalized[FIELD_ACTIVATION_EXPIRES_AT],
            normalized[FIELD_ACTIVATION_ATTEMPTS],
            normalized[FIELD_RECOVERY_HASH],
            normalized[FIELD_CREATED_AT],
            normalized[FIELD_UPDATED_AT],
            normalized[FIELD_SOURCE],
            tag,
        )
        _, error = self._write_with_retry(
            lambda connection: connection.execute(_SQLITE_ACCOUNT_UPSERT, params)
        )
        if error is not None:
            logger.error("could not write account tree to %r: %s", self.location, error)
            return False
        return True

    def _account_created_at(self, principal: str) -> str:
        if not os.path.exists(self.location):
            return ""
        try:
            with self._session() as connection:
                row = connection.execute(
                    "SELECT created_at FROM account_tree WHERE principal = ?",
                    (principal,),
                ).fetchone()
        except Exception:  # noqa: BLE001 - best-effort bookkeeping
            return ""
        return str(row["created_at"]) if row else ""

    def remove_account(self, principal: str) -> bool:
        if not os.path.exists(self.location):
            return False
        rowcount, error = self._write_with_retry(
            lambda connection: connection.execute(
                "DELETE FROM account_tree WHERE principal = ?", (principal,)
            ).rowcount
        )
        if error is not None:
            logger.error("could not update account tree: %s", error)
            return False
        return rowcount > 0

    def drop_accounts(self) -> bool:
        """回滚：删除账号树与导入回执表；身份行保持原样。"""
        if not os.path.exists(self.location):
            return False
        _, error = self._write_with_retry(
            lambda connection: (
                connection.execute("DROP TABLE IF EXISTS account_tree"),
                connection.execute("DROP TABLE IF EXISTS account_import_receipts"),
            )
        )
        if error is not None:
            logger.error("could not drop account tree: %s", error)
            return False
        self._schema_ready = False
        return True

    def record_import_receipt(self, bundle_id: str) -> bool:
        if not bundle_id:
            return False
        rowcount, error = self._write_with_retry(
            lambda connection: connection.execute(
                "INSERT OR IGNORE INTO account_import_receipts "
                "(bundle_id, imported_at) VALUES (?, ?)",
                (bundle_id, utc_now().isoformat()),
            ).rowcount
        )
        if error is not None:
            logger.error("could not record import receipt: %s", error)
            return False
        return rowcount > 0

    def has_import_receipt(self, bundle_id: str) -> bool:
        if not bundle_id or not os.path.exists(self.location):
            return False
        try:
            with self._session() as connection:
                row = connection.execute(
                    "SELECT 1 FROM account_import_receipts WHERE bundle_id = ?",
                    (bundle_id,),
                ).fetchone()
        except Exception:  # noqa: BLE001 - table may not exist yet
            return False
        return row is not None


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
    """Report which store is in use -- for boot logs and operator tooling.

    The three keys here are a **pinned public shape**
    (``tests/kernels/identity/test_human_identity_persistence.py`` compares it
    with ``==``), so new facts go in :func:`describe_registry_integrity` rather
    than being appended here.
    """
    resolved = store if store is not None else resolve_human_identity_store()
    return {
        "backend": resolved.backend_name,
        "location": resolved.location,
        "configured": bool(resolved.location),
    }


def describe_registry_integrity(
    store: Optional[JsonFileStore | SqliteHumanIdentityStore] = None,
) -> Dict[str, Any]:
    """Report whether registry rows must authenticate, and the last verdict.

    PHASE 3.6 / A3: "this registry is not authenticated" must be a fact an
    operator can *read off*, not a property they have to infer from the absence
    of a warning. A sibling of :func:`describe_human_identity_store` because
    that function's shape is pinned by tests and read by tooling.
    """
    resolved = store if store is not None else resolve_human_identity_store()
    last = getattr(resolved, "last_load_report", None)
    admitted = (last or {}).get("admitted", 0) if isinstance(last, dict) else 0
    report: Dict[str, Any] = {
        "backend": resolved.backend_name,
        "location": resolved.location,
        "integrity_enforced": integrity_enforced(),
        "integrity_state": registry_integrity_state(
            admitted=admitted, configured=bool(resolved.location)
        ),
        "integrity_key_env": HUMAN_IDENTITIES_INTEGRITY_KEY_ENV,
    }
    if isinstance(last, dict):
        report["last_load"] = last
    return report
