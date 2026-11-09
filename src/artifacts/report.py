"""把产物索引变成可回答人问题的只读报表。

CLI（:mod:`src.artifacts.cli`）只做参数解析与排版，真正的计算都在这里：

* :func:`usage` —— 当前工作区占用（按目录、最大的 N 个文件）；
* :func:`cleanup_suggestions` —— **只建议，绝不删除**。

两条硬约束：

1. 一律以磁盘现状为准。索引里的 ``size`` 是写入当时的大小，文件后来被改过就
   不再可信；所以磁盘占用和「还在不在」都重新向文件系统取值，并用
   :meth:`ArtifactIndex.verify` 的哈希结果标注「未改 / 已改 / 缺失」。
2. 缺失不是异常。文件被外部删掉时，记录仍然出现在结果里，状态标 ``missing``
   —— 静默返回空列表等于骗人。
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .index import ArtifactIndex, ArtifactRecord, _normalize_workspace, _row_to_record

__all__ = [
    "FileState",
    "UsageEntry",
    "UsageSummary",
    "CleanupSuggestion",
    "latest_per_path",
    "state_for",
    "states_for_records",
    "usage",
    "cleanup_suggestions",
    "status_label",
]


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class FileState:
    """一条记录的**当下**状态（文件系统 + 哈希校验）。"""

    record: ArtifactRecord
    status: str          # present_unchanged / present_changed / missing / unverifiable
    exists: bool
    size: Optional[int]  # 磁盘上的当前大小；不存在时为 None

    @property
    def path(self) -> str:
        return self.record.path

    def as_dict(self) -> Dict[str, Any]:
        return {
            "artifact_id": self.record.artifact_id,
            "path": self.record.path,
            "abs_path": self.record.abs_path,
            "version": self.record.version,
            "status": self.status,
            "exists": self.exists,
            "size": self.size,
            "recorded_size": self.record.size,
            "recorded_at": self.record.recorded_at,
            "execution_id": self.record.execution_id,
            "goal_id": self.record.goal_id,
            "task_id": self.record.task_id,
            "task_name": self.record.task_name,
        }


@dataclass(frozen=True)
class UsageEntry:
    directory: str
    files: int
    bytes: int

    def as_dict(self) -> Dict[str, Any]:
        return {"directory": self.directory, "files": self.files, "bytes": self.bytes}


@dataclass(frozen=True)
class UsageSummary:
    workspace: str
    total_files: int
    total_bytes: int
    index_records: int
    missing_records: int
    directories: Tuple[UsageEntry, ...]
    largest: Tuple[FileState, ...]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "workspace": self.workspace,
            "total_files": self.total_files,
            "total_bytes": self.total_bytes,
            "index_records": self.index_records,
            "missing_records": self.missing_records,
            "directories": [entry.as_dict() for entry in self.directories],
            "largest": [state.as_dict() for state in self.largest],
        }


@dataclass(frozen=True)
class CleanupSuggestion:
    """一条**建议**。字段里没有「删除动作」，删除永远由人执行。"""

    kind: str
    title: str
    paths: Tuple[str, ...]
    reclaimable_bytes: int
    action: str
    evidence: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "title": self.title,
            "paths": list(self.paths),
            "reclaimable_bytes": self.reclaimable_bytes,
            "action": self.action,
            "evidence": self.evidence,
        }


_STATUS_LABELS = {
    "present_unchanged": "在 / 未改",
    "present_changed": "在 / 已改",
    "missing": "缺失",
    "unverifiable": "未知",
}


def status_label(status: str) -> str:
    return _STATUS_LABELS.get(status, status)


# --------------------------------------------------------------------------- #
# 内部读取
# --------------------------------------------------------------------------- #
def _all_records(index: ArtifactIndex, workspace: Optional[str] = None) -> List[ArtifactRecord]:
    """读原始观测行（按 workspace / path / version 排序）。

    同一包内直接读索引表：schema 由 :mod:`src.artifacts.index` 独占维护，
    这里只读、不写，且复用它的锁避免与写入交错。
    """
    ws = _normalize_workspace(workspace) if workspace else None
    try:
        rows = index.read_rows(ws)  # 由 ArtifactIndex 自己持锁，避免裸用 _conn
    except sqlite3.DatabaseError:
        return []
    return [_row_to_record(row) for row in rows]


def latest_per_path(records: Sequence[ArtifactRecord]) -> List[ArtifactRecord]:
    """每个 ``(workspace, path)`` 只保留 ``version`` 最大的一行。"""
    latest: Dict[Tuple[str, str], ArtifactRecord] = {}
    for record in records:
        key = (record.workspace, record.path)
        current = latest.get(key)
        if current is None or record.version > current.version:
            latest[key] = record
    return list(latest.values())


# --------------------------------------------------------------------------- #
# 状态 / 占用
# --------------------------------------------------------------------------- #
def state_for(index: ArtifactIndex, record: ArtifactRecord) -> FileState:
    verification = index.verify(record.artifact_id)
    size: Optional[int] = None
    if verification.exists:
        try:
            size = os.path.getsize(record.abs_path)
        except OSError:
            size = None
    return FileState(
        record=record,
        status=verification.status,
        exists=verification.exists,
        size=size,
    )


def states_for_records(
    index: ArtifactIndex, records: Sequence[ArtifactRecord]
) -> List[FileState]:
    return [state_for(index, record) for record in records]


def usage(
    index: ArtifactIndex, workspace: str, *, top: int = 10
) -> UsageSummary:
    """当前工作区的磁盘占用（按目录汇总 + 最大的 N 个文件）。

    ``total_files`` 只数磁盘上真实存在的文件（同一路径多版本算一个）；
    ``index_records`` 是索引里的观测行数，用来提醒「历史版本堆积」。
    """
    ws = _normalize_workspace(workspace)
    records = _all_records(index, workspace=ws)
    latest = latest_per_path(records)

    buckets: Dict[str, List[int]] = {}
    kept: List[FileState] = []
    missing = 0
    for record in latest:
        state = state_for(index, record)
        if not state.exists:
            missing += 1
            continue
        directory = os.path.dirname(record.path) or "(根目录)"
        bucket = buckets.setdefault(directory, [0, 0])
        bucket[0] += 1
        bucket[1] += state.size or 0
        kept.append(state)

    entries = tuple(
        sorted(
            (UsageEntry(directory, files, size) for directory, (files, size) in buckets.items()),
            key=lambda entry: (-entry.bytes, entry.directory),
        )
    )
    largest = tuple(
        sorted(kept, key=lambda state: (-(state.size or 0), state.path))[: max(top, 0)]
    )
    return UsageSummary(
        workspace=ws,
        total_files=len(kept),
        total_bytes=sum(state.size or 0 for state in kept),
        index_records=len(records),
        missing_records=missing,
        directories=entries,
        largest=largest,
    )


# --------------------------------------------------------------------------- #
# 清理建议
# --------------------------------------------------------------------------- #
def _execution_outcome(journal: Any, execution_id: str) -> Dict[str, Any]:
    """从 journal 判定一次执行的结果；无法判定时 ``known=False``。

    只认 journal 里明确的结论，不猜：没有 ``execution_completed`` /
    ``execution_cancelled`` 事件就返回 ``known=False``。
    """
    try:
        events = journal.events(execution_id)
    except Exception:  # noqa: BLE001 - 报表不能因 journal 损坏而崩
        return {"known": False, "outcome": None, "failed_tasks": []}

    outcome: Optional[str] = None
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.event_type == "execution_cancelled":
            outcome = "cancelled"
        elif event.event_type == "execution_completed":
            failed = payload.get("failed")
            status = payload.get("status")
            if isinstance(failed, int) and failed > 0:
                outcome = "failed"
            elif isinstance(status, str) and status not in ("completed", "success"):
                outcome = "failed"
            else:
                outcome = "completed"

    failed_tasks: List[str] = []
    try:
        failed_tasks = sorted(journal.failed_task_names(execution_id))
    except Exception:  # noqa: BLE001
        failed_tasks = []

    return {
        "known": outcome is not None,
        "outcome": outcome,
        "failed_tasks": failed_tasks,
    }


def _fmt_paths(paths: Sequence[str], limit: int = 3) -> str:
    if len(paths) <= limit:
        return "、".join(paths)
    return "、".join(paths[:limit]) + f" 等 {len(paths)} 个"


def cleanup_suggestions(
    index: ArtifactIndex,
    workspace: str,
    *,
    journal: Any = None,
    min_versions: int = 3,
) -> List[CleanupSuggestion]:
    """生成可清理建议。

    **不删除任何东西**。四类：

    * ``failed_execution``：执行最终失败/取消，但产物还在盘上（需 journal 才能判定）；
    * ``version_pileup``：同一路径写入观测堆积，磁盘只有最新一份（裁索引，不释放磁盘）；
    * ``duplicate_content``：不同路径内容完全相同，是**唯一**能真正回收磁盘的一类；
    * ``stale_record``：记录指向的文件已不在盘上（裁索引，不释放磁盘）。
    """
    ws = _normalize_workspace(workspace)
    records = _all_records(index, workspace=ws)
    latest = latest_per_path(records)
    states = {state.record.artifact_id: state for state in states_for_records(index, latest)}

    suggestions: List[CleanupSuggestion] = []

    # 1) 失败/取消的执行留下的产物。
    if journal is not None:
        by_execution: Dict[str, List[FileState]] = {}
        for record in latest:
            if not record.execution_id:
                continue
            state = states[record.artifact_id]
            if state.exists:
                by_execution.setdefault(record.execution_id, []).append(state)

        for execution_id, exec_states in sorted(by_execution.items()):
            outcome = _execution_outcome(journal, execution_id)
            if not outcome["known"] or outcome["outcome"] not in ("failed", "cancelled"):
                continue
            # 同一执行里同一路径只留最新一版。
            dedup: Dict[str, FileState] = {}
            for state in exec_states:
                current = dedup.get(state.path)
                if current is None or state.record.version >= current.record.version:
                    dedup[state.path] = state
            kept = sorted(dedup.values(), key=lambda state: state.path)
            total = sum(state.size or 0 for state in kept)
            label = "失败" if outcome["outcome"] == "failed" else "已取消"
            failed = outcome["failed_tasks"]
            reason = f"执行 {execution_id} 判定{label}"
            if failed:
                reason += f"（失败任务：{'、'.join(failed)}）"
            suggestions.append(
                CleanupSuggestion(
                    kind="failed_execution",
                    title=f"{label}执行留下的产物",
                    paths=tuple(state.path for state in kept),
                    reclaimable_bytes=total,
                    action=(
                        "人工确认后删除这些文件（它们属于一次没有成功收尾的执行）；"
                        "删除前请确认没有别的产物依赖它们"
                    ),
                    evidence={
                        "execution_id": execution_id,
                        "goal_id": kept[0].record.goal_id,
                        "outcome": outcome["outcome"],
                        "failed_tasks": failed,
                        "files": len(kept),
                    },
                )
            )

    # 2) 同一路径的历史版本堆积（索引行，不是磁盘文件）。
    versions: Dict[str, List[ArtifactRecord]] = {}
    for record in records:
        versions.setdefault(record.path, []).append(record)
    for path, rows in sorted(versions.items()):
        if len(rows) < max(min_versions, 2):
            continue
        state = states.get(
            max(rows, key=lambda item: item.version).artifact_id
        )
        suggestions.append(
            CleanupSuggestion(
                kind="version_pileup",
                title="同一路径的历史版本堆积",
                paths=(path,),
                reclaimable_bytes=0,
                action=(
                    f"索引里有 {len(rows)} 条写入观测；磁盘只保留最新一份，"
                    "裁剪旧观测不会释放磁盘空间（旧内容已被覆盖）"
                ),
                evidence={
                    "versions": len(rows),
                    "latest_version": max(row.version for row in rows),
                    "latest_status": state.status if state else "unknown",
                    "latest_size": state.size if state else None,
                },
            )
        )

    # 3) 不同路径内容完全相同 —— 唯一真正能回收磁盘的一类。
    by_hash: Dict[str, List[FileState]] = {}
    for record in latest:
        state = states.get(record.artifact_id)
        if state is None or not state.exists or not state.size:
            continue
        if state.status != "present_unchanged":
            continue
        sha = record.content_sha256
        if not sha:
            continue
        by_hash.setdefault(sha, []).append(state)

    for sha, group in sorted(by_hash.items()):
        if len(group) < 2:
            continue
        group = sorted(group, key=lambda state: state.path)
        keep = group[0]
        others = group[1:]
        reclaimable = sum(state.size or 0 for state in others)
        suggestions.append(
            CleanupSuggestion(
                kind="duplicate_content",
                title="不同路径内容完全相同",
                paths=tuple(state.path for state in group),
                reclaimable_bytes=reclaimable,
                action=(
                    f"确认这些文件用途相同后，保留 {keep.path}，"
                    f"删除其余 {len(others)} 份可回收磁盘"
                ),
                evidence={
                    "sha256": sha,
                    "files": len(group),
                    "size_each": keep.size,
                    "keep": keep.path,
                },
            )
        )

    # 4) 索引记录指向的文件已不在盘上。
    stale = sorted(
        (state for state in states.values() if not state.exists),
        key=lambda state: state.path,
    )
    if stale:
        suggestions.append(
            CleanupSuggestion(
                kind="stale_record",
                title="索引记录指向的文件已不存在",
                paths=tuple(state.path for state in stale),
                reclaimable_bytes=0,
                action=(
                    "这些文件已被外部删除；索引行可以裁剪，磁盘无可回收"
                    "（裁剪前请确认不是被移动到别处）"
                ),
                evidence={
                    "records": len(stale),
                    "statuses": sorted({state.status for state in stale}),
                },
            )
        )

    suggestions.sort(key=lambda item: (-item.reclaimable_bytes, item.kind, item.paths[:1]))
    return suggestions
