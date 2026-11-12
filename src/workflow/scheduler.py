"""工作流的定时 / 事件触发与审批超时扫描。

触发方式是"最小可信"实现，不引入调度框架：
- ``interval``：每 N 秒一次（``next_due_at`` 由 store 维护）
- ``daily``：每天固定 ``HH:MM``（按本机时区）
- ``event``：订阅现有事件总线，事件名与触发器配置一致时启动一次运行

扫描线程默认 5 秒一拍；设置了 ``LIUHAO_WORKFLOW_SCHEDULER=0`` 时只保留手动
``tick()``（测试与"我不想让它自己跑"的场景都用这个开关）。
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict, List, Optional

from .engine import WorkflowRuntime, get_workflow_runtime
from .store import WorkflowStore, get_workflow_store

logger = logging.getLogger(__name__)

SCHEDULER_ENV_VAR = "LIUHAO_WORKFLOW_SCHEDULER"
DEFAULT_INTERVAL_SECONDS = 5.0


class WorkflowScheduler:
    def __init__(
        self,
        store: Optional[WorkflowStore] = None,
        runtime: Optional[WorkflowRuntime] = None,
        *,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        autostart: bool = True,
    ) -> None:
        self.store = store or get_workflow_store()
        self.runtime = runtime or get_workflow_runtime()
        self.interval_seconds = max(1.0, float(interval_seconds))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._subscribed: set[str] = set()
        self._subscribe_lock = threading.Lock()
        self._tick_lock = threading.Lock()
        if autostart and os.environ.get(SCHEDULER_ENV_VAR, "1") != "0":
            self.start()

    # ── 生命周期 ────────────────────────────────────────────

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="workflow-scheduler", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    def _loop(self) -> None:
        # 首拍稍等，避免和网关启动抢 IO。
        if self._stop.wait(1.0):
            return
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - 调度线程不能死
                logger.exception("workflow scheduler tick failed")
            if self._stop.wait(self.interval_seconds):
                return

    # ── 单拍 ────────────────────────────────────────────────

    def tick(self) -> Dict[str, Any]:
        with self._tick_lock:
            expired_runs = self.runtime.expire_overdue()
            fired: List[Dict[str, str]] = []
            for trigger in self.store.due_triggers():
                record = self.store.get_definition(str(trigger["def_id"]))
                if record is None or not record.get("enabled"):
                    self.store.mark_trigger_fired(str(trigger["trigger_key"]), retire=True)
                    continue
                try:
                    run = self.runtime.start_run(
                        record,
                        trigger_type=str(trigger["trigger_type"]),
                        trigger_payload={
                            "trigger_key": trigger["trigger_key"],
                            "config": trigger.get("config") or {},
                        },
                        inputs={},
                        submitted_by="system",
                        owner_account_id=str(record.get("owner_account_id") or ""),
                    )
                    fired.append({"def_id": record["def_id"], "run_id": run["run_id"]})
                except Exception as exc:  # noqa: BLE001 - 单条触发失败不影响其它
                    logger.warning(
                        "workflow trigger failed: %s (%s)", trigger["trigger_key"], exc
                    )
                finally:
                    self.store.mark_trigger_fired(str(trigger["trigger_key"]))
            self._ensure_event_subscriptions()
            return {"expired_runs": expired_runs, "fired": fired}

    # ── 事件触发 ────────────────────────────────────────────

    def fire_event(self, event_name: str, payload: Optional[Dict[str, Any]] = None) -> List[Dict[str, str]]:
        """按事件名启动所有匹配的流程；返回实际启动的运行。"""
        started: List[Dict[str, str]] = []
        for trigger in self.store.event_triggers(event_name):
            if not self._matches(trigger.get("config") or {}, payload or {}):
                continue
            record = self.store.get_definition(str(trigger["def_id"]))
            if record is None or not record.get("enabled"):
                continue
            try:
                run = self.runtime.start_run(
                    record,
                    trigger_type="event",
                    trigger_payload={"event": event_name, "payload": payload or {}},
                    inputs={"event": payload or {}},
                    submitted_by="system",
                    owner_account_id=str(record.get("owner_account_id") or ""),
                )
                started.append({"def_id": record["def_id"], "run_id": run["run_id"]})
            except Exception:  # noqa: BLE001
                logger.exception("workflow event trigger failed: %s", trigger["trigger_key"])
        return started

    @staticmethod
    def _matches(config: Dict[str, Any], payload: Dict[str, Any]) -> bool:
        filters = config.get("filters")
        if not isinstance(filters, dict) or not filters:
            return True
        for key, expected in filters.items():
            actual = payload.get(key)
            if actual is None:
                return False
            if str(actual) != str(expected):
                return False
        return True

    def _ensure_event_subscriptions(self) -> None:
        """为出现过的每个事件名注册一次总线订阅（重复触发由查询结果兜底）。"""
        try:
            from src.kernels.event import subscribe_event
        except Exception:  # noqa: BLE001 - 事件内核不可用时事件触发退化
            return
        with self._subscribe_lock:
            for trigger in self.store.list_triggers(kind="event"):
                event_name = str((trigger.get("config") or {}).get("event") or "")
                if not event_name or event_name in self._subscribed:
                    continue
                try:
                    subscribe_event(
                        event_name,
                        lambda event, name=event_name: self._on_bus_event(name, event),
                    )
                except Exception:  # noqa: BLE001
                    continue
                self._subscribed.add(event_name)

    def _on_bus_event(self, event_name: str, event: Any) -> None:
        data = getattr(event, "data", None)
        payload = data if isinstance(data, dict) else {"value": data}
        try:
            self.fire_event(event_name, payload)
        except Exception:  # noqa: BLE001
            logger.exception("workflow bus handler failed: %s", event_name)


_scheduler: Optional[WorkflowScheduler] = None
_scheduler_lock = threading.Lock()


def get_workflow_scheduler() -> WorkflowScheduler:
    global _scheduler
    if _scheduler is None:
        with _scheduler_lock:
            if _scheduler is None:
                _scheduler = WorkflowScheduler()
    return _scheduler


def reset_workflow_scheduler() -> None:
    global _scheduler
    with _scheduler_lock:
        if _scheduler is not None:
            _scheduler.stop()
        _scheduler = None
