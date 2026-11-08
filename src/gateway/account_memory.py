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
from datetime import datetime, timedelta, timezone
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
CREATE TABLE IF NOT EXISTS account_memory_migrations (
    name         TEXT PRIMARY KEY,
    completed_at TEXT NOT NULL,
    details      TEXT NOT NULL DEFAULT ''
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
# 2. 归属：老数据没有账号列，统一挂到当前账号树的根（``source`` 里标
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


def _root_principal() -> str:
    try:
        node = get_identity_manager().account_root()
        if node is not None:
            return str(node.principal)
    except Exception:  # noqa: BLE001
        pass
    humans = _known_principals()
    return humans[0] if humans else "liuhao"


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


#: 只留在内核检索库、不搬进"记忆页"的机器标签。
#: ``conversation`` 是会话轮次；其余是 agent_runtime / 规划器写入的遥测。
_NON_MEMORY_KERNEL_TAGS = frozenset(
    {"conversation", "agent_run", "episodic", "semantic", "learning",
     "goal", "decomposition", "task"}
)


def _copy_kernel_entries(
    source: sqlite3.Connection, target: sqlite3.Connection, owner: str
) -> Tuple[int, int]:
    """把 ``memory_entries`` 逐行写进 ``account_memory_entries``（主键沿用）。"""
    rows = source.execute(
        "SELECT entry_id, key, value, tier, scope, created_at, expires_at, tags "
        "FROM memory_entries"
    ).fetchall()
    imported = 0
    skipped = 0
    now_iso = _now_iso()
    for row in rows:
        entry_id = str(row[0] or "")
        text = _decode_value(row[2])
        if not entry_id or not text:
            skipped += 1
            continue
        old_tags: List[str] = []
        try:
            decoded = json.loads(row[7] or "[]")
            if isinstance(decoded, list):
                old_tags = [str(item) for item in decoded]
        except Exception:  # noqa: BLE001
            old_tags = []
        # 内核库里的机器记录不是"用户的记忆"：会话轮次、目标分解、任务执行、
        # 目标运行/学习回执都属于运行时遥测。它们没有账号归属（key 里只有
        # goal/task id），硬搬进来会全部挂到根账号，换机后根的记忆页会突然
        # 多出成员目标的失败回执——既污染计数，也泄露归属。
        if _NON_MEMORY_KERNEL_TAGS & set(old_tags):
            skipped += 1
            continue
        tags = _legacy_tags(
            origin="memory_store",
            tier=str(row[3] or ""),
            scope=str(row[4] or ""),
            key=str(row[1] or ""),
            original_expires=str(row[6] or ""),
            effective_expires=str(row[6] or ""),
            extra=old_tags,
        )
        created_at = str(row[5] or "") or now_iso
        cursor = target.execute(
            "INSERT OR IGNORE INTO account_memory_entries "
            "(id, owner, text, source, tags, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (entry_id, owner, text, "legacy:memory_store", tags, created_at),
        )
        if cursor.rowcount:
            imported += 1
        else:
            skipped += 1
    return imported, skipped


_PERSONAL_CONTEXT_SOURCES: Dict[str, Tuple[str, str]] = {
    "profile_preferences": ("preference", "value_json"),
    "profile_facts": ("fact", "value_json"),
}


def _owner_for_profile(raw: str, known: List[str], root: str) -> str:
    """画像库主体是 ``user-{principal}``；能对上账号树就保留原归属。"""
    candidates = [raw]
    if raw.startswith("user-"):
        candidates.append(raw[len("user-"):])
    if raw in known:
        return raw
    for candidate in candidates:
        if candidate in known:
            return candidate
    return root


def _copy_personal_context(
    source: sqlite3.Connection, target: sqlite3.Connection, root: str
) -> Tuple[int, int]:
    """画像偏好/事实 -> 记忆条目（id 由来源确定性生成，天然幂等）。"""
    known = _known_principals()
    imported = 0
    skipped = 0
    now_iso = _now_iso()
    for table, (kind, value_column) in _PERSONAL_CONTEXT_SOURCES.items():
        try:
            rows = source.execute(
                f"SELECT principal_id, key, {value_column}, updated_at FROM {table}"
            ).fetchall()
        except sqlite3.Error:
            continue
        for row in rows:
            principal_id = str(row[0] or "")
            key = str(row[1] or "")
            text = _decode_value(row[2])
            if not key or not text:
                skipped += 1
                continue
            owner = _owner_for_profile(principal_id, known, root)
            digest = hashlib.sha1(
                f"{principal_id}|{kind}|{key}".encode("utf-8")
            ).hexdigest()[:20]
            entry_id = f"legacy-pc-{digest}"
            try:
                created_at = datetime.fromtimestamp(
                    float(row[3]), tz=timezone.utc
                ).isoformat()
            except (TypeError, ValueError, OSError):
                created_at = now_iso
            tags = _legacy_tags(
                origin="personal_context",
                key=key,
                extra=[f"kind:{kind}"],
            )
            cursor = target.execute(
                "INSERT OR IGNORE INTO account_memory_entries "
                "(id, owner, text, source, tags, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    entry_id,
                    owner,
                    f"{key}: {text}",
                    "legacy:personal_context",
                    tags,
                    created_at,
                ),
            )
            if cursor.rowcount:
                imported += 1
            else:
                skipped += 1
    return imported, skipped


def ensure_legacy_memory_migrated(*, force: bool = False) -> Dict[str, Any]:
    """执行一次 0.1.0 -> 当前版本的记忆迁移（幂等、失败可重试）。

    返回 ``{"ran": bool, ...计数}``。``force=True`` 忽略完成标记，用于备份
    导入后立即把带过来的内核记忆映射进账号记忆表。
    """
    global _migration_done_paths, _migration_backoff
    with _migration_lock:
        path = db_path()
        if not force:
            if path in _migration_done_paths:
                return {"ran": False, "already": True}
            if time.monotonic() < _migration_backoff.get(path, 0.0):
                return {"ran": False, "backoff": True}

        kernel_path, profile_path = _legacy_paths()
        if not _legacy_sources_configured():
            _migration_done_paths.add(path)
            return {"ran": False, "reason": "legacy_store_not_configured"}
        has_kernel = os.path.exists(kernel_path)
        has_profile = os.path.exists(profile_path)
        if not has_kernel and not has_profile:
            _migration_done_paths.add(path)
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
            _migration_done_paths.add(path)
            try:
                details = json.loads(row[1] or "{}")
            except Exception:  # noqa: BLE001
                details = {}
            return {"ran": False, "already": True, "details": details}

        report: Dict[str, Any] = {
            "ran": True,
            "entries": 0,
            "skipped": 0,
            "ttl_refreshed": 0,
            "profile_entries": 0,
            "profile_skipped": 0,
            "sources": {
                "memory_store": kernel_path if has_kernel else "",
                "personal_context": profile_path if has_profile else "",
            },
        }
        target = _raw_connect()
        try:
            root = _root_principal()
            if has_kernel:
                source = sqlite3.connect(kernel_path, timeout=10)
                try:
                    if _has_table(source, "memory_entries"):
                        report["ttl_refreshed"] = _refresh_expired(
                            source, _legacy_ttl_days()
                        )
                        source.commit()
                        imported, skipped = _copy_kernel_entries(source, target, root)
                        report["entries"] += imported
                        report["skipped"] += skipped
                finally:
                    source.close()
            if has_profile:
                profile = sqlite3.connect(profile_path, timeout=10)
                try:
                    imported, skipped = _copy_personal_context(profile, target, root)
                    report["profile_entries"] += imported
                    report["profile_skipped"] += skipped
                finally:
                    profile.close()
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
            "legacy memory migrated: entries=%s skipped=%s ttl_refreshed=%s profile=%s",
            report["entries"],
            report["skipped"],
            report["ttl_refreshed"],
            report["profile_entries"],
        )
        return report
