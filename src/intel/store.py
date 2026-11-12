"""SQLite 持久化：OSI 独立表。

只新建本模块自己的 ``osi_*`` 表，不触碰任何既有表或迁移文件。
风格对齐 ``src/ai/personal_context.py`` / ``src/kernels/memory/store.py``：
SQLite + WAL + 线程锁 + 环境变量可配路径。

默认库文件位于 ``data/intel/osi_intel.db``；``data/`` 已被 ``.gitignore``
忽略，因此情报数据不会进入代码库。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Any

from src.intel.models import (
    AdvisoryRecord,
    Candidate,
    ChangeRecord,
    GapRecord,
    LicenseRecord,
    ProposalRecord,
    ProvenanceRecord,
    Snapshot,
)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_DB_PATH = os.path.join(_PROJECT_ROOT, "data", "intel", "osi_intel.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS osi_candidates (
    repo_url           TEXT PRIMARY KEY,
    name               TEXT NOT NULL,
    language           TEXT,
    stars              INTEGER,
    last_commit_at     TEXT,
    first_seen_at      TEXT NOT NULL,
    last_assessed_at   TEXT NOT NULL,
    description        TEXT,
    topics             TEXT,
    last_status        TEXT NOT NULL,
    last_error         TEXT
);

CREATE TABLE IF NOT EXISTS osi_snapshots (
    id                         INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_url                   TEXT NOT NULL,
    version                    TEXT,
    tag                        TEXT,
    commit_sha                 TEXT,
    fetched_at                 TEXT NOT NULL,
    source_url                 TEXT NOT NULL,
    license_raw_sha256         TEXT,
    dependency_manifest_sha256 TEXT,
    fetch_status               TEXT NOT NULL,
    details                    TEXT,
    error_reason               TEXT
);
CREATE INDEX IF NOT EXISTS idx_osi_snapshots_repo ON osi_snapshots(repo_url);

CREATE TABLE IF NOT EXISTS osi_licenses (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_url       TEXT NOT NULL,
    license_id     TEXT NOT NULL,
    source_url     TEXT NOT NULL,
    raw_sha256     TEXT,
    verdict        TEXT NOT NULL,
    category       TEXT NOT NULL,
    allowed        INTEGER NOT NULL,
    reason         TEXT,
    requires_note  INTEGER NOT NULL DEFAULT 0,
    captured_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_osi_licenses_repo ON osi_licenses(repo_url);

CREATE TABLE IF NOT EXISTS osi_advisories (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_url        TEXT NOT NULL,
    source          TEXT NOT NULL,
    advisory_id     TEXT,
    affected_range  TEXT,
    severity        TEXT,
    ecosystem       TEXT,
    package         TEXT,
    note            TEXT,
    source_url      TEXT NOT NULL,
    fetch_status    TEXT NOT NULL,
    fetched_at      TEXT NOT NULL,
    error_reason    TEXT
);
CREATE INDEX IF NOT EXISTS idx_osi_advisories_repo ON osi_advisories(repo_url);

CREATE TABLE IF NOT EXISTS osi_gap_analysis (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_url     TEXT NOT NULL,
    capability   TEXT NOT NULL,
    status       TEXT NOT NULL,
    file_paths   TEXT,
    note         TEXT,
    assessed_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_osi_gap_repo ON osi_gap_analysis(repo_url);

CREATE TABLE IF NOT EXISTS osi_proposals (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_url           TEXT NOT NULL,
    strategy           TEXT NOT NULL,
    effort             TEXT NOT NULL,
    risk               TEXT NOT NULL,
    license_conclusion TEXT NOT NULL,
    adoptable          INTEGER NOT NULL,
    summary            TEXT,
    disclaimer         TEXT NOT NULL,
    created_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_osi_proposals_repo ON osi_proposals(repo_url);

CREATE TABLE IF NOT EXISTS osi_provenance (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_url       TEXT NOT NULL,
    source         TEXT NOT NULL,
    source_url     TEXT NOT NULL,
    license_id     TEXT,
    version        TEXT,
    change_history TEXT,
    captured_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_osi_provenance_repo ON osi_provenance(repo_url);

CREATE TABLE IF NOT EXISTS osi_changes (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_url           TEXT NOT NULL,
    source             TEXT NOT NULL,
    source_url         TEXT NOT NULL,
    previous_version   TEXT,
    current_version    TEXT,
    previous_commit    TEXT,
    current_commit     TEXT,
    note               TEXT,
    detected_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_osi_changes_repo ON osi_changes(repo_url);
"""

#: 阶段二给已有库补列用；只动本模块自己的 ``osi_advisories`` 表。
_ADVISORY_ADDED_COLUMNS = {
    "ecosystem": "TEXT",
    "package": "TEXT",
    "note": "TEXT",
}


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    """幂等地给已存在的旧库补列（不改其它表）。"""
    existing = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, declaration in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")


class IntelStore:
    """``osi_*`` 表的读写入口。"""

    def __init__(self, db_path: str | None = None):
        if db_path is None:
            from ..common.data_paths import resolve_store_path

            db_path = resolve_store_path(
                os.path.join("intel", "osi_intel.db"),
                DEFAULT_DB_PATH,
                env_var="OSI_DB_PATH",
            )
        self._db_path = db_path
        self._lock = threading.RLock()
        if db_path != ":memory:":
            parent = os.path.dirname(os.path.abspath(db_path))
            os.makedirs(parent, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    # ------------------------------------------------------------------ #
    # 基础设施
    # ------------------------------------------------------------------ #
    def _init_db(self) -> None:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(_SCHEMA)
        _ensure_columns(conn, "osi_advisories", _ADVISORY_ADDED_COLUMNS)
        conn.commit()
        self._conn = conn

    @property
    def db_path(self) -> str:
        return self._db_path

    def _fetch_rows(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            cursor = self._conn.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # ------------------------------------------------------------------ #
    # osi_candidates
    # ------------------------------------------------------------------ #
    def upsert_candidate(self, candidate: Candidate) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO osi_candidates (
                    repo_url, name, language, stars, last_commit_at,
                    first_seen_at, last_assessed_at, description, topics,
                    last_status, last_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repo_url) DO UPDATE SET
                    name = excluded.name,
                    language = excluded.language,
                    stars = excluded.stars,
                    last_commit_at = excluded.last_commit_at,
                    last_assessed_at = excluded.last_assessed_at,
                    description = excluded.description,
                    topics = excluded.topics,
                    last_status = excluded.last_status,
                    last_error = excluded.last_error
                """,
                (
                    candidate.repo_url,
                    candidate.name,
                    candidate.language,
                    candidate.stars,
                    candidate.last_commit_at,
                    candidate.first_seen_at,
                    candidate.last_assessed_at,
                    candidate.description,
                    json.dumps(candidate.topics, ensure_ascii=False),
                    candidate.last_status,
                    candidate.last_error,
                ),
            )
            self._conn.commit()

    def get_candidate(self, repo_url: str) -> dict[str, Any] | None:
        rows = self._fetch_rows(
            "SELECT * FROM osi_candidates WHERE repo_url = ?", (repo_url,)
        )
        return rows[0] if rows else None

    def list_candidates(self) -> list[dict[str, Any]]:
        return self._fetch_rows("SELECT * FROM osi_candidates ORDER BY repo_url")

    # ------------------------------------------------------------------ #
    # osi_snapshots
    # ------------------------------------------------------------------ #
    def insert_snapshot(self, snapshot: Snapshot) -> int:
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO osi_snapshots (
                    repo_url, version, tag, commit_sha, fetched_at, source_url,
                    license_raw_sha256, dependency_manifest_sha256,
                    fetch_status, details, error_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.repo_url,
                    snapshot.version,
                    snapshot.tag,
                    snapshot.commit_sha,
                    snapshot.fetched_at,
                    snapshot.source_url,
                    snapshot.license_raw_sha256,
                    snapshot.dependency_manifest_sha256,
                    snapshot.fetch_status,
                    json.dumps(snapshot.details, ensure_ascii=False),
                    snapshot.error_reason,
                ),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def list_snapshots(
        self, repo_url: str | None = None, limit: int | None = None
    ) -> list[dict[str, Any]]:
        if repo_url is None:
            sql = "SELECT * FROM osi_snapshots ORDER BY id"
        else:
            sql = "SELECT * FROM osi_snapshots WHERE repo_url = ? ORDER BY id"
        rows = self._fetch_rows(sql, (repo_url,) if repo_url else ())
        return rows[-limit:] if limit else rows

    def latest_snapshot(self, repo_url: str) -> dict[str, Any] | None:
        rows = self._fetch_rows(
            "SELECT * FROM osi_snapshots WHERE repo_url = ? ORDER BY id DESC LIMIT 1",
            (repo_url,),
        )
        return rows[0] if rows else None

    def latest_comparable_snapshot(self, repo_url: str) -> dict[str, Any] | None:
        """最近一次**可用于版本比较**的快照。

        抓取失败（``not_fetched`` / ``partial`` 且没有版本与提交）的行不能作为
        比较基线，否则一次断网就会被误判成「版本从 v1.0.0 变成 None」。
        """
        rows = self._fetch_rows(
            """
            SELECT * FROM osi_snapshots
            WHERE repo_url = ?
              AND fetch_status != 'not_fetched'
              AND (version IS NOT NULL OR commit_sha IS NOT NULL)
            ORDER BY id DESC LIMIT 1
            """,
            (repo_url,),
        )
        return rows[0] if rows else None

    # ------------------------------------------------------------------ #
    # osi_licenses
    # ------------------------------------------------------------------ #
    def insert_license(self, record: LicenseRecord) -> int:
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO osi_licenses (
                    repo_url, license_id, source_url, raw_sha256, verdict,
                    category, allowed, reason, requires_note, captured_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.repo_url,
                    record.license_id,
                    record.source_url,
                    record.raw_sha256,
                    record.verdict,
                    record.category,
                    1 if record.allowed else 0,
                    record.reason,
                    1 if record.requires_note else 0,
                    record.captured_at,
                ),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def list_licenses(self, repo_url: str | None = None) -> list[dict[str, Any]]:
        if repo_url is None:
            return self._fetch_rows("SELECT * FROM osi_licenses ORDER BY id")
        return self._fetch_rows(
            "SELECT * FROM osi_licenses WHERE repo_url = ? ORDER BY id", (repo_url,)
        )

    # ------------------------------------------------------------------ #
    # osi_advisories
    # ------------------------------------------------------------------ #
    def insert_advisory(self, record: AdvisoryRecord) -> int:
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO osi_advisories (
                    repo_url, source, advisory_id, affected_range, severity,
                    ecosystem, package, note, source_url, fetch_status,
                    fetched_at, error_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.repo_url,
                    record.source,
                    record.advisory_id,
                    record.affected_range,
                    record.severity,
                    record.ecosystem,
                    record.package,
                    record.note,
                    record.source_url,
                    record.fetch_status,
                    record.fetched_at,
                    record.error_reason,
                ),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def list_advisories(self, repo_url: str | None = None) -> list[dict[str, Any]]:
        if repo_url is None:
            return self._fetch_rows("SELECT * FROM osi_advisories ORDER BY id")
        return self._fetch_rows(
            "SELECT * FROM osi_advisories WHERE repo_url = ? ORDER BY id", (repo_url,)
        )

    # ------------------------------------------------------------------ #
    # osi_gap_analysis
    # ------------------------------------------------------------------ #
    def insert_gap(self, record: GapRecord) -> int:
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO osi_gap_analysis (
                    repo_url, capability, status, file_paths, note, assessed_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record.repo_url,
                    record.capability,
                    record.status,
                    json.dumps(record.file_paths, ensure_ascii=False),
                    record.note,
                    record.assessed_at,
                ),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def list_gaps(self, repo_url: str | None = None) -> list[dict[str, Any]]:
        if repo_url is None:
            return self._fetch_rows("SELECT * FROM osi_gap_analysis ORDER BY id")
        return self._fetch_rows(
            "SELECT * FROM osi_gap_analysis WHERE repo_url = ? ORDER BY id", (repo_url,)
        )

    # ------------------------------------------------------------------ #
    # osi_proposals
    # ------------------------------------------------------------------ #
    def insert_proposal(self, record: ProposalRecord) -> int:
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO osi_proposals (
                    repo_url, strategy, effort, risk, license_conclusion,
                    adoptable, summary, disclaimer, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.repo_url,
                    record.strategy,
                    record.effort,
                    record.risk,
                    record.license_conclusion,
                    1 if record.adoptable else 0,
                    record.summary,
                    record.disclaimer,
                    record.created_at,
                ),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def list_proposals(self, repo_url: str | None = None) -> list[dict[str, Any]]:
        if repo_url is None:
            return self._fetch_rows("SELECT * FROM osi_proposals ORDER BY id")
        return self._fetch_rows(
            "SELECT * FROM osi_proposals WHERE repo_url = ? ORDER BY id", (repo_url,)
        )

    # ------------------------------------------------------------------ #
    # osi_provenance
    # ------------------------------------------------------------------ #
    def insert_provenance(self, record: ProvenanceRecord) -> int:
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO osi_provenance (
                    repo_url, source, source_url, license_id, version,
                    change_history, captured_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.repo_url,
                    record.source,
                    record.source_url,
                    record.license_id,
                    record.version,
                    json.dumps(record.change_history, ensure_ascii=False),
                    record.captured_at,
                ),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def list_provenance(self, repo_url: str | None = None) -> list[dict[str, Any]]:
        if repo_url is None:
            return self._fetch_rows("SELECT * FROM osi_provenance ORDER BY id")
        return self._fetch_rows(
            "SELECT * FROM osi_provenance WHERE repo_url = ? ORDER BY id", (repo_url,)
        )

    # ------------------------------------------------------------------ #
    # osi_changes
    # ------------------------------------------------------------------ #
    def insert_change(self, record: ChangeRecord) -> int:
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO osi_changes (
                    repo_url, source, source_url, previous_version, current_version,
                    previous_commit, current_commit, note, detected_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.repo_url,
                    record.source,
                    record.source_url,
                    record.previous_version,
                    record.current_version,
                    record.previous_commit,
                    record.current_commit,
                    record.note,
                    record.detected_at,
                ),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def list_changes(self, repo_url: str | None = None) -> list[dict[str, Any]]:
        if repo_url is None:
            return self._fetch_rows("SELECT * FROM osi_changes ORDER BY id")
        return self._fetch_rows(
            "SELECT * FROM osi_changes WHERE repo_url = ? ORDER BY id", (repo_url,)
        )

    def latest_change(self, repo_url: str) -> dict[str, Any] | None:
        rows = self._fetch_rows(
            "SELECT * FROM osi_changes WHERE repo_url = ? ORDER BY id DESC LIMIT 1",
            (repo_url,),
        )
        return rows[0] if rows else None

    # ------------------------------------------------------------------ #
    # 只读诊断
    # ------------------------------------------------------------------ #
    def table_names(self) -> list[str]:
        rows = self._fetch_rows(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        )
        return [str(row["name"]) for row in rows]
