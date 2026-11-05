"""产物索引（Artifact Index）—— 让「某次执行产出了哪些文件」可反查、可校验。

## 现状（基于真实代码，2026-11-06）

执行链里**已经**有三份与产物相关的记录，但没有一份能双向查询：

* **执行 journal**：``src/kernels/execution/_journal.py`` 的 ``journal_events``。
  ``task_completed`` 的 payload 只带一个 ``artifact_path``，由
  ``src/kernels/execution/__init__.py:936`` 的 ``artifact_path_from_output``
  从输出字典的 ``written`` / ``path`` / ``file`` 三个键里取**第一个**命中的值。
  一条任务写多个文件时，其余路径不入库。
* **目标历史**：``src/gateway/ai_management.py:1004`` 的 ``_collect_artifacts``
  把 ``capability_id == "file_write"`` 的已完成任务汇总成
  ``goal_entry["artifacts"]``（去重后的工作区相对路径字符串），随目标一起
  落 ``.liuhao_goals.json``。它丢掉了任务级归属，也没有哈希/版本。
* **文件面**：``src/gateway/files.py`` 只读浏览目录与文件内容，没有索引。

因此当前能回答「这次执行产出了什么吗？」只能靠反序列化目标 JSON 的一段
字符串列表；**不能**回答「这个文件是哪次执行写的」。同名覆盖之后更是无法
区分先后。审计链（``audit_store.db``）记录的是 principal/action/outcome，
不记录产物路径，也不能拿来当产物索引。

## 本模块定义了什么

一个 append-only 的**写入观测表**（不是文件系统镜像）：

* 主键 ``artifact_id`` = ``sha1(workspace + path_key + version)[:20]``，由
  ``(工作区, 工作区内相对路径, 第几次写入观测)`` 确定性生成。确定性是为了
  幂等：同一事件的重复投递会落到同一个主键上。
* ``version`` 是**按路径的写入序号**（第 1、2、3 次...），不是「第几种内容」。
  内容是否变过看 ``content_sha256``。这样做的理由：审计要的是「谁在第几次
  执行里写了这个路径」，内容相同也仍然是两次真实的写入。
* 每次写入都带执行归属：``execution_id`` / ``goal_id`` / ``task_id`` /
  ``task_name`` / ``correlation_id`` / ``journal_seq``。缺哪项就留 ``NULL``，
  绝不编造。

## 刻意做的取舍

1. **接线是显式可选的**。执行内核的 ``ExecutionEngine`` 增加了
   ``artifact_index=None`` 接缝：不传时行为与历史一致，传了才在任务完成后
   记一次写入观测。另有两条只读的「对账」入口
   (:meth:`ArtifactIndex.index_journal` / :meth:`ArtifactIndex.index_goal_file`)，用于补历史账。
2. **路径 fail-closed**。所有路径都过 ``src/ai/workspace.resolve_in_workspace``
   （realpath 消解 ``..`` 与符号链接）后必须落在工作区根之内；越界抛
   ``WorkspaceViolation``，绝不「看起来像相对路径就放行」。
3. **文件不在盘上也要记账**。历史产物可能已被删除；此时 ``content_sha256``
   为 ``NULL``，``verify()`` 会如实报告 ``missing``，而不是假装记录成功。
4. **不做文件系统镜像**。索引只回答「谁写的、写过几次、现在还在不在」；
   目录树仍由 ``src/gateway/files.py`` 负责。
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from src._time import utc_now
from src.ai.workspace import WorkspaceViolation, resolve_in_workspace

__all__ = [
    "ArtifactIndex",
    "ArtifactRecord",
    "ArtifactVerification",
    "WorkspaceViolation",
    "default_index_path",
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS artifact_writes (
    artifact_id      TEXT PRIMARY KEY,
    workspace        TEXT NOT NULL,
    path_key         TEXT NOT NULL,
    abs_path         TEXT NOT NULL,
    version          INTEGER NOT NULL,
    content_sha256   TEXT,
    size             INTEGER,
    execution_id     TEXT,
    goal_id          TEXT,
    correlation_id   TEXT,
    task_id          TEXT,
    task_name        TEXT,
    capability_id    TEXT,
    agent_id         TEXT,
    journal_seq      INTEGER,
    source           TEXT NOT NULL,
    recorded_at      TEXT NOT NULL,
    UNIQUE(workspace, path_key, version)
);
CREATE INDEX IF NOT EXISTS idx_artifact_exec
    ON artifact_writes(execution_id);
CREATE INDEX IF NOT EXISTS idx_artifact_goal
    ON artifact_writes(goal_id);
CREATE INDEX IF NOT EXISTS idx_artifact_task
    ON artifact_writes(task_id);
CREATE INDEX IF NOT EXISTS idx_artifact_path
    ON artifact_writes(workspace, path_key, version);
"""


def default_index_path() -> str:
    """未显式指定时的索引库位置。

    与执行 journal / 目标历史的落盘约定保持一致：
    ``LIUHAO_WORKSPACE_ROOT/liuhao_artifacts.db`` 优先（隔离验收可改向），
    否则 ``<repo_root>/.liuhao_artifacts.db``。
    """
    root = (os.environ.get("LIUHAO_WORKSPACE_ROOT") or "").strip()
    if root:
        return os.path.join(root, "liuhao_artifacts.db")
    repo_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    return os.path.join(repo_root, ".liuhao_artifacts.db")


@dataclass(frozen=True)
class ArtifactRecord:
    """一次「写入观测」。同一条路径写 N 次就有 N 行，``version`` 递增。"""

    artifact_id: str
    workspace: str
    path: str          # 工作区内相对路径，统一为正斜杠
    abs_path: str
    version: int
    content_sha256: Optional[str]
    size: Optional[int]
    execution_id: Optional[str]
    goal_id: Optional[str]
    correlation_id: Optional[str]
    task_id: Optional[str]
    task_name: Optional[str]
    capability_id: Optional[str]
    agent_id: Optional[str]
    journal_seq: Optional[int]
    source: str
    recorded_at: str


@dataclass(frozen=True)
class ArtifactVerification:
    """对一条历史记录的**当下**校验结果。

    ``status`` 取值：
      * ``present_unchanged`` —— 文件在，哈希与记录一致；
      * ``present_changed``   —— 文件在，但内容已被改过（不是这次执行写的了）；
      * ``missing``           —— 文件已不在盘上；
      * ``unverifiable``      —— 记录时文件就不在盘上（无哈希可比）。
    """

    artifact_id: str
    status: str
    expected_sha256: Optional[str]
    actual_sha256: Optional[str]
    exists: bool


def _normalize_workspace(workspace: str) -> str:
    """把工作区根解析为真实绝对路径；不存在则拒绝（不猜一个目录）。"""
    if not isinstance(workspace, str) or not workspace.strip():
        raise ValueError("workspace 不能为空")
    real = os.path.realpath(os.path.abspath(workspace))
    if not os.path.isdir(real):
        raise ValueError(f"workspace 不是已存在的目录：{workspace!r}")
    return real


def _path_key(workspace: str, path: str) -> Tuple[str, str]:
    """返回 ``(相对路径, 真实绝对路径)``；越界或指向目录即抛错。"""
    real = resolve_in_workspace(path, workspace)
    rel = os.path.relpath(real, workspace).replace(os.sep, "/")
    if rel in ("", ".", "..") or rel.startswith("../"):
        # 根目录本身不是产物；跨根相对路径理论上已被 resolve 拦掉，这里兜底。
        raise ValueError(f"不是工作区内的文件路径：{path!r}")
    if os.path.isdir(real):
        raise ValueError(f"目录不能作为产物：{path!r}")
    return rel, real


def _hash_file(abs_path: str) -> Tuple[Optional[str], Optional[int]]:
    """内容哈希与大小；文件不存在（或读不了）时返回 ``(None, None)``。"""
    try:
        with open(abs_path, "rb") as handle:
            data = handle.read()
    except OSError:
        return None, None
    return hashlib.sha256(data).hexdigest(), len(data)


def _artifact_id(workspace: str, path_key: str, version: int) -> str:
    raw = f"{workspace}\x00{path_key}\x00{version}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:20]


def _row_to_record(row: sqlite3.Row) -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id=row["artifact_id"],
        workspace=row["workspace"],
        path=row["path_key"],
        abs_path=row["abs_path"],
        version=int(row["version"]),
        content_sha256=row["content_sha256"],
        size=row["size"],
        execution_id=row["execution_id"],
        goal_id=row["goal_id"],
        correlation_id=row["correlation_id"],
        task_id=row["task_id"],
        task_name=row["task_name"],
        capability_id=row["capability_id"],
        agent_id=row["agent_id"],
        journal_seq=row["journal_seq"],
        source=row["source"],
        recorded_at=row["recorded_at"],
    )


class ArtifactIndex:
    """SQLite 支撑的产物写入观测索引。线程安全（单连接 + RLock）。"""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        directory = os.path.dirname(os.path.abspath(db_path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        # 与 execution journal 同一套取舍：跨线程共享连接，所以关掉同线程校验；
        # 并且必须先 connect 再设 WAL（Windows 上顺序反了会报错）。
        self._conn = sqlite3.connect(db_path, timeout=10.0, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ #
    # 写入
    # ------------------------------------------------------------------ #
    def record(
        self,
        *,
        workspace: str,
        path: str,
        execution_id: Optional[str] = None,
        goal_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        task_id: Optional[str] = None,
        task_name: Optional[str] = None,
        capability_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        journal_seq: Optional[int] = None,
        source: str = "direct",
    ) -> ArtifactRecord:
        """记录一次写入观测并返回落库后的记录。

        幂等规则：若上一条同路径记录的 ``content_sha256`` 与本次相同，且
        ``execution_id`` / ``goal_id`` / ``task_id`` 三者完全相同，则视为同一次
        写入的重复投递（重试/续跑），直接返回已有记录，不新增版本。

        Raises:
            ValueError: workspace/path 非法。
            WorkspaceViolation: 路径越界（fail-closed）。
        """
        ws = _normalize_workspace(workspace)
        key, abs_path = _path_key(ws, path)
        sha, size = _hash_file(abs_path)
        recorded_at = utc_now().isoformat()

        with self._lock:
            last = self._conn.execute(
                "SELECT * FROM artifact_writes WHERE workspace = ? AND path_key = ?"
                " ORDER BY version DESC LIMIT 1",
                (ws, key),
            ).fetchone()

            if (
                last is not None
                and sha is not None
                and last["content_sha256"] == sha
                and last["execution_id"] == execution_id
                and last["goal_id"] == goal_id
                and last["task_id"] == task_id
            ):
                return _row_to_record(last)

            version = int(last["version"]) + 1 if last is not None else 1
            artifact_id = _artifact_id(ws, key, version)
            self._conn.execute(
                "INSERT OR IGNORE INTO artifact_writes ("
                " artifact_id, workspace, path_key, abs_path, version,"
                " content_sha256, size, execution_id, goal_id, correlation_id,"
                " task_id, task_name, capability_id, agent_id, journal_seq,"
                " source, recorded_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    artifact_id, ws, key, abs_path, version,
                    sha, size, execution_id, goal_id, correlation_id,
                    task_id, task_name, capability_id, agent_id, journal_seq,
                    source, recorded_at,
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM artifact_writes WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
        return _row_to_record(row)

    def record_from_task_output(
        self,
        *,
        workspace: str,
        output: Any,
        execution_id: Optional[str] = None,
        goal_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        task_id: Optional[str] = None,
        task_name: Optional[str] = None,
        capability_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        journal_seq: Optional[int] = None,
        source: str = "kernel",
    ) -> Optional[ArtifactRecord]:
        """从一次任务输出里提取产物路径并记录；没有声明产物时返回 ``None``。

        「什么算产物」沿用执行内核的 canonical 判定
        （``src.kernels.execution.artifact_path_from_output``），本模块不另立标准。
        输出里的 ``workspace_root`` 优先于传入的 ``workspace``——工具自己报告的
        根才真正约束了那次写入。
        """
        from src.kernels.execution import artifact_path_from_output

        claimed = artifact_path_from_output(output)
        if not claimed:
            return None
        root = workspace
        if isinstance(output, dict):
            reported = output.get("workspace_root")
            if isinstance(reported, str) and reported.strip():
                root = reported
        return self.record(
            workspace=root,
            path=claimed,
            execution_id=execution_id,
            goal_id=goal_id,
            correlation_id=correlation_id,
            task_id=task_id,
            task_name=task_name,
            capability_id=capability_id,
            agent_id=agent_id,
            journal_seq=journal_seq,
            source=source,
        )

    # ------------------------------------------------------------------ #
    # 对账（只读事件源，不修改执行内核）
    # ------------------------------------------------------------------ #
    def index_journal(
        self,
        journal: Any,
        *,
        workspace_root: Optional[str] = None,
        source: str = "journal",
    ) -> Dict[str, int]:
        """把一份 ``ExecutionJournal`` 里已声明产物的完成事件灌进索引。

        只读：调用 journal 的公开读接口 (``executions()`` / ``events()``)，
        不写回 journal。返回 ``{"indexed": n, "skipped": m}``——``skipped`` 是
        路径越界或不再属于当前工作区的事件数（不是失败，是拒绝记账）。
        """
        ws = _normalize_workspace(workspace_root or _default_workspace_root())
        indexed = 0
        skipped = 0
        for execution_id in journal.executions():
            for event in journal.events(execution_id):
                if event.event_type != "task_completed":
                    continue
                payload = event.payload if isinstance(event.payload, dict) else {}
                claimed = payload.get("artifact_path")
                if not isinstance(claimed, str) or not claimed:
                    continue
                try:
                    self.record(
                        workspace=ws,
                        path=claimed,
                        execution_id=execution_id,
                        goal_id=payload.get("goal_id"),
                        task_id=payload.get("task_id"),
                        task_name=event.task_name,
                        journal_seq=event.seq,
                        source=source,
                    )
                except (WorkspaceViolation, ValueError):
                    skipped += 1
                    continue
                indexed += 1
        return {"indexed": indexed, "skipped": skipped}

    def index_goal_file(
        self,
        goal_json_path: str,
        *,
        workspace_root: Optional[str] = None,
        source: str = "goal_json",
    ) -> Dict[str, int]:
        """把 ``.liuhao_goals.json`` 里每条目标的 ``artifacts`` 灌进索引。

        这份 JSON 的顶层是 ``{goal_id: entry}``（见
        ``src/gateway/ai_management.py:325`` 的 ``_save_goals``）。goal 级产物
        只有相对路径、没有任务归属，因此这里写入的记录 ``task_id`` 为 ``NULL``；
        需要任务级归属时以 journal 对账为准。
        """
        with open(goal_json_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            return {"indexed": 0, "skipped": 0}

        ws = _normalize_workspace(workspace_root or _default_workspace_root())
        indexed = 0
        skipped = 0
        for goal_id, entry in data.items():
            if not isinstance(entry, dict):
                continue
            artifacts = entry.get("artifacts")
            if not isinstance(artifacts, list):
                continue
            for rel in artifacts:
                if not isinstance(rel, str) or not rel.strip():
                    continue
                try:
                    self.record(
                        workspace=ws,
                        path=rel,
                        goal_id=goal_id,
                        correlation_id=entry.get("correlation_id"),
                        source=source,
                    )
                except (WorkspaceViolation, ValueError):
                    skipped += 1
                    continue
                indexed += 1
        return {"indexed": indexed, "skipped": skipped}

    # ------------------------------------------------------------------ #
    # 反查
    # ------------------------------------------------------------------ #
    def artifacts_for_execution(
        self, execution_id: str, *, latest_only: bool = True
    ) -> List[ArtifactRecord]:
        """给定一次执行，它产出了哪些文件（默认每条路径只取最新一次写入）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM artifact_writes WHERE execution_id = ?"
                " ORDER BY version DESC",
                (execution_id,),
            ).fetchall()
        records = [_row_to_record(r) for r in rows]
        if not latest_only:
            return records
        seen: set[str] = set()
        out: List[ArtifactRecord] = []
        for rec in records:
            if rec.path in seen:
                continue
            seen.add(rec.path)
            out.append(rec)
        return out

    def paths_for_execution(self, execution_id: str) -> List[str]:
        """只要路径列表（便于和 ``goal_entry["artifacts"]`` 直接对比）。"""
        return [rec.path for rec in self.artifacts_for_execution(execution_id)]

    def artifacts_for_goal(self, goal_id: str, *, latest_only: bool = True) -> List[ArtifactRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM artifact_writes WHERE goal_id = ? ORDER BY version DESC",
                (goal_id,),
            ).fetchall()
        records = [_row_to_record(r) for r in rows]
        if not latest_only:
            return records
        seen: set[str] = set()
        out: List[ArtifactRecord] = []
        for rec in records:
            if rec.path in seen:
                continue
            seen.add(rec.path)
            out.append(rec)
        return out

    def artifacts_for_task(self, task_id: str) -> List[ArtifactRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM artifact_writes WHERE task_id = ? ORDER BY version",
                (task_id,),
            ).fetchall()
        return [_row_to_record(r) for r in rows]

    def history_for_file(self, path: str, *, workspace: str) -> List[ArtifactRecord]:
        """给定一个文件，它的完整写入历史（新 → 旧）。"""
        ws = _normalize_workspace(workspace)
        key, _ = _path_key(ws, path)
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM artifact_writes WHERE workspace = ? AND path_key = ?"
                " ORDER BY version DESC",
                (ws, key),
            ).fetchall()
        return [_row_to_record(r) for r in rows]

    def executions_for_file(self, path: str, *, workspace: str) -> List[Dict[str, Any]]:
        """给定一个文件，哪些执行写过它（按最近一次写入倒序）。

        返回的是**执行摘要**而不是原始行：同一执行多次写同一路径只出现一次。
        ``execution_id`` 为 ``NULL`` 的记录（goal_json 对账来的）会被跳过——
        它们没有可归因的执行。
        """
        ws = _normalize_workspace(workspace)
        key, _ = _path_key(ws, path)
        with self._lock:
            rows = self._conn.execute(
                "SELECT execution_id, goal_id, correlation_id,"
                "       MIN(recorded_at) AS first_seen,"
                "       MAX(recorded_at) AS last_seen,"
                "       MAX(version) AS max_version,"
                "       GROUP_CONCAT(DISTINCT task_name) AS task_names"
                " FROM artifact_writes"
                " WHERE workspace = ? AND path_key = ? AND execution_id IS NOT NULL"
                " GROUP BY execution_id, goal_id, correlation_id"
                " ORDER BY max_version DESC",
                (ws, key),
            ).fetchall()
        out: List[Dict[str, Any]] = []
        for row in rows:
            out.append(
                {
                    "execution_id": row["execution_id"],
                    "goal_id": row["goal_id"],
                    "correlation_id": row["correlation_id"],
                    "first_seen": row["first_seen"],
                    "last_seen": row["last_seen"],
                    "max_version": row["max_version"],
                    "task_names": (
                        row["task_names"].split(",") if row["task_names"] else []
                    ),
                }
            )
        return out

    def latest_for_file(self, path: str, *, workspace: str) -> Optional[ArtifactRecord]:
        history = self.history_for_file(path, workspace=workspace)
        return history[0] if history else None

    # ------------------------------------------------------------------ #
    # 校验
    # ------------------------------------------------------------------ #
    def verify(self, artifact_id: str) -> ArtifactVerification:
        """检查一条历史记录里的文件**现在**还在不在、内容是否仍是那一版。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM artifact_writes WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"未知 artifact_id：{artifact_id!r}")
        expected = row["content_sha256"]
        actual, _ = _hash_file(row["abs_path"])
        exists = os.path.exists(row["abs_path"])
        if not exists:
            status = "missing"
        elif expected is None:
            status = "unverifiable"
        elif actual == expected:
            status = "present_unchanged"
        else:
            status = "present_changed"
        return ArtifactVerification(
            artifact_id=artifact_id,
            status=status,
            expected_sha256=expected,
            actual_sha256=actual,
            exists=exists,
        )

    # ------------------------------------------------------------------ #
    # 杂项
    # ------------------------------------------------------------------ #
    def stats(self) -> Dict[str, int]:
        with self._lock:
            total = self._conn.execute(
                "SELECT COUNT(*) FROM artifact_writes"
            ).fetchone()[0]
            paths = self._conn.execute(
                "SELECT COUNT(*) FROM (SELECT DISTINCT workspace, path_key"
                " FROM artifact_writes)"
            ).fetchone()[0]
            executions = self._conn.execute(
                "SELECT COUNT(DISTINCT execution_id) FROM artifact_writes"
                " WHERE execution_id IS NOT NULL"
            ).fetchone()[0]
        return {"writes": int(total), "paths": int(paths), "executions": int(executions)}

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "ArtifactIndex":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()


def _default_workspace_root() -> str:
    """live 工作区根；只在对账入口未显式给根时用。"""
    from src.ai.workspace import workspace_root

    return workspace_root()
