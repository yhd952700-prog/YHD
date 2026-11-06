"""Weekly update check on the in-process scheduler: notify, never install.

Registered by ``src/tasks/local_handlers.py`` on the single-instance local
scheduler (the same one that runs heartbeats/reminders). The job:

* runs ``UpgradeManager.check()`` only (no download, no backup, no apply);
* notifies through the existing tray-balloon/console notifier when a newer
  version exists;
* records the last check time/result in ``upgrade-state/last-check.json``;
* re-enqueues itself for the next week with a deterministic job key so a
  scheduler restart does not duplicate or skip the cycle.

If no update source is configured, no job is seeded (there is nothing to
check). The installer/operator configures one of:

* ``LIUHAO_UPGRADE_RELEASE_DIR`` — local release directory
* ``LIUHAO_UPGRADE_MANIFEST_URL`` — HTTP(S) manifest URL

Nothing here auto-installs. The notification only tells the user a newer
version exists and that they must confirm the upgrade.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.common.atomic_write import atomic_write_text
from src.tasks.local_scheduler import ACTIVE_STATUSES, LocalScheduler
from src.tasks.reminder_notify import build_default_notifier

from .manager import UpgradeConfig, UpgradeManager
from .models import CheckStatus
from .sources import build_source

UPDATE_CHECK_JOB = "liuhao.upgrade.weekly-check"
UPDATE_CHECK_KEY_PREFIX = "liuhao.upgrade.weekly-check"
CHECK_STATE_SCHEMA = "liuhao.update-check/1"

DEFAULT_INTERVAL_SECONDS = 7 * 24 * 3600
MIN_INTERVAL_SECONDS = 60.0


def update_check_interval_seconds() -> float:
    """``LIUHAO_UPDATE_CHECK_INTERVAL`` in seconds (default 7 days, min 60)."""
    raw = os.environ.get("LIUHAO_UPDATE_CHECK_INTERVAL")
    if not raw:
        return float(DEFAULT_INTERVAL_SECONDS)
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(
            f"LIUHAO_UPDATE_CHECK_INTERVAL must be a number, got {raw!r}"
        ) from exc
    if value < MIN_INTERVAL_SECONDS:
        raise ValueError(
            f"LIUHAO_UPDATE_CHECK_INTERVAL must be >= {MIN_INTERVAL_SECONDS:g}s"
        )
    return value


def default_app_root() -> Path:
    """程序根目录（安装树 / 源码仓库），不是用户工作区。

    ``LIUHAO_APP_ROOT`` 可显式覆盖（测试与自定义安装用）。**不能**用
    ``LIUHAO_WORKSPACE_ROOT``：安装版启动器把它指向 ``<data>/workspace``，
    拿它当程序根会让升级检查错目录。
    """
    raw = os.environ.get("LIUHAO_APP_ROOT")
    if raw:
        return Path(raw).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def default_state_dir(app_root: Path | None = None) -> Path:
    raw = os.environ.get("LIUHAO_UPGRADE_STATE_DIR")
    if raw:
        return Path(raw).resolve()
    return (app_root or default_app_root()) / "upgrade-state"


def source_configured() -> bool:
    return bool(
        os.environ.get("LIUHAO_UPGRADE_RELEASE_DIR")
        or os.environ.get("LIUHAO_UPGRADE_MANIFEST_URL")
    )


def build_manager_from_env() -> UpgradeManager:
    app_root = default_app_root()
    release_dir = os.environ.get("LIUHAO_UPGRADE_RELEASE_DIR")
    manifest_url = os.environ.get("LIUHAO_UPGRADE_MANIFEST_URL")
    source = build_source(
        release_dir=Path(release_dir) if release_dir else None,
        manifest_url=manifest_url or None,
    )
    config = UpgradeConfig(
        app_root=app_root,
        # 与 CLI 共用同一套解析：显式参数 > LIUHAO_DATA_DIR > 安装树默认
        # （%LOCALAPPDATA%\LiuHao\data）> 便携/开发 <app>/data。
        data_dir=None,
        backup_root=os.environ.get("LIUHAO_UPGRADE_BACKUP_ROOT") or None,
        state_dir=default_state_dir(app_root),
        source=source,
        public_key=os.environ.get("LIUHAO_UPGRADE_PUBLIC_KEY") or None,
    )
    return UpgradeManager(config)


def _notify_result_dicts(results: Any) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    for result in results or []:
        if hasattr(result, "as_dict"):
            output.append(result.as_dict())
        elif isinstance(result, dict):
            output.append(result)
        else:
            output.append(
                {
                    "channel": getattr(result, "channel", "unknown"),
                    "ok": bool(getattr(result, "ok", False)),
                    "detail": str(getattr(result, "detail", "")),
                }
            )
    return output


def _write_error_state(
    exc: BaseException, *, next_check_at: str, state_dir: Path | None = None
) -> Dict[str, Any]:
    """Record an unexpected failure without ever claiming 已是最新."""
    state = {
        "schema": CHECK_STATE_SCHEMA,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "trigger": "weekly",
        "status": "error",
        "message": f"无法检查更新：{type(exc).__name__}: {exc}",
        "current_version": None,
        "latest_version": None,
        "notified": False,
        "notify_results": [],
        "next_check_at": next_check_at,
    }
    atomic_write_text(
        (state_dir or default_state_dir()) / "last-check.json",
        json.dumps(state, ensure_ascii=False, indent=2),
    )
    return state


def make_update_check_handler(
    scheduler: LocalScheduler,
    *,
    manager_factory: Optional[Callable[[], UpgradeManager]] = None,
    notifier_factory: Optional[Callable[[], Any]] = None,
    interval_seconds: Optional[float] = None,
) -> Callable[..., Any]:
    """Build the async job handler. Tests inject ``manager_factory``/notifier."""
    base_interval = (
        update_check_interval_seconds() if interval_seconds is None else float(interval_seconds)
    )
    if base_interval < MIN_INTERVAL_SECONDS:
        raise ValueError(f"interval_seconds must be >= {MIN_INTERVAL_SECONDS:g}")

    async def handler(interval_seconds: Optional[float] = None) -> Dict[str, Any]:  # noqa: A002
        current_interval = float(interval_seconds or base_interval)
        next_run = scheduler.now() + timedelta(seconds=current_interval)
        next_check_at = next_run.isoformat()
        manager: Optional[UpgradeManager] = None
        try:
            manager = (manager_factory or build_manager_from_env)()
            result = await asyncio.to_thread(manager.check, trigger="weekly")
            notified = False
            if result.update_available and result.release is not None:
                title = f"鎏灏有新版本 {result.release.version}"
                body = (
                    f"当前版本 {result.current_version}，发现新版本 {result.release.version}。"
                    "请打开鎏灏确认升级；不会自动安装。"
                )
                notifier = (notifier_factory or build_default_notifier)()
                results = await asyncio.to_thread(notifier.notify, title, body)
                dicts = _notify_result_dicts(results)
                notified = any(item.get("ok") for item in dicts)
                manager.annotate_last_check(
                    notified=notified,
                    notify_results=dicts,
                    next_check_at=next_check_at,
                )
            else:
                manager.annotate_last_check(
                    notified=False,
                    notify_results=[],
                    next_check_at=next_check_at,
                )
            return {
                "status": result.status.value,
                "message": result.message,
                "current_version": result.current_version,
                "latest_version": result.latest_version,
                "notified": notified,
                "next_check_at": next_check_at,
            }
        except Exception as exc:  # noqa: BLE001 - record failure, never fake success
            _write_error_state(
                exc,
                next_check_at=next_check_at,
                state_dir=manager.config.state_dir if manager is not None else None,
            )
            return {
                "status": "error",
                "message": f"无法检查更新：{type(exc).__name__}: {exc}",
                "notified": False,
                "next_check_at": next_check_at,
            }
        finally:
            if manager is not None:
                manager.close()
            # Deterministic key: a retry or a restart cannot create a duplicate
            # weekly cycle. max_attempts=1: a failed check is recorded, not retried
            # into a notification storm.
            scheduler.enqueue(
                UPDATE_CHECK_JOB,
                run_at=next_run,
                job_key=f"{UPDATE_CHECK_KEY_PREFIX}:{next_check_at}",
                payload={"interval_seconds": current_interval},
                max_attempts=1,
            )

    return handler


def register_update_check_job(
    scheduler: LocalScheduler,
    *,
    manager_factory: Optional[Callable[[], UpgradeManager]] = None,
    notifier_factory: Optional[Callable[[], Any]] = None,
    interval_seconds: Optional[float] = None,
) -> None:
    scheduler.register(
        UPDATE_CHECK_JOB,
        make_update_check_handler(
            scheduler,
            manager_factory=manager_factory,
            notifier_factory=notifier_factory,
            interval_seconds=interval_seconds,
        ),
    )


def seed_update_check_job(
    scheduler: LocalScheduler,
    *,
    manager_factory: Optional[Callable[[], UpgradeManager]] = None,
    notifier_factory: Optional[Callable[[], Any]] = None,
    interval_seconds: Optional[float] = None,
    force: bool = False,
    initial_delay_seconds: float = 0.0,
) -> Optional[int]:
    """Queue the first check once per active cycle (idempotent across restarts)."""
    configured = force or manager_factory is not None or source_configured()
    if not configured:
        return None
    for job in scheduler.list_jobs(limit=200):
        if (
            job.get("job_name") == UPDATE_CHECK_JOB
            and job.get("status") in ACTIVE_STATUSES
        ):
            return None
    interval = (
        update_check_interval_seconds() if interval_seconds is None else float(interval_seconds)
    )
    run_at = scheduler.now() + timedelta(seconds=max(0.0, initial_delay_seconds))
    return scheduler.enqueue(
        UPDATE_CHECK_JOB,
        run_at=run_at,
        job_key=f"{UPDATE_CHECK_KEY_PREFIX}:start:{run_at.isoformat()}",
        payload={"interval_seconds": interval},
        max_attempts=1,
    )
