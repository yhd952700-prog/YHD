"""组织架构数据面（企业工作台 P3A）—— 任意层级组织 / 岗位 / 任职 / 版本 / 影响检查。

为什么要有这个模块
------------------
盘点（`enterprise-workbench-audit/01-能力盘点矩阵.md` §1.4）确认：产品代码里
没有部门/岗位实体，`src/ai/organization.py` 是未接线的固定角色枚举库，不能直接
复用。本模块补齐的正是**数据层**：用户能自建任意层级组织，给节点指定负责人，
把 AI 员工挂到岗位上并设汇报关系，调整留版本、可查历史、可回滚，调整前能
检出"有人失去负责人 / 任务无人接管 / 组织内任职失效"。

存储
----
* 独立 SQLite 库 ``org_store.db``（``resolve_store_path(..., workspace=True)``，
  可被 ``LIUHAO_ORG_DB`` 覆盖），与 ``account_usage.py`` 同构：模块内
  ``org_meta.schema_version`` + 幂等迁移，**不新建第二套迁移框架**。
* 四张表：``org_nodes`` / ``org_assignments`` / ``org_versions`` / ``org_meta``。
* 写入走 ``BEGIN IMMEDIATE`` + 进程内可重入锁；版本快照在同一事务内落盘，
  append-only，回滚只新增版本、不改历史。

诚实边界
--------
* 组织数据**当前不参与 RBAC 判定**。影响检查里的 ``revoked_authority`` 只表示
  组织内的"任职/负责人记录本身失效"，文案不写"权限已被收回"。
* ``unattended_tasks`` 只能看到执行内核当前热集合里的未终结目标；读不到任务库时
  返回 ``checked=false``（未检查），绝不把"查不到"说成"无风险"。
* 不预设任何部门/岗位：空库初始化后节点数为 0，模板不落库。

路由与鉴权
----------
沿用仓库现有方式：``main.py`` 用 ``require_human_principal`` 挂闸门，本模块内
按令牌主体算账号归属 —— 读走 ``GoalViewer.sees``（主账号可看子树），写只作用于
自己的账号；跨账号一律中性 404。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Sequence, Set, Tuple

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from ..common.data_paths import resolve_store_path
from ..common.input_guard import normalize_required_name, reject_unsafe_text
from .account_context import deny
from .policy import require_bearer_payload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/org", tags=["org"])

#: 节点类型。**任意层级**：不强制 kind 与深度的对应关系（企业可以挂在部门下）。
ORG_KINDS: Tuple[str, ...] = (
    "company",          # 企业
    "business_unit",    # 事业部
    "branch",           # 分公司
    "department",       # 部门
    "team",             # 小组
    "position",         # 岗位
)
SUBJECT_TYPES: Tuple[str, ...] = ("employee", "human")

MAX_NAME = 200
MAX_DESCRIPTION = 2000
MAX_LABEL = 200
MAX_TITLE = 120
MAX_IMPACT_ITEMS = 100
MAX_DEPTH = 64

_ENV_DB = "LIUHAO_ORG_DB"
SCHEMA_VERSION = 2

# ── 表结构与迁移 ──────────────────────────────────────────────

_SCHEMA_V1: Tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS org_meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS org_nodes (
        node_id     TEXT PRIMARY KEY,
        account_id  TEXT NOT NULL,
        parent_id   TEXT NOT NULL DEFAULT '',
        kind        TEXT NOT NULL,
        name        TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        sort_order  INTEGER NOT NULL DEFAULT 0,
        created_at  REAL NOT NULL,
        updated_at  REAL NOT NULL,
        created_by  TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS org_assignments (
        assignment_id          TEXT PRIMARY KEY,
        account_id             TEXT NOT NULL,
        node_id                TEXT NOT NULL,
        subject_type           TEXT NOT NULL,
        subject_id             TEXT NOT NULL,
        subject_label          TEXT NOT NULL DEFAULT '',
        title                  TEXT NOT NULL DEFAULT '',
        is_lead                INTEGER NOT NULL DEFAULT 0,
        is_primary             INTEGER NOT NULL DEFAULT 0,
        reports_to_assignment_id TEXT NOT NULL DEFAULT '',
        created_at             REAL NOT NULL,
        updated_at             REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS org_versions (
        version_id     TEXT PRIMARY KEY,
        account_id     TEXT NOT NULL,
        seq            INTEGER NOT NULL,
        created_at     REAL NOT NULL,
        actor          TEXT NOT NULL DEFAULT '',
        action         TEXT NOT NULL,
        target_node_id TEXT NOT NULL DEFAULT '',
        summary_json   TEXT NOT NULL DEFAULT '{}',
        snapshot_json  TEXT NOT NULL DEFAULT '{}'
    )
    """,
    # 同级重名唯一：同一父节点下、大小写不敏感；不同父节点可以重名。
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_org_nodes_sibling_name
        ON org_nodes(account_id, parent_id, name COLLATE NOCASE)
    """,
    "CREATE INDEX IF NOT EXISTS ix_org_nodes_parent ON org_nodes(account_id, parent_id)",
    "CREATE INDEX IF NOT EXISTS ix_org_nodes_account ON org_nodes(account_id)",
    # 同一节点同一主体只允许一条任职记录。
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_org_assignments_subject
        ON org_assignments(account_id, node_id, subject_type, subject_id)
    """,
    # 一个节点最多一个负责人（部分唯一索引）。
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_org_assignments_lead
        ON org_assignments(account_id, node_id) WHERE is_lead = 1
    """,
    "CREATE INDEX IF NOT EXISTS ix_org_assignments_node ON org_assignments(account_id, node_id)",
    """
    CREATE INDEX IF NOT EXISTS ix_org_assignments_subject
        ON org_assignments(account_id, subject_type, subject_id)
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_org_versions_seq ON org_versions(account_id, seq)",
    "CREATE INDEX IF NOT EXISTS ix_org_versions_account ON org_versions(account_id, created_at)",
)

#: v2：组织模板。模板是数据不是代码：一套模板 = 一棵节点树 + 岗位 + 汇报边。
#: 内置模板随包分发、只读；``org_template_prefs`` 只记录账号级的隐藏偏好。
_SCHEMA_V2: Tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS org_templates (
        template_id    TEXT PRIMARY KEY,
        account_id     TEXT NOT NULL,
        name           TEXT NOT NULL,
        description    TEXT NOT NULL DEFAULT '',
        industry       TEXT NOT NULL DEFAULT '',
        schema_version INTEGER NOT NULL DEFAULT 1,
        revision       INTEGER NOT NULL DEFAULT 1,
        payload_json   TEXT NOT NULL,
        created_at     REAL NOT NULL,
        updated_at     REAL NOT NULL,
        created_by     TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_org_templates_name
        ON org_templates(account_id, name COLLATE NOCASE)
    """,
    "CREATE INDEX IF NOT EXISTS ix_org_templates_account ON org_templates(account_id)",
    """
    CREATE TABLE IF NOT EXISTS org_template_prefs (
        account_id   TEXT NOT NULL,
        template_key TEXT NOT NULL,
        hidden       INTEGER NOT NULL DEFAULT 0,
        sort_order   INTEGER,
        updated_at   REAL NOT NULL,
        PRIMARY KEY (account_id, template_key)
    )
    """,
)

#: 版本号 -> 迁移语句。后续版本只**追加**，不改历史条目。
_MIGRATIONS: Dict[int, Tuple[str, ...]] = {1: _SCHEMA_V1, 2: _SCHEMA_V2}

#: 快照字段白名单（P3A 要求：快照由 schema 驱动序列化，漏字段会立刻被单测抓到）。
SNAPSHOT_NODE_FIELDS: Tuple[str, ...] = (
    "node_id",
    "parent_id",
    "kind",
    "name",
    "description",
    "sort_order",
    "created_at",
    "updated_at",
    "created_by",
)
SNAPSHOT_ASSIGNMENT_FIELDS: Tuple[str, ...] = (
    "assignment_id",
    "node_id",
    "subject_type",
    "subject_id",
    "subject_label",
    "title",
    "is_lead",
    "is_primary",
    "reports_to_assignment_id",
    "created_at",
    "updated_at",
)

_WRITE_LOCK = threading.RLock()


def db_path() -> str:
    """组织库落盘路径：``LIUHAO_ORG_DB`` > ``LIUHAO_WORKSPACE_ROOT`` > ``LIUHAO_DATA_DIR`` > data/。"""
    return resolve_store_path(
        "org_store.db",
        os.path.join("data", "org_store.db"),
        env_var=_ENV_DB,
        workspace=True,
    )


def _ensure_schema(connection: sqlite3.Connection) -> None:
    """幂等迁移：按 ``org_meta.schema_version`` 只跑尚未应用的版本。"""
    connection.execute(
        "CREATE TABLE IF NOT EXISTS org_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    row = connection.execute(
        "SELECT value FROM org_meta WHERE key = 'schema_version'"
    ).fetchone()
    current = 0
    if row is not None:
        try:
            current = int(row["value"])
        except (TypeError, ValueError):
            current = 0
    for version in sorted(_MIGRATIONS):
        if version <= current:
            continue
        for statement in _MIGRATIONS[version]:
            connection.execute(statement)
        connection.execute(
            "INSERT OR REPLACE INTO org_meta(key, value) VALUES('schema_version', ?)",
            (str(version),),
        )


def _connect() -> sqlite3.Connection:
    path = db_path()
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10, isolation_level=None)
    connection.row_factory = sqlite3.Row
    _ensure_schema(connection)
    return connection


@contextmanager
def _tx() -> Iterator[sqlite3.Connection]:
    """写事务：进程内锁 + ``BEGIN IMMEDIATE``（跨进程串行化）。"""
    with _WRITE_LOCK:
        connection = _connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.execute("COMMIT")
        except BaseException:
            try:
                connection.execute("ROLLBACK")
            except Exception:  # noqa: BLE001 - 回滚失败不掩盖原始异常
                pass
            raise
        finally:
            connection.close()


def _read(fn):
    connection = _connect()
    try:
        return fn(connection)
    finally:
        connection.close()


# ── 行 -> 结构（字段白名单） ──────────────────────────────────


def _node_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "node_id": row["node_id"],
        "account_id": row["account_id"],
        "parent_id": row["parent_id"] or None,
        "kind": row["kind"],
        "name": row["name"],
        "description": row["description"],
        "sort_order": int(row["sort_order"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "created_by": row["created_by"],
    }


def _assignment_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "assignment_id": row["assignment_id"],
        "account_id": row["account_id"],
        "node_id": row["node_id"],
        "subject_type": row["subject_type"],
        "subject_id": row["subject_id"],
        "subject_label": row["subject_label"],
        "title": row["title"],
        "is_lead": bool(row["is_lead"]),
        "is_primary": bool(row["is_primary"]),
        "reports_to_assignment_id": row["reports_to_assignment_id"] or None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _load_nodes(connection: sqlite3.Connection, account_id: str) -> List[Dict[str, Any]]:
    rows = connection.execute(
        "SELECT * FROM org_nodes WHERE account_id = ? ORDER BY sort_order, name, node_id",
        (account_id,),
    ).fetchall()
    return [_node_dict(row) for row in rows]


def _load_assignments(
    connection: sqlite3.Connection, account_id: str
) -> List[Dict[str, Any]]:
    rows = connection.execute(
        "SELECT * FROM org_assignments WHERE account_id = ? "
        "ORDER BY is_lead DESC, is_primary DESC, created_at, assignment_id",
        (account_id,),
    ).fetchall()
    return [_assignment_dict(row) for row in rows]


def _snapshot(connection: sqlite3.Connection, account_id: str) -> Dict[str, Any]:
    nodes = [
        {field: node[field] for field in SNAPSHOT_NODE_FIELDS}
        for node in _load_nodes(connection, account_id)
    ]
    assignments = [
        {field: item[field] for field in SNAPSHOT_ASSIGNMENT_FIELDS}
        for item in _load_assignments(connection, account_id)
    ]
    return {"nodes": nodes, "assignments": assignments}


def _record_version(
    connection: sqlite3.Connection,
    account_id: str,
    *,
    actor: str,
    action: str,
    target_node_id: str = "",
    summary: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """写一版快照（append-only）。必须在写事务内调用。"""
    row = connection.execute(
        "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM org_versions WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    seq = int(row["next_seq"])
    version_id = uuid.uuid4().hex
    created_at = time.time()
    snapshot = _snapshot(connection, account_id)
    payload = summary or {}
    connection.execute(
        "INSERT INTO org_versions "
        "(version_id, account_id, seq, created_at, actor, action, target_node_id, "
        " summary_json, snapshot_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            version_id,
            account_id,
            seq,
            created_at,
            actor or "",
            action,
            target_node_id or "",
            json.dumps(payload, ensure_ascii=False),
            json.dumps(snapshot, ensure_ascii=False),
        ),
    )
    return {
        "version_id": version_id,
        "seq": seq,
        "created_at": created_at,
        "actor": actor or "",
        "action": action,
        "target_node_id": target_node_id or None,
        "summary": payload,
    }


def _audit(action: str, account_id: str, actor: str, target: str, details: Dict[str, Any]) -> bool:
    """写审计事件；失败只记日志并如实返回 False（不阻断业务）。"""
    try:
        from ..kernels.audit import AuditEventType, AuditScope, log_event

        payload = {"action": action, "target": target or "", "account": account_id}
        payload.update(details or {})
        log_event(
            event_type=AuditEventType.STATE_CHANGE,
            principal_id=actor or account_id,
            scope=AuditScope.L0,
            outcome="allow",
            details=payload,
        )
        return True
    except Exception as exc:  # noqa: BLE001 - 审计失败不能把成功写操作变成 500
        logger.warning("org audit failed (%s): %s", action, exc)
        return False


# ── 账号视角 ──────────────────────────────────────────────────


def _org_viewer(
    x_liuhao_token: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    """FastAPI 依赖：已核验令牌 -> 账号视角（复用 ai_management 的 GoalViewer）。"""
    from .ai_management import viewer_from_payload

    return viewer_from_payload(
        require_bearer_payload(x_liuhao_token, authorization)
    )


def _neutral(message: str = "资源不存在或无权访问") -> HTTPException:
    return deny(404, "ORG_NODE_NOT_FOUND", message)


def _read_account(viewer: Any, view_account: Optional[str]) -> str:
    """读视角：默认自己；主账号可显式看子树账号，越权一律中性 404。"""
    target = (view_account or "").strip() or viewer.principal
    if target == viewer.principal:
        return target
    if viewer.sees({"owner_account_id": target}):
        return target
    raise _neutral()


def _node_row(
    connection: sqlite3.Connection, account_id: str, node_id: str
) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM org_nodes WHERE account_id = ? AND node_id = ?",
        (account_id, node_id),
    ).fetchone()
    if row is None:
        raise _neutral()
    return row


def _assignment_row(
    connection: sqlite3.Connection, account_id: str, assignment_id: str
) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM org_assignments WHERE account_id = ? AND assignment_id = ?",
        (account_id, assignment_id),
    ).fetchone()
    if row is None:
        raise deny(404, "ORG_ASSIGNMENT_NOT_FOUND", "资源不存在或无权访问")
    return row


def _children_map(nodes: Sequence[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    children: Dict[str, List[Dict[str, Any]]] = {}
    for node in nodes:
        children.setdefault(node["parent_id"] or "", []).append(node)
    for bucket in children.values():
        bucket.sort(key=lambda item: (item["sort_order"], item["name"].casefold(), item["node_id"]))
    return children


def _subtree_ids(
    connection: sqlite3.Connection, account_id: str, node_id: str
) -> List[str]:
    """返回包含自身的子树节点 id（广度优先，防御异常数据造成的环）。"""
    nodes = _load_nodes(connection, account_id)
    children = _children_map(nodes)
    ordered: List[str] = []
    seen: Set[str] = set()
    queue = [node_id]
    while queue:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        ordered.append(current)
        queue.extend(child["node_id"] for child in children.get(current, []))
    return ordered


def _ancestor_ids(
    connection: sqlite3.Connection, account_id: str, node_id: str
) -> List[str]:
    by_id = {node["node_id"]: node for node in _load_nodes(connection, account_id)}
    chain: List[str] = []
    cursor = by_id.get(node_id, {}).get("parent_id") or ""
    depth = 0
    while cursor and depth < MAX_DEPTH:
        chain.append(cursor)
        cursor = by_id.get(cursor, {}).get("parent_id") or ""
        depth += 1
    return chain


def _would_cycle(connection: sqlite3.Connection, account_id: str, node_id: str, parent_id: str) -> bool:
    """把 node 挂到 parent 下是否会成环（parent 是自身或其后代即成环）。"""
    if not parent_id:
        return False
    if parent_id == node_id:
        return True
    return parent_id in _subtree_ids(connection, account_id, node_id)


# ── 主体校验 ──────────────────────────────────────────────────


def _clean_subject(subject_type: Any, subject_id: Any) -> Tuple[str, str, str]:
    """校验成员主体：employee 必须真实存在于员工库；human 必须存在于身份内核。"""
    kind = str(subject_type or "").strip().lower()
    if kind not in SUBJECT_TYPES:
        raise deny(422, "ORG_SUBJECT_TYPE_INVALID", "成员类型只能是 employee（AI 员工）或 human（账号）。")
    sid = str(subject_id or "").strip()
    if not sid:
        raise deny(422, "ORG_SUBJECT_REQUIRED", "成员不能为空。")
    label = sid
    if kind == "employee":
        try:
            from src.ai.employee_store import EmployeeStore

            store = EmployeeStore()
            names = set(store.list_employees())
            summaries = {str(item.get("name")): item for item in store.list_summaries()}
        except Exception as exc:  # noqa: BLE001 - 读不到就不猜
            logger.warning("org: employee store unavailable: %s", exc)
            raise deny(
                503,
                "ORG_EMPLOYEE_STORE_UNAVAILABLE",
                "员工库暂时读不到，无法校验成员身份；本次未写入。",
            )
        if sid not in names:
            raise deny(
                422,
                "UNKNOWN_EMPLOYEE",
                f"员工库里没有名为「{sid}」的 AI 员工；请先在员工管理中创建。",
            )
        role = str((summaries.get(sid) or {}).get("role_description") or "").strip()
        if role:
            label = f"{sid}（{role}）"
    else:
        try:
            from src.kernels.identity import get_identity_manager

            node = get_identity_manager().get_account(sid)
        except Exception:  # noqa: BLE001 - 身份内核不可用时不放行未知账号
            node = None
        if node is None:
            raise deny(422, "UNKNOWN_ACCOUNT", f"账号「{sid}」不存在；无法把它挂到组织节点上。")
    return kind, sid, label


def _validate_reports_to(
    connection: sqlite3.Connection,
    account_id: str,
    assignment_id: Optional[str],
    reports_to: Optional[str],
) -> str:
    """校验汇报对象：同账号、非自身、不成环。返回规范化后的 id（'' 表示无）。"""
    target = str(reports_to or "").strip()
    if not target:
        return ""
    row = _assignment_row(connection, account_id, target)
    if assignment_id and target == assignment_id:
        raise deny(409, "ORG_REPORT_CYCLE", "汇报关系不能指向自己。")
    seen: Set[str] = set()
    cursor = target
    depth = 0
    while cursor and depth < MAX_DEPTH:
        if cursor == assignment_id:
            raise deny(409, "ORG_REPORT_CYCLE", "汇报关系会形成环，本次未写入。")
        if cursor in seen:
            break
        seen.add(cursor)
        parent = connection.execute(
            "SELECT reports_to_assignment_id FROM org_assignments "
            "WHERE account_id = ? AND assignment_id = ?",
            (account_id, cursor),
        ).fetchone()
        cursor = str(parent["reports_to_assignment_id"] or "") if parent else ""
        depth += 1
    return str(row["assignment_id"])


# ── 影响检查 ──────────────────────────────────────────────────

_TERMINAL_GOAL_STATES = {"completed", "failed", "cancelled"}
_TERMINAL_TASK_STATES = {
    "completed",
    "failed",
    "cancelled",
    "completed_unattributed",
    "skipped",
}


def _active_tasks(viewer: Any, employees: Set[str]) -> Dict[str, Any]:
    """查"这些人手上还有没有没跑完的活"。

    只覆盖执行内核**当前热集合**里的未终结目标；读不到任务库时返回
    ``checked=false``——绝不把"查不到"说成"无风险"。
    """
    if not employees:
        return {
            "checked": True,
            "count": 0,
            "items": [],
            "note": "本次调整未涉及 AI 员工，无需检查任务接管。",
        }
    try:
        from .ai_management import AIStateManager

        manager = AIStateManager()
        with manager._goal_lock:
            goals = [dict(goal) for goal in manager._goals.values() if isinstance(goal, dict)]
    except Exception as exc:  # noqa: BLE001 - 如实报告未检查
        logger.warning("org impact: task store unavailable: %s", exc)
        return {
            "checked": False,
            "count": None,
            "items": [],
            "reason": f"任务库暂时读不到，未检查（{type(exc).__name__}）。",
        }
    items: List[Dict[str, Any]] = []
    for goal in goals:
        try:
            if not viewer.sees(goal):
                continue
        except Exception:  # noqa: BLE001 - 归属判不了就不把它算进结论
            continue
        state = str(goal.get("state") or "")
        if state in _TERMINAL_GOAL_STATES:
            continue
        for task in goal.get("tasks") or []:
            if not isinstance(task, dict):
                continue
            status = str(task.get("status") or "")
            if status in _TERMINAL_TASK_STATES:
                continue
            names: Set[str] = set()
            raw = task.get("assigned_employee")
            if raw:
                names.add(str(raw))
            for name in task.get("assigned_employees") or []:
                if name:
                    names.add(str(name))
            hit = names & employees
            if not hit:
                continue
            items.append(
                {
                    "goal_id": goal.get("goal_id"),
                    "goal_state": state,
                    "task_id": task.get("task_id"),
                    "task_status": status,
                    "employee": sorted(hit)[0],
                    "goal": str(goal.get("natural_language") or "")[:120],
                }
            )
            if len(items) >= MAX_IMPACT_ITEMS:
                break
        if len(items) >= MAX_IMPACT_ITEMS:
            break
    return {
        "checked": True,
        "count": len(items),
        "items": items,
        "note": "仅覆盖执行内核当前未终结的目标；已完成/已失败/已取消的目标不计入。",
    }


def _impact(
    connection: sqlite3.Connection,
    viewer: Any,
    account_id: str,
    action: str,
    *,
    node_id: Optional[str] = None,
    assignment_id: Optional[str] = None,
    target_parent_id: Optional[str] = None,
    cascade: bool = False,
    clear_reports: bool = False,
) -> Dict[str, Any]:
    """计算一次计划调整的影响面。只描述本模块真正掌握的事实。"""
    nodes = _load_nodes(connection, account_id)
    assignments = _load_assignments(connection, account_id)
    node_names = {node["node_id"]: node["name"] for node in nodes}
    node_by_id = {node["node_id"]: node for node in nodes}
    assignment_by_id = {item["assignment_id"]: item for item in assignments}
    by_node: Dict[str, List[Dict[str, Any]]] = {}
    for item in assignments:
        by_node.setdefault(item["node_id"], []).append(item)

    blockers: List[Dict[str, Any]] = []
    orphaned: List[Dict[str, Any]] = []
    removed: List[Dict[str, Any]] = []
    affected_nodes: List[str] = []

    def _add_orphan(node_key: str, reason: str, **extra: Any) -> None:
        payload = {
            "node_id": node_key,
            "node_name": node_names.get(node_key, ""),
            "reason": reason,
        }
        payload.update(extra)
        if payload not in orphaned:
            orphaned.append(payload)

    if action == "delete_node":
        if not node_id:
            raise deny(422, "ORG_NODE_REQUIRED", "删除检查需要提供 node_id。")
        if node_id not in node_by_id:
            raise _neutral()
        affected_nodes = _subtree_ids(connection, account_id, node_id)
        children = [item for item in affected_nodes if item != node_id]
        if children and not cascade:
            blockers.append(
                {
                    "code": "ORG_NODE_HAS_CHILDREN",
                    "message": (
                        f"该节点下还有 {len(children)} 个子节点。默认不级联删除；"
                        "确认影响面后可显式 cascade=true。"
                    ),
                    "node_ids": children[:MAX_IMPACT_ITEMS],
                    "node_names": [node_names.get(item, "") for item in children[:MAX_IMPACT_ITEMS]],
                }
            )
        removed = [item for item in assignments if item["node_id"] in set(affected_nodes)]
        for item in removed:
            if item["is_lead"]:
                _add_orphan(
                    item["node_id"],
                    "lead_removed",
                    subject_type=item["subject_type"],
                    subject_id=item["subject_id"],
                )
        for key in affected_nodes:
            leads = [item for item in by_node.get(key, []) if item["is_lead"]]
            members = by_node.get(key, [])
            if not leads and members:
                _add_orphan(key, "node_has_no_lead")

    elif action == "move_node":
        if not node_id:
            raise deny(422, "ORG_NODE_REQUIRED", "移动检查需要提供 node_id。")
        if node_id not in node_by_id:
            raise _neutral()
        affected_nodes = _subtree_ids(connection, account_id, node_id)
        parent = str(target_parent_id or "").strip()
        if parent and parent not in node_by_id:
            raise _neutral()
        if parent and _would_cycle(connection, account_id, node_id, parent):
            blockers.append(
                {
                    "code": "ORG_CYCLE",
                    "message": "不能把节点移动到它自己或它的下级节点下。",
                    "node_ids": [node_id],
                }
            )
        for key in affected_nodes:
            if not [item for item in by_node.get(key, []) if item["is_lead"]]:
                if by_node.get(key):
                    _add_orphan(key, "moved_subtree_without_lead")
        if parent:
            parent_leads = [item for item in by_node.get(parent, []) if item["is_lead"]]
            if not parent_leads and assignments:
                _add_orphan(parent, "new_parent_has_no_lead")

    elif action == "clear_lead":
        if not node_id:
            raise deny(422, "ORG_NODE_REQUIRED", "取消负责人检查需要提供 node_id。")
        if node_id not in node_by_id:
            raise _neutral()
        leads = [item for item in by_node.get(node_id, []) if item["is_lead"]]
        removed = list(leads)
        for item in leads:
            _add_orphan(
                node_id,
                "lead_removed",
                subject_type=item["subject_type"],
                subject_id=item["subject_id"],
            )
        affected_nodes = [node_id]

    elif action == "remove_assignment":
        if not assignment_id:
            raise deny(422, "ORG_ASSIGNMENT_REQUIRED", "卸载成员检查需要提供 assignment_id。")
        target = assignment_by_id.get(assignment_id)
        if target is None:
            raise deny(404, "ORG_ASSIGNMENT_NOT_FOUND", "资源不存在或无权访问")
        removed = [target]
        affected_nodes = [target["node_id"]]
        subordinates = [
            item for item in assignments if item["reports_to_assignment_id"] == assignment_id
        ]
        if subordinates and not clear_reports:
            blockers.append(
                {
                    "code": "ORG_ASSIGNMENT_HAS_REPORTS",
                    "message": (
                        f"该成员是 {len(subordinates)} 条汇报关系的上级。默认不自动解绑；"
                        "确认后可 clear_reports=true。"
                    ),
                    "assignment_ids": [item["assignment_id"] for item in subordinates],
                }
            )
        if target["is_lead"]:
            _add_orphan(
                target["node_id"],
                "lead_removed",
                subject_type=target["subject_type"],
                subject_id=target["subject_id"],
            )
    else:
        raise deny(422, "ORG_IMPACT_ACTION_INVALID", "不支持的检查动作。")

    # 三类告警：有人失去负责人 / 任务无人接管 / 组织内任职失效。
    employees = {
        item["subject_id"]
        for item in removed
        if item["subject_type"] == "employee" and item["subject_id"]
    }
    unattended = _active_tasks(viewer, employees)
    authority_items = [
        {
            "assignment_id": item["assignment_id"],
            "node_id": item["node_id"],
            "node_name": node_names.get(item["node_id"], ""),
            "subject_type": item["subject_type"],
            "subject_id": item["subject_id"],
            "role": "lead" if item["is_lead"] else ("primary" if item["is_primary"] else "member"),
        }
        for item in removed
    ]
    return {
        "action": action,
        "account_id": account_id,
        "allowed": not blockers,
        "blockers": blockers,
        "warnings": {
            "orphaned_lead": {
                "count": len(orphaned),
                "items": orphaned[:MAX_IMPACT_ITEMS],
                "label": "有人失去负责人",
            },
            "unattended_tasks": {
                **unattended,
                "label": "任务无人接管",
            },
            "revoked_authority": {
                "count": len(authority_items),
                "items": authority_items[:MAX_IMPACT_ITEMS],
                "label": "组织内任职失效",
                "note": (
                    "组织数据当前不参与 RBAC 判定；这里只表示组织内的任职/负责人记录本身失效，"
                    "不代表账号权限被收回。"
                ),
            },
        },
        "affected_node_ids": affected_nodes,
        "note": "影响检查是只读预演，不会写入任何数据。",
    }


# ── 请求 / 响应模型 ───────────────────────────────────────────


def _validate_kind(value: Any) -> str:
    kind = str(value or "").strip().lower()
    if kind not in ORG_KINDS:
        raise ValueError("节点类型必须是 " + " / ".join(ORG_KINDS) + " 之一")
    return kind


class NodeCreateRequest(BaseModel):
    name: str = Field(..., description="节点名称（同一父节点下不可重名）")
    kind: str = Field("department", description="节点类型")
    parent_id: Optional[str] = Field(None, description="上级节点；空表示根节点")
    description: str = Field("", description="节点说明")

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        cleaned = str(value or "").strip()
        if not cleaned:
            raise ValueError("节点名称不能为空")
        normalize_required_name(cleaned, "节点名称", max_chars=MAX_NAME)
        return cleaned

    @field_validator("kind")
    @classmethod
    def _check_kind(cls, value: str) -> str:
        return _validate_kind(value)

    @field_validator("description")
    @classmethod
    def _check_description(cls, value: str) -> str:
        cleaned = str(value or "")
        if len(cleaned) > MAX_DESCRIPTION:
            raise ValueError(f"节点说明最多 {MAX_DESCRIPTION} 个字")
        if cleaned:
            reject_unsafe_text(cleaned, "节点说明")
        return cleaned


class NodePatchRequest(BaseModel):
    name: Optional[str] = None
    kind: Optional[str] = None
    description: Optional[str] = None

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = str(value).strip()
        if not cleaned:
            raise ValueError("节点名称不能为空")
        normalize_required_name(cleaned, "节点名称", max_chars=MAX_NAME)
        return cleaned

    @field_validator("kind")
    @classmethod
    def _check_kind(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else _validate_kind(value)

    @field_validator("description")
    @classmethod
    def _check_description(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = str(value)
        if len(cleaned) > MAX_DESCRIPTION:
            raise ValueError(f"节点说明最多 {MAX_DESCRIPTION} 个字")
        if cleaned:
            reject_unsafe_text(cleaned, "节点说明")
        return cleaned


class NodeMoveRequest(BaseModel):
    parent_id: Optional[str] = Field(None, description="新上级；空表示移到根")


class LeadRequest(BaseModel):
    subject_type: str = Field("employee", description="employee 或 human")
    subject_id: str
    title: str = ""


class AssignmentCreateRequest(BaseModel):
    subject_type: str = Field("employee", description="employee 或 human")
    subject_id: str
    subject_label: str = ""
    title: str = ""
    is_primary: bool = False
    is_lead: bool = False
    reports_to_assignment_id: Optional[str] = None


class AssignmentPatchRequest(BaseModel):
    title: Optional[str] = None
    is_primary: Optional[bool] = None
    is_lead: Optional[bool] = None
    reports_to_assignment_id: Optional[str] = None
    clear_reports_to: bool = False


class ImpactRequest(BaseModel):
    action: str = Field(..., description="delete_node / move_node / clear_lead / remove_assignment")
    node_id: Optional[str] = None
    assignment_id: Optional[str] = None
    target_parent_id: Optional[str] = None
    cascade: bool = False
    clear_reports: bool = False

    @field_validator("action")
    @classmethod
    def _check_action(cls, value: str) -> str:
        action = str(value or "").strip()
        if action not in {"delete_node", "move_node", "clear_lead", "remove_assignment"}:
            raise ValueError("action 只能是 delete_node / move_node / clear_lead / remove_assignment")
        return action


# ── 树 / 版本 序列化 ─────────────────────────────────────────


def _build_tree(nodes: List[Dict[str, Any]], assignments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    children = _children_map(nodes)
    by_node: Dict[str, List[Dict[str, Any]]] = {}
    for item in assignments:
        by_node.setdefault(item["node_id"], []).append(item)

    def _render(node: Dict[str, Any], depth: int = 0) -> Dict[str, Any]:
        members = by_node.get(node["node_id"], [])
        lead = next((item for item in members if item["is_lead"]), None)
        rendered = dict(node)
        rendered["lead"] = lead
        rendered["assignments"] = members
        rendered["depth"] = depth
        rendered["children"] = [
            _render(child, depth + 1) for child in children.get(node["node_id"], [])
        ]
        return rendered

    roots = [_render(node) for node in children.get("", [])]
    # 防御：父节点缺失的孤儿节点也要出现在树里，而不是凭空消失。
    known = {node["node_id"] for node in nodes}
    orphans = [
        _render(node)
        for node in nodes
        if node["parent_id"] and node["parent_id"] not in known
    ]
    return roots + orphans


def _version_row_to_dict(row: sqlite3.Row, include_snapshot: bool = False) -> Dict[str, Any]:
    try:
        summary = json.loads(row["summary_json"] or "{}")
    except (TypeError, ValueError):
        summary = {}
    payload = {
        "version_id": row["version_id"],
        "account_id": row["account_id"],
        "seq": int(row["seq"]),
        "created_at": row["created_at"],
        "actor": row["actor"],
        "action": row["action"],
        "target_node_id": row["target_node_id"] or None,
        "summary": summary if isinstance(summary, dict) else {},
    }
    if include_snapshot:
        try:
            snapshot = json.loads(row["snapshot_json"] or "{}")
        except (TypeError, ValueError):
            snapshot = {}
        payload["snapshot"] = snapshot if isinstance(snapshot, dict) else {}
    return payload


def _validate_snapshot(snapshot: Any) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """回滚前的防御性校验：结构不对就拒绝，绝不半截恢复。"""
    if not isinstance(snapshot, dict):
        raise deny(422, "ORG_SNAPSHOT_INVALID", "历史版本快照结构不合法，无法回滚。")
    raw_nodes = snapshot.get("nodes")
    raw_assignments = snapshot.get("assignments")
    if not isinstance(raw_nodes, list) or not isinstance(raw_assignments, list):
        raise deny(422, "ORG_SNAPSHOT_INVALID", "历史版本快照结构不合法，无法回滚。")

    nodes: List[Dict[str, Any]] = []
    node_ids: Set[str] = set()
    for item in raw_nodes:
        if not isinstance(item, dict):
            raise deny(422, "ORG_SNAPSHOT_INVALID", "历史版本快照里有非法节点。")
        node_id = str(item.get("node_id") or "").strip()
        kind = str(item.get("kind") or "").strip()
        name = str(item.get("name") or "").strip()
        if not node_id or node_id in node_ids or kind not in ORG_KINDS or not name:
            raise deny(422, "ORG_SNAPSHOT_INVALID", "历史版本快照里有非法节点。")
        node_ids.add(node_id)
        try:
            sort_order = int(item.get("sort_order") or 0)
            created_at = float(item.get("created_at") or 0.0)
            updated_at = float(item.get("updated_at") or 0.0)
        except (TypeError, ValueError):
            raise deny(422, "ORG_SNAPSHOT_INVALID", "历史版本快照里的节点时间/排序字段不合法。")
        nodes.append(
            {
                "node_id": node_id,
                "parent_id": str(item.get("parent_id") or ""),
                "kind": kind,
                "name": name,
                "description": str(item.get("description") or ""),
                "sort_order": sort_order,
                "created_at": created_at,
                "updated_at": updated_at,
                "created_by": str(item.get("created_by") or ""),
            }
        )
    parent_of = {node["node_id"]: node["parent_id"] for node in nodes}
    for node in nodes:
        parent = node["parent_id"]
        if parent and parent not in node_ids:
            raise deny(422, "ORG_SNAPSHOT_INVALID", "历史版本快照里有指向不存在上级的节点。")
    # 环检测
    for node in nodes:
        seen: Set[str] = set()
        cursor = node["node_id"]
        while cursor:
            if cursor in seen:
                raise deny(422, "ORG_SNAPSHOT_INVALID", "历史版本快照里的层级存在环。")
            seen.add(cursor)
            cursor = parent_of.get(cursor, "")

    assignments: List[Dict[str, Any]] = []
    assignment_ids: Set[str] = set()
    for item in raw_assignments:
        if not isinstance(item, dict):
            raise deny(422, "ORG_SNAPSHOT_INVALID", "历史版本快照里有非法任职记录。")
        assignment_id = str(item.get("assignment_id") or "").strip()
        node_id = str(item.get("node_id") or "").strip()
        subject_type = str(item.get("subject_type") or "").strip()
        subject_id = str(item.get("subject_id") or "").strip()
        if (
            not assignment_id
            or assignment_id in assignment_ids
            or node_id not in node_ids
            or subject_type not in SUBJECT_TYPES
            or not subject_id
        ):
            raise deny(422, "ORG_SNAPSHOT_INVALID", "历史版本快照里有非法任职记录。")
        assignment_ids.add(assignment_id)
        try:
            created_at = float(item.get("created_at") or 0.0)
            updated_at = float(item.get("updated_at") or 0.0)
        except (TypeError, ValueError):
            raise deny(422, "ORG_SNAPSHOT_INVALID", "历史版本快照里的任职时间字段不合法。")
        assignments.append(
            {
                "assignment_id": assignment_id,
                "node_id": node_id,
                "subject_type": subject_type,
                "subject_id": subject_id,
                "subject_label": str(item.get("subject_label") or ""),
                "title": str(item.get("title") or ""),
                "is_lead": 1 if item.get("is_lead") else 0,
                "is_primary": 1 if item.get("is_primary") else 0,
                "reports_to_assignment_id": str(item.get("reports_to_assignment_id") or ""),
                "created_at": created_at,
                "updated_at": updated_at,
            }
        )
    for item in assignments:
        target = item["reports_to_assignment_id"]
        if target and target not in assignment_ids:
            raise deny(422, "ORG_SNAPSHOT_INVALID", "历史版本快照里有指向不存在上级的汇报关系。")
    return nodes, assignments


# ── 写操作内部实现 ───────────────────────────────────────────


def _insert_node(
    connection: sqlite3.Connection,
    account_id: str,
    actor: str,
    *,
    name: str,
    kind: str,
    parent_id: Optional[str],
    description: str = "",
) -> Dict[str, Any]:
    parent = str(parent_id or "").strip()
    if parent:
        parent_row = connection.execute(
            "SELECT node_id FROM org_nodes WHERE account_id = ? AND node_id = ?",
            (account_id, parent),
        ).fetchone()
        if parent_row is None:
            raise deny(404, "ORG_PARENT_NOT_FOUND", "资源不存在或无权访问")
    row = connection.execute(
        "SELECT COALESCE(MAX(sort_order), 0) + 1 AS next_order FROM org_nodes "
        "WHERE account_id = ? AND parent_id = ?",
        (account_id, parent),
    ).fetchone()
    now = time.time()
    node_id = uuid.uuid4().hex[:12]
    try:
        connection.execute(
            "INSERT INTO org_nodes "
            "(node_id, account_id, parent_id, kind, name, description, sort_order, "
            " created_at, updated_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                node_id,
                account_id,
                parent,
                kind,
                name,
                description,
                int(row["next_order"]),
                now,
                now,
                actor,
            ),
        )
    except sqlite3.IntegrityError:
        raise deny(
            409,
            "ORG_DUP_NAME",
            "同一上级下已存在同名节点（大小写不敏感）；不同上级下可以重名。",
        )
    return _node_dict(_node_row(connection, account_id, node_id))


def _delete_subtree(
    connection: sqlite3.Connection, account_id: str, node_ids: Sequence[str]
) -> Tuple[int, int, int]:
    """删除子树及其任职记录，返回 (节点数, 任职数, 被解绑的汇报关系数)。"""
    ids = list(dict.fromkeys(node_ids))
    if not ids:
        return 0, 0, 0
    placeholders = ",".join("?" for _ in ids)
    assignment_rows = connection.execute(
        f"SELECT assignment_id FROM org_assignments "
        f"WHERE account_id = ? AND node_id IN ({placeholders})",
        (account_id, *ids),
    ).fetchall()
    assignment_ids = [row["assignment_id"] for row in assignment_rows]
    unbound = 0
    if assignment_ids:
        sub_placeholders = ",".join("?" for _ in assignment_ids)
        cursor = connection.execute(
            f"UPDATE org_assignments SET reports_to_assignment_id = '', updated_at = ? "
            f"WHERE account_id = ? AND reports_to_assignment_id IN ({sub_placeholders}) "
            f"AND assignment_id NOT IN ({sub_placeholders})",
            (time.time(), account_id, *assignment_ids, *assignment_ids),
        )
        unbound = max(0, int(cursor.rowcount or 0))
        connection.execute(
            f"DELETE FROM org_assignments WHERE account_id = ? AND assignment_id IN ({sub_placeholders})",
            (account_id, *assignment_ids),
        )
    connection.execute(
        f"DELETE FROM org_nodes WHERE account_id = ? AND node_id IN ({placeholders})",
        (account_id, *ids),
    )
    return len(ids), len(assignment_ids), unbound


# ── REST 端点 ─────────────────────────────────────────────────


@router.post("/nodes", status_code=201)
def create_node(req: NodeCreateRequest, viewer=Depends(_org_viewer)) -> Dict[str, Any]:
    """创建一个组织节点（任意层级；不预设任何部门）。"""
    account = viewer.principal
    with _tx() as connection:
        node = _insert_node(
            connection,
            account,
            viewer.principal,
            name=req.name,
            kind=req.kind,
            parent_id=req.parent_id,
            description=req.description,
        )
        version = _record_version(
            connection,
            account,
            actor=viewer.principal,
            action="create_node",
            target_node_id=node["node_id"],
            summary={"name": node["name"], "kind": node["kind"], "parent_id": node["parent_id"]},
        )
    audit_ok = _audit(
        "org.create_node",
        account,
        viewer.principal,
        node["node_id"],
        {"name": node["name"], "kind": node["kind"], "version_id": version["version_id"]},
    )
    return {"node": node, "version": version, "audit_written": audit_ok}


@router.get("/tree")
def get_tree(
    view_account: Optional[str] = Query(None, description="主账号可按子树账号视角查看（只读）"),
    viewer=Depends(_org_viewer),
) -> Dict[str, Any]:
    """返回账号可见的整棵组织树（含负责人、成员、层级深度）。"""
    account = _read_account(viewer, view_account)

    def _build(connection: sqlite3.Connection) -> Dict[str, Any]:
        nodes = _load_nodes(connection, account)
        assignments = _load_assignments(connection, account)
        latest = connection.execute(
            "SELECT * FROM org_versions WHERE account_id = ? ORDER BY seq DESC LIMIT 1",
            (account,),
        ).fetchone()
        return {
            "account_id": account,
            "roots": _build_tree(nodes, assignments),
            "node_count": len(nodes),
            "assignment_count": len(assignments),
            "latest_version": _version_row_to_dict(latest) if latest is not None else None,
            "view_account": (view_account or "").strip() or None,
        }

    return _read(_build)


@router.get("/nodes/{node_id}")
def get_node(node_id: str, viewer=Depends(_org_viewer)) -> Dict[str, Any]:
    """查看单个节点：自身、祖先链、直接子节点、任职记录。"""

    def _load(connection: sqlite3.Connection) -> Dict[str, Any]:
        account = viewer.principal
        row = _node_row(connection, account, node_id)
        node = _node_dict(row)
        assignments = [
            item for item in _load_assignments(connection, account) if item["node_id"] == node_id
        ]
        children = [
            _node_dict(child)
            for child in connection.execute(
                "SELECT * FROM org_nodes WHERE account_id = ? AND parent_id = ? "
                "ORDER BY sort_order, name, node_id",
                (account, node_id),
            ).fetchall()
        ]
        ancestors = []
        for ancestor_id in _ancestor_ids(connection, account, node_id):
            ancestor = connection.execute(
                "SELECT * FROM org_nodes WHERE account_id = ? AND node_id = ?",
                (account, ancestor_id),
            ).fetchone()
            if ancestor is not None:
                ancestors.append(_node_dict(ancestor))
        return {
            "node": node,
            "lead": next((item for item in assignments if item["is_lead"]), None),
            "assignments": assignments,
            "children": children,
            "ancestors": list(reversed(ancestors)),
        }

    return _read(_load)


@router.patch("/nodes/{node_id}")
def patch_node(
    node_id: str, req: NodePatchRequest, viewer=Depends(_org_viewer)
) -> Dict[str, Any]:
    """改名 / 改类型 / 改说明。"""
    if req.name is None and req.kind is None and req.description is None:
        raise deny(422, "ORG_EMPTY_PATCH", "没有需要修改的字段。")
    account = viewer.principal
    with _tx() as connection:
        current = _node_row(connection, account, node_id)
        fields: Dict[str, Any] = {}
        if req.name is not None:
            fields["name"] = req.name
        if req.kind is not None:
            fields["kind"] = req.kind
        if req.description is not None:
            fields["description"] = req.description
        fields["updated_at"] = time.time()
        set_clause = ", ".join(f"{key} = ?" for key in fields)
        try:
            connection.execute(
                f"UPDATE org_nodes SET {set_clause} WHERE account_id = ? AND node_id = ?",
                (*fields.values(), account, node_id),
            )
        except sqlite3.IntegrityError:
            raise deny(
                409,
                "ORG_DUP_NAME",
                "同一上级下已存在同名节点（大小写不敏感）；不同上级下可以重名。",
            )
        node = _node_dict(_node_row(connection, account, node_id))
        version = _record_version(
            connection,
            account,
            actor=viewer.principal,
            action="update_node",
            target_node_id=node_id,
            summary={
                "before": {
                    "name": current["name"],
                    "kind": current["kind"],
                    "description": current["description"],
                },
                "after": {"name": node["name"], "kind": node["kind"], "description": node["description"]},
            },
        )
    audit_ok = _audit(
        "org.update_node",
        account,
        viewer.principal,
        node_id,
        {"version_id": version["version_id"]},
    )
    return {"node": node, "version": version, "audit_written": audit_ok}


@router.post("/nodes/{node_id}/move")
def move_node(
    node_id: str, req: NodeMoveRequest, viewer=Depends(_org_viewer)
) -> Dict[str, Any]:
    """移动节点到新的上级（空表示移到根）；环检测在写入前完成。"""
    account = viewer.principal
    with _tx() as connection:
        current = _node_row(connection, account, node_id)
        parent = str(req.parent_id or "").strip()
        if parent:
            _node_row(connection, account, parent)
        if _would_cycle(connection, account, node_id, parent):
            raise deny(409, "ORG_CYCLE", "不能把节点移动到它自己或它的下级节点下。")
        try:
            connection.execute(
                "UPDATE org_nodes SET parent_id = ?, updated_at = ? "
                "WHERE account_id = ? AND node_id = ?",
                (parent, time.time(), account, node_id),
            )
        except sqlite3.IntegrityError:
            raise deny(
                409,
                "ORG_DUP_NAME",
                "目标上级下已有同名节点（大小写不敏感）；请先改名再移动。",
            )
        node = _node_dict(_node_row(connection, account, node_id))
        version = _record_version(
            connection,
            account,
            actor=viewer.principal,
            action="move_node",
            target_node_id=node_id,
            summary={
                "from_parent_id": current["parent_id"] or None,
                "to_parent_id": parent or None,
            },
        )
    audit_ok = _audit(
        "org.move_node",
        account,
        viewer.principal,
        node_id,
        {
            "from_parent_id": current["parent_id"] or None,
            "to_parent_id": parent or None,
            "version_id": version["version_id"],
        },
    )
    return {"node": node, "version": version, "audit_written": audit_ok}


@router.delete("/nodes/{node_id}")
def delete_node(
    node_id: str,
    cascade: bool = Query(False, description="有子节点时是否显式级联删除整棵子树"),
    viewer=Depends(_org_viewer),
) -> Dict[str, Any]:
    """删除节点。默认拒绝有子节点的删除并返回影响面；cascade=true 才级联。"""
    account = viewer.principal
    with _tx() as connection:
        _node_row(connection, account, node_id)
        impact = _impact(connection, viewer, account, "delete_node", node_id=node_id, cascade=cascade)
        if not impact["allowed"]:
            raise deny(
                409,
                "ORG_NODE_HAS_CHILDREN",
                "该节点下还有子节点，默认不级联删除；请先查看影响面，确认后使用 cascade=true。",
                impact=impact,
            )
        subtree = _subtree_ids(connection, account, node_id)
        removed_nodes, removed_assignments, unbound = _delete_subtree(connection, account, subtree)
        version = _record_version(
            connection,
            account,
            actor=viewer.principal,
            action="delete_node",
            target_node_id=node_id,
            summary={
                "cascade": bool(cascade),
                "removed_nodes": removed_nodes,
                "removed_assignments": removed_assignments,
                "reports_unbound": unbound,
            },
        )
    audit_ok = _audit(
        "org.delete_node",
        account,
        viewer.principal,
        node_id,
        {
            "cascade": bool(cascade),
            "removed_nodes": removed_nodes,
            "removed_assignments": removed_assignments,
            "version_id": version["version_id"],
        },
    )
    return {
        "removed_node_id": node_id,
        "removed_nodes": removed_nodes,
        "removed_assignments": removed_assignments,
        "reports_unbound": unbound,
        "impact": impact,
        "version": version,
        "audit_written": audit_ok,
    }


@router.put("/nodes/{node_id}/lead")
def set_lead(node_id: str, req: LeadRequest, viewer=Depends(_org_viewer)) -> Dict[str, Any]:
    """设置/更换节点负责人。已在岗的成员会被就地提升为负责人，不重复建记录。"""
    account = viewer.principal
    subject_type, subject_id, label = _clean_subject(req.subject_type, req.subject_id)
    title = str(req.title or "").strip()[:MAX_TITLE]
    with _tx() as connection:
        _node_row(connection, account, node_id)
        existing_lead = connection.execute(
            "SELECT * FROM org_assignments WHERE account_id = ? AND node_id = ? AND is_lead = 1",
            (account, node_id),
        ).fetchone()
        member = connection.execute(
            "SELECT * FROM org_assignments WHERE account_id = ? AND node_id = ? "
            "AND subject_type = ? AND subject_id = ?",
            (account, node_id, subject_type, subject_id),
        ).fetchone()
        now = time.time()
        if existing_lead is not None and (
            existing_lead["subject_type"] != subject_type
            or existing_lead["subject_id"] != subject_id
        ):
            connection.execute(
                "UPDATE org_assignments SET is_lead = 0, updated_at = ? WHERE assignment_id = ?",
                (now, existing_lead["assignment_id"]),
            )
            existing_lead = None
        if member is not None:
            connection.execute(
                "UPDATE org_assignments SET is_lead = 1, subject_label = ?, title = ?, updated_at = ? "
                "WHERE assignment_id = ?",
                (label, title, now, member["assignment_id"]),
            )
            assignment_id = member["assignment_id"]
            action = "promote_lead"
        else:
            assignment_id = uuid.uuid4().hex
            connection.execute(
                "INSERT INTO org_assignments "
                "(assignment_id, account_id, node_id, subject_type, subject_id, subject_label, "
                " title, is_lead, is_primary, reports_to_assignment_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, '', ?, ?)",
                (assignment_id, account, node_id, subject_type, subject_id, label, title, now, now),
            )
            action = "set_lead"
        assignment = _assignment_dict(_assignment_row(connection, account, assignment_id))
        version = _record_version(
            connection,
            account,
            actor=viewer.principal,
            action=action,
            target_node_id=node_id,
            summary={
                "assignment_id": assignment_id,
                "subject_type": subject_type,
                "subject_id": subject_id,
            },
        )
    audit_ok = _audit(
        "org.set_lead",
        account,
        viewer.principal,
        node_id,
        {"assignment_id": assignment_id, "subject_id": subject_id, "version_id": version["version_id"]},
    )
    return {"assignment": assignment, "version": version, "audit_written": audit_ok}


@router.delete("/nodes/{node_id}/lead")
def clear_lead(
    node_id: str,
    clear_reports: bool = Query(False, description="该负责人有下级汇报时是否自动解绑"),
    viewer=Depends(_org_viewer),
) -> Dict[str, Any]:
    """取消负责人（删除这条任职记录）。有下级汇报关系时默认 409。"""
    account = viewer.principal
    with _tx() as connection:
        _node_row(connection, account, node_id)
        lead = connection.execute(
            "SELECT * FROM org_assignments WHERE account_id = ? AND node_id = ? AND is_lead = 1",
            (account, node_id),
        ).fetchone()
        if lead is None:
            raise deny(404, "ORG_LEAD_NOT_SET", "该节点还没有设置负责人。")
        subordinates = connection.execute(
            "SELECT assignment_id FROM org_assignments "
            "WHERE account_id = ? AND reports_to_assignment_id = ?",
            (account, lead["assignment_id"]),
        ).fetchall()
        if subordinates and not clear_reports:
            raise deny(
                409,
                "ORG_ASSIGNMENT_HAS_REPORTS",
                f"该负责人是 {len(subordinates)} 条汇报关系的上级。默认不自动解绑；"
                "确认后可 clear_reports=true。",
                assignment_ids=[row["assignment_id"] for row in subordinates],
            )
        unbound = 0
        if subordinates:
            cursor = connection.execute(
                "UPDATE org_assignments SET reports_to_assignment_id = '', updated_at = ? "
                "WHERE account_id = ? AND reports_to_assignment_id = ?",
                (time.time(), account, lead["assignment_id"]),
            )
            unbound = max(0, int(cursor.rowcount or 0))
        connection.execute(
            "DELETE FROM org_assignments WHERE account_id = ? AND assignment_id = ?",
            (account, lead["assignment_id"]),
        )
        version = _record_version(
            connection,
            account,
            actor=viewer.principal,
            action="clear_lead",
            target_node_id=node_id,
            summary={
                "removed_assignment_id": lead["assignment_id"],
                "subject_type": lead["subject_type"],
                "subject_id": lead["subject_id"],
                "reports_unbound": unbound,
            },
        )
    audit_ok = _audit(
        "org.clear_lead",
        account,
        viewer.principal,
        node_id,
        {"removed_assignment_id": lead["assignment_id"], "version_id": version["version_id"]},
    )
    return {
        "removed_assignment_id": lead["assignment_id"],
        "reports_unbound": unbound,
        "version": version,
        "audit_written": audit_ok,
    }


@router.post("/nodes/{node_id}/members", status_code=201)
def add_member(
    node_id: str, req: AssignmentCreateRequest, viewer=Depends(_org_viewer)
) -> Dict[str, Any]:
    """把 AI 员工 / 账号挂到节点上，可设主岗与汇报对象。"""
    account = viewer.principal
    subject_type, subject_id, auto_label = _clean_subject(req.subject_type, req.subject_id)
    label = str(req.subject_label or "").strip()[:MAX_LABEL] or auto_label
    title = str(req.title or "").strip()[:MAX_TITLE]
    with _tx() as connection:
        _node_row(connection, account, node_id)
        reports_to = _validate_reports_to(connection, account, None, req.reports_to_assignment_id)
        if req.is_lead:
            existing_lead = connection.execute(
                "SELECT assignment_id FROM org_assignments "
                "WHERE account_id = ? AND node_id = ? AND is_lead = 1",
                (account, node_id),
            ).fetchone()
            if existing_lead is not None:
                raise deny(
                    409,
                    "ORG_LEAD_EXISTS",
                    "该节点已经有负责人；请用 PUT /v1/org/nodes/{id}/lead 更换，避免静默覆盖。",
                )
        assignment_id = uuid.uuid4().hex
        now = time.time()
        try:
            connection.execute(
                "INSERT INTO org_assignments "
                "(assignment_id, account_id, node_id, subject_type, subject_id, subject_label, "
                " title, is_lead, is_primary, reports_to_assignment_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    assignment_id,
                    account,
                    node_id,
                    subject_type,
                    subject_id,
                    label,
                    title,
                    1 if req.is_lead else 0,
                    1 if req.is_primary else 0,
                    reports_to,
                    now,
                    now,
                ),
            )
        except sqlite3.IntegrityError:
            raise deny(
                409,
                "ORG_ASSIGNMENT_EXISTS",
                "该成员已经在这个节点上；如需调整主兼岗或汇报关系，请修改现有任职记录。",
            )
        assignment = _assignment_dict(_assignment_row(connection, account, assignment_id))
        version = _record_version(
            connection,
            account,
            actor=viewer.principal,
            action="add_member",
            target_node_id=node_id,
            summary={
                "assignment_id": assignment_id,
                "subject_type": subject_type,
                "subject_id": subject_id,
            },
        )
    audit_ok = _audit(
        "org.add_member",
        account,
        viewer.principal,
        node_id,
        {"assignment_id": assignment_id, "subject_id": subject_id, "version_id": version["version_id"]},
    )
    return {"assignment": assignment, "version": version, "audit_written": audit_ok}


@router.patch("/assignments/{assignment_id}")
def patch_assignment(
    assignment_id: str,
    req: AssignmentPatchRequest,
    viewer=Depends(_org_viewer),
) -> Dict[str, Any]:
    """调整任职：岗位称谓、主/兼岗、是否负责人、汇报对象。"""
    if (
        req.title is None
        and req.is_primary is None
        and req.is_lead is None
        and req.reports_to_assignment_id is None
        and not req.clear_reports_to
    ):
        raise deny(422, "ORG_EMPTY_PATCH", "没有需要修改的字段。")
    if req.clear_reports_to and req.reports_to_assignment_id:
        raise deny(422, "ORG_AMBIGUOUS_PATCH", "不能同时指定新的汇报对象和 clear_reports_to。")
    account = viewer.principal
    with _tx() as connection:
        current = _assignment_row(connection, account, assignment_id)
        fields: Dict[str, Any] = {}
        if req.title is not None:
            fields["title"] = str(req.title).strip()[:MAX_TITLE]
        if req.is_primary is not None:
            fields["is_primary"] = 1 if req.is_primary else 0
        if req.is_lead is not None:
            if req.is_lead:
                other = connection.execute(
                    "SELECT assignment_id FROM org_assignments "
                    "WHERE account_id = ? AND node_id = ? AND is_lead = 1 AND assignment_id != ?",
                    (account, current["node_id"], assignment_id),
                ).fetchone()
                if other is not None:
                    connection.execute(
                        "UPDATE org_assignments SET is_lead = 0, updated_at = ? WHERE assignment_id = ?",
                        (time.time(), other["assignment_id"]),
                    )
                fields["is_lead"] = 1
            else:
                fields["is_lead"] = 0
        if req.clear_reports_to:
            fields["reports_to_assignment_id"] = ""
        elif req.reports_to_assignment_id is not None:
            fields["reports_to_assignment_id"] = _validate_reports_to(
                connection, account, assignment_id, req.reports_to_assignment_id
            )
        fields["updated_at"] = time.time()
        sql = ", ".join(f"{key} = ?" for key in fields)
        connection.execute(
            f"UPDATE org_assignments SET {sql} WHERE account_id = ? AND assignment_id = ?",
            (*fields.values(), account, assignment_id),
        )
        assignment = _assignment_dict(_assignment_row(connection, account, assignment_id))
        version = _record_version(
            connection,
            account,
            actor=viewer.principal,
            action="update_assignment",
            target_node_id=current["node_id"],
            summary={"assignment_id": assignment_id, "fields": sorted(fields.keys())},
        )
    audit_ok = _audit(
        "org.update_assignment",
        account,
        viewer.principal,
        assignment_id,
        {"version_id": version["version_id"]},
    )
    return {"assignment": assignment, "version": version, "audit_written": audit_ok}


@router.delete("/assignments/{assignment_id}")
def remove_assignment(
    assignment_id: str,
    clear_reports: bool = Query(False, description="该成员有下级汇报时是否自动解绑"),
    viewer=Depends(_org_viewer),
) -> Dict[str, Any]:
    """卸载成员；若其是负责人则一并取消负责人。有下级汇报时默认 409。"""
    account = viewer.principal
    with _tx() as connection:
        target = _assignment_row(connection, account, assignment_id)
        subordinates = connection.execute(
            "SELECT assignment_id FROM org_assignments "
            "WHERE account_id = ? AND reports_to_assignment_id = ?",
            (account, assignment_id),
        ).fetchall()
        if subordinates and not clear_reports:
            raise deny(
                409,
                "ORG_ASSIGNMENT_HAS_REPORTS",
                f"该成员是 {len(subordinates)} 条汇报关系的上级。默认不自动解绑；"
                "确认后可 clear_reports=true。",
                assignment_ids=[row["assignment_id"] for row in subordinates],
            )
        unbound = 0
        if subordinates:
            cursor = connection.execute(
                "UPDATE org_assignments SET reports_to_assignment_id = '', updated_at = ? "
                "WHERE account_id = ? AND reports_to_assignment_id = ?",
                (time.time(), account, assignment_id),
            )
            unbound = max(0, int(cursor.rowcount or 0))
        connection.execute(
            "DELETE FROM org_assignments WHERE account_id = ? AND assignment_id = ?",
            (account, assignment_id),
        )
        version = _record_version(
            connection,
            account,
            actor=viewer.principal,
            action="remove_member",
            target_node_id=target["node_id"],
            summary={
                "removed_assignment_id": assignment_id,
                "subject_type": target["subject_type"],
                "subject_id": target["subject_id"],
                "reports_unbound": unbound,
            },
        )
    audit_ok = _audit(
        "org.remove_member",
        account,
        viewer.principal,
        assignment_id,
        {"version_id": version["version_id"]},
    )
    return {
        "removed_assignment_id": assignment_id,
        "reports_unbound": unbound,
        "version": version,
        "audit_written": audit_ok,
    }


@router.get("/versions")
def list_versions(
    limit: int = Query(50, ge=1, le=500, description="最多返回多少条历史版本"),
    view_account: Optional[str] = Query(None, description="主账号可按子树账号视角查看"),
    viewer=Depends(_org_viewer),
) -> Dict[str, Any]:
    """历史版本列表（不含快照正文，避免响应过大）。"""
    account = _read_account(viewer, view_account)

    def _load(connection: sqlite3.Connection) -> Dict[str, Any]:
        rows = connection.execute(
            "SELECT * FROM org_versions WHERE account_id = ? ORDER BY seq DESC LIMIT ?",
            (account, int(limit)),
        ).fetchall()
        total = connection.execute(
            "SELECT COUNT(1) AS n FROM org_versions WHERE account_id = ?", (account,)
        ).fetchone()["n"]
        return {
            "account_id": account,
            "versions": [_version_row_to_dict(row) for row in rows],
            "count": len(rows),
            "total": int(total),
        }

    return _read(_load)


@router.get("/versions/{version_id}")
def get_version(version_id: str, viewer=Depends(_org_viewer)) -> Dict[str, Any]:
    """查看某一版的完整快照。"""
    account = viewer.principal

    def _load(connection: sqlite3.Connection) -> Dict[str, Any]:
        row = connection.execute(
            "SELECT * FROM org_versions WHERE account_id = ? AND version_id = ?",
            (account, version_id),
        ).fetchone()
        if row is None:
            raise deny(404, "ORG_VERSION_NOT_FOUND", "资源不存在或无权访问")
        return _version_row_to_dict(row, include_snapshot=True)

    return _read(_load)


@router.post("/versions/{version_id}/rollback")
def rollback_version(version_id: str, viewer=Depends(_org_viewer)) -> Dict[str, Any]:
    """回滚到某一版：重建当时的结构（含已被删除的节点），并追加一版新历史。"""
    account = viewer.principal
    with _tx() as connection:
        row = connection.execute(
            "SELECT * FROM org_versions WHERE account_id = ? AND version_id = ?",
            (account, version_id),
        ).fetchone()
        if row is None:
            raise deny(404, "ORG_VERSION_NOT_FOUND", "资源不存在或无权访问")
        try:
            snapshot = json.loads(row["snapshot_json"] or "{}")
        except (TypeError, ValueError):
            raise deny(422, "ORG_SNAPSHOT_INVALID", "历史版本快照已损坏，无法回滚。")
        nodes, assignments = _validate_snapshot(snapshot)
        # 目标版本若是"回滚产生"的，也允许继续回滚——历史只读，不递归改写。
        connection.execute("DELETE FROM org_assignments WHERE account_id = ?", (account,))
        connection.execute("DELETE FROM org_nodes WHERE account_id = ?", (account,))
        now = time.time()
        try:
            for node in nodes:
                connection.execute(
                    "INSERT INTO org_nodes "
                    "(node_id, account_id, parent_id, kind, name, description, sort_order, "
                    " created_at, updated_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        node["node_id"],
                        account,
                        node["parent_id"],
                        node["kind"],
                        node["name"],
                        node["description"],
                        node["sort_order"],
                        node["created_at"] or now,
                        node["updated_at"] or now,
                        node["created_by"],
                    ),
                )
            for item in assignments:
                connection.execute(
                    "INSERT INTO org_assignments "
                    "(assignment_id, account_id, node_id, subject_type, subject_id, subject_label, "
                    " title, is_lead, is_primary, reports_to_assignment_id, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        item["assignment_id"],
                        account,
                        item["node_id"],
                        item["subject_type"],
                        item["subject_id"],
                        item["subject_label"],
                        item["title"],
                        item["is_lead"],
                        item["is_primary"],
                        item["reports_to_assignment_id"],
                        item["created_at"] or now,
                        item["updated_at"] or now,
                    ),
                )
        except sqlite3.IntegrityError:
            raise deny(
                422,
                "ORG_SNAPSHOT_INVALID",
                "历史版本快照与当前约束冲突（同级重名或负责人重复），未回滚。",
            )
        version = _record_version(
            connection,
            account,
            actor=viewer.principal,
            action="rollback",
            target_node_id=row["target_node_id"] or "",
            summary={
                "rolled_back_to": version_id,
                "rolled_back_seq": int(row["seq"]),
                "restored_nodes": len(nodes),
                "restored_assignments": len(assignments),
            },
        )
    audit_ok = _audit(
        "org.rollback",
        account,
        viewer.principal,
        version_id,
        {
            "rolled_back_seq": int(row["seq"]),
            "restored_nodes": len(nodes),
            "restored_assignments": len(assignments),
            "version_id": version["version_id"],
        },
    )
    return {
        "rolled_back_to": version_id,
        "restored_nodes": len(nodes),
        "restored_assignments": len(assignments),
        "version": version,
        "audit_written": audit_ok,
    }


@router.post("/impact")
def check_impact(req: ImpactRequest, viewer=Depends(_org_viewer)) -> Dict[str, Any]:
    """只读预演一次组织调整的影响面（不写入任何数据）。"""
    account = viewer.principal
    return _read(
        lambda connection: _impact(
            connection,
            viewer,
            account,
            req.action,
            node_id=req.node_id,
            assignment_id=req.assignment_id,
            target_parent_id=req.target_parent_id,
            cascade=req.cascade,
            clear_reports=req.clear_reports,
        )
    )
