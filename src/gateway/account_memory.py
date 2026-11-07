"""账号记忆：按 owner 隔离的列表 / 检索 / 导出（``/v1/memory/*``）。

产品口径（``docs/account-hierarchy.md`` §3.4）
----------------------------------------------
* 记忆条目属于**记录者本人**：自己可读可写，别人（含上级）不可见；
* 主账号拥有**整棵子树的只读聚合**，且结果必须带来源标注（``account_id``）；
* 不是自己的条目一律 404（与"不存在"不可区分，防枚举）；
* 主账号**不得静默改写**子账号记忆：写入端点的 owner 恒为令牌主体，请求体
  里没有、也不会使用 "owner" 字段；
* 代管（把记忆交给上级处理）必须由本人**显式开启**，开关本身写审计。
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid
from typing import Any, Dict, Iterable, List, Tuple

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..kernels.identity import _accounts, get_identity_manager
from .account_context import AccountContext, deny, require_account_principal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/memory", tags=["memory"])

_ENV_DB = "LIUHAO_ACCOUNT_MEMORY_DB"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS account_memory_entries (
    id         TEXT PRIMARY KEY,
    owner      TEXT NOT NULL,
    text       TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT '',
    tags       TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_owner ON account_memory_entries(owner);
CREATE TABLE IF NOT EXISTS account_memory_custody (
    owner      TEXT PRIMARY KEY,
    enabled    INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT '',
    updated_by TEXT NOT NULL DEFAULT ''
);
"""


class MemoryWriteRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=8000)
    source: str = Field("user", max_length=64)
    tags: List[str] = Field(default_factory=list)


class MemoryExportRequest(BaseModel):
    scope: str = Field("self", description="self 或 subtree")
    format: str = Field("json", description="json 或 md")


class CustodyRequest(BaseModel):
    enabled: bool


class MemoryAdoptRequest(BaseModel):
    """根的"取用"：把子树账号的记忆复制到根自己的记忆库。

    ``account_id`` 留空表示整棵子树；无论哪种，都只写入**根自己的 owner**，
    子账号侧数据与可见性完全不变（子账号看不到根的动作）。
    """
    account_id: str = Field("", max_length=128, description="来源账号；空=整棵子树")


def db_path() -> str:
    explicit = (os.environ.get(_ENV_DB) or "").strip()
    if explicit:
        return explicit
    root = (os.environ.get("LIUHAO_WORKSPACE_ROOT") or "").strip()
    if root:
        return os.path.join(root, "account_memory.sqlite3")
    return os.path.join("data", "account_memory.sqlite3")


def _connect() -> sqlite3.Connection:
    path = db_path()
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.executescript(_SCHEMA)
    connection.commit()
    return connection


def _now_iso() -> str:
    from src._time import utc_now

    return utc_now().isoformat()


def _row_to_entry(row: sqlite3.Row) -> Dict[str, Any]:
    try:
        tags = json.loads(row["tags"] or "[]")
    except Exception:  # noqa: BLE001
        tags = []
    return {
        "id": row["id"],
        "account_id": row["owner"],
        "text": row["text"],
        "source": row["source"],
        "tags": tags if isinstance(tags, list) else [],
        "created_at": row["created_at"],
    }


def _visible_owners(context: AccountContext, scope: str) -> Tuple[str, ...]:
    """把 ``scope`` 收敛成 owner 集合；越权/非法一律 404/400，不泄露结构。"""
    wanted = str(scope or "self").strip().lower()
    if wanted == "self":
        return (context.principal,)
    if wanted == "subtree":
        if not context.is_root:
            raise deny(
                404, _accounts.CODE_SUBTREE_DENIED, _accounts.NEUTRAL_NOT_FOUND_MESSAGE
            )
        return tuple(context.subtree) or (context.principal,)
    raise deny(400, "ACCOUNT_SCOPE_INVALID", "范围无效")


def _query_entries(owners: Iterable[str], query: str) -> List[Dict[str, Any]]:
    owner_list = [str(item) for item in owners if item]
    if not owner_list:
        return []
    placeholders = ",".join(["?"] * len(owner_list))
    sql = (
        "SELECT id, owner, text, source, tags, created_at "
        f"FROM account_memory_entries WHERE owner IN ({placeholders})"
    )
    params: List[Any] = list(owner_list)
    needle = str(query or "").strip()
    if needle:
        sql += " AND (text LIKE ? OR source LIKE ? OR tags LIKE ?)"
        pattern = f"%{needle}%"
        params.extend([pattern, pattern, pattern])
    sql += " ORDER BY created_at DESC, id DESC"
    with _connect() as connection:
        rows = connection.execute(sql, params).fetchall()
    return [_row_to_entry(row) for row in rows]


@router.get("/entries")
def list_entries(
    scope: str = Query("self", description="self 或 subtree"),
    q: str = Query("", description="关键词；匹配正文/来源/标签"),
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """列表与检索共用同一过滤：先按 owner 收敛，再做关键词匹配。"""
    owners = _visible_owners(context, scope)
    entries = _query_entries(owners, q)
    return {
        "entries": entries,
        "scope": "subtree" if len(owners) > 1 else "self",
        "count": len(entries),
    }


@router.get("/entries/{entry_id}")
def get_entry(
    entry_id: str, context: AccountContext = Depends(require_account_principal)
) -> Dict[str, Any]:
    """单条读取：不是自己的条目与"不存在"完全相同（404）。"""
    with _connect() as connection:
        row = connection.execute(
            "SELECT id, owner, text, source, tags, created_at "
            "FROM account_memory_entries WHERE id = ?",
            (str(entry_id),),
        ).fetchone()
    if row is None or not context.can_read(str(row["owner"])):
        raise deny(
            404, _accounts.CODE_MEMORY_FOREIGN_DENIED, _accounts.NEUTRAL_NOT_FOUND_MESSAGE
        )
    return {"entry": _row_to_entry(row)}


@router.post("/entries", status_code=201)
def write_entry(
    payload: MemoryWriteRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """只写**自己**的记忆；主账号也不能借这个端点改写子账号记忆。"""
    entry_id = str(uuid.uuid4())
    created_at = _now_iso()
    with _connect() as connection:
        connection.execute(
            "INSERT INTO account_memory_entries "
            "(id, owner, text, source, tags, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                entry_id,
                context.principal,
                payload.text,
                payload.source,
                json.dumps(payload.tags, ensure_ascii=False),
                created_at,
            ),
        )
        connection.commit()
    return {
        "entry": {
            "id": entry_id,
            "account_id": context.principal,
            "text": payload.text,
            "source": payload.source,
            "tags": payload.tags,
            "created_at": created_at,
        }
    }


@router.post("/export")
def export_entries(
    payload: MemoryExportRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """导出可见记忆（成员仅自己，主账号可整棵子树），带来源标注。"""
    owners = _visible_owners(context, payload.scope)
    entries = _query_entries(owners, "")
    fmt = str(payload.format or "json").strip().lower()
    if fmt not in ("json", "md"):
        raise deny(400, "ACCOUNT_FORMAT_INVALID", "导出格式无效")
    scope_name = "subtree" if len(owners) > 1 else "self"
    if fmt == "md":
        lines = [
            f"- **{entry['account_id']}** · {entry['created_at']} · "
            f"{entry['text'].replace(chr(10), ' ')}"
            for entry in entries
        ]
        content = "# 记忆导出（%s）\n\n%s\n" % (scope_name, "\n".join(lines))
        filename = f"memory-{scope_name}.md"
    else:
        content = json.dumps(
            {"scope": scope_name, "entries": entries}, ensure_ascii=False, indent=2
        )
        filename = f"memory-{scope_name}.json"
    get_identity_manager().record_event(
        action="memory.export",
        actor=context.principal,
        target=context.principal,
        outcome="allowed",
        details={"scope": scope_name, "entries": len(entries), "format": fmt},
    )
    return {"content": content, "filename": filename, "entries": len(entries)}


@router.post("/adopt")
def adopt_entries(
    payload: MemoryAdoptRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """主账号把子树记忆**复制**进自己的记忆库（可搜索、可引用）。

    只新增 ``owner = 主账号`` 的条目，并给每条打上来源标签
    （``from:<来源账号>`` / ``origin:<原条目 id>``），返回与审计都能追溯；
    子账号的记忆行本身不会被改写，也不会收到任何通知 —— 子账号侧不可见。
    重复取用同一条目按 ``origin:<id>`` 去重，不产生副本。
    """
    if not context.is_root:
        raise deny(
            404, _accounts.CODE_SUBTREE_DENIED, _accounts.NEUTRAL_NOT_FOUND_MESSAGE
        )
    wanted = str(payload.account_id or "").strip()
    if wanted:
        if wanted == context.principal or wanted not in context.subtree:
            raise deny(
                404, _accounts.CODE_SUBTREE_DENIED, _accounts.NEUTRAL_NOT_FOUND_MESSAGE
            )
        owners = (wanted,)
    else:
        owners = tuple(item for item in context.subtree if item != context.principal)
    entries = _query_entries(owners, "")
    imported = 0
    skipped = 0
    adopted_at = _now_iso()
    with _connect() as connection:
        for entry in entries:
            marker = '%"origin:' + str(entry["id"]) + '"%'
            existing = connection.execute(
                "SELECT 1 FROM account_memory_entries WHERE owner = ? AND tags LIKE ? LIMIT 1",
                (context.principal, marker),
            ).fetchone()
            if existing is not None:
                skipped += 1
                continue
            tags = list(entry["tags"]) + [
                f"from:{entry['account_id']}",
                f"origin:{entry['id']}",
            ]
            connection.execute(
                "INSERT INTO account_memory_entries "
                "(id, owner, text, source, tags, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    context.principal,
                    entry["text"],
                    f"adopt:{entry['account_id']}",
                    json.dumps(tags, ensure_ascii=False),
                    adopted_at,
                ),
            )
            imported += 1
        connection.commit()
    get_identity_manager().record_event(
        action="memory.adopt",
        actor=context.principal,
        target=context.principal,
        outcome="allowed",
        details={
            "accounts": list(owners),
            "imported": imported,
            "skipped": skipped,
        },
    )
    return {
        "imported": imported,
        "skipped": skipped,
        "accounts": list(owners),
    }


@router.get("/custody")
def read_custody(
    context: AccountContext = Depends(require_account_principal)
) -> Dict[str, Any]:
    with _connect() as connection:
        row = connection.execute(
            "SELECT enabled, updated_at FROM account_memory_custody WHERE owner = ?",
            (context.principal,),
        ).fetchone()
    return {
        "enabled": bool(row["enabled"]) if row else False,
        "updated_at": row["updated_at"] if row else "",
    }


@router.post("/custody")
def set_custody(
    payload: CustodyRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """本人显式开启/关闭"由上级代管记忆"；开关写审计，绝不静默变更。"""
    updated_at = _now_iso()
    with _connect() as connection:
        connection.execute(
            "INSERT INTO account_memory_custody (owner, enabled, updated_at, updated_by) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(owner) DO UPDATE SET enabled = excluded.enabled, "
            " updated_at = excluded.updated_at, updated_by = excluded.updated_by",
            (context.principal, 1 if payload.enabled else 0, updated_at, context.principal),
        )
        connection.commit()
    get_identity_manager().record_event(
        action="memory.custody",
        actor=context.principal,
        target=context.principal,
        outcome="allowed",
        details={"enabled": bool(payload.enabled)},
    )
    return {"enabled": bool(payload.enabled), "updated_at": updated_at}
