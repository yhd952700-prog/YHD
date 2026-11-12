"""G8/G9 P0：一个「静默 un-fenced」的进程必须至少是可观测的。

被修的真实缺陷
--------------
``src/gateway/main.py`` 的 lifespan 在 ``attach_default_executor_fence()``
失败时会打一条 ERROR 日志然后**继续启动**。于是进程带着「自主动作未受 fence
约束」的状态对外服务，而这个降级状态在当时是**三重不可见**：

1. 没有指标（Prometheus 里根本没有对应的 family）；
2. ``/v1/ready`` 不反映它（依然 healthy / 200）;
3. 没有任何测试断言它。

也就是说：运维只能靠人肉翻日志才能发现「主权护栏没装上」。本文件的每一条断言
都对准这三处之一，且每条都有**对照**（负对照验证故障可见、正对照验证健康时不
误报）。

设计约束（不要为了通过测试而破坏它们）
--------------------------------------
* fence 只在「自主动作强制开启」（``LIUHAO_EXECUTOR_FENCE=on``，即
  ``executor_fence_armed()``）时才有意义。gate 合法未武装的部署**不得**被标
  降级 —— :class:`TestUnarmedDeploymentIsUnchanged` 守着这条。
* ``LIUHAO_REQUIRE_EXECUTOR_FENCE=1`` 是 opt-in 硬失败，默认关闭；本文件只验证
  它在开启时会中止启动，不改变默认值。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import src.kernels.execution.fence as fence_module
from src.gateway.main import get_app
from src.observability.metrics import REGISTRY

INSTALLED_GAUGE = "liuhao_executor_fence_installed"
FAILURE_COUNTER = "liuhao_executor_fence_install_failures_total"


def _sample(name: str) -> float:
    """真实的 Prometheus 采样值（不是「这个 family 被定义过」）。"""
    return REGISTRY.get_sample_value(name, {}) or 0.0


@pytest.fixture(autouse=True)
def _isolate_fence_install_state():
    """隔离进程级安装状态：状态字典与两个指标都是模块级全局。

    不隔离的话，负对照会把 ``attached=False`` 泄漏给同会话里的其它用例（例如
    ``tests/observability/test_fence_metrics.py::test_ready_exposes_
    executor_fence``），那不是被测代码的问题，而是测试之间的串味。
    """
    saved = dict(fence_module._DEFAULT_FENCE_STATUS)
    yield
    fence_module._DEFAULT_FENCE_STATUS.update(saved)


@pytest.fixture
def broken_attach(monkeypatch):
    """让真实的启动接线失败：注入一个会抛的安装函数。

    ``main.py`` 是在 lifespan **内部** ``from ..kernels.execution.fence import
    attach_default_executor_fence`` 的，因此这里 patch 模块属性会被真实调用路径
    取到 —— 走的是真的 try/except，而不是复制一份逻辑。
    """

    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated lease backend unavailable")

    monkeypatch.setattr(
        fence_module, "attach_default_executor_fence", _boom
    )
    return _boom


@pytest.fixture
def armed(monkeypatch):
    """打开自主动作强制（fence 此时才是「必需」的）。"""
    monkeypatch.setenv("LIUHAO_EXECUTOR_FENCE", "on")


def _boot():
    """启动一次真实 gateway（lifespan 跑完整段接线）并返回 client。"""
    return TestClient(get_app())


# --------------------------------------------------------------------------- #
# 1. 负对照：安装失败必须留下可查询的持久状态
# --------------------------------------------------------------------------- #
class TestFailedInstallIsRecorded:
    def test_status_reports_not_attached_with_the_error(self, broken_attach, armed):
        with _boot():
            status = fence_module.get_default_fence_status()

        assert status["attached"] is False, "安装失败了却报告 attached=True"
        assert status["error"], "失败状态必须带上错误文本，否则运维无从下手"
        assert "simulated lease backend unavailable" in status["error"]
        assert isinstance(status["failed_at"], float), "失败时刻必须可查（用于告警排序）"

    def test_installed_gauge_reads_zero(self, broken_attach, armed):
        # 先把 gauge 置成 1（模拟「上一轮是装上的」），否则 0 可能只是没被碰过的
        # 初始值 —— 那样这条断言就是空的。置 1 后必须被**主动**改回 0。
        from src.observability import metrics as metrics_module

        metrics_module.liuhao_executor_fence_installed.set(1)
        with _boot():
            pass
        assert _sample(INSTALLED_GAUGE) == 0.0, (
            "fence 没装上，但 liuhao_executor_fence_installed 不是 0 —— "
            "这个 gauge 说谎就等于没有 gauge"
        )

    def test_failure_counter_increments(self, broken_attach, armed):
        before = _sample(FAILURE_COUNTER)
        with _boot():
            pass
        after = _sample(FAILURE_COUNTER)
        assert after - before == 1.0, f"失败计数没有 +1: {before} -> {after}"

    def test_ready_is_degraded_and_names_the_fence(self, broken_attach, armed):
        with _boot() as client:
            response = client.get("/v1/ready")
            body = response.json()

        check = body["checks"]["executor_fence"]
        assert check["status"] == "degraded", (
            "fence 未安装而 /v1/ready 仍然健康 —— 这正是本次要关掉的静默 fail-open"
        )
        assert check["installed"] is False
        assert response.status_code == 503, "降级必须落到 HTTP 503，而不是 200 + 一句警告"
        assert body["status"] == "degraded"
        assert any("Executor Fence" in e for e in body.get("errors", [])), (
            "errors 里没有点名 fence，运维看到的只是一句泛泛的 degraded"
        )

    def test_require_flag_aborts_startup(self, broken_attach, armed, monkeypatch):
        monkeypatch.setenv("LIUHAO_REQUIRE_EXECUTOR_FENCE", "1")
        with pytest.raises(Exception) as excinfo:
            with _boot():
                pass
        message = str(excinfo.value)
        assert "EXECUTOR FENCE REQUIRED" in message, message
        assert "LIUHAO_REQUIRE_EXECUTOR_FENCE" in message, message
        # 中止前也必须把失败状态记录下来（供 crash-loop 后的取证）。
        assert fence_module.get_default_fence_status()["attached"] is False

    def test_unknown_backend_is_a_real_install_failure(self, armed, monkeypatch):
        """不用 monkeypatch 造假：让**真实**的 attach 走未知后端分支抛错。

        这条比注入假函数更有价值：它证明真实代码路径的失败确实会落到记录 /
        指标 / 就绪三条链路上，而不是只证明「我们新写的 recorder 能用」。
        """
        monkeypatch.setenv("LIUHAO_DISTRIBUTED_LEASE_BACKEND", "__not_a_backend__")
        with _boot() as client:
            body = client.get("/v1/ready").json()

        status = fence_module.get_default_fence_status()
        assert status["attached"] is False
        assert "Unsupported executor-fence backend" in (status["error"] or "")
        assert _sample(INSTALLED_GAUGE) == 0.0
        assert body["checks"]["executor_fence"]["status"] == "degraded"


# --------------------------------------------------------------------------- #
# 2. 正对照：健康安装不得误报
# --------------------------------------------------------------------------- #
class TestHealthyInstall:
    def test_status_reports_attached(self, armed):
        with _boot():
            status = fence_module.get_default_fence_status()

        assert status["attached"] is True, (
            "fence 装上了却报告 attached=False —— 假警报会训练运维忽略告警"
        )
        assert status["error"] is None
        assert status["failed_at"] is None

    def test_installed_gauge_reads_one(self, armed):
        # 同上：先置 0，证明健康启动会**主动**把它改成 1，而不是碰巧没被改过。
        from src.observability import metrics as metrics_module

        metrics_module.liuhao_executor_fence_installed.set(0)
        with _boot():
            pass
        assert _sample(INSTALLED_GAUGE) == 1.0

    def test_failure_counter_does_not_move(self, armed):
        before = _sample(FAILURE_COUNTER)
        with _boot():
            pass
        assert _sample(FAILURE_COUNTER) == before, "健康启动却计入了失败"

    def test_ready_is_not_degraded_by_the_fence(self, armed):
        with _boot() as client:
            body = client.get("/v1/ready").json()

        check = body["checks"]["executor_fence"]
        assert check["status"] == "healthy", check
        assert check["installed"] is True
        assert not any("Executor Fence" in e for e in body.get("errors", []))


# --------------------------------------------------------------------------- #
# 3. 回归护栏：fence 合法不必要时，行为必须与改动前完全一致
# --------------------------------------------------------------------------- #
class TestUnarmedDeploymentIsUnchanged:
    def test_unarmed_and_uninstalled_is_still_healthy(self, broken_attach):
        """gate 没武装 => fence 不是必需的 => 不得把部署打成降级。"""
        with _boot() as client:
            body = client.get("/v1/ready").json()

        check = body["checks"]["executor_fence"]
        assert check["armed"] is False
        assert check["status"] == "healthy", (
            "未武装的部署被标成降级了 —— 这会把「配置选择」误报成「故障」"
        )
        assert "note" in check, "未武装时必须保留那条信息性说明"
        assert not any("Executor Fence" in e for e in body.get("errors", []))


# --------------------------------------------------------------------------- #
# 4. 访问器契约：网关不得依赖不存在的符号
# --------------------------------------------------------------------------- #
class TestStatusAccessorContract:
    def test_exposes_the_documented_keys(self):
        status = fence_module.get_default_fence_status()
        for key in ("attached", "error", "failed_at"):
            assert key in status, key

    def test_returns_a_copy_not_the_live_dict(self):
        fence_module.reset_default_fence_status()
        status = fence_module.get_default_fence_status()
        status["attached"] = True
        assert fence_module.get_default_fence_status()["attached"] is False, (
            "返回了活字典 —— 调用方的一次误写就能把 un-fenced 伪装成 fenced"
        )
