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

router = APIRouter(prefix="/v1", tags=["chat"])

# 进程内会话表：session_id -> 鎏灏实例（各自独立历史，principal 即 session 隔离键）。
_sessions: Dict[str, LiuHaoAssistant] = {}
_sessions_lock = threading.Lock()


def get_assistant(session_id: str) -> LiuHaoAssistant:
    """获取或创建会话实例（线程安全，多用户并发隔离）。"""
    with _sessions_lock:
        if session_id not in _sessions:
            _sessions[session_id] = LiuHaoAssistant(name=f"liuhao-{session_id}")
        return _sessions[session_id]


def list_sessions() -> List[str]:
    """返回活跃会话 ID 列表（有序）。"""
    with _sessions_lock:
        return sorted(_sessions.keys())


def delete_session(session_id: str) -> bool:
    """删除一个会话：清空其持久化历史并移除实例。返回是否成功。"""
    with _sessions_lock:
        assistant = _sessions.pop(session_id, None)
    if assistant is None:
        return False
    # reset 会清空该 principal 的对话历史（conversation store），不影响其它会话。
    assistant.reset()
    return True


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="用户消息")
    session_id: Optional[str] = Field("default", description="会话 ID（多轮独立）")


class ChatResponse(BaseModel):
    reply: str
    status: str
    turn: int
    correlation_id: str
    agent: str


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    assistant = get_assistant(req.session_id)
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
    assistant = get_assistant(req.session_id)

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
    """列出活跃会话（多用户隔离可见性）。"""
    return {"sessions": list_sessions(), "count": len(_sessions)}


@router.delete("/chat/sessions/{session_id}")
def chat_delete_session(session_id: str) -> Dict[str, object]:
    """删除会话（清空历史 + 移除实例）。"""
    deleted = delete_session(session_id)
    return {"session_id": session_id, "deleted": deleted}
