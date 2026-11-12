"""taskiq 集成的真实执行验证（不是 mock）。

覆盖：

1. 进程内 broker（`InMemoryBroker`）真的跑任务、真的产出 taskiq 结果。
2. 同一 task_id 被重复投递时，幂等账本挡住第二次副作用。
3. SQLite broker 跨「生产者关闭 → 新消费者实例」仍然保留待执行消息，
   执行后 ack 删除（= 真实持久化，不是内存队列）。
4. 队列级恢复扫描：崩溃留下的 `running` 行按陈旧阈值回填 `pending`，
   且只有 worker 进程启动时才回填（调度器启动不应抢跑）。
5. taskiq 自带调度：`LabelScheduleSource` 读取 interval label，
   `TaskiqScheduler.on_ready` 真的把恢复扫描任务写进队列。
6. 默认 broker 是 SQLite，不要求 Redis。
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from taskiq.acks import AckableMessage
from taskiq.brokers.inmemory_broker import InMemoryBroker
from taskiq.kicker import AsyncKicker
from taskiq.receiver import Receiver
from taskiq.schedule_sources import LabelScheduleSource
from taskiq.scheduler.scheduler import TaskiqScheduler

from src._time import utc_now
from src.kernels.audit import audit_query
from src.tasks.broker import build_broker, broker_mode, default_queue_db
from src.tasks.registry import PROBE_TASK, RECOVERY_SCAN_TASK, register_tasks
from src.tasks.sqlite_broker import SqliteTaskBroker


def _find_action(events, action: str) -> bool:
    for event in events:
        details = event.get("details") or {}
        if isinstance(details, dict) and details.get("action") == action:
            return True
    return False


async def test_inmemory_broker_really_executes_task(tmp_path):
    broker = build_broker("memory")
    assert isinstance(broker, InMemoryBroker)
    tasks = register_tasks(broker, recovery_interval=60)

    marker = tmp_path / "marker.txt"
    journal_path = str(tmp_path / "tasks.journal")
    handle = await tasks[PROBE_TASK].kiq(
        marker_path=str(marker), journal_path=journal_path
    )
    result = await handle.wait_result(timeout=15)

    assert result.is_err is False
    assert result.return_value["executed"] is True
    assert result.return_value["deduplicated"] is False
    assert marker.read_text(encoding="utf-8").splitlines() == [result.return_value["task_id"]]

    events = audit_query(principal_id="liuhao-worker", limit=50)
    assert _find_action(events, "worker_probe.completed")


async def test_redelivery_of_same_task_id_is_deduplicated(tmp_path):
    broker = build_broker("memory")
    tasks = register_tasks(broker, recovery_interval=60)
    marker = tmp_path / "marker.txt"
    journal_path = str(tmp_path / "tasks.journal")

    kicker = AsyncKicker(PROBE_TASK, broker, {}).with_task_id("fixed-task-id")
    first = await kicker.kiq(marker_path=str(marker), journal_path=journal_path)
    first_result = await first.wait_result(timeout=15)
    assert first_result.return_value["executed"] is True

    # 模拟队列重新投递：同一 task_id 再来一次。
    # 注意：结果后端按 task_id 存结果，第二次 kick 后的 wait_result 可能读到
    # 上一次的旧结果，所以这里用 wait_all() 等执行真正结束，再从副作用文件与
    # 审计记录看是否真的被挡住。
    await kicker.kiq(marker_path=str(marker), journal_path=journal_path)
    await broker.wait_all()

    assert marker.read_text(encoding="utf-8").splitlines() == ["fixed-task-id"]
    events = audit_query(principal_id="liuhao-worker", limit=50)
    assert _find_action(events, "worker_probe.deduplicated")


async def test_sqlite_broker_survives_producer_restart_and_acks(tmp_path):
    db = str(tmp_path / "queue.db")
    journal_path = str(tmp_path / "tasks.journal")
    marker = tmp_path / "marker.txt"

    producer = SqliteTaskBroker(db)
    tasks = register_tasks(producer, recovery_interval=60)
    handle = await tasks[PROBE_TASK].kiq(
        marker_path=str(marker), journal_path=journal_path
    )
    assert producer.pending_count() == 1
    # 生产者进程退出（连接关闭），消息必须还在磁盘上。
    await producer.shutdown()
    assert not marker.exists()

    consumer = SqliteTaskBroker(db)
    consumer.is_worker_process = True
    register_tasks(consumer, recovery_interval=60)
    receiver = Receiver(consumer, max_tasks_to_execute=1, propagate_exceptions=False)
    finish = asyncio.Event()
    await asyncio.wait_for(receiver.listen(finish), timeout=20)

    assert consumer.pending_count() == 0
    assert consumer.running_count() == 0
    assert marker.read_text(encoding="utf-8").splitlines() == [handle.task_id]
    await consumer.shutdown()


async def _insert_running_row(broker: SqliteTaskBroker, claimed_at: str) -> None:
    with broker._lock:
        broker._conn.execute(
            "INSERT INTO task_queue"
            " (task_id, task_name, queue, payload, status, created_at, claimed_at)"
            " VALUES (?, ?, ?, ?, 'running', ?, ?)",
            ("stale-id", PROBE_TASK, broker.queue, b"stale-payload", utc_now().isoformat(), claimed_at),
        )


async def test_requeue_stale_only_happens_in_worker_mode(tmp_path):
    db = str(tmp_path / "queue.db")
    stale_claim = (utc_now() - timedelta(hours=1)).isoformat()

    broker = SqliteTaskBroker(db)
    await _insert_running_row(broker, stale_claim)
    assert broker.running_count() == 1

    await broker.startup()  # 调度器模式：不回填
    assert broker.running_count() == 1
    assert broker.pending_count() == 0

    broker.is_worker_process = True
    await broker.startup()  # worker 模式：回填
    assert broker.running_count() == 0
    assert broker.pending_count() == 1
    await broker.shutdown()


async def test_label_schedule_and_on_ready_enqueue_real_row(tmp_path):
    db = str(tmp_path / "queue.db")
    broker = SqliteTaskBroker(db)
    register_tasks(broker, recovery_interval=60)

    source = LabelScheduleSource(broker)
    await source.startup()
    schedules = await source.get_schedules()
    assert len(schedules) == 1
    schedule = schedules[0]
    assert schedule.task_name == RECOVERY_SCAN_TASK
    assert schedule.interval == 60

    scheduler = TaskiqScheduler(broker, [source])
    await scheduler.on_ready(source, schedule)
    assert broker.pending_count() == 1

    with broker._lock:
        row = broker._conn.execute(
            "SELECT task_name FROM task_queue WHERE status = 'pending'"
        ).fetchone()
    assert row == (RECOVERY_SCAN_TASK,)
    await broker.shutdown()


def test_default_broker_is_sqlite_without_redis(monkeypatch, tmp_path):
    monkeypatch.delenv("LIUHAO_TASK_BROKER", raising=False)
    monkeypatch.delenv("LIUHAO_TASK_DB", raising=False)
    monkeypatch.setenv("LIUHAO_WORKSPACE_ROOT", str(tmp_path))
    assert broker_mode() == "sqlite"
    assert default_queue_db().endswith("tasks.db")


def test_redis_mode_fails_loudly_when_package_missing(monkeypatch):
    monkeypatch.setenv("LIUHAO_TASK_BROKER", "redis")
    monkeypatch.setenv("LIUHAO_REDIS_URL", "redis://localhost:6379/0")
    try:
        build_broker()
    except RuntimeError as exc:
        assert "taskiq-redis" in str(exc)
    else:  # pragma: no cover - 只有显式安装了可选依赖才会走到这里
        pytest.skip("taskiq-redis is installed; Redis broker path active")


async def test_sqlite_broker_uses_wal_and_atomic_claim(tmp_path):
    db = str(tmp_path / "queue.db")
    broker = SqliteTaskBroker(db)
    with broker._lock:
        mode = broker._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"

    # 直接插一条 pending 行，验证 listen() 只交出一次且带 ack。
    with broker._lock:
        broker._conn.execute(
            "INSERT INTO task_queue (task_id, task_name, queue, payload, status, created_at)"
            " VALUES ('id-1', ?, ?, ?, 'pending', ?)",
            ("probe", broker.queue, b"data", utc_now().isoformat()),
        )

    agen = broker.listen()
    message = await agen.__anext__()
    assert isinstance(message, AckableMessage)
    await message.ack()
    await agen.aclose()

    assert broker.pending_count() == 0
    assert broker.running_count() == 0
    await broker.shutdown()
