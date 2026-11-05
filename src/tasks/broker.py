"""任务队列 broker 选择器 + 职责边界说明。

## 边界（写死在代码里，别将来混用）

| 维度 | taskiq 任务队列（本包） | msg_bus 事件总线（`src/distribution/msg_bus.py`） |
|---|---|---|
| 消息语义 | 「执行这个任务一次」 | 「这件事发生了」 |
| 关键字段 | task_name / 参数 / task_id | topic / payload / event_id |
| 消费者 | worker（点对点消费，可重试） | 任意订阅者（扇出广播） |
| 投递保证 | 至少一次 + 幂等键兜底 | 事件流，消费进度由订阅方维护 |
| 典型用途 | goal 恢复扫描、记忆固化、知识摄取 | UI 状态推送、指标、审计旁路 |

同一个 Redis 实例可以同时承载两者，但**语义不共享**：不要用 msg_bus 排队
跑任务（没有重试/结果/幂等语义），也不要用 taskiq 广播事件（每个任务只会
被一个 worker 消费）。

## 默认不依赖 Redis

`LIUHAO_TASK_BROKER` 取值：

* `sqlite`（默认）—— 单机多进程可用，不需要 Redis/Postgres，落盘在
  `LIUHAO_TASK_DB`（默认 `LIUHAO_WORKSPACE_ROOT/queue/tasks.db`，
  否则 `~/.liuhao/queue/tasks.db`）。
* `memory` —— 进程内执行（`InMemoryBroker`），只适合单进程开发/测试；
  跨进程的 worker 看不到它。
* `redis` —— **可选且默认关闭**。需要显式设置 `LIUHAO_TASK_BROKER=redis`
  且安装 `taskiq-redis`（`pip install -e ".[taskiq-redis]"`），连接串取
  `LIUHAO_REDIS_URL`。未安装时抛明确错误，不做静默降级。
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Optional

from taskiq.abc.broker import AsyncBroker

from src.tasks.sqlite_broker import SqliteTaskBroker

logger = logging.getLogger(__name__)

_BROKERS = ("sqlite", "memory", "redis")


def broker_mode() -> str:
    mode = (os.environ.get("LIUHAO_TASK_BROKER") or "sqlite").strip().lower()
    if mode not in _BROKERS:
        raise ValueError(
            f"unknown LIUHAO_TASK_BROKER={mode!r}; expected one of {_BROKERS}"
        )
    return mode


def default_queue_db() -> str:
    explicit = os.environ.get("LIUHAO_TASK_DB")
    if explicit:
        return explicit
    workspace = os.environ.get("LIUHAO_WORKSPACE_ROOT")
    base = (
        os.path.join(workspace, "queue")
        if workspace
        else os.path.join(os.path.expanduser("~"), ".liuhao", "queue")
    )
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "tasks.db")


def build_broker(mode: Optional[str] = None, db_path: Optional[str] = None) -> AsyncBroker:
    """Build a broker instance; explicit args win over environment."""
    selected = mode.strip().lower() if mode else broker_mode()

    if selected == "memory":
        from taskiq.brokers.inmemory_broker import InMemoryBroker

        # await_inplace=False keeps kick() async like a real queue.
        return InMemoryBroker(await_inplace=False)

    if selected == "sqlite":
        return SqliteTaskBroker(
            db_path or default_queue_db(),
            queue=os.environ.get("LIUHAO_TASK_QUEUE") or "default",
        )

    if selected == "redis":
        try:
            from taskiq_redis import ListQueueBroker
        except ImportError as exc:  # pragma: no cover - 取决于可选依赖
            raise RuntimeError(
                "LIUHAO_TASK_BROKER=redis requires the optional 'taskiq-redis' "
                "package; install with pip install -e '.[taskiq-redis]' or "
                "use LIUHAO_TASK_BROKER=sqlite (no Redis needed)."
            ) from exc
        url = os.environ.get("LIUHAO_REDIS_URL")
        if not url:
            raise RuntimeError(
                "LIUHAO_TASK_BROKER=redis requires LIUHAO_REDIS_URL to be set."
            )
        return ListQueueBroker(url=url)

    raise ValueError(f"unknown broker mode {selected!r}")


_broker: Optional[AsyncBroker] = None
_broker_lock = threading.Lock()


def get_broker() -> AsyncBroker:
    """Process-wide broker singleton used by the worker/scheduler entrypoints."""
    global _broker
    if _broker is None:
        with _broker_lock:
            if _broker is None:
                _broker = build_broker()
                logger.info("task broker selected: %s", broker_mode())
    return _broker
