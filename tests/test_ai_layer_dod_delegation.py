"""能力层七维审计：Audited / Policy Controlled 的「下沉内核」实证测试（Round 59）。

背景：``docs/spec/AI-LAYER-DOD-AUDIT.md`` 曾用「关键字扫描 + 架构推理」把几乎
所有 Phase 的 Audited / Policy 标为 ``K``（下沉内核传递性满足）。那份结论从未
验证过「下沉这条路真的通」。本文件用**运行时观测量**（audit store 事件增量）
锁住已验证的事实，防止未来重构静默切断或误改策略语义。

判据（``docs/CODEX-CONTRACT.md`` §5）：
  Audited           = 关键操作写入 audit log（含 correlation_id）
  Policy Controlled = 通过 policy engine 判决，且判决被记录

历史结论（Round 59，2026-09-11，21 项运行时探针）：仅 6 项产生审计、15 项不产生。

根因不是"下沉这条路断了"，而是**传递性满足只对真的调用了被装饰内核动作的操作成立**：
编排/算法类模块（collaboration / perception / organization / enoch / world_interface /
evolution / l10k / hardening / conversation_store / tool_registry）一个内核动作都不调，
因此在端到端意义上完全没有审计。

Round 60 据此补齐：新增 ``src/ai/audit.py``（能力层审计助手，与 ``src/ai/observability.py``
同构）+ 22 个 ``@audited`` 注入点。本节的正向断言即锁定补齐后的事实；
仍无审计的只剩 **P17 Economy**（其 docstring 声明为有意独立实现）。
"""

from __future__ import annotations

import pytest

from src.kernels._crosscutting import kernel_action
from src.kernels.audit import audit_query, get_audit_store
from src.kernels.policy import get_policy_engine

# --------------------------------------------------------------------------- #
# 观测工具
# --------------------------------------------------------------------------- #


def _total_events() -> int:
    """审计存储的累计事件数（用于增量观测）。"""
    stats = get_audit_store().get_stats()
    for key in ("total_events", "count", "total"):
        if key in stats:
            return int(stats[key])
    return int(sum(v for v in stats.values() if isinstance(v, int)))


def _recent_events(limit: int = 100) -> list:
    return audit_query(limit=limit, reverse=True) or []


def _actions_in(events) -> list:
    return [((e.get("details") or {}).get("action")) for e in events]


# --------------------------------------------------------------------------- #
# 1. kernel_action 契约：判决被记录，但从不拦截
# --------------------------------------------------------------------------- #


class _Thing:
    def __init__(self) -> None:
        self.value = 0

    @kernel_action("dod_probe.increment", risk_level="LOW")
    def increment(self, n: int = 1) -> int:
        self.value += n
        return self.value


def test_kernel_action_records_decision_with_correlation_id():
    """Audited 定义：事件写入且含 correlation_id。"""
    thing = _Thing()
    thing.increment(1)
    events = _recent_events()
    matching = [
        e for e in events
        if (e.get("details") or {}).get("action") == "dod_probe.increment"
    ]
    assert matching, "kernel_action 未写入审计事件"
    ev = matching[0]
    assert ev.get("correlation_id"), "审计事件缺 correlation_id"
    decision = (ev.get("details") or {}).get("policy_decision")
    assert decision in ("allow", "deny", "defer")


def test_kernel_action_records_policy_as_not_enforced():
    """Policy Controlled 只做到「有判决记录」，不是「策略拦截」。

    ``policy_enforced=False`` 是刻意的诚实标注：读审计的人不应把 deny
    误读成「动作被拒绝」，因为动作实际已经执行。
    """
    thing = _Thing()
    before = thing.value
    thing.increment(2)
    assert thing.value == before + 2, "动作应照常执行（装饰器 additive）"

    events = _recent_events()
    matching = [
        e for e in events
        if (e.get("details") or {}).get("action") == "dod_probe.increment"
    ]
    assert matching
    details = matching[0].get("details") or {}
    assert details.get("policy_enforced") is False
    # 判决依据可追溯（"dod_probe.increment" 不在内核动作白名单内）
    assert details.get("policy_rule") == "default_deny"


def test_system_actor_can_never_be_allowed_by_builtin_rules():
    """锁住结构性事实：未经核验的 ``system`` 裸声明恒被判 deny。

    Policy C-1 起，内核动作改由**内部 service 主体**归因（经 Identity Kernel
    核验），因此这个测试锁的是「裸声明仍然不行」这一半边界；service 路径的
    正向行为见 ``tests/kernels/policy/test_internal_service_policy.py``。
    """
    engine = get_policy_engine()
    for risk in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
        decision = engine.evaluate_simple(
            actor={"type": "system", "verified": True},
            action={"name": "memory.store", "risk_level": risk},
            resource=None,
            scope=None,
        )
        assert decision.is_denied, f"risk={risk} 意外未判 deny，策略语义已变更"


def test_service_actor_verdict_carries_information():
    """Policy C-1：内核层判决不再是常量，而是白名单驱动的 allow/deny。

    白名单内（查询/计算/簿记类）-> allow；授权/破坏类 -> deny。
    """
    from src.kernels.identity import INTERNAL_SERVICE_PRINCIPAL
    from src.kernels.policy import (
        INTERNAL_SERVICE_ALLOWED_ACTIONS,
        INTERNAL_SERVICE_DENIED_ACTIONS,
    )

    engine = get_policy_engine()
    actor = {"type": "service", "principal": INTERNAL_SERVICE_PRINCIPAL}

    allowed = engine.evaluate_simple(
        actor=dict(actor), action={"name": sorted(INTERNAL_SERVICE_ALLOWED_ACTIONS)[0]},
    )
    assert allowed.is_allowed
    assert "RULE:internal_service_allow:allow" in allowed.traceability

    denied = engine.evaluate_simple(
        actor=dict(actor), action={"name": sorted(INTERNAL_SERVICE_DENIED_ACTIONS)[0]},
    )
    assert denied.is_denied
    assert "RULE:default_deny:deny" in denied.traceability


# --------------------------------------------------------------------------- #
# 2. 已验证的下沉路径（正向）
# --------------------------------------------------------------------------- #


def test_personal_context_delegates_audit_to_memory_kernel(tmp_path):
    """P5 Memory：画像写入下沉 memory kernel，产生 memory.store 审计事件。"""
    from src.ai.personal_context import PersonalContextManager

    mgr = PersonalContextManager(db_path=str(tmp_path / "pc.db"))
    before = _total_events()
    mgr.set_preference("dod-user", "lang", "zh")
    assert _total_events() > before, "personal_context 未触发任何审计"
    assert "memory.store" in _actions_in(_recent_events())


def test_lcore_delegation_audits_capability_registration(monkeypatch):
    """P9 L-Core：构造时经能力内核注册内置能力 -> ``capability.register`` 审计事件。

    为什么要重置全局注册表：能力注册对 `get_capability_registry()` 的**全局单例**
    是幂等的——调用方在调 `register()` 之前就查重，所以同一进程里只有**首次**
    构造才会真正注册并写审计（实测：首次 +12，之后恒为 0）。在全量套件里，前面的
    能力内核测试早已注册过，若不重置就会观察不到增量。

    `monkeypatch` 会在测试结束时自动还原该全局，不影响其它测试。
    """
    import src.kernels.capability as capability_kernel

    monkeypatch.setattr(capability_kernel, "_global_registry", None)

    from src.ai.lcore import LCore

    before = _total_events()
    LCore()
    assert _total_events() > before, "lcore 未触发任何审计"
    assert "capability.register" in _actions_in(_recent_events())


def test_governance_decision_is_audited():
    """P16 Governance：访问判定下沉 security kernel，写 security.decide_access。"""
    from src.ai.governance import SecurityChain

    before = _total_events()
    SecurityChain().evaluate({"text": "hello", "actor": "dod-user"}, "read")
    assert _total_events() > before, "governance 未触发任何审计"
    assert "security.decide_access" in _actions_in(_recent_events())


def test_verification_experience_engine_delegates_to_memory():
    """P18 Verification：经验落盘下沉 memory kernel（同模块 verify() 则不下沉）。"""
    from src.ai.verification import ExperienceEngine, ExperienceEntry

    engine = ExperienceEngine()
    before = _total_events()
    engine.store(ExperienceEntry(id="dod-e1", summary="s", verdict="success"))
    assert _total_events() > before, "ExperienceEngine.store 未触发审计"


# --------------------------------------------------------------------------- #
# 3. Round 60 补齐：能力层自身审计埋点（src/ai/audit.py + 22 个注入点）
# --------------------------------------------------------------------------- #


def test_capability_audit_emit_writes_event_with_correlation_id():
    """emit() 写入审计事件，并原样带上 correlation_id 与 layer 标记。"""
    from src.ai.audit import emit

    before = _total_events()
    assert emit("dod.emit.probe", module="src.ai.audit", correlation_id="cid-dod-1") is True
    assert _total_events() > before, "emit 未写入审计事件"

    ev = [
        e for e in _recent_events()
        if (e.get("details") or {}).get("action") == "dod.emit.probe"
    ][0]
    assert ev.get("correlation_id") == "cid-dod-1"
    assert (ev.get("details") or {}).get("layer") == "capability"


def test_capability_audit_records_failure_and_reraises():
    """被包装方法抛异常：记 failure 审计，异常照常抛出（additive 契约不吞异常）。"""
    from src.ai.audit import audited

    class _Boom:
        @audited("dod.boom", module="src.ai.audit")
        def go(self):
            raise RuntimeError("boom")

    before = _total_events()
    with pytest.raises(RuntimeError):
        _Boom().go()

    assert _total_events() > before, "失败路径未写审计"
    ev = [
        e for e in _recent_events()
        if (e.get("details") or {}).get("action") == "dod.boom"
    ][0]
    assert ev.get("outcome") == "failure"


def test_capability_audit_fail_loud_not_fatal(monkeypatch):
    """审计存储故障时：不抛异常（业务继续），但**不能静默** —— 必须记 warning。

    静默失败比不审计更危险：审计面看起来"有接线"，实则全部丢失。
    """
    import src.kernels.audit as audit_kernel

    from src.ai import audit as ai_audit

    def _boom(*_a, **_k):
        raise RuntimeError("audit store down")

    monkeypatch.setattr(audit_kernel, "log_event", _boom)
    warnings: list = []
    monkeypatch.setattr(ai_audit.logger, "warning",
                        lambda *a, **k: warnings.append(a))

    assert ai_audit.emit("dod.unavailable", module="src.ai.audit") is False
    assert warnings, "审计写入失败被静默吞掉，未记 warning"


@pytest.fixture(scope="module")
def backfill_cases(tmp_path_factory):
    """Round 60 的 22 个注入点的 (label, expected_action, callable)。

    每个 case **自带前置条件**（不依赖执行顺序）：
    工具生命周期用例用独立的 ToolRegistry 走完必要的前置步骤。
    """
    from src.ai.ada import ComputeEngine
    from src.ai.agent_factory import (
        AgentMemory,
        AgentPolicy,
        AgentRuntimeService,
    )
    from src.ai.collaboration import AgentMessage, MessageBus, MessageKind
    from src.ai.conversation_store import ConversationStore
    from src.ai.employee import Agent
    from src.ai.enoch import create_mission
    from src.ai.evolution import EvolutionEngine
    from src.ai.l10k import L10KRegistry
    from src.ai.organization import Organization
    from src.ai.perception import TextPerceiver
    from src.ai.providers import MockProvider
    from src.ai.runtime_loop import RuntimeLoop
    from src.ai.tool_registry import Tool, ToolRegistry
    from src.ai.verification import VerificationEngine
    from src.ai.world_interface import FilesystemAdapter, WorldInterface, WorldRequest

    tmp = tmp_path_factory.mktemp("dod_backfill")
    conv_db = str(tmp / "dod_conv.db")
    conv = ConversationStore(db_path=conv_db)
    bus = MessageBus()
    org = Organization("dod-org")
    world = WorldInterface(
        adapters=[FilesystemAdapter()],
        authorize=lambda _r: True,
    )

    def _agent() -> Agent:
        return Agent(id="dod-a", agent_type="general", name="dod-a",
                     provider=MockProvider(name="dod", model="mock-model"))

    def _tool_case(method: str, i: int):
        """工具生命周期：自带前置步骤，成功路径（无异常即为成功）。"""
        reg = ToolRegistry()
        tid = reg.register(Tool(f"dod-t{i}", f"T{i}", "1.0", "d", "cap", {},
                                lambda **_k: None))
        if method == "register":
            return tid
        reg.approve(tid)
        if method == "approve":
            return True
        reg.activate(tid)
        if method == "activate":
            return True
        reg.suspend(tid)
        if method == "suspend":
            return True
        return reg.revoke(tid)

    return [
        ("P3 agent_runtime.start", "p3.agent_runtime.start",
         lambda: AgentRuntimeService(agent=_agent()).start()),
        ("P3 runtime_loop.step", "p3.runtime_loop.step",
         lambda: RuntimeLoop(
             runtime=AgentRuntimeService(agent=_agent()),
             bus=MessageBus(),
             policy=AgentPolicy(principal="dod-a"),
             memory=AgentMemory(principal="dod-a"),
         ).step()),
        ("P5 conversation.append", "p5.conversation.append",
         lambda: conv.append("dod-p", 1, "user", "hi")),
        ("P9 tool.register", "p9.tool.register", lambda: _tool_case("register", 1)),
        ("P9 tool.approve", "p9.tool.approve", lambda: _tool_case("approve", 2)),
        ("P9 tool.activate", "p9.tool.activate", lambda: _tool_case("activate", 3)),
        ("P9 tool.suspend", "p9.tool.suspend", lambda: _tool_case("suspend", 4)),
        ("P9 tool.revoke", "p9.tool.revoke", lambda: _tool_case("revoke", 5)),
        ("P10 bus.send", "p10.bus.send",
         lambda: bus.send(AgentMessage("dod-m", "a", "b", MessageKind.TELL, {"x": 1}))),
        ("P11 perception.perceive", "p11.perception.perceive",
         lambda: TextPerceiver().perceive("dod probe")),
        ("P11 ada.run_python", "p11.ada.run_python",
         lambda: ComputeEngine().run_python("1 + 1")),
        ("P12 organization.create_goal", "p12.organization.create_goal",
         lambda: org.create_goal("dod goal")),
        ("P12 organization.create_department", "p12.organization.create_department",
         lambda: org.create_department("dod dept")),
        ("P13 enoch.create_mission", "p13.enoch.create_mission",
         lambda: create_mission("dod mission")),
        ("P15 world.execute", "p15.world.execute",
         lambda: world.execute(WorldRequest(adapter="filesystem", action="read",
                                            params={"path": conv_db}))),
        ("P18 verification.verify", "p18.verification.verify",
         lambda: VerificationEngine().verify({"success": True, "output": "x"})),
        ("P19 evolution.propose", "p19.evolution.propose",
         lambda: EvolutionEngine().propose("dod change", 1.0, "raise metric")),
        ("P20 l10k.register_task", "p20.l10k.register_task",
         lambda: L10KRegistry().register_task("dod-task", "easy")),
    ]


def test_capability_layer_backfill_writes_expected_audit(backfill_cases):
    """Round 60 的每个注入点，运行时都必须写出预期动作的审计事件。

    断言只用「新增事件里出现了该动作」，不用精确增量 —— 审计 store 是全局单例，
    精确增量天然与执行顺序相关（Round 59 在 CI 上踩过这个坑）。
    任一 case 自身抛异常即判失败（说明该关键操作已坏）。
    """
    missing = []
    for label, expected, call in backfill_cases:
        before = _total_events()
        call()
        added = _total_events() - before
        new_actions = _actions_in(_recent_events(limit=200))[:max(added, 0)]
        if expected not in new_actions:
            missing.append(f"{label}（期望 {expected}，实际新增 {new_actions}）")

    assert not missing, "以下能力层关键操作未写审计：\n  " + "\n  ".join(missing)


def test_hardening_suite_is_audited():
    """P21 Hardening：Round 60 补齐后，套件自身动作必须写审计。"""
    from src.ai.hardening import run_hardening_suite

    before = _total_events()
    run_hardening_suite()
    added = _total_events() - before
    assert added > 0, "hardening 未产生任何审计"
    assert "p21.hardening.run_checks" in _actions_in(_recent_events(limit=200))[:added]


def test_perception_perceive_is_audited():
    """P11 Perception：Round 60 补齐后，perceive() 自身必须写审计。"""
    from src.ai.perception import TextPerceiver, WorldModel

    world = WorldModel()
    world.apply(TextPerceiver().perceive("dod warmup"))  # 预热
    before = _total_events()
    world.apply(TextPerceiver().perceive("dod probe"))
    added = _total_events() - before
    assert added > 0, "perception 未产生审计"
    assert "p11.perception.perceive" in _actions_in(_recent_events(limit=200))[:added]


# --------------------------------------------------------------------------- #
# 4. 仍无下沉的模块（有意设计，守住不夸大）
# --------------------------------------------------------------------------- #


def test_economy_still_has_no_capability_layer_audit():
    """P17 Economy 至今**没有**能力层审计埋点（有意保留，非遗漏）。

    与 P11/P21 不同，economy 的独立实现是 ``economy.py`` docstring 明示的设计意图，
    因此 Round 60 刻意**没有**给它注入 @audited。若将来给它加上了，本测试会失败 ——
    那正是提醒同步更新 AI-LAYER-DOD-AUDIT.md 的 P17 评级与本节标题。
    """
    from src.ai.economy import BudgetEngine

    engine = BudgetEngine(total=100.0)
    before = _total_events()
    engine.consume(engine.reserve(10.0))
    assert _total_events() == before, (
        "economy 开始产生审计了 —— 请同步更新 AI-LAYER-DOD-AUDIT.md 的 P17 评级"
    )
