"""Persistent, queryable upgrade ledger.

Every check/download/apply/rollback is written to SQLite, so the answer to
"what happened, from which version to which version, and why did it fail?"
survives a process restart. Details are JSON; no secrets are stored.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS upgrade_records (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    from_version TEXT,
    to_version TEXT,
    reason TEXT,
    detail_json TEXT,
    backup_id TEXT,
    artifact_path TEXT
);
CREATE INDEX IF NOT EXISTS ix_upgrade_records_created
    ON upgrade_records(created_at DESC);
CREATE INDEX IF NOT EXISTS ix_upgrade_records_kind
    ON upgrade_records(kind, status);
"""


class UpgradeLedger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def record(
        self,
        *,
        kind: str,
        status: str,
        from_version: Optional[str] = None,
        to_version: Optional[str] = None,
        reason: str = "",
        detail: Optional[Dict[str, Any]] = None,
        backup_id: Optional[str] = None,
        artifact_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        record_id = uuid.uuid4().hex
        created_at = datetime.now(timezone.utc).isoformat()
        payload = json.dumps(detail or {}, ensure_ascii=False, sort_keys=True)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO upgrade_records
                    (id, created_at, kind, status, from_version, to_version,
                     reason, detail_json, backup_id, artifact_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    created_at,
                    kind,
                    status,
                    from_version,
                    to_version,
                    reason,
                    payload,
                    backup_id,
                    artifact_path,
                ),
            )
            self._conn.commit()
        return self.get(record_id) or {}

    def get(self, record_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM upgrade_records WHERE id = ?", (record_id,)
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def list_records(
        self,
        *,
        kind: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        clauses: List[str] = []
        params: List[Any] = []
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = (
            f"SELECT * FROM upgrade_records {where} "
            "ORDER BY created_at DESC LIMIT ?"
        )
        params.append(max(1, int(limit)))
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def latest_successful_apply(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM upgrade_records
                WHERE kind = 'apply' AND status = 'completed'
                ORDER BY created_at DESC LIMIT 1
                """
            ).fetchone()
        return self._row_to_dict(row) if row else None

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        result = dict(row)
        try:
            result["detail"] = json.loads(result.pop("detail_json") or "{}")
        except json.JSONDecodeError:
            result["detail"] = {}
        return result
