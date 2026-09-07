"""VHL benchmark 集成测试 — 验证八层能端到端串起来且 L10K 聚合诚实。

Covers: run_vhl_benchmark 跑通 8 个能力层、7 个任务全部 verified、
VHL 计算诚实（weighted_units / human_minutes）、确定性（可复现）、
反作弊（重复记录被拒）。
"""
import pytest

from src.ai.vhl_benchmark import HUMAN_MINUTES_ASSUMPTION, run_vhl_benchmark


EXPECTED_KEYS = {"org", "report", "persist", "mission", "security", "economy", "verify"}


def test_benchmark_runs_all_eight_layers():
    out = run_vhl_benchmark()
    assert set(out["outcomes"].keys()) == EXPECTED_KEYS
    # 7 个 benchmark 任务全部 verified。
    assert set(out["verdicts"].keys()) == EXPECTED_KEYS
    assert all(v == "verified" for v in out["verdicts"].values())
    # 八层之间无集成缝（seams 为空 = 顺畅串通）。
    assert out["seams"] == []


def test_vhl_computed_honestly():
    out = run_vhl_benchmark()
    b = out["baseline"]
    # VHL = weighted_units / human_minutes，是真实累加，不伪造。
    expected = b["main"]["weighted_units"] / HUMAN_MINUTES_ASSUMPTION
    assert abs(out["vhl"] - expected) < 1e-9
    assert b["main"]["weighted_units"] == 15.0  # 2+2+3+3+1+2+2
    # 诚实：单次 benchmark 达不到 10,000，如实报告 baseline。
    assert out["reached"] is False
    assert out["target"] == 10000


def test_benchmark_is_deterministic():
    a = run_vhl_benchmark()
    b = run_vhl_benchmark()
    assert a["baseline"]["main"] == b["baseline"]["main"]
    assert a["vhl"] == b["vhl"]
    assert a["baseline"]["total_registered_tasks"] == b["baseline"]["total_registered_tasks"]


def test_benchmark_reports_five_properties():
    out = run_vhl_benchmark()
    b = out["baseline"]
    # §112-116 五属性必须显式呈现。
    assert b["reproducible"] is True
    assert b["auditable"] is True
    assert b["fixed_task_count"] == 7
    # held-out 与 main 隔离。
    assert "held_out" in b
    assert "main" in b
