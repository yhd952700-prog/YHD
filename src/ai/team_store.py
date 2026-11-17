"""团队持久化（任务 6 / 7-3 Step 2）。

贾维斯底下一队的唯一事实来源：队员（5 个角色：队长 + 查资料/
整理/提醒/写代码，按需出现，没派过活时列表为空）、派给队员的
任务、队内消息记录。全部落 SQLite，重启后还在。

空白开局（Joseph 2026-11-14 ~14:33 定，前台不预摆）：全新库不
再播种 5 名固定队员；队员记录只在派活（create_task /
create_held_task）或雇佣（ensure_member）时按需建一条。已种下的
旧库不悄悄清——删数据是大事，要先问他。

队员是贾维斯的“手下”，不是 Joseph 雇佣的 AI Employee（那是
``EmployeeStore`` 里按名雇佣的另一套），也不人类组织的
``organization.py``——三套不要混用。

状态机（任务）：
    pending → running → done / failed / cancelled
    awaiting_approval → running（批了）/ rejected（拒了）/ cancelled（叫停）
失败计数按队员累计：连续失败 2 次标“已升级问 Joseph”（escalated=1），
成功一次清零。进度数字（第几步 / 共几步）只在这里算，前端只展示。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from src.common.data_paths import resolve_store_path

# 固定队形（7-3 §3）：队长 + 4 个固定角色，每角色默认 1 名队员。
TEAM_ROLES = ("manager", "researcher", "analyst", "reminder", "coder")

ROLE_DISPLAY_NAMES = {
    "manager": "队长·贾维斯",
    "researcher": "查资料员",
    "analyst": "整理员",
    "reminder": "提醒员",
    "coder": "写代码员",
}

TASK_PENDING = "pending"
TASK_RUNNING = "running"
TASK_DONE = "done"
TASK_FAILED = "failed"
TASK_CANCELLED = "cancelled"
# 7-3 Step 4：含危险动作的派活先挂起，等 Joseph 批了才跑。
TASK_AWAITING_APPROVAL = "awaiting_approval"
TASK_REJECTED = "rejected"
#: 队员执行回来的是“没配模型”的老实话：活根本没干，不能记 done。
#: 卡住的原因写进 error，队员回空闲，等 Joseph 配好模型再派。
TASK_NEEDS_MODEL = "needs_model"

TERMINAL_TASK_STATUSES = (
    TASK_DONE,
    TASK_FAILED,
    TASK_CANCELLED,
    TASK_REJECTED,
    TASK_NEEDS_MODEL,
)

# 连续失败几次升级问 Joseph（7-3 §6）。
ESCALATE_AFTER_FAILURES = 2

#: 本进程最早可能建任务的时间点（重启恢复的判定线，team_service 用它
#: 当 ``_PROCESS_STARTED_AT``）。
#:
#: 取**本模块**的导入时间，而不是 team_service 的导入时间：建任务必经
#: 本模块，所以本进程内新建的任务创建时间一定晚于它；team_service 是
#: 懒导入的，用它的导入时间当判定线会把“导入前刚建的本进程任务”误判
#: 成上个进程留下的中断任务——取消时先跑恢复扫荡把它标 failed，取消
#: 再去写时已是终态，只能原样返回 failed（test_list_tasks_filters_and_cancel
#: 的回归）。上个进程留下的任务创建时间一定早于本进程启动，也就早于
#: 本模块在该进程里的导入，所以这条线仍能区分新旧。
STORE_MODULE_IMPORTED_AT = time.time()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS team_members (
    role TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'idle',
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    escalated INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS team_tasks (
    task_id TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    title TEXT NOT NULL,
    plan_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    step_index INTEGER NOT NULL DEFAULT 0,
    step_total INTEGER NOT NULL DEFAULT 1,
    result_text TEXT NOT NULL DEFAULT '',
    artifacts_json TEXT NOT NULL DEFAULT '[]',
    sources_json TEXT NOT NULL DEFAULT '[]',
    error TEXT NOT NULL DEFAULT '',
    tokens_used INTEGER,
    tokens_estimated INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    started_at REAL,
    ended_at REAL,
    correlation_id TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_team_tasks_role_status
    ON team_tasks(role, status);
CREATE INDEX IF NOT EXISTS idx_team_tasks_created
    ON team_tasks(created_at DESC);
CREATE TABLE IF NOT EXISTS team_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL,
    sender TEXT NOT NULL,
    receiver TEXT NOT NULL,
    kind TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    correlation_id TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_messages_corr
    ON team_messages(correlation_id);
"""


def default_team_db() -> str:
    return resolve_store_path(
        "liuhao_team.db",
        Path(__file__).resolve().parents[2] / "liuhao_team.db",
        env_var="LIUHAO_TEAM_DB",
    )


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _row_to_member(row: sqlite3.Row) -> Dict[str, Any]:
    keys = set(row.keys())
    return {
        "role": row["role"],
        "agent_id": row["agent_id"],
        "display_name": row["display_name"],
        "status": row["status"],
        "consecutive_failures": row["consecutive_failures"],
        "escalated": bool(row["escalated"]),
        "updated_at": row["updated_at"],
        # P6：队员可配不同模型（存模型配置 id + 展示用 provider/model/base_url；
        # Key 永远只存在模型配置中心，队员表里不存 Key）。
        "model_config_id": row["model_config_id"] if "model_config_id" in keys else "",
        "model_provider": row["model_provider"] if "model_provider" in keys else "",
        "model_name": row["model_name"] if "model_name" in keys else "",
        "model_base_url": row["model_base_url"] if "model_base_url" in keys else "",
    }


def _row_to_task(row: sqlite3.Row) -> Dict[str, Any]:
    keys = set(row.keys())
    return {
        "task_id": row["task_id"],
        "role": row["role"],
        "agent_id": row["agent_id"],
        "title": row["title"],
        "plan_id": row["plan_id"],
        "status": row["status"],
        "step_index": row["step_index"],
        "step_total": row["step_total"],
        "result_text": row["result_text"],
        "artifacts": json.loads(row["artifacts_json"] or "[]"),
        "sources": json.loads(row["sources_json"] or "[]"),
        "error": row["error"],
        "tokens_used": row["tokens_used"],
        "tokens_estimated": bool(row["tokens_estimated"]),
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "correlation_id": row["correlation_id"],
        # 待批任务的恢复信息（队长 id / 队员 id / 超时 / 危险种类），批了才跑。
        "meta": json.loads(row["meta_json"] or "{}") if "meta_json" in keys else {},
    }


class TeamStore:
    """队伍、队员任务与队内消息的 SQLite 存取。"""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = str(db_path or default_team_db())
        directory = os.path.dirname(os.path.abspath(self._db_path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self._db_path, timeout=10.0, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=10000")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
            self._migrate()
            # 空白开局：全新库不再播种固定队员（旧 ensure_seed 已删）。
            # 队员记录只在派活/雇佣时由 ensure_member 按需建。

    def _migrate(self) -> None:
        """已建库的增量列（老库没有就补，不丢数据）。"""
        task_cols = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(team_tasks)")
        }
        if "meta_json" not in task_cols:
            self._conn.execute(
                "ALTER TABLE team_tasks ADD COLUMN meta_json TEXT NOT NULL DEFAULT '{}'"
            )
        member_cols = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(team_members)")
        }
        for column in (
            "model_config_id",
            "model_provider",
            "model_name",
            "model_base_url",
        ):
            if column not in member_cols:
                self._conn.execute(
                    f"ALTER TABLE team_members ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
                )
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None

    # ── 队员 ──

    def ensure_member(self, role: str, agent_id: Optional[str] = None) -> Dict[str, Any]:
        """按需建一条队员记录（已有就直接返回，不覆盖）。

        队员只在这里出现：派活（create_task / create_held_task）、雇佣
        （MultiAgentTeam 里 hire 出来的，绑到角色上）。全新库里没有
        队员是正常的——空白开局，不预摆。
        """
        if role not in TEAM_ROLES:
            raise ValueError(f"unknown team role: {role}")
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO team_members"
                " (role, agent_id, display_name, status, updated_at)"
                " VALUES (?, ?, ?, 'idle', ?)",
                (role, agent_id or f"team-{role}", ROLE_DISPLAY_NAMES[role], now),
            )
            if agent_id:
                self._conn.execute(
                    "UPDATE team_members SET agent_id = ?, updated_at = ? WHERE role = ?",
                    (agent_id, now, role),
                )
            self._conn.commit()
            return self.get_member(role)

    def get_member(self, role: str) -> Dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM team_members WHERE role = ?", (role,)
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown team member role: {role}")
            member = _row_to_member(row)
            self._attach_task_info(member)
        return member

    def _attach_task_info(self, member: Dict[str, Any]) -> None:
        """当前任务 + 最近结果（list_members 与 get_member 共用）。"""
        current = self._conn.execute(
            "SELECT * FROM team_tasks WHERE role = ?"
            " AND status IN ('pending', 'running')"
            " ORDER BY created_at DESC LIMIT 1",
            (member["role"],),
        ).fetchone()
        member["current_task"] = _row_to_task(current) if current else None
        latest = self._conn.execute(
            "SELECT * FROM team_tasks WHERE role = ? AND status = 'done'"
            " ORDER BY ended_at DESC LIMIT 1",
            (member["role"],),
        ).fetchone()
        member["latest_result"] = _row_to_task(latest) if latest else None

    def list_members(self) -> List[Dict[str, Any]]:
        """队员 + 每人当前任务（一句话 + 进度）+ 最近结果。

        当前任务 = 该角色最新一条未终态任务；没有则为 None，写“空闲”，
        不编一个假任务。最近结果 = 该角色最新一条已完成任务。
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM team_members ORDER BY"
                " CASE role WHEN 'manager' THEN 0 WHEN 'researcher' THEN 1"
                " WHEN 'analyst' THEN 2 WHEN 'reminder' THEN 3 ELSE 4 END"
            ).fetchall()
            members = [_row_to_member(row) for row in rows]
            for member in members:
                self._attach_task_info(member)
        return members

    # ── 任务 ──

    def create_task(
        self,
        *,
        role: str,
        title: str,
        plan_id: str = "",
        step_total: int = 1,
        correlation_id: str = "",
    ) -> Dict[str, Any]:
        if role not in TEAM_ROLES:
            raise ValueError(f"unknown team role: {role}")
        if not title.strip():
            raise ValueError("task title must not be empty")
        task_id = _new_id("team_task")
        now = time.time()
        # 派活时按需建队员记录：没派过活的角色在这里第一次出现。
        member = self.ensure_member(role)
        with self._lock:
            self._conn.execute(
                "INSERT INTO team_tasks"
                " (task_id, role, agent_id, title, plan_id, status,"
                "  step_index, step_total, created_at, started_at, correlation_id)"
                " VALUES (?, ?, ?, ?, ?, 'running', 0, ?, ?, ?, ?)",
                (
                    task_id,
                    role,
                    member["agent_id"],
                    title.strip(),
                    plan_id,
                    max(1, step_total),
                    now,
                    now,
                    correlation_id or task_id,
                ),
            )
            self._conn.execute(
                "UPDATE team_members SET status = 'running', updated_at = ? WHERE role = ?",
                (now, role),
            )
            self._conn.commit()
            return self.get_task(task_id)

    def create_held_task(
        self,
        *,
        role: str,
        title: str,
        plan_id: str = "",
        step_total: int = 1,
        correlation_id: str = "",
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """含危险动作的派活：只登记、不执行，等批了才跑（7-3 Step 4）。

        队员不标忙（还没开始干活），任务状态为 awaiting_approval。
        """
        if role not in TEAM_ROLES:
            raise ValueError(f"unknown team role: {role}")
        if not title.strip():
            raise ValueError("task title must not be empty")
        task_id = _new_id("team_task")
        now = time.time()
        # 待批派活同样按需建队员记录。
        member = self.ensure_member(role)
        with self._lock:
            self._conn.execute(
                "INSERT INTO team_tasks"
                " (task_id, role, agent_id, title, plan_id, status,"
                "  step_index, step_total, created_at, correlation_id, meta_json)"
                " VALUES (?, ?, ?, ?, ?, 'awaiting_approval', 0, ?, ?, ?, ?)",
                (
                    task_id,
                    role,
                    member["agent_id"],
                    title.strip(),
                    plan_id,
                    max(1, step_total),
                    now,
                    correlation_id or task_id,
                    json.dumps(meta or {}, ensure_ascii=False),
                ),
            )
            self._conn.commit()
            return self.get_task(task_id)

    def mark_running(self, task_id: str) -> Dict[str, Any]:
        """待批任务被批了：awaiting_approval → running，队员标忙。"""
        now = time.time()
        with self._lock:
            task = self.get_task(task_id)
            if task["status"] != TASK_AWAITING_APPROVAL:
                raise ValueError(
                    f"任务不在待批状态（现在是 {task['status']}），不能开始执行"
                )
            self._conn.execute(
                "UPDATE team_tasks SET status = 'running', started_at = ?"
                " WHERE task_id = ?",
                (now, task_id),
            )
            self._conn.execute(
                "UPDATE team_members SET status = 'running', updated_at = ? WHERE role = ?",
                (now, task["role"]),
            )
            self._conn.commit()
            return self.get_task(task_id)

    def reject_task(self, task_id: str, reason: str = "") -> Dict[str, Any]:
        """Joseph 拒绝：任务标 rejected，什么都没执行；队员回空闲。"""
        now = time.time()
        with self._lock:
            task = self.get_task(task_id)
            if task["status"] in TERMINAL_TASK_STATUSES:
                return task
            self._conn.execute(
                "UPDATE team_tasks SET status = 'rejected',"
                " error = ?, ended_at = ? WHERE task_id = ?",
                (reason or "Joseph 拒绝了", now, task_id),
            )
            self._conn.execute(
                "UPDATE team_members SET status = 'idle', updated_at = ? WHERE role = ?",
                (now, task["role"]),
            )
            self._conn.commit()
            return self.get_task(task_id)

    def mark_interrupted(self, task_id: str, reason: str) -> Dict[str, Any]:
        """重启恢复：上次网关退出前还在跑的任务标失败、写清中断原因。

        与 fail_task 的区别：不计入队员连续失败（中断不是队员的错），
        队员回空闲、不标 failed / 不升级。
        """
        now = time.time()
        with self._lock:
            task = self.get_task(task_id)
            if task["status"] in TERMINAL_TASK_STATUSES:
                return task
            self._conn.execute(
                "UPDATE team_tasks SET status = 'failed', error = ?, ended_at = ?"
                " WHERE task_id = ?",
                (reason, now, task_id),
            )
            self._conn.execute(
                "UPDATE team_members SET status = 'idle', updated_at = ? WHERE role = ?",
                (now, task["role"]),
            )
            self._conn.commit()
            return self.get_task(task_id)

    def set_member_model(
        self,
        role: str,
        *,
        model_config_id: str = "",
        model_provider: str = "",
        model_name: str = "",
        model_base_url: str = "",
    ) -> Dict[str, Any]:
        """P6：给一名队员配模型（只存模型配置 id 与展示信息，不存 Key）。

        严格要求队员已存在（派过活/雇过）：配模型不能成为偷偷种队员
        的口子；不存在时 get_member 抛 KeyError，网关层转 404。
        """
        if role not in TEAM_ROLES:
            raise ValueError(f"unknown team role: {role}")
        if role == "manager":
            raise ValueError("队长（贾维斯本体）用默认模型，不单独配")
        now = time.time()
        with self._lock:
            self.get_member(role)
            self._conn.execute(
                "UPDATE team_members SET model_config_id = ?, model_provider = ?,"
                " model_name = ?, model_base_url = ?, updated_at = ? WHERE role = ?",
                (
                    model_config_id,
                    model_provider,
                    model_name,
                    model_base_url,
                    now,
                    role,
                ),
            )
            self._conn.commit()
            return self.get_member(role)

    def get_task(self, task_id: str) -> Dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM team_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown team task: {task_id}")
        return _row_to_task(row)

    def list_tasks(
        self,
        *,
        role: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM team_tasks WHERE 1 = 1"
        params: List[Any] = []
        if role:
            query += " AND role = ?"
            params.append(role)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(200, limit)))
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [_row_to_task(row) for row in rows]

    def update_progress(self, task_id: str, step_index: int) -> Dict[str, Any]:
        """推进度：第几步 / 共几步（只写数字，不改状态）。"""
        with self._lock:
            task = self.get_task(task_id)
            if task["status"] in TERMINAL_TASK_STATUSES:
                return task
            clamped = max(0, min(step_index, task["step_total"]))
            self._conn.execute(
                "UPDATE team_tasks SET step_index = ? WHERE task_id = ?",
                (clamped, task_id),
            )
            self._conn.commit()
            return self.get_task(task_id)

    def complete_task(
        self,
        task_id: str,
        result_text: str = "",
        artifacts: Optional[Sequence[Dict[str, Any]]] = None,
        sources: Optional[Sequence[str]] = None,
        tokens_used: Optional[int] = None,
        tokens_estimated: bool = False,
    ) -> Dict[str, Any]:
        """完成：写结果 + 产物 + 来源 + 用量；队员失败计数清零。"""
        now = time.time()
        with self._lock:
            task = self.get_task(task_id)
            if task["status"] in TERMINAL_TASK_STATUSES:
                return task
            self._conn.execute(
                "UPDATE team_tasks SET status = 'done', result_text = ?,"
                " artifacts_json = ?, sources_json = ?,"
                " tokens_used = ?, tokens_estimated = ?, ended_at = ?,"
                " step_index = step_total WHERE task_id = ?",
                (
                    result_text,
                    json.dumps(list(artifacts or []), ensure_ascii=False),
                    json.dumps(list(sources or []), ensure_ascii=False),
                    tokens_used,
                    1 if tokens_estimated else 0,
                    now,
                    task_id,
                ),
            )
            self._conn.execute(
                "UPDATE team_members SET status = 'idle',"
                " consecutive_failures = 0, escalated = 0, updated_at = ?"
                " WHERE role = ?",
                (now, task["role"]),
            )
            self._conn.commit()
            return self.get_task(task_id)

    def block_task(
        self,
        task_id: str,
        error: str,
        result_text: str = "",
        artifacts: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """卡住了（不是失败、不是完成）：队员没法干活，等外部条件。

        用于“没配模型”：队员执行回来的是未配置提示，活根本没干，
        状态记 ``needs_model`` 而不是 ``done``，结果原文照样留着，
        让人一眼看出是卡住了、不是干完了。队员回空闲；这不算队员
        失败，不计连续失败次数、不升级问 Joseph（配模型是 Joseph
        在设置里做的事，不是队员的错）。
        """
        now = time.time()
        with self._lock:
            task = self.get_task(task_id)
            if task["status"] in TERMINAL_TASK_STATUSES:
                return task
            self._conn.execute(
                "UPDATE team_tasks SET status = 'needs_model', error = ?,"
                " result_text = ?, artifacts_json = ?, ended_at = ?"
                " WHERE task_id = ?",
                (
                    error or "卡住了，原因未说明",
                    result_text,
                    json.dumps(list(artifacts or []), ensure_ascii=False),
                    now,
                    task_id,
                ),
            )
            self._conn.execute(
                "UPDATE team_members SET status = 'idle', updated_at = ?"
                " WHERE role = ?",
                (now, task["role"]),
            )
            self._conn.commit()
            return self.get_task(task_id)

    def fail_task(self, task_id: str, error: str) -> Dict[str, Any]:
        """失败：写清真实原因；连续失败 2 次标“已升级问 Joseph”。"""
        now = time.time()
        with self._lock:
            task = self.get_task(task_id)
            if task["status"] in TERMINAL_TASK_STATUSES:
                return task
            self._conn.execute(
                "UPDATE team_tasks SET status = 'failed', error = ?, ended_at = ?"
                " WHERE task_id = ?",
                (error or "未说明原因", now, task_id),
            )
            # 任务存在则队员一定存在（建任务时已按需建）；ensure 只是兜底。
            member = self.ensure_member(task["role"])
            failures = member["consecutive_failures"] + 1
            self._conn.execute(
                "UPDATE team_members SET status = 'failed',"
                " consecutive_failures = ?, escalated = ?, updated_at = ?"
                " WHERE role = ?",
                (failures, 1 if failures >= ESCALATE_AFTER_FAILURES else 0, now, task["role"]),
            )
            self._conn.commit()
            return self.get_task(task_id)

    def cancel_task(self, task_id: str, reason: str = "") -> Dict[str, Any]:
        """叫停：记录标已取消；队员回空闲。正在跑的那一次调用结束后
        结果不再采用（见 collaboration.delegate_async 的终态守卫）。"""
        now = time.time()
        with self._lock:
            task = self.get_task(task_id)
            if task["status"] in TERMINAL_TASK_STATUSES:
                return task
            self._conn.execute(
                "UPDATE team_tasks SET status = 'cancelled',"
                " error = ?, ended_at = ? WHERE task_id = ?",
                (reason or "已叫停", now, task_id),
            )
            self._conn.execute(
                "UPDATE team_members SET status = 'idle', updated_at = ? WHERE role = ?",
                (now, task["role"]),
            )
            self._conn.commit()
            return self.get_task(task_id)

    # ── 队内消息 ──

    def record_message(
        self,
        *,
        message_id: str,
        sender: str,
        receiver: str,
        kind: str,
        summary: str = "",
        correlation_id: str = "",
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO team_messages"
                " (message_id, sender, receiver, kind, summary, correlation_id, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (message_id, sender, receiver, kind, summary, correlation_id, time.time()),
            )
            self._conn.commit()

    def list_messages(self, correlation_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM team_messages WHERE correlation_id = ?"
                " ORDER BY created_at ASC LIMIT ?",
                (correlation_id, max(1, min(200, limit))),
            ).fetchall()
        return [
            {
                "message_id": row["message_id"],
                "sender": row["sender"],
                "receiver": row["receiver"],
                "kind": row["kind"],
                "summary": row["summary"],
                "correlation_id": row["correlation_id"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    # ── 汇总 ──

    def team_summary(self) -> Dict[str, Any]:
        """团队汇总：谁没完成、卡在哪一步，前端只展示不自己算。"""
        members = self.list_members()
        failed = [
            {
                "role": member["role"],
                "display_name": member["display_name"],
                "consecutive_failures": member["consecutive_failures"],
                "escalated": member["escalated"],
            }
            for member in members
            if member["status"] == "failed"
        ]
        running = [
            {
                "role": member["role"],
                "display_name": member["display_name"],
                "title": (member["current_task"] or {}).get("title", ""),
                "step_index": (member["current_task"] or {}).get("step_index", 0),
                "step_total": (member["current_task"] or {}).get("step_total", 1),
            }
            for member in members
            if member["current_task"] is not None
        ]
        return {
            "total_members": len(members),
            "running_count": len(running),
            "failed_count": len(failed),
            "escalated_count": sum(1 for item in failed if item["escalated"]),
            "running": running,
            "failed": failed,
        }


_store: Optional[TeamStore] = None
_store_lock = threading.Lock()


def get_team_store() -> TeamStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = TeamStore()
        return _store


def reset_team_store() -> None:
    """测试用：丢掉单例（调用方负责删库文件或换 LIUHAO_TEAM_DB）。"""
    global _store
    with _store_lock:
        if _store is not None:
            try:
                _store.close()
            finally:
                _store = None
