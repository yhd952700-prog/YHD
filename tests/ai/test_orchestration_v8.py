"""第二季 V8：多 Bot 协作编排测试。

覆盖：引擎三补口（超时收尾 + 迟到结果丢弃、预绑定不被轮换覆盖、pass_task
上下文）、成员选择规则、编排全链（派活/上游简报/换人重试/上限不重试/
无成员如实失败/部分完成口径/软取消/汇总在图外）。
"""

from __future__ import annotations

import time

import pytest

from src.ai.goal_task_graph import GoalDefinition, GoalTaskGraph, TaskNode, TaskStatus
from src.ai.orchestration import (
    MemberCapReached,
    TeamOrchestrator,
    select_assignments,
)


def _graph(goal_text="把新品发布会办了", tasks=()):
    graph = GoalTaskGraph(
        provider=object(),
        goal=GoalDefinition(id="goal_t", description=goal_text),
        enable_observability=False,
    )
    if tasks:
        graph._store_tasks("goal_t", list(tasks))
    return graph


def _task(tid, desc, ttype="general", deps=()):
    return TaskNode(id=tid, description=desc, task_type=ttype, depends_on=list(deps))


MEMBERS = [
    {"id": "bot-research", "name": "调研员", "job": "市场调研", "order_index": 0},
    {"id": "bot-writer", "name": "文案官", "job": "文案编辑", "order_index": 1},
    {"id": "bot-writer2", "name": "二号文案", "job": "文案写作", "order_index": 2},
    {"id": "bot-hidden", "name": "隐身者", "job": "市场调研", "order_index": 3, "hidden": 1},
]


# ---------------------------------------------------------------- 引擎补口


def test_engine_respects_prebound_agent_and_passes_task():
    seen = {}

    def executor(agent_id, description, task):
        seen["agent"] = agent_id
        seen["task_id"] = task.id
        return "ok"

    graph = _graph(tasks=[_task("task_1", "查竞品")])
    graph.tasks["task_1"].assigned_agent = "bot-research"
    out = graph.execute_graph("goal_t", executor, pass_task=True)
    assert out["status"] == "completed"
    assert seen == {"agent": "bot-research", "task_id": "task_1"}


def test_engine_timeout_fails_task_and_discards_late_result():
    import threading

    worker_done = threading.Event()

    def slow_executor(agent_id, description):
        time.sleep(3)
        worker_done.set()
        return "迟到的成果"

    graph = _graph(tasks=[_task("task_1", "慢活")])
    started = time.time()
    out = graph.execute_graph("goal_t", slow_executor, task_timeout_seconds=0.5)
    elapsed = time.time() - started
    assert elapsed < 2.5, "整图不许陪等超时任务（3 秒的活、0.5 秒收尾）"
    assert out["status"] == "failed"
    task = graph.tasks["task_1"]
    assert task.status == TaskStatus.FAILED
    assert "超时" in (task.error or "")
    # 等迟到线程真正跑完，再断言它的结果被丢弃、状态没被改写
    assert worker_done.wait(timeout=10), "迟到线程应已自然结束"
    time.sleep(0.2)
    assert task.status == TaskStatus.FAILED
    assert task.result is None
    assert "超时" in (task.error or "")


def test_engine_without_timeout_keeps_old_behaviour():
    graph = _graph(tasks=[_task("task_1", "普通活")])
    out = graph.execute_graph("goal_t", lambda a, d: "done")
    assert out["status"] == "completed"
    assert graph.tasks["task_1"].assigned_agent == "agent_0"


# ---------------------------------------------------------------- 成员选择


def test_selector_unique_job_match_wins():
    tasks = [_task("t1", "查市场", "research"), _task("t2", "写稿", "writing")]
    picked = select_assignments(tasks, MEMBERS)
    assert picked["t1"] == "bot-research"  # hidden 的同岗位不算
    assert picked["t2"] in {"bot-writer", "bot-writer2"}
    first = picked["t2"]
    tasks2 = [_task("t3", "再写一篇", "writing")]
    picked2 = select_assignments(tasks2, MEMBERS)
    assert picked2["t3"] == first  # 单任务时负载兜底取 order 靠前者，稳定可复现


def test_selector_multi_match_balances_load():
    tasks = [_task(f"t{i}", f"写稿{i}", "writing") for i in range(4)]
    picked = select_assignments(tasks, MEMBERS)
    counts = {}
    for bot in picked.values():
        counts[bot] = counts.get(bot, 0) + 1
    assert counts == {"bot-writer": 2, "bot-writer2": 2}


def test_selector_empty_pool_assigns_none():
    tasks = [_task("t1", "查市场", "research")]
    picked = select_assignments(tasks, [])
    assert picked["t1"] is None
    assert tasks[0].metadata["bot_id"] if "bot_id" in tasks[0].metadata else True
    assert tasks[0].metadata["select_reason"] == "无可用成员"


# ---------------------------------------------------------------- 编排全链


def _ok_runner(replies):
    def runner(bot_id, brief, *, idempotency_key):
        replies.append((bot_id, brief))
        if bot_id == "jarvis":
            return "汇总：都办好了。"
        return f"{bot_id} 的成果"
    return runner


def test_orchestration_full_chain_with_upstream_brief():
    replies = []
    graph = _graph(tasks=[
        _task("task_1", "查竞品价格", "research"),
        _task("task_2", "按调研写卖点", "writing", deps=["task_1"]),
    ])
    orch = TeamOrchestrator(graph, MEMBERS, _ok_runner(replies))
    result = orch.run("goal_t", task_timeout_seconds=30)
    assert result["status"] == "completed"
    assert result["completed"] == 2 and result["failed"] == 0
    assert result["synthesis"] == "汇总：都办好了。"
    briefs = {b: br for b, br in replies if b != "jarvis"}
    assert "上游结果" in briefs["bot-writer"] or "上游结果" in briefs["bot-writer2"]
    assert "bot-research 的成果" in "".join(briefs.values())


def test_orchestration_handover_retry_once():
    calls = []

    def flaky_runner(bot_id, brief, *, idempotency_key):
        calls.append(bot_id)
        if bot_id == "jarvis":
            return "汇总"
        if bot_id == "bot-writer":
            raise RuntimeError("文案官掉线了")
        return f"{bot_id} 接手完成"

    graph = _graph(tasks=[_task("task_1", "写发布稿", "writing")])
    orch = TeamOrchestrator(graph, MEMBERS, flaky_runner)
    result = orch.run("goal_t", task_timeout_seconds=30)
    assert result["status"] == "completed"
    item = result["items"][0]
    assert item["bot_id"] == "bot-writer2"
    assert item["attempts"] and item["attempts"][0]["bot_id"] == "bot-writer"
    assert "掉线" in item["attempts"][0]["error"]


def test_orchestration_no_substitute_partial_honest():
    def failing_runner(bot_id, brief, *, idempotency_key):
        if bot_id == "jarvis":
            assert "没有完成" in brief or "没成" in brief
            return "汇总：有一件没成。"
        raise RuntimeError("全员掉线")

    members = [MEMBERS[0]]  # 只有调研员，writing 无人直配也无替补之外的池
    graph = _graph(tasks=[
        _task("task_1", "查市场", "research"),
        _task("task_2", "写稿", "writing"),
    ])

    def runner(bot_id, brief, *, idempotency_key):
        if bot_id == "jarvis":
            return failing_runner(bot_id, brief, idempotency_key=idempotency_key)
        if "写稿" in brief:
            raise RuntimeError("全员掉线")
        return "调研成果"

    orch = TeamOrchestrator(graph, members, runner)
    result = orch.run("goal_t", task_timeout_seconds=30)
    assert result["status"] == "partial"
    assert result["completed"] == 1 and result["failed"] == 1
    assert result["synthesis"] == "汇总：有一件没成。"


def test_orchestration_cap_reached_no_retry():
    calls = []

    def runner(bot_id, brief, *, idempotency_key):
        calls.append(bot_id)
        return "不应该被调用到第二次"

    def cap_status(bot_id):
        return {"capped": True, "cap_usd": 10, "cost": 10}

    graph = _graph(tasks=[_task("task_1", "写稿", "writing")])
    orch = TeamOrchestrator(graph, MEMBERS, runner, cap_status=cap_status)
    result = orch.run("goal_t", task_timeout_seconds=30)
    assert result["failed"] == 1
    assert "上限" in (result["items"][0]["error"] or "")
    assert calls == ["jarvis"]  # 只有汇总被调用，成员一次没跑、更没换人


def test_orchestration_empty_pool_fails_honestly():
    graph = _graph(tasks=[_task("task_1", "查市场", "research")])
    orch = TeamOrchestrator(graph, [], _ok_runner([]))
    result = orch.run("goal_t", task_timeout_seconds=30)
    assert result["status"] == "failed"
    assert "没有能干这活的成员" in (result["items"][0]["error"] or "")


def test_orchestration_cancel_marks_skipped():
    graph = _graph(tasks=[_task("task_1", "查市场", "research")])
    orch = TeamOrchestrator(graph, MEMBERS, _ok_runner([]))
    orch.request_cancel()
    result = orch.run("goal_t", task_timeout_seconds=30)
    assert result["items"][0]["status"] == "skipped"
    assert result["skipped"] == 1


def test_orchestration_plan_preview_assigns_without_running():
    calls = []
    graph = _graph(tasks=[_task("task_1", "查市场", "research")])
    orch = TeamOrchestrator(graph, MEMBERS, _ok_runner(calls))
    plan = orch.plan("goal_t")
    assert plan["task_count"] == 1
    assert plan["tasks"][0]["bot_id"] == "bot-research"
    assert calls == []
