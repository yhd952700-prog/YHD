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
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List

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
