"""主机命令白名单（Owner 专属管理端点）。

设计稿 anti-induction §1.3：白名单存在 ``data/host_command_allowlist.json``
（数据目录，``LIUHAO_HOST_COMMAND_ALLOWLIST_FILE`` 可覆盖路径），出厂为空，
只 Owner 能改；改动需二次确认（``confirm=true``），写后记审计（谁、何时、
改了什么），每次保存留一份带时间戳的旧版本（只留最近 5 份），可一键回滚。
改完下一次命令即生效（broker 对文件型能力目录做 mtime 热重载），不用重启。

前端设置页的白名单编辑（改前确认框、回滚按钮）由 C 组接。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from ..ai.host_command.allowlist import (
    ALLOWLIST_MAX_ENTRIES,
    ALLOWLIST_MAX_VERSIONS,
    allowlist_path,
    list_versions,
    load_allowlist,
    rollback_allowlist,
    save_allowlist,
    validate_allowlist_entry,
)
from .account_context import AccountContext, deny
from .owner import _audit_owner, require_owner

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/owner/host-command", tags=["owner"])

CODE_ALLOWLIST_INVALID = "ALLOWLIST_INVALID"
CODE_ALLOWLIST_DUPLICATE = "ALLOWLIST_DUPLICATE"
CODE_ALLOWLIST_LIMIT = "ALLOWLIST_LIMIT"
CODE_ALLOWLIST_NOT_FOUND = "ALLOWLIST_NOT_FOUND"
CODE_CONFIRM_REQUIRED = "CONFIRM_REQUIRED"


class AllowlistEntryIn(BaseModel):
    """一条白名单条目（写接口输入；字段含义见 allowlist.py）。"""

    model_config = ConfigDict(extra="forbid")

    capability_id: str = Field(..., description="条目 id，如 git-status")
    description: str = Field(default="", description="这条是干什么的")
    program: str = Field(..., description="程序名，如 git / notepad")
    # 已退役字段：出现（非空）即报错，绝不能静默丢掉——丢掉等于接受。
    command_glob: Optional[str] = Field(
        default=None, description="已退役的整串 fnmatch，出现即报错"
    )
    subcommands: Optional[List[str]] = Field(
        default=None, description="允许的子命令，如 [status, pull]；空为任意"
    )
    arg_patterns: Optional[List[str]] = Field(
        default=None, description="逐位参数形状；空为不许带尾随参数"
    )
    forbidden_args: Optional[List[str]] = Field(
        default=None, description="禁用的危险开关，如 [-f, --force]"
    )
    allow_shell: bool = Field(default=False, description="是否允许 use_shell")
    allowed_cwds: Optional[List[str]] = Field(
        default=None, description="允许的 cwd 根目录"
    )
    max_seconds: Optional[float] = Field(default=None, description="最长跑多久（秒）")
    adapter: Optional[str] = Field(default=None, description="限定 adapter")
    require_shell: Optional[bool] = Field(default=None, description="是否必须用 shell")
    requires_approval: bool = Field(
        default=False, description="即使匹配也要先批后执行"
    )


class AllowlistReplaceIn(BaseModel):
    """整表替换（写接口输入）+ 二次确认。"""

    model_config = ConfigDict(extra="forbid")

    entries: List[AllowlistEntryIn] = Field(default_factory=list)
    confirm: bool = Field(
        default=False, description="二次确认：点了确认框才为 true"
    )


class AllowlistAddIn(AllowlistEntryIn):
    """加一条：条目字段 + 二次确认。"""

    model_config = ConfigDict(extra="forbid")

    confirm: bool = Field(
        default=False, description="二次确认：点了确认框才为 true"
    )


class AllowlistRollbackIn(BaseModel):
    """回滚到某一旧版本 + 二次确认。"""

    model_config = ConfigDict(extra="forbid")

    version: str = Field(..., description="旧版本文件名，见 GET /versions")
    confirm: bool = Field(
        default=False, description="二次确认：点了确认框才为 true"
    )


def _require_confirm(confirmed: bool) -> None:
    if not confirmed:
        raise deny(
            400,
            CODE_CONFIRM_REQUIRED,
            "改白名单要二次确认：请先在确认框里看清新增/删除了哪些条目，再点确认",
        )


def _audit(
    action: str, context: AccountContext, target: str, outcome: str, details: Dict[str, Any]
) -> bool:
    return _audit_owner(action, context.principal, target, outcome, details)


def _validate_all(
    items: List[AllowlistEntryIn],
) -> List[Dict[str, Any]]:
    normalized_entries: List[Dict[str, Any]] = []
    seen = set()
    for item in items:
        normalized, error = validate_allowlist_entry(item.model_dump())
        if error is not None or normalized is None:
            raise deny(400, CODE_ALLOWLIST_INVALID, error or "条目不合法")
        if normalized["capability_id"] in seen:
            raise deny(
                400,
                CODE_ALLOWLIST_DUPLICATE,
                f"重复的 capability_id：{normalized['capability_id']}",
            )
        seen.add(normalized["capability_id"])
        normalized_entries.append(normalized)
    return normalized_entries


@router.get("/allowlist")
def get_allowlist(context: AccountContext = Depends(require_owner)) -> Dict[str, Any]:
    """Owner 读白名单：条目 + 旧版本清单 + 文件位置。"""
    entries, damaged = load_allowlist()
    return {
        "entries": entries,
        "count": len(entries),
        "max_entries": ALLOWLIST_MAX_ENTRIES,
        "max_versions": ALLOWLIST_MAX_VERSIONS,
        "versions": list_versions(),
        "file": allowlist_path(),
        "damaged": damaged,
    }


@router.get("/allowlist/versions")
def get_allowlist_versions(
    context: AccountContext = Depends(require_owner),
) -> Dict[str, Any]:
    """Owner 读旧版本清单（回滚用）。"""
    return {"versions": list_versions(), "max_versions": ALLOWLIST_MAX_VERSIONS}


@router.post("/allowlist", status_code=201)
def add_allowlist_entry(
    body: AllowlistAddIn, context: AccountContext = Depends(require_owner)
) -> Dict[str, Any]:
    """Owner 加一条白名单；需二次确认；写后下一次命令即生效。"""
    _require_confirm(body.confirm)
    normalized, error = validate_allowlist_entry(
        body.model_dump(exclude={"confirm"})
    )
    if error is not None or normalized is None:
        raise deny(400, CODE_ALLOWLIST_INVALID, error or "条目不合法")
    entries, _damaged = load_allowlist()
    if any(item["capability_id"] == normalized["capability_id"] for item in entries):
        raise deny(
            400,
            CODE_ALLOWLIST_DUPLICATE,
            f"capability_id 已存在：{normalized['capability_id']}",
        )
    if len(entries) >= ALLOWLIST_MAX_ENTRIES:
        raise deny(
            400,
            CODE_ALLOWLIST_LIMIT,
            f"白名单最多 {ALLOWLIST_MAX_ENTRIES} 条，请先删掉不用的条目",
        )
    entries.append(normalized)
    saved = save_allowlist(entries, principal=context.principal)
    written = _audit(
        "host_command.allowlist.add",
        context,
        normalized["capability_id"],
        "allowed",
        {
            "capability_id": normalized["capability_id"],
            "program": normalized["program"],
            "subcommands": normalized["subcommands"],
            "allow_shell": normalized["allow_shell"],
        },
    )
    return {
        "entry": normalized,
        "count": len(entries),
        "backup_version": saved["backup_version"],
        "audit_written": written,
    }


@router.put("/allowlist")
def replace_allowlist(
    body: AllowlistReplaceIn, context: AccountContext = Depends(require_owner)
) -> Dict[str, Any]:
    """Owner 整表替换白名单（前端“保存”一次提交）；需二次确认。"""
    _require_confirm(body.confirm)
    if len(body.entries) > ALLOWLIST_MAX_ENTRIES:
        raise deny(
            400,
            CODE_ALLOWLIST_LIMIT,
            f"白名单最多 {ALLOWLIST_MAX_ENTRIES} 条，请先删掉不用的条目",
        )
    normalized_entries = _validate_all(body.entries)
    old_entries, _damaged = load_allowlist()
    old_ids = {item["capability_id"] for item in old_entries}
    new_ids = {item["capability_id"] for item in normalized_entries}
    saved = save_allowlist(normalized_entries, principal=context.principal)
    written = _audit(
        "host_command.allowlist.replace",
        context,
        "allowlist",
        "allowed",
        {
            "count": len(normalized_entries),
            "added": sorted(new_ids - old_ids),
            "removed": sorted(old_ids - new_ids),
        },
    )
    return {
        "entries": normalized_entries,
        "count": len(normalized_entries),
        "added": sorted(new_ids - old_ids),
        "removed": sorted(old_ids - new_ids),
        "backup_version": saved["backup_version"],
        "audit_written": written,
    }


@router.delete("/allowlist/{capability_id}")
def delete_allowlist_entry(
    capability_id: str,
    confirm: bool = False,
    context: AccountContext = Depends(require_owner),
) -> Dict[str, Any]:
    """Owner 删一条白名单；需二次确认（confirm=true 查询参数）；删除留审计。"""
    _require_confirm(confirm)
    entries, _damaged = load_allowlist()
    kept = [item for item in entries if item["capability_id"] != capability_id]
    if len(kept) == len(entries):
        raise deny(404, CODE_ALLOWLIST_NOT_FOUND, "该条目不在白名单里")
    saved = save_allowlist(kept, principal=context.principal)
    written = _audit(
        "host_command.allowlist.remove",
        context,
        capability_id,
        "allowed",
        {"capability_id": capability_id},
    )
    return {
        "removed": capability_id,
        "count": len(kept),
        "backup_version": saved["backup_version"],
        "audit_written": written,
    }


@router.post("/allowlist/rollback")
def rollback_allowlist_endpoint(
    body: AllowlistRollbackIn, context: AccountContext = Depends(require_owner)
) -> Dict[str, Any]:
    """Owner 一键回到某一旧版本；需二次确认；回滚也留审计。"""
    _require_confirm(body.confirm)
    try:
        entries = rollback_allowlist(body.version, principal=context.principal)
    except FileNotFoundError:
        raise deny(404, CODE_ALLOWLIST_NOT_FOUND, f"旧版本不存在：{body.version}")
    except ValueError as exc:
        raise deny(400, CODE_ALLOWLIST_INVALID, str(exc))
    written = _audit(
        "host_command.allowlist.rollback",
        context,
        body.version,
        "allowed",
        {"version": body.version, "count": len(entries)},
    )
    return {
        "entries": entries,
        "count": len(entries),
        "version": body.version,
        "audit_written": written,
    }
