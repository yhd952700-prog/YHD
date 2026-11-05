"""真实重启续跑：A 进程注册提醒后退出，B 进程重启排程器，到点仍然触发。

不用同进程 mock，也不缩圈：A/B 都是独立 Python 进程，共享同一个 SQLite 库。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from src.tasks.reminders import ReminderStore

REPO_ROOT = Path(__file__).resolve().parents[2]


def _env(tmp_path: Path) -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["LIUHAO_WORKSPACE_ROOT"] = str(tmp_path / "workspace")
    env["LIUHAO_LOCAL_SCHEDULER"] = "1"
    env["LIUHAO_LOCAL_SCHEDULER_DB"] = str(tmp_path / "local_scheduler.db")
    env["LIUHAO_REMINDERS_DB"] = str(tmp_path / "reminders.db")
    env["LIUHAO_REMINDER_OUTBOX"] = str(tmp_path / "reminders_outbox.jsonl")
    env["LIUHAO_REMINDER_OS_NOTIFY"] = "0"
    env["LIUHAO_TIMEZONE_OFFSET_HOURS"] = "0"
    env["AUDIT_DB_PATH"] = str(tmp_path / "audit.db")
    return env


_CREATE_SCRIPT = """
import json
from src.tasks.reminders import get_reminder_service

service = get_reminder_service()
record = service.create("user-restart", "3秒后提醒我重启续跑")
print(json.dumps({"id": record["id"], "run_at": record["run_at"]}, ensure_ascii=False))
"""

_RUN_SCRIPT = """
import asyncio
import os
import time

from src.tasks.local_handlers import build_local_scheduler


async def main():
    scheduler = build_local_scheduler()
    task = asyncio.create_task(scheduler.run())
    outbox = os.environ["LIUHAO_REMINDER_OUTBOX"]
    deadline = time.monotonic() + 30
    fired = False
    while time.monotonic() < deadline:
        if os.path.exists(outbox) and os.path.getsize(outbox) > 0:
            fired = True
            break
        await asyncio.sleep(0.2)
    scheduler.stop()
    await asyncio.wait_for(task, 20)
    print("FIRED" if fired else "NOT_FIRED")


asyncio.run(main())
"""


def test_reminder_survives_process_restart_and_fires(tmp_path):
    env = _env(tmp_path)

    # A 进程：注册 3 秒后的提醒，然后立刻退出（此时还没到点）。
    created = subprocess.run(
        [sys.executable, "-c", _CREATE_SCRIPT],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert created.returncode == 0, created.stderr
    payload = json.loads(created.stdout.strip().splitlines()[-1])
    reminder_id = payload["id"]
    assert not (tmp_path / "reminders_outbox.jsonl").exists()

    # B 进程：全新的排程器实例，从同一个库续跑到期任务。
    runner = subprocess.run(
        [sys.executable, "-c", _RUN_SCRIPT],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert runner.returncode == 0, runner.stderr
    assert "FIRED" in runner.stdout, runner.stdout + runner.stderr

    outbox = tmp_path / "reminders_outbox.jsonl"
    lines = [line for line in outbox.read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert "重启续跑" in record["body"]

    store = ReminderStore(env["LIUHAO_REMINDERS_DB"])
    try:
        reminder = store.get(reminder_id)
        deliveries = store.list_deliveries("user-restart", reminder_id=reminder_id)
    finally:
        store.close()
    assert reminder is not None
    assert reminder["status"] == "completed"
    assert [item["status"] for item in deliveries] == ["delivered"]
