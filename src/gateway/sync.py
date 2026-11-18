"""U5 多端同步网关面。

- 管理面（``router``，挂人类闸门）：状态、签发配对令牌、登记对端、开关。
- 对端面（``peer_router``，不挂人类闸门）：只认配对令牌——每台设备先在
  管理面签发令牌，用户把令牌抄到另一台设备登记为对端，之后两台凭令牌
  交换 bundle。令牌在本机只存哈希；同步总开关关着时对端面一律拒绝。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from ..sync import service as sync_service
from ..sync import state as sync_state
from .account_context import AccountContext, require_account_principal

router = APIRouter(prefix="/v1/sync", tags=["sync"])
peer_router = APIRouter(prefix="/v1/sync/peer", tags=["sync-peer"])


class PeerIn(BaseModel):
    name: str
    base_url: str
    token: str


class EnableIn(BaseModel):
    enabled: bool


@router.get("/status")
def status(context: AccountContext = Depends(require_account_principal)) -> Dict[str, Any]:
    return sync_service.sync_status(context.principal)


@router.post("/pairing-token")
def pairing_token(context: AccountContext = Depends(require_account_principal)) -> Dict[str, Any]:
    """签发配对令牌（明文只显示这一次，请抄到另一台设备上登记）。"""
    token = sync_state.create_pairing_token(context.principal)
    return {"token": token, "note": "只显示这一次：把这串令牌填到另一台设备的「登记对端」里。"}


@router.post("/peers")
def add_peer(body: PeerIn, context: AccountContext = Depends(require_account_principal)) -> Dict[str, Any]:
    sync_state.add_peer(body.name, body.base_url, body.token)
    return {"ok": True, "status": sync_service.sync_status(context.principal)}


@router.post("/enable")
def enable(body: EnableIn, context: AccountContext = Depends(require_account_principal)) -> Dict[str, Any]:
    sync_state.set_enabled(body.enabled)
    return {"ok": True, "status": sync_service.sync_status(context.principal)}


@router.post("/run")
def run(body: Dict[str, Any], context: AccountContext = Depends(require_account_principal)) -> Dict[str, Any]:
    peer_name = str(body.get("peer") or "")
    try:
        return sync_service.sync_with_peer(peer_name, context.principal)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --------------------------------------------------------------------- #
# 对端面：配对令牌鉴权
# --------------------------------------------------------------------- #
def _require_peer(authorization: Optional[str]) -> None:
    state = sync_state.load_state()
    if not state.get("enabled"):
        raise HTTPException(status_code=403, detail="本机未开启多端同步")
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not sync_state.verify_pairing_token(token):
        raise HTTPException(status_code=401, detail="配对令牌不对")


@peer_router.get("/bundle")
def peer_bundle(since: str = "", authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _require_peer(authorization)
    state = sync_state.load_state()
    owner = str(state.get("owner") or "")
    if not owner:
        raise HTTPException(status_code=400, detail="本机还没登记同步归属账号（请先签发一次配对令牌）")
    bundle = sync_service.build_local_bundle(owner, since)
    return {"bundle": bundle}


@peer_router.post("/bundle")
def peer_apply(body: Dict[str, Any], authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _require_peer(authorization)
    state = sync_state.load_state()
    owner = str(state.get("owner") or "")
    if not owner:
        raise HTTPException(status_code=400, detail="本机还没登记同步归属账号（请先签发一次配对令牌）")
    bundle = body.get("bundle") or {}
    try:
        report = sync_service.apply_remote_bundle(bundle, owner)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"report": report}
