"""鎏灏对话 HTTP 端点。

- ``POST /v1/chat``          发送一条消息，返回结构化回复
- ``GET  /v1/chat/stats``    查看会话统计

每个 ``session_id`` 维护独立的多轮历史（不串会话）。默认 session "default"。
provider 由环境变量 AI_PROVIDER_TYPE / AI_PROVIDER_MODEL 控制（默认 ollama）。
"""

from __future__ import annotations

import json
from typing import Dict, Optional

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..ai.liuhao import LiuHaoAssistant

router = APIRouter(prefix="/v1", tags=["chat"])

# 进程内会话表：session_id -> 鎏灏实例（各自独立历史）。
_sessions: Dict[str, LiuHaoAssistant] = {}


def get_assistant(session_id: str) -> LiuHaoAssistant:
    if session_id not in _sessions:
        _sessions[session_id] = LiuHaoAssistant(name=f"liuhao-{session_id}")
    return _sessions[session_id]


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
    - ``{"type": "done", "turn": N, ...}``      结束帧（落盘/审计已完成）
    """
    assistant = get_assistant(req.session_id)

    def event_stream():
        for token in assistant.chat_stream(req.message):
            payload = {"type": "token", "content": token}
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
