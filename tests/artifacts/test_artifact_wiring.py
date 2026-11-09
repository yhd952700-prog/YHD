"""产物索引「接线」的验收测试（不重复 ``test_artifact_index`` 的单元覆盖）。

三条硬要求，对应本轮接线：

1. 不传 ``artifact_index`` 时，执行结果与接线前一致：事件流、任务状态、
   任务输出、验证结果都不变。
2. 传索引时，执行成功，且索引能双向回答「这次执行产出了什么」与
   「这个文件是哪次执行写的」。
3. 索引写失败不得让执行失败 —— 索引是旁路。

全部数据落在 ``tmp_path``，不读真实工作区。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.ai.agent_runtime import AgentRuntime
from src.artifacts.index import ArtifactIndex
from src.kernels.execution import ExecutionEngine, Goal, Task, TaskStatus
from src.kernels.execution._journal import ExecutionJournal


@pytest.fixture()
def workspace(tmp_path: Path) -> str:
    ws = tmp_path / "ws"
    ws.mkdir()
    return os.path.realpath(str(ws))


@pytest.fixture()
def idx(tmp_path: Path) -> ArtifactIndex:
    index = ArtifactIndex(str(tmp_path / "artifacts.db"))
    yield index
    index.close()


def _write_executor(workspace: str):
    """真实落盘的 capability executor（与生产 file_write 的契约一致）。"""

    def execute(capability_id: str, inputs: dict) -> dict:
        with open(inputs["path"], "w", encoding="utf-8") as handle:
            handle.write(inputs["content"])
        return {
            "success": True,
            "status": "executed",
            "path": inputs["path"],
            "workspace_root": workspace,
        }

    return execute


class _OneTaskDecomposer:
    """把 goal 固定拆成一条「写报告」任务，避免依赖真实规划逻辑。"""

    def __init__(self, abs_path: str, assigned_agent: str | None = None) -> None:
        self._abs_path = abs_path
        self._assigned_agent = assigned_agent

    def decompose(self, goal: Goal):
        task = Task(
            id="task-1",
            goal_id=goal.id,
            name="write-report",
            description="write the report",
            capability_id="file_write",
            inputs={"path": self._abs_path, "content": "# report\n"},
        )
        if self._assigned_agent is not None:
            task.assigned_agent = self._assigned_agent
        return [task]


def _run(
    tmp_path: Path,
    workspace: str,
    journal_name: str | None,
    *,
    artifact_index=None,
    executor=None,
    assigned_agent=None,
):
    # journal_name=None 表示这条执行链**不接 journal**（默认部署的最坏情况）。
    journal = ExecutionJournal(str(tmp_path / journal_name)) if journal_name else None
    engine = ExecutionEngine(
        capability_executor=executor or _write_executor(workspace),
        journal=journal,
        artifact_index=artifact_index,
    )
    engine.decomposer = _OneTaskDecomposer(
        os.path.join(workspace, "report.md"), assigned_agent=assigned_agent
    )
    ctx = engine.execute_goal(
        Goal(id="goal-wire", natural_language="write the report"), plan_mode="auto"
    )
    return engine, journal, ctx


#: journal 事件里与本次改造无关的易变字段（plan_id 每次是新的 uuid，
#: exec_started_at 是执行开始时的真实时间戳，两次运行不可能相同）。
_VOLATILE_PAYLOAD_KEYS = frozenset({"plan_id", "exec_started_at"})


def _journal_signature(journal: ExecutionJournal, execution_id: str):
    """把 journal 事件流压成可比对的签名（顺序、类型、稳定 payload）。"""
    return [
        (
            event.event_type,
            tuple(
                sorted(
                    (key, value)
                    for key, value in (event.payload or {}).items()
                    if key not in _VOLATILE_PAYLOAD_KEYS
                )
            ),
        )
        for event in journal.events(execution_id)
    ]


def test_without_index_execution_is_unchanged(
    tmp_path: Path, workspace: str, idx: ArtifactIndex
) -> None:
    """不传索引：执行结果与挂了索引时逐项一致，且索引一行都不写。"""
    base_engine, base_journal, base_ctx = _run(tmp_path, workspace, "base.journal")
    wired_engine, wired_journal, wired_ctx = _run(
        tmp_path, workspace, "wired.journal", artifact_index=idx
    )

    # 接缝是显式的：不传就是 None，不会偷偷造一个索引。
    assert base_engine.artifact_index is None
    assert wired_engine.artifact_index is idx

    # 事件流完全相同（索引不写 journal，也不改任何 payload）。
    assert _journal_signature(base_journal, "goal-wire") == _journal_signature(
        wired_journal, "goal-wire"
    )

    # 执行结果完全相同。
    assert base_ctx.failed_tasks == wired_ctx.failed_tasks == set()
    assert base_ctx.completed_tasks == wired_ctx.completed_tasks == {"task-1"}
    assert base_journal.completed_task_artifacts("goal-wire") == (
        wired_journal.completed_task_artifacts("goal-wire")
    )

    base_result = base_ctx.task_results["task-1"]
    wired_result = wired_ctx.task_results["task-1"]
    assert base_result.success == wired_result.success is True
    assert base_result.output == wired_result.output
    assert base_result.output == {
        "success": True,
        "status": "executed",
        "path": os.path.join(workspace, "report.md"),
        "workspace_root": workspace,
        "capability": "file_write",
    }
    assert (
        base_ctx.verification_results["task-1"].result
        == wired_ctx.verification_results["task-1"].result
    )


def test_unattached_index_stays_empty(tmp_path: Path, workspace: str) -> None:
    """索引对象存在但没挂到引擎上时，一次真实执行也不该往里写任何行。"""
    detached = ArtifactIndex(str(tmp_path / "detached.db"))
    try:
        _, _, ctx = _run(tmp_path, workspace, "detached.journal")
        assert ctx.failed_tasks == set()
        assert os.path.exists(os.path.join(workspace, "report.md"))
        assert detached.artifacts_for_execution("goal-wire") == []
        assert detached.stats()["writes"] == 0
    finally:
        detached.close()


def test_wired_index_answers_both_directions(
    tmp_path: Path, workspace: str, idx: ArtifactIndex
) -> None:
    """传索引：执行成功，且「执行→产物」「文件→执行」都能反查。"""
    _, _, ctx = _run(tmp_path, workspace, "wired.journal", artifact_index=idx)
    assert ctx.failed_tasks == set()
    assert ctx.completed_tasks == {"task-1"}

    # 方向一：这次执行产出了什么。
    assert idx.paths_for_execution("goal-wire") == ["report.md"]
    record = idx.artifacts_for_execution("goal-wire")[0]
    assert record.execution_id == "goal-wire"
    assert record.goal_id == "goal-wire"
    assert record.task_id == "task-1"
    assert record.task_name == "write-report"
    assert record.capability_id == "file_write"
    assert record.source == "kernel"
    # journal.record() 没有返回 seq —— 这里必须是 None，不能编造。
    assert record.journal_seq is None
    assert idx.verify(record.artifact_id).status == "present_unchanged"

    # 方向二：这个文件是哪次执行写的。
    producers = idx.executions_for_file("report.md", workspace=workspace)
    assert [p["execution_id"] for p in producers] == ["goal-wire"]


class _ExplodingIndex:
    """模拟索引写失败（磁盘满 / 库损坏 / 契约变更）。"""

    def record_from_task_output(self, **kwargs):
        raise RuntimeError("artifact index is broken")


def test_index_failure_does_not_fail_execution(
    tmp_path: Path, workspace: str
) -> None:
    """索引是旁路：写失败只记警告，执行仍必须成功。"""
    _, journal, ctx = _run(
        tmp_path, workspace, "broken.journal", artifact_index=_ExplodingIndex()
    )
    assert ctx.failed_tasks == set()
    assert ctx.completed_tasks == {"task-1"}
    assert journal.completed_task_artifacts("goal-wire") == {
        "write-report": os.path.join(workspace, "report.md")
    }


def test_index_works_without_journal(
    tmp_path: Path, workspace: str, idx: ArtifactIndex
) -> None:
    """不接 journal 时，产物的执行归属也不能丢（execution_id 取 goal.id）。"""
    _, journal, ctx = _run(tmp_path, workspace, None, artifact_index=idx)
    assert journal is None
    assert ctx.failed_tasks == set()
    assert idx.paths_for_execution("goal-wire") == ["report.md"]
    assert [
        producer["execution_id"]
        for producer in idx.executions_for_file("report.md", workspace=workspace)
    ] == ["goal-wire"]


def test_unattributed_agent_is_not_written_to_index(
    tmp_path: Path, workspace: str, idx: ArtifactIndex
) -> None:
    """归属不成立的 agent 不得进索引 —— 与 COMPLETED_UNATTRIBUTED 同一口径。"""

    def ghost_executor(capability_id: str, inputs: dict) -> dict:
        with open(inputs["path"], "w", encoding="utf-8") as handle:
            handle.write(inputs["content"])
        return {
            "success": True,
            "status": "executed",
            "path": inputs["path"],
            "workspace_root": workspace,
            "agent_attribution": "unattributed",
        }

    _, _, ctx = _run(
        tmp_path,
        workspace,
        "ghost.journal",
        artifact_index=idx,
        executor=ghost_executor,
        assigned_agent="ghost-agent",
    )
    assert ctx.task_results["task-1"].success is True
    assert ctx.plan.tasks[0].status == TaskStatus.COMPLETED_UNATTRIBUTED
    assert idx.paths_for_execution("goal-wire") == ["report.md"]
    # 产物是真的，执行者归属不是 —— 索引只能记前者。
    assert idx.artifacts_for_execution("goal-wire")[0].agent_id is None


def test_agent_runtime_passes_index_through(idx: ArtifactIndex, tmp_path: Path) -> None:
    """AgentRuntime 只做透传：给了就挂到引擎，不给仍是 None。"""
    wired = AgentRuntime(scope="L1", artifact_index=idx)
    assert wired.engine.artifact_index is idx

    plain = AgentRuntime(scope="L1")
    assert plain.engine.artifact_index is None
