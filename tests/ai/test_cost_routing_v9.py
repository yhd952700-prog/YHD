"""第二季 V9 成本智能路由测试。

覆盖：
- 分级 G0–G5 顺序（尤其 G3 长上下文先于 G4 轻形态——设计 R3 锁定）；
- 误路由防护：成员任务/显式指定/上一轮用工具一律不降级；
- 账本 route_grade 列：老库迁移、record/read、usage_summary by_route；
- 映射状态与价格护栏（省档不便宜/未定价 → 不生效且如实报原因）；
- 省档优先链：轻活先打省档、省档失败自动升档回原链（Failover 语义）；
- 账号开关默认关 + 往返。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.ai import cost_routing as cr
from src.ai import model_router as mr
from src.gateway import a2a, account_usage


# ---------------------------------------------------------------- 分级


def test_grade_pinned_wins_over_everything():
    assert cr.grade_turn(
        explicit_model=True, member_task=True, prev_turn_used_tools=True,
        message="翻译一下", context_tokens=10,
    ) == "pinned"


def test_grade_member_task_is_hard():
    assert cr.grade_turn(
        explicit_model=False, member_task=True, prev_turn_used_tools=False,
        message="翻译一下 hello", context_tokens=20,
    ) == "hard"


def test_grade_prev_turn_tools_is_hard():
    assert cr.grade_turn(
        explicit_model=False, member_task=False, prev_turn_used_tools=True,
        message="总结一下", context_tokens=20,
    ) == "hard"


def test_grade_long_context_beats_light_keyword():
    # G3 先于 G4（设计 R3）：长上下文里出现「翻译」也必须是 hard。
    assert cr.grade_turn(
        explicit_model=False, member_task=False, prev_turn_used_tools=False,
        message="请翻译这份合同", context_tokens=cr.HARD_CONTEXT_TOKENS + 1,
    ) == "hard"


def test_grade_light_forms():
    for message in ("翻译一下：hello world", "帮我总结一下这段话", "你好"):
        assert cr.grade_turn(
            explicit_model=False, member_task=False,
            prev_turn_used_tools=False, message=message, context_tokens=30,
        ) == "light", message


def test_grade_default_is_hard():
    # 拿不准一律 hard（误路由防护的默认方向）。
    assert cr.grade_turn(
        explicit_model=False, member_task=False, prev_turn_used_tools=False,
        message="帮我分析一下这份财报的风险点", context_tokens=60,
    ) == "hard"


def test_grade_light_keyword_but_mid_context_is_hard():
    # 轻形态但上下文超过轻档上限（<=800）→ 不降级。
    assert cr.grade_turn(
        explicit_model=False, member_task=False, prev_turn_used_tools=False,
        message="翻译一下", context_tokens=cr.LIGHT_CONTEXT_TOKENS + 1,
    ) == "hard"


# ---------------------------------------------------------------- 账本


@pytest.fixture()
def usage_db(tmp_path, monkeypatch):
    monkeypatch.setenv("LIUHAO_ACCOUNT_USAGE_DB", str(tmp_path / "usage.sqlite3"))
    account_usage._initialised.clear()
    yield
    account_usage._initialised.clear()


def test_ledger_records_and_summarises_route_grade(usage_db, monkeypatch):
    monkeypatch.setattr(
        account_usage, "_price_table",
        lambda models: {"cheap": 1.0, "strong": 5.0},
    )
    account_usage.record_usage(
        actor="a1", key_owner="a1", model="cheap", tokens_in=100,
        tokens_out=50, session_id="s", correlation_id="c-light",
        route_grade="light",
    )
    account_usage.record_usage(
        actor="a1", key_owner="a1", model="strong", tokens_in=200,
        tokens_out=100, session_id="s", correlation_id="c-hard",
        route_grade="hard",
    )
    account_usage.record_usage(
        actor="a1", key_owner="a1", model="strong", tokens_in=10,
        tokens_out=5, session_id="s", correlation_id="c-legacy",
    )
    events = account_usage.usage_events("a1", owners=["a1"])
    grades = {e["correlation_id"]: e["route_grade"] for e in events["events"]}
    assert grades == {"c-light": "light", "c-hard": "hard", "c-legacy": ""}
    summary = account_usage.usage_summary("a1", owners=["a1"])
    by_route = {b["route_grade"]: b for b in summary["by_route"]}
    assert by_route["light"]["calls"] == 1
    assert by_route["light"]["cost"] == pytest.approx(0.15)
    assert by_route["hard"]["calls"] == 1
    assert by_route[""]["calls"] == 1  # 旧行（未分级）单独成档，不冒充


def test_ledger_migrates_old_schema(usage_db, tmp_path):
    # 先按旧 schema 建库（无 route_grade 列），再走正式入口应自动补列。
    import sqlite3

    path = tmp_path / "usage.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE account_usage_events ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, "
        "actor TEXT NOT NULL, key_owner TEXT NOT NULL, "
        "model TEXT NOT NULL DEFAULT '', tokens_in INTEGER NOT NULL DEFAULT 0, "
        "tokens_out INTEGER NOT NULL DEFAULT 0, "
        "estimated INTEGER NOT NULL DEFAULT 1, "
        "session_id TEXT NOT NULL DEFAULT '', correlation_id TEXT NOT NULL DEFAULT '')"
    )
    connection.commit()
    connection.close()
    account_usage._initialised.clear()
    assert account_usage.record_usage(
        actor="a1", key_owner="a1", model="m", tokens_in=1, tokens_out=1,
        session_id="s", correlation_id="c1", route_grade="light",
    )
    events = account_usage.usage_events("a1", owners=["a1"])
    assert events["events"][0]["route_grade"] == "light"


# ---------------------------------------------------------------- 映射/护栏


class _FakeProvider:
    def __init__(self, name, fail=False):
        self.name = name
        self.model = name
        self.capabilities = SimpleNamespace()
        self._fail = fail

    def generate(self, prompt, **kwargs):
        if self._fail:
            raise RuntimeError(f"{self.name} down")
        return f"answer-by-{self.name}"


def _fake_records():
    return [
        {"id": "rec-strong", "model": "strong"},
        {"id": "rec-cheap", "model": "cheap"},
    ]


def _install_fake_chain(monkeypatch, *, prices, light_record="rec-cheap",
                        fail_models=()):
    monkeypatch.setattr(
        mr, "_chat_chain_records", lambda store, **kw: _fake_records()
    )

    def _route(task_type, **kw):
        if task_type != "chat_light" or not light_record:
            raise mr.NoModelAvailableError("no assignment")
        return SimpleNamespace(selected_model=SimpleNamespace(
            model_id=light_record, model_name="cheap"))

    monkeypatch.setattr(mr, "route_task_from_store", _route)
    import src.models.registry as registry

    class _Registry:
        def get_model(self, name):
            return SimpleNamespace(estimated_cost_per_1k=prices[name])

    monkeypatch.setattr(registry, "get_model_registry", lambda: _Registry())

    class _Store:
        def resolve(self, record_id, **kwargs):
            model = {r["id"]: r["model"] for r in _fake_records()}[record_id]
            return {"model": model}

    import src.ai.model_config as model_config

    monkeypatch.setattr(model_config, "get_model_config_store", lambda: _Store())
    monkeypatch.setattr(
        mr, "provider_from_config",
        lambda cfg: _FakeProvider(cfg["model"], fail=cfg["model"] in fail_models),
    )


def test_mapping_status_price_guard(monkeypatch):
    _install_fake_chain(monkeypatch, prices={"strong": 5.0, "cheap": 1.0})
    status = mr.light_mapping_status()
    assert status["configured"] is True
    assert status["price_ok"] is True
    assert status["light_model"] == "cheap"
    assert status["default_model"] == "strong"


def test_mapping_status_not_cheaper_is_inert(monkeypatch):
    _install_fake_chain(monkeypatch, prices={"strong": 5.0, "cheap": 9.0})
    status = mr.light_mapping_status()
    assert status["configured"] is True
    assert status["price_ok"] is False
    assert "并不比默认模型便宜" in status["reason"]
    assert mr.resolve_light_first_chain() is None


def test_mapping_status_unpriced_is_inert(monkeypatch):
    _install_fake_chain(monkeypatch, prices={"strong": 5.0})
    # cheap 不在价格表 → get_model_metadata 抛 KeyError → 未定价处理。
    status = mr.light_mapping_status()
    assert status["price_ok"] is False
    assert mr.resolve_light_first_chain() is None


def test_mapping_status_no_assignment(monkeypatch):
    _install_fake_chain(monkeypatch, prices={"strong": 5.0, "cheap": 1.0},
                        light_record="")
    status = mr.light_mapping_status()
    assert status["configured"] is False
    assert "chat_light" in status["reason"]


def test_light_chain_serves_light_first(monkeypatch):
    _install_fake_chain(monkeypatch, prices={"strong": 5.0, "cheap": 1.0})
    chain = mr.resolve_light_first_chain()
    assert chain is not None
    assert chain.generate("hi") == "answer-by-cheap"


def test_light_chain_escalates_on_failure(monkeypatch):
    _install_fake_chain(monkeypatch, prices={"strong": 5.0, "cheap": 1.0},
                        fail_models=("cheap",))
    chain = mr.resolve_light_first_chain()
    assert chain is not None
    assert chain.generate("hi") == "answer-by-strong"
    assert chain.last_fell_back_from  # 升档事实留痕（响应 escalated 的来源）


def test_provider_for_task_light_first_only_when_asked(monkeypatch):
    _install_fake_chain(monkeypatch, prices={"strong": 5.0, "cheap": 1.0})
    monkeypatch.delenv("LIUHAO_MODEL_ROUTER", raising=False)
    plain = mr.provider_for_task("chat")
    assert plain.generate("hi") == "answer-by-strong"
    light = mr.provider_for_task("chat", light_first=True)
    assert light.generate("hi") == "answer-by-cheap"


# ---------------------------------------------------------------- 开关


def test_settings_cost_routing_default_off_and_roundtrip(monkeypatch):
    state: dict = {}
    monkeypatch.setattr(a2a, "load_state", lambda: state)
    monkeypatch.setattr(a2a, "save_state", lambda s: state.update(s))
    assert a2a.get_account_settings("acc1")["cost_routing_enabled"] is False
    a2a.set_account_settings("acc1", cost_routing_enabled=True)
    assert a2a.get_account_settings("acc1")["cost_routing_enabled"] is True
    a2a.set_account_settings("acc1", cost_routing_enabled=False)
    assert a2a.get_account_settings("acc1")["cost_routing_enabled"] is False
    # 别的账号不受影响
    assert a2a.get_account_settings("acc2")["cost_routing_enabled"] is False
