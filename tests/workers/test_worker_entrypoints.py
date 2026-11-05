"""worker / scheduler 入口的真实进程验证。

背景：`config/supervisor.conf` 之前配置了 `src.workers.runner`，但目录不存在，
守护进程配置是空指。这里同时守住两件事：

1. supervisor 仍指向这两个入口，且模块真实可导入（防回归）。
2. `python -m src.workers.runner` 能在**另一个进程**里消费 SQLite 队列并执行
   任务（不是同进程 mock）；scheduler 能把周期任务真正写进队列。
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
import time
from pathlib import Path

from src.kernels.audit import AuditStore
from src.tasks.registry import PROBE_TASK, RECOVERY_SCAN_TASK, register_tasks
from src.tasks.sqlite_broker import SqliteTaskBroker

REPO_ROOT = Path(__file__).resolve().parents[2]


def _worker_env(tmp_path: Path) -> dict:
    env = os.environ.copy()
    env["LIUHAO_TASK_BROKER"] = "sqlite"
    env["LIUHAO_TASK_DB"] = str(tmp_path / "queue.db")
    env["LIUHAO_TASK_JOURNAL"] = str(tmp_path / "tasks.journal")
    env["LIUHAO_WORKSPACE_ROOT"] = str(tmp_path / "workspace")
    env["AUDIT_DB_PATH"] = str(tmp_path / "audit.db")
    env["PYTHONPATH"] = str(REPO_ROOT)
    Path(env["LIUHAO_WORKSPACE_ROOT"]).mkdir(parents=True, exist_ok=True)
    return env


def _spawn(module: str, *args: str, env: dict) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", module, *args],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_supervisor_targets_exist():
    conf = (REPO_ROOT / "config" / "supervisor.conf").read_text(encoding="utf-8")
    assert "src.workers.runner" in conf
    assert "src.workers.scheduler" in conf
    importlib.import_module("src.workers.runner")
    importlib.import_module("src.workers.scheduler")


async def test_worker_entrypoint_consumes_sqlite_queue_in_separate_process(tmp_path):
    env = _worker_env(tmp_path)
    db = env["LIUHAO_TASK_DB"]
    marker = tmp_path / "probe-marker.txt"
    journal = env["LIUHAO_TASK_JOURNAL"]

    producer = SqliteTaskBroker(db)
    tasks = register_tasks(producer, recovery_interval=60)
    handle = await tasks[PROBE_TASK].kiq(
        marker_path=str(marker), journal_path=journal
    )
    await producer.shutdown()

    proc = _spawn("src.workers.runner", "--max-tasks", "1", env=env)
    try:
        stdout, stderr = proc.communicate(timeout=60)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
        raise AssertionError(f"worker did not exit\nstdout={stdout}\nstderr={stderr}")

    assert proc.returncode == 0, f"worker failed\nstdout={stdout}\nstderr={stderr}"
    assert marker.read_text(encoding="utf-8").splitlines() == [handle.task_id]

    # 审计没有被绕过：worker 进程把自己的启停写进了审计库。
    store = AuditStore(env["AUDIT_DB_PATH"])
    events = store.query_events(principal_id="liuhao-worker")
    actions = {
        (event.get("details") or {}).get("action")
        for event in events
        if isinstance(event.get("details"), dict)
    }
    assert "worker.start" in actions
    assert "worker_probe.completed" in actions
    assert "worker.stop" in actions
    # 本地排程器在 worker 里默认启用，并在停机时留下审计（证明真的接线了）
    assert "local_scheduler.stop" in actions


async def test_scheduler_enqueues_periodic_task_into_real_queue(tmp_path):
    env = _worker_env(tmp_path)
    env["LIUHAO_RECOVERY_SCAN_INTERVAL"] = "5"

    proc = _spawn("src.workers.scheduler", "--poll-interval", "0.2", env=env)
    broker = SqliteTaskBroker(env["LIUHAO_TASK_DB"])
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if broker.pending_count() >= 1:
                break
            if proc.poll() is not None:
                stdout, stderr = proc.communicate()
                raise AssertionError(
                    f"scheduler exited early rc={proc.returncode}\n"
                    f"stdout={stdout}\nstderr={stderr}"
                )
            time.sleep(0.2)
        assert broker.pending_count() >= 1, "scheduler never enqueued the periodic task"
        with broker._lock:
            rows = broker._conn.execute(
                "SELECT task_name FROM task_queue WHERE status = 'pending'"
            ).fetchall()
        assert (RECOVERY_SCAN_TASK,) in rows

        # 同进程的本地排程器也在真实运行：首个心跳作业被执行并写审计
        store = AuditStore(env["AUDIT_DB_PATH"])
        deadline = time.monotonic() + 15
        actions: set = set()
        while time.monotonic() < deadline:
            events = store.query_events(principal_id="liuhao-worker")
            actions = {
                (event.get("details") or {}).get("action")
                for event in events
                if isinstance(event.get("details"), dict)
            }
            if "local_scheduler.heartbeat" in actions:
                break
            time.sleep(0.2)
        assert "local_scheduler.heartbeat" in actions
    finally:
        proc.terminate()
        try:
            proc.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
        await broker.shutdown()
