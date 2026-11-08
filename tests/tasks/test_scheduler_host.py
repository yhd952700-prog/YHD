"""网关内托管本地排程器的契约。

历史缺陷：桌面版与 ``scripts/start_liuhao.py`` 只拉起网关，没有任何进程
``LocalScheduler.run()``；提醒照常入队却永远不触发，而且没有报错。这里钉死
三条契约：

1. 本地/命令行默认在网关内托管；生产环境默认不托管（由独立 scheduler 进程负责）；
2. 托管期间真的会出队并执行 handler；
3. 同一个实例根只允许一个运行者，第二个必须安静让位而不是双跑。
"""

from __future__ import annotations

import asyncio

import pytest

from src.tasks.local_scheduler import LocalScheduler
from src.tasks.scheduler_host import (
    SCHEDULER_HOST_ENV,
    HostedLocalScheduler,
    claim_local_scheduler_owner,
    hosting_enabled,
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """每个用例都在自己的实例根/队列库里，不碰真实数据。"""
    monkeypatch.delenv(SCHEDULER_HOST_ENV, raising=False)
    monkeypatch.delenv("LIUHAO_INSTANCE_DIR", raising=False)
    monkeypatch.delenv("LIUHAO_LOCAL_SCHEDULER", raising=False)
    monkeypatch.delenv("LIUHAO_ENV", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    # pytest 每个 phase 都会重设 PYTEST_CURRENT_TEST；本文件的用例验证"托管行为"
    # 本身，显式打开开关，避免默认值在测试进程里被降级为关闭。
    monkeypatch.setenv(SCHEDULER_HOST_ENV, "1")
    monkeypatch.setenv("LIUHAO_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    return tmp_path


def _scheduler(tmp_path, name: str) -> LocalScheduler:
    return LocalScheduler(
        str(tmp_path / "queue" / f"{name}.db"), tick_seconds=0.05
    )


def test_hosting_enabled_defaults_local_on_production_off(monkeypatch):
    monkeypatch.delenv(SCHEDULER_HOST_ENV, raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    assert hosting_enabled() is True
    monkeypatch.setenv("LIUHAO_ENV", "production")
    assert hosting_enabled() is False
    monkeypatch.setenv(SCHEDULER_HOST_ENV, "1")
    assert hosting_enabled() is True
    monkeypatch.setenv(SCHEDULER_HOST_ENV, "0")
    assert hosting_enabled() is False
    monkeypatch.setenv(SCHEDULER_HOST_ENV, "maybe")
    with pytest.raises(ValueError):
        hosting_enabled()


async def test_started_host_really_executes_due_jobs(_isolate_env):
    tmp_path = _isolate_env
    scheduler = _scheduler(tmp_path, "main")
    seen: list[int] = []

    async def handler(value: int = 0, **_):
        seen.append(value)
        return {"ok": True}

    scheduler.register("test.echo", handler)
    scheduler.enqueue("test.echo", delay_seconds=0.0, payload={"value": 7})

    host = HostedLocalScheduler(root=tmp_path / "root", scheduler=scheduler)
    try:
        assert host.start() is True
        assert host.running is True
        deadline = asyncio.get_running_loop().time() + 5
        while not seen and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.02)
        assert seen == [7]
    finally:
        await host.stop()
    assert host.running is False


async def test_second_host_yields_instead_of_double_running(_isolate_env, tmp_path):
    scheduler_a = _scheduler(tmp_path, "a")
    scheduler_b = _scheduler(tmp_path, "b")
    host_a = HostedLocalScheduler(root=tmp_path / "root", scheduler=scheduler_a)
    host_b = HostedLocalScheduler(root=tmp_path / "root", scheduler=scheduler_b)
    assert host_a.start() is True
    try:
        assert host_b.start() is False
        assert "另一个进程" in host_b.reason
        assert host_b.running is False
    finally:
        await host_a.stop()

    # 锁随停止释放：同一实例根可以再次托管（例如网关重启）。
    scheduler_c = _scheduler(tmp_path, "c")
    host_c = HostedLocalScheduler(root=tmp_path / "root", scheduler=scheduler_c)
    assert host_c.start() is True
    await host_c.stop()


async def test_hosting_disabled_does_not_touch_queue(_isolate_env, tmp_path, monkeypatch):
    monkeypatch.setenv(SCHEDULER_HOST_ENV, "0")
    scheduler = _scheduler(tmp_path, "off")
    host = HostedLocalScheduler(root=tmp_path / "root", scheduler=scheduler)
    assert host.start() is False
    assert "不在网关内托管" in host.reason
    await host.stop()


async def test_owner_claim_serializes_runners(_isolate_env, tmp_path):
    """worker/scheduler/gateway 三种角色共用一把锁：第二个只能让位。"""
    first = claim_local_scheduler_owner(tmp_path / "root")
    assert first is not None
    second = claim_local_scheduler_owner(tmp_path / "root")
    assert second is None
    first.release()
    third = claim_local_scheduler_owner(tmp_path / "root")
    assert third is not None
    third.release()
