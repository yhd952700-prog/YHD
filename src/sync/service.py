"""同步编排：构建本机 bundle、合并对端 bundle、与对端网关完成一轮双向同步。

协议（两个端点，都要求配对令牌）：
- ``GET  /v1/sync/peer/bundle?since=`` → 对端返回它的增量 bundle；
- ``POST /v1/sync/peer/bundle``       → 对端合并我方 bundle 并返回报告。

一轮同步 = 先拉后推：拉到的先合并进本地，再把本地全量（按对端水位增量）
推给对端合并。两边最终收敛；冲突各自留痕。同步总开关默认关，关着时
编排层直接拒绝运行（端点层同样拒绝）。
"""
from __future__ import annotations

import json
import urllib.request
from typing import Any, Dict, Optional

from . import state as sync_state
from .adapters import KINDS, SyncAdapter
from .engine import build_bundle, merge_bundle

_TIMEOUT_SECONDS = 30


def sync_status(owner: str) -> Dict[str, Any]:
    state = sync_state.load_state()
    return {
        "enabled": bool(state.get("enabled")),
        "node_id": state.get("node_id"),
        "owner": owner,
        "peers": [
            {
                "name": p.get("name"),
                "base_url": p.get("base_url"),
                "last_sync_at": p.get("last_sync_at") or "",
                "has_token": bool(p.get("token")),
            }
            for p in state.get("peers") or []
        ],
        "pairing_tokens_issued": len(state.get("pairing_token_hashes") or []),
        "recent_conflicts": sync_state.read_conflicts(10),
    }


def build_local_bundle(owner: str, since: str = "", *, adapter: Optional[SyncAdapter] = None) -> Dict[str, Any]:
    adapter = adapter or SyncAdapter(owner)
    state = sync_state.load_state()
    entities = []
    errors: Dict[str, str] = {}
    for kind in KINDS:
        try:
            entities.extend(adapter.collect(kind, since))
        except Exception as exc:  # noqa: BLE001 - 单类失败降级上报，不拖垮整包
            errors[kind] = f"{type(exc).__name__}"
    bundle = build_bundle(state.get("node_id") or "", owner, entities)
    if errors:
        bundle["collect_errors"] = errors
    return bundle


def apply_remote_bundle(bundle: Dict[str, Any], owner: str, *, adapter: Optional[SyncAdapter] = None) -> Dict[str, Any]:
    adapter = adapter or SyncAdapter(owner)
    return merge_bundle(bundle, adapter, conflict_logger=sync_state.log_conflict)


def _http_json(method: str, url: str, token: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"对端返回 HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError("连不上对端设备（请确认对端已开机、地址正确）") from exc


def sync_with_peer(peer_name: str, owner: str, *, adapter: Optional[SyncAdapter] = None) -> Dict[str, Any]:
    """与一个已配对设备完成一轮双向同步（拉 → 合并 → 推 → 对端合并）。"""
    state = sync_state.load_state()
    if not state.get("enabled"):
        raise RuntimeError("多端同步还没开启（默认关），请先开启再同步")
    peer = sync_state.get_peer(peer_name)
    if peer is None:
        raise RuntimeError(f"没有名为「{peer_name}」的已配对设备")
    base = str(peer.get("base_url") or "").rstrip("/")
    token = str(peer.get("token") or "")
    if not base or not token:
        raise RuntimeError("这个设备的地址或配对令牌不完整")

    adapter = adapter or SyncAdapter(owner)

    # 增量口径：按内容哈希，不按墙钟时间。水位（watermark）若按打包时刻
    # 与实体 updated_at 比较，两台设备时钟不一致时旧时间戳的修改会被漏掉
    # （双网关演练实测踩中）。改为：本机记下上次成功推给对端的
    # {kind:id: sha256}，只推变化了的实体；拉取方向由合并引擎的
    # identical 判定天然幂等。断线时哈希表不更新，下轮自动续传。
    pulled = _http_json("GET", f"{base}/v1/sync/peer/bundle", token)
    remote_bundle = pulled.get("bundle") or {}
    pull_report = apply_remote_bundle(remote_bundle, owner, adapter=adapter)

    local_bundle = build_local_bundle(owner, "", adapter=adapter)
    sent_shas = dict(peer.get("sent_shas") or {})
    changed = []
    current_shas: Dict[str, str] = {}
    for entity in local_bundle.get("entities") or []:
        key = f"{entity.get('kind')}:{entity.get('id')}"
        sha = "deleted" if entity.get("deleted") else str(entity.get("sha256") or "")
        current_shas[key] = sha
        if sent_shas.get(key) != sha:
            changed.append(entity)
    push_bundle = dict(local_bundle)
    push_bundle["entities"] = changed
    from .engine import bundle_sha

    push_bundle["bundle_sha256"] = bundle_sha(push_bundle)
    pushed = _http_json("POST", f"{base}/v1/sync/peer/bundle", token, {"bundle": push_bundle})
    push_report = pushed.get("report") or {}

    from datetime import datetime

    sync_state.update_peer(
        peer_name,
        last_sync_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        sent_shas=current_shas,
    )
    return {
        "peer": peer_name,
        "pulled_from_node": remote_bundle.get("node_id") or "",
        "pull_report": pull_report,
        "push_report": push_report,
        "collect_errors": local_bundle.get("collect_errors") or {},
    }
