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

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, field_validator

from ..common.input_guard import normalize_required_text, reject_unsafe_text
from ..common.data_paths import resolve_store_path
from ..kernels.identity import _accounts, get_identity_manager
from .account_context import AccountContext, deny, require_account_principal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/memory", tags=["memory"])

_ENV_DB = "LIUHAO_ACCOUNT_MEMORY_DB"

#: 迁移时无法从旧数据判定归属的条目统一落到这里（MEM-016）。
#: 它不是账号、不出现在任何人的 self/subtree 列表里，只有根账号能通过
#: ``GET /v1/memory/unassigned`` 查看，避免"猜不出就归给根"的隐私错误。
_UNASSIGNED_OWNER = "__unassigned__"

#: 记忆写入的短窗口幂等：同内容重复提交在这段时间内只落一条。
MEMORY_IDEMPOTENCY_WINDOW_SECONDS = 60
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
CREATE TABLE IF NOT EXISTS account_memory_migrations (
    name         TEXT PRIMARY KEY,
    completed_at TEXT NOT NULL,
    details      TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS account_memory_quarantine (
    id           TEXT PRIMARY KEY,
    source       TEXT NOT NULL,
    source_ref   TEXT NOT NULL DEFAULT '',
    reason       TEXT NOT NULL,
    owner_hint   TEXT NOT NULL DEFAULT '',
    payload      TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_quarantine_reason
    ON account_memory_quarantine(reason);
"""


class MemoryWriteRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=8000)
    source: str = Field("user", max_length=64)
    tags: List[str] = Field(default_factory=list)

    @field_validator("text")
    @classmethod
    def _check_text(cls, value: str) -> str:
        return normalize_required_text(value, "记忆内容", max_chars=8000)

    @field_validator("source")
    @classmethod
    def _check_source(cls, value: str) -> str:
        if value:
            reject_unsafe_text(value, "来源")
        return value


class MemoryPatchRequest(BaseModel):
    """纠正一条记忆：只允许改正文/来源/标签，身份与归属不可改。"""

    text: Optional[str] = Field(None, min_length=1, max_length=8000)
    source: Optional[str] = Field(None, max_length=64)
    tags: Optional[List[str]] = None

    @field_validator("text")
    @classmethod
    def _check_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return normalize_required_text(value, "记忆内容", max_chars=8000)

    @field_validator("source")
    @classmethod
    def _check_source(cls, value: Optional[str]) -> Optional[str]:
        if value:
            reject_unsafe_text(value, "来源")
        return value


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


class UnassignedMemoryEntry(BaseModel):
    """一条归属无法判定的历史记忆（``owner="__unassigned__"``）。"""

    id: str
    account_id: str = Field(description="恒为 __unassigned__：不属于任何账号")
    text: str
    source: str
    tags: List[str] = Field(default_factory=list)
    created_at: str


class QuarantinedMemoryRowResponse(BaseModel):
    """迁移时被跳过、但原文仍保留在隔离区的一条源记录。"""

    id: str
    source: str
    source_ref: str
    reason: str = Field(
        description="non_memory_telemetry / empty_text / missing_entry_id / missing_key"
    )
    owner_hint: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    created_at: str


class UnassignedMemoryResponse(BaseModel):
    """``GET /v1/memory/unassigned``：未归属记忆 + 迁移隔离区（根账号专属）。"""

    entries: List[UnassignedMemoryEntry]
    count: int
    total: int
    limit: int
    offset: int
    has_more: bool
    quarantined: List[QuarantinedMemoryRowResponse]
    quarantined_count: int
    quarantined_total: int
    quarantine_limit: int
    quarantine_offset: int
    note: str


class MemoryDeletionReceipt(BaseModel):
    """根账号处置未归属/隔离记录的删除回执。"""

    id: str
    deleted: bool
    removed: int = Field(description="实际删除行数；0 表示没有可删的行")


class NeutralNotFound(BaseModel):
    """越权与"不存在"同形的 404 实体（不暴露资源是否存在）。"""

    code: str = Field(examples=[_accounts.CODE_MEMORY_FOREIGN_DENIED])
    message: str = Field(examples=[_accounts.NEUTRAL_NOT_FOUND_MESSAGE])
    detail: str = Field(examples=[_accounts.NEUTRAL_NOT_FOUND_MESSAGE])


_NOT_FOUND_RESPONSE: Dict[int, Dict[str, Any]] = {
    404: {
        "model": NeutralNotFound,
        "description": (
            "非根账号一律中性 404：与「这条记录不存在」逐字一致，"
            "不泄露未归属区/隔离区的存在。"
        ),
    }
}


def db_path() -> str:
    """账号记忆库落盘路径：``LIUHAO_ACCOUNT_MEMORY_DB`` > ``LIUHAO_WORKSPACE_ROOT``
    > ``LIUHAO_DATA_DIR`` > ``data/account_memory.sqlite3``。"""
    return resolve_store_path(
        "account_memory.sqlite3",
        os.path.join("data", "account_memory.sqlite3"),
        env_var=_ENV_DB,
        workspace=True,
    )


def _connect() -> sqlite3.Connection:
    """对外连接：首次访问时先做一次 0.1.0 记忆迁移（失败不阻断读写）。"""
    try:
        ensure_legacy_memory_migrated()
    except Exception:  # noqa: BLE001 - 迁移失败要可重试，但不能让记忆页整个 500
        logger.error("legacy memory migration failed; will retry", exc_info=True)
    return _raw_connect()


def _raw_connect() -> sqlite3.Connection:
    """不做迁移的连接（迁移自身与后台检查用，避免递归）。"""
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


def _escape_like(value: str) -> str:
    """把 LIKE 通配符转成字面量；先转义反斜杠自身，顺序不能颠倒。"""
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def _query_entries(
    owners: Iterable[str],
    query: str,
    *,
    limit: Optional[int] = None,
    offset: int = 0,
) -> List[Dict[str, Any]]:
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
        sql += (
            " AND (text LIKE ? ESCAPE '\\'"
            " OR source LIKE ? ESCAPE '\\'"
            " OR tags LIKE ? ESCAPE '\\')"
        )
        pattern = f"%{_escape_like(needle)}%"
        params.extend([pattern, pattern, pattern])
    sql += " ORDER BY created_at DESC, id DESC"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params.extend([max(0, int(limit)), max(0, int(offset))])
    with _connect() as connection:
        rows = connection.execute(sql, params).fetchall()
    return [_row_to_entry(row) for row in rows]


def _count_entries(owner: str) -> int:
    """某 owner 的条目总数（未归属区只有根账号会查）。"""
    with _connect() as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM account_memory_entries WHERE owner = ?",
            (str(owner),),
        ).fetchone()
    return int(row[0]) if row else 0


def _query_quarantine(limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    """迁移隔离区：跳过但**没有丢**的源行原文（只有根账号会读）。"""
    with _connect() as connection:
        rows = connection.execute(
            "SELECT id, source, source_ref, reason, owner_hint, payload, created_at "
            "FROM account_memory_quarantine "
            "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            (max(1, int(limit)), max(0, int(offset))),
        ).fetchall()
    items: List[Dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row[5] or "{}")
        except Exception:  # noqa: BLE001 - 原文损坏也要能看到这条隔离记录
            payload = {"raw": row[5]}
        items.append(
            {
                "id": row[0],
                "source": row[1],
                "source_ref": row[2],
                "reason": row[3],
                "owner_hint": row[4],
                "payload": payload,
                "created_at": row[6],
            }
        )
    return items


def _count_quarantine() -> int:
    with _connect() as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM account_memory_quarantine"
        ).fetchone()
    return int(row[0]) if row else 0


# ─── 员工记忆：内部写入 + 按员工读取（归属仍是账号） ──────────────
#
# 员工产生的记忆必须落在**员工所属账号**名下：``owner`` 就是归属账号，
# ``source='employee'`` 表明这条记忆由某个员工执行产生，``tags`` 里带
# ``employee:<stable_id>`` 以便按员工筛选（用稳定 id，改名不断线）。
# 读取时由员工接口先做可见性判断（主账号可见子树，子账号只见自己），
# 因此这里只按 owner + tag 精确取，不再做一次权限判断。

EMPLOYEE_SOURCE = "employee"


def employee_tag(employee_stable_id: str) -> str:
    return f"employee:{employee_stable_id}"


def record_entry_for_owner(
    owner: str,
    text: str,
    *,
    source: str = "user",
    tags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """把一条记忆写入指定账号（内部调用：归属由调用方从可信路径解析）。"""
    entry_id = str(uuid.uuid4())
    created_at = _now_iso()
    tag_list = [str(tag) for tag in (tags or [])]
    with _connect() as connection:
        connection.execute(
            "INSERT INTO account_memory_entries "
            "(id, owner, text, source, tags, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                entry_id,
                str(owner),
                str(text),
                str(source),
                json.dumps(tag_list, ensure_ascii=False),
                created_at,
            ),
        )
        connection.commit()
    return {
        "id": entry_id,
        "account_id": str(owner),
        "text": str(text),
        "source": str(source),
        "tags": tag_list,
        "created_at": created_at,
    }


def list_employee_entries(owner: str, stable_id: str) -> List[Dict[str, Any]]:
    """某位员工产生的记忆（只按 owner + 稳定 id 精确取）。"""
    wanted = employee_tag(stable_id)
    rows = _query_entries((str(owner),), "")
    return [
        entry
        for entry in rows
        if entry.get("source") == EMPLOYEE_SOURCE and wanted in (entry.get("tags") or [])
    ]


@router.get("/entries")
def list_entries(
    scope: str = Query("self", description="self 或 subtree"),
    q: str = Query("", description="关键词；匹配正文/来源/标签（按字面量，不解释通配符）"),
    limit: int = Query(100, ge=1, le=500, description="单页条数上限"),
    offset: int = Query(0, ge=0, le=1000000, description="分页偏移"),
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """列表与检索共用同一过滤：先按 owner 收敛，再做关键词匹配。

    ``q`` 里的 ``%`` / ``_`` 按字面量匹配（显式 ``ESCAPE '\\'``）；分页保证
    一次请求不可能吐出本人（或子树）的全部记忆。
    """
    owners = _visible_owners(context, scope)
    # 多取一条判断是否还有下一页，但绝不把它返回给调用方。
    page = _query_entries(owners, q, limit=limit + 1, offset=offset)
    has_more = len(page) > limit
    entries = page[:limit]
    return {
        "entries": entries,
        "scope": "subtree" if len(owners) > 1 else "self",
        "count": len(entries),
        "limit": limit,
        "offset": offset,
        "has_more": has_more,
    }


@router.get(
    "/unassigned",
    response_model=UnassignedMemoryResponse,
    responses=_NOT_FOUND_RESPONSE,
    summary="无归属历史记忆与迁移隔离区（仅主账号）",
)
def list_unassigned_entries(
    limit: int = Query(50, ge=1, le=500, description="单页条数上限"),
    offset: int = Query(0, ge=0, le=1000000, description="分页偏移"),
    quarantine_limit: int = Query(50, ge=1, le=500, description="隔离区单页条数上限"),
    quarantine_offset: int = Query(0, ge=0, le=1000000, description="隔离区分页偏移"),
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """迁移时无法判定归属的历史记忆 + 隔离区（MEM-016：仅根账号可查）。

    ``entries`` 是归属无法判定、但仍以 ``__unassigned__`` 存进新库的记录；
    ``quarantined`` 是迁移时**跳过**的源行原文（运行时遥测、空正文/空 id）。
    两者都不属于任何账号，普通 ``/entries`` 的 self/subtree 都不会返回；
    根账号在这里能看到条数与原文，据此人工确认归属后再决定补录或删除。
    非根账号与"不存在"同形（404），不泄露未归属区与隔离区的存在。
    """
    if not context.is_root:
        raise deny(
            404, _accounts.CODE_MEMORY_FOREIGN_DENIED, _accounts.NEUTRAL_NOT_FOUND_MESSAGE
        )
    page = _query_entries((_UNASSIGNED_OWNER,), "", limit=limit + 1, offset=offset)
    has_more = len(page) > limit
    entries = page[:limit]
    quarantined = _query_quarantine(quarantine_limit, quarantine_offset)
    return {
        "entries": entries,
        "count": len(entries),
        "total": _count_entries(_UNASSIGNED_OWNER),
        "limit": limit,
        "offset": offset,
        "has_more": has_more,
        "quarantined": quarantined,
        "quarantined_count": len(quarantined),
        "quarantined_total": _count_quarantine(),
        "quarantine_limit": quarantine_limit,
        "quarantine_offset": quarantine_offset,
        "note": (
            "entries 是迁移时无法从 key/tags 识别归属、但已存进新库的记录；"
            "quarantined 是迁移时跳过、但原始行已落到隔离区的记录"
            "（reason=non_memory_telemetry / empty_text / missing_entry_id / "
            "missing_key）。两者都不会出现在任何账号的记忆列表里，"
            "只有根账号能在这里查看。"
        ),
    }


@router.delete(
    "/unassigned/{entry_id}",
    response_model=MemoryDeletionReceipt,
    responses=_NOT_FOUND_RESPONSE,
    summary="删除一条无归属历史记忆（仅主账号）",
)
def delete_unassigned_entry(
    entry_id: str, context: AccountContext = Depends(require_account_principal)
) -> Dict[str, Any]:
    """删除一条无归属历史记忆（仅根账号；成员与"不存在"同为中性 404）。

    未归属区不属于任何账号，普通 ``DELETE /entries/{id}`` 对它一律 404；
    这个根账号专用入口让"看得见"能真的落到"处置得掉"。
    """
    if not context.is_root:
        raise deny(
            404, _accounts.CODE_MEMORY_FOREIGN_DENIED, _accounts.NEUTRAL_NOT_FOUND_MESSAGE
        )
    with _connect() as connection:
        row = connection.execute(
            "SELECT id, owner FROM account_memory_entries WHERE id = ?",
            (str(entry_id),),
        ).fetchone()
        if row is None or str(row["owner"]) != _UNASSIGNED_OWNER:
            raise deny(
                404, _accounts.CODE_MEMORY_FOREIGN_DENIED, _accounts.NEUTRAL_NOT_FOUND_MESSAGE
            )
        cursor = connection.execute(
            "DELETE FROM account_memory_entries WHERE id = ? AND owner = ?",
            (str(entry_id), _UNASSIGNED_OWNER),
        )
        connection.commit()
    removed = int(cursor.rowcount or 0)
    get_identity_manager().record_event(
        action="memory.unassigned.delete",
        actor=context.principal,
        target=_UNASSIGNED_OWNER,
        outcome="allowed",
        details={"entry_id": str(entry_id), "deleted": removed},
    )
    return {"id": str(entry_id), "deleted": True, "removed": removed}


@router.delete(
    "/quarantine/{quarantine_id}",
    response_model=MemoryDeletionReceipt,
    responses=_NOT_FOUND_RESPONSE,
    summary="删除一条迁移隔离记录（仅主账号）",
)
def delete_quarantined_row(
    quarantine_id: str, context: AccountContext = Depends(require_account_principal)
) -> Dict[str, Any]:
    """删除一条迁移隔离记录（仅根账号；成员与"不存在"同为中性 404）。

    隔离区保留的是迁移时跳过的源行原文；确认不 (再) 需要时由根账号删除。
    删除只影响隔离区本身，不会触碰已导入的记忆条目。
    """
    if not context.is_root:
        raise deny(
            404, _accounts.CODE_MEMORY_FOREIGN_DENIED, _accounts.NEUTRAL_NOT_FOUND_MESSAGE
        )
    with _connect() as connection:
        cursor = connection.execute(
            "DELETE FROM account_memory_quarantine WHERE id = ?", (str(quarantine_id),)
        )
        connection.commit()
    removed = int(cursor.rowcount or 0)
    if removed == 0:
        raise deny(
            404, _accounts.CODE_MEMORY_FOREIGN_DENIED, _accounts.NEUTRAL_NOT_FOUND_MESSAGE
        )
    get_identity_manager().record_event(
        action="memory.quarantine.delete",
        actor=context.principal,
        target=str(quarantine_id),
        outcome="allowed",
        details={"quarantine_id": str(quarantine_id), "deleted": removed},
    )
    return {"id": str(quarantine_id), "deleted": True, "removed": removed}


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
    """只写**自己**的记忆；主账号也不能借这个端点改写子账号记忆。

    幂等窗口：同一账号在 ``MEMORY_IDEMPOTENCY_WINDOW_SECONDS`` 内重复提交
    完全相同的内容（正文 / 来源 / 标签）时，返回已存在的那条并标记
    ``deduplicated: true`` —— 重试不会凭空多出一条记忆。
    """
    from src._time import utc_now

    cutoff = (
        utc_now() - timedelta(seconds=MEMORY_IDEMPOTENCY_WINDOW_SECONDS)
    ).isoformat()
    tags_json = json.dumps(payload.tags, ensure_ascii=False)
    with _connect() as connection:
        existing = connection.execute(
            "SELECT id, owner, text, source, tags, created_at "
            "FROM account_memory_entries "
            "WHERE owner = ? AND text = ? AND source = ? AND tags = ? "
            "AND created_at >= ? ORDER BY created_at DESC LIMIT 1",
            (
                context.principal,
                payload.text,
                payload.source,
                tags_json,
                cutoff,
            ),
        ).fetchone()
        if existing is not None:
            return {
                "entry": _row_to_entry(existing),
                "deduplicated": True,
            }
        entry_id = str(uuid.uuid4())
        created_at = _now_iso()
        connection.execute(
            "INSERT INTO account_memory_entries "
            "(id, owner, text, source, tags, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                entry_id,
                context.principal,
                payload.text,
                payload.source,
                tags_json,
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


@router.patch("/entries/{entry_id}")
def patch_entry(
    entry_id: str,
    payload: MemoryPatchRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """纠正**自己**的一条记忆（改正文/来源/标签），并写审计。

    归属不可改：主账号也不能借 PATCH 把条目改到子账号名下。不是本人一律
    404（与"不存在"不可区分）。
    """
    with _connect() as connection:
        row = connection.execute(
            "SELECT id, owner, text, source, tags, created_at "
            "FROM account_memory_entries WHERE id = ?",
            (str(entry_id),),
        ).fetchone()
        if row is None or str(row["owner"]) != str(context.principal):
            raise deny(
                404,
                _accounts.CODE_MEMORY_FOREIGN_DENIED,
                _accounts.NEUTRAL_NOT_FOUND_MESSAGE,
            )
        new_text = payload.text if payload.text is not None else row["text"]
        new_source = payload.source if payload.source is not None else row["source"]
        new_tags = (
            json.dumps(payload.tags, ensure_ascii=False)
            if payload.tags is not None
            else row["tags"]
        )
        if (
            payload.text is None
            and payload.source is None
            and payload.tags is None
        ):
            raise deny(400, "MEMORY_PATCH_EMPTY", "没有要修改的字段")
        connection.execute(
            "UPDATE account_memory_entries SET text = ?, source = ?, tags = ? "
            "WHERE id = ? AND owner = ?",
            (new_text, new_source, new_tags, str(entry_id), context.principal),
        )
        connection.commit()
    get_identity_manager().record_event(
        action="memory.update",
        actor=context.principal,
        target=context.principal,
        outcome="allowed",
        details={"entry_id": str(entry_id), "fields": sorted(payload.model_dump(exclude_none=True))},
    )
    with _connect() as connection:
        updated = connection.execute(
            "SELECT id, owner, text, source, tags, created_at "
            "FROM account_memory_entries WHERE id = ?",
            (str(entry_id),),
        ).fetchone()
    return {"entry": _row_to_entry(updated)}


@router.delete("/entries/{entry_id}")
def delete_entry(
    entry_id: str, context: AccountContext = Depends(require_account_principal)
) -> Dict[str, Any]:
    """删除**自己**的一条记忆，并清理同 owner 的 ``origin:<id>`` 派生副本。

    不是本人 404；删除后列表/检索/单条读取都不可见。删除动作写审计。
    """
    with _connect() as connection:
        row = connection.execute(
            "SELECT id, owner FROM account_memory_entries WHERE id = ?",
            (str(entry_id),),
        ).fetchone()
        if row is None or str(row["owner"]) != str(context.principal):
            raise deny(
                404,
                _accounts.CODE_MEMORY_FOREIGN_DENIED,
                _accounts.NEUTRAL_NOT_FOUND_MESSAGE,
            )
        cursor = connection.execute(
            "DELETE FROM account_memory_entries WHERE id = ? AND owner = ?",
            (str(entry_id), context.principal),
        )
        marker = '%"origin:' + str(entry_id) + '"%'
        derived = connection.execute(
            "DELETE FROM account_memory_entries WHERE owner = ? AND tags LIKE ?",
            (context.principal, marker),
        )
        connection.commit()
    deleted = int(cursor.rowcount or 0) + int(derived.rowcount or 0)
    get_identity_manager().record_event(
        action="memory.delete",
        actor=context.principal,
        target=context.principal,
        outcome="allowed",
        details={"entry_id": str(entry_id), "deleted": deleted},
    )
    return {"id": str(entry_id), "deleted": True, "removed": deleted}


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


# --------------------------------------------------------------------------- #
# 0.1.0 老记忆迁移（``memory_store.db`` / ``personal_context.db``）
# --------------------------------------------------------------------------- #
#
# 背景：0.1.0 的 Memory Kernel 把记忆写在 ``memory_store.db.memory_entries``，
# 画像写在 ``personal_context.db``；当前版本的 ``/v1/memory/*`` 只读
# ``account_memory_entries``。两套库之间此前没有任何迁移，导致升级后记忆页、
# 检索、导出都是 200 + 空列表（数据还在，但界面"看不见"）。
#
# 策略（幂等、可重试、不丢原库）：
# 1. 只读源库、只增目标行：``INSERT OR IGNORE``，主键沿用老 ``entry_id``；
# 2. 归属：老数据没有账号列，按 key/tags 里的账号前缀逐个核对；对得上的
#    保留原账号，**确实对不上的**落到 ``__unassigned__``（不是根账号本人），
#    只有根能通过 ``GET /v1/memory/unassigned`` 查看（``source`` 里标
#    ``legacy:memory_store``，``tags`` 里保留 tier/scope/key/原 TTL）；
# 3. TTL：**不静默删除**。已过期条目的 ``expires_at`` 刷新为"迁移时刻 +
#    ``LIUHAO_LEGACY_MEMORY_TTL_DAYS``（默认 30 天）"，并在 tags 里同时保留
#    ``legacy-expired-at:<原值>`` 与 ``legacy-ttl-refreshed``，原库不删行；
# 4. 迁移完成写 ``account_memory_migrations`` 标记；失败回滚并保持可重试，
#    源库文件始终原地保留；
# 5. 备份导入后以 ``force=True`` 再跑一次，使导入的内核记忆立即可读。
_MIGRATION_NAME = "legacy_kernel_memory_v1"
_LEGACY_TTL_DAYS_DEFAULT = 30
_migration_lock = threading.RLock()
_migration_done_paths: set[str] = set()
_migration_backoff: Dict[str, float] = {}

#: 迁移报告里 ``merged_into`` 明细的条数上限（避免一次升级把整库原文
#: 塞进报告；总数仍然精确）。
_MERGED_DETAIL_LIMIT = 50


@dataclass
class _CopyResult:
    """一个来源的逐行去向账：每一行都必须落进且只落进一个桶。

    * ``imported_rows``：新写进 ``account_memory_entries``；
    * ``merged_rows``：目标已有同 id 条目（重复迁移/重复源行），内容未变；
    * ``quarantined_rows``：未写进记忆表，但原文已落到隔离区；
    * ``lost_rows``：以上都不是 == 真丢了，迁移必须让它保持 0。
    """

    source_rows: int = 0
    imported_rows: int = 0
    merged_rows: int = 0
    quarantined_rows: int = 0
    merged_into: List[Dict[str, Any]] = field(default_factory=list)
    quarantine_ids: List[str] = field(default_factory=list)
    skip_reasons: Dict[str, int] = field(default_factory=dict)

    def merge(self, other: "_CopyResult") -> None:
        self.source_rows += other.source_rows
        self.imported_rows += other.imported_rows
        self.merged_rows += other.merged_rows
        self.quarantined_rows += other.quarantined_rows
        self.merged_into.extend(other.merged_into)
        self.quarantine_ids.extend(other.quarantine_ids)
        for reason, count in other.skip_reasons.items():
            self.skip_reasons[reason] = self.skip_reasons.get(reason, 0) + count

    def note_skip(self, reason: str, quarantine_id: str) -> None:
        self.quarantined_rows += 1
        self.quarantine_ids.append(quarantine_id)
        self.skip_reasons[reason] = self.skip_reasons.get(reason, 0) + 1

    @property
    def lost_rows(self) -> int:
        return self.source_rows - (
            self.imported_rows + self.merged_rows + self.quarantined_rows
        )


def _legacy_paths() -> Tuple[str, str]:
    """老内核记忆库与画像库的实际路径（与各自模块的 env/默认值一致）。"""
    from ..ai.personal_context import DEFAULT_DB_PATH as profile_default
    from ..kernels.memory.store import DEFAULT_DB_PATH as kernel_default

    kernel = os.environ.get("MEMORY_DB_PATH") or kernel_default
    profile = os.environ.get("PERSONAL_CONTEXT_DB_PATH") or profile_default
    return kernel, profile


def _legacy_sources_configured() -> bool:
    """只有显式配置了数据目录才迁移。

    安装版启动器 / Docker / 验收实例都会设置 ``MEMORY_DB_PATH`` 与
    ``PERSONAL_CONTEXT_DB_PATH``；开发态直接跑仓库根时两者未设，此时仓库根
    的 ``personal_context.db`` 属于开发者现场数据，不能因为跑测试/单测就被
    读进临时库里（否则测试会互相污染，也会把别人的画像当"老数据"迁移）。
    """
    return bool(
        (os.environ.get("MEMORY_DB_PATH") or "").strip()
        or (os.environ.get("PERSONAL_CONTEXT_DB_PATH") or "").strip()
    )


def _legacy_ttl_days() -> int:
    raw = (os.environ.get("LIUHAO_LEGACY_MEMORY_TTL_DAYS") or "").strip()
    try:
        days = int(raw) if raw else _LEGACY_TTL_DAYS_DEFAULT
    except ValueError:
        days = _LEGACY_TTL_DAYS_DEFAULT
    return max(1, days)


def _known_principals() -> List[str]:
    """当前账号树里的全部主体（用于把老行归到正确 owner）。"""
    try:
        manager = get_identity_manager()
        principals = [str(node.principal) for node in manager.list_accounts()]
    except Exception:  # noqa: BLE001 - 身份未就绪时退回人类身份
        principals = []
    try:
        principals.extend(
            str(getattr(identity, "principal", "") or "")
            for identity in get_identity_manager().list_identities()
        )
    except Exception:  # noqa: BLE001
        pass
    return [item for item in principals if item]


def _decode_value(raw: Any) -> str:
    """把老库里的 JSON 值还原成可检索文本（不丢结构）。"""
    if raw is None:
        return ""
    if not isinstance(raw, str):
        return str(raw)
    try:
        value = json.loads(raw)
    except Exception:  # noqa: BLE001 - 不是 JSON 就按原文字处理
        return raw
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("content", "text", "message", "value", "summary"):
            if key in value:
                return _decode_value(value[key])
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        return str(value)


def _parse_time(raw: Any) -> Optional[datetime]:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def _legacy_tags(
    *,
    origin: str,
    tier: str = "",
    scope: str = "",
    key: str = "",
    original_expires: str = "",
    effective_expires: str = "",
    extra: Iterable[str] = (),
) -> str:
    tags = {"legacy", f"origin:{origin}"}
    if tier:
        tags.add(f"tier:{tier}")
    if scope:
        tags.add(f"scope:{scope}")
    if key:
        tags.add(f"key:{key[:200]}")
    if original_expires:
        tags.add(f"legacy-expires-at:{original_expires}")
    if effective_expires:
        tags.add(f"expires-at:{effective_expires}")
    tags.update(str(item) for item in extra if item)
    return json.dumps(sorted(tags), ensure_ascii=False)


def _has_table(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _refresh_expired(connection: sqlite3.Connection, ttl_days: int) -> int:
    """把已过期的老条目续期而不是删除；原过期时间保留在 tags 里。"""
    rows = connection.execute(
        "SELECT key_hash, expires_at, tags FROM memory_entries "
        "WHERE expires_at IS NOT NULL AND expires_at != ''"
    ).fetchall()
    now = datetime.now(timezone.utc)
    refreshed = 0
    for row in rows:
        expires_at = _parse_time(row[1])
        if expires_at is None or expires_at > now:
            continue
        original = str(row[1])
        effective = (now + timedelta(days=ttl_days)).isoformat()
        try:
            tags = json.loads(row[2] or "[]")
            if not isinstance(tags, list):
                tags = []
        except Exception:  # noqa: BLE001
            tags = []
        for marker in ("legacy-ttl-refreshed", f"legacy-expired-at:{original}"):
            if marker not in tags:
                tags.append(marker)
        connection.execute(
            "UPDATE memory_entries SET expires_at = ?, tags = ? WHERE key_hash = ?",
            (effective, json.dumps(tags, ensure_ascii=False), row[0]),
        )
        refreshed += 1
    return refreshed


def _owner_for_memory(key: str, old_tags: List[str], known: List[str]) -> str:
    """从内核记忆的 key/tags 里认出它属于哪个账号；认不出就落到未归属区。

    内核库没有 owner 列，但写入方把账号编码进了 ``agent:liuhao-<principal>-<session>``
    标签与 ``turn:liuhao-<principal>-...`` key。过去一律记到根，会让成员会话
    在导入/首次迁移后出现在根账号的记忆页；这里按已知账号逐个对前缀。
    **只有确实对不上任何账号时才返回 ``__unassigned__``**：猜不出归属不是
    根的错，不能把成员私密记忆塞进根的子树聚合（MEM-016）。
    """
    ordered = sorted((item for item in known if item), key=len, reverse=True)
    for tag in old_tags:
        if not tag.startswith("agent:"):
            continue
        agent = tag[len("agent:"):]
        if not agent.startswith("liuhao-"):
            continue
        rest = agent[len("liuhao-"):]
        for principal in ordered:
            if rest == principal or rest.startswith(principal + "-"):
                return principal
    if key.startswith("turn:liuhao-"):
        rest = key[len("turn:liuhao-"):]
        for principal in ordered:
            if rest.startswith(principal + "-"):
                return principal
    return _UNASSIGNED_OWNER


#: 只留在内核检索库、不搬进"记忆页"的机器标签。
#: ``conversation`` 是会话轮次；其余是 agent_runtime / 规划器写入的遥测。
_NON_MEMORY_KERNEL_TAGS = frozenset(
    {"conversation", "agent_run", "episodic", "semantic", "learning",
     "goal", "decomposition", "task"}
)


def _quarantine_id(source: str, rowid: Any, source_ref: str, reason: str) -> str:
    """隔离记录 id：同一源行、同一原因在任何一次重跑里都得到同一个 id。"""
    raw = f"{source}|{rowid}|{source_ref}|{reason}"
    return f"q-{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:20]}"


def _quarantine_row(
    target: sqlite3.Connection,
    *,
    quarantine_id: str,
    source: str,
    source_ref: str,
    reason: str,
    owner_hint: str,
    payload: Dict[str, Any],
    created_at: str,
) -> None:
    """把一条**没有进入记忆表**的源行原样写进隔离区（同库、可查询、幂等）。

    这是 MEM-016 的关键补口：以前"跳过"只 `continue`，新库查不到、也没有
    任何地方能证明原文还在；现在跳过之前先落盘，迁移报告里的每个"跳过"
    都能在这里找到对应原文与原因。
    """
    target.execute(
        "INSERT OR IGNORE INTO account_memory_quarantine "
        "(id, source, source_ref, reason, owner_hint, payload, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            quarantine_id,
            source,
            source_ref,
            reason,
            owner_hint,
            json.dumps(payload, ensure_ascii=False, default=str),
            created_at,
        ),
    )


def _copy_kernel_entries(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    known: Optional[List[str]] = None,
) -> _CopyResult:
    """把 ``memory_entries`` 逐行写进 ``account_memory_entries``（主键沿用）。

    每条源行都有明确去向：
    * 正常行 -> 新库（认不出归属的落 ``__unassigned__``）；
    * 运行时遥测 / 空正文 / 空 id -> 隔离区，原文与原因都可查（不再静默丢）；
    * 同 id 已存在 -> 记为 ``merged``，并给出目标条目 id 与最终正文。
    """
    known = known if known is not None else []
    rows = source.execute(
        "SELECT rowid, entry_id, key, value, tier, scope, created_at, expires_at, tags "
        "FROM memory_entries"
    ).fetchall()
    result = _CopyResult(source_rows=len(rows))
    now_iso = _now_iso()
    for row in rows:
        rowid = row[0]
        entry_id = str(row[1] or "")
        key = str(row[2] or "")
        raw_value = row[3]
        text = _decode_value(raw_value)
        old_tags: List[str] = []
        try:
            decoded = json.loads(row[8] or "[]")
            if isinstance(decoded, list):
                old_tags = [str(item) for item in decoded]
        except Exception:  # noqa: BLE001
            old_tags = []
        payload = {
            "entry_id": entry_id,
            "key": key,
            "value": raw_value,
            "tier": str(row[4] or ""),
            "scope": str(row[5] or ""),
            "created_at": str(row[6] or ""),
            "expires_at": str(row[7] or ""),
            "tags": old_tags,
        }
        owner_hint = _owner_for_memory(key, old_tags, known)
        if owner_hint == _UNASSIGNED_OWNER:
            owner_hint = ""
        if not entry_id or not text:
            # 异常格式：没有主键或正文为空。以前直接 continue，换机后这条
            # 记录在新库和任何界面都不存在；现在先落隔离区再跳过。
            reason = "missing_entry_id" if not entry_id else "empty_text"
            qid = _quarantine_id("memory_store", rowid, entry_id or key, reason)
            _quarantine_row(
                target,
                quarantine_id=qid,
                source="memory_store",
                source_ref=entry_id or key,
                reason=reason,
                owner_hint=owner_hint,
                payload=payload,
                created_at=str(row[6] or "") or now_iso,
            )
            result.note_skip(reason, qid)
            continue
        # 内核库里的机器记录不是"用户的记忆"：会话轮次、目标分解、任务执行、
        # 目标运行/学习回执都属于运行时遥测。它们没有账号归属（key 里只有
        # goal/task id），硬搬进来会全部挂到根账号，换机后根的记忆页会突然
        # 多出成员目标的失败回执——既污染计数，也泄露归属。
        # 但"不进记忆页"不等于"可以消失"：原文进隔离区，根账号可查。
        if _NON_MEMORY_KERNEL_TAGS & set(old_tags):
            reason = "non_memory_telemetry"
            qid = _quarantine_id("memory_store", rowid, entry_id, reason)
            _quarantine_row(
                target,
                quarantine_id=qid,
                source="memory_store",
                source_ref=entry_id,
                reason=reason,
                owner_hint=owner_hint,
                payload=payload,
                created_at=str(row[6] or "") or now_iso,
            )
            result.note_skip(reason, qid)
            continue
        entry_owner = _owner_for_memory(key, old_tags, known)
        tags = _legacy_tags(
            origin="memory_store",
            tier=str(row[4] or ""),
            scope=str(row[5] or ""),
            key=key,
            original_expires=str(row[7] or ""),
            effective_expires=str(row[7] or ""),
            extra=old_tags,
        )
        created_at = str(row[6] or "") or now_iso
        cursor = target.execute(
            "INSERT OR IGNORE INTO account_memory_entries "
            "(id, owner, text, source, tags, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (entry_id, entry_owner, text, "legacy:memory_store", tags, created_at),
        )
        if cursor.rowcount:
            result.imported_rows += 1
            continue
        # 同 id 已存在：重复迁移/重复源行，不是丢失。把目标条目的最终正文
        # 写进报告，证明它是"合并进已有条目"而不是被丢弃。
        existing = target.execute(
            "SELECT text FROM account_memory_entries WHERE id = ?", (entry_id,)
        ).fetchone()
        result.merged_rows += 1
        if len(result.merged_into) < _MERGED_DETAIL_LIMIT:
            result.merged_into.append(
                {
                    "source": "memory_store",
                    "source_ref": entry_id,
                    "entry_id": entry_id,
                    "final_text": str(existing[0]) if existing else text,
                }
            )
    return result


_PERSONAL_CONTEXT_SOURCES: Dict[str, Tuple[str, str]] = {
    "profile_preferences": ("preference", "value_json"),
    "profile_facts": ("fact", "value_json"),
}


def _owner_for_profile(raw: str, known: List[str]) -> str:
    """画像库主体是 ``user-{principal}``；能对上账号树就保留原归属。

    对不上任何账号时落到未归属区：旧画像可能是成员私密偏好，不能默认归根。
    """
    candidates = [raw]
    if raw.startswith("user-"):
        candidates.append(raw[len("user-"):])
    if raw in known:
        return raw
    for candidate in candidates:
        if candidate in known:
            return candidate
    return _UNASSIGNED_OWNER


def _copy_personal_context(
    source: sqlite3.Connection, target: sqlite3.Connection
) -> _CopyResult:
    """画像偏好/事实 -> 记忆条目（id 由来源确定性生成，天然幂等）。

    空 key / 空正文不再静默跳过：原文进隔离区，原因标为 ``missing_key`` /
    ``empty_text``；同 id 已存在记 ``merged`` 并给出目标条目最终正文。
    """
    known = _known_principals()
    result = _CopyResult()
    now_iso = _now_iso()
    for table, (kind, value_column) in _PERSONAL_CONTEXT_SOURCES.items():
        try:
            rows = source.execute(
                f"SELECT rowid, principal_id, key, {value_column}, updated_at "
                f"FROM {table}"
            ).fetchall()
        except sqlite3.Error:
            continue
        result.source_rows += len(rows)
        for row in rows:
            rowid = row[0]
            principal_id = str(row[1] or "")
            key = str(row[2] or "")
            raw_value = row[3]
            text = _decode_value(raw_value)
            payload = {
                "table": table,
                "principal_id": principal_id,
                "key": key,
                "value": raw_value,
                "updated_at": row[4],
                "kind": kind,
            }
            if not key or not text:
                reason = "missing_key" if not key else "empty_text"
                owner_hint = _owner_for_profile(principal_id, known)
                if owner_hint == _UNASSIGNED_OWNER:
                    owner_hint = ""
                qid = _quarantine_id(
                    "personal_context", rowid, f"{table}:{key}", reason
                )
                _quarantine_row(
                    target,
                    quarantine_id=qid,
                    source="personal_context",
                    source_ref=f"{table}:{key or principal_id}",
                    reason=reason,
                    owner_hint=owner_hint,
                    payload=payload,
                    created_at=now_iso,
                )
                result.note_skip(reason, qid)
                continue
            owner = _owner_for_profile(principal_id, known)
            digest = hashlib.sha1(
                f"{principal_id}|{kind}|{key}".encode("utf-8")
            ).hexdigest()[:20]
            entry_id = f"legacy-pc-{digest}"
            try:
                created_at = datetime.fromtimestamp(
                    float(row[4]), tz=timezone.utc
                ).isoformat()
            except (TypeError, ValueError, OSError):
                created_at = now_iso
            tags = _legacy_tags(
                origin="personal_context",
                key=key,
                extra=[f"kind:{kind}"],
            )
            final_text = f"{key}: {text}"
            cursor = target.execute(
                "INSERT OR IGNORE INTO account_memory_entries "
                "(id, owner, text, source, tags, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    entry_id,
                    owner,
                    final_text,
                    "legacy:personal_context",
                    tags,
                    created_at,
                ),
            )
            if cursor.rowcount:
                result.imported_rows += 1
                continue
            existing = target.execute(
                "SELECT text FROM account_memory_entries WHERE id = ?", (entry_id,)
            ).fetchone()
            result.merged_rows += 1
            if len(result.merged_into) < _MERGED_DETAIL_LIMIT:
                result.merged_into.append(
                    {
                        "source": "personal_context",
                        "source_ref": f"{table}:{key}",
                        "entry_id": entry_id,
                        "final_text": str(existing[0]) if existing else final_text,
                    }
                )
    return result


def ensure_legacy_memory_migrated(*, force: bool = False) -> Dict[str, Any]:
    """执行一次 0.1.0 -> 当前版本的记忆迁移（幂等、失败可重试）。

    返回 ``{"ran": bool, ...计数}``。``force=True`` 忽略完成标记，用于备份
    导入后立即把带过来的内核记忆映射进账号记忆表。
    """
    global _migration_done_paths, _migration_backoff
    with _migration_lock:
        path = db_path()
        if not force:
            # 不再用"进程内已迁移过"提前返回：内核库可能在首次访问之后才
            # 写入（会话收尾、目标跑完），提前返回会让这些记忆永久不可见。
            if time.monotonic() < _migration_backoff.get(path, 0.0):
                return {"ran": False, "backoff": True}

        kernel_path, profile_path = _legacy_paths()
        if not _legacy_sources_configured():
            # 不缓存"现在还没有旧库"：会话/目标写入会晚于首次访问才建库，
            # 一旦缓存就永远看不到这些记忆（换机拷贝目录后尤其明显）。
            return {"ran": False, "reason": "legacy_store_not_configured"}
        has_kernel = os.path.exists(kernel_path)
        has_profile = os.path.exists(profile_path)
        if not has_kernel and not has_profile:
            return {"ran": False, "reason": "no_legacy_store"}

        marker = _raw_connect()
        try:
            row = marker.execute(
                "SELECT completed_at, details FROM account_memory_migrations "
                "WHERE name = ?",
                (_MIGRATION_NAME,),
            ).fetchone()
        finally:
            marker.close()
        if row is not None and not force:
            try:
                details = json.loads(row[1] or "{}")
            except Exception:  # noqa: BLE001
                details = {}
            # 一次性标记会让"迁移之后才写进内核库"的记忆永远看不见
            # （会话收尾、目标跑完都晚于首次访问）。按内核行数判断是否真有新料。
            kernel_rows = None
            if has_kernel:
                try:
                    probe = sqlite3.connect(kernel_path, timeout=10)
                    try:
                        if _has_table(probe, "memory_entries"):
                            kernel_rows = int(
                                probe.execute(
                                    "SELECT COUNT(*) FROM memory_entries"
                                ).fetchone()[0]
                            )
                    finally:
                        probe.close()
                except sqlite3.Error:
                    kernel_rows = None
            if kernel_rows is not None and kernel_rows <= int(
                details.get("kernel_entries", -1)
            ):
                _migration_done_paths.add(path)
                return {"ran": False, "already": True, "details": details}

        report: Dict[str, Any] = {
            "ran": True,
            "ttl_refreshed": 0,
            "sources": {
                "memory_store": kernel_path if has_kernel else "",
                "personal_context": profile_path if has_profile else "",
            },
        }
        result = _CopyResult()
        target = _raw_connect()
        try:
            if has_kernel:
                source = sqlite3.connect(kernel_path, timeout=10)
                try:
                    if _has_table(source, "memory_entries"):
                        report["ttl_refreshed"] = _refresh_expired(
                            source, _legacy_ttl_days()
                        )
                        source.commit()
                        kernel_result = _copy_kernel_entries(
                            source, target, _known_principals()
                        )
                        result.merge(kernel_result)
                        report["entries"] = kernel_result.imported_rows
                        report["kernel_entries"] = int(
                            source.execute(
                                "SELECT COUNT(*) FROM memory_entries"
                            ).fetchone()[0]
                        )
                finally:
                    source.close()
            if has_profile:
                profile = sqlite3.connect(profile_path, timeout=10)
                try:
                    profile_result = _copy_personal_context(profile, target)
                    result.merge(profile_result)
                    report["profile_entries"] = profile_result.imported_rows
                finally:
                    profile.close()
            # 逐行去向账：源行数 = 导入 + 合并进已有条目 + 隔离区 + 真丢失。
            # ``count_conserved`` 只看"导入 + 跳过 == 源"，账平了但跳过可能是
            # 丢弃；这里改成 ``rows_accounted`` + ``lost_rows``，并单列
            # ``merged_rows`` / ``quarantined_rows`` / ``skip_reasons``。
            report["source_rows"] = result.source_rows
            report["imported_rows"] = result.imported_rows
            report["merged_rows"] = result.merged_rows
            report["quarantined_rows"] = result.quarantined_rows
            report["lost_rows"] = result.lost_rows
            report["rows_accounted"] = result.source_rows == (
                result.imported_rows
                + result.merged_rows
                + result.quarantined_rows
            )
            report["skip_reasons"] = dict(result.skip_reasons)
            report["merged_into"] = list(result.merged_into)
            report["merged_into_truncated"] = (
                result.merged_rows > len(result.merged_into)
            )
            # 目标侧真实计数：记忆表里的迁移行 + 隔离区里的源行。
            report["target_legacy_rows"] = int(
                target.execute(
                    "SELECT COUNT(*) FROM account_memory_entries "
                    "WHERE source LIKE 'legacy:%'"
                ).fetchone()[0]
            )
            report["quarantine_rows"] = int(
                target.execute(
                    "SELECT COUNT(*) FROM account_memory_quarantine"
                ).fetchone()[0]
            )
            report["preserved_rows"] = (
                report["target_legacy_rows"] + report["quarantine_rows"]
            )
            # 无法判定归属、落到 __unassigned__ 的行数（根账号可查详情）。
            report["unassigned_rows"] = int(
                target.execute(
                    "SELECT COUNT(*) FROM account_memory_entries "
                    "WHERE owner = ? AND source LIKE 'legacy:%'",
                    (_UNASSIGNED_OWNER,),
                ).fetchone()[0]
            )
            target.execute(
                "INSERT OR REPLACE INTO account_memory_migrations "
                "(name, completed_at, details) VALUES (?, ?, ?)",
                (
                    _MIGRATION_NAME,
                    _now_iso(),
                    json.dumps(report, ensure_ascii=False),
                ),
            )
            target.commit()
        except Exception:
            target.rollback()
            _migration_backoff[path] = time.monotonic() + 30
            raise
        finally:
            target.close()

        _migration_done_paths.add(path)
        logger.info(
            "legacy memory migrated: source_rows=%s imported=%s merged=%s "
            "quarantined=%s lost=%s ttl_refreshed=%s profile_imported=%s "
            "unassigned=%s",
            report.get("source_rows"),
            report.get("imported_rows"),
            report.get("merged_rows"),
            report.get("quarantined_rows"),
            report.get("lost_rows"),
            report["ttl_refreshed"],
            report.get("profile_entries"),
            report.get("unassigned_rows"),
        )
        return report
