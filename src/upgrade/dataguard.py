"""Non-destructive preflight verification of ``data/`` and explicit state files.

The upgrade refuses to start when the data it is supposed to protect is already
damaged: it is better to tell the user "data is not healthy, upgrade aborted"
than to apply code on top of a broken database.

SQLite is detected by its header (not by extension). Each database is opened
and checked with ``PRAGMA integrity_check``. JSON and YAML state files are
parsed. Nothing is written.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List

from .models import DataGuardIssue, DataGuardReport

_SQLITE_MAGIC = b"SQLite format 3\x00"
_SQLITE_SIDECARS = ("-wal", "-shm", "-journal")


def is_sqlite_file(path: Path) -> bool:
    try:
        with open(path, "rb") as handle:
            return handle.read(16) == _SQLITE_MAGIC
    except OSError:
        return False


def iter_files(paths: Iterable[Path]) -> Iterable[Path]:
    seen: set[str] = set()
    for root in paths:
        root = Path(root)
        if not root.exists():
            continue
        candidates = [root] if root.is_file() else sorted(root.rglob("*"))
        for candidate in candidates:
            if not candidate.is_file():
                continue
            if candidate.name.endswith(_SQLITE_SIDECARS):
                # Sidecars are part of the main DB; checking them separately is
                # meaningless and a stale WAL must never be copied on its own.
                continue
            key = str(candidate.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            yield candidate


def check_sqlite(path: Path, busy_timeout_ms: int = 5000) -> List[str]:
    errors: List[str] = []
    try:
        conn = sqlite3.connect(str(path))
    except sqlite3.Error as exc:
        return [f"无法打开 SQLite：{exc}"]
    try:
        conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        rows = conn.execute("PRAGMA integrity_check").fetchall()
        messages = [str(row[0]) for row in rows if row]
        if not messages or any(message.lower() != "ok" for message in messages):
            errors.extend(messages or ["integrity_check 未返回 ok"])
    except sqlite3.Error as exc:
        errors.append(f"integrity_check 失败：{exc}")
    finally:
        conn.close()
    return errors


def verify_data_paths(
    paths: Iterable[Path],
    *,
    busy_timeout_ms: int = 5000,
) -> DataGuardReport:
    report = DataGuardReport(
        ok=True,
        checked_at=datetime.now(timezone.utc).isoformat(),
    )
    for path in iter_files(paths):
        if path.is_symlink():
            report.ok = False
            report.issues.append(
                DataGuardIssue(str(path), "symlink", "数据目录中存在符号链接，拒绝预检")
            )
            continue
        report.files_checked += 1
        if is_sqlite_file(path):
            report.sqlite_checked += 1
            errors = check_sqlite(path, busy_timeout_ms=busy_timeout_ms)
            for error in errors:
                report.ok = False
                report.issues.append(DataGuardIssue(str(path), "sqlite", error))
            continue
        if path.suffix.lower() == ".json":
            report.json_checked += 1
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                report.ok = False
                report.issues.append(DataGuardIssue(str(path), "json", str(exc)))
            continue
        if path.suffix.lower() in (".yaml", ".yml"):
            report.yaml_checked += 1
            try:
                import yaml

                yaml.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError) as exc:
                report.ok = False
                report.issues.append(DataGuardIssue(str(path), "yaml", str(exc)))
            except Exception as exc:  # yaml.YAMLError without importing it eagerly
                report.ok = False
                report.issues.append(DataGuardIssue(str(path), "yaml", str(exc)))
    return report


def _path_key(path: Path) -> str:
    """Stable lookup key across pre/post upgrade (case-insensitive on Windows)."""
    return os.path.normcase(str(Path(path).resolve()))


def sqlite_tables(path: Path, busy_timeout_ms: int = 5000) -> List[str]:
    """User tables present in a SQLite file (internal ``sqlite_*`` excluded)."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        rows = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return [str(row[0]) for row in rows if row]
    finally:
        conn.close()


def capture_table_inventory(paths: Iterable[Path], *, busy_timeout_ms: int = 5000) -> Dict[str, List[str]]:
    """Record the tables each SQLite file has *before* the upgrade.

    The post-install self-check uses this to prove that upgrade did not make a
    previously readable table disappear. A capture failure degrades to an empty
    list for that file (the preflight already proved it is healthy).
    """
    inventory: Dict[str, List[str]] = {}
    for path in iter_files(paths):
        if not is_sqlite_file(path):
            continue
        try:
            inventory[_path_key(path)] = sqlite_tables(path, busy_timeout_ms=busy_timeout_ms)
        except sqlite3.Error:
            inventory[_path_key(path)] = []
    return inventory


def verify_expected_tables(
    paths: Iterable[Path],
    expected: Dict[str, List[str]],
    *,
    busy_timeout_ms: int = 5000,
) -> List[DataGuardIssue]:
    """Re-open every recorded DB and prove its recorded tables are readable."""
    issues: List[DataGuardIssue] = []
    current = {_path_key(path): path for path in iter_files(paths)}
    for key, tables in (expected or {}).items():
        path = current.get(key)
        if path is None or not path.is_file():
            issues.append(DataGuardIssue(key, "table", "升级后数据库文件不存在"))
            continue
        try:
            present = set(sqlite_tables(path, busy_timeout_ms=busy_timeout_ms))
        except sqlite3.Error as exc:
            issues.append(DataGuardIssue(str(path), "table", f"升级后数据库无法打开：{exc}"))
            continue
        missing = [table for table in tables if table not in present]
        if missing:
            issues.append(
                DataGuardIssue(str(path), "table", "升级后关键表缺失：" + ", ".join(missing))
            )
            continue
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            try:
                conn = sqlite3.connect(str(path))
                try:
                    conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
                    conn.execute(f"SELECT 1 FROM {quoted} LIMIT 1")
                finally:
                    conn.close()
            except sqlite3.Error as exc:
                issues.append(
                    DataGuardIssue(str(path), "table", f"关键表 {table} 不可读：{exc}")
                )
    return issues
