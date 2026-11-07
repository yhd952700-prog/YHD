"""P0-3 并发目标串号回归：同一 ExecutionEngine 上的两个目标，journal 事件
必须严格落在各自的 execution_id 下，任务完成事件不得记到另一个目标名下。

测试用真实的 ExecutionJournal 落盘 + 真实的 execute_goal 流程；只有一个
capability executor 被门控，用来制造确定性的交错：C 先开跑并停在任务里，
D 完整跑完，然后 C 才继续 —— 旧实现里 C 恢复时会读实例上的
``_execution_id``（此时已被 D 覆盖），于是 C 的 task_completed 记进 D。
"""

from __future__ import annotations

import threading

from src.kernels.execution import ExecutionEngine, Goal, PlanStatus
from src.kernels.execution._journal import ExecutionJournal


def test_two_goals_keep_their_own_journal_events(tmp_path):
    journal = ExecutionJournal(str(tmp_path / "journal.db"))
    engine = ExecutionEngine(journal=journal)

    c_started = threading.Event()
    release_c = threading.Event()

    # 任务在引擎自己的线程池里执行，线程名不可控；用能力 id 做确定性门控：
    # C 的 Search 走 ``network_bus``，D 的 Plan 走 ``execution_pipeline``。
    def executor(capability_id, inputs):
        if capability_id == "network_bus":
            c_started.set()
            assert release_c.wait(10), "C never continued"
        return {"ok": True, "capability": capability_id}

    engine.set_capability_executor(executor)

    results: dict = {}

    def run_c():
        results["C"] = engine.execute_goal(
            Goal(id="goal-C", natural_language="search the archive", scope="L1")
        )

    thread_c = threading.Thread(target=run_c, name="goal-C-runner")
    thread_c.start()
    assert c_started.wait(10), "C never started"

    results["D"] = engine.execute_goal(
        Goal(id="goal-D", natural_language="plan the rollout", scope="L1")
    )

    release_c.set()
    thread_c.join(10)
    assert not thread_c.is_alive()

    c_tasks = journal.completed_task_names("goal-C")
    d_tasks = journal.completed_task_names("goal-D")
    assert c_tasks == {"Search"}, f"C 的任务被写到了错误 execution_id 下: {c_tasks}"
    assert d_tasks == {"Plan"}, f"D 的任务被写到了错误 execution_id 下: {d_tasks}"
    assert "Plan" not in c_tasks
    assert "Search" not in d_tasks

    # 执行开始/结束事件也必须一一对应，不能出现 D 收到两次 completed。
    c_events = journal.events("goal-C")
    d_events = journal.events("goal-D")
    assert [e.event_type for e in c_events].count("execution_completed") == 1
    assert [e.event_type for e in d_events].count("execution_completed") == 1
    assert results["C"].plan.status == PlanStatus.COMPLETED
    assert results["D"].plan.status == PlanStatus.COMPLETED
