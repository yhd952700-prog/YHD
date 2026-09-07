"""End-to-end integration tests — Phases 9 / 10 / 12 / 15 composition.

Proves the four layers compose into one runnable scenario: Organization wraps a
role-specialized collaboration team; the team's output is persisted through an
L-Core orchestrator into the World Interface, then read back and verified.
"""
import os

from src.ai.e2e_demo import run_e2e_demo


def test_full_scenario_composes(tmp_path):
    out = run_e2e_demo(output_dir=str(tmp_path))

    # --- Act 1+3: Organization structure, budget, policy, KPI ---
    org = out["org"]
    assert org["member_count"] == 1
    assert org["department_count"] == 2
    assert org["goal_count"] == 1
    assert org["budget_total"] == 5000.0
    assert org["budget_spent"] == 1200.0
    assert org["budget_remaining"] == 3800.0
    assert org["tasks_submitted"] == 1
    assert org["tasks_completed"] == 1
    assert out["kpi"]["success_rate"] == 1.0

    # --- Act 2: Collaboration pipeline handed off through three roles ---
    assert out["pipeline_status"] == "completed"
    assert out["pipeline_stages"] == ["researcher", "analyst", "qa"]
    # Deterministic echo provider: three nested transforms.
    assert out["report_text"].startswith("analysis of: analysis of: analysis of:")

    # --- Act 4: L-Core closed the loop and routed to a real tool ---
    lcore = out["lcore"]
    assert lcore["status"] == "completed"
    assert lcore["task_count"] == 1
    assert all(r["success"] for r in lcore["results"].values())

    # --- The report is a real artifact on disk, read back through World ---
    assert os.path.exists(out["report_path"])
    read_back = out["world_read_back"]
    assert read_back["status"] == "observed"
    assert read_back["data"] == out["report_text"]


def test_scenario_is_isolated_per_run(tmp_path):
    """Each run writes into its own directory and reports its own path."""
    first = run_e2e_demo(output_dir=str(tmp_path / "a"))
    second = run_e2e_demo(output_dir=str(tmp_path / "b"))
    assert first["report_path"] != second["report_path"]
    assert os.path.exists(first["report_path"])
    assert os.path.exists(second["report_path"])
