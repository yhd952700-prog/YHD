"""企业知识库业务层：分类、文档、版本、全文检索与过期管理。

权限口径（与账号体系一致，越权一律中性 404）：

* **读**：``private`` 仅上传者；``team`` 上传者及其祖先账号（根能看整棵子树）；
  ``enterprise`` 所有已登录账号。
* **写/删**：只有文档上传者本人。Agent 拿不到「覆盖」入口 —— 新内容只能成为
  新版本，旧版本正文永久保留。
* **分类管理**：创建者本人；系统预置分类只有根账号能改。

可见性只能**收窄不能放宽**：文档可见性不得宽于所属分类。分类从 ``team`` 调到
``private`` 时，其下文档同步收窄；反向调整不会把历史文档自动放宽。
"""

from __future__ import annotations

import difflib
import hashlib
import math
import os
import re
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from src._time import utc_now

from .store import (
    knowledge_db_path,
    knowledge_files_dir,
    dumps_tags,
    loads_tags,
    new_id,
    now_iso,
    open_knowledge,
    row_to_dict,
)
from .text import (
    SUPPORTED_SUFFIXES,
    TextExtractionError,
    excerpt,
    extract_content,
    fts_query,
    mime_for,
    query_terms,
    segment_text,
    split_chunks,
    suffix_of,
)

VISIBILITY_RANK = {"private": 0, "team": 1, "enterprise": 2}
VISIBILITIES = tuple(VISIBILITY_RANK)

_MAX_TITLE = 300
_MAX_KEY = 200
_MAX_SOURCE_REF = 500
_MAX_TEXT_CHARS = 2_000_000
_MAX_DIFF_CHARS = 20_000


class KnowledgeError(Exception):
    """业务错误：路由层映射为 ``status`` + ``code``。"""

    def __init__(self, message: str, *, code: str = "knowledge_error", status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def not_found() -> KnowledgeError:
    """越权与不存在同形：不泄露「存在但不属于你」。"""
    return KnowledgeError("文档不存在或无权访问", code="not_found", status=404)


def invalid(message: str) -> KnowledgeError:
    return KnowledgeError(message, code="invalid_request", status=422)


def conflict(message: str) -> KnowledgeError:
    return KnowledgeError(message, code="conflict", status=409)


@dataclass(frozen=True)
class Viewer:
    """一次请求的账号视角。由网关注入，业务层不信任请求体里的归属字段。"""

    principal: str
    subtree: Tuple[str, ...]
    is_root: bool = False

    @property
    def owners(self) -> Tuple[str, ...]:
        return self.subtree or (self.principal,)

    def can_see(self, owner: str, visibility: str) -> bool:
        if owner == self.principal:
            return True
        if visibility == "private":
            return False
        if visibility == "team":
            return owner in self.owners
        return visibility == "enterprise"

    def can_write(self, owner: str) -> bool:
        return owner == self.principal


# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _owner_slug(owner: str) -> str:
    return hashlib.sha256(owner.encode("utf-8")).hexdigest()[:16]


def _now() -> str:
    return now_iso()


def _clean_identifier(value: Any, field: str, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        raise invalid(f"{field} 过长（上限 {limit} 字符）")
    return text


def _clean_title(value: Any, fallback: str) -> str:
    title = str(value or "").strip() or fallback.strip()
    if not title:
        raise invalid("标题不能为空")
    if len(title) > _MAX_TITLE:
        title = title[:_MAX_TITLE]
    return title


def _normalize_doc_key(value: Any, fallback: str) -> str:
    raw = str(value or "").strip() or fallback.strip()
    raw = raw.lower()
    if not raw:
        raise invalid("文档标识不能为空")
    if len(raw) > _MAX_KEY:
        raw = raw[:_MAX_KEY]
    return raw


def _normalize_visibility(value: Any, default: str) -> str:
    text = str(value or "").strip().lower() or default
    if text not in VISIBILITY_RANK:
        raise invalid(f"可见性只能是 {' / '.join(VISIBILITIES)}")
    return text


def _normalize_expiry(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    candidate = text.replace("Z", "+00:00")
    try:
        from datetime import date, datetime, time

        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", candidate):
            parsed = datetime.combine(date.fromisoformat(candidate), time(23, 59, 59))
        else:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(tz=None).replace(tzinfo=None)
    except ValueError as exc:
        raise invalid("过期时间格式不对；用 YYYY-MM-DD 或 ISO 时间") from exc
    return parsed.replace(microsecond=0).isoformat()


def _parse_stamp(value: Optional[str]):
    if not value:
        return None
    try:
        from datetime import datetime

        return datetime.fromisoformat(value)
    except ValueError:
        return None


def expiry_state(expires_at: Optional[str], notice_days: int) -> Tuple[str, Optional[int]]:
    """返回 (状态, 剩余天数)。状态：none / ok / expiring / expired。"""
    parsed = _parse_stamp(expires_at)
    if parsed is None:
        return "none", None
    delta = parsed - utc_now()
    if delta.total_seconds() <= 0:
        return "expired", 0
    days = int(math.ceil(delta.total_seconds() / 86400))
    if days <= max(0, int(notice_days)):
        return "expiring", days
    return "ok", days


def _like_escape(term: str) -> str:
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ---------------------------------------------------------------------------
# 分类
# ---------------------------------------------------------------------------


def _category_rows(connection: sqlite3.Connection) -> List[Dict[str, Any]]:
    rows = connection.execute(
        "SELECT * FROM kb_categories ORDER BY sort_order ASC, name ASC"
    ).fetchall()
    return [dict(row) for row in rows]


def _category_map(categories: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {item["id"]: item for item in categories}


def _descendant_ids(
    categories: Dict[str, Dict[str, Any]], root_id: str, include_self: bool = True
) -> List[str]:
    children: Dict[str, List[str]] = {}
    for item in categories.values():
        parent = item.get("parent_id") or ""
        children.setdefault(parent, []).append(item["id"])
    result: List[str] = [root_id] if include_self else []
    stack = [root_id]
    while stack:
        current = stack.pop()
        for child in children.get(current, []):
            result.append(child)
            stack.append(child)
    return result


def _breadcrumb(categories: Dict[str, Dict[str, Any]], category_id: Optional[str]) -> List[str]:
    names: List[str] = []
    current = categories.get(category_id or "")
    guard = 0
    while current is not None and guard < 20:
        names.append(current["name"])
        current = categories.get(current.get("parent_id") or "")
        guard += 1
    return list(reversed(names))


def _can_manage_category(viewer: Viewer, category: Dict[str, Any]) -> bool:
    if category.get("created_by") == viewer.principal:
        return True
    return bool(category.get("system")) and viewer.is_root


def _require_category(
    connection: sqlite3.Connection, category_id: str
) -> Dict[str, Any]:
    row = connection.execute(
        "SELECT * FROM kb_categories WHERE id = ?", (category_id,)
    ).fetchone()
    if row is None:
        raise invalid("分类不存在")
    return dict(row)


def _rebuild_category_paths(connection: sqlite3.Connection, root_id: str) -> None:
    categories = _category_map(_category_rows(connection))
    stack: List[Tuple[str, str]] = [(root_id, f"/{root_id}")]
    while stack:
        category_id, path = stack.pop()
        connection.execute(
            "UPDATE kb_categories SET path = ? WHERE id = ?", (path, category_id)
        )
        for item in categories.values():
            if (item.get("parent_id") or "") == category_id:
                stack.append((item["id"], f"{path}/{item['id']}"))


def create_category(
    viewer: Viewer,
    *,
    name: str,
    parent_id: str = "",
    visibility: str = "team",
    description: str = "",
) -> Dict[str, Any]:
    clean_name = _clean_title(name, "")
    if len(clean_name) > 80:
        raise invalid("分类名过长（上限 80 字符）")
    vis = _normalize_visibility(visibility, "team")
    now = _now()
    with open_knowledge() as connection:
        parent = ""
        if parent_id:
            parent_row = _require_category(connection, parent_id)
            parent = parent_row["id"]
        duplicate = connection.execute(
            "SELECT id FROM kb_categories WHERE COALESCE(parent_id, '') = ? AND name = ?",
            (parent, clean_name),
        ).fetchone()
        if duplicate is not None:
            raise conflict("同一层级下已有同名分类")
        category_id = new_id("cat")
        path = f"{parent_row['path']}/{category_id}" if parent else f"/{category_id}"
        connection.execute(
            "INSERT INTO kb_categories"
            " (id, name, parent_id, path, description, visibility, created_by,"
            "  system, sort_order, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?)",
            (
                category_id,
                clean_name,
                parent or None,
                path,
                str(description or "").strip(),
                vis,
                viewer.principal,
                now,
                now,
            ),
        )
        connection.commit()
        row = connection.execute(
            "SELECT * FROM kb_categories WHERE id = ?", (category_id,)
        ).fetchone()
        assert row is not None
        return _category_view(dict(row), [])


def update_category(
    viewer: Viewer,
    category_id: str,
    *,
    name: Optional[str] = None,
    parent_id: Optional[str] = None,
    visibility: Optional[str] = None,
    description: Optional[str] = None,
) -> Dict[str, Any]:
    with open_knowledge() as connection:
        category = _require_category(connection, category_id)
        if not _can_manage_category(viewer, category):
            raise not_found()
        updates: List[str] = []
        params: List[Any] = []
        if name is not None:
            clean_name = _clean_title(name, category["name"])
            sibling = connection.execute(
                "SELECT id FROM kb_categories WHERE COALESCE(parent_id, '') = ?"
                " AND name = ? AND id <> ?",
                (category.get("parent_id") or "", clean_name, category_id),
            ).fetchone()
            if sibling is not None:
                raise conflict("同一层级下已有同名分类")
            updates.append("name = ?")
            params.append(clean_name)
        if description is not None:
            updates.append("description = ?")
            params.append(str(description).strip())
        moved = False
        if parent_id is not None:
            target = parent_id.strip()
            if target and target != category_id:
                _require_category(connection, target)
                descendants = _descendant_ids(
                    _category_map(_category_rows(connection)), category_id
                )
                if target in descendants:
                    raise invalid("不能把分类移动到自己的子分类下")
                updates.append("parent_id = ?")
                params.append(target)
                moved = True
            elif not target:
                updates.append("parent_id = NULL")
                moved = True
        new_visibility = None
        if visibility is not None:
            new_visibility = _normalize_visibility(visibility, category["visibility"])
            updates.append("visibility = ?")
            params.append(new_visibility)
        if not updates:
            return _category_view(category, [])
        updates.append("updated_at = ?")
        params.append(_now())
        params.append(category_id)
        connection.execute(
            f"UPDATE kb_categories SET {', '.join(updates)} WHERE id = ?", params
        )
        if moved:
            _rebuild_category_paths(connection, category_id)
        if new_visibility is not None and VISIBILITY_RANK[new_visibility] < VISIBILITY_RANK[
            category["visibility"]
        ]:
            # 只收窄：把比新可见性更宽的文档同步压到新级别。
            wider = [
                name
                for name, rank in VISIBILITY_RANK.items()
                if rank > VISIBILITY_RANK[new_visibility]
            ]
            placeholders = ", ".join("?" for _ in wider)
            connection.execute(
                f"UPDATE kb_documents SET visibility = ?, updated_at = ?"
                f" WHERE category_id = ? AND visibility IN ({placeholders})",
                [new_visibility, _now(), category_id, *wider],
            )
        connection.commit()
        row = connection.execute(
            "SELECT * FROM kb_categories WHERE id = ?", (category_id,)
        ).fetchone()
        assert row is not None
        return _category_view(dict(row), [])


def delete_category(viewer: Viewer, category_id: str) -> Dict[str, Any]:
    with open_knowledge() as connection:
        category = _require_category(connection, category_id)
        if not _can_manage_category(viewer, category):
            raise not_found()
        children = connection.execute(
            "SELECT COUNT(*) AS n FROM kb_categories WHERE parent_id = ?", (category_id,)
        ).fetchone()["n"]
        if children:
            raise conflict("先删除或移动子分类")
        documents = connection.execute(
            "SELECT COUNT(*) AS n FROM kb_documents WHERE category_id = ?", (category_id,)
        ).fetchone()["n"]
        if documents:
            raise conflict("分类下还有文档，先移走或删除文档")
        connection.execute("DELETE FROM kb_categories WHERE id = ?", (category_id,))
        connection.commit()
        return {"deleted": True, "id": category_id}


def list_categories(viewer: Viewer) -> List[Dict[str, Any]]:
    with open_knowledge() as connection:
        categories = _category_map(_category_rows(connection))
        visibility_clause, params = _visibility_clause(viewer, alias="d")
        counts = {
            row["category_id"]: row["n"]
            for row in connection.execute(
                "SELECT d.category_id, COUNT(*) AS n FROM kb_documents d"
                f" WHERE d.status = 'active' AND {visibility_clause}"
                " GROUP BY d.category_id",
                params,
            ).fetchall()
        }
        views = {
            cat_id: _category_view(item, [], counts.get(cat_id, 0))
            for cat_id, item in categories.items()
        }
        for item in categories.values():
            parent = item.get("parent_id") or ""
            if parent and parent in views:
                views[parent]["children"].append(views[item["id"]])
        roots = [
            views[item["id"]]
            for item in categories.values()
            if not (item.get("parent_id") or "")
        ]
        return roots


def _category_view(
    row: Dict[str, Any], children: List[Dict[str, Any]], count: int = 0
) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "parent_id": row.get("parent_id"),
        "visibility": row["visibility"],
        "description": row.get("description") or "",
        "created_by": row.get("created_by") or "",
        "system": bool(row.get("system")),
        "document_count": int(count),
        "children": children,
    }


# ---------------------------------------------------------------------------
# 可见性 SQL
# ---------------------------------------------------------------------------


def _visibility_clause(viewer: Viewer, alias: str = "d") -> Tuple[str, List[Any]]:
    owners = viewer.owners
    placeholders = ", ".join("?" for _ in owners)
    clause = (
        f"(({alias}.visibility = 'private' AND {alias}.owner = ?)"
        f" OR ({alias}.visibility = 'team' AND {alias}.owner IN ({placeholders}))"
        f" OR {alias}.visibility = 'enterprise')"
    )
    return clause, [viewer.principal, *owners]


def _visible_documents(
    viewer: Viewer,
    connection: sqlite3.Connection,
    *,
    category_id: Optional[str] = None,
    include_subcategories: bool = True,
    project_id: Optional[str] = None,
    include_expired: bool = False,
) -> Tuple[str, Dict[str, Any]]:
    """拼 WHERE 片段（不含分页），返回 (sql, params)。"""
    clauses = ["d.status = 'active'"]
    params: Dict[str, Any] = {"viewer": viewer.principal}
    owners = viewer.owners
    owner_slots = []
    for index, owner in enumerate(owners):
        key = f"owner{index}"
        owner_slots.append(f":{key}")
        params[key] = owner
    clauses.append(
        "((d.visibility = 'private' AND d.owner = :viewer)"
        f" OR (d.visibility = 'team' AND d.owner IN ({', '.join(owner_slots)}))"
        " OR d.visibility = 'enterprise')"
    )

    if category_id:
        if include_subcategories:
            all_categories = _category_map(_category_rows(connection))
            if category_id not in all_categories:
                raise invalid("分类不存在")
            ids = _descendant_ids(all_categories, category_id)
            placeholders = ", ".join(f":cat{index}" for index in range(len(ids)))
            clauses.append(f"d.category_id IN ({placeholders})")
            for index, value in enumerate(ids):
                params[f"cat{index}"] = value
        else:
            clauses.append("d.category_id = :category")
            params["category"] = category_id
    if project_id:
        clauses.append("d.project_id = :project")
        params["project"] = project_id
    if not include_expired:
        clauses.append("(d.expires_at IS NULL OR d.expires_at = '' OR d.expires_at >= :now)")
        params["now"] = _now()
    return " AND ".join(clauses), params


# ---------------------------------------------------------------------------
# 文档
# ---------------------------------------------------------------------------


def _document_row(connection: sqlite3.Connection, document_id: str) -> Dict[str, Any]:
    row = connection.execute(
        "SELECT * FROM kb_documents WHERE id = ?", (document_id,)
    ).fetchone()
    if row is None:
        raise not_found()
    return dict(row)


def _version_meta(row: sqlite3.Row) -> Dict[str, Any]:
    data = dict(row)
    data.pop("extracted_text", None)
    return data


def _doc_summary(
    document: Dict[str, Any],
    categories: Dict[str, Dict[str, Any]],
    version: Optional[Dict[str, Any]],
    match: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    state, days = expiry_state(
        document.get("expires_at"), int(document.get("expiry_notice_days") or 30)
    )
    category_id = document.get("category_id")
    breadcrumb = _breadcrumb(categories, category_id)
    summary = {
        "id": document["id"],
        "title": document["title"],
        "doc_key": document["doc_key"],
        "owner_account_id": document["owner"],
        "project_id": document.get("project_id") or "",
        "category_id": category_id,
        "category_name": breadcrumb[-1] if breadcrumb else "",
        "category_path": breadcrumb,
        "source_type": document.get("source_type") or "",
        "source_ref": document.get("source_ref") or "",
        "origin_filename": document.get("origin_filename") or "",
        "mime": document.get("mime") or "",
        "visibility": document["visibility"],
        "tags": loads_tags(document.get("tags")),
        "description": document.get("description") or "",
        "current_version": int(document.get("current_version") or 0),
        "chunk_count": int((version or {}).get("chunk_count") or 0),
        "size_bytes": int((version or {}).get("size_bytes") or 0),
        "text_source": (version or {}).get("text_source") or "",
        "expires_at": document.get("expires_at"),
        "expiry_notice_days": int(document.get("expiry_notice_days") or 30),
        "expiry_state": state,
        "days_left": days,
        "created_at": document.get("created_at"),
        "updated_at": document.get("updated_at"),
    }
    if match:
        summary["match"] = match
    return summary


def _current_version(
    connection: sqlite3.Connection, document: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    row = connection.execute(
        "SELECT * FROM kb_document_versions WHERE document_id = ? AND version = ?",
        (document["id"], int(document.get("current_version") or 0)),
    ).fetchone()
    return _version_meta(row) if row is not None else None


def _store_file(source: Path, document_id: str, owner: str, version: int, filename: str) -> str:
    suffix = suffix_of(filename)
    target_dir = knowledge_files_dir() / _owner_slug(owner) / document_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"v{version}{suffix}"
    if target.exists():
        target = target_dir / f"v{version}_{int(utc_now().timestamp())}{suffix}"
    shutil.move(str(source), str(target))
    return str(target)


def _index_chunks(
    connection: sqlite3.Connection,
    document_id: str,
    version: int,
    title: str,
    chunks: Sequence[Dict[str, Any]],
) -> int:
    connection.execute(
        "DELETE FROM kb_chunk_fts WHERE document_id = ?", (document_id,)
    )
    connection.execute("DELETE FROM kb_chunks WHERE document_id = ?", (document_id,))
    title_seg = segment_text(title)
    inserted = 0
    for chunk in chunks:
        chunk_id = new_id("chk")
        connection.execute(
            "INSERT INTO kb_chunks"
            " (id, document_id, version, chunk_index, text, heading_path, page)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                chunk_id,
                document_id,
                version,
                chunk["index"],
                chunk["text"],
                chunk["heading_path"],
                chunk.get("page"),
            ),
        )
        connection.execute(
            "INSERT INTO kb_chunk_fts (seg, title_seg, chunk_id, document_id)"
            " VALUES (?, ?, ?, ?)",
            (segment_text(chunk["text"]), title_seg, chunk_id, document_id),
        )
        inserted += 1
    return inserted


def ingest_file(
    viewer: Viewer,
    *,
    source: Path,
    filename: str,
    title: str = "",
    category_id: str = "",
    doc_key: str = "",
    visibility: str = "",
    project_id: str = "",
    source_ref: str = "",
    description: str = "",
    expires_at: Optional[str] = None,
    expiry_notice_days: int = 30,
    tags: Optional[Sequence[str]] = None,
    note: str = "",
    allow_low_quality: bool = False,
) -> Dict[str, Any]:
    safe_name = Path(filename).name
    if not safe_name or safe_name in {".", ".."}:
        raise invalid("文件名无效")
    if len(safe_name) > 200:
        raise invalid("文件名过长（上限 200 字符）")
    suffix = suffix_of(safe_name)
    if suffix not in SUPPORTED_SUFFIXES:
        raise TextExtractionError(
            f"不支持的文件类型：{suffix or '未知后缀'}", code="unsupported_file_type", status=415
        )
    extracted = extract_content(
        source, safe_name, description=description, allow_low_quality=allow_low_quality
    )
    text = extracted.text
    if len(text) > _MAX_TEXT_CHARS:
        raise invalid(f"提取正文过长（上限 {_MAX_TEXT_CHARS} 字符），请拆分后上传")
    return _ingest_extracted(
        viewer,
        text=text,
        filename=safe_name,
        source_path=source,
        file_type=extracted.file_type,
        text_source=extracted.text_source,
        title=title or extracted.title or Path(safe_name).stem,
        category_id=category_id,
        doc_key=doc_key,
        visibility=visibility,
        project_id=project_id,
        source_ref=source_ref,
        description=description,
        expires_at=expires_at,
        expiry_notice_days=expiry_notice_days,
        tags=tags,
        note=note,
    )


def ingest_text(
    viewer: Viewer,
    *,
    text: str,
    title: str,
    category_id: str = "",
    doc_key: str = "",
    visibility: str = "",
    project_id: str = "",
    source_ref: str = "",
    description: str = "",
    expires_at: Optional[str] = None,
    expiry_notice_days: int = 30,
    tags: Optional[Sequence[str]] = None,
    note: str = "",
) -> Dict[str, Any]:
    body = (text or "").strip()
    if not body:
        raise invalid("正文不能为空")
    if len(body) > _MAX_TEXT_CHARS:
        raise invalid(f"正文过长（上限 {_MAX_TEXT_CHARS} 字符）")
    return _ingest_extracted(
        viewer,
        text=body,
        filename="",
        source_path=None,
        file_type="text",
        text_source="manual_text",
        title=title,
        category_id=category_id,
        doc_key=doc_key,
        visibility=visibility,
        project_id=project_id,
        source_ref=source_ref,
        description=description,
        expires_at=expires_at,
        expiry_notice_days=expiry_notice_days,
        tags=tags,
        note=note,
    )


def _ingest_extracted(
    viewer: Viewer,
    *,
    text: str,
    filename: str,
    source_path: Optional[Path],
    file_type: str,
    text_source: str,
    title: str,
    category_id: str,
    doc_key: str,
    visibility: str,
    project_id: str,
    source_ref: str,
    description: str,
    expires_at: Optional[str],
    expiry_notice_days: int,
    tags: Optional[Sequence[str]],
    note: str,
) -> Dict[str, Any]:
    clean_title = _clean_title(title, Path(filename).stem if filename else "未命名文档")
    key = _normalize_doc_key(doc_key, filename or clean_title)
    clean_project = _clean_identifier(project_id, "项目标识", _MAX_KEY)
    clean_source_ref = _clean_identifier(source_ref, "来源说明", _MAX_SOURCE_REF)
    expiry = _normalize_expiry(expires_at)
    notice_days = max(0, min(int(expiry_notice_days or 30), 3650))
    content_hash = _sha256(text)
    chunks = split_chunks(text)
    if not chunks:
        raise invalid("提取后没有可索引的正文")

    with open_knowledge() as connection:
        category = None
        if category_id:
            category = _require_category(connection, category_id)

        existing = connection.execute(
            "SELECT * FROM kb_documents WHERE owner = ? AND doc_key = ?",
            (viewer.principal, key),
        ).fetchone()
        # 新版本默认继承旧可见性；分类更窄时取更窄者。显式放宽一律拒绝。
        if existing is not None:
            default_visibility = existing["visibility"]
            if category and VISIBILITY_RANK[category["visibility"]] < VISIBILITY_RANK[
                default_visibility
            ]:
                default_visibility = category["visibility"]
        else:
            default_visibility = category["visibility"] if category else "team"
        vis = _normalize_visibility(visibility, default_visibility)
        if category and VISIBILITY_RANK[vis] > VISIBILITY_RANK[category["visibility"]]:
            raise invalid("文档可见性不能宽于所属分类")
        if existing is not None and VISIBILITY_RANK[vis] > VISIBILITY_RANK[existing["visibility"]]:
            raise invalid("新版本不能放宽文档可见性；如需放宽请新建文档")
        now = _now()
        if existing is not None:
            document = dict(existing)
            if document["status"] == "active" and int(document["current_version"] or 0) > 0:
                current = connection.execute(
                    "SELECT content_hash FROM kb_document_versions"
                    " WHERE document_id = ? AND version = ?",
                    (document["id"], int(document["current_version"])),
                ).fetchone()
                if current is not None and current["content_hash"] == content_hash:
                    summary = _doc_summary(
                        document, _category_map(_category_rows(connection)),
                        _current_version(connection, document),
                    )
                    summary["deduplicated"] = True
                    return summary
            document_id = document["id"]
            version = int(
                connection.execute(
                    "SELECT COALESCE(MAX(version), 0) AS v FROM kb_document_versions"
                    " WHERE document_id = ?",
                    (document_id,),
                ).fetchone()["v"]
            ) + 1
        else:
            document_id = new_id("doc")
            version = 1

        stored_path = ""
        size_bytes = 0
        if source_path is not None:
            size_bytes = source_path.stat().st_size
            stored_path = _store_file(
                source_path, document_id, viewer.principal, version, filename or "upload.bin"
            )
        else:
            size_bytes = len(text.encode("utf-8"))

        tag_json = dumps_tags(tags) if tags is not None else (
            document["tags"] if existing is not None else "[]"
        )
        if existing is None:
            # 先插主表满足版本表外键；current_version 此时已经是本次版本号。
            connection.execute(
                "INSERT INTO kb_documents"
                " (id, owner, project_id, category_id, doc_key, title, current_version,"
                "  source_type, source_ref, origin_filename, mime, visibility, tags,"
                "  description, expires_at, expiry_notice_days, status, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)",
                (
                    document_id,
                    viewer.principal,
                    clean_project,
                    category["id"] if category else None,
                    key,
                    clean_title,
                    version,
                    "upload" if source_path is not None else "text",
                    clean_source_ref,
                    filename,
                    mime_for(filename) if filename else "text/plain",
                    vis,
                    tag_json,
                    str(description or "").strip(),
                    expiry,
                    notice_days,
                    now,
                    now,
                ),
            )
        connection.execute(
            "INSERT INTO kb_document_versions"
            " (id, document_id, version, content_hash, extracted_text, chunk_count,"
            "  file_path, origin_filename, mime, size_bytes, text_source, note,"
            "  created_by, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                new_id("ver"),
                document_id,
                version,
                content_hash,
                text,
                len(chunks),
                stored_path,
                filename,
                mime_for(filename) if filename else "text/plain",
                size_bytes,
                text_source,
                str(note or "").strip(),
                viewer.principal,
                now,
            ),
        )
        if existing is not None:
            connection.execute(
                "UPDATE kb_documents SET title = ?, current_version = ?,"
                " source_type = ?, source_ref = ?, origin_filename = ?, mime = ?,"
                " visibility = ?, tags = ?, description = ?, expires_at = ?,"
                " expiry_notice_days = ?, status = 'active', deleted_at = NULL,"
                " updated_at = ?,"
                " category_id = COALESCE(?, category_id),"
                " project_id = CASE WHEN ? <> '' THEN ? ELSE project_id END"
                " WHERE id = ?",
                (
                    clean_title,
                    version,
                    "upload" if source_path is not None else "text",
                    clean_source_ref,
                    filename,
                    mime_for(filename) if filename else "text/plain",
                    vis,
                    tag_json,
                    str(description or "").strip(),
                    expiry,
                    notice_days,
                    now,
                    category["id"] if category else None,
                    clean_project,
                    clean_project,
                    document_id,
                ),
            )
        _index_chunks(connection, document_id, version, clean_title, chunks)
        connection.commit()
        stored = _document_row(connection, document_id)
        summary = _doc_summary(
            stored,
            _category_map(_category_rows(connection)),
            _current_version(connection, stored),
        )
        summary["deduplicated"] = False
        summary["created"] = existing is None
        summary["file_type"] = file_type
        summary["text_source"] = text_source
        return summary


def list_documents(
    viewer: Viewer,
    *,
    category_id: str = "",
    include_subcategories: bool = True,
    project_id: str = "",
    include_expired: bool = False,
    q: str = "",
    limit: int = 20,
    offset: int = 0,
    expiring_within_days: Optional[int] = None,
) -> Dict[str, Any]:
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    with open_knowledge() as connection:
        categories = _category_map(_category_rows(connection))
        where, params = _visible_documents(
            viewer,
            connection,
            category_id=category_id or None,
            include_subcategories=include_subcategories,
            project_id=project_id or None,
            include_expired=include_expired or expiring_within_days is not None,
        )
        rank_map: Dict[str, float] = {}
        match_map: Dict[str, Dict[str, Any]] = {}
        if q.strip():
            rank_map, match_map = _document_rank_map(connection, viewer, q, where, params)
            if not rank_map:
                return {"documents": [], "total": 0, "limit": limit, "offset": offset, "query": q}
            placeholders = ", ".join(f":id{index}" for index in range(len(rank_map)))
            where = f"{where} AND d.id IN ({placeholders})"
            for index, document_id in enumerate(rank_map):
                params[f"id{index}"] = document_id
        rows = connection.execute(
            f"SELECT d.* FROM kb_documents d WHERE {where}", params
        ).fetchall()
        documents = [dict(row) for row in rows]
        if expiring_within_days is not None:
            days = max(0, int(expiring_within_days))
            documents = [
                item
                for item in documents
                if expiry_state(item.get("expires_at"), days)[0] in ("expired", "expiring")
            ]
        if q.strip():
            documents.sort(key=lambda item: rank_map.get(item["id"], 0.0), reverse=True)
        else:
            documents.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
        total = len(documents)
        page = documents[offset : offset + limit]
        versions: Dict[str, Dict[str, Any]] = {}
        for item in page:
            current = _current_version(connection, item)
            if current is not None:
                versions[item["id"]] = current
        views = [
            _doc_summary(item, categories, versions.get(item["id"]), match_map.get(item["id"]))
            for item in page
        ]
        return {
            "documents": views,
            "total": total,
            "limit": limit,
            "offset": offset,
            "query": q,
            "retrieval": {"mode": "fts5_bm25"} if q.strip() else None,
        }


def _document_rank_map(
    connection: sqlite3.Connection,
    viewer: Viewer,
    query: str,
    where: str,
    params: Dict[str, Any],
) -> Tuple[Dict[str, float], Dict[str, Dict[str, Any]]]:
    """返回 {document_id: 归一化分数} 与 {document_id: 最佳命中片段}。"""
    expression = fts_query(query)
    if expression is None:
        return {}, {}
    sql = (
        "SELECT c.document_id AS document_id, c.text AS text, c.heading_path AS heading_path,"
        " c.page AS page, bm25(kb_chunk_fts, 1.0, 3.0) AS rank"
        " FROM kb_chunk_fts"
        " JOIN kb_chunks c ON c.id = kb_chunk_fts.chunk_id"
        " JOIN kb_documents d ON d.id = c.document_id"
        f" WHERE kb_chunk_fts MATCH :expr AND {where}"
        " ORDER BY rank ASC LIMIT 2000"
    )
    try:
        rows = connection.execute(sql, {**params, "expr": expression}).fetchall()
    except sqlite3.OperationalError:
        return _like_rank_map(connection, viewer, query, where, params)
    if not rows:
        return _like_rank_map(connection, viewer, query, where, params)
    scores: Dict[str, float] = {}
    matches: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        document_id = row["document_id"]
        score = _normalize_score(-float(row["rank"]))
        if score > scores.get(document_id, 0.0):
            scores[document_id] = score
        if document_id not in matches:
            matches[document_id] = {
                "excerpt": excerpt(row["text"], query),
                "heading_path": row["heading_path"] or "",
                "page": row["page"],
            }
    return scores, matches


def _like_rank_map(
    connection: sqlite3.Connection,
    viewer: Viewer,
    query: str,
    where: str,
    params: Dict[str, Any],
) -> Tuple[Dict[str, float], Dict[str, Dict[str, Any]]]:
    """FTS5 不可用/无命中时的降级：LIKE 词法匹配，分数=命中词占比。"""
    terms = query_terms(query)
    if not terms:
        return {}, {}
    clauses = []
    like_params = dict(params)
    for index, term in enumerate(terms):
        clauses.append(
            f"(c.text LIKE :like{index} ESCAPE '\\' OR d.title LIKE :like{index} ESCAPE '\\')"
        )
        like_params[f"like{index}"] = f"%{_like_escape(term)}%"
    rows = connection.execute(
        "SELECT c.document_id AS document_id, c.text AS text, c.heading_path AS heading_path,"
        " c.page AS page"
        " FROM kb_chunks c JOIN kb_documents d ON d.id = c.document_id"
        f" WHERE ({' OR '.join(clauses)}) AND {where}"
        " LIMIT 2000",
        like_params,
    ).fetchall()
    scores: Dict[str, float] = {}
    matches: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        document_id = row["document_id"]
        lowered = (row["text"] or "").lower()
        hits = sum(1 for term in terms if term in lowered)
        score = round(hits / max(1, len(terms)), 4)
        if score > scores.get(document_id, 0.0):
            scores[document_id] = score
        if document_id not in matches:
            matches[document_id] = {
                "excerpt": excerpt(row["text"], query),
                "heading_path": row["heading_path"] or "",
                "page": row["page"],
            }
    return scores, matches


def _normalize_score(raw: float) -> float:
    """BM25 分数 -> 0..1。只用于展示排序强弱，不是概率。"""
    if raw <= 0:
        return 0.0
    return round(min(1.0, 1.0 - math.exp(-raw / 8.0)), 4)


def search(
    viewer: Viewer,
    query: str,
    *,
    category_id: str = "",
    include_subcategories: bool = True,
    project_id: str = "",
    top_k: int = 8,
    include_expired: bool = False,
    min_score: float = 0.0,
) -> Dict[str, Any]:
    text = (query or "").strip()
    if not text:
        raise invalid("检索词不能为空")
    top_k = max(1, min(int(top_k), 50))
    with open_knowledge() as connection:
        where, params = _visible_documents(
            viewer,
            connection,
            category_id=category_id or None,
            include_subcategories=include_subcategories,
            project_id=project_id or None,
            include_expired=include_expired,
        )
        expression = fts_query(text)
        mode = "fts5_bm25"
        rows: List[sqlite3.Row] = []
        if expression is not None:
            sql = (
                "SELECT c.id AS chunk_id, c.document_id AS document_id, c.text AS text,"
                " c.heading_path AS heading_path, c.page AS page, c.version AS version,"
                " d.title AS title, d.owner AS owner, d.category_id AS category_id,"
                " d.source_type AS source_type, d.source_ref AS source_ref,"
                " d.origin_filename AS origin_filename, d.expires_at AS expires_at,"
                " d.expiry_notice_days AS expiry_notice_days,"
                " bm25(kb_chunk_fts, 1.0, 3.0) AS rank"
                " FROM kb_chunk_fts"
                " JOIN kb_chunks c ON c.id = kb_chunk_fts.chunk_id"
                " JOIN kb_documents d ON d.id = c.document_id"
                f" WHERE kb_chunk_fts MATCH :expr AND {where}"
                " AND c.version = d.current_version"
                " ORDER BY rank ASC LIMIT :limit"
            )
            try:
                rows = connection.execute(
                    sql, {**params, "expr": expression, "limit": top_k}
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
                mode = "like_fallback"
        else:
            mode = "like_fallback"
        if not rows:
            if mode == "fts5_bm25":
                mode = "like_fallback"
            rows, _ = _like_hits(connection, viewer, text, where, params, top_k)
        categories = _category_map(_category_rows(connection))
        hits = []
        for row in rows:
            state, days = expiry_state(
                row["expires_at"], int(row["expiry_notice_days"] or 30)
            )
            if "rank" in row.keys() and row["rank"] is not None:
                score = _normalize_score(-float(row["rank"]))
            else:
                score = float(row["score"] or 0.0)
            if score < min_score:
                continue
            breadcrumb = _breadcrumb(categories, row["category_id"])
            hits.append(
                {
                    "chunk_id": row["chunk_id"],
                    "document_id": row["document_id"],
                    "title": row["title"],
                    "score": score,
                    "text": row["text"],
                    "excerpt": excerpt(row["text"], text),
                    "heading_path": row["heading_path"] or "",
                    "page": row["page"],
                    "version": int(row["version"]),
                    "owner_account_id": row["owner"],
                    "category_id": row["category_id"],
                    "category_path": breadcrumb,
                    "source_type": row["source_type"] or "",
                    "source_ref": row["source_ref"] or "",
                    "origin_filename": row["origin_filename"] or "",
                    "expires_at": row["expires_at"],
                    "expiry_state": state,
                    "days_left": days,
                }
            )
        has_documents = connection.execute(
            f"SELECT 1 FROM kb_documents d WHERE {where} LIMIT 1", params
        ).fetchone()
        status = "ok" if hits else "no_relevant"
        reason = None if hits else ("empty_corpus" if has_documents is None else "below_threshold")
        return {
            "query": text,
            "count": len(hits),
            "hits": hits,
            "retrieval": {
                "mode": mode,
                "status": status,
                "reason": reason,
                "top_score": hits[0]["score"] if hits else None,
                "note": (
                    "词法检索（FTS5 + 中文 bigram + BM25），不是语义相似度"
                    if mode == "fts5_bm25"
                    else "FTS5 不可用或未命中，已降级为关键词匹配"
                ),
            },
        }


def _like_hits(
    connection: sqlite3.Connection,
    viewer: Viewer,
    query: str,
    where: str,
    params: Dict[str, Any],
    top_k: int,
) -> Tuple[List[sqlite3.Row], str]:
    terms = query_terms(query)
    if not terms:
        return [], "like_fallback"
    clauses = []
    like_params = dict(params)
    for index, term in enumerate(terms):
        clauses.append(
            f"(c.text LIKE :like{index} ESCAPE '\\' OR d.title LIKE :like{index} ESCAPE '\\')"
        )
        like_params[f"like{index}"] = f"%{_like_escape(term)}%"
    rows = connection.execute(
        "SELECT c.id AS chunk_id, c.document_id AS document_id, c.text AS text,"
        " c.heading_path AS heading_path, c.page AS page, c.version AS version,"
        " d.title AS title, d.owner AS owner, d.category_id AS category_id,"
        " d.source_type AS source_type, d.source_ref AS source_ref,"
        " d.origin_filename AS origin_filename, d.expires_at AS expires_at,"
        " d.expiry_notice_days AS expiry_notice_days,"
        " 0.5 AS score"
        " FROM kb_chunks c JOIN kb_documents d ON d.id = c.document_id"
        f" WHERE ({' OR '.join(clauses)}) AND {where}"
        " AND c.version = d.current_version"
        f" LIMIT {int(top_k)}",
        like_params,
    ).fetchall()
    return list(rows), "like_fallback"


def get_document(viewer: Viewer, document_id: str, *, include_text: bool = True) -> Dict[str, Any]:
    with open_knowledge() as connection:
        document = _document_row(connection, document_id)
        if document.get("status") != "active" or not viewer.can_see(
            document["owner"], document["visibility"]
        ):
            raise not_found()
        categories = _category_map(_category_rows(connection))
        versions = [
            _version_meta(row)
            for row in connection.execute(
                "SELECT * FROM kb_document_versions WHERE document_id = ?"
                " ORDER BY version DESC",
                (document_id,),
            ).fetchall()
        ]
        current = _current_version(connection, document)
        summary = _doc_summary(document, categories, current)
        summary["versions"] = versions
        if include_text and current is not None:
            row = connection.execute(
                "SELECT extracted_text FROM kb_document_versions"
                " WHERE document_id = ? AND version = ?",
                (document_id, int(document["current_version"])),
            ).fetchone()
            text = row["extracted_text"] if row is not None else ""
            summary["text"] = text[:20_000]
            summary["text_truncated"] = len(text) > 20_000
        summary["can_write"] = viewer.can_write(document["owner"])
        return summary


def get_version(
    viewer: Viewer, document_id: str, version: int, *, max_chars: int = 20_000
) -> Dict[str, Any]:
    with open_knowledge() as connection:
        document = _document_row(connection, document_id)
        if document.get("status") != "active" or not viewer.can_see(
            document["owner"], document["visibility"]
        ):
            raise not_found()
        row = connection.execute(
            "SELECT * FROM kb_document_versions WHERE document_id = ? AND version = ?",
            (document_id, int(version)),
        ).fetchone()
        if row is None:
            raise not_found()
        data = dict(row)
        text = data.pop("extracted_text", "")
        data["text"] = text[:max_chars]
        data["text_truncated"] = len(text) > max_chars
        data["is_current"] = int(version) == int(document["current_version"])
        return data


def diff_versions(
    viewer: Viewer, document_id: str, from_version: int, to_version: int
) -> Dict[str, Any]:
    with open_knowledge() as connection:
        document = _document_row(connection, document_id)
        if document.get("status") != "active" or not viewer.can_see(
            document["owner"], document["visibility"]
        ):
            raise not_found()
        texts: Dict[int, str] = {}
        for version in (from_version, to_version):
            row = connection.execute(
                "SELECT extracted_text FROM kb_document_versions"
                " WHERE document_id = ? AND version = ?",
                (document_id, int(version)),
            ).fetchone()
            if row is None:
                raise not_found()
            texts[int(version)] = row["extracted_text"]
        old = texts[int(from_version)].splitlines()
        new = texts[int(to_version)].splitlines()
        unified = list(
            difflib.unified_diff(
                old, new, fromfile=f"v{from_version}", tofile=f"v{to_version}", lineterm="", n=2
            )
        )
        added = sum(1 for line in unified if line.startswith("+") and not line.startswith("+++"))
        removed = sum(1 for line in unified if line.startswith("-") and not line.startswith("---"))
        body = "\n".join(unified)
        return {
            "document_id": document_id,
            "from_version": int(from_version),
            "to_version": int(to_version),
            "added_lines": added,
            "removed_lines": removed,
            "changed": bool(unified),
            "unified": body[:_MAX_DIFF_CHARS],
            "truncated": len(body) > _MAX_DIFF_CHARS,
        }


def update_document(
    viewer: Viewer,
    document_id: str,
    *,
    title: Optional[str] = None,
    category_id: Optional[str] = None,
    visibility: Optional[str] = None,
    expires_at: Optional[str] = None,
    clear_expiry: bool = False,
    expiry_notice_days: Optional[int] = None,
    tags: Optional[Sequence[str]] = None,
    description: Optional[str] = None,
    project_id: Optional[str] = None,
    source_ref: Optional[str] = None,
) -> Dict[str, Any]:
    with open_knowledge() as connection:
        document = _document_row(connection, document_id)
        if not viewer.can_write(document["owner"]) or document.get("status") != "active":
            raise not_found()
        updates: List[str] = []
        params: List[Any] = []
        if title is not None:
            updates.append("title = ?")
            params.append(_clean_title(title, document["title"]))
        target_category = document.get("category_id")
        if category_id is not None:
            target_category = category_id.strip() or None
            if target_category:
                _require_category(connection, target_category)
            updates.append("category_id = ?")
            params.append(target_category)
        if visibility is not None:
            vis = _normalize_visibility(visibility, document["visibility"])
            category = (
                _require_category(connection, target_category) if target_category else None
            )
            if category and VISIBILITY_RANK[vis] > VISIBILITY_RANK[category["visibility"]]:
                raise invalid("文档可见性不能宽于所属分类")
            if document["visibility"] == "private" and vis != "private":
                raise invalid("私有文档只能由本人保持私有，不能扩大可见范围")
            updates.append("visibility = ?")
            params.append(vis)
        if clear_expiry:
            updates.append("expires_at = NULL")
        elif expires_at is not None:
            updates.append("expires_at = ?")
            params.append(_normalize_expiry(expires_at))
        if expiry_notice_days is not None:
            updates.append("expiry_notice_days = ?")
            params.append(max(0, min(int(expiry_notice_days), 3650)))
        if tags is not None:
            updates.append("tags = ?")
            params.append(dumps_tags(tags))
        if description is not None:
            updates.append("description = ?")
            params.append(str(description).strip())
        if project_id is not None:
            updates.append("project_id = ?")
            params.append(_clean_identifier(project_id, "项目标识", _MAX_KEY))
        if source_ref is not None:
            updates.append("source_ref = ?")
            params.append(_clean_identifier(source_ref, "来源说明", _MAX_SOURCE_REF))
        if not updates:
            raise invalid("没有需要更新的字段")
        updates.append("updated_at = ?")
        params.append(_now())
        params.append(document_id)
        connection.execute(
            f"UPDATE kb_documents SET {', '.join(updates)} WHERE id = ?", params
        )
        connection.commit()
        stored = _document_row(connection, document_id)
        return _doc_summary(
            stored,
            _category_map(_category_rows(connection)),
            _current_version(connection, stored),
        )


def delete_document(viewer: Viewer, document_id: str) -> Dict[str, Any]:
    """软删除：正文版本与原件保留在库里，索引与列表立即消失。"""
    with open_knowledge() as connection:
        document = _document_row(connection, document_id)
        if not viewer.can_write(document["owner"]) or document.get("status") != "active":
            raise not_found()
        now = _now()
        connection.execute(
            "UPDATE kb_documents SET status = 'deleted', deleted_at = ?, updated_at = ?"
            " WHERE id = ?",
            (now, now, document_id),
        )
        connection.execute(
            "DELETE FROM kb_chunk_fts WHERE document_id = ?", (document_id,)
        )
        connection.execute("DELETE FROM kb_chunks WHERE document_id = ?", (document_id,))
        connection.commit()
        return {"deleted": True, "id": document_id, "deleted_at": now}


def expiring_documents(viewer: Viewer, *, days: int = 30, limit: int = 100) -> Dict[str, Any]:
    days = max(0, min(int(days), 3650))
    with open_knowledge() as connection:
        where, params = _visible_documents(viewer, connection, include_expired=True)
        rows = connection.execute(
            "SELECT d.expires_at, d.expiry_notice_days FROM kb_documents d"
            f" WHERE {where} AND d.expires_at IS NOT NULL AND d.expires_at <> ''",
            params,
        ).fetchall()
        counts = {"expired": 0, "expiring": 0}
        for row in rows:
            state, _ = expiry_state(
                row["expires_at"], int(row["expiry_notice_days"] or 30)
            )
            if state in counts:
                counts[state] += 1
    result = list_documents(
        viewer,
        include_expired=True,
        limit=limit,
        expiring_within_days=days,
    )
    return {
        "days": days,
        "counts": counts,
        "documents": result["documents"],
        "total": result["total"],
    }


def document_file_info(
    viewer: Viewer, document_id: str, version: Optional[int] = None
) -> Tuple[Path, str, str]:
    """返回 (原件路径, 原始文件名, MIME)。纯文本入库的版本没有原件。"""
    with open_knowledge() as connection:
        document = _document_row(connection, document_id)
        if document.get("status") != "active" or not viewer.can_see(
            document["owner"], document["visibility"]
        ):
            raise not_found()
        target_version = int(version or document["current_version"])
        row = connection.execute(
            "SELECT file_path, origin_filename, mime FROM kb_document_versions"
            " WHERE document_id = ? AND version = ?",
            (document_id, target_version),
        ).fetchone()
        if row is None:
            raise not_found()
        stored = Path(row["file_path"] or "")
        if not row["file_path"] or not stored.is_file():
            raise KnowledgeError(
                "该版本没有原件（纯文本入库），只有提取后的正文",
                code="file_not_stored",
                status=404,
            )
        return stored, row["origin_filename"] or stored.name, row["mime"] or "application/octet-stream"


def document_file_path(viewer: Viewer, document_id: str, version: Optional[int] = None) -> Path:
    return document_file_info(viewer, document_id, version)[0]


def status_counts(viewer: Viewer) -> Dict[str, Any]:
    with open_knowledge() as connection:
        where, params = _visible_documents(viewer, connection)
        total = connection.execute(
            f"SELECT COUNT(*) AS n FROM kb_documents d WHERE {where}", params
        ).fetchone()["n"]
        chunks = connection.execute(
            "SELECT COALESCE(SUM(v.chunk_count), 0) AS n FROM kb_documents d"
            " JOIN kb_document_versions v ON v.document_id = d.id"
            " AND v.version = d.current_version"
            f" WHERE {where}",
            params,
        ).fetchone()["n"]
        categories = connection.execute(
            "SELECT COUNT(*) AS n FROM kb_categories"
        ).fetchone()["n"]
        expiring = expiring_documents(viewer, days=30, limit=200)
        return {
            "documents": int(total),
            "chunks": int(chunks),
            "categories": int(categories),
            "expired": expiring["counts"]["expired"],
            "expiring": expiring["counts"]["expiring"],
            "store": knowledge_db_path(),
            "retrieval": "fts5_bm25",
            "supported_types": sorted(item.lstrip(".") for item in SUPPORTED_SUFFIXES),
        }


__all__ = [
    "KnowledgeError",
    "Viewer",
    "VISIBILITIES",
    "conflict",
    "create_category",
    "delete_category",
    "delete_document",
    "diff_versions",
    "document_file_path",
    "expiring_documents",
    "expiry_state",
    "get_document",
    "get_version",
    "ingest_file",
    "ingest_text",
    "invalid",
    "list_categories",
    "list_documents",
    "not_found",
    "search",
    "status_counts",
    "update_category",
    "update_document",
]
