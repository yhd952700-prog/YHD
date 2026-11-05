"""产物索引的「人能用」层验收：报表 + CLI。

覆盖范围对应本轮要求：

1. 文件被外部删除后，查询**仍然返回记录**并标 ``missing``（不抛异常、不返空）；
2. 工作区占用按目录统计、按大小列出最大的 N 个；
3. 清理建议只生成清单（含「失败执行留下半成品」「同一路径版本堆积」），
   且明确说明不释放磁盘/不删除；
4. ``python -m src.artifacts`` 的 JSON 输出可被脚本直接解析。

全部数据落在 ``tmp_path``，不读真实工作区。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.artifacts.cli import main
from src.artifacts.index import ArtifactIndex
from src.artifacts.report import (
    cleanup_suggestions,
    states_for_records,
    usage,
)
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


def _run_cli(capsys: pytest.CaptureFixture[str], argv: list[str]):
    code = main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# ---------------------------------------------------------------------- #
# 缺失文件：如实标注，不抛异常、不返空
# ---------------------------------------------------------------------- #
def test_missing_file_is_reported_not_silently_dropped(
    idx: ArtifactIndex, workspace: str
) -> None:
    abs_path = _write(workspace, "notes/a.md", "hello")
    idx.record(
        workspace=workspace,
        path="notes/a.md",
        execution_id="exec-1",
        goal_id="goal-1",
        task_name="write-a",
    )

    os.remove(abs_path)

    # 1) 按执行查：记录还在，不能被静默过滤成空。
    records = idx.artifacts_for_execution("exec-1")
    assert len(records) == 1
    assert records[0].path == "notes/a.md"

    states = states_for_records(idx, records)
    assert len(states) == 1
    assert states[0].status == "missing"
    assert states[0].exists is False
    assert states[0].size is None

    # 2) 按文件查：历史仍然可读（文件不存在不应让查询失败）。
    history = idx.history_for_file("notes/a.md", workspace=workspace)
    assert [record.execution_id for record in history] == ["exec-1"]
    assert idx.verify(history[0].artifact_id).status == "missing"

    # 3) 反查执行：仍然能回答「谁写过它」。
    producers = idx.executions_for_file("notes/a.md", workspace=workspace)
    assert [item["execution_id"] for item in producers] == ["exec-1"]


# ---------------------------------------------------------------------- #
# 工作区占用
# ---------------------------------------------------------------------- #
def test_usage_counts_directories_and_largest(
    idx: ArtifactIndex, workspace: str
) -> None:
    _write(workspace, "root.txt", "a" * 10)
    _write(workspace, "docs/b.md", "b" * 100)
    _write(workspace, "docs/c.md", "c" * 50)
    _write(workspace, "media/d.bin", "d" * 300)
    for rel in ("root.txt", "docs/b.md", "docs/c.md", "media/d.bin"):
        idx.record(workspace=workspace, path=rel, execution_id="exec-1")

    summary = usage(idx, workspace, top=2)

    assert summary.total_files == 4
    assert summary.total_bytes == 460
    assert summary.index_records == 4
    assert summary.missing_records == 0

    directories = {entry.directory: (entry.files, entry.bytes) for entry in summary.directories}
    assert directories["(根目录)"] == (1, 10)
    assert directories["docs"] == (2, 150)
    assert directories["media"] == (1, 300)
    # 按大小倒序：media > docs > 根目录。
    assert [entry.directory for entry in summary.directories] == [
        "media",
        "docs",
        "(根目录)",
    ]

    assert [state.path for state in summary.largest] == ["media/d.bin", "docs/b.md"]


def test_usage_ignores_missing_files_but_counts_them(
    idx: ArtifactIndex, workspace: str
) -> None:
    abs_path = _write(workspace, "notes/a.md", "hello")
    idx.record(workspace=workspace, path="notes/a.md", execution_id="exec-1")
    os.remove(abs_path)

    summary = usage(idx, workspace)

    assert summary.total_files == 0
    assert summary.total_bytes == 0
    assert summary.index_records == 1
    assert summary.missing_records == 1
    assert summary.largest == ()


# ---------------------------------------------------------------------- #
# 清理建议：只建议，不删除
# ---------------------------------------------------------------------- #
def _failed_execution_journal(tmp_path: Path, execution_id: str) -> ExecutionJournal:
    journal = ExecutionJournal(str(tmp_path / "execution.journal"))
    journal.record(execution_id, "execution_started")
    journal.record(execution_id, "task_started", "write")
    journal.record(execution_id, "task_failed", "write", reason="boom")
    journal.record(
        execution_id,
        "execution_completed",
        plan_id="plan-1",
        status="failed",
        completed=0,
        failed=1,
        resumed=False,
    )
    return journal


def test_cleanup_failed_execution_requires_journal(
    idx: ArtifactIndex, workspace: str, tmp_path: Path
) -> None:
    _write(workspace, "draft/report.md", "half")
    idx.record(
        workspace=workspace,
        path="draft/report.md",
        execution_id="exec-fail",
        goal_id="goal-fail",
        task_id="task-1",
        task_name="write",
    )

    # 没有 journal 时不能凭猜测判定「失败」。
    without_journal = cleanup_suggestions(idx, workspace)
    assert [item for item in without_journal if item.kind == "failed_execution"] == []

    journal = _failed_execution_journal(tmp_path, "exec-fail")
    try:
        with_journal = cleanup_suggestions(idx, workspace, journal=journal)
    finally:
        journal.close()

    failed = [item for item in with_journal if item.kind == "failed_execution"]
    assert len(failed) == 1
    assert failed[0].paths == ("draft/report.md",)
    assert failed[0].reclaimable_bytes == len("half")
    assert failed[0].evidence["execution_id"] == "exec-fail"
    assert failed[0].evidence["failed_tasks"] == ["write"]
    # 建议里只有「人工确认后删除」，没有任何自动删除动作。
    assert "人工确认" in failed[0].action


def test_cleanup_version_pileup_and_duplicate_content(
    idx: ArtifactIndex, workspace: str
) -> None:
    for version, execution_id in enumerate(("exec-1", "exec-2", "exec-3"), start=1):
        _write(workspace, "notes/a.md", f"v{version}")
        idx.record(
            workspace=workspace, path="notes/a.md", execution_id=execution_id
        )

    _write(workspace, "dup/one.txt", "same-content")
    _write(workspace, "dup/two.txt", "same-content")
    idx.record(workspace=workspace, path="dup/one.txt", execution_id="exec-4")
    idx.record(workspace=workspace, path="dup/two.txt", execution_id="exec-5")

    suggestions = cleanup_suggestions(idx, workspace, min_versions=3)
    kinds = [item.kind for item in suggestions]
    assert "version_pileup" in kinds
    assert "duplicate_content" in kinds

    pileup = [item for item in suggestions if item.kind == "version_pileup"]
    assert [item.paths for item in pileup] == [("notes/a.md",)]
    assert pileup[0].reclaimable_bytes == 0
    # 必须老实说明：裁索引不释放磁盘（磁盘只有最新一份）。
    assert "不会释放磁盘空间" in pileup[0].action
    assert pileup[0].evidence["versions"] == 3

    duplicate = [item for item in suggestions if item.kind == "duplicate_content"]
    assert len(duplicate) == 1
    assert duplicate[0].paths == ("dup/one.txt", "dup/two.txt")
    assert duplicate[0].reclaimable_bytes == len("same-content")
    assert duplicate[0].evidence["keep"] == "dup/one.txt"


def test_cleanup_stale_record_does_not_claim_reclaimable_disk(
    idx: ArtifactIndex, workspace: str
) -> None:
    abs_path = _write(workspace, "gone.md", "x")
    idx.record(workspace=workspace, path="gone.md", execution_id="exec-1")
    os.remove(abs_path)

    suggestions = cleanup_suggestions(idx, workspace)
    stale = [item for item in suggestions if item.kind == "stale_record"]
    assert len(stale) == 1
    assert stale[0].paths == ("gone.md",)
    assert stale[0].reclaimable_bytes == 0
    assert "外部删除" in stale[0].action


# ---------------------------------------------------------------------- #
# CLI：三种查询 + 占用 + 清理，JSON 可解析
# ---------------------------------------------------------------------- #
def test_cli_execution_and_file_json(
    idx: ArtifactIndex, workspace: str, capsys: pytest.CaptureFixture[str]
) -> None:
    abs_path = _write(workspace, "notes/a.md", "hello")
    idx.record(
        workspace=workspace,
        path="notes/a.md",
        execution_id="exec-1",
        goal_id="goal-1",
        task_id="task-1",
        task_name="write-a",
    )
    os.remove(abs_path)

    code, out, err = _run_cli(
        capsys,
        [
            "--db",
            idx.db_path,
            "--workspace",
            workspace,
            "--json",
            "execution",
            "exec-1",
        ],
    )
    assert code == 0
    assert err == ""
    payload = json.loads(out)
    assert payload["command"] == "execution"
    assert len(payload["artifacts"]) == 1
    assert payload["artifacts"][0]["path"] == "notes/a.md"
    assert payload["artifacts"][0]["status"] == "missing"
    assert payload["artifacts"][0]["exists"] is False
    assert payload["artifacts"][0]["version"] == 1

    code, out, err = _run_cli(
        capsys,
        [
            "--db",
            idx.db_path,
            "--workspace",
            workspace,
            "--json",
            "file",
            "notes/a.md",
        ],
    )
    assert code == 0
    assert err == ""
    payload = json.loads(out)
    assert payload["command"] == "file"
    assert payload["tracked"] is True
    assert payload["exists"] is False
    assert payload["status"] == "missing"
    assert payload["history"][0]["status"] == "missing"
    assert payload["executions"][0]["execution_id"] == "exec-1"
    assert payload["executions"][0]["task_names"] == ["write-a"]


def test_cli_goal_usage_and_human_output(
    idx: ArtifactIndex, workspace: str, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(workspace, "docs/b.md", "b" * 100)
    _write(workspace, "docs/c.md", "c" * 10)
    idx.record(
        workspace=workspace,
        path="docs/b.md",
        execution_id="exec-1",
        goal_id="goal-1",
    )
    idx.record(
        workspace=workspace,
        path="docs/c.md",
        execution_id="exec-2",
        goal_id="goal-1",
    )

    code, out, _ = _run_cli(
        capsys,
        [
            "--db",
            idx.db_path,
            "--workspace",
            workspace,
            "--json",
            "goal",
            "goal-1",
        ],
    )
    assert code == 0
    payload = json.loads(out)
    assert {item["path"] for item in payload["artifacts"]} == {
        "docs/b.md",
        "docs/c.md",
    }

    code, out, _ = _run_cli(
        capsys,
        [
            "--db",
            idx.db_path,
            "--workspace",
            workspace,
            "--json",
            "usage",
            "--top",
            "1",
        ],
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["command"] == "usage"
    assert payload["total_files"] == 2
    assert payload["total_bytes"] == 110
    assert len(payload["largest"]) == 1
    assert payload["largest"][0]["path"] == "docs/b.md"

    # 非 JSON 输出也要能看：包含中文表头与状态标签。
    code, out, _ = _run_cli(
        capsys,
        ["--db", idx.db_path, "--workspace", workspace, "usage"],
    )
    assert code == 0
    assert "工作区：" in out
    assert "按目录：" in out


def test_cli_cleanup_json_and_missing_journal(
    idx: ArtifactIndex, workspace: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(workspace, "draft/report.md", "half")
    idx.record(
        workspace=workspace,
        path="draft/report.md",
        execution_id="exec-fail",
        task_id="task-1",
    )

    journal = _failed_execution_journal(tmp_path, "exec-fail")
    journal_path = journal.db_path
    journal.close()

    code, out, err = _run_cli(
        capsys,
        [
            "--db",
            idx.db_path,
            "--workspace",
            workspace,
            "--json",
            "cleanup",
            "--journal",
            journal_path,
        ],
    )
    assert code == 0
    assert err == ""
    payload = json.loads(out)
    assert payload["command"] == "cleanup"
    assert payload["note"].startswith("仅建议")
    assert any(
        item["kind"] == "failed_execution" for item in payload["suggestions"]
    )

    # journal 不存在时必须明确说明跳过失败判定，而不是假装没有失败执行。
    code, out, _ = _run_cli(
        capsys,
        [
            "--db",
            idx.db_path,
            "--workspace",
            workspace,
            "cleanup",
            "--journal",
            str(tmp_path / "nope.journal"),
        ],
    )
    assert code == 0
    assert "未找到执行 journal" in out


def test_cli_missing_index_is_friendly_and_creates_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = str(tmp_path / "never-created.db")

    code, out, err = _run_cli(capsys, ["--db", db_path, "--json", "usage"])

    assert code == 0
    assert err == ""
    payload = json.loads(out)
    assert payload["exists"] is False
    assert "不存在" in payload["note"]
    # 只读查询不能顺手创建索引库。
    assert not os.path.exists(db_path)
