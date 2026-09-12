"""能力层七维审计：Audited / Policy Controlled 的「下沉内核」实证测试（Round 59）。

背景：``docs/spec/AI-LAYER-DOD-AUDIT.md`` 曾用「关键字扫描 + 架构推理」把几乎
所有 Phase 的 Audited / Policy 标为 ``K``（下沉内核传递性满足）。那份结论从未
验证过「下沉这条路真的通」。本文件用**运行时观测量**（audit store 事件增量）
锁住已验证的事实，防止未来重构静默切断或误改策略语义。

判据（``docs/CODEX-CONTRACT.md`` §5）：
  Audited           = 关键操作写入 audit log（含 correlation_id）
  Policy Controlled = 通过 policy engine 判决，且判决被记录

已验证结论（2026-09-11，21 项运行时探针）：6 项产生审计、15 项不产生。
"""

from __future__ import annotations

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


def test_system_actor_can_never_be_allowed_by_builtin_rules():
    """锁住结构性事实：装饰器所用的 system actor 恒被判 deny。

    若将来为 system actor 增补可放行的策略规则（使该维度成为真正的控制点），
    这个测试会失败——那正是提醒同步更新 AI-LAYER-DOD-AUDIT.md 与
    ``_crosscutting.py`` 中的告警文字。
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


def test_lcore_delegation_audits_capability_registration():
    """P9 L-Core：构造时注册能力 -> capability kernel 写审计事件。"""
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
# 3. 已证实「无下沉」的模块（守住不夸大：这些 Phase 的 Audited 不是 K）
# --------------------------------------------------------------------------- #


def test_economy_operations_produce_no_audit_events():
    """P17 Economy：声明为「self-contained, dependency-free」，不下沉 resource kernel。

    这是**已知边界**而非本测试要修的东西：economy 的 BudgetEngine 有意独立
    实现，因此其关键操作不写审计。若要补，必须显式接线 resource kernel。
    """
    from src.ai.economy import BudgetEngine

    engine = BudgetEngine(total=100.0)
    before = _total_events()
    engine.consume(engine.reserve(10.0))
    assert _total_events() == before, (
        "economy 开始产生审计了 —— 请同步更新 AI-LAYER-DOD-AUDIT.md 的 P17 评级"
    )


def test_hardening_own_operations_produce_no_audit_events():
    """P21 Hardening：不 import 任何 kernel，其审计评级不应标为 K。

    注意：**首次**调用会因内部构造 ``AgentNetworkGateway`` 而附带一条
    ``network.add_route``（网关路由注册的一次性副作用，非 hardening 自身的
    检查动作）。因此这里先预热一次，再测增量——使判据与调用顺序无关。
    """
    from src.ai.hardening import run_hardening_suite

    run_hardening_suite()  # 预热：消化一次性的路由注册副作用
    before = _total_events()
    run_hardening_suite()
    assert _total_events() == before, (
        "hardening 的检查动作开始产生审计了 —— 请同步更新 AI-LAYER-DOD-AUDIT.md 的 P21 评级"
    )


def test_perception_pure_operations_produce_no_audit_events():
    """P11 Perception：纯内存态算法，无 kernel 调用即无审计。"""
    from src.ai.perception import TextPerceiver, WorldModel

    world = WorldModel()
    world.apply(TextPerceiver().perceive("dod probe"))  # 预热
    before = _total_events()
    world.apply(TextPerceiver().perceive("dod probe 2"))
    assert _total_events() == before, (
        "perception 开始产生审计了 —— 请同步更新 AI-LAYER-DOD-AUDIT.md 的 P11 评级"
    )
