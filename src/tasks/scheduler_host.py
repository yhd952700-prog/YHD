"""在网关进程内托管本地排程器。

为什么需要它
------------
``LocalScheduler``（时间点任务 / 指数退避重试 / 死信）过去只在
``python -m src.workers.scheduler`` 里 ``run()``。而用户实际启动产品的两条路
——双击桌面向导（``src/desktop``）与 ``scripts/start_liuhao.py`` ——都只拉起
网关和前端，**没有任何进程调用 ``LocalScheduler.run()``**。结果是：

* 聊天里创建的提醒会正常写进 ``reminders.db`` 并往 ``local_scheduler.db``
  入队，但队列永远没人消费 —— 到点不响，也没有任何报错；
* 心跳、更新检查、情报周报同理。

这个模块把排程器交给网关进程托管，让"打开产品"就等于"排程器在跑"。

为什么还要一把锁
----------------
排程器假设**一个数据库只有一个运行者**（启动时 ``recover_expired_leases(
force=True)`` 会回收所有仍标记 running 的行）。编码为多进程运行会互相抢走
对方正在执行的任务。所以真正 ``run()`` 之前必须先拿到
``<实例根>/run/local_scheduler.lock``：

* 桌面单进程：网关拿到锁并运行 —— 这正是要修的那条断链；
* supervisor 部署：``liuhao-scheduler`` 与网关都会尝试，先到者运行，另一个
  记一条 INFO 后跳过，不会双跑；
* 进程被杀：内核自动释放锁，不会留下僵尸锁。
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

from src._time import utc_now
from src.common.instance_lock import (
    InstanceGuardUnavailable,
    InstanceLock,
    InstanceLockBusy,
    instance_root,
)

from .local_handlers import build_local_scheduler
from .local_scheduler import LocalScheduler, local_scheduler_enabled

logger = logging.getLogger(__name__)

#: 与网关自己的 ``instance.lock`` 区分开：同名锁会在同一进程里二次加锁失败。
LOCK_NAME = "local_scheduler"

#: ``1`` 强制在网关进程内托管，``0`` 强制关闭；不设置时按环境默认。
SCHEDULER_HOST_ENV = "LIUHAO_GATEWAY_SCHEDULER"

_TRUTHY = {"1", "true", "yes", "on", "enabled"}
_FALSY = {"0", "false", "no", "off", "disabled"}


def hosting_enabled() -> bool:
    """网关是否应在自己进程里托管排程器。

    默认值必须是"本地开、生产关"：supervisor 部署里网关是 ``--workers 4``，
    而排程器由独立的 ``liuhao-scheduler``（numprocs=1）负责；若每个网关
    worker 都托管一份，会变成 5 个运行者抢同一个 SQLite 库。显式设置该变量
    可覆盖默认。
    """
    raw = (os.environ.get(SCHEDULER_HOST_ENV) or "").strip().lower()
    if raw:
        if raw in _TRUTHY:
            return True
        if raw in _FALSY:
            return False
        raise ValueError(
            f"{SCHEDULER_HOST_ENV} must be one of {sorted(_TRUTHY | _FALSY)}, "
            f"got {raw!r}"
        )
    # 测试进程里默认不托管：网关会在大量用例中被反复构建，若每个都起排程器，
    # 会向仓库根目录写锁和队列库，污染其它用例。需要验证托管的用例显式设 1。
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    env = (
        os.environ.get("LIUHAO_ENV") or os.environ.get("APP_ENV") or ""
    ).strip().lower()
    return env not in {"production", "prod"}


def claim_local_scheduler_owner(
    root: Optional[str | os.PathLike[str]] = None,
) -> Optional[InstanceLock]:
    """抢占"谁运行排程器"的锁；抢不到返回 ``None``。

    worker（``src/workers/runner.py``）在 supervisor 下是 ``numprocs=2``，两个
    进程过去都会 ``run()`` 同一个库，启动时的 ``recover_expired_leases(
    force=True)`` 会把对方在途任务抢回来重发 —— 同一件事弹两次。用它把
    "同时只有一个运行者"变成硬约束。
    """
    base = Path(root) if root is not None else instance_root(Path.cwd())
    guard = InstanceLock(base, subdir="run", name=LOCK_NAME)
    try:
        guard.acquire()
    except (InstanceLockBusy, InstanceGuardUnavailable) as exc:
        logger.info("本地排程器让位：%s", exc)
        return None
    return guard


class HostedLocalScheduler:
    """网关生命周期内持有并运行本地排程器（拿不到锁时静默让位）。"""

    def __init__(
        self,
        *,
        root: Optional[str | os.PathLike[str]] = None,
        scheduler: Optional[LocalScheduler] = None,
        stop_timeout: float = 5.0,
    ) -> None:
        self._root = Path(root) if root is not None else instance_root(Path.cwd())
        self._scheduler = scheduler
        self._stop_timeout = stop_timeout
        self._guard: Optional[InstanceLock] = None
        self._task: Optional[asyncio.Task] = None
        self.reason = ""

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def scheduler(self) -> Optional[LocalScheduler]:
        return self._scheduler

    def start(self) -> bool:
        """在**当前事件循环**里启动；返回是否由本进程运行。

        所有失败路径都只记日志并返回 ``False``：托管是增强能力，绝不能
        阻断网关启动（提醒不响是遗憾，网关起不来是事故）。
        """
        if self._task is not None:
            return self.running
        try:
            if not hosting_enabled():
                self.reason = (
                    f"按配置不在网关内托管（{SCHEDULER_HOST_ENV}=0 或生产环境）"
                )
                logger.info("本地排程器未启动：%s", self.reason)
                return False
            if not local_scheduler_enabled():
                self.reason = "已按配置关闭本地排程器（LIUHAO_LOCAL_SCHEDULER）"
                logger.info("本地排程器未启动：%s", self.reason)
                return False
        except ValueError as exc:
            self.reason = f"配置无效：{exc}"
            logger.error("本地排程器未启动：%s", self.reason)
            return False

        guard = InstanceLock(self._root, subdir="run", name=LOCK_NAME)
        try:
            guard.acquire()
        except InstanceLockBusy:
            self.reason = "另一个进程已在运行本地排程器"
            logger.info("本地排程器未启动：%s（提醒由那个进程触发）", self.reason)
            return False
        except InstanceGuardUnavailable as exc:
            self.reason = f"无法获取排程器锁：{exc}"
            logger.warning("本地排程器未启动：%s", self.reason)
            return False

        try:
            scheduler = self._scheduler or build_local_scheduler()
            task = asyncio.create_task(scheduler.run(), name="local-scheduler")
        except Exception as exc:  # noqa: BLE001 - 托管失败不能拖垮网关
            guard.release()
            self.reason = f"{type(exc).__name__}: {exc}"
            logger.error("本地排程器启动失败：%s", self.reason, exc_info=True)
            return False

        task.add_done_callback(self._on_task_done)
        self._guard = guard
        self._scheduler = scheduler
        self._task = task
        self.reason = "运行中"
        guard.publish(
            pid=os.getpid(),
            role="gateway",
            started_at=utc_now().isoformat(),
            db=getattr(scheduler, "db_path", None),
        )
        logger.info("本地排程器已在网关进程内启动（db=%s）", getattr(scheduler, "db_path", "?"))
        return True

    async def stop(self) -> None:
        """停止并释放锁；任何异常都不向调用方抛出。"""
        task, scheduler = self._task, self._scheduler
        self._task = None
        if scheduler is not None:
            try:
                scheduler.stop()
            except Exception:  # noqa: BLE001
                logger.exception("本地排程器停止失败")
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=self._stop_timeout)
            except asyncio.TimeoutError:
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=1.0)
                except BaseException:  # noqa: BLE001 - 已经是收尾阶段
                    pass
            except asyncio.CancelledError:
                # 任务自身被取消（scheduler.stop/取消传播），不是调用方被取消。
                pass
            except Exception:  # noqa: BLE001
                logger.exception("本地排程器退出时出错")
        if scheduler is not None:
            try:
                scheduler.close()
            except Exception:  # noqa: BLE001
                logger.exception("本地排程器关闭连接失败")
        if self._guard is not None:
            try:
                self._guard.clear_metadata()
                self._guard.release()
            finally:
                self._guard = None
        if scheduler is not None:
            self._scheduler = None

    def _on_task_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            self.reason = f"异常退出：{type(exc).__name__}: {exc}"
            logger.error("本地排程器异常退出：%s", self.reason, exc_info=exc)


__all__ = [
    "HostedLocalScheduler",
    "LOCK_NAME",
    "SCHEDULER_HOST_ENV",
    "claim_local_scheduler_owner",
    "hosting_enabled",
]
