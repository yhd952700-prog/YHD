"""企业长期记忆：与知识库、正式业务库物理分离的独立 SQLite 库。

隔离规则
========

* 独立库文件（``LIUHAO_KNOWLEDGE_MEMORY_DB``，默认 ``data/knowledge_memory.sqlite3``），
  本模块的 SQL 只出现 ``lt_memories`` / ``lt_memory_revisions``，没有任何
  ``kb_documents`` 写入口。Agent 通过记忆接口**不可能覆盖企业资料**。
* ``personal`` 记忆只有本人可见；``project`` / ``employee`` 记忆对记录者及其
  祖先账号可见；``enterprise`` 记忆全公司可见。写/改/删永远只属于记录者本人。
* 项目（``project_id``）与员工（``employee_id``）是记忆视图的两个隔离维度，
  列表、聚合视图都按这两个字段过滤；不做跨项目拼接。

记忆内容可以频繁重写，但每次更新都会写入 ``lt_memory_revisions``，保留可追溯性。
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional, Sequence

from .service import (
    Viewer,
    _MAX_TITLE,
    _clean_title,
    _normalize_expiry,
    _now,
    expiry_state,
    invalid,
    not_found,
)
from .store import dumps_tags, loads_tags, new_id, open_memory, row_to_dict

MEMORY_SCOPES = ("personal", "project", "employee", "enterprise")
_MAX_CONTENT = 20_000
_MAX_IDENTIFIER = 200


def _clean_optional(value: Any, field: str, limit: int = _MAX_IDENTIFIER) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        raise invalid(f"{field} 过长（上限 {limit} 字符）")
    return text


def _normalize_scope(value: Any, default: str = "personal") -> str:
    scope = str(value or "").strip().lower() or default
    if scope not in MEMORY_SCOPES:
        raise invalid(f"记忆范围只能是 {' / '.join(MEMORY_SCOPES)}")
    return scope


def _can_read(viewer: Viewer, owner: str, scope: str) -> bool:
    if owner == viewer.principal:
        return True
    if scope == "personal":
        return False
    if scope in ("project", "employee"):
        return owner in viewer.owners
    return scope == "enterprise"


def _memory_row(connection: sqlite3.Connection, memory_id: str) -> Dict[str, Any]:
    row = connection.execute(
        "SELECT * FROM lt_memories WHERE id = ?", (memory_id,)
    ).fetchone()
    if row is None:
        raise not_found()
    return dict(row)


def _revision_count(connection: sqlite3.Connection, memory_id: str) -> int:
    row = connection.execute(
        "SELECT COUNT(*) AS n FROM lt_memory_revisions WHERE memory_id = ?",
        (memory_id,),
    ).fetchone()
    return int(row["n"]) if row is not None else 0


def _memory_view(
    connection: sqlite3.Connection, row: Dict[str, Any], viewer: Viewer
) -> Dict[str, Any]:
    state, days = expiry_state(row.get("expires_at"), 0)
    return {
        "id": row["id"],
        "owner_account_id": row["owner"],
        "project_id": row.get("project_id") or "",
        "employee_id": row.get("employee_id") or "",
        "scope": row["scope"],
        "title": row.get("title") or "",
        "content": row["content"],
        "source": row.get("source") or "",
        "source_ref": row.get("source_ref") or "",
        "tags": loads_tags(row.get("tags")),
        "confidence": row.get("confidence"),
        "expires_at": row.get("expires_at"),
        "expiry_state": state,
        "days_left": days,
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "revision": _revision_count(connection, row["id"]),
        "can_write": row["owner"] == viewer.principal,
    }


def create_memory(
    viewer: Viewer,
    *,
    content: str,
    title: str = "",
    project_id: str = "",
    employee_id: str = "",
    scope: str = "personal",
    source: str = "",
    source_ref: str = "",
    tags: Optional[Sequence[str]] = None,
    confidence: Optional[float] = None,
    expires_at: Optional[str] = None,
) -> Dict[str, Any]:
    body = (content or "").strip()
    if not body:
        raise invalid("记忆内容不能为空")
    if len(body) > _MAX_CONTENT:
        raise invalid(f"记忆内容过长（上限 {_MAX_CONTENT} 字符）")
    clean_scope = _normalize_scope(scope)
    clean_project = _clean_optional(project_id, "项目标识")
    clean_employee = _clean_optional(employee_id, "员工标识")
    if clean_scope == "project" and not clean_project:
        raise invalid("project 范围的记忆必须指定 project_id")
    if clean_scope == "employee" and not clean_employee:
        raise invalid("employee 范围的记忆必须指定 employee_id")
    clean_title = str(title or "").strip()[:_MAX_TITLE] if title else body.splitlines()[0][:80]
    if clean_title == "":
        clean_title = "未命名记忆"
    clean_confidence = None
    if confidence is not None:
        try:
            clean_confidence = max(0.0, min(float(confidence), 1.0))
        except (TypeError, ValueError) as exc:
            raise invalid("confidence 必须是 0..1 的数字") from exc
    stamp = _now()
    with open_memory() as connection:
        memory_id = new_id("mem")
        connection.execute(
            "INSERT INTO lt_memories"
            " (id, owner, project_id, employee_id, scope, title, content, source,"
            "  source_ref, tags, confidence, expires_at, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                memory_id,
                viewer.principal,
                clean_project,
                clean_employee,
                clean_scope,
                clean_title,
                body,
                _clean_optional(source, "来源", 300),
                _clean_optional(source_ref, "来源引用", 500),
                dumps_tags(tags),
                clean_confidence,
                _normalize_expiry(expires_at),
                stamp,
                stamp,
            ),
        )
        connection.execute(
            "INSERT INTO lt_memory_revisions"
            " (id, memory_id, revision, title, content, source, tags, edited_by, created_at)"
            " VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?)",
            (
                new_id("rev"),
                memory_id,
                clean_title,
                body,
                _clean_optional(source, "来源", 300),
                dumps_tags(tags),
                viewer.principal,
                stamp,
            ),
        )
        connection.commit()
        return _memory_view(connection, _memory_row(connection, memory_id), viewer)


def get_memory(viewer: Viewer, memory_id: str) -> Dict[str, Any]:
    with open_memory() as connection:
        row = _memory_row(connection, memory_id)
        if row.get("deleted_at") or not _can_read(viewer, row["owner"], row["scope"]):
            raise not_found()
        view = _memory_view(connection, row, viewer)
        view["revisions"] = [
            row_to_dict(item, json_fields=("tags",))
            for item in connection.execute(
                "SELECT * FROM lt_memory_revisions WHERE memory_id = ?"
                " ORDER BY revision DESC",
                (memory_id,),
            ).fetchall()
        ]
        return view


def list_memories(
    viewer: Viewer,
    *,
    project_id: str = "",
    employee_id: str = "",
    scope: str = "",
    q: str = "",
    include_expired: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    clauses = ["m.deleted_at IS NULL"]
    params: Dict[str, Any] = {"viewer": viewer.principal}
    owners = viewer.owners
    owner_slots = []
    for index, owner in enumerate(owners):
        key = f"owner{index}"
        owner_slots.append(f":{key}")
        params[key] = owner
    clauses.append(
        "((m.scope = 'personal' AND m.owner = :viewer)"
        " OR (m.scope IN ('project', 'employee')"
        f" AND m.owner IN ({', '.join(owner_slots)}))"
        " OR m.scope = 'enterprise')"
    )
    if project_id:
        clauses.append("m.project_id = :project")
        params["project"] = project_id
    if employee_id:
        clauses.append("m.employee_id = :employee")
        params["employee"] = employee_id
    if scope:
        clauses.append("m.scope = :scope")
        params["scope"] = _normalize_scope(scope)
    if not include_expired:
        clauses.append("(m.expires_at IS NULL OR m.expires_at = '' OR m.expires_at >= :now)")
        params["now"] = _now()
    if q.strip():
        clauses.append("(m.title LIKE :kw ESCAPE '\\' OR m.content LIKE :kw ESCAPE '\\')")
        keyword = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params["kw"] = f"%{keyword}%"
    where = " AND ".join(clauses)
    with open_memory() as connection:
        total = connection.execute(
            f"SELECT COUNT(*) AS n FROM lt_memories m WHERE {where}", params
        ).fetchone()["n"]
        rows = connection.execute(
            f"SELECT m.* FROM lt_memories m WHERE {where}"
            " ORDER BY m.updated_at DESC LIMIT :limit OFFSET :offset",
            {**params, "limit": limit, "offset": offset},
        ).fetchall()
        items = [_memory_view(connection, dict(row), viewer) for row in rows]
    return {
        "memories": items,
        "total": int(total),
        "limit": limit,
        "offset": offset,
        "filters": {
            "project_id": project_id,
            "employee_id": employee_id,
            "scope": scope,
            "q": q,
        },
    }


def update_memory(
    viewer: Viewer,
    memory_id: str,
    *,
    content: Optional[str] = None,
    title: Optional[str] = None,
    project_id: Optional[str] = None,
    employee_id: Optional[str] = None,
    scope: Optional[str] = None,
    source: Optional[str] = None,
    source_ref: Optional[str] = None,
    tags: Optional[Sequence[str]] = None,
    confidence: Optional[float] = None,
    expires_at: Optional[str] = None,
    clear_expiry: bool = False,
) -> Dict[str, Any]:
    with open_memory() as connection:
        row = _memory_row(connection, memory_id)
        if row.get("deleted_at") or row["owner"] != viewer.principal:
            raise not_found()
        updates: List[str] = []
        params: List[Any] = []
        if content is not None:
            body = content.strip()
            if not body:
                raise invalid("记忆内容不能为空")
            if len(body) > _MAX_CONTENT:
                raise invalid(f"记忆内容过长（上限 {_MAX_CONTENT} 字符）")
            updates.append("content = ?")
            params.append(body)
        if title is not None:
            updates.append("title = ?")
            params.append(_clean_title(title, row.get("title") or "未命名记忆"))
        if project_id is not None:
            updates.append("project_id = ?")
            params.append(_clean_optional(project_id, "项目标识"))
        if employee_id is not None:
            updates.append("employee_id = ?")
            params.append(_clean_optional(employee_id, "员工标识"))
        if scope is not None:
            updates.append("scope = ?")
            params.append(_normalize_scope(scope, row["scope"]))
        if source is not None:
            updates.append("source = ?")
            params.append(_clean_optional(source, "来源", 300))
        if source_ref is not None:
            updates.append("source_ref = ?")
            params.append(_clean_optional(source_ref, "来源引用", 500))
        if tags is not None:
            updates.append("tags = ?")
            params.append(dumps_tags(tags))
        if confidence is not None:
            try:
                updates.append("confidence = ?")
                params.append(max(0.0, min(float(confidence), 1.0)))
            except (TypeError, ValueError) as exc:
                raise invalid("confidence 必须是 0..1 的数字") from exc
        if clear_expiry:
            updates.append("expires_at = NULL")
        elif expires_at is not None:
            updates.append("expires_at = ?")
            params.append(_normalize_expiry(expires_at))
        if not updates:
            raise invalid("没有需要更新的字段")
        effective_scope = scope if scope is not None else row["scope"]
        effective_project = project_id if project_id is not None else row.get("project_id") or ""
        effective_employee = (
            employee_id if employee_id is not None else row.get("employee_id") or ""
        )
        if effective_scope == "project" and not str(effective_project).strip():
            raise invalid("project 范围的记忆必须指定 project_id")
        if effective_scope == "employee" and not str(effective_employee).strip():
            raise invalid("employee 范围的记忆必须指定 employee_id")
        stamp = _now()
        updates.append("updated_at = ?")
        params.append(stamp)
        params.append(memory_id)
        connection.execute(
            f"UPDATE lt_memories SET {', '.join(updates)} WHERE id = ?", params
        )
        fresh = _memory_row(connection, memory_id)
        revision = _revision_count(connection, memory_id) + 1
        connection.execute(
            "INSERT INTO lt_memory_revisions"
            " (id, memory_id, revision, title, content, source, tags, edited_by, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                new_id("rev"),
                memory_id,
                revision,
                fresh.get("title") or "",
                fresh["content"],
                fresh.get("source") or "",
                fresh.get("tags") or "[]",
                viewer.principal,
                stamp,
            ),
        )
        connection.commit()
        return _memory_view(connection, fresh, viewer)


def delete_memory(viewer: Viewer, memory_id: str) -> Dict[str, Any]:
    with open_memory() as connection:
        row = _memory_row(connection, memory_id)
        if row.get("deleted_at") or row["owner"] != viewer.principal:
            raise not_found()
        stamp = _now()
        connection.execute(
            "UPDATE lt_memories SET deleted_at = ?, updated_at = ? WHERE id = ?",
            (stamp, stamp, memory_id),
        )
        connection.commit()
        return {"deleted": True, "id": memory_id, "deleted_at": stamp}


def memory_views(viewer: Viewer) -> Dict[str, Any]:
    """按项目、按员工聚合的隔离视图（只统计当前账号可见的记忆）。"""
    with open_memory() as connection:
        where, params = _memory_visibility_clause(viewer)
        projects = [
            dict(row)
            for row in connection.execute(
                "SELECT m.project_id AS project_id, COUNT(*) AS count,"
                " MAX(m.updated_at) AS updated_at"
                f" FROM lt_memories m WHERE {where} AND m.project_id <> ''"
                " GROUP BY m.project_id ORDER BY updated_at DESC",
                params,
            ).fetchall()
        ]
        employees = [
            dict(row)
            for row in connection.execute(
                "SELECT m.employee_id AS employee_id, COUNT(*) AS count,"
                " MAX(m.updated_at) AS updated_at"
                f" FROM lt_memories m WHERE {where} AND m.employee_id <> ''"
                " GROUP BY m.employee_id ORDER BY updated_at DESC",
                params,
            ).fetchall()
        ]
        scopes = {
            row["scope"]: int(row["count"])
            for row in connection.execute(
                f"SELECT m.scope AS scope, COUNT(*) AS count FROM lt_memories m"
                f" WHERE {where} GROUP BY m.scope",
                params,
            ).fetchall()
        }
        total = connection.execute(
            f"SELECT COUNT(*) AS n FROM lt_memories m WHERE {where}", params
        ).fetchone()["n"]
    return {
        "projects": projects,
        "employees": employees,
        "scopes": {scope: scopes.get(scope, 0) for scope in MEMORY_SCOPES},
        "total": int(total),
        "store": "knowledge_memory.sqlite3",
        "isolation": "独立数据库；personal 仅本人，project/employee 对祖先账号只读，write 仅记录者",
    }


def _memory_visibility_clause(viewer: Viewer) -> tuple[str, Dict[str, Any]]:
    params: Dict[str, Any] = {"viewer": viewer.principal}
    owner_slots = []
    for index, owner in enumerate(viewer.owners):
        key = f"owner{index}"
        owner_slots.append(f":{key}")
        params[key] = owner
    where = (
        "m.deleted_at IS NULL AND"
        " ((m.scope = 'personal' AND m.owner = :viewer)"
        " OR (m.scope IN ('project', 'employee')"
        f" AND m.owner IN ({', '.join(owner_slots)}))"
        " OR m.scope = 'enterprise')"
    )
    return where, params


__all__ = [
    "MEMORY_SCOPES",
    "create_memory",
    "delete_memory",
    "get_memory",
    "list_memories",
    "memory_views",
    "update_memory",
]
