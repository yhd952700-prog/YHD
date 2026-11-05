"""Weekly update check on the real LocalScheduler: notify only, never install."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.tasks.local_scheduler import COMPLETED, PENDING, LocalScheduler
from src.tasks.reminder_notify import NotifyResult
from src.upgrade import LocalDirectorySource, UpgradeConfig, UpgradeManager
from src.upgrade.update_check import (
    DEFAULT_INTERVAL_SECONDS,
    UPDATE_CHECK_JOB,
    register_update_check_job,
    seed_update_check_job,
)

WEEK = float(DEFAULT_INTERVAL_SECONDS)
assert WEEK == 7 * 24 * 3600


class FakeClock:
    def __init__(self, start: datetime | None = None) -> None:
        self.current = start or datetime(2026, 11, 6, 9, 0, 0)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current = self.current + timedelta(seconds=seconds)


class RecordingNotifier:
    channel = "recording"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def notify(
        self, title: str, body: str, *, idempotency_key: str | None = None
    ) -> list[NotifyResult]:
        self.calls.append((title, body))
        return [NotifyResult(self.channel, True, "recorded")]


def _scheduler(tmp_path: Path, clock: FakeClock) -> LocalScheduler:
    return LocalScheduler(
        str(tmp_path / "sched.db"),
        max_concurrency=1,
        tick_seconds=0.01,
        now_fn=clock,
        audit_sink=lambda action, outcome, details: None,
    )


async def _drive(scheduler: LocalScheduler, predicate, *, timeout: float = 5.0) -> bool:
    runner = asyncio.create_task(scheduler.run())
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            await asyncio.sleep(0.01)
        return bool(predicate())
    finally:
        scheduler.stop()
        await asyncio.wait_for(runner, timeout=5.0)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "LIUHAO_UPGRADE_RELEASE_DIR",
        "LIUHAO_UPGRADE_MANIFEST_URL",
        "LIUHAO_UPGRADE_STATE_DIR",
        "LIUHAO_UPDATE_CHECK_INTERVAL",
    ):
        monkeypatch.delenv(name, raising=False)


def _make_app(root: Path, *, version: str = "0.1.0") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "liuhao-ai-os"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "data").mkdir(exist_ok=True)
    return root


def _release(release_dir: Path, *, version: str) -> dict:
    release_dir.mkdir(parents=True, exist_ok=True)
    artifact = f"liuhao-{version}.zip"
    zip_path = release_dir / artifact
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "pyproject.toml",
            f'[project]\nname = "liuhao-ai-os"\nversion = "{version}"\n',
        )
    release = {
        "version": version,
        "artifact": artifact,
        "sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
        "size": zip_path.stat().st_size,
    }
    (release_dir / "LIUHAO_RELEASE.json").write_text(
        json.dumps({"schema": "liuhao.release-manifest/1", "releases": [release]}),
        encoding="utf-8",
    )
    return release


def _manager_factory(app_root: Path, release_dir: Path, backup_root: Path, state_dir: Path):
    def factory() -> UpgradeManager:
        return UpgradeManager(
            UpgradeConfig(
                app_root=app_root,
                data_dir=app_root / "data",
                backup_root=backup_root,
                state_dir=state_dir,
                source=LocalDirectorySource(release_dir),
            )
        )

    return factory


def _weekly_pending(scheduler: LocalScheduler) -> list[dict]:
    return [
        job
        for job in scheduler.list_jobs(limit=100)
        if job["job_name"] == UPDATE_CHECK_JOB and job["status"] == PENDING
    ]


def test_weekly_check_notifies_but_never_installs(tmp_path: Path) -> None:
    app_root = _make_app(tmp_path / "app")
    release_dir = tmp_path / "releases"
    _release(release_dir, version="0.2.0")
    state_dir = tmp_path / "state"
    backup_root = tmp_path / "backups"

    clock = FakeClock()
    scheduler = _scheduler(tmp_path, clock)
    notifier = RecordingNotifier()
    register_update_check_job(
        scheduler,
        manager_factory=_manager_factory(app_root, release_dir, backup_root, state_dir),
        notifier_factory=lambda: notifier,
        interval_seconds=WEEK,
    )
    job_id = scheduler.enqueue(
        UPDATE_CHECK_JOB, payload={"interval_seconds": WEEK}, job_key="weekly-seed"
    )

    assert asyncio.run(
        _drive(scheduler, lambda: bool(notifier.calls) and bool(_weekly_pending(scheduler)))
    )

    # Notified, with an explicit "user must confirm" message.
    assert len(notifier.calls) == 1
    title, body = notifier.calls[0]
    assert "0.2.0" in title
    assert "不会自动安装" in body
    assert "确认" in body

    # But nothing was downloaded, backed up or installed.
    assert (app_root / "pyproject.toml").read_text(encoding="utf-8").find("0.1.0") >= 0
    assert (app_root / "src" / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert list(backup_root.glob("*/backup.json")) == []

    manager = UpgradeManager(
        UpgradeConfig(
            app_root=app_root,
            data_dir=app_root / "data",
            backup_root=backup_root,
            state_dir=state_dir,
            source=LocalDirectorySource(release_dir),
        )
    )
    try:
        assert manager.history(kind="apply") == []
        assert manager.history(kind="download") == []
        state = manager.last_check()
    finally:
        manager.close()

    assert state["status"] == "update_available"
    assert state["trigger"] == "weekly"
    assert state["latest_version"] == "0.2.0"
    assert state["notified"] is True
    assert state["notify_results"][0]["ok"] is True

    # The first job completed and exactly one follow-up is queued for +7 days.
    assert scheduler.get_job(job_id)["status"] == COMPLETED
    pending = _weekly_pending(scheduler)
    assert len(pending) == 1
    expected_run = (clock() + timedelta(seconds=WEEK)).isoformat()
    assert pending[0]["run_at"] == expected_run
    assert pending[0]["payload"]["interval_seconds"] == WEEK
    assert pending[0]["max_attempts"] == 1
    assert state["next_check_at"] == expected_run
    scheduler.close()


def test_weekly_check_reports_unreachable_instead_of_up_to_date(tmp_path: Path) -> None:
    app_root = _make_app(tmp_path / "app")
    missing_release_dir = tmp_path / "missing-releases"
    state_dir = tmp_path / "state"
    backup_root = tmp_path / "backups"

    clock = FakeClock()
    scheduler = _scheduler(tmp_path, clock)
    notifier = RecordingNotifier()
    register_update_check_job(
        scheduler,
        manager_factory=_manager_factory(
            app_root, missing_release_dir, backup_root, state_dir
        ),
        notifier_factory=lambda: notifier,
        interval_seconds=WEEK,
    )
    job_id = scheduler.enqueue(
        UPDATE_CHECK_JOB, payload={"interval_seconds": WEEK}, job_key="weekly-unreachable"
    )

    assert asyncio.run(
        _drive(scheduler, lambda: scheduler.get_job(job_id)["status"] == COMPLETED)
    )

    assert notifier.calls == []
    state = json.loads((state_dir / "last-check.json").read_text(encoding="utf-8"))
    assert state["status"] == "unreachable"
    assert "无法检查更新" in state["message"]
    assert "已是最新" not in state["message"]
    assert state["notified"] is False
    assert len(_weekly_pending(scheduler)) == 1
    scheduler.close()


def test_weekly_check_up_to_date_does_not_notify(tmp_path: Path) -> None:
    app_root = _make_app(tmp_path / "app")
    release_dir = tmp_path / "releases"
    _release(release_dir, version="0.1.0")
    state_dir = tmp_path / "state"
    backup_root = tmp_path / "backups"

    clock = FakeClock()
    scheduler = _scheduler(tmp_path, clock)
    notifier = RecordingNotifier()
    register_update_check_job(
        scheduler,
        manager_factory=_manager_factory(app_root, release_dir, backup_root, state_dir),
        notifier_factory=lambda: notifier,
        interval_seconds=WEEK,
    )
    job_id = scheduler.enqueue(
        UPDATE_CHECK_JOB, payload={"interval_seconds": WEEK}, job_key="weekly-current"
    )

    assert asyncio.run(
        _drive(scheduler, lambda: scheduler.get_job(job_id)["status"] == COMPLETED)
    )

    assert notifier.calls == []
    state = json.loads((state_dir / "last-check.json").read_text(encoding="utf-8"))
    assert state["status"] == "up_to_date"
    assert "已是最新版本 0.1.0" in state["message"]
    scheduler.close()


def test_weekly_check_failure_is_recorded_and_never_faked_as_up_to_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_root = _make_app(tmp_path / "app")
    state_dir = tmp_path / "state"
    monkeypatch.setenv("LIUHAO_UPGRADE_STATE_DIR", str(state_dir))

    clock = FakeClock()
    scheduler = _scheduler(tmp_path, clock)
    notifier = RecordingNotifier()

    def exploding_factory() -> UpgradeManager:
        raise RuntimeError("manager exploded")

    register_update_check_job(
        scheduler,
        manager_factory=exploding_factory,
        notifier_factory=lambda: notifier,
        interval_seconds=WEEK,
    )
    job_id = scheduler.enqueue(
        UPDATE_CHECK_JOB, payload={"interval_seconds": WEEK}, job_key="weekly-error"
    )

    assert asyncio.run(
        _drive(scheduler, lambda: scheduler.get_job(job_id)["status"] == COMPLETED)
    )

    assert notifier.calls == []
    state = json.loads((state_dir / "last-check.json").read_text(encoding="utf-8"))
    assert state["status"] == "error"
    assert "无法检查更新" in state["message"]
    assert "manager exploded" in state["message"]
    assert "已是最新" not in state["message"]
    # A failed cycle still schedules the next week rather than going dark.
    assert len(_weekly_pending(scheduler)) == 1
    scheduler.close()


def test_seed_is_idempotent_and_needs_a_configured_source(tmp_path: Path) -> None:
    clock = FakeClock()
    scheduler = _scheduler(tmp_path, clock)

    # Nothing configured: no weekly job is queued (there is nothing to check).
    assert seed_update_check_job(scheduler) is None
    assert _weekly_pending(scheduler) == []

    # With an injected factory, seeding happens once and is idempotent.
    first = seed_update_check_job(scheduler, manager_factory=lambda: None, interval_seconds=WEEK)
    assert first is not None
    assert seed_update_check_job(scheduler, manager_factory=lambda: None, interval_seconds=WEEK) is None
    assert len(_weekly_pending(scheduler)) == 1
    scheduler.close()


def test_register_local_jobs_includes_the_weekly_check(tmp_path: Path) -> None:
    from src.tasks.local_handlers import register_local_jobs

    clock = FakeClock()
    scheduler = _scheduler(tmp_path, clock)
    try:
        register_local_jobs(scheduler)
        assert UPDATE_CHECK_JOB in scheduler.registered_jobs()
    finally:
        scheduler.close()
