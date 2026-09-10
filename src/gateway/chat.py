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

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..ai.liuhao import LiuHaoAssistant
from ..ai.conversation_store import get_conversation_store

router = APIRouter(prefix="/v1", tags=["chat"])

# 进程内会话表：session_id -> 鎏灏实例（各自独立历史，principal 即 session 隔离键）。
_sessions: Dict[str, LiuHaoAssistant] = {}
# 会话 -> 用户 归属（用于画像跨会话共享；不影响历史隔离）。
_session_users: Dict[str, str] = {}
_sessions_lock = threading.Lock()


def get_assistant(session_id: str, user_id: Optional[str] = None) -> LiuHaoAssistant:
    """获取或创建会话实例（线程安全，多用户并发隔离）。

    ``user_id`` 存在时，画像主体取 ``user-{user_id}`` —— 同一用户的多个会话
    **共享画像**；会话历史仍按 ``liuhao-{session_id}`` 隔离（二者刻意解耦，
    否则共享画像会把多会话历史也串在一起）。
    """
    with _sessions_lock:
        if session_id in _sessions and user_id and _session_users.get(session_id) != user_id:
            # 同一会话换了用户：重建实例以重新绑定画像主体（历史从 store 重载，不丢）。
            _sessions.pop(session_id, None)
        if session_id not in _sessions:
            profile_principal = f"user-{user_id}" if user_id else None
            _sessions[session_id] = LiuHaoAssistant(
                name=f"liuhao-{session_id}", profile_principal=profile_principal
            )
            if user_id:
                _session_users[session_id] = user_id
        return _sessions[session_id]


def list_sessions() -> List[str]:
    """返回活跃会话 ID 列表（有序）。"""
    with _sessions_lock:
        return sorted(_sessions.keys())


def delete_session(session_id: str) -> bool:
    """删除一个会话：清空其持久化历史并移除实例。返回是否成功。"""
    with _sessions_lock:
        assistant = _sessions.pop(session_id, None)
        _session_users.pop(session_id, None)
    if assistant is None:
        return False
    # reset 会清空该 principal 的对话历史（conversation store），不影响其它会话。
    assistant.reset()
    return True


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="用户消息")
    session_id: Optional[str] = Field("default", description="会话 ID（多轮独立）")
    user_id: Optional[str] = Field(None, description="用户 ID（同用户跨会话共享画像）")


class ChatResponse(BaseModel):
    reply: str
    status: str
    turn: int
    correlation_id: str
    agent: str


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    assistant = get_assistant(req.session_id, req.user_id)
    result = assistant.chat(req.message)
    return ChatResponse(
        reply=result["reply"],
        status=result["status"],
        turn=result["turn"],
        correlation_id=result["correlation_id"],
        agent=result["agent"],
    )


@router.post("/chat/stream")
def chat_stream(req: ChatRequest) -> StreamingResponse:
    """流式对话（Server-Sent Events）。

    每个事件一行 ``data: {json}``：

    - ``{"type": "token", "content": "..."}``  逐 token
    - ``{"type": "tool", "name": ..., "args": ..., "output": ...}``  工具调用
    - ``{"type": "done", "turn": N, ...}``      结束帧（落盘/审计已完成）
    """
    assistant = get_assistant(req.session_id, req.user_id)

    def event_stream():
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
        }
        yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/chat/stats")
def chat_stats(session_id: str = "default") -> Dict[str, object]:
    assistant = get_assistant(session_id)
    return assistant.stats()


@router.get("/chat/sessions")
def chat_sessions() -> Dict[str, object]:
    """列出活跃会话（多用户隔离可见性）。

    ``users`` 给出 会话→用户 归属（``null`` 表示该会话未声明 user_id，
    画像主体回退为会话自身）。
    """
    sessions = list_sessions()
    return {
        "sessions": sessions,
        "count": len(sessions),
        "users": {s: _session_users.get(s) for s in sessions},
    }


@router.get("/chat/history")
def chat_history(session_id: str = "default", user_id: Optional[str] = None) -> Dict[str, object]:
    """返回某会话的历史消息（从持久化 store 读，跨后端重启有效）。

    历史按会话隔离；``profile_principal`` 单独给出该会话读写的画像主体
    （传 ``user_id`` 时为 ``user-{user_id}``，跨会话共享）。
    """
    principal = f"liuhao-{session_id}"
    history = get_conversation_store().load(principal)
    return {
        "session_id": session_id,
        "history": history,
        "count": len(history),
        "user_id": user_id or _session_users.get(session_id),
        "profile_principal": f"user-{user_id}" if user_id else (
            f"user-{_session_users[session_id]}"
            if session_id in _session_users
            else principal
        ),
    }


@router.delete("/chat/sessions/{session_id}")
def chat_delete_session(session_id: str) -> Dict[str, object]:
    """删除会话（清空历史 + 移除实例）。"""
    deleted = delete_session(session_id)
    return {"session_id": session_id, "deleted": deleted}
