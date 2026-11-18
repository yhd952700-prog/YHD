"""V2(b)：简报播报 HTTP 面。

* ``POST /v1/briefing/broadcast`` — 生成简报并开始合成（聊天工具同路）。
* ``POST /v1/briefing/broadcast/stop`` — 停播。
* ``GET  /v1/briefing/broadcast/status`` — preparing/ready/done/stopped/failed。
* ``GET  /v1/briefing/broadcast/pending`` — 主题层播放器轮询（ready 才返回）。
* ``GET  /v1/briefing/broadcast/{id}/audio/{index}`` — 逐段 wav。
* ``POST /v1/briefing/broadcast/{id}/played`` — 播放端回执，防重复播。

账号口径与 voice.py 一致：只管自己（ViewAs 下 403）。挂载时由
``main.py`` 加人类主权闸门。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from src.tasks.briefing_speech import get_briefing_speech_service

from .account_context import AccountContext, deny, require_account_principal

router = APIRouter(prefix="/v1/briefing", tags=["briefing"])


def _require_self(context: AccountContext) -> str:
    if getattr(context, "viewed_as_by", None):
        raise deny(403, "ACCOUNT_SCOPE_DENIED", "无权执行该操作")
    return context.principal


@router.post("/broadcast")
def start_broadcast(
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    owner = _require_self(context)
    return get_briefing_speech_service().start(owner)


@router.post("/broadcast/stop")
def stop_broadcast(
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    owner = _require_self(context)
    return get_briefing_speech_service().stop(owner)


@router.get("/broadcast/status")
def broadcast_status(
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    owner = _require_self(context)
    return get_briefing_speech_service().status(owner)


@router.get("/broadcast/pending")
def broadcast_pending(
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    owner = _require_self(context)
    pending = get_briefing_speech_service().pending(owner)
    return {"pending": pending}


@router.get("/broadcast/{broadcast_id}/audio/{index}")
def broadcast_audio(
    broadcast_id: str,
    index: int,
    context: AccountContext = Depends(require_account_principal),
) -> Response:
    owner = _require_self(context)
    data: Optional[bytes] = get_briefing_speech_service().chunk_audio(
        owner, broadcast_id, index
    )
    if data is None:
        return Response(status_code=404, content="这一段还没有合成好。")
    return Response(content=data, media_type="audio/wav")


@router.post("/broadcast/{broadcast_id}/played")
def broadcast_played(
    broadcast_id: str,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    owner = _require_self(context)
    return get_briefing_speech_service().mark_played(owner, broadcast_id)
