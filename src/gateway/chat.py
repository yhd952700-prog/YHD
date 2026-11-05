"""鎏灏对话 HTTP 端点。

- ``POST /v1/chat``          发送一条消息，返回结构化回复
- ``GET  /v1/chat/stats``    查看会话统计

每个 ``session_id`` 维护独立的多轮历史（不串会话）。默认 session "default"。
provider 由环境变量 AI_PROVIDER_TYPE / AI_PROVIDER_MODEL 控制（默认 ollama）。
"""

from __future__ import annotations

import json
import threading
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..ai.liuhao import LiuHaoAssistant
from ..ai.model_router import provider_for_task
from ..ai.conversation_store import get_conversation_store
from .knowledge_context import (
    build_knowledge_context,
    knowledge_prompt,
    public_metadata,
)
from .policy import require_human_principal

router = APIRouter(prefix="/v1", tags=["chat"])

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
_sessions_lock = threading.Lock()


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
    session_id: str, principal: Optional[str] = None, model_id: Optional[str] = None
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
    with _sessions_lock:
        if key not in _sessions:
            _sessions[key] = LiuHaoAssistant(
                name=history_principal, profile_principal=profile_principal
            )
            if principal:
                _session_users[key] = principal
        assistant = _sessions[key]
        # 热加载：显式 model_id 优先；否则按「对话」任务类型在模型管理里路由，
        # 未命中回落默认模型（与旧行为一致），决策写日志便于核对。
        assistant.provider = provider_for_task("chat", model_id=model_id)
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
    if assistant is None:
        return False
    # reset 会清空该 principal 的对话历史（conversation store），不影响其它会话。
    assistant.reset()
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


@router.post("/chat", response_model=ChatResponse)
def chat(
    req: ChatRequest, principal: str = Depends(require_human_principal)
) -> ChatResponse:
    # 主体只来自令牌；请求体里的 user_id（即使传了）一律不采信。
    assistant = get_assistant(req.session_id, principal, req.model_id)
    # 先检索再生成：空库/不可用/检索失败都静默降级，元信息如实说明。
    knowledge = build_knowledge_context(req.message, enabled=req.use_knowledge)
    with knowledge_prompt(assistant, knowledge):
        result = assistant.chat(req.message)
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
    assistant = get_assistant(req.session_id, principal, req.model_id)
    knowledge = build_knowledge_context(req.message, enabled=req.use_knowledge)

    def event_stream():
        # 提示词注入必须覆盖整个惰性生成周期；生成器关闭时 finally 恢复原值。
        with knowledge_prompt(assistant, knowledge):
            for item in assistant.chat_stream(req.message):
                if isinstance(item, dict):
                    # 工具调用事件：结构化透传（type/name/args/output）。
                    yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                else:
                    payload = {"type": "token", "content": item}
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        # 生成器耗尽时 chat_stream 已完成落盘 + 审计，发结束帧。
        done = {
            "type": "done",
            "turn": assistant.turn,
            "session_id": req.session_id,
            "knowledge": public_metadata(knowledge),
        }
        yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/chat/stats")
def chat_stats(
    session_id: str = "default", principal: str = Depends(require_human_principal)
) -> Dict[str, object]:
    assistant = get_assistant(session_id, principal)
    return assistant.stats()


@router.get("/chat/sessions")
def chat_sessions(
    principal: str = Depends(require_human_principal),
) -> Dict[str, object]:
    """列出**当前主体**的活跃会话（不会暴露别人的会话名）。

    ``users`` 给出 会话→主体 归属，值恒为令牌主体 —— 归属是派生事实，
    不再接受客户端声明。
    """
    sessions = list_sessions(principal)
    return {
        "sessions": sessions,
        "count": len(sessions),
        "users": {s: principal for s in sessions},
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
    session_id: str = "default", principal: str = Depends(require_human_principal)
) -> Dict[str, object]:
    """返回某会话的历史消息（从持久化 store 读，跨后端重启有效）。"""
    return read_history(session_id, principal)


@router.delete("/chat/sessions/{session_id}")
def chat_delete_session(
    session_id: str, principal: str = Depends(require_human_principal)
) -> Dict[str, object]:
    """删除**自己的**会话（清空历史 + 移除实例）；别人的会话按不存在处理。"""
    deleted = delete_session(session_id, principal)
    return {"session_id": session_id, "deleted": deleted}
