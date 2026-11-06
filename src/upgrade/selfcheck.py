"""Post-install self-check: real service startup, databases, key tables.

This runs **after** the new code has been installed and is the gate before the
upgrade is declared successful. Any issue makes the manager roll back
automatically; it is not a suggestion the user has to act on.

The service is verified by *actually starting it* (see
:mod:`src.upgrade.startup_probe`): a sandbox copy of the installed code, a
temporary free port, the full uvicorn lifespan, and a health-endpoint poll.
That catches the failures users hit most -- port already taken, migration error,
missing dependency, lifespan exception -- which an import-only check cannot see.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .console_contract import FrontendContractError, verify_health_contract, verify_installed
from .dataguard import is_sqlite_file, iter_files, verify_expected_tables
from .startup_probe import StartupCheckConfig, StartupReport, run_startup_check


@dataclass(frozen=True)
class SelfCheckConfig:
    """What the post-install gate checks.

    ``check_service=False`` exists for diagnostics/tests only; the production
    default (``True``) is what makes a broken release roll back.
    """

    service_module: str = "src.gateway.main"
    service_attr: str = "app"
    check_service: bool = True
    busy_timeout_ms: int = 5000
    startup: StartupCheckConfig = field(default_factory=StartupCheckConfig)


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
    service_detail: str = ""
    startup: Optional[StartupReport] = None
    frontend_contract: Dict[str, Any] = field(default_factory=dict)
    databases_checked: int = 0
    tables_checked: int = 0
    issues: List[SelfCheckIssue] = field(default_factory=list)
    checked_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "service_checked": self.service_checked,
            "service_ok": self.service_ok,
            "service_detail": self.service_detail,
            "startup": self.startup.to_dict() if self.startup else None,
            "frontend_contract": dict(self.frontend_contract),
            "databases_checked": self.databases_checked,
            "tables_checked": self.tables_checked,
            "issues": [issue.to_dict() for issue in self.issues],
            "checked_at": self.checked_at,
        }


def run_self_check(
    app_root: Path | str,
    data_paths: Iterable[Path],
    *,
    expected_tables: Optional[Dict[str, List[str]]] = None,
    config: Optional[SelfCheckConfig] = None,
) -> SelfCheckReport:
    """Check the installed app without modifying the live installation."""
    config = config or SelfCheckConfig()
    root = Path(app_root)
    report = SelfCheckReport(ok=True, checked_at=datetime.now(timezone.utc).isoformat())

    if config.check_service:
        report.service_checked = True
        startup = run_startup_check(
            root,
            module=config.service_module,
            attr=config.service_attr,
            config=config.startup,
        )
        report.startup = startup
        report.service_ok = startup.ok
        report.service_detail = startup.reason or (
            f"服务已真实启动并通过健康检查（{startup.health_path}，"
            f"{startup.startup_seconds:.1f} 秒）"
        )
        if not startup.ok:
            report.ok = False
            report.issues.append(
                SelfCheckIssue("service", str(root), startup.reason)
            )

    # Front-end/back-end contract gate. A package without the console build
    # output upgrades the service but leaves the browser on the old UI; the
    # service is healthy, so nothing else catches it.
    try:
        applicable, stamp, note = verify_installed(root)
        report.frontend_contract = {
            "applicable": applicable,
            "checked": applicable,
            "api_contract": stamp.get("api_contract"),
            "frontend_version": stamp.get("frontend_version"),
            "console_files": stamp.get("console_files"),
            "console_fingerprint": stamp.get("console_fingerprint"),
            "note": note,
        }
        if applicable and report.startup is not None and report.startup.ok:
            verify_health_contract(stamp, report.startup.health_body)
            report.frontend_contract["running_api_contract"] = (
                report.startup.health_body.get("api_contract")
            )
    except FrontendContractError as exc:
        report.ok = False
        report.issues.append(SelfCheckIssue("frontend_contract", str(root), str(exc)))

    paths = [Path(path) for path in data_paths]
    for path in iter_files(paths):
        if not is_sqlite_file(path):
            continue
        report.databases_checked += 1
        try:
            conn = sqlite3.connect(str(path))
            try:
                conn.execute(f"PRAGMA busy_timeout={config.busy_timeout_ms}")
                # ``SELECT 1`` is a constant expression: some SQLite builds
                # never touch the file while preparing it, so a corrupt
                # database passed the gate as healthy. Read the schema instead
                # -- that forces page 1 to be parsed on every build.
                conn.execute("SELECT count(*) FROM sqlite_master")
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
