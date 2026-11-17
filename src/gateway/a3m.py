"""A3m：主/子账号规矩的后端（Joseph 2026-11-14 ~09:13 亲口定，09:14、09:15、
09:26 逐条补齐）。

本模块集中实现主/子账号规矩的后端能力（B 组随后做界面），与 ``a2a.py`` /
``a2b.py`` / ``a3s.py`` 同款做法：新增的小状态统一存在一份
``a3m_state.json``（``LIUHAO_A3M_STATE_FILE`` 可覆盖，默认走数据根目录），
写时用 ``atomic_write_text`` + 文件锁；凡是“没配好、没接通、没数据”的
情况一律返回老实空状态，绝不编数字。

十六条（与任务书一一对应）：

1. 主账号跨账号只读：Owner 专用的跨账号只读接口（聊天、任务、文件、知识
   库、Bot、用量、记忆、技能），按子账号查；子账号调一律 403。
2. Key 各用各的：调用只用调用者自己的 Key。兜底已堵死——
   ``account_context.key_scope_for`` 的根视角不再含任何成员 Key（能用的
   没有，可见的也没有：列表/详情里不再出现成员的模型配置），
   ``kernels.identity._accounts.can_borrow_key`` 只认自己（见两处注释）。
   成员“配没配 Key”这个管理信息走本模块的 Owner 汇总（只给 bool）。
   没配 Key 就明确返回“没配 Key”，用量按账号分开记（复用
   ``account_usage``），任何返回给子账号的响应里不得出现别人的 Key
   （哪怕掩码也不行，本模块只给“已配/未配”状态）。
3. 记忆与技能单向吸取：只有主账号能吸取子账号的记忆与技能；主账号自动
   吸收，不用逐条确认；吸来的每条标明来源是哪个子账号，仅主账号可见；
   主账号能在成员管理里单独关掉某一个人的吸取（关了就不再吸那个人新
   的，之前吸来的留着并标明来源）。子账号之间、子账号看主账号的吸取/
   读取接口不存在（调了就 403）。
4. 主账号的贾维斯能操控所有子账号的贾维斯和他们建的 Bot：看状态、派活、
   暂停、收回结果；操控在子账号那边完全不可见——不通知、不留痕，派活记
   录只出现在主账号这边。派活以主账号自己的名义下 goal（``owner_account_id``
   = 主账号），子账号自己的目标列表里看不到这条 goal；Bot 的忙闲状态本身
   是它自己的状态，不属于派活记录。
5. 子账号看不到的东西后端真拦住：全局总览、成员管理、别人的聊天/任务/
   文件/知识库、主账号的数据、账单与用量总账、系统设置、别人的 Key。
   现有接口的过滤情况见 ``docs/a3m-account-filter-audit.md``（逐个过了一
   遍）；本模块的新接口一律先认调用者是谁，再决定能看谁的数据，子账号
   越权一律 403（不返回空列表假装没有）。
6. 注册改成自己注册＋主账号批准：不要邀请码了。账号加 ``pending_approval``
   状态；注册接口只建“待批准”账号（见 ``accounts.py`` 的 self-register，
   不发令牌）；批准/停用/删除接口只接受主账号；停用＝锁登录、数据留着；
   删除＝进回收站、30 天后真删（本模块的 ``account_trash`` 登记 +
   ``purge_expired_trash``；审计链永不删，工作区文件留着，见函数注释）。
   A2a 邀请链接入口已关（``a2a.py`` 三个端点返回 410，底层函数保留）。
7. 子账号之间完全隔离：每个查询都按账号过滤；协作空间成员可见性按账号
   过滤（``spaces.py`` 已按 ``_principals`` 过滤，成员只看得到自己在的
   空间；本模块的跨账号读只走主账号）。
8. 子账号的助手不叫贾维斯，默认名叫小灵；子账号自己能在设置里改名。
   头像默认非小铁（红金小铁只属于主账号的贾维斯），可改。本模块只留字
   段和接口，头像文件本身是 B 组/前端的事。
9. 主账号的“等你确认”收件箱每条写明是哪个子账号、哪个 Bot。
10. 子账号不设任何上限：Bot 数量、存储、并发任务、用量花费都不封顶；
    主账号自己的总账单上限仍由他自己设（A2a/A3s 已有，见用量策略端点）。
11. 子账号离开：走之前能一键导出自己的东西（复用 A3s 通用格式导出，
    不带 Key、不含别人的数据）。
12. 成员名单和没批准的注册申请只有主账号能看见。
13. 主账号能在成员管理里帮子账号解锁、重置密码（复用现有解锁/改密码原
    语：``set_account_status`` + ``rotate_credential`` + bump epoch，
    不重写认证核心；临时密码只在响应里出现一次，绝不写日志）。
14. 主账号动子账号的东西只记在主账号可见的审计链里：写进身份哈希链时
    actor 恒为主账号（成员按 principal_id 过滤本来就看不到），同时在
    本模块的 ``owner_audit`` 里留一份仅主账号可见的镜像；子账号查自己
    的审计链时这些记录不出现。
15. 子账号不能知道主账号能看到他们的东西：后端返回给子账号的任何响应里
    不得出现“主账号可见/会被看到”这类字样（本模块所有成员可见文案都
    是中性文案，测试里逐条扫描）。
16. 文档：``docs/account-hierarchy.md`` 里“主账号能用子账号的 Key”与新
    规矩冲突，以这次的新规矩为准——Key 不共用，只吸取记忆与技能；新规
    矩说明见 ``docs/account-master-sub.md``。

底线：不碰登录/权限/审计链/备份恢复核心逻辑，只在已有机制上接线；
``accounts.py:324`` 与 ``local_proof.py:161`` 的 403 文案不动；
``api.x.ai`` 不进信任名单与出站登记表；LGPL 法务书面确认仍缺，
本模块不宣称许可证已闭环。
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..kernels.identity import _accounts, get_identity_manager
from .account_context import AccountContext, deny, require_account_principal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/a3m", tags=["a3m"])

_STATE_ENV = "LIUHAO_A3M_STATE_FILE"
_state_lock = threading.Lock()

#: 删除进回收站后多少天真删。
TRASH_RETENTION_DAYS = 30

#: 助手名字：主账号默认“贾维斯”，子账号默认“小灵”。
OWNER_ASSISTANT_NAME = "贾维斯"
MEMBER_ASSISTANT_NAME = "小灵"
#: 头像标识：红金小铁只属于主账号的贾维斯；子账号默认用别的标识。
OWNER_AVATAR_ID = "xiaotie"
MEMBER_AVATAR_ID = "xiaoling"

#: 返回给子账号的响应里绝不许出现的字样（第 15 条）。测试里逐条扫描。
FORBIDDEN_MEMBER_WORDING = (
    "主账号可见",
    "会被看到",
    "能看到你",
    "能看到您",
    "会被主账号",
    "上级能看到",
    "上级可见",
    "会被上级",
)

#: 没配自己的 Key 时的明确提示（第 2 条）。
NO_OWN_KEY_MESSAGE = "还没有配置自己的模型 Key，请先在设置里添加自己的 Key。"

#: 邀请链接已停用时 a2a 端点返回的文案（与 a2a.py 保持一致）。
INVITE_DISABLED_MESSAGE = "邀请链接已停用，请走注册＋批准。"


def _principal(context=Depends(require_account_principal)):
    return context


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_path() -> str:
    from ..common.data_paths import resolve_store_path

    return resolve_store_path(
        "a3m_state.json",
        os.path.join("data", "a3m_state.json"),
        env_var=_STATE_ENV,
    )


def _default_state() -> Dict[str, Any]:
    return {
        "pending": {},
        "absorption_opt_out": {},
        "absorbed": [],
        "assistant_profiles": {},
        "bot_controls": [],
        "pending_confirmations": [],
        "account_trash": {},
        "owner_audit": [],
    }


def load_state() -> Dict[str, Any]:
    """读状态文件；不存在/损坏时返回默认空状态（不编数据）。"""
    from ..common.atomic_write import read_text_with_retry

    try:
        raw = json.loads(read_text_with_retry(_state_path()))
    except (OSError, ValueError):
        return _default_state()
    if not isinstance(raw, dict):
        return _default_state()
    merged = _default_state()
    merged.update(raw)
    return merged


def save_state(state: Dict[str, Any]) -> None:
    from ..common.atomic_write import atomic_write_text, exclusive_file_lock

    path = _state_path()
    with _state_lock:
        with exclusive_file_lock(f"{path}.lock", description="A3m 状态"):
            atomic_write_text(
                path,
                json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
            )


# --------------------------------------------------------------------------- #
# 通用判定
# --------------------------------------------------------------------------- #

def _manager():
    return get_identity_manager()


def _require_owner(context: AccountContext) -> None:
    """本模块 Owner 专属接口的统一门：不是主账号一律 403（第 1/4/12 条）。

    独立根（没有账号节点的老令牌）也拒绝：fail-closed，宁可让老部署的
    管理员走账号树登录，不放一个“看起来像根”的令牌进来。
    """
    if context.standalone or not context.is_root:
        raise deny(403, "A3M_OWNER_ONLY", "没有权限")


def _target_node(manager, principal: str):
    node = manager.get_account(str(principal or "").strip())
    if node is None:
        raise deny(404, _accounts.CODE_SUBTREE_DENIED, _accounts.NEUTRAL_NOT_FOUND_MESSAGE)
    return node


def _require_target_in_subtree(context: AccountContext, target: str) -> None:
    """Owner 查某个子账号：目标不在自己的子树内一律 403（第 1 条）。"""
    target = str(target or "").strip()
    if target == context.principal:
        return
    if target not in (context.subtree or ()):
        raise deny(403, "A3M_OWNER_ONLY", "没有权限")


def _public_account(node) -> Dict[str, Any]:
    return _accounts.public_member(node)


def _owner_audit(action: str, actor: str, target: str, details: Optional[Dict[str, Any]] = None) -> None:
    """主账号跨账号操作的审计（第 14 条）：两处留痕，都是仅主账号可见。

    1. 身份哈希链：actor 恒为主账号；成员按 principal_id 过滤本来就看不到
       actor=主账号的记录（见 ``gateway/audit.py`` 的 ``_read_events``）。
       details 里再打 ``visibility: owner_only`` 标记，供以后核查。
    2. 本模块 ``owner_audit`` 镜像：主账号专用的审计列表端点读这里。
    """
    payload = dict(details or {})
    payload.setdefault("visibility", "owner_only")
    try:
        _manager().record_event(
            action=f"a3m.{action}",
            actor=actor,
            target=target,
            outcome="allowed",
            details=payload,
        )
    except Exception:  # noqa: BLE001 - 审计失败不阻断业务（既有语义）
        logger.warning("a3m owner audit chain write failed: %s -> %s", actor, target)
    try:
        state = load_state()
        entries = state.setdefault("owner_audit", [])
        entries.append(
            {
                "id": str(uuid.uuid4()),
                "action": f"a3m.{action}",
                "actor": actor,
                "target": target,
                "details": payload,
                "created_at": _now_iso(),
            }
        )
        state["owner_audit"] = entries[-500:]
        save_state(state)
    except Exception:  # noqa: BLE001 - 镜像失败不阻断业务
        logger.warning("a3m owner audit mirror write failed", exc_info=True)


def list_owner_audit(limit: int = 100) -> List[Dict[str, Any]]:
    """仅主账号可见的审计镜像（第 14 条），最新在前。"""
    entries = load_state().get("owner_audit", [])
    return list(reversed(entries))[: max(0, int(limit))]


# --------------------------------------------------------------------------- #
# 第 6 条：注册＋批准 / 停用 / 删除（回收站 30 天）/ 恢复
# --------------------------------------------------------------------------- #

def record_pending_registration(principal: str, display_name: Optional[str] = None) -> Dict[str, Any]:
    """自助注册成功后记一笔待批准（``accounts.py`` 的 self-register 调用）。"""
    state = load_state()
    entry = {
        "principal": str(principal),
        "display_name": display_name or "",
        "created_at": _now_iso(),
        "status": _accounts.ACCOUNT_STATUS_PENDING,
    }
    state.setdefault("pending", {})[str(principal)] = entry
    save_state(state)
    return entry


def list_pending_registrations() -> List[Dict[str, Any]]:
    """待批准的注册申请（第 12 条：只接受主账号，见 HTTP 层）。

    以账号节点状态为准（``pending_approval``），状态文件只补 display_name
    与注册时间；节点已批准/已删的申请不会出现在这里。
    """
    manager = _manager()
    nodes = manager.account_nodes()
    pending_state = load_state().get("pending", {})
    out: List[Dict[str, Any]] = []
    for principal, node in sorted(nodes.items()):
        if node.status != _accounts.ACCOUNT_STATUS_PENDING:
            continue
        entry = dict(pending_state.get(principal, {}))
        entry.setdefault("principal", principal)
        entry.setdefault("display_name", "")
        entry.setdefault("created_at", node.created_at or "")
        entry["status"] = _accounts.ACCOUNT_STATUS_PENDING
        out.append(entry)
    return out


def list_members() -> List[Dict[str, Any]]:
    """成员名单（第 12 条：只接受主账号，见 HTTP 层）。

    含根以外的全部账号：待批准/已开通/已停用/已删除都在内（状态字段写明），
    另带助手名与吸取开关，B 组的成员管理页直接用这份。
    """
    manager = _manager()
    nodes = manager.account_nodes()
    profiles = load_state().get("assistant_profiles", {})
    opt_out = load_state().get("absorption_opt_out", {})
    out: List[Dict[str, Any]] = []
    for principal in sorted(nodes):
        node = nodes[principal]
        if node.is_root:
            continue
        entry = _public_account(node)
        profile = profiles.get(principal, {})
        entry["assistant_name"] = profile.get(
            "assistant_name", MEMBER_ASSISTANT_NAME
        )
        entry["avatar_id"] = profile.get("avatar_id", MEMBER_AVATAR_ID)
        entry["absorption_opt_out"] = bool(opt_out.get(principal, False))
        out.append(entry)
    return out


def approve_registration(owner: str, principal: str) -> Dict[str, Any]:
    """批准注册申请：待批准 → 已开通（active）。只接受主账号（HTTP 层拦）。"""
    manager = _manager()
    node = _target_node(manager, principal)
    if node.is_root:
        raise deny(409, "A3M_NOT_A_MEMBER", "该账号不需要批准")
    if node.status != _accounts.ACCOUNT_STATUS_PENDING:
        raise deny(409, "A3M_NOT_PENDING", "该账号不是待批准状态")
    manager.set_account_status(owner, principal, _accounts.ACCOUNT_STATUS_ACTIVE, "批准开通")
    state = load_state()
    state.get("pending", {}).pop(str(principal), None)
    save_state(state)
    _owner_audit("registration.approve", owner, principal, {})
    return {"account": _public_account(_target_node(manager, principal)), "status": "active"}


def reject_registration(owner: str, principal: str) -> Dict[str, Any]:
    """驳回注册申请：待批准 → 已删除（进回收站登记，30 天后可真删）。

    驳回的账号从未开通、没有任何业务数据；进回收站只是留一条可核查的
    记录，不会因此保留任何聊天/文件。
    """
    manager = _manager()
    node = _target_node(manager, principal)
    if node.is_root:
        raise deny(409, "A3M_NOT_A_MEMBER", "该账号不需要批准")
    if node.status != _accounts.ACCOUNT_STATUS_PENDING:
        raise deny(409, "A3M_NOT_PENDING", "该账号不是待批准状态")
    manager.set_account_status(owner, principal, _accounts.ACCOUNT_STATUS_DELETED, "驳回注册申请")
    state = load_state()
    state.get("pending", {}).pop(str(principal), None)
    _record_trash(state, principal, reason="registration_rejected")
    save_state(state)
    _owner_audit("registration.reject", owner, principal, {})
    return {"account": _public_account(_target_node(manager, principal)), "status": "deleted"}


def suspend_member(owner: str, principal: str) -> Dict[str, Any]:
    """停用：锁登录、数据留着（第 6 条）。只接受主账号。"""
    manager = _manager()
    node = _target_node(manager, principal)
    if node.is_root:
        raise deny(409, "A3M_NOT_A_MEMBER", "不能停用该账号")
    if node.status == _accounts.ACCOUNT_STATUS_SUSPENDED:
        return {"account": _public_account(node), "status": "suspended", "already": True}
    manager.set_account_status(owner, principal, _accounts.ACCOUNT_STATUS_SUSPENDED, "停用")
    _owner_audit("member.suspend", owner, principal, {})
    return {"account": _public_account(_target_node(manager, principal)), "status": "suspended"}


def restore_member(owner: str, principal: str) -> Dict[str, Any]:
    """恢复：停用/已删除 → 已开通。只接受主账号。"""
    manager = _manager()
    node = _target_node(manager, principal)
    if node.is_root:
        raise deny(409, "A3M_NOT_A_MEMBER", "该账号不需要恢复")
    if node.status == _accounts.ACCOUNT_STATUS_ACTIVE:
        return {"account": _public_account(node), "status": "active", "already": True}
    manager.set_account_status(owner, principal, _accounts.ACCOUNT_STATUS_ACTIVE, "恢复开通")
    state = load_state()
    state.get("account_trash", {}).pop(str(principal), None)
    save_state(state)
    _owner_audit("member.restore", owner, principal, {})
    return {"account": _public_account(_target_node(manager, principal)), "status": "active"}


def _record_trash(state: Dict[str, Any], principal: str, reason: str = "") -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    entry = {
        "principal": str(principal),
        "deleted_at": now.isoformat(),
        "purge_after": (now + timedelta(days=TRASH_RETENTION_DAYS)).isoformat(),
        "retention_days": TRASH_RETENTION_DAYS,
        "reason": reason,
        "status": "in_trash",
    }
    state.setdefault("account_trash", {})[str(principal)] = entry
    return entry


def delete_member(owner: str, principal: str) -> Dict[str, Any]:
    """删除：进回收站，30 天后真删（第 6 条）。只接受主账号。

    立即生效的是“账号不可用”（登录与业务接口都被 active 检查拦住）；
    数据（记忆、文件、用量）先留着，30 天后 ``purge_expired_trash``
    才真删；审计链永不删。
    """
    manager = _manager()
    node = _target_node(manager, principal)
    if node.is_root:
        raise deny(409, "A3M_NOT_A_MEMBER", "不能删除该账号")
    if node.status == _accounts.ACCOUNT_STATUS_DELETED:
        state = load_state()
        entry = state.get("account_trash", {}).get(str(principal))
        return {
            "account": _public_account(node),
            "status": "deleted",
            "already": True,
            "trash": entry,
        }
    manager.set_account_status(owner, principal, _accounts.ACCOUNT_STATUS_DELETED, "删除（进回收站）")
    state = load_state()
    entry = _record_trash(state, principal, reason="deleted_by_owner")
    save_state(state)
    _owner_audit("member.delete", owner, principal, {"purge_after": entry["purge_after"]})
    return {
        "account": _public_account(_target_node(manager, principal)),
        "status": "deleted",
        "trash": entry,
    }


def list_trash() -> List[Dict[str, Any]]:
    """回收站里的账号（第 6 条：只接受主账号，见 HTTP 层）。"""
    return sorted(
        load_state().get("account_trash", {}).values(),
        key=lambda item: str(item.get("deleted_at") or ""),
    )


def purge_expired_trash(owner: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """真删已过 30 天的回收站账号（第 6 条）。只接受主账号。

    真删的内容：该账号的记忆条目、该账号名下的模型配置、本模块里该账号
    的本地记录（助手名、吸取开关、待批准、派活记录里以其为目标的条目）。
    已吸走的记忆与本事**留给主账号**（不删，来源标记还在）。
    不删的内容：审计链（设计上永不删）、工作区文件（删文件是破坏性操作，
    且 ``files.py`` 的文件回收站是另一套机制；30 天后文件仍留在该账号
    的工作区目录里，由 Joseph 需要时手动清理——见交卷报告的缺口）。
    """
    moment = now or datetime.now(timezone.utc)
    trash = load_state().get("account_trash", {})
    purged: List[str] = []
    skipped: List[str] = []
    for principal, entry in sorted(trash.items()):
        if entry.get("status") == "purged":
            continue
        purge_after = str(entry.get("purge_after") or "")
        try:
            due = datetime.fromisoformat(purge_after) <= moment
        except ValueError:
            due = False
        if not due:
            skipped.append(principal)
            continue
        # 注意顺序：_purge_account_data 与 _owner_audit 各自都会重新读盘、
        # 改、写盘；这里每步都重新读一份新鲜 state 再写，绝不能拿着循环开头
        # 读到的旧 state 在最后统一 save——那样会把中间的写入覆盖丢
        # （曾经因此丢过 member.purge 的审计镜像）。
        _purge_account_data(principal)
        state = load_state()
        record = state.get("account_trash", {}).get(principal, {})
        record["status"] = "purged"
        record["purged_at"] = moment.isoformat()
        state.setdefault("account_trash", {})[principal] = record
        save_state(state)
        _owner_audit("member.purge", owner, principal, {})
        purged.append(principal)
    return {"purged": purged, "skipped_not_due": skipped}


def _purge_account_data(principal: str) -> None:
    """删除一个账号的记忆与模型配置（``purge_expired_trash`` 内部用）。"""
    try:
        from .account_memory import _connect as _memory_connect

        with _memory_connect() as connection:
            connection.execute(
                "DELETE FROM account_memory_entries WHERE owner = ?", (str(principal),)
            )
            connection.commit()
    except Exception:  # noqa: BLE001 - 记忆库不可用时记一条日志，不让整批真删失败
        logger.warning("a3m purge: memory delete failed for %s", principal, exc_info=True)
    try:
        from ..ai.model_config import ModelConfigStore

        store = ModelConfigStore()
        for row in store.list_models(visible_to=(str(principal),), allow_unowned=False):
            try:
                store.delete(str(row.get("id") or ""), actor="a3m-purge")
            except Exception:  # noqa: BLE001 - 单条删失败不阻断其余
                logger.warning(
                    "a3m purge: model delete failed for %s", row.get("id"), exc_info=True
                )
    except Exception:  # noqa: BLE001
        logger.warning("a3m purge: model store failed for %s", principal, exc_info=True)
    state = load_state()
    state.get("assistant_profiles", {}).pop(str(principal), None)
    state.get("absorption_opt_out", {}).pop(str(principal), None)
    state.get("pending", {}).pop(str(principal), None)
    state["bot_controls"] = [
        item for item in state.get("bot_controls", [])
        if str(item.get("target_account") or "") != str(principal)
    ]
    save_state(state)


# --------------------------------------------------------------------------- #
# 第 13 条：主账号帮子账号解锁、重置密码（复用现有原语，不重写认证核心）
# --------------------------------------------------------------------------- #

def unlock_member(owner: str, principal: str) -> Dict[str, Any]:
    """解锁：停用状态 → 恢复开通；已开通的直接返回已是开通状态。

    复用 ``set_account_status``（与停用同一条路），不另写解锁逻辑。
    登录限流（多次输错密码的临时锁）不在账号状态里，本函数不管那一层。
    """
    manager = _manager()
    node = _target_node(manager, principal)
    if node.is_root:
        raise deny(409, "A3M_NOT_A_MEMBER", "该账号不需要解锁")
    if node.status == _accounts.ACCOUNT_STATUS_ACTIVE:
        return {"account": _public_account(node), "status": "active", "already_active": True}
    manager.set_account_status(owner, principal, _accounts.ACCOUNT_STATUS_ACTIVE, "解锁")
    _owner_audit("member.unlock", owner, principal, {})
    return {"account": _public_account(_target_node(manager, principal)), "status": "active"}


def reset_member_password(owner: str, principal: str) -> Dict[str, Any]:
    """重置子账号密码：发一个一次性临时密码（只在本次响应里出现一次）。

    复用 ``rotate_credential`` + ``bump_account_epoch``（与
    ``accounts.py`` 的管理者重置同一套原语）：旧令牌立即失效。临时密码
    绝不写日志、不写审计 details；对方首次登录后应立即自己改密码。
    """
    from .auth import rotate_credential

    manager = _manager()
    node = _target_node(manager, principal)
    if node.is_root:
        raise deny(409, "A3M_NOT_A_MEMBER", "不能重置该账号的密码")
    if node.status == _accounts.ACCOUNT_STATUS_DELETED:
        raise deny(409, "A3M_ACCOUNT_DELETED", "该账号已删除")
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"
    temporary = "".join(secrets.choice(alphabet) for _ in range(16))
    if not rotate_credential(principal, temporary):
        raise deny(500, "ACCOUNT_CREDENTIAL_WRITE_FAILED", "密码重置失败")
    manager.bump_account_epoch(principal, "password-reset-by-owner")
    manager.record_event(
        action="account.password.reset",
        actor=owner,
        target=principal,
        outcome="allowed",
        details={"credential": "rotated", "mode": "admin", "visibility": "owner_only"},
    )
    _owner_audit("member.reset_password", owner, principal, {})
    return {
        "ok": True,
        "account": principal,
        "temporary_password": temporary,
        "notice": "请把这个临时密码交给该成员，并提醒对方首次登录后立即修改密码。",
    }


# --------------------------------------------------------------------------- #
# 第 8 条：助手名字与头像标识（账号级字段；头像文件本身是 B 组的事）
# --------------------------------------------------------------------------- #

def assistant_profile(principal: str) -> Dict[str, Any]:
    """某账号的助手名字与头像标识。默认：主账号“贾维斯/小铁”，子账号“小灵/非小铁”。"""
    manager = _manager()
    node = manager.get_account(str(principal))
    is_root = bool(node is not None and node.is_root)
    stored = load_state().get("assistant_profiles", {}).get(str(principal), {})
    return {
        "account": str(principal),
        "assistant_name": stored.get(
            "assistant_name", OWNER_ASSISTANT_NAME if is_root else MEMBER_ASSISTANT_NAME
        ),
        "avatar_id": stored.get(
            "avatar_id", OWNER_AVATAR_ID if is_root else MEMBER_AVATAR_ID
        ),
        "is_owner_default": is_root,
    }


def set_assistant_profile(
    principal: str, *, assistant_name: Optional[str] = None, avatar_id: Optional[str] = None
) -> Dict[str, Any]:
    """改自己的助手名字/头像标识（只能改自己的，HTTP 层只传令牌主体）。

    保留规则：第 8 条——“贾维斯”这个名字与小铁头像只属于主账号的助手，
    子账号不能把自己的助手改成这两个；子账号默认“小灵”，可改成别的。
    """
    manager = _manager()
    node = manager.get_account(str(principal))
    is_root = bool(node is not None and node.is_root)
    name = (assistant_name or "").strip() if assistant_name is not None else None
    avatar = (avatar_id or "").strip() if avatar_id is not None else None
    if name is not None:
        if not name or len(name) > 20:
            raise deny(400, "A3M_ASSISTANT_NAME_INVALID", "助手名字在 1 到 20 个字之间。")
        if not is_root and name == OWNER_ASSISTANT_NAME:
            raise deny(400, "A3M_ASSISTANT_NAME_RESERVED", "这个名字留给主账号的助手，请换一个名字。")
    if avatar is not None:
        if not avatar or len(avatar) > 64:
            raise deny(400, "A3M_AVATAR_INVALID", "头像标识在 1 到 64 个字之间。")
        if not is_root and avatar == OWNER_AVATAR_ID:
            raise deny(400, "A3M_AVATAR_RESERVED", "这个头像只属于主账号的助手，请换一个。")
    state = load_state()
    profiles = state.setdefault("assistant_profiles", {})
    current = dict(profiles.get(str(principal), {}))
    if name is not None:
        current["assistant_name"] = name
    if avatar is not None:
        current["avatar_id"] = avatar
    current["updated_at"] = _now_iso()
    profiles[str(principal)] = current
    save_state(state)
    return assistant_profile(principal)


# --------------------------------------------------------------------------- #
# 第 2 条：Key 各用各的——只给“已配/未配”状态，永不返回 Key（哪怕掩码）
# --------------------------------------------------------------------------- #

def own_key_status(principal: str) -> Dict[str, Any]:
    """调用者自己的模型 Key 配了没有：只返回状态，不返回 Key、不返回掩码。

    ``configured`` 为 False 时聊天会失败——调用方应先去设置里添加自己的
    Key（见 ``require_own_key`` 的明确报错），绝不能悄悄走别人的 Key。
    """
    from ..ai.model_config import ModelConfigStore

    rows = ModelConfigStore().list_models(
        visible_to=(str(principal),), allow_unowned=False
    )
    default_name = None
    for row in rows:
        if row.get("is_default"):
            default_name = str(row.get("name") or row.get("model") or "")
            break
    if default_name is None and rows:
        default_name = str(rows[0].get("name") or rows[0].get("model") or "")
    return {
        "account": str(principal),
        "configured": bool(rows),
        "model_count": len(rows),
        "default_model": default_name,
        "message": "已配置自己的模型 Key。" if rows else NO_OWN_KEY_MESSAGE,
    }


def require_own_key(principal: str, model_id: Optional[str] = None) -> Dict[str, Any]:
    """模型调用前的本账号 Key 门禁（进程内用，供聊天/派活等调用方接线）。

    * 指定了 ``model_id``：只认归属是调用者自己的那一行；命中别人的 Key
      一律 403，绝不静默改用；
    * 没指定：调用者自己名下没有任何可用模型时明确报“没配 Key”（409，
      与聊天无模型时的 409 口径一致），不回退到别人的默认模型。

    返回的 dict 含解密后的 Key，**只供进程内使用，绝不原样返回给 HTTP**。
    """
    from ..ai.model_config import ModelConfigStore

    store = ModelConfigStore()
    scope = (str(principal),)
    resolved = store.resolve(
        model_id,
        visible_to=scope,
        allow_unowned=False,
        allow_owners=scope,
    )
    if resolved is None:
        raise deny(409, "MODEL_KEY_NOT_CONFIGURED", NO_OWN_KEY_MESSAGE)
    owner = str(resolved.get("owner_principal") or "")
    if owner and owner != str(principal):
        raise deny(403, _accounts.CODE_KEY_NOT_OWNED, "没有权限")
    return resolved


# --------------------------------------------------------------------------- #
# 第 1 条：主账号跨账号只读（聊天、任务、文件、知识库、Bot、用量、记忆、技能）
# --------------------------------------------------------------------------- #

READ_KINDS = ("chats", "tasks", "files", "knowledge", "bots", "usage", "memory", "skills")


def _conversation_sessions(target: str, limit: int = 50) -> Dict[str, Any]:
    from ..ai.conversation_store import ConversationStore

    store = ConversationStore()
    page = store.list_sessions_page(target, limit=limit)
    sessions = []
    for session_id in page.get("sessions", []):
        try:
            title = store.get_session_title(str(session_id))
        except Exception:  # noqa: BLE001
            title = ""
        sessions.append({"session_id": str(session_id), "title": title})
    return {"sessions": sessions, "total": int(page.get("total", len(sessions)))}


def _knowledge_documents(target: str, limit: int = 50) -> Dict[str, Any]:
    # D 组修复：这里原来 import 的 DocumentService 在 src/knowledge/documents.py
    # 里不存在（真实类名是 KnowledgeDocumentService），是 A3m 留下的错名；
    # 该函数是惰性 import，平时不执行，全量 import 完整性测试才把它抓出来。
    from ..knowledge.documents import KnowledgeDocumentService as DocumentService
    from ..knowledge.documents import KnowledgeScope

    service = DocumentService()
    scope = KnowledgeScope(
        principal=str(target),
        owners=(str(target),),
        include_unowned=False,
        owns_legacy=False,
    )
    result = service.list_documents(limit=limit, scope=scope)
    documents = [
        {
            "id": item.get("id"),
            "title": item.get("title"),
            "source": item.get("source"),
        }
        for item in result.get("documents", [])
    ]
    return {"documents": documents, "total": int(result.get("total", len(documents)))}


def _ai_state_manager():
    from .ai_management import AIStateManager

    return AIStateManager()


def _target_bots(target: str) -> List[Dict[str, Any]]:
    from .ai_management import GoalViewer

    viewer = GoalViewer(principal=str(target), subtree=(str(target),), is_root=False)
    return _ai_state_manager().list_all_employees(viewer)


def _target_skills(target: str) -> List[Dict[str, Any]]:
    """某账号的技能本事：其 Bot（员工）身上绑的技能（真实绑定记录）。"""
    from .skill_bindings import SkillBindingStore

    try:
        bots = _target_bots(target)
    except Exception:  # noqa: BLE001
        bots = []
    names = {str(item.get("name") or "") for item in bots if item.get("name")}
    store = SkillBindingStore()
    out: List[Dict[str, Any]] = []
    for binding in store.list_all():
        employee = str(binding.get("employee") or "")
        if employee in names:
            out.append(
                {
                    "skill_id": binding.get("skill_id"),
                    "employee": employee,
                    "skill_name": binding.get("skill_name") or binding.get("skill_id"),
                }
            )
    return out


def owner_read_account(owner: str, target: str, kind: str, *, limit: int = 50) -> Dict[str, Any]:
    """主账号按子账号读一样东西（第 1 条，只读；子账号调 HTTP 层就 403）。

    每种只读都带账号条件，不存在“顺带看到别人”的路径；知识库与 Bot 读不到
    时如实返回 ``available: false`` + 原因，不编一条假记录。
    """
    if kind not in READ_KINDS:
        raise deny(400, "A3M_READ_KIND_INVALID", "要读的东西不存在。")
    manager = _manager()
    node = _target_node(manager, target)
    if node.is_root and str(target) != str(owner):
        raise deny(403, "A3M_OWNER_ONLY", "没有权限")
    if kind == "chats":
        try:
            data = _conversation_sessions(target, limit=limit)
        except Exception as exc:  # noqa: BLE001
            return {"kind": kind, "account": target, "available": False, "reason": str(exc)}
        return {"kind": kind, "account": target, "available": True, **data}
    if kind == "tasks":
        from .a3s import collect_tasks_export

        items = collect_tasks_export(target)
        return {"kind": kind, "account": target, "available": True, "tasks": items, "count": len(items)}
    if kind == "files":
        from .a3s import collect_file_list

        items = collect_file_list(target)
        return {"kind": kind, "account": target, "available": True, "files": items, "count": len(items)}
    if kind == "knowledge":
        try:
            data = _knowledge_documents(target, limit=limit)
        except Exception as exc:  # noqa: BLE001
            return {"kind": kind, "account": target, "available": False, "reason": str(exc)}
        return {"kind": kind, "account": target, "available": True, **data}
    if kind == "bots":
        try:
            bots = _target_bots(target)
        except Exception as exc:  # noqa: BLE001
            return {"kind": kind, "account": target, "available": False, "reason": str(exc)}
        return {"kind": kind, "account": target, "available": True, "bots": bots, "count": len(bots)}
    if kind == "usage":
        from .account_usage import usage_summary

        owners = tuple(manager.subtree_principals(target)) or (target,)
        summary = usage_summary(target, include_callers=True, owners=owners)
        return {"kind": kind, "account": target, "available": True, **summary}
    if kind == "memory":
        from .account_memory import _query_entries

        entries = _query_entries((target,), "", limit=limit)
        return {"kind": kind, "account": target, "available": True, "entries": entries, "count": len(entries)}
    if kind == "skills":
        try:
            skills = _target_skills(target)
        except Exception as exc:  # noqa: BLE001
            return {"kind": kind, "account": target, "available": False, "reason": str(exc)}
        return {"kind": kind, "account": target, "available": True, "skills": skills, "count": len(skills)}
    raise deny(400, "A3M_READ_KIND_INVALID", "要读的东西不存在。")  # pragma: no cover


def owner_account_summary(owner: str, target: str) -> Dict[str, Any]:
    """主账号看某子账号的一页汇总（给 B 组成员管理页用，只读）。"""
    from .account_memory import _count_entries

    manager = _manager()
    node = _target_node(manager, target)
    if node.is_root and str(target) != str(owner):
        raise deny(403, "A3M_OWNER_ONLY", "没有权限")
    try:
        bots = _target_bots(target)
        bot_count = len(bots)
    except Exception:  # noqa: BLE001
        bot_count = 0
    return {
        "account": _public_account(node),
        "assistant": assistant_profile(target),
        "key_configured": bool(own_key_status(target)["configured"]),
        "memory_count": _count_entries(target),
        "bot_count": bot_count,
        "absorption_opt_out": bool(
            load_state().get("absorption_opt_out", {}).get(str(target), False)
        ),
    }


# --------------------------------------------------------------------------- #
# 第 3 条：记忆与技能单向吸取（只进主账号，标明来源；子账号之间不可见）
# --------------------------------------------------------------------------- #

def absorption_opted_out(principal: str) -> bool:
    return bool(load_state().get("absorption_opt_out", {}).get(str(principal), False))


def set_absorption_opt_out(owner: str, principal: str, opted_out: bool) -> Dict[str, Any]:
    """主账号单独关掉某一个人的吸取（第 3 条，按人开关）。

    关了就不再吸那个人新的；之前吸来的留着，并标明来源（不动已吸记录）。
    """
    manager = _manager()
    node = _target_node(manager, principal)
    if node.is_root:
        raise deny(409, "A3M_NOT_A_MEMBER", "该账号不需要吸取开关")
    state = load_state()
    opt_out = state.setdefault("absorption_opt_out", {})
    if opted_out:
        opt_out[str(principal)] = True
    else:
        opt_out.pop(str(principal), None)
    save_state(state)
    _owner_audit(
        "absorption.opt_out", owner, principal, {"opted_out": bool(opted_out)}
    )
    return {"account": str(principal), "absorption_opt_out": bool(opted_out)}


def _absorbed_source_ids() -> set:
    return {
        str(item.get("source_entry_id") or "")
        for item in load_state().get("absorbed", [])
        if item.get("kind") == "memory"
    }


def absorb_from_member(owner: str, source: str, *, limit: int = 200) -> Dict[str, Any]:
    """从某子账号吸取记忆与技能进主账号的贾维斯（第 3 条）。

    * 主账号自动吸收，不用逐条确认；
    * 记忆：把对方的记忆条目抄一份进主账号名下，source=absorbed，
      tags 里写明 ``absorbed_from:<子账号>`` 与原 source；去重按源条目 id，
      同一条只吸一次；
    * 技能：只记录“对方的 Bot 身上绑了什么技能”（真实绑定记录，kind=skill），
      **不自动安装成可执行技能**——装技能是另外的动作，要主账号另行确认，
      这里只留一条带来源的学习记录；
    * 关掉吸取的人直接跳过（``skipped``），之前吸来的不动。
    """
    from .account_memory import _query_entries, record_entry_for_owner

    manager = _manager()
    node = _target_node(manager, source)
    if node.is_root:
        raise deny(409, "A3M_NOT_A_MEMBER", "该账号不需要吸取")
    if absorption_opted_out(source):
        return {
            "account": str(source),
            "skipped": True,
            "reason": "已关闭对该账号的吸取",
            "new_memories": 0,
            "new_skills": 0,
        }
    state = load_state()
    absorbed = state.setdefault("absorbed", [])
    known = _absorbed_source_ids()
    new_memories = 0
    for entry in _query_entries((str(source),), "", limit=limit):
        source_entry_id = str(entry.get("id") or "")
        if not source_entry_id or source_entry_id in known:
            continue
        copied = record_entry_for_owner(
            owner,
            str(entry.get("text") or ""),
            source="absorbed",
            tags=[f"absorbed_from:{source}", f"source:{entry.get('source') or ''}"],
        )
        absorbed.append(
            {
                "id": str(uuid.uuid4()),
                "kind": "memory",
                "source_account": str(source),
                "source_entry_id": source_entry_id,
                "entry_id": str(copied.get("id") or ""),
                "text_preview": str(entry.get("text") or "")[:120],
                "absorbed_at": _now_iso(),
            }
        )
        known.add(source_entry_id)
        new_memories += 1
    new_skills = 0
    known_skills = {
        (str(item.get("source_account") or ""), str(item.get("skill_id") or ""), str(item.get("employee") or ""))
        for item in absorbed
        if item.get("kind") == "skill"
    }
    try:
        skills = _target_skills(source)
    except Exception:  # noqa: BLE001
        skills = []
    for skill in skills:
        key = (str(source), str(skill.get("skill_id") or ""), str(skill.get("employee") or ""))
        if key in known_skills:
            continue
        absorbed.append(
            {
                "id": str(uuid.uuid4()),
                "kind": "skill",
                "source_account": str(source),
                "skill_id": str(skill.get("skill_id") or ""),
                "employee": str(skill.get("employee") or ""),
                "skill_name": str(skill.get("skill_name") or skill.get("skill_id") or ""),
                "absorbed_at": _now_iso(),
            }
        )
        known_skills.add(key)
        new_skills += 1
    save_state(state)
    _owner_audit(
        "absorption.absorb",
        owner,
        source,
        {"new_memories": new_memories, "new_skills": new_skills},
    )
    return {
        "account": str(source),
        "skipped": False,
        "new_memories": new_memories,
        "new_skills": new_skills,
    }


def absorb_all(owner: str) -> Dict[str, Any]:
    """把整棵子树里所有子账号的新记忆与技能吸一遍（跳过已关吸取的人）。"""
    manager = _manager()
    results: List[Dict[str, Any]] = []
    for principal in sorted(manager.subtree_principals(owner)):
        if principal == owner:
            continue
        node = manager.get_account(principal)
        if node is None or node.status != _accounts.ACCOUNT_STATUS_ACTIVE:
            continue
        results.append(absorb_from_member(owner, principal))
    return {"results": results}


def list_absorbed(source: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    """主账号看吸来的记忆与技能（第 3 条：仅主账号可见，见 HTTP 层）。"""
    entries = load_state().get("absorbed", [])
    if source:
        entries = [item for item in entries if str(item.get("source_account") or "") == str(source)]
    return list(reversed(entries))[: max(0, int(limit))]


# --------------------------------------------------------------------------- #
# 第 4 条：主账号操控子账号的 Bot（看状态、派活、暂停、收回结果；子账号不可见）
# --------------------------------------------------------------------------- #

def _find_bot(target: str, bot_id: str) -> Dict[str, Any]:
    for bot in _target_bots(target):
        if str(bot.get("employee_id") or "") == str(bot_id) or str(bot.get("name") or "") == str(bot_id):
            return bot
    raise deny(404, _accounts.CODE_SUBTREE_DENIED, _accounts.NEUTRAL_NOT_FOUND_MESSAGE)


def _record_bot_control(
    owner: str, target: str, bot_id: str, action: str, **extra: Any
) -> Dict[str, Any]:
    state = load_state()
    record = {
        "id": str(uuid.uuid4()),
        "action": action,
        "target_account": str(target),
        "bot_id": str(bot_id),
        "created_by": owner,
        "created_at": _now_iso(),
        **extra,
    }
    state.setdefault("bot_controls", []).append(record)
    save_state(state)
    return record


def list_bot_controls(target: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    """主账号看派活记录（第 4 条：只出现在主账号这边，见 HTTP 层）。"""
    records = load_state().get("bot_controls", [])
    if target:
        records = [item for item in records if str(item.get("target_account") or "") == str(target)]
    return list(reversed(records))[: max(0, int(limit))]


def dispatch_bot(owner: str, target: str, bot_id: str, instruction: str) -> Dict[str, Any]:
    """给某子账号的 Bot 派活（第 4 条，只接受主账号）。

    真实派活：以**主账号自己的名义**下一个 goal（``owner_account_id`` =
    主账号），指名该 Bot 执行，后台跑，立即返回 running。goal 归属主账号，
    因此子账号自己的目标列表里看不到这条活、收不到任何通知；派活记录只
    在本模块的 ``bot_controls`` 里（主账号可见）。Bot 的忙闲状态本身会
    变（那是它自己的状态，不是派活记录）。
    """
    instruction = (instruction or "").strip()
    if not instruction:
        raise deny(400, "A3M_INSTRUCTION_EMPTY", "派活内容不能为空。")
    manager = _manager()
    _target_node(manager, target)
    bot = _find_bot(target, bot_id)
    stable_id = str(bot.get("employee_id") or bot.get("name") or bot_id)
    try:
        outcome = _ai_state_manager().create_and_execute_goal(
            instruction,
            owner_account_id=owner,
            pinned_agent=stable_id,
            background=True,
        )
    except Exception as exc:  # noqa: BLE001 - 派活没下去要如实说，不假装已派
        record = _record_bot_control(
            owner, target, bot_id, "dispatch", instruction=instruction,
            status="failed", reason=str(exc),
        )
        raise deny(502, "A3M_DISPATCH_FAILED", f"派活没有成功：{exc}") from exc
    record = _record_bot_control(
        owner, target, bot_id, "dispatch", instruction=instruction,
        status=str(outcome.get("state") or "running"),
        goal_id=str(outcome.get("goal_id") or ""),
    )
    _owner_audit("bot.dispatch", owner, target, {"bot_id": bot_id, "goal_id": record.get("goal_id")})
    return record


def _goal_bot_id(goal: Dict[str, Any]) -> str:
    for key in ("employee_id", "pinned_agent", "employee", "agent_name", "bot_id"):
        value = goal.get(key)
        if value:
            return str(value)
    attribution = goal.get("employee_attribution") or {}
    if isinstance(attribution, dict):
        for key in ("employee_id", "stable_id", "name"):
            if attribution.get(key):
                return str(attribution[key])
    return ""


def pause_bot(owner: str, target: str, bot_id: str) -> Dict[str, Any]:
    """暂停某子账号的 Bot 正在干的活（第 4 条，只接受主账号）。

    找的是**主账号名下**、指名该 Bot 的 running goal（派活时就是这么下的），
    逐个真实中止；没有在跑的活就如实返回 ``no_running_goal``，不假装停了什么。
    """
    from .ai_management import GoalViewer

    manager = _manager()
    _target_node(manager, target)
    bot = _find_bot(target, bot_id)
    stable_id = str(bot.get("employee_id") or bot.get("name") or bot_id)
    viewer = GoalViewer(principal=owner, subtree=tuple(manager.subtree_principals(owner)) or (owner,), is_root=True)
    stopped: List[str] = []
    try:
        goals = _ai_state_manager().list_goals(viewer)
    except Exception as exc:  # noqa: BLE001
        raise deny(502, "A3M_BOT_STATUS_FAILED", f"读不到 Bot 的任务：{exc}") from exc
    for goal in goals:
        if str(goal.get("state") or "") != "running":
            continue
        if _goal_bot_id(goal) not in (stable_id, str(bot.get("name") or ""), str(bot_id)):
            continue
        goal_id = str(goal.get("goal_id") or "")
        if not goal_id:
            continue
        try:
            _ai_state_manager().stop_goal(goal_id)
            stopped.append(goal_id)
        except Exception:  # noqa: BLE001 - 单个停失败不阻断其余，记下来
            logger.warning("a3m pause: stop failed goal=%s", goal_id, exc_info=True)
    record = _record_bot_control(
        owner, target, bot_id, "pause",
        status="paused" if stopped else "no_running_goal",
        goal_ids=stopped,
    )
    _owner_audit("bot.pause", owner, target, {"bot_id": bot_id, "goal_ids": stopped})
    return record


def collect_bot_result(owner: str, target: str, bot_id: str, *, limit: int = 5) -> Dict[str, Any]:
    """收回某子账号的 Bot 的结果（第 4 条，只接受主账号）。

    读的是主账号名下、指名该 Bot 的 goal 的真实终态（goal_id、状态、任务、
    结果摘要），只返回真实有的；没有结果就如实返回空列表。
    """
    from .ai_management import GoalViewer

    manager = _manager()
    _target_node(manager, target)
    bot = _find_bot(target, bot_id)
    stable_id = str(bot.get("employee_id") or bot.get("name") or bot_id)
    viewer = GoalViewer(principal=owner, subtree=tuple(manager.subtree_principals(owner)) or (owner,), is_root=True)
    try:
        goals = _ai_state_manager().list_goals(viewer)
    except Exception as exc:  # noqa: BLE001
        raise deny(502, "A3M_BOT_STATUS_FAILED", f"读不到 Bot 的任务：{exc}") from exc
    mine = [
        goal for goal in goals
        if _goal_bot_id(goal) in (stable_id, str(bot.get("name") or ""), str(bot_id))
    ]
    mine = sorted(mine, key=lambda item: str(item.get("created_at") or ""), reverse=True)[: max(0, int(limit))]
    results = [
        {
            "goal_id": str(goal.get("goal_id") or ""),
            "state": str(goal.get("state") or ""),
            "task": str(goal.get("natural_language") or goal.get("name") or ""),
            "result": goal.get("result") or goal.get("summary") or "",
        }
        for goal in mine
    ]
    record = _record_bot_control(
        owner, target, bot_id, "collect",
        status="collected", goal_ids=[item["goal_id"] for item in results],
    )
    _owner_audit("bot.collect", owner, target, {"bot_id": bot_id, "count": len(results)})
    return {"bot_id": str(bot_id), "target_account": str(target), "results": results, "control": record}


# --------------------------------------------------------------------------- #
# 第 9 条：“等你确认”收件箱——每条写明是哪个子账号、哪个 Bot
# --------------------------------------------------------------------------- #

def _display_name(manager, principal: str) -> str:
    node = manager.get_account(str(principal))
    return str(principal)


def enrich_confirmation(item: Dict[str, Any]) -> Dict[str, Any]:
    """给一条待确认项补上归属字段（第 9 条）。

    ``source_account_id`` / ``source_account_name``：这条确认来自哪个账号；
    ``bot_name``：来自哪个 Bot（审批记录里的 bot/employee/agent 字段，
    没有就空着，绝不编一个名字）。
    """
    enriched = dict(item)
    source = str(item.get("owner_account_id") or item.get("source_account_id") or "")
    enriched["source_account_id"] = source
    enriched["source_account_name"] = source
    bot_name = (
        item.get("bot_name")
        or item.get("employee")
        or item.get("agent_name")
        or item.get("bot")
        or ""
    )
    if not bot_name:
        targets = item.get("targets") or []
        if targets and isinstance(targets[0], dict):
            bot_name = str(targets[0].get("bot_name") or targets[0].get("name") or "")
        elif targets:
            bot_name = str(targets[0])
    enriched["bot_name"] = str(bot_name or "")
    return enriched


def list_inbox(context: AccountContext, *, status: str = "pending", limit: int = 100) -> Dict[str, Any]:
    """“等你确认”收件箱（第 9 条）。

    主账号：整棵子树的待确认，每条带 source_account_id/name + bot_name；
    子账号：只看到自己的（按 owner_account_id 过滤），看不到别人的。
    """
    from ..workflow.store import get_workflow_store

    if context.is_root and not context.standalone:
        owners = list(context.subtree) or [context.principal]
    else:
        owners = [context.principal]
    items: List[Dict[str, Any]] = []
    try:
        approvals = get_workflow_store().list_approvals(status=status, owners=owners, limit=limit)
        items.extend(enrich_confirmation(item) for item in approvals)
    except Exception as exc:  # noqa: BLE001 - 审批库读不到时不让收件箱整体失败
        logger.warning("a3m inbox: workflow approvals unreadable: %s", exc)
    state = load_state()
    for entry in state.get("pending_confirmations", []):
        if str(entry.get("status") or "") != str(status or "pending"):
            continue
        if str(entry.get("source_account_id") or "") not in owners:
            continue
        items.append(enrich_confirmation(dict(entry)))
    items = sorted(items, key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return {"items": items[: max(0, int(limit))], "count": len(items)}


def record_pending_confirmation(
    source_account: str, bot_name: str, kind: str, title: str, detail: str = ""
) -> Dict[str, Any]:
    """登记一条待确认（带归属），供其他模块在需要人确认时调用。

    登记本身不通知任何人；主账号在收件箱里看到，子账号只看到自己的。
    """
    state = load_state()
    entry = {
        "id": str(uuid.uuid4()),
        "source_account_id": str(source_account),
        "source_account_name": str(source_account),
        "bot_name": str(bot_name or ""),
        "kind": str(kind or ""),
        "title": str(title or ""),
        "detail": str(detail or ""),
        "status": "pending",
        "created_at": _now_iso(),
    }
    state.setdefault("pending_confirmations", []).append(entry)
    save_state(state)
    return entry


def decide_pending_confirmation(
    context: AccountContext, confirmation_id: str, decision: str
) -> Dict[str, Any]:
    """对本模块登记的待确认表态（主账号可决任意一条；子账号只能决自己的）。"""
    if decision not in ("approved", "rejected"):
        raise deny(400, "A3M_DECISION_INVALID", "表态只能是同意或拒绝。")
    state = load_state()
    entries = state.get("pending_confirmations", [])
    target = next(
        (item for item in entries if str(item.get("id") or "") == str(confirmation_id)), None
    )
    if target is None:
        raise deny(404, _accounts.CODE_SUBTREE_DENIED, _accounts.NEUTRAL_NOT_FOUND_MESSAGE)
    if not context.is_root and str(target.get("source_account_id") or "") != context.principal:
        raise deny(403, "A3M_OWNER_ONLY", "没有权限")
    target["status"] = decision
    target["decided_at"] = _now_iso()
    target["decided_by"] = context.principal
    save_state(state)
    return enrich_confirmation(dict(target))


# --------------------------------------------------------------------------- #
# 第 10 条：用量策略——子账号不设任何上限；第 11 条：自助导出（复用 A3s）
# --------------------------------------------------------------------------- #

def usage_policy(context: AccountContext) -> Dict[str, Any]:
    """用量策略端点（第 10 条）。

    子账号：``unlimited: true``，Bot 数量、存储、并发任务、用量花费都不封顶
    （用自己的 Key，花自己的）；主账号：自己的总账单上限照 A2a/A3s 的口径
    返回（本模块不重做上限逻辑）。
    """
    if context.is_root and not context.standalone:
        from .a3s import check_owner_monthly_cap

        return {
            "role": "owner",
            "subaccounts_unlimited": True,
            "owner_cap": check_owner_monthly_cap(context.principal),
        }
    return {
        "role": "member",
        "unlimited": True,
        "cap": None,
        "message": "不设上限：Bot 数量、存储、并发任务、用量花费都不封顶（用自己的 Key，花自己的）。",
    }


def export_self(principal: str, types: Sequence[str]) -> Dict[str, Any]:
    """子账号一键导出自己的东西（第 11 条）：复用 A3s 通用格式导出。

    聊天/任务/记忆/文件清单，文本＋JSON，不带 Key、不含别人的数据
    （A3s 导出里有断言式 Key 检查，见 ``a3s._assert_no_key_material``）。
    """
    from .a3s import export_by_type

    wanted = [str(item) for item in types if str(item).strip()]
    return export_by_type(principal, wanted or ["chat", "tasks", "memory", "files"])


# --------------------------------------------------------------------------- #
# HTTP 面
# --------------------------------------------------------------------------- #

class AssistantProfileBody(BaseModel):
    assistant_name: Optional[str] = Field(None, max_length=20)
    avatar_id: Optional[str] = Field(None, max_length=64)


class OptOutBody(BaseModel):
    opt_out: bool = True


class DispatchBody(BaseModel):
    instruction: str = Field(..., min_length=1, max_length=2000)


class ConfirmationBody(BaseModel):
    source_account_id: Optional[str] = Field(None, max_length=128)
    bot_name: Optional[str] = Field("", max_length=128)
    kind: str = Field(..., min_length=1, max_length=64)
    title: str = Field(..., min_length=1, max_length=200)
    detail: Optional[str] = Field("", max_length=2000)


class DecideBody(BaseModel):
    decision: str = Field(..., min_length=1, max_length=32)


# -- 自己（子账号与主账号都可用，只作用于自己）------------------------------- #

@router.get("/me/key-status")
def http_my_key_status(context: AccountContext = Depends(_principal)) -> Dict[str, Any]:
    """我自己的 Key 配了没有：只给“已配/未配”状态，不给 Key、不给掩码。"""
    return own_key_status(context.principal)


@router.get("/me/assistant")
def http_my_assistant(context: AccountContext = Depends(_principal)) -> Dict[str, Any]:
    return assistant_profile(context.principal)


@router.patch("/me/assistant")
def http_set_my_assistant(
    body: AssistantProfileBody, context: AccountContext = Depends(_principal)
) -> Dict[str, Any]:
    """改自己助手的名/头像（第 8 条：只能改自己的）。"""
    return set_assistant_profile(
        context.principal, assistant_name=body.assistant_name, avatar_id=body.avatar_id
    )


@router.get("/me/export")
def http_my_export(
    types: str = Query("chat,tasks,memory,files"),
    context: AccountContext = Depends(_principal),
) -> Dict[str, Any]:
    """一键导出自己的东西（第 11 条）：通用格式，不带 Key、不含别人的数据。"""
    return export_self(context.principal, [item for item in types.split(",") if item.strip()])


@router.get("/me/usage-policy")
def http_my_usage_policy(context: AccountContext = Depends(_principal)) -> Dict[str, Any]:
    """用量策略（第 10 条）：子账号不设任何上限；主账号看自己的总上限。"""
    return usage_policy(context)


@router.get("/inbox")
def http_inbox(
    status: str = Query("pending"),
    context: AccountContext = Depends(_principal),
) -> Dict[str, Any]:
    """“等你确认”收件箱（第 9 条）：每条写明是哪个子账号、哪个 Bot。"""
    return list_inbox(context, status=status)


@router.post("/confirmations", status_code=201)
def http_record_confirmation(
    body: ConfirmationBody, context: AccountContext = Depends(_principal)
) -> Dict[str, Any]:
    """登记一条待确认：默认归自己；主账号可为子树里的子账号登记。"""
    source = (body.source_account_id or "").strip() or context.principal
    if source != context.principal:
        _require_owner(context)
        _require_target_in_subtree(context, source)
    return record_pending_confirmation(
        source, body.bot_name or "", body.kind, body.title, body.detail or ""
    )


@router.post("/confirmations/{confirmation_id}/decide")
def http_decide_confirmation(
    confirmation_id: str, body: DecideBody, context: AccountContext = Depends(_principal)
) -> Dict[str, Any]:
    return decide_pending_confirmation(context, confirmation_id, body.decision)


# -- 主账号专属：成员名单 / 注册批准 ----------------------------------------- #

@router.get("/owner/members")
def http_owner_members(context: AccountContext = Depends(_principal)) -> Dict[str, Any]:
    """成员名单（第 12 条：只接受主账号，子账号调一律 403）。"""
    _require_owner(context)
    members = list_members()
    return {"members": members, "count": len(members)}


@router.get("/owner/registrations/pending")
def http_owner_pending(context: AccountContext = Depends(_principal)) -> Dict[str, Any]:
    """没批准的注册申请（第 12 条：只接受主账号，子账号调一律 403）。"""
    _require_owner(context)
    pending = list_pending_registrations()
    return {"pending": pending, "count": len(pending)}


@router.post("/owner/registrations/{principal}/approve")
def http_owner_approve(principal: str, context: AccountContext = Depends(_principal)) -> Dict[str, Any]:
    _require_owner(context)
    return approve_registration(context.principal, principal)


@router.post("/owner/registrations/{principal}/reject")
def http_owner_reject(principal: str, context: AccountContext = Depends(_principal)) -> Dict[str, Any]:
    _require_owner(context)
    return reject_registration(context.principal, principal)


@router.post("/owner/members/{principal}/suspend")
def http_owner_suspend(principal: str, context: AccountContext = Depends(_principal)) -> Dict[str, Any]:
    _require_owner(context)
    return suspend_member(context.principal, principal)


@router.post("/owner/members/{principal}/restore")
def http_owner_restore(principal: str, context: AccountContext = Depends(_principal)) -> Dict[str, Any]:
    _require_owner(context)
    return restore_member(context.principal, principal)


@router.post("/owner/members/{principal}/delete")
def http_owner_delete(principal: str, context: AccountContext = Depends(_principal)) -> Dict[str, Any]:
    _require_owner(context)
    return delete_member(context.principal, principal)


@router.post("/owner/members/{principal}/unlock")
def http_owner_unlock(principal: str, context: AccountContext = Depends(_principal)) -> Dict[str, Any]:
    """帮子账号解锁（第 13 条：只接受主账号）。"""
    _require_owner(context)
    return unlock_member(context.principal, principal)


@router.post("/owner/members/{principal}/reset-password")
def http_owner_reset_password(principal: str, context: AccountContext = Depends(_principal)) -> Dict[str, Any]:
    """帮子账号重置密码（第 13 条：只接受主账号；临时密码只在本次响应里出现一次）。"""
    _require_owner(context)
    return reset_member_password(context.principal, principal)


@router.post("/owner/members/{principal}/absorption-opt-out")
def http_owner_absorption_opt_out(
    principal: str, body: OptOutBody, context: AccountContext = Depends(_principal)
) -> Dict[str, Any]:
    """单独关掉/打开对某一个人的吸取（第 3 条：只接受主账号）。"""
    _require_owner(context)
    return set_absorption_opt_out(context.principal, principal, body.opt_out)


# -- 主账号专属：跨账号只读 -------------------------------------------------- #

@router.get("/owner/accounts/{principal}")
def http_owner_account_summary(principal: str, context: AccountContext = Depends(_principal)) -> Dict[str, Any]:
    """主账号看某子账号的一页汇总（第 1 条：只接受主账号）。"""
    _require_owner(context)
    _require_target_in_subtree(context, principal)
    return owner_account_summary(context.principal, principal)


@router.get("/owner/accounts/{principal}/{kind}")
def http_owner_read(
    principal: str, kind: str, context: AccountContext = Depends(_principal)
) -> Dict[str, Any]:
    """主账号按子账号读一样东西（第 1 条：只接受主账号；kind 见 READ_KINDS）。"""
    _require_owner(context)
    _require_target_in_subtree(context, principal)
    return owner_read_account(context.principal, principal, kind)


@router.get("/owner/accounts/{principal}/assistant")
def http_owner_assistant(principal: str, context: AccountContext = Depends(_principal)) -> Dict[str, Any]:
    """主账号看某子账号的助手名/头像标识（第 8 条：只接受主账号）。"""
    _require_owner(context)
    _require_target_in_subtree(context, principal)
    return assistant_profile(principal)


# -- 主账号专属：吸取 -------------------------------------------------------- #

@router.post("/owner/absorb/{principal}")
def http_owner_absorb(principal: str, context: AccountContext = Depends(_principal)) -> Dict[str, Any]:
    """从某子账号吸取新记忆与技能（第 3 条：只接受主账号）。"""
    _require_owner(context)
    _require_target_in_subtree(context, principal)
    return absorb_from_member(context.principal, principal)


@router.post("/owner/absorb-all")
def http_owner_absorb_all(context: AccountContext = Depends(_principal)) -> Dict[str, Any]:
    """把所有子账号的新记忆与技能吸一遍（第 3 条：只接受主账号）。"""
    _require_owner(context)
    return absorb_all(context.principal)


@router.get("/owner/absorbed")
def http_owner_absorbed(
    source: Optional[str] = Query(None),
    context: AccountContext = Depends(_principal),
) -> Dict[str, Any]:
    """主账号看吸来的记忆与技能（第 3 条：只接受主账号，每条标明来源）。"""
    _require_owner(context)
    items = list_absorbed(source=source)
    return {"absorbed": items, "count": len(items)}


# -- 主账号专属：Bot 操控 ---------------------------------------------------- #

@router.post("/owner/bots/{principal}/{bot_id}/dispatch")
def http_owner_dispatch(
    principal: str, bot_id: str, body: DispatchBody, context: AccountContext = Depends(_principal)
) -> Dict[str, Any]:
    """给某子账号的 Bot 派活（第 4 条：只接受主账号；子账号那边不可见）。"""
    _require_owner(context)
    _require_target_in_subtree(context, principal)
    return dispatch_bot(context.principal, principal, bot_id, body.instruction)


@router.post("/owner/bots/{principal}/{bot_id}/pause")
def http_owner_pause(
    principal: str, bot_id: str, context: AccountContext = Depends(_principal)
) -> Dict[str, Any]:
    """暂停某子账号的 Bot 正在干的活（第 4 条：只接受主账号）。"""
    _require_owner(context)
    _require_target_in_subtree(context, principal)
    return pause_bot(context.principal, principal, bot_id)


@router.post("/owner/bots/{principal}/{bot_id}/collect")
def http_owner_collect(
    principal: str, bot_id: str, context: AccountContext = Depends(_principal)
) -> Dict[str, Any]:
    """收回某子账号的 Bot 的结果（第 4 条：只接受主账号）。"""
    _require_owner(context)
    _require_target_in_subtree(context, principal)
    return collect_bot_result(context.principal, principal, bot_id)


@router.get("/owner/bot-controls")
def http_owner_bot_controls(
    target: Optional[str] = Query(None),
    context: AccountContext = Depends(_principal),
) -> Dict[str, Any]:
    """派活记录（第 4 条：只出现在主账号这边，子账号调一律 403）。"""
    _require_owner(context)
    records = list_bot_controls(target=target)
    return {"controls": records, "count": len(records)}


# -- 主账号专属：审计镜像 / 回收站 ------------------------------------------- #

@router.get("/owner/audit")
def http_owner_audit(
    limit: int = Query(100, ge=1, le=500),
    context: AccountContext = Depends(_principal),
) -> Dict[str, Any]:
    """主账号可见的跨账号操作审计（第 14 条：只接受主账号）。"""
    _require_owner(context)
    entries = list_owner_audit(limit=limit)
    return {"entries": entries, "count": len(entries)}


@router.get("/owner/trash")
def http_owner_trash(context: AccountContext = Depends(_principal)) -> Dict[str, Any]:
    """回收站里的账号（第 6 条：只接受主账号）。"""
    _require_owner(context)
    entries = list_trash()
    return {"trash": entries, "count": len(entries)}


@router.post("/owner/trash/purge-expired")
def http_owner_purge(context: AccountContext = Depends(_principal)) -> Dict[str, Any]:
    """真删已过 30 天的回收站账号（第 6 条：只接受主账号）。"""
    _require_owner(context)
    return purge_expired_trash(context.principal)
