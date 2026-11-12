"""能力缺口测试：唯一来源必须是 docs/capability-map.md 的四态表格。"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.intel.capabilities import (
    build_gap_records,
    load_capability_map,
    parse_capability_map,
    select_relevant_rows,
)
from src.intel.models import Candidate, GapStatus, ProposalStrategy
from src.intel.proposals import build_proposal

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_MAP_PATH = _PROJECT_ROOT / "docs" / "capability-map.md"


def test_real_capability_map_parses_all_four_states():
    rows = load_capability_map(_MAP_PATH)
    assert len(rows) >= 30
    states = {row.state for row in rows}
    assert {
        GapStatus.EXISTING,
        GapStatus.PARTIAL,
        GapStatus.FAKE,
        GapStatus.MISSING,
    }.issubset(states)

    by_id = {row.row_id: row for row in rows}
    assert by_id["1.1"].state is GapStatus.EXISTING
    assert by_id["1.3"].state is GapStatus.PARTIAL
    assert by_id["1.4"].state is GapStatus.FAKE
    assert by_id["1.5"].state is GapStatus.MISSING
    assert by_id["4.1"].state is GapStatus.EXISTING
    assert by_id["6.8"].state is GapStatus.FAKE

    assert any(row.file_paths for row in rows)
    assert "src/ai/agent_runtime.py" in by_id["2.2"].file_paths
    assert "src/tasks/local_scheduler.py" in by_id["3.5"].file_paths


def test_parser_ignores_unknown_rows_and_missing_source_fails_loudly(tmp_path):
    text = (
        "## 1. 感知层（Perception）\n\n"
        "| # | 能力 | 状态 | 证据 |\n|---|---|---|---|\n"
        "| 1.1 | 文本 | 🟢 真接线 | `src/a.py` |\n"
        "| 1.9 | 未知状态 | 也许有 | - |\n"
    )
    rows = parse_capability_map(text)
    assert len(rows) == 1
    assert rows[0].state is GapStatus.EXISTING
    assert rows[0].file_paths == ("src/a.py",)

    with pytest.raises(FileNotFoundError):
        load_capability_map(tmp_path / "missing.md")


def test_gap_records_use_only_map_evidence_and_mark_relevance():
    rows = load_capability_map(_MAP_PATH)
    candidate = Candidate(
        repo_url="https://github.com/alpha/agent",
        name="agent",
        first_seen_at="2026-11-06T03:00:00",
        last_assessed_at="2026-11-06T03:00:00",
        description="Agent orchestration framework",
        topics=["agent", "workflow"],
        last_status="fetched",
    )
    records = build_gap_records(
        candidate,
        rows,
        "2026-11-06T03:00:00",
        map_path="docs/capability-map.md",
    )

    assert len(records) == len(rows)
    assert all("唯一对照源：docs/capability-map.md" in record.note for record in records)
    assert all(record.status in {status.value for status in GapStatus} for record in records)
    assert any(record.relevant for record in records)
    # 证据只能来自能力地图；不读取候选仓库 README。
    assert all(
        "README" not in path for record in records for path in record.file_paths
    )

    relevant = select_relevant_rows(candidate, rows)
    assert relevant
    assert any("agent" in row.evidence for row in relevant)


def test_fake_state_forces_adopt_instead_of_borrow():
    rows = load_capability_map(_MAP_PATH)
    by_id = {row.row_id: row for row in rows}
    candidate = Candidate(
        repo_url="https://github.com/alpha/agent",
        name="agent",
        first_seen_at="2026-11-06T03:00:00",
        last_assessed_at="2026-11-06T03:00:00",
        topics=["automation"],
    )
    fake_gap = build_gap_records(candidate, [by_id["3.6"]], "2026-11-06T03:00:00")[0]
    existing_gap = build_gap_records(candidate, [by_id["1.1"]], "2026-11-06T03:00:00")[0]

    decision = type(
        "Decision",
        (),
        {
            "allowed": True,
            "reason": "MIT",
            "requires_note": False,
            "license_id": "MIT",
            "verdict": type("Verdict", (), {"value": "permissive"}),
            "category": type("Category", (), {"value": "runtime"}),
        },
    )
    fake_proposal = build_proposal(candidate, decision, [fake_gap], "t")
    existing_proposal = build_proposal(candidate, decision, [existing_gap], "t")

    assert fake_gap.status == GapStatus.FAKE.value
    assert fake_proposal.strategy == ProposalStrategy.ADOPT.value
    assert existing_proposal.strategy == ProposalStrategy.BORROW.value
