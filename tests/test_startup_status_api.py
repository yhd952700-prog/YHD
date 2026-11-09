"""启动阶段计时与 /v1/startup 契约的回归测试。

覆盖三件事：
1. 阶段打点真的记录了启动各段（不是装饰性的空表）；
2. ``GET /v1/startup`` 对**本机回环**免鉴权可读、结构符合给前端的契约；
   远端匿名请求拿不到启动阶段/耗时/降级组件名（统一 404）；
3. **后台初始化失败必须可见**（``state="degraded"`` 且 ``background.errors``
   非空），而不是悄悄吞掉 —— 这正是"挪到后台"最容易被做坏的地方。
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from src.gateway import startup_profile
from src.gateway.main import get_app


@pytest.fixture()
def client():
    with TestClient(get_app()) as test_client:
        yield test_client


def _wait_background_done(timeout: float = 15.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = startup_profile.background_state()
        if state["status"] in ("done", "failed"):
            return state
        time.sleep(0.05)
    return startup_profile.background_state()


def test_startup_profile_records_real_stages(client):
    snapshot = startup_profile.snapshot()
    names = [stage["name"] for stage in snapshot["stages"]]

    # 这些阶段必须真实存在：它们就是"冷启动时间花在哪"的答案。
    required = [
        "进入 src.gateway 包",
        "src.gateway.main 开始导入",
        "main 顶层依赖导入完成",
        "get_app 开始",
        "运行配置加载完成",
        "中间件注册完成，开始注册路由",
        "全部路由 + 静态驾驶舱挂载完成",
        "lifespan 启动开始",
        "密钥 / JWT / RBAC 初始化完成",
        "内核生命周期 initialize_all 完成",
        "就绪：开始接受请求",
    ]
    for expected in required:
        assert any(name.startswith(expected) for name in names), (
            f"缺少启动阶段打点：{expected}"
        )

    # 阶段耗时必须是单调递增的累计值，且与本段耗时自洽。
    elapsed = [stage["elapsed_ms"] for stage in snapshot["stages"]]
    assert elapsed == sorted(elapsed)
    deltas = [stage["delta_ms"] for stage in snapshot["stages"]]
    assert all(delta >= 0 for delta in deltas)


def test_startup_endpoint_is_loopback_public_and_reports_ready(client):
    response = client.get("/v1/startup")
    assert response.status_code == 200
    body = response.json()

    assert body["core_ready"] is True
    assert body["state"] in ("ready", "degraded")
    assert body["elapsed_ms"] >= 0
    assert isinstance(body["stages"], list) and body["stages"]
    assert set(body["background"]) >= {
        "status", "started_at_ms", "finished_at_ms", "steps", "errors"
    }
    assert response.headers["cache-control"] == "no-store"

    # 就绪语义：/v1/health 也必须是 200（两者一致）。
    assert client.get("/v1/health").status_code == 200


def test_startup_endpoint_from_loopback_requires_no_token(client):
    # 启动期间用户可能还没登录；但只有本机回环能读。
    body = client.get("/v1/startup").json()
    assert body["core_ready"] is True


def test_startup_endpoint_refuses_remote_client():
    """远端匿名请求不能拿到启动拓扑。"""
    with TestClient(get_app(), client=("203.0.113.5", 50000)) as remote:
        response = remote.get("/v1/startup")
    assert response.status_code == 404
    assert response.json().get("detail") == "Not Found"


def test_startup_endpoint_can_be_explicitly_disabled(monkeypatch):
    """``LIUHAO_STARTUP_STATUS=0``：连本机回环也 404（反代部署的进程层兜底）。"""
    monkeypatch.setenv("LIUHAO_STARTUP_STATUS", "0")
    with TestClient(get_app()) as local:
        response = local.get("/v1/startup")
    assert response.status_code == 404
    assert response.json().get("detail") == "Not Found"


def test_background_warmup_completes_and_is_reported(client):
    state = _wait_background_done()
    assert state["status"] == "done", state
    assert state["errors"] == []
    step_names = [step["name"] for step in state["steps"]]
    assert "预热对话链（LLM/provider/会话存储）" in step_names


def test_background_failure_is_visible_not_swallowed(monkeypatch):
    """强行让后台预热失败：/v1/startup 必须报 degraded + 真实错误。"""

    def _boom() -> None:
        raise RuntimeError("warmup exploded (test)")

    from src.gateway import chat as chat_module

    monkeypatch.setattr(chat_module, "warmup", _boom)

    with TestClient(get_app()) as test_client:
        state = _wait_background_done()
        assert state["status"] == "failed"
        assert any(
            "warmup exploded (test)" in entry["error"]
            for entry in state["errors"]
        )
        body = test_client.get("/v1/startup").json()
        assert body["state"] == "degraded"
        # 核心功能依然可用：失败的是可选组件，不应把整个网关判死。
        assert body["core_ready"] is True
        assert test_client.get("/v1/health").status_code == 200


def test_scheduler_start_failure_is_visible_in_startup_errors(monkeypatch):
    """排程器起不来时，中文原因必须出现在 /v1/startup.background.errors。

    回归 OPS-004：后台线程里 ``asyncio.create_task`` 抛
    ``no running event loop``，旧代码只记 ERROR 日志，启动器显示
    "后台组件未就绪（原因未上报）"。
    """
    from src.tasks.scheduler_host import HostedLocalScheduler

    def _fail_start(self, loop=None):
        self.reason = "启动位置错误：当前线程没有运行中的事件循环"
        self.start_error = (
            "本地排程器启动失败：调用 start() 的线程没有运行中的事件循环"
            "（RuntimeError: no running event loop）。提醒与定时任务不会触发"
        )
        return False

    monkeypatch.setattr(HostedLocalScheduler, "start", _fail_start)
    # 不等首屏：把这个失败尽快推到 /v1/startup，避免用例空等。
    monkeypatch.setenv("LIUHAO_SCHEDULER_GRACE_S", "0")
    monkeypatch.setenv("LIUHAO_WARMUP_GRACE_S", "0")

    with TestClient(get_app()) as test_client:
        deadline = time.time() + 10
        body: dict = {}
        while time.time() < deadline:
            body = test_client.get("/v1/startup").json()
            if body["background"]["errors"]:
                break
            time.sleep(0.05)

        errors = body["background"]["errors"]
        assert any(
            "启动本地排程器" in entry["step"] and "事件循环" in entry["error"]
            for entry in errors
        ), errors
        assert body["background"]["status"] == "failed"
        assert body["state"] == "degraded"
        assert body["core_ready"] is True
