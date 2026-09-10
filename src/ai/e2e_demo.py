"""
End-to-end integration demo — MASTER-SPEC Phases 9 / 10 / 12 / 15.

Composes the four capability layers built after the kernel layer into one
runnable scenario, with the explicit goal of surfacing the *integration seams*
between them:

    Organization        (Phase 12)  structure, budget, policies, KPIs
      L-- MultiAgentTeam  (Phase 10)  researcher -> analyst -> QA pipeline
      L-- L-Core          (Phase 9)   intent -> plan -> policy -> execute -> verify
            L-- World Interface (Phase 15)  persist the report to the real FS

Scenario: a research organization hires a role-specialized team; the team
synthesizes a report through a sequential pipeline; an L-Core orchestrator
persists it to the filesystem through the World Interface (under an injected
authorization policy), then reads it back and verifies it.

Known seam (intentional): the L-Core routes ``capability_id`` -> ``Tool``, while
the World Interface exposes ``execute(WorldRequest)``. They are bridged here by
wrapping the World call inside a Tool's ``fn`` closure — the natural composition
point, not a hidden rewrite.

Runnable:
    python -m src.ai.e2e_demo
"""

from __future__ import annotations

import os
import tempfile
from typing import Any, Callable, Dict, Optional

from .collaboration import MultiAgentTeam, Role
from .lcore import LCore
from .organization import Organization
from .providers import reset_provider, set_provider
from .tool_registry import Tool
from .world_interface import FilesystemAdapter, WorldInterface, WorldRequest
from .observability import observe


class _DeterministicProvider:
    """Offline, deterministic provider: echoes a transform of the prompt.

    Matches the ``generate_with_retry`` contract used by ``Agent.execute``.
    No network, no API key — keeps the demo reproducible.
    """

    def __init__(self) -> None:
        self.calls: list = []

    @observe("e2e_demo._deterministic_provider.generate_with_retry")
    def generate_with_retry(self, prompt: str, **kwargs) -> str:
        self.calls.append(prompt)
        return f"analysis of: {prompt}"


def _world_authorize(output_dir: str) -> Callable[[WorldRequest], bool]:
    """Organization policy: only filesystem writes inside the sanctioned dir.

    Observe (read/list) is always allowed; writes are scoped to ``output_dir``.
    """

    root = os.path.abspath(output_dir) + os.sep

    def _allow(request: WorldRequest) -> bool:
        if request.adapter == "filesystem" and request.action == "write":
            path = os.path.abspath(request.params.get("path", ""))
            return path.startswith(root)
        return True

    return _allow


@observe("e2e_demo.run_e2e_demo")
def run_e2e_demo(
    output_dir: Optional[str] = None,
    provider: Optional[Any] = None,
) -> Dict[str, Any]:
    """Run the four-layer scenario and return a structured summary.

    Args:
        output_dir: where the report is persisted (default: a fresh temp dir).
        provider: deterministic provider (default: ``_DeterministicProvider``).
    """
    output_dir = output_dir or tempfile.mkdtemp(prefix="liuhao_e2e_")
    provider = provider or _DeterministicProvider()

    # The Organization's AgentFactory reads the *global* provider, while the
    # MultiAgentTeam takes one directly. Point both at the same provider so the
    # whole scenario is deterministic and offline.
    set_provider(provider)
    try:
        # ------------------------------------------------- Act 1: Organization
        org = Organization("鎏灏研究院", budget=5000.0)
        org.add_policy("外部写盘需经 World 授权")
        research_dept = org.create_department("研究部")
        org.create_department("质量部")
        org.create_goal("研究并交付一份 Agent OS 架构现状报告")
        org.spend(1200.0)

        # ------------------------------------------------ Act 2: Collaboration
        team = MultiAgentTeam("研究小队", provider=provider)
        manager = team.hire(Role.MANAGER)
        team.hire(Role.RESEARCHER)
        team.hire(Role.ANALYST)
        team.hire(Role.QA)

        delegate = team.delegate(manager, Role.RESEARCHER, "梳理 14 kernel 架构")
        pipeline = team.pipeline(
            [Role.RESEARCHER, Role.ANALYST, Role.QA],
            seed="鎏灏 OS：十源 DNA，14 kernel",
        )
        report_text = pipeline["final"]
        org.remember("final_report", report_text)

        # -------------------------------- Act 3: Organization hires + KPI
        analyst_id = org.hire(Role.ANALYST, research_dept, name="chief-analyst")
        org.delegate(analyst_id, Role.ANALYST, "审校报告")
        kpi = org.evaluate(analyst_id)

        # ------------------------------------------ Act 4: L-Core + World
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
            capability="multi_tier_memory",  # "save/store" keyword -> this capability
            schema={"content": "string"},
            fn=persist_report,
        ))

        lcore_out = lcore.handle_intent("save the report")

        # Read the report back through the World Interface (observe).
        read_back = world.observe(WorldRequest(
            adapter="filesystem", action="read", params={"path": report_path},
        ))

        return {
            "org": org.report(),
            "delegate_status": delegate["status"],
            "pipeline_status": pipeline["status"],
            "pipeline_stages": [s["role"] for s in pipeline["stages"]],
            "report_text": report_text,
            "kpi": kpi,
            "lcore": lcore_out,
            "world_read_back": read_back,
            "report_path": report_path,
        }
    finally:
        reset_provider()


@observe("e2e_demo.main")
def main() -> None:
    """Print a human-readable run of the scenario."""
    out = run_e2e_demo()
    read_ok = (
        out["world_read_back"]["status"] == "observed"
        and out["world_read_back"]["data"] == out["report_text"]
    )
    print("=== 鎏灏 OS 四层端到端演示 ===")
    print(f"组织: {out['org']}")
    print(
        f"协作管线: {out['pipeline_status']}  "
        f"阶段 {' -> '.join(out['pipeline_stages'])}"
    )
    print(f"成员 KPI: {out['kpi']}")
    print(
        f"L-Core: {out['lcore']['status']}  "
        f"任务数 {out['lcore']['task_count']}"
    )
    print(f"报告落盘: {out['report_path']}")
    print(f"读回校验: 一致 = {read_ok}")
    print("--- 报告内容 ---")
    print(out["report_text"])


if __name__ == "__main__":
    main()
