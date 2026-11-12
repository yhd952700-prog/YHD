"""产物索引 —— 独立验收测试。

这个文件只测 ``src/artifacts``：不依赖真实工作区、不依赖网关、不读全局
``LIUHAO_WORKSPACE_ROOT``（每个用例都显式给根），全部数据落在 ``tmp_path``。

验收的核心是两个方向，而不是「表建出来了」：

    给定一次执行 → 它产出了哪些文件
    给定一个文件 → 哪次执行写的它

最后一条用例用**真实的 ExecutionEngine + 真实 journal + 真实落盘**跑一遍，
确保索引读的不是测试捏造的事件格式。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.artifacts.index import ArtifactIndex, WorkspaceViolation
from src.kernels.execution import ExecutionEngine, Goal, Task
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


def _write(ws: str, rel: str, text: str) -> str:
    abs_path = os.path.join(ws, rel)
    parent = os.path.dirname(abs_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return abs_path


# ---------------------------------------------------------------------- #
# 方向一：执行 -> 产物
# ---------------------------------------------------------------------- #
def test_artifacts_for_execution(idx: ArtifactIndex, workspace: str) -> None:
    _write(workspace, "reports/q4.md", "# Q4\n")
    _write(workspace, "reports/q4.csv", "a,b\n1,2\n")

    idx.record(
        workspace=workspace,
        path="reports/q4.md",
        execution_id="exec-1",
        goal_id="goal-1",
        task_id="task-1",
        task_name="write-md",
        capability_id="file_write",
    )
    idx.record(
        workspace=workspace,
        path="reports/q4.csv",
        execution_id="exec-1",
        goal_id="goal-1",
        task_id="task-2",
        task_name="write-csv",
    )
    # 另一条执行写的是别的文件，不能串进来。
    _write(workspace, "other.txt", "x")
    idx.record(workspace=workspace, path="other.txt", execution_id="exec-2")

    paths = idx.paths_for_execution("exec-1")
    assert sorted(paths) == ["reports/q4.csv", "reports/q4.md"]

    records = idx.artifacts_for_execution("exec-1")
    by_path = {r.path: r for r in records}
    assert by_path["reports/q4.md"].task_name == "write-md"
    assert by_path["reports/q4.md"].capability_id == "file_write"
    assert by_path["reports/q4.csv"].task_id == "task-2"


def test_artifacts_for_task(idx: ArtifactIndex, workspace: str) -> None:
    """给一次任务，它产出了哪些文件（任务级反查）。"""
    _write(workspace, "a.md", "1")
    _write(workspace, "b.md", "2")
    idx.record(
        workspace=workspace, path="a.md", execution_id="exec-1", task_id="task-1"
    )
    idx.record(
        workspace=workspace, path="b.md", execution_id="exec-1", task_id="task-2"
    )
    assert [r.path for r in idx.artifacts_for_task("task-1")] == ["a.md"]
    assert [r.path for r in idx.artifacts_for_task("task-2")] == ["b.md"]


# ---------------------------------------------------------------------- #
# 方向二：文件 -> 执行
# ---------------------------------------------------------------------- #
def test_executions_for_file(idx: ArtifactIndex, workspace: str) -> None:
    _write(workspace, "notes/a.md", "v1")
    idx.record(
        workspace=workspace,
        path="notes/a.md",
        execution_id="exec-1",
        goal_id="goal-1",
        task_id="task-1",
        task_name="write-a",
    )

    producers = idx.executions_for_file("notes/a.md", workspace=workspace)
    assert len(producers) == 1
    assert producers[0]["execution_id"] == "exec-1"
    assert producers[0]["goal_id"] == "goal-1"
    assert producers[0]["task_names"] == ["write-a"]


def test_executions_for_file_accepts_absolute_path(
    idx: ArtifactIndex, workspace: str
) -> None:
    _write(workspace, "notes/a.md", "v1")
    idx.record(workspace=workspace, path="notes/a.md", execution_id="exec-1")
    producers = idx.executions_for_file(
        os.path.join(workspace, "notes", "a.md"), workspace=workspace
    )
    assert [p["execution_id"] for p in producers] == ["exec-1"]


# ---------------------------------------------------------------------- #
# 同名覆盖 / 多版本
# ---------------------------------------------------------------------- #
def test_overwrite_creates_new_version_and_keeps_history(
    idx: ArtifactIndex, workspace: str
) -> None:
    _write(workspace, "notes/a.md", "v1")
    first = idx.record(
        workspace=workspace, path="notes/a.md", execution_id="exec-1", task_id="t1"
    )
    assert first.version == 1

    _write(workspace, "notes/a.md", "v2")
    second = idx.record(
        workspace=workspace, path="notes/a.md", execution_id="exec-2", task_id="t2"
    )
    assert second.version == 2
    assert second.content_sha256 != first.content_sha256

    history = idx.history_for_file("notes/a.md", workspace=workspace)
    assert [r.version for r in history] == [2, 1]
    assert history[0].execution_id == "exec-2"
    assert history[1].execution_id == "exec-1"

    # 反查能同时列出两个执行，而不是只剩最后一个。
    producers = idx.executions_for_file("notes/a.md", workspace=workspace)
    assert [p["execution_id"] for p in producers] == ["exec-2", "exec-1"]


def test_same_execution_same_bytes_is_idempotent(
    idx: ArtifactIndex, workspace: str
) -> None:
    _write(workspace, "notes/a.md", "same")
    kwargs = dict(
        workspace=workspace, path="notes/a.md", execution_id="exec-1", task_id="t1"
    )
    first = idx.record(**kwargs)
    second = idx.record(**kwargs)
    assert first.artifact_id == second.artifact_id
    assert idx.stats()["writes"] == 1


def test_different_execution_same_bytes_is_a_new_version(
    idx: ArtifactIndex, workspace: str
) -> None:
    """内容没变，但换了一次执行 —— 这是两次真实写入，不能合并成一条。"""
    _write(workspace, "notes/a.md", "same")
    first = idx.record(workspace=workspace, path="notes/a.md", execution_id="exec-1")
    second = idx.record(workspace=workspace, path="notes/a.md", execution_id="exec-2")
    assert second.version == 2
    assert second.content_sha256 == first.content_sha256


# ---------------------------------------------------------------------- #
# 边界与诚实性
# ---------------------------------------------------------------------- #
def test_path_outside_workspace_is_rejected(
    idx: ArtifactIndex, workspace: str, tmp_path: Path
) -> None:
    outside = tmp_path / "secret.txt"
    outside.write_text("x", encoding="utf-8")
    with pytest.raises(WorkspaceViolation):
        idx.record(workspace=workspace, path=str(outside), execution_id="exec-1")
    assert idx.stats()["writes"] == 0


def test_missing_file_is_recorded_but_verifiable_as_missing(
    idx: ArtifactIndex, workspace: str
) -> None:
    record = idx.record(
        workspace=workspace, path="notes/never-written.md", execution_id="exec-1"
    )
    assert record.content_sha256 is None
    assert record.size is None

    verification = idx.verify(record.artifact_id)
    assert verification.status == "missing"
    assert verification.exists is False


def test_verify_detects_external_change(idx: ArtifactIndex, workspace: str) -> None:
    _write(workspace, "notes/a.md", "original")
    record = idx.record(workspace=workspace, path="notes/a.md", execution_id="exec-1")

    assert idx.verify(record.artifact_id).status == "present_unchanged"

    _write(workspace, "notes/a.md", "someone else edited this")
    changed = idx.verify(record.artifact_id)
    assert changed.status == "present_changed"
    assert changed.actual_sha256 != changed.expected_sha256


def test_record_from_task_output_uses_kernel_contract(
    idx: ArtifactIndex, workspace: str
) -> None:
    abs_path = _write(workspace, "notes/a.md", "x")
    record = idx.record_from_task_output(
        workspace=workspace,
        output={"path": abs_path, "workspace_root": workspace},
        execution_id="exec-1",
        task_id="t1",
    )
    assert record is not None
    assert record.path == "notes/a.md"

    # 输出没有声明产物时，不能凭空造一条记录。
    assert (
        idx.record_from_task_output(
            workspace=workspace, output={"ok": True}, execution_id="exec-1"
        )
        is None
    )


# ---------------------------------------------------------------------- #
# 两个只读对账入口
# ---------------------------------------------------------------------- #
def test_goal_file_adapter(idx: ArtifactIndex, workspace: str, tmp_path: Path) -> None:
    _write(workspace, "notes/a.md", "v1")
    goal_file = tmp_path / "liuhao_goals.json"
    goal_file.write_text(
        json.dumps(
            {
                "goal-1": {
                    "artifacts": ["notes/a.md"],
                    "correlation_id": "corr-1",
                }
            }
        ),
        encoding="utf-8",
    )

    report = idx.index_goal_file(str(goal_file), workspace_root=workspace)
    assert report == {"indexed": 1, "skipped": 0}

    records = idx.artifacts_for_goal("goal-1")
    assert [r.path for r in records] == ["notes/a.md"]
    # goal 级产物没有任务归属 —— 这里必须是 None，不能编一个出来。
    assert records[0].task_id is None
    assert records[0].correlation_id == "corr-1"


def test_journal_adapter_end_to_end(
    idx: ArtifactIndex, workspace: str, tmp_path: Path
) -> None:
    """真实执行引擎 → 真实 journal → 索引 → 双向反查。"""
    journal = ExecutionJournal(str(tmp_path / "execution.journal"))
    abs_path = os.path.join(workspace, "report.md")

    def capability_executor(capability_id: str, inputs: dict) -> dict:
        with open(inputs["path"], "w", encoding="utf-8") as handle:
            handle.write(inputs["content"])
        return {
            "success": True,
            "status": "executed",
            "path": inputs["path"],
            "workspace_root": workspace,
        }

    class _OneTaskDecomposer:
        def decompose(self, goal):
            return [
                Task(
                    id="task-1",
                    goal_id=goal.id,
                    name="write-report",
                    description="write the report",
                    capability_id="file_write",
                    inputs={"path": abs_path, "content": "# report\n"},
                )
            ]

    engine = ExecutionEngine(
        capability_executor=capability_executor, journal=journal
    )
    engine.decomposer = _OneTaskDecomposer()

    ctx = engine.execute_goal(
        Goal(id="goal-e2e", natural_language="write the report"), plan_mode="auto"
    )
    assert ctx.failed_tasks == set()

    # 先证明 journal 真的记下了产物（否则后面的索引只是在读空气）。
    assert journal.completed_task_artifacts("goal-e2e") == {"write-report": abs_path}
    assert os.path.exists(abs_path)

    report = idx.index_journal(journal, workspace_root=workspace)
    assert report["indexed"] == 1
    assert report["skipped"] == 0

    # 方向一：这次执行产出了什么。
    assert idx.paths_for_execution("goal-e2e") == ["report.md"]
    record = idx.artifacts_for_execution("goal-e2e")[0]
    assert record.task_name == "write-report"
    assert record.goal_id == "goal-e2e"
    assert record.journal_seq is not None
    assert idx.verify(record.artifact_id).status == "present_unchanged"

    # 方向二：这个文件是哪次执行写的。
    producers = idx.executions_for_file("report.md", workspace=workspace)
    assert [p["execution_id"] for p in producers] == ["goal-e2e"]

    journal.close()
