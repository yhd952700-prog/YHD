"""工作流引擎权威归属护栏（2026-09-13 裁决）。

背景：能力层 SSOT 是 ``LiuHao-O/packages/MAPPING.md``。仓库里曾同时存在两套
「目标 → 任务」工作流实现：

- ``src/ai/goal_task_graph.py``    —— 手写 DAG，**SSOT 权威**
- ``src/ai/langgraph_workflow.py`` —— LangGraph 状态机，**已评估、未采纳**

本测试钉死该归属，防止有人在不动 SSOT 的情况下静默翻转权威（或反过来，
改了权威却忘了同步标注与文档）。

注意：这不是能力测试，只是**一致性护栏** —— 它断言的是「映射表文本」与
「模块级常量」这两处契约可观测的事实，不依赖任何 docstring 措辞。
"""
from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MAPPING = _REPO_ROOT / "LiuHao-O" / "packages" / "MAPPING.md"

#: SSOT 里指向工作流实现的三行，全部必须落在手写 DAG 上。
_AUTHORITATIVE_PACKAGES = ("goal", "planning", "task")


def _mapping_rows():
    """解析 MAPPING.md 的 ``| 包 | 状态 | 复用模块 | 十源 |`` 表格。"""
    assert _MAPPING.is_file(), f"MAPPING.md 缺失：{_MAPPING}"
    rows = {}
    for line in _MAPPING.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\|\s*([a-z]+)\s*\|([^|]*)\|([^|]*)\|", line)
        if m:
            rows[m.group(1)] = m.group(3).strip()
    return rows


def test_mapping_table_maps_workflow_rows_to_handrolled_engine():
    """SSOT 的 goal / planning / task 三行都必须引用手写 DAG。"""
    rows = _mapping_rows()
    for pkg in _AUTHORITATIVE_PACKAGES:
        assert pkg in rows, f"MAPPING.md 缺少 {pkg} 行"
        assert "src.ai.goal_task_graph" in rows[pkg], (
            f"{pkg} 行不再指向手写 DAG：{rows[pkg]!r}；若确要改用 LangGraph 引擎，"
            "必须同时更新 MAPPING.md、facade 与 ENGINE_AUTHORITY 标注"
        )


def test_langgraph_engine_is_not_referenced_by_ssot_rows():
    """反向断言：没有任何 SSOT 行把 LangGraph 引擎当权威。"""
    offenders = {
        pkg: mods
        for pkg, mods in _mapping_rows().items()
        if "src.ai.langgraph_workflow" in mods
    }
    assert not offenders, (
        "LangGraph 引擎被写进了 SSOT 映射，但裁决结论是「已评估未采纳」："
        f"{offenders}。请先更新 MAPPING.md 的裁决小节与 ENGINE_AUTHORITY 标注。"
    )


def test_handrolled_engine_is_importable_and_exposes_goaltaskgraph():
    from src.ai.goal_task_graph import GoalTaskGraph

    assert GoalTaskGraph is not None


def test_langgraph_engine_declares_itself_experimental():
    """备选引擎必须自报 experimental —— runtime 可观测的常量，不是 docstring。"""
    from src.ai import langgraph_workflow

    assert getattr(langgraph_workflow, "ENGINE_AUTHORITY", None) == "experimental"
