"""鎏灏对话 HTTP 端点。

- ``POST /v1/chat``          发送一条消息，返回结构化回复
- ``GET  /v1/chat/stats``    查看会话统计

每个 ``session_id`` 维护独立的多轮历史（不串会话）。默认 session "default"。
provider 由环境变量 AI_PROVIDER_TYPE / AI_PROVIDER_MODEL 控制（默认 ollama）。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .knowledge_context import (
    build_knowledge_context,
    knowledge_prompt,
    public_metadata,
)
from .policy import require_human_principal
from .account_context import (
    AccountContext,
    audit_key_use,
    deny,
    key_scope_for,
    require_account_principal,
)
from ..kernels.identity import _accounts
from .account_usage import record_usage
from ..ai.model_config import ModelConfigError

router = APIRouter(prefix="/v1", tags=["chat"])

logger = logging.getLogger(__name__)


# ─── 重量级依赖的惰性接缝 ────────────────────────────────────
#
# 为什么**不能**在模块顶层 ``from ..ai.liuhao import LiuHaoAssistant``：
# ``src.ai.liuhao`` → ``.employee`` → ``..knowledge.memory`` → ``mem0``，而
# mem0 会连带导入 qdrant_client / openai / opentelemetry / grpc / numpy。
# 实测这一步在冷启动里占 **4.4 秒**（本机，空数据目录），但它只在"真的要
# 对话"时才需要，与"登录 / 健康检查 / 模型配置"无关。把它留在启动路径上
# 就是拿用户等待时间换模块整洁。
#
# 保留同名模块级函数而不是在端点内直接 import，是因为测试把
# ``chat_module.LiuHaoAssistant`` / ``chat_module.get_conversation_store``
# 当作真实接缝替换（monkeypatch）；这些名字必须继续可被替换、可被调用。
def LiuHaoAssistant(*args, **kwargs):  # noqa: N802 - 保持原类名以兼容测试接缝
    from ..ai.liuhao import LiuHaoAssistant as _impl

    return _impl(*args, **kwargs)


def provider_for_task(task_type: str, *, model_id=None, key_scope=None):
    from ..ai.model_router import provider_for_task as _impl

    if key_scope is None:
        return _impl(task_type, model_id=model_id)
    return _impl(
        task_type,
        model_id=model_id,
        visible_to=list(key_scope.visible_to),
        allow_unowned=key_scope.allow_unowned,
        allow_owners=list(key_scope.allow_owners),
        allow_env_fallback=key_scope.is_root,
    )


def get_conversation_store():
    from ..ai.conversation_store import get_conversation_store as _impl

    return _impl()


def warmup() -> None:
    """后台预热对话链的重依赖（不阻塞就绪；失败由调用方如实上报）。

    只做导入，不创建实例、不读模型配置 —— 因此不会产生额外副作用，也不会
    因为"用户还没配模型"而失败。如果这里失败，第一次真实对话也会以同样的
    方式失败，而且错误会在启动日志与 ``/v1/startup`` 的 background.errors 里
    出现，不会被拖到用户按下发送键才暴露。
    """
    from ..ai.conversation_store import get_conversation_store as _store  # noqa: F401
    from ..ai.liuhao import LiuHaoAssistant as _assistant  # noqa: F401
    from ..ai.model_router import provider_for_task as _router  # noqa: F401


# 进程内会话表：**principal 与 session_id 的复合键** -> 鎏灏实例。
#
# 复合键是冲突隔离的关键：此前直接拿客户端传来的 ``session_id`` 当键，于是
# 另一个已登录用户只要猜中 ``"default"`` 就能读到同一实例的历史与画像。
# 现在键里带上 **令牌主体**（``sub``），两个用户即使都用 ``session_id=default``
# 也是两份互不可见实例。
_SESSION_KEY_SEP = "\x00"
_sessions: Dict[str, LiuHaoAssistant] = {}
# 复合键 -> 主体（供会话列表如实展示归属；历史隔离不依赖它）。
_session_users: Dict[str, str] = {}
# 会话容量上限：此前 _sessions 无上限，任何一次失败请求/大量 session_id
# 都能让进程里无限累积 assistant 及其 provider 句柄。超限按 LRU 淘汰。
_MAX_SESSIONS_DEFAULT = 128
_session_last_used: Dict[str, float] = {}
_sessions_lock = threading.Lock()


def _max_sessions() -> int:
    raw = (os.environ.get("LIUHAO_CHAT_MAX_SESSIONS") or "").strip()
    try:
        value = int(raw) if raw else _MAX_SESSIONS_DEFAULT
    except ValueError:
        value = _MAX_SESSIONS_DEFAULT
    return max(1, value)


def _close_provider(provider) -> None:
    """Best-effort close of a single provider instance."""
    closer = getattr(provider, "close", None)
    if callable(closer):
        try:
            closer()
        except Exception:
            pass


def _begin_turn(assistant: LiuHaoAssistant) -> None:
    fn = getattr(assistant, "begin_turn", None)
    if callable(fn):
        fn()


def _end_turn(assistant: LiuHaoAssistant) -> None:
    fn = getattr(assistant, "end_turn", None)
    if callable(fn):
        fn()


def _session_key(principal: Optional[str], session_id: str) -> str:
    """复合键：有 principal 时与 session_id 绑定，否则退回纯 session_id（兼容纯函数调用）。"""
    return f"{principal}{_SESSION_KEY_SEP}{session_id}" if principal else session_id


def _owner_of(key: str) -> Optional[str]:
    if _SESSION_KEY_SEP in key:
        return key.split(_SESSION_KEY_SEP, 1)[0] or None
    return None


def _bare_session_id(key: str) -> str:
    return key.split(_SESSION_KEY_SEP, 1)[1] if _SESSION_KEY_SEP in key else key


def get_assistant(
    session_id: str,
    principal: Optional[str] = None,
    model_id: Optional[str] = None,
    key_scope=None,
) -> LiuHaoAssistant:
    """获取或创建会话实例（线程安全，按主体隔离）。

    ``principal`` 必须来自 **身份令牌**（``require_human_principal``），绝不可取
    客户端请求体里的 ``user_id``。存在时：

    * 会话历史主体为 ``liuhao-{principal}-{session_id}``（跨用户/跨会话均隔离）；
    * 画像主体为 ``user-{principal}`` —— 同一主体的多个会话**共享画像**。

    ``model_id`` 为空时按模型配置中心**当前的默认模型**解析 —— 每次请求都
    重新解析，因此界面上切换默认模型/改密钥后，已存在的会话也会在下一轮
    立即换用新配置（不需要重启后端）。
    """
    key = _session_key(principal, session_id)
    history_principal = (
        f"liuhao-{principal}-{session_id}" if principal else f"liuhao-{session_id}"
    )
    profile_principal = f"user-{principal}" if principal else None
    # 先解析 provider，再创建会话：provider 解析失败（如成员没有可用模型，
    # 返回 409）时绝不留下一个空 assistant。此前是先插入 _sessions 再解析，
    # 实测 member 连续 10 次 409 会留下 10 个空会话。
    provider = provider_for_task("chat", model_id=model_id, key_scope=key_scope)
    with _sessions_lock:
        assistant = _sessions.get(key)
        if assistant is None:
            try:
                assistant = LiuHaoAssistant(
                    name=history_principal,
                    profile_principal=profile_principal,
                    provider=provider,
                )
            except Exception:
                _close_provider(provider)
                raise
            # 容量上限：先淘汰最久未使用的会话，再插入新会话。
            while _sessions and len(_sessions) >= _max_sessions():
                if _session_last_used:
                    oldest = min(_session_last_used, key=_session_last_used.get)
                else:
                    oldest = next(iter(_sessions))
                evicted = _sessions.pop(oldest, None)
                _session_users.pop(oldest, None)
                _session_last_used.pop(oldest, None)
                if evicted is not None:
                    closer = getattr(evicted, "close", None)
                    if callable(closer):
                        try:
                            closer()
                        except Exception:
                            pass
            if principal:
                _session_users[key] = principal
            _sessions[key] = assistant
        else:
            # 热加载：显式 model_id 优先；否则按「对话」任务类型在模型管理里
            # 路由，未命中回落默认模型。替换时按活跃轮次安全关闭旧 provider。
            assistant.set_provider(provider)
        _session_last_used[key] = time.monotonic()
        return assistant


def list_sessions(principal: Optional[str] = None) -> List[str]:
    """返回活跃会话 ID 列表（有序）。

    ``principal`` 非空时只返回该主体的会话 —— HTTP 面必须传令牌主体，
    否则一个用户能看到别人的会话名。``None`` 仅用于进程内全局视图
    （如驾驶舱聚合统计）。
    """
    with _sessions_lock:
        keys = [
            key
            for key in _sessions
            if principal is None or _owner_of(key) == principal
        ]
    return sorted(_bare_session_id(key) for key in keys)


def delete_session(session_id: str, principal: Optional[str] = None) -> bool:
    """删除一个会话：清空其持久化历史并移除实例。返回是否成功。

    ``principal`` 非空时只能删自己的会话（复合键命中）；不传则按纯
    ``session_id`` 命中（仅供进程内/测试直接调用）。
    """
    key = _session_key(principal, session_id)
    with _sessions_lock:
        assistant = _sessions.pop(key, None)
        _session_users.pop(key, None)
        _session_last_used.pop(key, None)
    if assistant is None:
        return False
    # reset 会清空该 principal 的对话历史（conversation store），不影响其它会话。
    assistant.reset()
    closer = getattr(assistant, "close", None)
    if callable(closer):
        try:
            closer()
        except Exception:
            pass
    return True


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000, description="用户消息")
    session_id: Optional[str] = Field("default", description="会话 ID（多轮独立）")
    model_id: Optional[str] = Field(
        None, description="模型配置 id 或名称；留空则用当前默认模型"
    )
    use_knowledge: Optional[bool] = Field(
        None,
        description=(
            "是否检索知识库并注入上下文。null/省略=自动（有文档就开），"
            "false=本次关闭，true=本次启用。"
        ),
    )


class ChatResponse(BaseModel):
    reply: str
    status: str
    turn: int
    correlation_id: str
    agent: str
    #: 始终存在：说明本次是否使用知识库、命中来源；未使用必有原因。
    knowledge: Dict[str, object]


def _assistant_for(req: "ChatRequest", principal: str) -> LiuHaoAssistant:
    """按账号 Key 作用域构造会话；越级/冒用在此转成稳定错误码。"""
    scope = key_scope_for(principal=principal)
    try:
        assistant = get_assistant(
            req.session_id, principal, req.model_id, key_scope=scope
        )
    except ModelConfigError as exc:
        audit_key_use(
            scope,
            None,
            outcome="denied",
            source="explicit",
            extra={"model_config_id": req.model_id, "error_code": exc.code},
        )
        detail: object = (
            {"code": exc.code, "message": exc.detail} if exc.code else exc.detail
        )
        raise HTTPException(status_code=exc.status_code, detail=detail)
    owner = str(getattr(assistant.provider, "owner_principal", "") or "")
    if owner and owner != principal and scope.can_use(owner):
        # 主账号使用成员 Key：actor 仍是主账号，另记 key_owner 与授权来源。
        audit_key_use(scope, owner, outcome="allowed", source="authorized_share")
    return assistant


def _record_chat_usage(
    assistant: LiuHaoAssistant,
    principal: str,
    req: "ChatRequest",
    *,
    tokens_in: int,
    tokens_out: int,
    correlation_id: str,
) -> bool:
    """记一行用量：账单归 ``key_owner``，用量归真实 actor。

    返回是否真的写入了账本。失败不阻断对话，但调用方必须感知到（流式结束帧
    携带 ``usage_recorded``，readiness 里也有 ``usage_ledger`` 状态）。
    """
    owner = str(getattr(assistant.provider, "owner_principal", "") or "") or principal
    model = str(getattr(assistant.provider, "model", "") or req.model_id or "")
    recorded = record_usage(
        actor=principal,
        key_owner=owner,
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        session_id=req.session_id,
        correlation_id=correlation_id,
    )
    if not recorded:
        logger.error(
            "chat usage was NOT recorded (session=%s actor=%s)", req.session_id, principal
        )
    return recorded


def _estimate_tokens(text: str) -> int:
    """无 provider usage 时的保守估算；账本里标记 ``estimated=1``。"""
    return max(1, len(str(text or "")) // 4)


@router.post("/chat", response_model=ChatResponse)
def chat(
    req: ChatRequest, principal: str = Depends(require_human_principal)
) -> ChatResponse:
    # 主体只来自令牌；请求体里的 user_id（即使传了）一律不采信。
    assistant = _assistant_for(req, principal)
    # 先检索再生成：空库/不可用/检索失败都静默降级，元信息如实说明。
    knowledge = build_knowledge_context(
        req.message, enabled=req.use_knowledge, principal=principal
    )
    with knowledge_prompt(assistant, knowledge):
        _begin_turn(assistant)
        try:
            result = assistant.chat(req.message)
        finally:
            _end_turn(assistant)
    if result.get("status") == "completed":
        _record_chat_usage(
            assistant,
            principal,
            req,
            tokens_in=_estimate_tokens(req.message),
            tokens_out=_estimate_tokens(result.get("reply")),
            correlation_id=str(result.get("correlation_id") or ""),
        )
    return ChatResponse(
        reply=result["reply"],
        status=result["status"],
        turn=result["turn"],
        correlation_id=result["correlation_id"],
        agent=result["agent"],
        knowledge=public_metadata(knowledge),
    )


@router.post("/chat/stream")
def chat_stream(
    req: ChatRequest, principal: str = Depends(require_human_principal)
) -> StreamingResponse:
    """流式对话（Server-Sent Events）。

    每个事件一行 ``data: {json}``：

    - ``{"type": "token", "content": "..."}``  逐 token
    - ``{"type": "tool", "name": ..., "args": ..., "output": ...}``  工具调用
    - ``{"type": "done", "turn": N, ...}``      结束帧（落盘/审计已完成）
    """
    assistant = _assistant_for(req, principal)
    knowledge = build_knowledge_context(
        req.message, enabled=req.use_knowledge, principal=principal
    )

    def event_stream():
        # 提示词注入必须覆盖整个惰性生成周期；生成器关闭时 finally 恢复原值。
        reply_chars = 0
        _begin_turn(assistant)
        try:
            with knowledge_prompt(assistant, knowledge):
                for item in assistant.chat_stream(req.message):
                    if isinstance(item, dict):
                        # 工具调用事件：结构化透传（type/name/args/output）。
                        yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                    else:
                        reply_chars += len(str(item))
                        payload = {"type": "token", "content": item}
                        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        finally:
            _end_turn(assistant)
        # 生成器耗尽时 chat_stream 已完成落盘 + 审计，先记用量再发结束帧。
        # 只有真实成功的轮次才记账：失败/拒绝不能产生"已用量"假账。
        turn_status = str(getattr(assistant, "last_turn_status", "") or "completed")
        turn_error = getattr(assistant, "last_turn_error", None)
        correlation_id = str(getattr(assistant, "last_turn_correlation_id", "") or "")
        usage_recorded = False
        if turn_status == "completed":
            usage_recorded = _record_chat_usage(
                assistant,
                principal,
                req,
                tokens_in=_estimate_tokens(req.message),
                tokens_out=max(1, reply_chars // 4),
                correlation_id=correlation_id,
            )
        # 发结束帧：status 让前端能区分成功/失败/拒绝，而不是把 200 + done
        # 当成成功。
        done = {
            "type": "done",
            "status": turn_status,
            "turn": assistant.turn,
            "session_id": req.session_id,
            "correlation_id": correlation_id,
            "usage_recorded": usage_recorded,
            "knowledge": public_metadata(knowledge),
        }
        if turn_status != "completed":
            done["error"] = str(turn_error or "生成失败")
        yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _session_owned_by_other(principal: str, session_id: str) -> bool:
    """该会话名是否被**其他主体**占用（用于把越权读取判成 404）。

    只看活跃实例：持久化层按主体分键（``liuhao-<principal>-<session>``），
    别人无法枚举，也不需要由此推断"某个名字是否存在"。
    """
    key = _session_key(principal, session_id)
    if key in _sessions:
        return False
    with _sessions_lock:
        for other_key in _sessions:
            owner = _owner_of(other_key)
            if owner and owner != principal and _bare_session_id(other_key) == session_id:
                return True
    return False


def _neutral_not_found() -> HTTPException:
    return deny(404, _accounts.CODE_SUBTREE_DENIED, _accounts.NEUTRAL_NOT_FOUND_MESSAGE)


@router.get("/chat/stats")
def chat_stats(
    session_id: str = "default",
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, object]:
    if _session_owned_by_other(context.principal, session_id):
        raise _neutral_not_found()
    scope = key_scope_for(context)
    assistant = get_assistant(session_id, context.principal, key_scope=scope)
    return assistant.stats()


@router.get("/chat/sessions")
def chat_sessions(
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, object]:
    """列出**当前主体**的活跃会话（不会暴露别人的会话名）。

    ``users`` 给出 会话→主体 归属，值恒为令牌主体 —— 归属是派生事实，
    不再接受客户端声明。
    """
    sessions = list_sessions(context.principal)
    return {
        "sessions": sessions,
        "count": len(sessions),
        "users": {s: context.principal for s in sessions},
    }


def read_history(
    session_id: str = "default", principal: Optional[str] = None
) -> Dict[str, object]:
    """从持久化 store 读取某会话的历史（按主体+会话复合隔离）。

    ``profile_principal`` 单独给出该会话读写的画像主体：有主体时恒为
    ``user-{principal}``（跨会话共享）。
    """
    if principal:
        history_principal = f"liuhao-{principal}-{session_id}"
        profile_principal = f"user-{principal}"
    else:
        history_principal = f"liuhao-{session_id}"
        profile_principal = history_principal
    history = get_conversation_store().load(history_principal)
    return {
        "session_id": session_id,
        "history": history,
        "count": len(history),
        "user_id": principal,
        "profile_principal": profile_principal,
    }


@router.get("/chat/history")
def chat_history(
    session_id: str = "default",
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, object]:
    """返回某会话的历史消息（从持久化 store 读，跨后端重启有效）。

    别人占用的会话名一律 404（与"没有历史"不可区分）。
    """
    if _session_owned_by_other(context.principal, session_id):
        raise _neutral_not_found()
    return read_history(session_id, context.principal)


@router.delete("/chat/sessions/{session_id}")
def chat_delete_session(
    session_id: str,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, object]:
    """删除**自己的**会话（清空历史 + 移除实例）；别人的会话按不存在处理（404）。"""
    if not delete_session(session_id, context.principal):
        raise _neutral_not_found()
    return {"session_id": session_id, "deleted": True}
