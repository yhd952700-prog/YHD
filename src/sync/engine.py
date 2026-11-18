"""同步引擎：捆绑包（bundle）的构建、校验与合并。

实体模型：``{kind, id, updated_at, deleted, payload, sha256}``。
合并规则（写死，不留模糊地带）：

- 本地没有 → 写入（applied）。
- 远端 ``updated_at`` 更新 → 覆盖（applied）；更旧 → 保留本地（kept）。
- 时间相同但内容哈希不同 → **冲突**：保留本地、写冲突留痕（conflict），
  绝不静默覆盖或丢弃。
- 删除以墓碑（deleted + deleted_at）传播，同样服从上面的时间比较。
- ``conversations`` 类是 append-only：逐条合并、只增不改，同位置内容不同
  只留痕不覆盖（由适配器内部处理）。

bundle 自带整体 SHA-256，接收方先验签（防传输损坏/篡改）再合并。
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Dict, List

BUNDLE_FORMAT = "liuhao.sync.v1"
APPEND_ONLY_KINDS = {"conversations"}


def entity_sha(payload: Any) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def make_entity(kind: str, entity_id: str, updated_at: str, payload: Dict[str, Any],
                *, deleted: bool = False) -> Dict[str, Any]:
    return {
        "kind": kind,
        "id": str(entity_id),
        "updated_at": str(updated_at or ""),
        "deleted": bool(deleted),
        "payload": payload if not deleted else {},
        "sha256": entity_sha(payload if not deleted else {}),
    }


def bundle_sha(bundle: Dict[str, Any]) -> str:
    body = {k: v for k, v in bundle.items() if k != "bundle_sha256"}
    canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_bundle(node_id: str, owner: str, entities: List[Dict[str, Any]]) -> Dict[str, Any]:
    bundle = {
        "format": BUNDLE_FORMAT,
        "node_id": node_id,
        "owner": owner,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "entities": entities,
    }
    bundle["bundle_sha256"] = bundle_sha(bundle)
    return bundle


def verify_bundle(bundle: Dict[str, Any]) -> None:
    if not isinstance(bundle, dict) or bundle.get("format") != BUNDLE_FORMAT:
        raise ValueError("同步包格式不对")
    if bundle.get("bundle_sha256") != bundle_sha(bundle):
        raise ValueError("同步包校验失败（内容已损坏或被篡改），已拒绝合并")


def merge_bundle(bundle: Dict[str, Any], adapter: Any, *, conflict_logger) -> Dict[str, Any]:
    """把远端 bundle 合并进本地（经适配器落盘），返回逐项报告。"""
    verify_bundle(bundle)
    report: Dict[str, Any] = {
        "from_node": bundle.get("node_id") or "",
        "applied": 0,
        "kept": 0,
        "identical": 0,
        "deleted_applied": 0,
        "conflict_count": 0,
        "conflicts": [],
        "by_kind": {},
    }

    def _bump(kind: str, key: str) -> None:
        bucket = report["by_kind"].setdefault(
            kind, {"applied": 0, "kept": 0, "identical": 0, "conflicts": 0, "deleted_applied": 0}
        )
        bucket[key] += 1
        if key == "conflicts":
            report["conflict_count"] += 1
        else:
            report[key] += 1

    for entity in bundle.get("entities") or []:
        kind = str(entity.get("kind") or "")
        entity_id = str(entity.get("id") or "")
        if not kind or not entity_id:
            continue
        if kind in APPEND_ONLY_KINDS:
            outcome, conflicts = adapter.merge_append_only(kind, entity)
            for conflict in conflicts:
                conflict_logger(conflict)
                report["conflicts"].append(conflict)
                _bump(kind, "conflicts")
            _bump(kind, outcome)  # outcome ∈ applied/kept/identical
            continue

        local = adapter.get(kind, entity_id)
        if entity.get("deleted"):
            if local is None:
                _bump(kind, "identical")
            elif str(entity.get("updated_at") or "") >= str(local.get("updated_at") or ""):
                adapter.delete(kind, entity_id)
                _bump(kind, "deleted_applied")
            else:
                _bump(kind, "kept")
            continue
        if local is None:
            adapter.put(kind, entity)
            _bump(kind, "applied")
            continue
        remote_ts = str(entity.get("updated_at") or "")
        local_ts = str(local.get("updated_at") or "")
        if remote_ts > local_ts:
            adapter.put(kind, entity)
            _bump(kind, "applied")
        elif remote_ts < local_ts:
            _bump(kind, "kept")
        elif entity.get("sha256") == local.get("sha256"):
            _bump(kind, "identical")
        else:
            conflict = {
                "kind": kind,
                "id": entity_id,
                "reason": "same_timestamp_different_content",
                "kept": "local",
                "local_sha256": local.get("sha256"),
                "remote_sha256": entity.get("sha256"),
                "from_node": bundle.get("node_id") or "",
            }
            conflict_logger(conflict)
            report["conflicts"].append(conflict)
            _bump(kind, "conflicts")
    return report
