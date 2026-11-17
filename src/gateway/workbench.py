"""工作台 Bot / 分组 / 群聊 / 置顶的网关接口（``/v1/workbench``）。

跟协作空间（``spaces.py``）同一套可见性规则：按账号隔离，Owner
（root）看到整棵子树，成员只看到自己的；看不见的当不存在，统一
返回 404，不区分“没有”和“无权”。前端 Workbench 改走这套接口，
localStorage 只留作离线缓存，首次把已有的本地数据迁移一次进后端。

端点一览：
- Bot：``GET/POST /bots``、``GET/PATCH/DELETE /bots/{id}``、
  ``POST /bots/reorder``、``POST/GET/DELETE /bots/{id}/avatar``（传图做头像）
- 分组：``GET/POST /groups``、``PATCH/DELETE /groups/{id}``、
  ``POST /groups/reorder``（删组后组里的 Bot 回到未分组）
- 群聊：``GET/POST /group-chats``、``GET/PATCH /group-chats/{id}``、
  ``POST /group-chats/{id}/members``、``DELETE /group-chats/{id}/members/{bot_id}``、
  ``POST /group-chats/{id}/archive|restore|leave|rejoin``、
  ``DELETE /group-chats/{id}``（解散）
- ``POST /migrate``：浏览器本地快照一次性并入后端（幂等，updated_at 新的赢）
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Sequence

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..common.input_guard import normalize_required_text, reject_unsafe_text
from ..workbench.store import (
    JARVIS_ID,
    MAX_AVATAR_BYTES,
    avatar_media_type,
    get_workbench_store,
    validate_avatar_bytes,
    _safe_avatar_stem,
)
from .account_context import AccountContext, require_account_principal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/workbench", tags=["workbench"])


def _principals(context: AccountContext) -> List[str]:
    return list(context.subtree) if context.subtree else [context.principal]


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="找不到该内容")


class BotCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    job: str = Field(default="", max_length=200)
    personality: str = Field(default="", max_length=500)
    idea: str = Field(default="", max_length=500)
    direction: str = Field(default="", max_length=500)
    mood: str = Field(default="", max_length=100)
    gender: str = Field(default="", max_length=20)
    color: str = Field(default="", max_length=32)
    accessory: str = Field(default="", max_length=64)
    model_id: str = Field(default="", max_length=128)
    group_id: str = Field(default="", max_length=64)
    pinned: bool = False
    hidden: bool = False
    routines: List[str] = Field(default_factory=list)


class BotPatchRequest(BaseModel):
    name: Optional[str] = Field(default=None, max_length=64)
    job: Optional[str] = Field(default=None, max_length=200)
    personality: Optional[str] = Field(default=None, max_length=500)
    idea: Optional[str] = Field(default=None, max_length=500)
    direction: Optional[str] = Field(default=None, max_length=500)
    mood: Optional[str] = Field(default=None, max_length=100)
    gender: Optional[str] = Field(default=None, max_length=20)
    color: Optional[str] = Field(default=None, max_length=32)
    accessory: Optional[str] = Field(default=None, max_length=64)
    model_id: Optional[str] = Field(default=None, max_length=128)
    group_id: Optional[str] = Field(default=None, max_length=64)
    pinned: Optional[bool] = None
    hidden: Optional[bool] = None
    routines: Optional[List[str]] = None


class GroupCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class GroupPatchRequest(BaseModel):
    name: Optional[str] = Field(default=None, max_length=64)
    folded: Optional[bool] = None


class GroupChatCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    member_bot_ids: List[str] = Field(default_factory=list)


class GroupChatPatchRequest(BaseModel):
    name: Optional[str] = Field(default=None, max_length=64)


class MemberAddRequest(BaseModel):
    bot_id: str = Field(min_length=1, max_length=64)


class ReorderRequest(BaseModel):
    ids: List[str] = Field(default_factory=list)


class JarvisRoutinesRequest(BaseModel):
    routines: List[str] = Field(default_factory=list)


class MigrateRequest(BaseModel):
    bots: List[Dict[str, Any]] = Field(default_factory=list)
    groups: List[Dict[str, Any]] = Field(default_factory=list)
    group_chats: List[Dict[str, Any]] = Field(default_factory=list)


def _clean_optional_text(value: Optional[str], label: str, max_chars: int) -> Optional[str]:
    if value is None:
        return None
    try:
        reject_unsafe_text(value, label)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if len(value) > max_chars:
        raise HTTPException(
            status_code=422, detail=f"{label}过长，最多允许 {max_chars} 个字符。"
        )
    return value


# -- Bot ---------------------------------------------------------------


@router.get("/bots")
def list_bots(
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    bots = get_workbench_store().list_bots(_principals(context))
    return {"bots": bots, "count": len(bots)}


@router.post("/bots", status_code=201)
def create_bot(
    body: BotCreateRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    try:
        name = normalize_required_text(body.name, "Bot 名字", max_chars=64)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    fields = body.model_dump()
    fields["name"] = name
    bot = get_workbench_store().create_bot(context.principal, fields)
    return {"bot": bot}


@router.get("/bots/{bot_id}")
def get_bot(
    bot_id: str,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    bot = get_workbench_store().get_bot(_principals(context), bot_id)
    if bot is None:
        raise _not_found()
    return {"bot": bot}


@router.patch("/bots/{bot_id}")
def update_bot(
    bot_id: str,
    body: BotPatchRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    patch = body.model_dump(exclude_unset=True)
    if "name" in patch and patch["name"] is not None:
        try:
            patch["name"] = normalize_required_text(
                patch["name"], "Bot 名字", max_chars=64
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    for key, label, limit in (
        ("job", "职责", 200),
        ("personality", "性格", 500),
        ("idea", "想法", 500),
        ("direction", "方向", 500),
        ("mood", "心情", 100),
        ("gender", "性别", 20),
        ("color", "配色", 32),
        ("accessory", "配件", 64),
        ("model_id", "模型", 128),
        ("group_id", "分组", 64),
    ):
        if key in patch:
            patch[key] = _clean_optional_text(patch[key], label, limit)
    bot = get_workbench_store().update_bot(_principals(context), bot_id, patch)
    if bot is None:
        raise _not_found()
    return {"bot": bot}


@router.delete("/bots/{bot_id}")
def delete_bot(
    bot_id: str,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    if bot_id == JARVIS_ID:
        raise HTTPException(status_code=400, detail="贾维斯本人不能删除。")
    if not get_workbench_store().delete_bot(_principals(context), bot_id):
        raise _not_found()
    return {"ok": True}


@router.post("/bots/reorder")
def reorder_bots(
    body: ReorderRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    get_workbench_store().reorder_bots(_principals(context), list(body.ids))
    return {"ok": True}


# -- Bot 头像传图 ------------------------------------------------------
# 传自己的图片做头像：配色+饰件（小铁抠图微调）留着，传图是多一个选择，
# 不是取代——没传图时头像照旧是配色+饰件的样子，传了图就显示那张图，
# 删了图就回到配色+饰件。文件只认魔数（PNG/JPG/GIF/WebP）+ 2MB 上限，
# 不合规返回 422 人话原因，不悄悄吞掉；头像文件跟 workbench.db 同一个
# 数据目录（avatars/），备份走数据目录整份备份，头像跟着备份走。


@router.post("/bots/{bot_id}/avatar")
async def upload_bot_avatar(
    bot_id: str,
    file: UploadFile = File(...),
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    store = get_workbench_store()
    bot = store.get_bot(_principals(context), bot_id)
    if bot is None:
        raise _not_found()
    # 只读上限+1 字节：超了就知道超了，又不把几个 G 的文件读进内存。
    content = await file.read(MAX_AVATAR_BYTES + 1)
    try:
        extension = validate_avatar_bytes(content)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    directory = store.avatar_dir()
    directory.mkdir(parents=True, exist_ok=True)
    stem = _safe_avatar_stem(bot_id)
    # 先写临时文件再改名：写一半崩了也不会留一张坏图顶掉旧头像。
    tmp_path = directory / f".{stem}.tmp-{os.getpid()}"
    final_path = directory / f"{stem}.{extension}"
    try:
        tmp_path.write_bytes(content)
        os.replace(tmp_path, final_path)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
    # 换了格式时旧格式的文件删掉，一个 Bot 只留一张头像。
    for old in store.avatar_dir().glob(f"{stem}.*"):
        if old.name != final_path.name:
            try:
                old.unlink()
            except OSError:
                pass
    updated = store.set_bot_avatar(_principals(context), bot_id)
    if updated is None:
        raise _not_found()
    return {"bot": updated}


@router.get("/bots/{bot_id}/avatar")
def get_bot_avatar(
    bot_id: str,
    context: AccountContext = Depends(require_account_principal),
) -> FileResponse:
    store = get_workbench_store()
    bot = store.get_bot(_principals(context), bot_id)
    if bot is None or not bot.get("avatar_url"):
        raise _not_found()
    path = store.find_bot_avatar_file(bot_id)
    if path is None:
        raise _not_found()
    return FileResponse(path, media_type=avatar_media_type(path.suffix.lstrip(".")))


@router.delete("/bots/{bot_id}/avatar")
def delete_bot_avatar(
    bot_id: str,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    store = get_workbench_store()
    bot = store.clear_bot_avatar(_principals(context), bot_id)
    if bot is None:
        raise _not_found()
    return {"bot": bot}


# -- 分组 --------------------------------------------------------------


@router.get("/groups")
def list_groups(
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    groups = get_workbench_store().list_groups(_principals(context))
    return {"groups": groups, "count": len(groups)}


@router.post("/groups", status_code=201)
def create_group(
    body: GroupCreateRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    try:
        name = normalize_required_text(body.name, "分组名字", max_chars=64)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    group = get_workbench_store().create_group(context.principal, name)
    return {"group": group}


@router.patch("/groups/{group_id}")
def update_group(
    group_id: str,
    body: GroupPatchRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    patch = body.model_dump(exclude_unset=True)
    if "name" in patch and patch["name"] is not None:
        try:
            patch["name"] = normalize_required_text(
                patch["name"], "分组名字", max_chars=64
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    group = get_workbench_store().update_group(
        _principals(context), group_id, patch
    )
    if group is None:
        raise _not_found()
    return {"group": group}


@router.delete("/groups/{group_id}")
def delete_group(
    group_id: str,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    if not get_workbench_store().delete_group(_principals(context), group_id):
        raise _not_found()
    return {"ok": True}


@router.post("/groups/reorder")
def reorder_groups(
    body: ReorderRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    get_workbench_store().reorder_groups(_principals(context), list(body.ids))
    return {"ok": True}


# -- 群聊 --------------------------------------------------------------


@router.get("/group-chats")
def list_group_chats(
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    chats = get_workbench_store().list_group_chats(_principals(context))
    return {"group_chats": chats, "count": len(chats)}


@router.post("/group-chats", status_code=201)
def create_group_chat(
    body: GroupChatCreateRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    try:
        name = normalize_required_text(body.name, "群聊名字", max_chars=64)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    member_ids = [
        bot_id for bot_id in dict.fromkeys(body.member_bot_ids)
        if bot_id and bot_id != JARVIS_ID
    ]
    if len(member_ids) < 1:
        raise HTTPException(
            status_code=422, detail="群聊至少要有一个 Bot 参加。"
        )
    chat = get_workbench_store().create_group_chat(
        context.principal, name, member_ids
    )
    return {"group_chat": chat}


@router.get("/group-chats/{chat_id}")
def get_group_chat(
    chat_id: str,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    chat = get_workbench_store().get_group_chat(_principals(context), chat_id)
    if chat is None:
        raise _not_found()
    return {"group_chat": chat}


@router.patch("/group-chats/{chat_id}")
def update_group_chat(
    chat_id: str,
    body: GroupChatPatchRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    patch = body.model_dump(exclude_unset=True)
    if "name" in patch and patch["name"] is not None:
        try:
            patch["name"] = normalize_required_text(
                patch["name"], "群聊名字", max_chars=64
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    chat = get_workbench_store().update_group_chat(
        _principals(context), chat_id, patch
    )
    if chat is None:
        raise _not_found()
    return {"group_chat": chat}


@router.post("/group-chats/{chat_id}/members", status_code=201)
def add_group_chat_member(
    chat_id: str,
    body: MemberAddRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    chat = get_workbench_store().add_group_chat_member(
        _principals(context), chat_id, body.bot_id
    )
    if chat is None:
        raise _not_found()
    return {"group_chat": chat}


@router.delete("/group-chats/{chat_id}/members/{bot_id}")
def remove_group_chat_member(
    chat_id: str,
    bot_id: str,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    chat = get_workbench_store().remove_group_chat_member(
        _principals(context), chat_id, bot_id
    )
    if chat is None:
        raise _not_found()
    return {"group_chat": chat}


@router.post("/group-chats/{chat_id}/archive")
def archive_group_chat(
    chat_id: str,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    chat = get_workbench_store().update_group_chat(
        _principals(context), chat_id, {"archived": True}
    )
    if chat is None:
        raise _not_found()
    return {"group_chat": chat}


@router.post("/group-chats/{chat_id}/restore")
def restore_group_chat(
    chat_id: str,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    chat = get_workbench_store().update_group_chat(
        _principals(context), chat_id, {"archived": False}
    )
    if chat is None:
        raise _not_found()
    return {"group_chat": chat}


@router.post("/group-chats/{chat_id}/leave")
def leave_group_chat(
    chat_id: str,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    chat = get_workbench_store().update_group_chat(
        _principals(context), chat_id, {"left": True}
    )
    if chat is None:
        raise _not_found()
    return {"group_chat": chat}


@router.post("/group-chats/{chat_id}/rejoin")
def rejoin_group_chat(
    chat_id: str,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    chat = get_workbench_store().update_group_chat(
        _principals(context), chat_id, {"left": False}
    )
    if chat is None:
        raise _not_found()
    return {"group_chat": chat}


@router.delete("/group-chats/{chat_id}")
def dissolve_group_chat(
    chat_id: str,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    if not get_workbench_store().delete_group_chat(
        _principals(context), chat_id
    ):
        raise _not_found()
    return {"ok": True}


# -- 贾维斯本人教过的例程 --------------------------------------------
# 贾维斯不是 Bot 行（不进 bots 表），教过的例程单开接口按账号存，
# 跟 Bot 的 routines 一样：教一次变按钮，下次点一下就行。
#
# 光变按钮还不够：教的是一句带时间的真任务（比如“每天早上 8 点提醒我
# 看邮箱”），只存字符串就永远不会跑——点按钮只是把原话再发进聊天，
# 没配模型时连这一步都走不通。所以保存时顺手把新教的、能解析出时间的
# 句子排成真提醒（复用提醒服务，跟模型调提醒工具同一条路：同一张表、
# 同一个排程器，到点一样会响）；解析不出的只留按钮，不猜、不反问、
# 不让教这一步失败。删掉一句教过的例程时，顺手取消由它排上的提醒
# （按原文 source_text 认领，只动这一句带来的那条）。


def _schedule_taught_reminders(
    principal: str, texts: Sequence[str]
) -> List[Dict[str, Any]]:
    """把新教的句子里能解析出时间的排成真提醒；排不上的只留按钮。"""
    if not texts:
        return []
    try:
        from ..tasks.reminder_time import NeedsClarification
        from ..tasks.reminders import get_reminder_service

        service = get_reminder_service()
    except Exception:  # noqa: BLE001 - 提醒服务起不来也不许拖垮教这一步
        logger.warning("teach: reminder service unavailable", exc_info=True)
        return []
    try:
        active = service.store.list(principal, status="active")
    except Exception:  # noqa: BLE001 - 去重查不到就按没排过处理
        active = []
    have = {str(r.get("source_text") or "") for r in active}
    scheduled: List[Dict[str, Any]] = []
    for text in texts:
        if text in have:
            continue  # 同一句话已经排过一次了，不重复建
        try:
            created = service.create(principal, text)
        except NeedsClarification:
            # 时间说不全（比如“每周五汇总本周花销”没几点）：只留按钮，
            # 等用户点按钮再跟贾维斯说清楚，不在这里反问。
            continue
        except ValueError:
            # 不是一句定时提醒：只留按钮。
            continue
        except Exception:  # noqa: BLE001 - 排程失败不许拖垮教这一步
            logger.warning("teach: schedule reminder failed: %r", text, exc_info=True)
            continue
        have.add(text)
        scheduled.append(
            {
                "reminder_id": created["id"],
                "text": text,
                "title": created["title"],
                "human_time": created["human_time"],
                "time_text": created.get("time_text") or "",
                "repeat": created["repeat"],
                "run_at_local": created["run_at_local"],
            }
        )
    return scheduled


def _cancel_removed_taught_reminders(
    principal: str, texts: Sequence[str]
) -> List[Dict[str, Any]]:
    """删掉的教过的例程：取消由它排上的提醒（按原文认领）。"""
    if not texts:
        return []
    try:
        from ..tasks.reminders import get_reminder_service

        service = get_reminder_service()
        active = service.store.list(principal, status="active")
    except Exception:  # noqa: BLE001 - 取消失败不许拖垮保存这一步
        logger.warning("teach: cancel removed reminders failed", exc_info=True)
        return []
    cancelled: List[Dict[str, Any]] = []
    for text in texts:
        for record in active:
            if str(record.get("source_text") or "") != text:
                continue
            try:
                result = service.cancel(principal, str(record["id"]))
            except Exception:  # noqa: BLE001 - 单条取消失败继续下一条
                logger.warning(
                    "teach: cancel reminder failed: %r", record.get("id"), exc_info=True
                )
                continue
            if result.get("status") == "cancelled":
                cancelled.append(
                    {"reminder_id": str(record["id"]), "text": text, "title": str(record.get("title") or "")}
                )
    return cancelled


@router.get("/jarvis-routines")
def list_jarvis_routines(
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    routines = get_workbench_store().get_jarvis_routines(context.principal)
    return {"routines": routines, "count": len(routines)}


@router.put("/jarvis-routines")
def save_jarvis_routines(
    body: JarvisRoutinesRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    store = get_workbench_store()
    old = store.get_jarvis_routines(context.principal)
    routines = store.set_jarvis_routines(context.principal, body.routines)
    added = [r for r in routines if r not in old]
    removed = [r for r in old if r not in routines]
    scheduled = _schedule_taught_reminders(context.principal, added)
    cancelled = _cancel_removed_taught_reminders(context.principal, removed)
    return {
        "routines": routines,
        "count": len(routines),
        "scheduled": scheduled,
        "cancelled_reminders": cancelled,
    }


# -- 迁移 --------------------------------------------------------------


@router.post("/migrate")
def migrate_snapshot(
    body: MigrateRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    counts = get_workbench_store().migrate_snapshot(
        context.principal,
        {
            "bots": body.bots,
            "groups": body.groups,
            "group_chats": body.group_chats,
        },
    )
    return {"migrated": counts}
