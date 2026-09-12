"""能力层七维审计复核：Audited / Policy Controlled 的「下沉内核」是否真的通。

用途：复现 ``docs/spec/AI-LAYER-DOD-AUDIT.md`` §3.5 的运行时实测矩阵。

方法：
  1) **对照实验**：先直接调用已知被 ``@kernel_action`` 装饰的
     ``get_memory_kernel().store()``，确认 audit store 计数 +1。
     只有对照通过，「增量为 0」才能解释为「该模块确实不产生审计」，
     而不是「测量失灵」。
  2) 逐个调用能力层模块的关键操作，测其 audit store 事件增量。

用法::

    D:\\LiuHao-AI-OS\\.venv\\Scripts\\python.exe scripts/verify_ai_layer_audit.py

判据来自 ``docs/CODEX-CONTRACT.md`` §5：
  Audited           = 关键操作写入 audit log（含 correlation_id）
  Policy Controlled = 通过 policy engine 判决，且判决被记录
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _tmpdir() -> Path:
    """优先用 D 盘临时目录（本项目约定）；不可用时退回系统临时目录。"""
    candidate = Path("D:/cache/temp")
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        return candidate
    except OSError:
        return Path(tempfile.mkdtemp(prefix="liuhao_ai_dod_"))


TMP = _tmpdir()
# 隔离到独立 DB，避免污染工作区内的 audit_store.db / memory_store.db
os.environ["AUDIT_DB_PATH"] = str(TMP / "dod_audit.db")
os.environ["MEMORY_DB_PATH"] = str(TMP / "dod_memory.db")
os.environ["CONVERSATION_DB_PATH"] = str(TMP / "dod_conv.db")
os.environ["PERSONAL_CONTEXT_DB_PATH"] = str(TMP / "dod_personal.db")

from src.kernels.audit import audit_query, get_audit_store  # noqa: E402
from src.kernels.policy import get_policy_engine  # noqa: E402

ROWS: list = []


def _count() -> int:
    stats = get_audit_store().get_stats()
    for key in ("total_events", "count", "total"):
        if key in stats:
            return int(stats[key])
    return int(sum(v for v in stats.values() if isinstance(v, int)))


def probe(phase: str, label: str, fn) -> None:
    before = _count()
    status, err = "ok", None
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - 探针需报告而非中断
        status, err = "ERR", f"{type(exc).__name__}: {exc}"[:60]
    delta = _count() - before
    ROWS.append((phase, label, delta, status, err))
    tag = "AUDITED" if delta > 0 else "no-audit"
    suffix = f"   !! {err}" if err else ""
    print(f"  [{tag:8}] delta={delta:+d}  {phase:4} {label:44}{suffix}")


class _StubProvider:
    def generate_with_retry(self, prompt, **kwargs):
        return f"echo:{prompt}"


def _agent(aid: str = "a1"):
    from src.ai.employee import Agent
    return Agent(id=aid, agent_type="general", name=aid, provider=_StubProvider())


def main() -> int:
    print("=" * 96)
    print("对照实验：验证探针本身有效")
    print("=" * 96)
    from src.kernels.memory import MemoryScope, MemoryTier, get_memory_kernel

    probe(
        "CTL",
        "直接调 get_memory_kernel().store()",
        lambda: get_memory_kernel().store(
            "dod_ctl", {"x": 1}, tier=MemoryTier.SHORT_TERM, scope=MemoryScope.L1
        ),
    )
    if ROWS[-1][2] <= 0:
        print("\n对照失败：探针测不到已知会审计的 kernel 方法，结论不可用。")
        return 2
    print("对照通过：探针能观测到 @kernel_action 写入的审计事件。\n")

    print("=" * 96)
    print("15-Phase 代表模块运行时审计增量矩阵")
    print("=" * 96)

    def p3a():
        from src.ai.agent_factory import AgentRuntimeService
        AgentRuntimeService(agent=_agent()).start()

    def p3b():
        from src.ai.agent_factory import AgentMemory, AgentPolicy, AgentRuntimeService
        from src.ai.collaboration import MessageBus
        from src.ai.runtime_loop import RuntimeLoop
        loop = RuntimeLoop(
            runtime=AgentRuntimeService(agent=_agent("rt1")),
            bus=MessageBus(),
            policy=AgentPolicy(principal="rt1", capabilities=[], scope="L1"),
            memory=AgentMemory(principal="rt1"),
        )
        if hasattr(loop, "step"):
            loop.step()

    def p5a():
        from src.ai.conversation_store import ConversationStore
        ConversationStore(db_path=str(TMP / "dod_conv.db")).append("u1", 1, "user", "hi")

    def p5b():
        from src.ai.personal_context import PersonalContextManager
        PersonalContextManager(db_path=str(TMP / "dod_personal.db")).set_preference(
            "u1", "lang", "zh"
        )

    def p9a():
        from src.ai.lcore import LCore
        LCore()

    def p9b():
        from src.ai.tool_registry import Tool, ToolRegistry
        ToolRegistry().register(
            Tool(tool_id="t1", name="t1", version="1.0", description="d",
                 capability="c1", schema={}, fn=lambda: 1)
        )

    def p10():
        from src.ai.collaboration import AgentMessage, MessageBus, MessageKind
        bus = MessageBus()
        bus.register("a1")
        bus.send(AgentMessage(id="m1", sender="a0", receiver="a1",
                              kind=MessageKind.ASK, payload={"m": 1}))

    def p11a():
        from src.ai.perception import TextPerceiver
        TextPerceiver().perceive("hello world")

    def p11b():
        from src.ai.ada import ComputeEngine
        ComputeEngine().run_python("1+1")

    def p12():
        from src.ai.organization import Organization
        org = Organization(name="org1")
        org.create_goal("g1")
        org.create_department("d1")

    def p13():
        from src.ai.enoch import create_mission
        create_mission("demo mission")

    def p14():
        from src.ai.network_gateway import AgentNetworkGateway
        AgentNetworkGateway()

    def p15():
        from src.ai.world_interface import FilesystemAdapter, WorldInterface, WorldRequest
        WorldInterface(adapters=[FilesystemAdapter()]).execute(
            WorldRequest(adapter="filesystem", action="read", params={"path": "README.md"})
        )

    def p16():
        from src.ai.governance import SecurityChain
        SecurityChain().evaluate({"text": "hello", "actor": "u1"}, "read")

    def p17():
        from src.ai.economy import BudgetEngine
        engine = BudgetEngine(total=100.0)
        engine.consume(engine.reserve(10.0))

    def p18a():
        from src.ai.verification import VerificationEngine
        VerificationEngine().verify({"success": True, "output": "ok"})

    def p18b():
        from src.ai.verification import ExperienceEngine, ExperienceEntry
        ExperienceEngine().store(ExperienceEntry(id="e1", summary="s", verdict="success"))

    def p19():
        from src.ai.evolution import EvolutionEngine
        EvolutionEngine()

    def p20a():
        from src.ai.l10k import L10KRegistry
        L10KRegistry().register_task("t1", "trivial")

    def p20b():
        from src.ai.vhl_benchmark import run_vhl_benchmark
        run_vhl_benchmark()

    def p21():
        from src.ai.hardening import run_hardening_suite
        run_hardening_suite()

    probe("P3", "agent_factory.AgentRuntimeService.start()", p3a)
    probe("P3", "runtime_loop.RuntimeLoop(...).step()", p3b)
    probe("P5", "conversation_store.append()", p5a)
    probe("P5", "personal_context.set_preference()", p5b)
    probe("P9", "lcore.LCore()", p9a)
    probe("P9", "tool_registry.register(Tool)", p9b)
    probe("P10", "collaboration.MessageBus.send()", p10)
    probe("P11", "perception.TextPerceiver.perceive()", p11a)
    probe("P11", "ada.ComputeEngine.run_python()", p11b)
    probe("P12", "organization.Organization.create_goal/department()", p12)
    probe("P13", "enoch.create_mission()", p13)
    probe("P14", "network_gateway.AgentNetworkGateway()", p14)
    probe("P15", "world_interface.execute(filesystem.read)", p15)
    probe("P16", "governance.SecurityChain.evaluate()", p16)
    probe("P17", "economy.BudgetEngine.reserve/consume()", p17)
    probe("P18", "verification.VerificationEngine.verify()", p18a)
    probe("P18", "verification.ExperienceEngine.store()", p18b)
    probe("P19", "evolution.EvolutionEngine()", p19)
    probe("P20", "l10k.L10KRegistry.register_task()", p20a)
    probe("P20", "vhl_benchmark.run_vhl_benchmark()", p20b)
    probe("P21", "hardening.run_hardening_suite()", p21)

    print("\n" + "=" * 96)
    audited = [r for r in ROWS if r[2] > 0]
    clean = [r for r in ROWS if r[2] <= 0 and r[3] == "ok"]
    errored = [r for r in ROWS if r[3] == "ERR"]
    print(f"共 {len(ROWS)} 项：产生审计 {len(audited)} / 不产生审计 {len(clean)} / 出错 {len(errored)}")
    print("\n【产生审计 — 下沉路径成立】")
    for phase, label, delta, _, _ in audited:
        print(f"   +{delta:<3} {phase} {label}")
    print("\n【不产生审计 — 该模块的关键操作未写入审计】")
    for phase, label, _, _, _ in clean:
        print(f"    0    {phase} {label}")
    if errored:
        print("\n【探针出错（结论不确定）】")
        for phase, label, _, _, err in errored:
            print(f"    {phase} {label}  [{err}]")

    print("\n--- 审计事件的 policy_decision 取值分布 ---")
    dist: dict = {}
    for ev in (audit_query(limit=300) or []):
        decision = (ev.get("details") or {}).get("policy_decision")
        dist[decision] = dist.get(decision, 0) + 1
    print(f"   {dist}")
    print("\n--- policy 引擎对装饰器 actor 的判决（四种风险等级）---")
    engine = get_policy_engine()
    for risk in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
        decision = engine.evaluate_simple(
            actor={"type": "system", "verified": True},
            action={"name": "memory.store", "risk_level": risk},
            resource=None, scope=None,
        )
        print(f"   risk={risk:9} -> {decision.decision.value}")

    print("\n注意：policy_decision 恒为 deny 且装饰器从不拦截 —— 见 AI-LAYER-DOD-AUDIT.md §3.5.4。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
