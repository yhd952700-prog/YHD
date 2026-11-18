"""大升级 U1 契约：多模型容灾的熔断防抖 + 用量聊天通路。

无真 Key 演练口径：全部用脚本化假 provider（可编程失败序列）+ 假时钟，
断言行为而不是实现细节：

* 主模型超时/5xx → 自动切备用并成功，切换有 notice、按备用记账标记；
* 连续失败达阈值 → 熔断打开，后续请求**跳过**主模型（不再每次撞墙）；
* 冷却到期 → 试探主模型，成功即恢复主模型优先（half-open → closed）；
* 全部候选都在冷却 → 仍按原顺序全试，绝不把用户堵死；
* 401 这类不可重试错误不进熔断、直接抛出（原语义不许被熔断吞掉）；
* usage_report 工具按月汇总，口径与用量账本同一套。
"""
from __future__ import annotations

import pytest

from src.ai.providers import (
    BaseProvider,
    FailoverProvider,
    ProviderChainError,
    ProviderHealthRegistry,
    ProviderHTTPError,
    ProviderTimeoutError,
    provider_health_key,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _Scripted(BaseProvider):
    """按脚本行动的假 provider：script 是 'ok'/'timeout'/'500'/'401' 列表，
    每次调用弹一个，弹空后一直 'ok'。"""

    def __init__(self, name: str, script: list[str]):
        super().__init__(name, f"{name}-model")
        self.script = list(script)
        self.calls = 0

    def _act(self) -> str:
        self.calls += 1
        action = self.script.pop(0) if self.script else "ok"
        if action == "timeout":
            raise ProviderTimeoutError("上游", 5.0)
        if action == "500":
            raise ProviderHTTPError(
                "模型服务返回 HTTP 500，详情已记录到服务端日志。",
                status_code=500,
                upstream_kind="upstream_error",
            )
        if action == "401":
            raise ProviderHTTPError(
                "模型服务返回 HTTP 401，详情已记录到服务端日志。",
                status_code=401,
                upstream_kind="auth_error",
            )
        return f"来自 {self.name} 的回复"

    def chat(self, messages, **kwargs):
        return self._act()

    def chat_stream(self, messages, **kwargs):
        yield self._act()


def _registry(clock: _Clock, threshold: int = 3, cooldown: float = 60.0):
    return ProviderHealthRegistry(
        failure_threshold=threshold,
        base_cooldown_seconds=cooldown,
        max_cooldown_seconds=600.0,
        clock=clock,
    )


class TestFailoverBasics:
    def test_primary_timeout_fails_over_to_backup(self):
        clock = _Clock()
        primary = _Scripted("primary", ["timeout"])
        backup = _Scripted("backup", [])
        chain = FailoverProvider(
            [primary, backup], labels=["主", "备"], health_registry=_registry(clock)
        )
        assert chain.chat([]) == "来自 backup 的回复"
        assert chain.last_served_model == "backup-model"
        assert chain.last_fell_back_from == ["主"]

    def test_stream_failover_emits_notice(self):
        clock = _Clock()
        primary = _Scripted("primary", ["500"])
        backup = _Scripted("backup", [])
        chain = FailoverProvider(
            [primary, backup], labels=["主", "备"], health_registry=_registry(clock)
        )
        items = list(chain.chat_stream([]))
        notices = [i for i in items if isinstance(i, dict) and i.get("code") == "failover"]
        assert notices, "切换必须有显式 notice，不许静默换厂商"
        assert items[-1] == "来自 backup 的回复"


class TestCircuitBreaker:
    def test_circuit_opens_after_threshold_and_skips_primary(self):
        clock = _Clock()
        registry = _registry(clock, threshold=3)
        primary = _Scripted("primary", ["timeout"] * 10)
        backup = _Scripted("backup", [])
        chain = FailoverProvider(
            [primary, backup], labels=["主", "备"], health_registry=registry
        )
        for _ in range(3):
            assert chain.chat([]) == "来自 backup 的回复"
        assert primary.calls == 3
        key = provider_health_key(primary)
        assert registry.in_cooldown(key)
        # 第 4 次请求：主模型在冷却中，必须被跳过（不再撞墙）
        assert chain.chat([]) == "来自 backup 的回复"
        assert primary.calls == 3, "冷却中的主模型不应再被调用"
        assert chain.last_skipped_cooling == ["主"]
        assert chain.last_fell_back_from == ["主"]

    def test_half_open_recovers_primary(self):
        clock = _Clock()
        registry = _registry(clock, threshold=2, cooldown=60.0)
        primary = _Scripted("primary", ["timeout", "timeout"])  # 之后转 ok
        backup = _Scripted("backup", [])
        chain = FailoverProvider(
            [primary, backup], labels=["主", "备"], health_registry=registry
        )
        chain.chat([])
        chain.chat([])
        assert registry.in_cooldown(provider_health_key(primary))
        chain.chat([])  # 冷却中：跳过主模型
        assert primary.calls == 2
        clock.advance(61.0)  # 冷却到期 → 试探
        assert chain.chat([]) == "来自 primary 的回复"
        assert not registry.in_cooldown(provider_health_key(primary))
        assert chain.last_served_model == "primary-model"

    def test_half_open_failure_reopens_with_longer_cooldown(self):
        clock = _Clock()
        registry = _registry(clock, threshold=1, cooldown=60.0)
        primary = _Scripted("primary", ["timeout", "timeout"])
        backup = _Scripted("backup", [])
        chain = FailoverProvider(
            [primary, backup], labels=["主", "备"], health_registry=registry
        )
        chain.chat([])  # 熔断打开，冷却 60s
        clock.advance(61.0)
        chain.chat([])  # 试探失败 → 重新熔断，冷却应翻倍到 120s
        snap = {s["key"]: s for s in registry.snapshot()}
        state = snap[provider_health_key(primary)]
        assert state["circuit_opens"] == 2
        assert state["cooling_down"] is True
        assert state["cooldown_remaining_seconds"] > 60.0

    def test_all_in_cooldown_still_tries_full_order(self):
        clock = _Clock()
        registry = _registry(clock, threshold=1)
        primary = _Scripted("primary", ["timeout", "timeout"])
        backup = _Scripted("backup", ["timeout", "ok"])
        chain = FailoverProvider(
            [primary, backup], labels=["主", "备"], health_registry=registry
        )
        with pytest.raises(ProviderChainError):
            chain.chat([])  # 两边各失败一次，双双熔断
        # 全部冷却中：仍按原顺序全试，第二个恢复后应成功
        assert chain.chat([]) == "来自 backup 的回复"

    def test_non_retryable_error_not_counted_and_raises(self):
        clock = _Clock()
        registry = _registry(clock, threshold=1)
        primary = _Scripted("primary", ["401"])
        backup = _Scripted("backup", [])
        chain = FailoverProvider(
            [primary, backup], labels=["主", "备"], health_registry=registry
        )
        with pytest.raises(ProviderHTTPError):
            chain.chat([])
        assert backup.calls == 0, "401 不许静默切备用（原语义）"
        assert not registry.in_cooldown(provider_health_key(primary)), (
            "401 是配置问题，要人来看，不进熔断"
        )

    def test_stream_notice_when_primary_skipped_by_cooldown(self):
        clock = _Clock()
        registry = _registry(clock, threshold=1)
        primary = _Scripted("primary", ["timeout"])
        backup = _Scripted("backup", [])
        chain = FailoverProvider(
            [primary, backup], labels=["主", "备"], health_registry=registry
        )
        list(chain.chat_stream([]))  # 打挂主模型 → 熔断
        items = list(chain.chat_stream([]))  # 冷却跳过主模型
        assert primary.calls == 1
        notices = [i for i in items if isinstance(i, dict) and i.get("code") == "failover"]
        assert notices, "冷却跳过主模型时开口前必须先明示切换"
        assert items[-1] == "来自 backup 的回复"


class TestUsageReportTool:
    def test_usage_report_tool_answers_monthly_summary(self, tmp_path, monkeypatch):
        from src.gateway import account_usage

        monkeypatch.setenv("LIUHAO_DATA_DIR", str(tmp_path))
        # account_usage.db_path() 走 data_dir 解析；直接打桩到 tmp 库更稳。
        db_file = str(tmp_path / "usage.db")
        monkeypatch.setattr(account_usage, "db_path", lambda: db_file)

        from src._time import utc_now

        now = utc_now()
        account_usage.record_usage(
            actor="alice", key_owner="alice", model="gpt-5-mini",
            tokens_in=1000, tokens_out=500,
        )
        account_usage.record_usage(
            actor="alice", key_owner="alice", model="gpt-5-mini",
            tokens_in=2000, tokens_out=1000,
        )
        assert now  # 仅为可读性锚点

        from src.ai.tools.kernel_tools import make_tools

        tools = {tool.name: tool for tool in make_tools("user-alice")}
        assert "usage_report" in tools, "聊天工具集必须含用量查询（精而简）"
        import json

        out = json.loads(tools["usage_report"].fn())
        assert out["status"] == "ok"
        assert out["calls"] == 2
        by_model = {item["model"]: item for item in out["by_model"]}
        assert by_model["gpt-5-mini"]["tokens_in"] == 3000
        assert by_model["gpt-5-mini"]["tokens_out"] == 1500


# --------------------------------------------------------------------- #
# 路由任务备用链的契约（回归：曾误伤聊天路由兜底的单 provider 契约）
# --------------------------------------------------------------------- #
class _DummyProvider(BaseProvider):
    def __init__(self, model: str) -> None:
        self.model = model
        self.base_url = None
        self._capabilities = frozenset()  # BaseProvider.capabilities 属性的后备字段

    def chat(self, messages, **kwargs):  # pragma: no cover - 不在本组调用
        raise NotImplementedError


class _RouteResult:
    def __init__(self, selected, fallbacks):
        self.selected_model = selected
        self.fallback_models = fallbacks
        self.reason = "test-route"


def _stub_routing(monkeypatch, task_fallbacks):
    from src.ai import model_router as routing

    selected = routing.ModelProfile(
        model_id="m-main", provider="openai", model_name="main-model",
        tier=routing._tier_for("openai"),
        capabilities=routing._capabilities_for_provider("openai"),
        base_url="", vendor="openai",
    )
    calls = []

    def fake_resolve(model_id=None, **kwargs):
        calls.append(model_id)
        return _DummyProvider(str(model_id or "default"))

    monkeypatch.setattr(routing, "resolve_provider", fake_resolve)
    monkeypatch.setattr(
        routing, "route_task_from_store",
        lambda task_type, **kwargs: _RouteResult(selected, task_fallbacks),
    )
    monkeypatch.setattr(routing, "_chat_provider_chain", lambda **kwargs: None)
    return routing, calls


def test_non_chat_task_gets_fallback_chain(monkeypatch):
    routing, calls = _stub_routing(monkeypatch, ["m-backup"])
    provider = routing.provider_for_task("embedding")
    assert isinstance(provider, FailoverProvider)
    assert calls == ["m-main", "m-backup"]


def test_chat_routed_fallback_stays_single_provider(monkeypatch):
    routing, calls = _stub_routing(monkeypatch, ["m-backup"])
    provider = routing.provider_for_task("chat")
    assert not isinstance(provider, FailoverProvider)
    assert calls == ["m-main"]  # 聊天路由兜底只解析选中模型一次（原契约）
