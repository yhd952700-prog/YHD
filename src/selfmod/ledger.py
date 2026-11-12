"""Persistent proposal/event store for controlled self-modification."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_COLUMNS = (
    "updated_at",
    "status",
    "reason",
    "approver",
    "approved_at",
    "token_hash",
    "token_stage",
    "test_summary_json",
    "backup_dir",
    "applied_at",
    "rolled_back_at",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS selfmod_proposals (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    title TEXT NOT NULL,
    rationale TEXT NOT NULL,
    status TEXT NOT NULL,
    base_version TEXT,
    diff_text TEXT NOT NULL,
    edits_json TEXT NOT NULL,
    base_hashes_json TEXT NOT NULL,
    test_command_json TEXT,
    reason TEXT NOT NULL DEFAULT '',
    approver TEXT NOT NULL DEFAULT '',
    approved_at TEXT,
    token_hash TEXT,
    token_stage TEXT,
    test_summary_json TEXT NOT NULL DEFAULT '{}',
    backup_dir TEXT,
    applied_at TEXT,
    rolled_back_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_selfmod_created ON selfmod_proposals(created_at DESC);
CREATE TABLE IF NOT EXISTS selfmod_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT,
    detail_json TEXT NOT NULL DEFAULT '{}'
);
"""


class SelfModLedger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def create(
        self,
        *,
        proposal_id: str,
        title: str,
        rationale: str,
        status: str,
        base_version: Optional[str],
        diff_text: str,
        edits: List[Dict[str, Any]],
        base_hashes: Dict[str, Optional[str]],
        test_command: Optional[List[str]],
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO selfmod_proposals
                    (id, created_at, updated_at, title, rationale, status,
                     base_version, diff_text, edits_json, base_hashes_json,
                     test_command_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal_id,
                    now,
                    now,
                    title,
                    rationale,
                    status,
                    base_version,
                    diff_text,
                    json.dumps(edits, ensure_ascii=False),
                    json.dumps(base_hashes, ensure_ascii=False, sort_keys=True),
                    json.dumps(test_command, ensure_ascii=False)
                    if test_command is not None
                    else None,
                ),
            )
            self._conn.commit()
        return self.get(proposal_id) or {}

    def update(self, proposal_id: str, **fields: Any) -> Dict[str, Any]:
        unknown = set(fields) - set(_COLUMNS)
        if unknown:
            raise ValueError(f"未知的提案字段：{sorted(unknown)}")
        fields["updated_at"] = datetime.now(timezone.utc).isoformat()
        values: List[Any] = []
        assignments: List[str] = []
        for key, value in fields.items():
            assignments.append(f"{key} = ?")
            if key == "test_summary_json" and not isinstance(value, str):
                values.append(json.dumps(value, ensure_ascii=False))
            else:
                values.append(value)
        sql = (
            f"UPDATE selfmod_proposals SET {', '.join(assignments)} WHERE id = ?"
        )
        with self._lock:
            self._conn.execute(sql, values + [proposal_id])
            self._conn.commit()
        return self.get(proposal_id) or {}

    def get(self, proposal_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM selfmod_proposals WHERE id = ?", (proposal_id,)
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def list(self, *, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM selfmod_proposals ORDER BY created_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def add_event(
        self,
        proposal_id: str,
        kind: str,
        status: str = "",
        detail: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO selfmod_events
                    (proposal_id, created_at, kind, status, detail_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    proposal_id,
                    datetime.now(timezone.utc).isoformat(),
                    kind,
                    status,
                    json.dumps(detail or {}, ensure_ascii=False),
                ),
            )
            self._conn.commit()

    def events(self, proposal_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM selfmod_events WHERE proposal_id = ?
                ORDER BY id DESC LIMIT ?
                """,
                (proposal_id, max(1, int(limit))),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["detail"] = json.loads(item.pop("detail_json") or "{}")
            except json.JSONDecodeError:
                item["detail"] = {}
            result.append(item)
        return result

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        result = dict(row)
        result["diff"] = result.pop("diff_text", "") or ""
        for key, target in (
            ("edits_json", "edits"),
            ("base_hashes_json", "base_hashes"),
            ("test_command_json", "test_command"),
            ("test_summary_json", "test_summary"),
        ):
            raw = result.pop(key, None)
            try:
                result[target] = json.loads(raw) if raw else ({} if target != "edits" else [])
            except json.JSONDecodeError:
                result[target] = {} if target != "edits" else []
        return result
