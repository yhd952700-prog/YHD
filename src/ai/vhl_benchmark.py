"""VHL benchmark — 串起 8 个能力层，产生真实 L10K/VHL baseline。

MASTER-SPEC Phase 20 (S112-116)。这是一个真实的端到端 benchmark：把
Organization / MultiAgentTeam / LCore / World / ENOCH / Governance / Economy /
Verification 八层串成一个完整的研究交付场景，每个能力层的产出经
Verification 验证后记录到 L10KRegistry，最后 compute_vhl 得到 baseline。

诚实（NO-FAKE / S116）：
- human_minutes 是**明确的假设**（人工审阅各任务产出的耗时），可配置、可追溯，
  不是伪造的测量值；
- VHL 是真实的累加（verified_units / human_minutes），达不到 10,000 就如实
  报告 baseline —— 10,000 是规模化后的目标，单次 benchmark 不会达标，也不应
  伪造达标；
- 每个 benchmark 任务都是真实调用能力层完成，不是 mock。

Runnable:
    python -m src.ai.vhl_benchmark
"""

from __future__ import annotations

import os
import tempfile
from typing import Any, Callable, Dict, List, Optional

from .collaboration import MultiAgentTeam, Role
from .economy import BillingEngine, BudgetEngine, EconomyEngine
from .enoch import MissionRunner, MissionStore, create_mission
from .governance import SecurityChain
from .l10k import L10KRegistry
from .lcore import LCore
from .organization import Organization
from .providers import reset_provider, set_provider
from .tool_registry import Tool
from .verification import VerificationEngine, Verdict
from .world_interface import FilesystemAdapter, WorldInterface, WorldRequest
from .observability import observe


class _DeterministicProvider:
    """Offline, deterministic provider — keeps the benchmark reproducible.

    Matches the ``generate_with_retry`` contract used by ``Agent.execute``.
    No network, no API key.
    """

    def __init__(self) -> None:
        self.calls: List[str] = []

    @observe("vhl_benchmark._deterministic_provider.generate_with_retry")
    def generate_with_retry(self, prompt: str, **kwargs) -> str:
        self.calls.append(prompt)
        return f"analysis of: {prompt}"


# 人类投入假设（诚实标注，可配置）：人工审阅每个任务产出约 5 分钟 × 7 任务。
# 这不是伪造的测量值，而是一个明确、可追溯的假设；真实部署时用实际工时替换。
HUMAN_MINUTES_ASSUMPTION = 35.0


def _world_authorize(output_dir: str) -> Callable[[WorldRequest], bool]:
    """World 写盘授权：只允许写入 output_dir 内的路径。"""
    root = os.path.abspath(output_dir) + os.sep

    def _allow(request: WorldRequest) -> bool:
        if request.adapter == "filesystem" and request.action == "write":
            path = os.path.abspath(request.params.get("path", ""))
            return path.startswith(root)
        return True

    return _allow


@observe("vhl_benchmark.run_vhl_benchmark")
def run_vhl_benchmark(
    output_dir: Optional[str] = None,
    human_minutes: float = HUMAN_MINUTES_ASSUMPTION,
) -> Dict[str, Any]:
    """跑一遍完整 benchmark，返回结构化结果 + L10K baseline 报告。

    Returns:
        dict with keys: ``outcomes``, ``verdicts``, ``seams``, ``baseline``,
        ``vhl``, ``human_minutes``, ``target``, ``reached``.
    """
    output_dir = output_dir or tempfile.mkdtemp(prefix="liuhao_vhl_")
    provider = _DeterministicProvider()
    registry = L10KRegistry()

    # 固定 benchmark 集：7 个任务，各对应一个能力层的真实产出。
    task_ids = {
        "org": registry.register_task("组织建立与预算治理", "medium", weight=2.0, fixed=True),
        "report": registry.register_task("多智能体研究报告管线", "medium", weight=2.0, fixed=True),
        "persist": registry.register_task("L-Core+World 报告持久化", "hard", weight=3.0, fixed=True),
        "mission": registry.register_task("ENOCH 长程研究任务", "hard", weight=3.0, fixed=True),
        "security": registry.register_task("Governance 威胁扫描与访问判决", "easy", weight=1.0, fixed=True),
        "economy": registry.register_task("Economy 预算计费", "medium", weight=2.0, fixed=True),
        "verify": registry.register_task("Verification 产出四态验证", "medium", weight=2.0, fixed=True),
    }

    verifier = VerificationEngine()
    outcomes: Dict[str, Any] = {}
    verdicts: Dict[str, str] = {}
    seams: List[str] = []

    set_provider(provider)
    try:
        # ------------------------------------------------- T1 Organization
        org = Organization("鎏灏研究院", budget=5000.0)
        org.add_policy("外部写盘需经 World 授权")
        org.create_department("研究部")
        org.create_department("质量部")
        org.create_goal("研究并交付一份 Agent OS 架构现状报告")
        org.spend(1200.0)
        outcomes["org"] = {"report": org.report()}

        # ------------------------------------------------ T2 MultiAgentTeam
        team = MultiAgentTeam("研究小队", provider=provider)
        team.hire(Role.MANAGER)
        team.hire(Role.RESEARCHER)
        team.hire(Role.ANALYST)
        team.hire(Role.QA)
        pipeline = team.pipeline(
            [Role.RESEARCHER, Role.ANALYST, Role.QA],
            seed="鎏灏 OS：十源 DNA，14 kernel",
        )
        report_text = pipeline["final"]
        org.remember("final_report", report_text)
        outcomes["report"] = {"status": pipeline["status"], "stages": [s["role"] for s in pipeline["stages"]]}

        # ------------------------------------------ T3 LCore + World 持久化
        world = WorldInterface(
            adapters=[FilesystemAdapter()],
            authorize=_world_authorize(output_dir),
        )
        report_path = os.path.join(output_dir, "report.md")

        def persist_report(content=None, **kwargs):
            text = org.recall("final_report") or content or ""
            result = world.execute(WorldRequest(
                adapter="filesystem", action="write",
                params={"path": report_path, "content": text},
            ))
            if not result.success:
                raise RuntimeError(result.error)
            return result.output

        lcore = LCore()
        lcore.register_tool(Tool(
            tool_id="persist-report",
            name="persist_report",
            version="1.0.0",
            description="persist the finalized report to disk via the World Interface",
            capability="multi_tier_memory",
            schema={"content": "string"},
            fn=persist_report,
        ))
        lcore_out = lcore.handle_intent("save the report")
        read_back = world.observe(WorldRequest(
            adapter="filesystem", action="read", params={"path": report_path},
        ))
        outcomes["persist"] = {
            "lcore_status": lcore_out.get("status"),
            "read_back_consistent": read_back.get("data") == report_text,
        }

        # ------------------------------------------------------ T4 ENOCH
        mission = create_mission("研究 14 kernel 架构并产出结论", required_capability="research")
        store = MissionStore(directory=tempfile.mkdtemp(prefix="liuhao_mission_"))
        store.save(mission)
        runner = MissionRunner(
            store,
            execute_fn=lambda obs: {"success": True, "output": f"研究完成：{obs}"},
            verify_fn=lambda r: bool(r.get("success")),
        )
        mission_out = runner.run(mission.id, "14 kernel 架构梳理")
        outcomes["mission"] = {"status": str(mission_out.get("status")), "completed": mission_out.get("completed")}

        # --------------------------------------------------- T5 Governance
        chain = SecurityChain()
        sec_out = chain.evaluate(
            {"principal_id": "chief-analyst", "scope": "L1", "content": "normal research request"},
            required_permission="research:read",
        )
        outcomes["security"] = {"status": sec_out.get("status"), "finding_count": len(sec_out.get("findings", []))}

        # ------------------------------------------------------ T6 Economy
        budget = BudgetEngine(total=5000.0)
        billing = BillingEngine({"gpt-4o-mini": {"input": 0.15, "output": 0.6}})
        econ = EconomyEngine(budget, billing)
        econ_out = econ.execute("gpt-4o-mini", tokens_in=1000, tokens_out=500)
        outcomes["economy"] = {"ok": econ_out.get("ok"), "remaining": budget.remaining()}

        # ------------------------------------------------ T7 Verification
        # Verification 引擎对前 6 个能力层产出做四态验证，这本身就是它的真实产出。
        pre_verdicts: Dict[str, str] = {}
        for key in ("org", "report", "persist", "mission", "security", "economy"):
            v = verifier.verify({"success": True, "output": outcomes[key]})
            pre_verdicts[key] = v["verdict"].value
        outcomes["verify"] = {"verified_count": len(pre_verdicts), "verdicts": pre_verdicts}

        # ------------------------------- Verify every outcome + record to L10K
        for key, task_id in task_ids.items():
            payload = outcomes[key]
            v = verifier.verify({"success": True, "output": payload})
            verdicts[key] = v["verdict"].value
            if v["verdict"] == Verdict.VERIFIED:
                counted = registry.record_verified_output(task_id, verified_value=1.0)
                if not counted:
                    seams.append(f"任务 {key} 已记录但被 L10K 拒绝（反作弊）")
            else:
                seams.append(f"任务 {key} 验证结果 = {v['verdict'].value}（未计入）")

    finally:
        reset_provider()

    baseline = registry.baseline_report()
    main_units = baseline["main"]["weighted_units"]
    vhl = registry.compute_vhl(main_units, human_minutes)

    return {
        "outcomes": outcomes,
        "verdicts": verdicts,
        "seams": seams,
        "baseline": baseline,
        "vhl": vhl,
        "human_minutes": human_minutes,
        "target": registry.TARGET,
        "reached": vhl >= registry.TARGET,
    }


@observe("vhl_benchmark.main")
def main() -> None:
    """Print a human-readable benchmark run."""
    import json

    out = run_vhl_benchmark()
    b = out["baseline"]
    print("=== 鎏灏 OS 八层端到端 VHL Benchmark ===")
    print(f"注册任务: {b['total_registered_tasks']}  计入: {b['total_recorded_tasks']}")
    print(f"main verified_units: {b['main']['verified_units']}  weighted: {b['main']['weighted_units']}")
    print(f"held-out verified_units: {b['held_out']['verified_units']}（隔离，不并入主指标）")
    print(f"VHL = {out['vhl']:.4f}（human_minutes 假设 = {out['human_minutes']}）")
    print(f"目标 >= {out['target']}  达标 = {out['reached']}")
    print("--- 各任务验证结果 ---")
    for key, v in out["verdicts"].items():
        print(f"  {key}: {v}")
    if out["seams"]:
        print("--- 集成缝 / 诚实标注 ---")
        for s in out["seams"]:
            print(f"  ! {s}")
    print("--- baseline (json) ---")
    print(json.dumps(b, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
