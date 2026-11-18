"""同步状态：节点身份、配对端、增量水位、墓碑与冲突留痕。

状态文件 ``<data>/sync/state.json``（0600）。配对令牌在本机只存 SHA-256
哈希；发起方保存的对端令牌属于凭据，同样只落在本机状态文件里，不进日志、
不进 git。同步总开关默认关（``enabled: false``）——用户显式开启才跑。
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

_LOCK = threading.RLock()


def sync_dir() -> Path:
    from ..common.data_paths import data_root

    root = data_root()
    if root is not None:
        return Path(root) / "sync"
    return Path.home() / ".liuhao" / "sync"


def _state_path() -> Path:
    return sync_dir() / "state.json"


def _default_state() -> Dict[str, Any]:
    return {
        "node_id": f"node-{uuid.uuid4().hex[:12]}",
        "enabled": False,
        # 本机同步归属账号（签发配对令牌时记下）：对端面凭令牌进来时用它
        # 决定读写哪个账号的数据——配对是同一用户两台设备之间的事。
        "owner": "",
        # 我方签发的配对令牌（只存哈希）：对端拿明文令牌来交换时校验。
        "pairing_token_hashes": [],
        # 我方保存的对端：{name, base_url, token(明文凭据，仅本机), last_sync_at, watermark}
        "peers": [],
        # 已删除实体的墓碑：{kind: {entity_id: deleted_at_iso}}
        "tombstones": {},
        # 设置实体的本地版本戳（与文件 mtime 取大者为准）
        "settings_stamp": "",
    }


def load_state() -> Dict[str, Any]:
    with _LOCK:
        path = _state_path()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    state = _default_state()
                    state.update(data)
                    return state
            except (OSError, ValueError):
                pass
        return _default_state()


def save_state(state: Dict[str, Any]) -> None:
    with _LOCK:
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(path)


def hash_token(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def create_pairing_token(owner: str = "") -> str:
    """签发一个配对令牌：明文只返回这一次，本机只存哈希。"""
    token = secrets.token_urlsafe(32)
    state = load_state()
    hashes = list(state.get("pairing_token_hashes") or [])
    hashes.append(hash_token(token))
    state["pairing_token_hashes"] = hashes[-10:]
    if owner:
        state["owner"] = owner
    save_state(state)
    return token


def verify_pairing_token(token: str) -> bool:
    if not token:
        return False
    state = load_state()
    return hash_token(token) in set(state.get("pairing_token_hashes") or [])


def set_enabled(enabled: bool) -> Dict[str, Any]:
    state = load_state()
    state["enabled"] = bool(enabled)
    save_state(state)
    return state


def add_peer(name: str, base_url: str, token: str) -> Dict[str, Any]:
    state = load_state()
    peers = [p for p in state.get("peers") or [] if p.get("name") != name]
    peers.append(
        {
            "name": str(name or "peer"),
            "base_url": str(base_url or "").rstrip("/"),
            "token": str(token or ""),
            "last_sync_at": "",
            "watermark": "",
        }
    )
    state["peers"] = peers
    save_state(state)
    return state


def get_peer(name: str) -> Optional[Dict[str, Any]]:
    for peer in load_state().get("peers") or []:
        if peer.get("name") == name:
            return peer
    return None


def update_peer(name: str, **fields: Any) -> None:
    state = load_state()
    for peer in state.get("peers") or []:
        if peer.get("name") == name:
            peer.update(fields)
    save_state(state)


def record_tombstone(kind: str, entity_id: str, deleted_at: str) -> None:
    state = load_state()
    tombs = state.setdefault("tombstones", {})
    bucket = tombs.setdefault(kind, {})
    if deleted_at >= str(bucket.get(entity_id) or ""):
        bucket[entity_id] = deleted_at
    save_state(state)


def tombstones(kind: str) -> Dict[str, str]:
    return dict((load_state().get("tombstones") or {}).get(kind) or {})


def log_conflict(record: Dict[str, Any]) -> None:
    """冲突留痕：追加到 conflicts.jsonl，永不覆盖、永不静默。"""
    record = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **record}
    path = sync_dir() / "conflicts.jsonl"
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_conflicts(limit: int = 50) -> List[Dict[str, Any]]:
    path = sync_dir() / "conflicts.jsonl"
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()[-max(1, limit):]
    out: List[Dict[str, Any]] = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out
