"""组织架构模板（企业工作台 P3A 收尾）—— 模板是数据，不是代码。

边界
----
* 一套模板 = 一棵节点树 + 岗位节点 + 汇报边，用 JSON 描述；可导入/导出。
* 内置模板随包分发、**只读**：可复制为自定义模板再改；只允许隐藏，不允许真删。
  理由：内置 JSON 在升级时会重新分发，真删会在下次升级被回填；隐藏记在
  ``org_template_prefs``，持久生效且可恢复。自定义模板可改名、改内容、硬删。
* **不预设、不自动套用**：本模块所有路由都只被人类令牌调用（``main.py`` 挂
  ``_require_human``），且不注册任何 Agent 工具；前端打开抽屉时不预选模板、不自动
  预览、不自动写入。
* 套用是一次普通写操作：在同一个事务里追加一版 ``template_apply`` 快照，可回滚。

三种套用模式
------------
* ``subtree``：把模板根建到指定父节点下（父节点为空 = 顶层）。
* ``merge``：模板根映射到锚点节点，其子节点按名称递归匹配；同名同类型合并，
  同名不同类型冲突，无同名新建。
* ``replace``：删除该账号全部节点与任职后，按模板整树重建。

dry-run 与并发
--------------
预览只读，返回 ``preview_token``（账号 + 模板指纹 + base_seq + 模式 + 目标 +
选择集 + 冲突处理的 sha256）。套用必须带回该令牌；服务端重新计算，不一致返回
``409 ORG_TEMPLATE_STALE_PREVIEW``，不写入任何数据。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import time
import uuid
from typing import Any, Dict, List, Optional, Sequence, Set

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..common.input_guard import normalize_required_name, reject_unsafe_text
from . import org
from .account_context import deny

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/org/templates", tags=["org-templates"])

TEMPLATE_KIND = "liuhao.org.template"
TEMPLATE_SCHEMA_VERSION = 1
BUILTIN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "org_templates_builtin")

MAX_TEMPLATE_NODES = 200
MAX_TEMPLATE_DEPTH = 16
MAX_TEMPLATE_JSON = 512 * 1024
MAX_SELECTION = 200

MODES = ("subtree", "merge", "replace")
_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")

_builtin_cache: Optional[List[Dict[str, Any]]] = None


# ── 基础工具 ──────────────────────────────────────────────────


def _invalid(message: str) -> HTTPException:
    return deny(422, "ORG_TEMPLATE_INVALID", message)


def _not_found() -> HTTPException:
    return deny(404, "ORG_TEMPLATE_NOT_FOUND", "资源不存在或无权访问")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _canonical_hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _clean_name(value: Any, label: str) -> str:
    try:
        name = normalize_required_name(str(value or ""), label, max_chars=org.MAX_NAME)
        reject_unsafe_text(name, label)
        return name
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - 统一转成模板错误码
        raise _invalid(f"{label}不合法：{exc}") from exc


def _clean_text(value: Any, label: str, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        raise _invalid(f"{label}超过 {limit} 个字符。")
    try:
        reject_unsafe_text(text, label)
    except Exception as exc:  # noqa: BLE001
        raise _invalid(f"{label}不合法：{exc}") from exc
    return text


# ── 模板校验与规范化 ──────────────────────────────────────────


def _canonical_node(raw: Any, *, depth: int, keys: Set[str], sibling_names: Set[str]) -> Dict[str, Any]:
    if depth > MAX_TEMPLATE_DEPTH:
        raise _invalid(f"模板层级超过 {MAX_TEMPLATE_DEPTH} 层。")
    if not isinstance(raw, dict):
        raise _invalid("模板节点必须是对象。")
    key = str(raw.get("key") or "").strip()
    if not _KEY_RE.match(key):
        raise _invalid("模板节点 key 只能包含字母、数字、点、下划线、冒号与短横线，且不超过 64 字符。")
    if key in keys:
        raise _invalid(f"模板节点 key「{key}」重复。")
    keys.add(key)
    name = _clean_name(raw.get("name"), "节点名称")
    folded = name.casefold()
    if folded in sibling_names:
        raise _invalid(f"同一上级下存在重名节点「{name}」。")
    sibling_names.add(folded)
    kind = str(raw.get("kind") or "").strip()
    if kind not in org.ORG_KINDS:
        raise _invalid(f"节点类型「{kind}」不支持。")
    description = _clean_text(raw.get("description"), "节点说明", org.MAX_DESCRIPTION)
    children_raw = raw.get("children") or []
    if not isinstance(children_raw, list):
        raise _invalid("children 必须是数组。")
    child_names: Set[str] = set()
    children = [
        _canonical_node(child, depth=depth + 1, keys=keys, sibling_names=child_names)
        for child in children_raw
    ]
    return {
        "key": key,
        "name": name,
        "kind": kind,
        "description": description,
        "children": children,
    }


def _validate_payload(payload: Any) -> Dict[str, Any]:
    """校验并返回白名单化的模板对象；错误统一 422 ``ORG_TEMPLATE_INVALID``。"""
    if not isinstance(payload, dict):
        raise _invalid("模板必须是 JSON 对象。")
    if payload.get("kind") != TEMPLATE_KIND:
        raise _invalid(f"模板 kind 必须是 {TEMPLATE_KIND}。")
    try:
        schema_version = int(payload.get("schema_version"))
    except (TypeError, ValueError):
        raise _invalid("模板缺少 schema_version。")
    if schema_version != TEMPLATE_SCHEMA_VERSION:
        raise _invalid(f"只支持 schema_version={TEMPLATE_SCHEMA_VERSION} 的模板。")
    template = payload.get("template")
    if not isinstance(template, dict):
        raise _invalid("模板缺少 template 对象。")
    name = _clean_name(template.get("name"), "模板名称")
    root = template.get("root")
    if not isinstance(root, dict):
        raise _invalid("模板缺少 root 节点。")
    keys: Set[str] = set()
    canonical_root = _canonical_node(root, depth=1, keys=keys, sibling_names=set())
    if len(keys) > MAX_TEMPLATE_NODES:
        raise _invalid(f"模板节点数超过 {MAX_TEMPLATE_NODES}。")
    return {
        "kind": TEMPLATE_KIND,
        "schema_version": TEMPLATE_SCHEMA_VERSION,
        "template": {
            "key": str(template.get("key") or "").strip(),
            "name": name,
            "industry": _clean_text(template.get("industry"), "行业", 80),
            "description": _clean_text(template.get("description"), "模板说明", org.MAX_DESCRIPTION),
            "version": int(template.get("version") or 1),
            "sort": int(template.get("sort") or 100),
            "root": canonical_root,
        },
    }


def _flatten(root: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

    def walk(node: Dict[str, Any], parent_key: Optional[str], depth: int) -> None:
        rows.append(
            {
                "key": node["key"],
                "name": node["name"],
                "kind": node["kind"],
                "description": node.get("description") or "",
                "parent_key": parent_key,
                "depth": depth,
                "children": node.get("children") or [],
            }
        )
        for child in node.get("children") or []:
            walk(child, node["key"], depth + 1)

    walk(root, None, 0)
    return rows


def _stats(root: Dict[str, Any]) -> Dict[str, int]:
    rows = _flatten(root)
    return {
        "node_count": len(rows),
        "position_count": sum(1 for row in rows if row["kind"] == "position"),
        "max_depth": max((row["depth"] for row in rows), default=0),
    }


# ── 内置模板 ──────────────────────────────────────────────────


def _load_builtins() -> List[Dict[str, Any]]:
    global _builtin_cache
    if _builtin_cache is not None:
        return _builtin_cache
    result: List[Dict[str, Any]] = []
    try:
        names = sorted(os.listdir(BUILTIN_DIR))
    except OSError as exc:
        logger.warning("org templates: builtin dir unavailable: %s", exc)
        names = []
    for filename in names:
        if not filename.endswith(".json"):
            continue
        path = os.path.join(BUILTIN_DIR, filename)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = _validate_payload(json.load(handle))
            template = payload["template"]
            key = template.get("key") or f"builtin.{filename[:-5]}"
            payload["template"]["key"] = key
            result.append(
                {
                    "template_id": key,
                    "source": "builtin",
                    "name": template["name"],
                    "industry": template["industry"],
                    "description": template["description"],
                    "version": template["version"],
                    "revision": None,
                    "sort": template["sort"],
                    "payload": payload,
                    "builtin": True,
                }
            )
        except Exception as exc:  # noqa: BLE001 - 单套内置损坏不拖垮整页
            logger.warning("org templates: skip invalid builtin %s: %s", filename, exc)
    result.sort(key=lambda item: (int(item["sort"]), item["name"]))
    _builtin_cache = result
    return result


# ── 存储访问 ──────────────────────────────────────────────────


def _prefs(connection: sqlite3.Connection, account_id: str) -> Dict[str, Dict[str, Any]]:
    rows = connection.execute(
        "SELECT template_key, hidden, sort_order FROM org_template_prefs WHERE account_id = ?",
        (account_id,),
    ).fetchall()
    return {
        row["template_key"]: {"hidden": bool(row["hidden"]), "sort_order": row["sort_order"]}
        for row in rows
    }


def _row_template(row: sqlite3.Row) -> Dict[str, Any]:
    payload = _validate_payload(json.loads(row["payload_json"]))
    return {
        "template_id": row["template_id"],
        "source": "custom",
        "name": row["name"],
        "industry": row["industry"],
        "description": row["description"],
        "version": None,
        "revision": int(row["revision"]),
        "sort": 1000,
        "payload": payload,
        "builtin": False,
        "row": row,
    }


def _resolve_template(
    connection: sqlite3.Connection, account_id: str, template_id: str
) -> Dict[str, Any]:
    for entry in _load_builtins():
        if entry["template_id"] == template_id:
            return entry
    row = connection.execute(
        "SELECT * FROM org_templates WHERE account_id = ? AND template_id = ?",
        (account_id, template_id),
    ).fetchone()
    if row is None:
        raise _not_found()
    return _row_template(row)


def _entry_summary(entry: Dict[str, Any], hidden: bool) -> Dict[str, Any]:
    template = entry["payload"]["template"]
    stats = _stats(template["root"])
    return {
        "template_id": entry["template_id"],
        "name": entry["name"],
        "source": entry["source"],
        "industry": entry["industry"],
        "description": entry["description"],
        "version": entry["version"],
        "revision": entry["revision"],
        "sort": entry["sort"],
        "hidden": hidden,
        "editable": not entry["builtin"],
        "deletable": not entry["builtin"],
        "deletable_reason": (
            "内置模板随包分发，只允许隐藏；升级不会把隐藏项重新显示。"
            if entry["builtin"]
            else ""
        ),
        **stats,
    }


def _fingerprint(entry: Dict[str, Any]) -> str:
    return _canonical_hash(
        {
            "template_id": entry["template_id"],
            "source": entry["source"],
            "version": entry["version"],
            "revision": entry["revision"],
            "payload": entry["payload"],
        }
    )


def _latest_seq(connection: sqlite3.Connection, account_id: str) -> int:
    row = connection.execute(
        "SELECT COALESCE(MAX(seq), 0) AS seq FROM org_versions WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    return int(row["seq"])


def _find_child(nodes: Sequence[Dict[str, Any]], parent_id: str, name: str) -> Optional[Dict[str, Any]]:
    target = name.casefold()
    for node in nodes:
        if (node["parent_id"] or "") != parent_id:
            continue
        if node["name"].casefold() == target:
            return node
    return None


def _children(nodes: Sequence[Dict[str, Any]], parent_id: str) -> List[Dict[str, Any]]:
    return [node for node in nodes if (node["parent_id"] or "") == parent_id]


def _path_label(nodes: Sequence[Dict[str, Any]], node_id: str) -> str:
    by_id = {node["node_id"]: node for node in nodes}
    parts: List[str] = []
    cursor = by_id.get(node_id)
    while cursor is not None:
        parts.append(cursor["name"])
        cursor = by_id.get(cursor["parent_id"] or "")
    return " / ".join(reversed(parts))


# ── 请求模型 ──────────────────────────────────────────────────


class TemplateCreateRequest(BaseModel):
    name: str
    description: str = ""
    industry: str = ""


class TemplateCopyRequest(BaseModel):
    name: Optional[str] = None


class TemplatePatchRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    industry: Optional[str] = None
    root: Optional[Dict[str, Any]] = None
    revision: Optional[int] = None


class ResolutionRequest(BaseModel):
    key: str
    action: str
    new_name: str = ""


class TemplateApplyRequest(BaseModel):
    mode: str
    target_parent_id: Optional[str] = None
    target_node_id: Optional[str] = None
    selected_keys: Optional[List[str]] = None
    resolutions: List[ResolutionRequest] = Field(default_factory=list)
    preview_token: Optional[str] = None


class TemplatePreviewRequest(BaseModel):
    mode: str
    target_parent_id: Optional[str] = None
    target_node_id: Optional[str] = None
    selected_keys: Optional[List[str]] = None
    resolutions: List[ResolutionRequest] = Field(default_factory=list)


# ── 模板库 CRUD ───────────────────────────────────────────────


@router.get("")
def list_templates(
    include_hidden: bool = Query(False),
    viewer=Depends(org._org_viewer),
) -> Dict[str, Any]:
    account_id = viewer.principal
    connection = org._connect()
    try:
        prefs = _prefs(connection, account_id)
        builtins = [
            _entry_summary(entry, bool(prefs.get(entry["template_id"], {}).get("hidden")))
            for entry in _load_builtins()
        ]
        custom_rows = connection.execute(
            "SELECT * FROM org_templates WHERE account_id = ? ORDER BY updated_at DESC",
            (account_id,),
        ).fetchall()
        customs = [
            _entry_summary(_row_template(row), bool(prefs.get(row["template_id"], {}).get("hidden")))
            for row in custom_rows
        ]
        visible = [
            item
            for item in builtins + customs
            if include_hidden or not item["hidden"]
        ]
        visible.sort(
            key=lambda item: (
                0 if item["source"] == "builtin" else 1,
                int(item.get("sort") or 0),
                item["name"],
            )
        )
        hidden = [item for item in builtins + customs if item["hidden"]]
        return {
            "templates": visible,
            "total": len(visible),
            "hidden_count": len(hidden),
            "note": "模板只作参考；选择、预览、套用都必须由人在组织架构页确认。",
        }
    finally:
        connection.close()


@router.get("/{template_id}")
def get_template(template_id: str, viewer=Depends(org._org_viewer)) -> Dict[str, Any]:
    account_id = viewer.principal
    connection = org._connect()
    try:
        entry = _resolve_template(connection, account_id, template_id)
        prefs = _prefs(connection, account_id)
        summary = _entry_summary(entry, bool(prefs.get(template_id, {}).get("hidden")))
        return {
            **summary,
            "payload": entry["payload"],
            "fingerprint": _fingerprint(entry),
        }
    finally:
        connection.close()


@router.post("")
def create_template(
    req: TemplateCreateRequest, viewer=Depends(org._org_viewer)
) -> Dict[str, Any]:
    account_id = viewer.principal
    name = _clean_name(req.name, "模板名称")
    root = {
        "key": "root",
        "name": name,
        "kind": "company",
        "description": "",
        "children": [],
    }
    payload = _validate_payload(
        {
            "kind": TEMPLATE_KIND,
            "schema_version": TEMPLATE_SCHEMA_VERSION,
            "template": {
                "key": "",
                "name": name,
                "industry": req.industry,
                "description": req.description,
                "version": 1,
                "sort": 1000,
                "root": root,
            },
        }
    )
    template_id = "tpl_" + uuid.uuid4().hex[:16]
    payload["template"]["key"] = template_id
    now = time.time()
    with org._tx() as connection:
        try:
            connection.execute(
                "INSERT INTO org_templates "
                "(template_id, account_id, name, description, industry, schema_version, "
                " revision, payload_json, created_at, updated_at, created_by) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    template_id,
                    account_id,
                    name,
                    payload["template"]["description"],
                    payload["template"]["industry"],
                    1,
                    1,
                    json.dumps(payload, ensure_ascii=False),
                    now,
                    now,
                    viewer.principal,
                ),
            )
        except sqlite3.IntegrityError:
            raise deny(
                409,
                "ORG_TEMPLATE_DUP_NAME",
                "已有同名模板；模板名称在同一账号下必须唯一（大小写不敏感）。",
            )
    return {
        "template_id": template_id,
        "name": name,
        "source": "custom",
        "revision": 1,
        "note": "已创建空白模板；可以改名，或通过导入覆盖节点树。",
    }


@router.post("/{template_id}/copy")
def copy_template(
    template_id: str,
    req: TemplateCopyRequest,
    viewer=Depends(org._org_viewer),
) -> Dict[str, Any]:
    account_id = viewer.principal
    connection = org._connect()
    try:
        source = _resolve_template(connection, account_id, template_id)
    finally:
        connection.close()
    base_name = _clean_name(req.name or f"{source['name']} 副本", "模板名称")
    payload = json.loads(_canonical(source["payload"]))
    new_id = "tpl_" + uuid.uuid4().hex[:16]
    payload["template"]["key"] = new_id
    payload["template"]["name"] = base_name
    payload["template"]["version"] = 1
    payload["template"]["sort"] = 1000
    payload = _validate_payload(payload)
    now = time.time()
    with org._tx() as connection:
        name = base_name
        for attempt in range(2, 12):
            exists = connection.execute(
                "SELECT 1 FROM org_templates WHERE account_id = ? AND name = ? COLLATE NOCASE",
                (account_id, name),
            ).fetchone()
            if exists is None:
                break
            name = f"{base_name} {attempt}"
        payload["template"]["name"] = name
        try:
            connection.execute(
                "INSERT INTO org_templates "
                "(template_id, account_id, name, description, industry, schema_version, "
                " revision, payload_json, created_at, updated_at, created_by) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_id,
                    account_id,
                    name,
                    payload["template"]["description"],
                    payload["template"]["industry"],
                    1,
                    1,
                    json.dumps(payload, ensure_ascii=False),
                    now,
                    now,
                    viewer.principal,
                ),
            )
        except sqlite3.IntegrityError:
            raise deny(409, "ORG_TEMPLATE_DUP_NAME", "同名模板已存在，请换一个名称。")
    return {
        "template_id": new_id,
        "name": name,
        "source": "custom",
        "copied_from": template_id,
        "revision": 1,
        "note": "已复制为自定义模板；内置模板本身不会被修改。",
    }


@router.patch("/{template_id}")
def patch_template(
    template_id: str,
    req: TemplatePatchRequest,
    viewer=Depends(org._org_viewer),
) -> Dict[str, Any]:
    account_id = viewer.principal
    with org._tx() as connection:
        entry = _resolve_template(connection, account_id, template_id)
        if entry["builtin"]:
            raise deny(
                409,
                "ORG_TEMPLATE_BUILTIN_READONLY",
                "内置模板只读；请先复制为自定义模板再修改。",
            )
        row = entry["row"]
        current_revision = int(row["revision"])
        if req.revision is not None and int(req.revision) != current_revision:
            raise deny(
                409,
                "ORG_TEMPLATE_STALE_REVISION",
                "模板已被修改，请刷新后再保存。",
                current_revision=current_revision,
            )
        payload = json.loads(row["payload_json"])
        if req.name is not None:
            payload["template"]["name"] = _clean_name(req.name, "模板名称")
        if req.description is not None:
            payload["template"]["description"] = _clean_text(
                req.description, "模板说明", org.MAX_DESCRIPTION
            )
        if req.industry is not None:
            payload["template"]["industry"] = _clean_text(req.industry, "行业", 80)
        if req.root is not None:
            payload["template"]["root"] = req.root
        payload = _validate_payload(payload)
        try:
            connection.execute(
                "UPDATE org_templates SET name = ?, description = ?, industry = ?, "
                "payload_json = ?, revision = ?, updated_at = ? "
                "WHERE account_id = ? AND template_id = ?",
                (
                    payload["template"]["name"],
                    payload["template"]["description"],
                    payload["template"]["industry"],
                    json.dumps(payload, ensure_ascii=False),
                    current_revision + 1,
                    time.time(),
                    account_id,
                    template_id,
                ),
            )
        except sqlite3.IntegrityError:
            raise deny(409, "ORG_TEMPLATE_DUP_NAME", "同名模板已存在，请换一个名称。")
    return {
        "template_id": template_id,
        "revision": current_revision + 1,
        "note": "已保存；下一次套用会使用这一版模板内容。",
    }


@router.delete("/{template_id}")
def delete_template(template_id: str, viewer=Depends(org._org_viewer)) -> Dict[str, Any]:
    account_id = viewer.principal
    with org._tx() as connection:
        entry = _resolve_template(connection, account_id, template_id)
        if entry["builtin"]:
            raise deny(
                409,
                "ORG_TEMPLATE_BUILTIN_HIDE_ONLY",
                "内置模板不允许删除，只允许隐藏；隐藏后可随时恢复。",
            )
        connection.execute(
            "DELETE FROM org_templates WHERE account_id = ? AND template_id = ?",
            (account_id, template_id),
        )
        connection.execute(
            "DELETE FROM org_template_prefs WHERE account_id = ? AND template_key = ?",
            (account_id, template_id),
        )
    return {"template_id": template_id, "deleted": True}


@router.post("/{template_id}/hide")
def hide_template(template_id: str, viewer=Depends(org._org_viewer)) -> Dict[str, Any]:
    return _set_hidden(template_id, True, viewer)


@router.post("/{template_id}/unhide")
def unhide_template(template_id: str, viewer=Depends(org._org_viewer)) -> Dict[str, Any]:
    return _set_hidden(template_id, False, viewer)


def _set_hidden(template_id: str, hidden: bool, viewer: Any) -> Dict[str, Any]:
    account_id = viewer.principal
    with org._tx() as connection:
        _resolve_template(connection, account_id, template_id)
        connection.execute(
            "INSERT INTO org_template_prefs(account_id, template_key, hidden, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(account_id, template_key) DO UPDATE SET hidden = excluded.hidden, "
            "updated_at = excluded.updated_at",
            (account_id, template_id, 1 if hidden else 0, time.time()),
        )
    return {
        "template_id": template_id,
        "hidden": hidden,
        "note": "已隐藏；可在同一入口的「已隐藏模板」里恢复。"
        if hidden
        else "已恢复显示。",
    }


@router.get("/{template_id}/export")
def export_template(template_id: str, viewer=Depends(org._org_viewer)) -> Dict[str, Any]:
    account_id = viewer.principal
    connection = org._connect()
    try:
        entry = _resolve_template(connection, account_id, template_id)
        return entry["payload"]
    finally:
        connection.close()


@router.post("/import")
def import_template(
    payload: Dict[str, Any] = Body(...),
    name: Optional[str] = Query(None),
    viewer=Depends(org._org_viewer),
) -> Dict[str, Any]:
    account_id = viewer.principal
    raw = payload
    if "template" not in raw and isinstance(raw.get("payload"), dict):
        raw = raw["payload"]
    canonical = _validate_payload(raw)
    if name:
        canonical["template"]["name"] = _clean_name(name, "模板名称")
    template_id = "tpl_" + uuid.uuid4().hex[:16]
    canonical["template"]["key"] = template_id
    canonical["template"]["version"] = 1
    canonical["template"]["sort"] = 1000
    now = time.time()
    with org._tx() as connection:
        try:
            connection.execute(
                "INSERT INTO org_templates "
                "(template_id, account_id, name, description, industry, schema_version, "
                " revision, payload_json, created_at, updated_at, created_by) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    template_id,
                    account_id,
                    canonical["template"]["name"],
                    canonical["template"]["description"],
                    canonical["template"]["industry"],
                    1,
                    1,
                    json.dumps(canonical, ensure_ascii=False),
                    now,
                    now,
                    viewer.principal,
                ),
            )
        except sqlite3.IntegrityError:
            raise deny(409, "ORG_TEMPLATE_DUP_NAME", "同名模板已存在，请换一个名称。")
    return {
        "template_id": template_id,
        "name": canonical["template"]["name"],
        "source": "custom",
        "revision": 1,
        "note": "已导入为自定义模板；在预览前不会写入组织树。",
    }


# ── 预览 / 套用 ───────────────────────────────────────────────


def _plan(
    connection: sqlite3.Connection,
    viewer: Any,
    account_id: str,
    entry: Dict[str, Any],
    req: TemplatePreviewRequest,
) -> Dict[str, Any]:
    mode = str(req.mode or "").strip()
    if mode not in MODES:
        raise deny(422, "ORG_TEMPLATE_INVALID", "mode 只能是 subtree / merge / replace。")
    template = entry["payload"]["template"]
    rows = _flatten(template["root"])
    row_by_key = {row["key"]: row for row in rows}
    root_key = rows[0]["key"]

    selected_raw = req.selected_keys
    if selected_raw is None or not isinstance(selected_raw, list) or not selected_raw:
        raise deny(
            422,
            "ORG_TEMPLATE_SELECTION_INVALID",
            "必须显式传入非空的 selected_keys；不传不会被当成“全选”。",
        )
    if len(selected_raw) > MAX_SELECTION:
        raise deny(422, "ORG_TEMPLATE_LIMIT", f"选择集超过 {MAX_SELECTION} 个节点。")
    selected: Set[str] = set()
    for key in selected_raw:
        if key not in row_by_key:
            raise deny(
                422,
                "ORG_TEMPLATE_SELECTION_INVALID",
                f"选择集里的节点「{key}」不在模板中。",
            )
        selected.add(key)
    for row in rows:
        if row["key"] in selected and row["parent_key"] and row["parent_key"] not in selected:
            raise deny(
                422,
                "ORG_TEMPLATE_SELECTION_INVALID",
                f"选中「{row['name']}」时必须同时选中它的上级。",
            )
    if mode == "merge" and root_key not in selected:
        raise deny(
            422,
            "ORG_TEMPLATE_SELECTION_INVALID",
            "合并模式下模板根固定映射到锚点，不能被取消。",
        )

    nodes = org._load_nodes(connection, account_id)
    assignments = org._load_assignments(connection, account_id)
    by_id = {node["node_id"]: node for node in nodes}

    target: Optional[Dict[str, Any]] = None
    target_parent_id = str(req.target_parent_id or "").strip()
    target_node_id = str(req.target_node_id or "").strip()
    if mode == "subtree":
        if target_parent_id and target_parent_id not in by_id:
            raise deny(404, "ORG_TEMPLATE_TARGET_NOT_FOUND", "资源不存在或无权访问")
        if target_parent_id:
            target = {"node_id": target_parent_id, "name": by_id[target_parent_id]["name"], "path": _path_label(nodes, target_parent_id)}
    elif mode == "merge":
        if not target_node_id:
            raise deny(422, "ORG_TEMPLATE_TARGET_REQUIRED", "合并模式必须指定锚点节点。")
        if target_node_id not in by_id:
            raise deny(404, "ORG_TEMPLATE_TARGET_NOT_FOUND", "资源不存在或无权访问")
        target = {"node_id": target_node_id, "name": by_id[target_node_id]["name"], "path": _path_label(nodes, target_node_id)}
    else:
        target = None

    resolutions = {item.key: item for item in req.resolutions if item.key in row_by_key}
    mapping: Dict[str, str] = {}
    blocked: Set[str] = set()
    planned_names: Dict[str, Set[str]] = {}
    statuses: Dict[str, str] = {}
    reason: Dict[str, str] = {}
    existing_for: Dict[str, str] = {}
    final_names: Dict[str, str] = {}
    conflicts: List[Dict[str, Any]] = []
    decisions: List[Dict[str, Any]] = []

    def sibling_names(parent_id: str) -> Set[str]:
        if parent_id not in planned_names:
            planned_names[parent_id] = {node["name"].casefold() for node in _children(nodes, parent_id)}
        return planned_names[parent_id]

    for row in rows:
        key = row["key"]
        parent_key = row["parent_key"]
        if key in blocked:
            statuses[key] = "skip"
            reason[key] = "上级冲突或已跳过，本节点不会写入。"
            blocked.add(key)
            continue
        if key not in selected:
            statuses[key] = "skip"
            reason[key] = "未勾选。"
            continue
        if parent_key and parent_key not in mapping and mode != "subtree":
            statuses[key] = "skip"
            reason[key] = "上级未落入现有树，本节点不会写入。"
            blocked.add(key)
            continue
        resolution = resolutions.get(key)
        if resolution is not None and resolution.action == "skip" and not (mode == "merge" and key == root_key):
            statuses[key] = "skip"
            reason[key] = "按冲突处理跳过。"
            blocked.add(key)
            decisions.append({"key": key, "action": "skip", "new_name": ""})
            continue

        existing: Optional[Dict[str, Any]] = None
        desired_name = row["name"]
        if mode == "merge" and key == root_key:
            existing = by_id.get(target_node_id)
        elif mode == "subtree" and key == root_key:
            existing = _find_child(nodes, target_parent_id, desired_name)
        elif mode == "merge":
            parent_existing = mapping.get(parent_key or "")
            if parent_existing:
                existing = _find_child(nodes, parent_existing, desired_name)
        # replace 没有既有节点可比；subtree 的非根节点都是新建。

        conflict_code = ""
        if existing is not None:
            same_kind = existing["kind"] == row["kind"]
            if mode == "subtree":
                conflict_code = "ORG_TEMPLATE_NAME_CONFLICT"
            elif not same_kind:
                conflict_code = "ORG_TEMPLATE_KIND_CONFLICT"
            else:
                statuses[key] = "merge"
                mapping[key] = existing["node_id"]
                existing_for[key] = existing["node_id"]
                final_names[key] = existing["name"]
                continue

        if conflict_code:
            renamed = resolution is not None and resolution.action == "rename"
            if renamed:
                try:
                    candidate = _clean_name(resolution.new_name, "新名称")  # type: ignore[union-attr]
                except HTTPException as exc:
                    raise deny(
                        422,
                        "ORG_TEMPLATE_RENAME_CONFLICT",
                        "新名称不合法，请重新填写。",
                    ) from exc
                parent_id = target_parent_id if mode == "subtree" and key == root_key else mapping.get(parent_key or "")
                names = sibling_names(parent_id or "")
                if candidate.casefold() in names:
                    raise deny(
                        409,
                        "ORG_TEMPLATE_RENAME_CONFLICT",
                        f"「{candidate}」在目标上级下已存在同名节点，请换一个名称。",
                        key=key,
                    )
                names.add(candidate.casefold())
                desired_name = candidate
                decisions.append({"key": key, "action": "rename", "new_name": candidate})
                final_names[key] = candidate
                statuses[key] = "create"
                conflicts.append(
                    {
                        "key": key,
                        "code": conflict_code,
                        "name": row["name"],
                        "parent_key": parent_key,
                        "existing_node_id": existing["node_id"] if existing else None,
                        "existing_kind": existing["kind"] if existing else None,
                        "template_kind": row["kind"],
                        "resolved": "rename",
                        "options": ["rename", "skip"],
                    }
                )
                continue
            statuses[key] = "conflict"
            reason[key] = (
                "同名节点类型不同，需要改名或跳过。"
                if conflict_code == "ORG_TEMPLATE_KIND_CONFLICT"
                else "目标上级下已有同名节点，需要改名或跳过。"
            )
            conflicts.append(
                {
                    "key": key,
                    "code": conflict_code,
                    "name": row["name"],
                    "parent_key": parent_key,
                    "existing_node_id": existing["node_id"] if existing else None,
                    "existing_kind": existing["kind"] if existing else None,
                    "template_kind": row["kind"],
                    "resolved": None,
                    "options": ["rename", "skip"],
                }
            )
            blocked.add(key)
            continue

        # 无冲突：新建（或在 merge 根上映射锚点）。
        if mode == "merge" and key == root_key:
            statuses[key] = "merge"
            mapping[key] = target_node_id
            existing_for[key] = target_node_id
            final_names[key] = by_id[target_node_id]["name"]
            continue
        statuses[key] = "create"
        final_names[key] = desired_name
        # 预览阶段用占位 id 占住父子关系，让后代继续参与匹配；真正落库时由 apply 重建。
        mapping[key] = f"@new:{key}"

    # 先序插入需要父映射；merge 根的映射已就绪。这里不再补第二遍，插入时按行顺序取。

    counts = {"total": len(rows), "selected": len(selected), "create": 0, "merge": 0, "skip": 0, "conflict": 0}
    for key, status in statuses.items():
        counts[status] = counts.get(status, 0) + 1
    # 未出现（被上级未选中/阻断）的行也按 skip 计。
    counts["skip"] += len(rows) - len(statuses)

    base_seq = _latest_seq(connection, account_id)
    token = _canonical_hash(
        {
            "account": account_id,
            "template": _fingerprint(entry),
            "base_seq": base_seq,
            "mode": mode,
            "target_parent_id": target_parent_id,
            "target_node_id": target_node_id,
            "selected_keys": sorted(selected),
            "resolutions": sorted(
                (
                    {"key": item.key, "action": item.action, "new_name": item.new_name}
                    for item in req.resolutions
                ),
                key=lambda item: (item["key"], item["action"], item["new_name"]),
            ),
        }
    )

    warnings: Dict[str, Any]
    removals: Optional[Dict[str, Any]] = None
    if mode == "replace":
        removed_employees = {
            item["subject_id"]
            for item in assignments
            if item.get("subject_type") == "employee" and item.get("subject_id")
        }
        task_state = org._active_tasks(viewer, removed_employees)
        authority_items = [
            {
                "assignment_id": item["assignment_id"],
                "node_name": by_id.get(item["node_id"], {}).get("name", ""),
                "subject_id": item["subject_id"],
                "role": "lead" if item.get("is_lead") else "member",
            }
            for item in assignments[: org.MAX_IMPACT_ITEMS]
        ]
        removals = {
            "nodes": len(nodes),
            "assignments": len(assignments),
            "unattended_tasks": task_state,
            "authority_items": authority_items,
        }
        warnings = {
            "unattended_tasks": task_state,
            "authority_note": "组织内任职失效；组织数据当前不参与权限判定，不代表账号权限被收回。",
        }
    else:
        checked = {
            "checked": True,
            "count": 0,
            "items": [],
            "note": "本次套用不删除现有节点或任职，无需检查任务接管。",
        }
        warnings = {
            "unattended_tasks": checked,
            "authority_note": "组织数据当前不参与权限判定，不代表账号权限被收回。",
        }

    return {
        "template": {
            "template_id": entry["template_id"],
            "name": entry["name"],
            "source": entry["source"],
            "version": entry["version"],
            "revision": entry["revision"],
        },
        "mode": mode,
        "target": target,
        "base_seq": base_seq,
        "preview_token": token,
        "selected_keys": sorted(selected),
        "summary": counts,
        "nodes": [
            {
                "key": row["key"],
                "name": final_names.get(row["key"], row["name"]),
                "kind": row["kind"],
                "parent_key": row["parent_key"],
                "depth": row["depth"],
                "status": statuses.get(row["key"], "skip"),
                "existing_node_id": existing_for.get(row["key"]),
                "reason": reason.get(row["key"]),
            }
            for row in rows
        ],
        "conflicts": conflicts,
        "decisions": decisions,
        "removals": removals,
        "warnings": warnings,
        "rollback": {
            "available": base_seq > 0,
            "previous_seq": base_seq if base_seq > 0 else None,
        },
        "note": "只读预演，不写入任何数据。",
    }


@router.post("/{template_id}/preview")
def preview_template(
    template_id: str,
    req: TemplatePreviewRequest,
    viewer=Depends(org._org_viewer),
) -> Dict[str, Any]:
    account_id = viewer.principal
    connection = org._connect()
    try:
        entry = _resolve_template(connection, account_id, template_id)
        return _plan(connection, viewer, account_id, entry, req)
    finally:
        connection.close()


@router.post("/{template_id}/apply")
def apply_template(
    template_id: str,
    req: TemplateApplyRequest,
    viewer=Depends(org._org_viewer),
) -> Dict[str, Any]:
    account_id = viewer.principal
    actor = viewer.principal
    if not req.preview_token:
        raise deny(
            409,
            "ORG_TEMPLATE_STALE_PREVIEW",
            "套用前必须先预览；请重新预览后再确认。",
        )
    version: Dict[str, Any]
    created_nodes: List[Dict[str, Any]] = []
    with org._tx() as connection:
        entry = _resolve_template(connection, account_id, template_id)
        plan = _plan(connection, viewer, account_id, entry, TemplatePreviewRequest(**req.model_dump()))
        if plan["preview_token"] != req.preview_token:
            raise deny(
                409,
                "ORG_TEMPLATE_STALE_PREVIEW",
                "预览后组织或模板已被改动，请重新预览再确认。",
                base_seq=plan["base_seq"],
            )
        unresolved = [item for item in plan["conflicts"] if item.get("resolved") is None]
        if unresolved:
            raise deny(
                409,
                "ORG_TEMPLATE_CONFLICT",
                "还有冲突未处理，未写入任何数据。",
                conflicts=unresolved,
            )

        rows = _flatten(entry["payload"]["template"]["root"])
        row_by_key = {row["key"]: row for row in rows}
        statuses = {item["key"]: item["status"] for item in plan["nodes"]}
        names = {item["key"]: item["name"] for item in plan["nodes"]}
        mapping: Dict[str, str] = {}
        removed_nodes = 0
        removed_assignments = 0

        if plan["mode"] == "replace":
            removed_nodes = len(org._load_nodes(connection, account_id))
            removed_assignments = len(org._load_assignments(connection, account_id))
            connection.execute("DELETE FROM org_assignments WHERE account_id = ?", (account_id,))
            connection.execute("DELETE FROM org_nodes WHERE account_id = ?", (account_id,))

        for item in plan["nodes"]:
            key = item["key"]
            status = statuses.get(key, "skip")
            if status == "skip":
                continue
            if status == "merge":
                if item.get("existing_node_id"):
                    mapping[key] = str(item["existing_node_id"])
                continue
            row = row_by_key[key]
            if plan["mode"] == "subtree" and row["parent_key"] is None:
                parent_id = str(req.target_parent_id or "").strip()
            elif plan["mode"] == "replace" and row["parent_key"] is None:
                parent_id = ""
            else:
                parent_id = mapping.get(row["parent_key"] or "")
                if row["parent_key"] and not parent_id:
                    raise deny(
                        409,
                        "ORG_TEMPLATE_CONFLICT",
                        "上级节点没有落库，已整体取消，不留半棵树。",
                    )
            node = org._insert_node(
                connection,
                account_id,
                actor,
                name=names.get(key, row["name"]),
                kind=row["kind"],
                parent_id=parent_id or None,
                description=row.get("description") or "",
            )
            mapping[key] = node["node_id"]
            created_nodes.append(node)

        decisions = plan["decisions"]
        summary = {
            "template_id": entry["template_id"],
            "template_key": entry["template_id"],
            "template_name": entry["name"],
            "template_version": entry["version"],
            "template_source": entry["source"],
            "mode": plan["mode"],
            "target_node_id": (plan["target"] or {}).get("node_id") or "",
            "selected_count": plan["summary"]["selected"],
            "created_nodes": len(created_nodes),
            "merged_nodes": sum(1 for item in plan["nodes"] if item["status"] == "merge"),
            "renamed_nodes": sum(1 for item in decisions if item["action"] == "rename"),
            "skipped_nodes": sum(1 for item in plan["nodes"] if item["status"] == "skip"),
            "removed_nodes": removed_nodes,
            "removed_assignments": removed_assignments,
            "resolutions": decisions,
        }
        version = org._record_version(
            connection,
            account_id,
            actor=actor,
            action="template_apply",
            target_node_id=(plan["target"] or {}).get("node_id") or "",
            summary=summary,
        )
    audited = org._audit(
        "org.template_apply",
        account_id,
        actor,
        template_id,
        {
            "mode": plan["mode"],
            "seq": version["seq"],
            "created_nodes": len(created_nodes),
            "merged_nodes": summary["merged_nodes"],
        },
    )
    return {
        "template": plan["template"],
        "mode": plan["mode"],
        "target": plan["target"],
        "version": version,
        "summary": summary,
        "created_nodes": created_nodes,
        "rollback": {
            "available": version["seq"] > 1,
            "previous_seq": version["seq"] - 1 if version["seq"] > 1 else None,
            "note": (
                "套用已追加一版；可在版本历史回滚到上一版。"
                if version["seq"] > 1
                else "这是组织的第一版，没有更早的快照可回滚；继续调整后才可回滚到套用前。"
            ),
        },
        "audit_written": audited,
        "note": f"已套用《{entry['name']}》，写入第 {version['seq']} 版。",
    }
