"""出站白名单（Owner 专属管理端点）。

设计稿 net-guard-smart §4.2：Owner 白名单存在 ``data/net_guard_allowlist.json``
（数据目录由 ``LIUHAO_DATA_DIR`` 决定，路径可用 ``LIUHAO_NET_GUARD_ALLOWLIST_FILE``
覆盖），只 Owner 能改，上限 50 条，可设 ``expires_at`` 过期，改完不用重启。

本模块只做管理面：校验 + 落盘 + 审计 + 刷新守卫缓存。放行判定本身在
``src/ai/net_guard.py``（第 1 层身份）。前端设置页的 Owner 专区由 C 组接。
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from src.common.atomic_write import atomic_write_text, exclusive_file_lock
from ..ai.net_guard import (
    ALLOWLIST_MAX_ENTRIES,
    TRUSTED_VENDOR_HOSTS,
    allowlist_path,
    load_owner_allowlist,
    refresh_owner_allowlist,
    validate_allowlist_entry,
)
from .account_context import AccountContext, deny
from .owner import _audit_owner, require_owner

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/owner/net-guard", tags=["owner"])

_lock = threading.Lock()

CODE_ALLOWLIST_INVALID = "ALLOWLIST_INVALID"
CODE_ALLOWLIST_DUPLICATE = "ALLOWLIST_DUPLICATE"
CODE_ALLOWLIST_LIMIT = "ALLOWLIST_LIMIT"
CODE_ALLOWLIST_NOT_FOUND = "ALLOWLIST_NOT_FOUND"


class AllowlistEntryIn(BaseModel):
    """一条 Owner 白名单条目（写接口输入）。"""

    host: str = Field(..., description="单个主机名，如 api.example-vendor.com")
    label: str = Field(..., description="这是什么端点")
    reason: str = Field(..., description="为什么要加")
    expires_at: Optional[str] = Field(
        default=None, description="到期时间（YYYY-MM-DD 或 ISO 时间），空为永不过期"
    )


class AllowlistReplaceIn(BaseModel):
    """整表替换（写接口输入）。"""

    entries: List[AllowlistEntryIn] = Field(default_factory=list)


def _save(entries: List[Dict[str, Optional[str]]], principal: str) -> None:
    """原子落盘整张白名单表（临时文件 + os.replace，不留半截 JSON）。"""
    path = allowlist_path()
    payload = {
        "version": 1,
        "updated_at": datetime.now().astimezone().isoformat(),
        "updated_by": principal,
        "entries": entries,
    }
    with _lock:
        with exclusive_file_lock(f"{path}.lock", description="出站白名单"):
            atomic_write_text(
                path,
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            )
    refresh_owner_allowlist()


def _audit(action: str, context: AccountContext, target: str, outcome: str, details: Dict[str, Any]) -> bool:
    written = _audit_owner(action, context.principal, target, outcome, details)
    return written


@router.get("/allowlist")
def get_allowlist(context: AccountContext = Depends(require_owner)) -> Dict[str, Any]:
    """Owner 读白名单：条目 + 内置厂商列表（前端展示用）。"""
    _hosts, entries = load_owner_allowlist()
    return {
        "entries": entries,
        "count": len(entries),
        "max_entries": ALLOWLIST_MAX_ENTRIES,
        "builtin_hosts": sorted(TRUSTED_VENDOR_HOSTS),
        "file": allowlist_path(),
    }


@router.post("/allowlist", status_code=201)
def add_allowlist_entry(
    body: AllowlistEntryIn, context: AccountContext = Depends(require_owner)
) -> Dict[str, Any]:
    """Owner 加一条白名单；写后立即生效（守卫缓存已刷新）。"""
    normalized, error = validate_allowlist_entry(body.model_dump())
    if error is not None or normalized is None:
        raise deny(400, CODE_ALLOWLIST_INVALID, error or "条目不合法")
    _hosts, entries = load_owner_allowlist()
    if any(item["host"] == normalized["host"] for item in entries):
        raise deny(400, CODE_ALLOWLIST_DUPLICATE, "该域名已在白名单里")
    if len(entries) >= ALLOWLIST_MAX_ENTRIES:
        raise deny(
            400,
            CODE_ALLOWLIST_LIMIT,
            f"白名单最多 {ALLOWLIST_MAX_ENTRIES} 条，请先删掉不用的条目",
        )
    entries.append(normalized)
    _save(entries, context.principal)
    written = _audit(
        "net_guard.allowlist.add",
        context,
        normalized["host"],
        "allowed",
        {"host": normalized["host"], "label": normalized["label"], "reason": normalized["reason"]},
    )
    return {"entry": normalized, "count": len(entries), "audit_written": written}


@router.put("/allowlist")
def replace_allowlist(
    body: AllowlistReplaceIn, context: AccountContext = Depends(require_owner)
) -> Dict[str, Any]:
    """Owner 整表替换白名单（前端“保存”一次提交）。"""
    if len(body.entries) > ALLOWLIST_MAX_ENTRIES:
        raise deny(
            400,
            CODE_ALLOWLIST_LIMIT,
            f"白名单最多 {ALLOWLIST_MAX_ENTRIES} 条，请先删掉不用的条目",
        )
    normalized_entries: List[Dict[str, Optional[str]]] = []
    seen = set()
    for item in body.entries:
        normalized, error = validate_allowlist_entry(item.model_dump())
        if error is not None or normalized is None:
            raise deny(400, CODE_ALLOWLIST_INVALID, error or "条目不合法")
        if normalized["host"] in seen:
            raise deny(400, CODE_ALLOWLIST_DUPLICATE, f"重复的域名：{normalized['host']}")
        seen.add(normalized["host"])
        normalized_entries.append(normalized)
    _save(normalized_entries, context.principal)
    written = _audit(
        "net_guard.allowlist.replace",
        context,
        "allowlist",
        "allowed",
        {"count": len(normalized_entries)},
    )
    return {"entries": normalized_entries, "count": len(normalized_entries), "audit_written": written}


@router.delete("/allowlist/{host}")
def delete_allowlist_entry(
    host: str, context: AccountContext = Depends(require_owner)
) -> Dict[str, Any]:
    """Owner 删一条白名单；删除也留审计痕迹。"""
    from ..ai.net_guard import _normalize_host

    normalized_host = _normalize_host(host)
    _hosts, entries = load_owner_allowlist()
    kept = [item for item in entries if item["host"] != normalized_host]
    if len(kept) == len(entries):
        raise deny(404, CODE_ALLOWLIST_NOT_FOUND, "该域名不在白名单里")
    _save(kept, context.principal)
    written = _audit(
        "net_guard.allowlist.remove",
        context,
        normalized_host,
        "allowed",
        {"host": normalized_host},
    )
    return {"removed": normalized_host, "count": len(kept), "audit_written": written}
