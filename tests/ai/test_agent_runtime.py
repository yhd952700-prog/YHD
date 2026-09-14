"""Phase 3 — Agent Runtime 测试。

覆盖（NO-FAKE，全部离线、使用引擎内置模拟执行器）：
- run_goal 主路径：目标级状态机走到 COMPLETED
- Observer 按 correlation_id 构建 ExecutionTrace
- Verifier 根因加固：output 为 None/非 dict 时优雅判失败（不再 TypeError 崩溃）
- Failure Recovery：replan 钩子（幂等重跑 + 最大次数上限）
- Evaluator 接线：run_goal 返回的 evaluation 非 None 且字段可读
- MemoryUpdate：Episodic 记录可回查
"""

from src.ai.agent_runtime import (
    AgentRunResult,
    AgentRunState,
    AgentRuntime,
    ExecutionTrace,
)
from src.kernels.execution import ActionResult, Task, Verifier, VerifyResult
from src.kernels.memory import MemoryScope, get_memory_kernel


def _runtime() -> AgentRuntime:
    # 默认无 capability_executor → 引擎内置 _simulate_capability（离线、无 LLM）。
    return AgentRuntime(scope="L1")


def test_run_goal_happy_path():
    rt = _runtime()
    result = rt.run_goal("search for the latest AI news", goal_id="g1")
    assert result.state == AgentRunState.COMPLETED
    assert result.context is not None
    assert result.evaluation is not None
    assert len(result.memory_keys) >= 1
    assert result.error is None


def test_observer_builds_trace():
    rt = _runtime()
    result = rt.run_goal("search and find documents about kernels", goal_id="g2")
    trace: ExecutionTrace = result.trace
    types = {e.event_type for e in trace.entries}
    assert "task_started" in types
    assert "task_completed" in types
    # 全部事件必须同属本目标链路
    assert all(e.correlation_id == result.correlation_id for e in trace.entries)
    assert trace.summary()["event_count"] >= 2


def test_verifier_none_output_no_crash():
    """根因加固：output 非 dict 时返回 FAILED + replan_required，而非抛 TypeError。"""
    task = Task(
        id="t1", goal_id="g", name="X", description="", capability_id="network_bus"
    )
    action = ActionResult(action_id="a1", success=True, output=None)
    verifier = Verifier()
    res = verifier.verify(task, action, criteria={"answer": "x"})
    assert res.result == VerifyResult.FAILED
    assert res.replan_required is True


def test_replan_on_replan_required():
    rt = _runtime()
    # 先跑一轮（成功），再显式调用 replan 验证"重跑机制"生效且 replan_count+1。
    first = rt.run_goal("search for docs about memory", goal_id="g3")
    replaned = rt.replan(first)
    assert replaned.replan_count == 1
    assert replaned.goal_id == "g3"


def test_replan_respects_max_bound():
    rt = _runtime()
    prior = AgentRunResult(
        goal_id="gx",
        correlation_id="cx",
        state=AgentRunState.FAILED,
        context=None,
        evaluation=None,
        trace=ExecutionTrace(correlation_id="cx"),
        replan_count=rt.max_replans,  # 已达上限
    )
    same = rt.replan(prior)
    assert same is prior  # 不进入无限重规划


def test_evaluator_wired():
    rt = _runtime()
    result = rt.run_goal("find the quarterly report", goal_id="g4")
    assert result.evaluation is not None
    assert hasattr(result.evaluation, "replan_triggered")
    assert isinstance(result.evaluation.overall_score, float)


def test_memory_persist():
    rt = _runtime()
    result = rt.run_goal("search the knowledge index", goal_id="g5")
    mk = get_memory_kernel()
    recalled = mk.recall("episodic:run:g5", scope=MemoryScope.L1)
    assert recalled is not None
    # recall() returns a MemoryEntry; the stored payload is on .value
    assert recalled.value.get("goal_id") == "g5"
    assert result.memory_keys
