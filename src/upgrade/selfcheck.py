"""Post-install self-check: service imports, databases open, key tables readable.

This runs **after** the new code has been installed and is the gate before the
upgrade is declared successful. Any issue makes the manager roll back
automatically; it is not a suggestion the user has to act on.

The service probe intentionally runs in a **subprocess**: importing
``src.gateway.main`` must not execute in the live process (a broken release
could crash or hang the very process that is supposed to roll it back), and a
timeout is meaningful only across a process boundary. The probe imports the
module and asserts the ASGI ``app`` exists with at least one route; it does not
start uvicorn or run the lifespan.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .dataguard import is_sqlite_file, iter_files, verify_expected_tables

_PROBE_TEMPLATE = """
import importlib, json
try:
    module = importlib.import_module(%r)
    app = getattr(module, %r, None)
    routes = len(getattr(app, "routes", []))
    print(json.dumps({"ok": app is not None and routes > 0, "routes": routes}))
except BaseException as exc:
    print(json.dumps({"ok": False, "routes": 0, "error": type(exc).__name__ + ": " + str(exc)}))
"""


@dataclass(frozen=True)
class SelfCheckConfig:
    """What the post-install gate checks.

    ``check_service=False`` exists for diagnostics/tests only; the production
    default (``True``) is what makes a broken release roll back.
    """

    service_module: str = "src.gateway.main"
    service_attr: str = "app"
    service_timeout_seconds: float = 90.0
    check_service: bool = True
    busy_timeout_ms: int = 5000


@dataclass
class SelfCheckIssue:
    kind: str
    path: str
    detail: str

    def to_dict(self) -> Dict[str, str]:
        return {"kind": self.kind, "path": self.path, "detail": self.detail}


@dataclass
class SelfCheckReport:
    ok: bool
    service_checked: bool = False
    service_ok: bool = False
    service_routes: int = 0
    service_detail: str = ""
    databases_checked: int = 0
    tables_checked: int = 0
    issues: List[SelfCheckIssue] = field(default_factory=list)
    checked_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "service_checked": self.service_checked,
            "service_ok": self.service_ok,
            "service_routes": self.service_routes,
            "service_detail": self.service_detail,
            "databases_checked": self.databases_checked,
            "tables_checked": self.tables_checked,
            "issues": [issue.to_dict() for issue in self.issues],
            "checked_at": self.checked_at,
        }


def _probe_env(app_root: Path) -> Dict[str, str]:
    env = os.environ.copy()
    parts = [str(app_root), str(app_root / "src")]
    if env.get("PYTHONPATH"):
        parts.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(parts)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _parse_probe(stdout: str) -> Optional[Dict[str, Any]]:
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "ok" in payload:
            return payload
    return None


def probe_service(
    app_root: Path,
    config: SelfCheckConfig,
) -> Tuple[bool, int, str]:
    """Import the service entry point in a child process. Returns (ok, routes, detail)."""
    code = _PROBE_TEMPLATE % (config.service_module, config.service_attr)
    try:
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(app_root),
            env=_probe_env(app_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1.0, float(config.service_timeout_seconds)),
        )
    except subprocess.TimeoutExpired:
        return False, 0, f"服务启动检查超时（{config.service_timeout_seconds:g} 秒）"
    except OSError as exc:
        return False, 0, f"无法启动服务检查子进程：{exc}"

    payload = _parse_probe(completed.stdout)
    if payload is None:
        tail = ((completed.stderr or completed.stdout or "").strip())[-400:]
        return False, 0, f"服务检查无有效输出（退出码 {completed.returncode}）：{tail}"
    routes = int(payload.get("routes") or 0)
    if bool(payload.get("ok")):
        return True, routes, f"服务入口可导入，路由数 {routes}"
    error = str(payload.get("error") or "应用对象缺失或没有路由")
    return False, routes, f"服务无法启动：{error}"


def run_self_check(
    app_root: Path | str,
    data_paths: Iterable[Path],
    *,
    expected_tables: Optional[Dict[str, List[str]]] = None,
    config: Optional[SelfCheckConfig] = None,
) -> SelfCheckReport:
    """Check the installed app without modifying anything."""
    config = config or SelfCheckConfig()
    root = Path(app_root)
    report = SelfCheckReport(ok=True, checked_at=datetime.now(timezone.utc).isoformat())

    if config.check_service:
        report.service_checked = True
        ok, routes, detail = probe_service(root, config)
        report.service_ok = ok
        report.service_routes = routes
        report.service_detail = detail
        if not ok:
            report.ok = False
            report.issues.append(SelfCheckIssue("service", str(root), detail))

    paths = [Path(path) for path in data_paths]
    for path in iter_files(paths):
        if not is_sqlite_file(path):
            continue
        report.databases_checked += 1
        try:
            conn = sqlite3.connect(str(path))
            try:
                conn.execute(f"PRAGMA busy_timeout={config.busy_timeout_ms}")
                conn.execute("SELECT 1")
            finally:
                conn.close()
        except sqlite3.Error as exc:
            report.ok = False
            report.issues.append(SelfCheckIssue("sqlite", str(path), f"数据库无法打开：{exc}"))

    if expected_tables:
        table_issues = verify_expected_tables(
            paths, expected_tables, busy_timeout_ms=config.busy_timeout_ms
        )
        report.tables_checked = sum(len(tables) for tables in expected_tables.values())
        if table_issues:
            report.ok = False
            for issue in table_issues:
                report.issues.append(
                    SelfCheckIssue(issue.kind, issue.path, issue.detail)
                )

    return report
