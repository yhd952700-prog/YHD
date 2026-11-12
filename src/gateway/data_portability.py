"""数据可携带性：人可读导出、一键带走、记忆/知识库回导。

为什么单独一层
--------------
``account_backup`` 解决的是"换机整树认领"：口令加密、含身份与 Key 密文、
只能在全新实例导入。普通用户还有另一个更朴素的需求 —— **把我的东西导出成
能直接打开的文件**（Markdown / CSV / JSON），以及把导出的记忆、知识库再
导回来。

本模块只做这件事，边界写死：

* 只导出**令牌主体自己的**数据；根账号也不会因为"能看整棵树"就在这里
  带走成员的会话/记忆/员工/知识库（那是加密备份的职责，并且有二次确认）；
* 不导出任何凭据、模型 Key、密文主密钥、登录令牌、内部绝对路径；
* 会话导出是**对话文本**（带时间），不是数据库行转储；
* 记忆与知识库的导入只写当前账号，按内容去重，不覆盖现场数据；
* 所有导出/导入写审计，只记条数与格式，不记内容。
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import re
import uuid
import zipfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from ..kernels.identity import _accounts, get_identity_manager
from .account_context import AccountContext, deny, require_account_principal
from .display import UserReadableHTTPException

logger = logging.getLogger(__name__)

#: 网关启动时自带的种子员工：属于基础设施，不属于"我的数据"。
_SEED_EMPLOYEE_NAME = "liuhao-default"

accounts_router = APIRouter(prefix="/v1/accounts", tags=["accounts"])
chat_router = APIRouter(prefix="/v1/chat", tags=["chat"])
knowledge_router = APIRouter(prefix="/v1/knowledge", tags=["knowledge"])
memory_router = APIRouter(prefix="/v1/memory", tags=["memory"])

#: 单文件与工作区产物的体积上限：一键导出宁可跳过并如实写进清单，也不失败。
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_FILES_BYTES = 64 * 1024 * 1024

_SAFE_NAME = re.compile(r"[^0-9A-Za-z_\u4e00-\u9fff.-]+")
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def _now_iso() -> str:
    from src._time import utc_now

    return utc_now().isoformat()


def _iso_from_epoch(value: Any) -> str:
    try:
        stamp = float(value or 0)
    except (TypeError, ValueError):
        return ""
    if stamp <= 0:
        return ""
    return datetime.fromtimestamp(stamp, timezone.utc).replace(tzinfo=None).isoformat()


def _safe_name(value: Any, fallback: str = "item") -> str:
    name = _SAFE_NAME.sub("-", str(value or "").strip()).strip(".-")
    name = name[:120] or fallback
    if name.split(".")[0].upper() in _WINDOWS_RESERVED:
        name = f"_{name}"
    return name


def _csv_cell(value: Any) -> Any:
    """防 CSV 公式注入：以 = + - @ 开头的文本在 Excel 里会被当公式执行。"""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


# --------------------------------------------------------------------------- #
# 会话：人可读 Markdown
# --------------------------------------------------------------------------- #

def _conversation_markdown(session_id: str, turns: List[Dict[str, Any]]) -> str:
    lines = [
        f"# 会话：{session_id}",
        "",
        f"- 导出时间（UTC）：{_now_iso()}",
        f"- 消息数：{len(turns)}",
        "",
    ]
    for item in turns:
        role = str(item.get("role") or "")
        label = "我" if role == "user" else "贾维斯"
        stamp = _iso_from_epoch(item.get("created_at"))
        lines.append(f"## {label}" + (f" · {stamp}" if stamp else ""))
        lines.append("")
        lines.append(str(item.get("content") or ""))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _load_own_session(session_id: str, principal: str) -> List[Dict[str, Any]]:
    from .chat import _history_principal_candidates

    store = _conversation_store()
    for candidate in _history_principal_candidates(principal, session_id):
        if store.has_rows(candidate):
            return store.load_turns(candidate)
    return []


def _conversation_store():
    from src.ai.conversation_store import get_conversation_store

    return get_conversation_store()


def collect_conversations(context: AccountContext) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """返回 ``([{session_id, markdown}], manifest)``；只含当前账号自己的会话。"""
    from .chat import list_sessions

    try:
        sessions = list_sessions(
            context.principal,
            known_principals=list(context.subtree),
            allow_legacy=context.is_root,
        )
    except Exception:  # noqa: BLE001 - 列表失败时至少导出空清单，不阻断其它数据
        sessions = []
    files: List[Dict[str, Any]] = []
    total = 0
    for session_id in sessions:
        turns = _load_own_session(session_id, context.principal)
        if not turns:
            continue
        files.append(
            {
                "session_id": session_id,
                "markdown": _conversation_markdown(session_id, turns),
            }
        )
        total += len(turns)
    return files, {"sessions": len(files), "messages": total}


def collect_memories(principal: str) -> List[Dict[str, Any]]:
    from .account_memory import _query_entries

    return _query_entries((principal,), "")


def _memory_markdown(principal: str, entries: List[Dict[str, Any]]) -> str:
    lines = [f"# 我的记忆（{principal}）", "", f"- 导出时间（UTC）：{_now_iso()}",
             f"- 条数：{len(entries)}", ""]
    for entry in entries:
        lines.append(f"## {entry.get('created_at') or '时间未知'}")
        detail = []
        if entry.get("source"):
            detail.append(f"来源：{entry['source']}")
        if entry.get("tags"):
            detail.append("标签：" + "、".join(str(tag) for tag in entry["tags"]))
        if detail:
            lines.append(" · ".join(detail))
            lines.append("")
        lines.append(str(entry.get("text") or ""))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def collect_employees(context: AccountContext) -> List[Dict[str, Any]]:
    """只保留 ``owner_account_id == 本人`` 的员工（根也看不到成员员工）。"""
    try:
        from .ai_management import AIStateManager, GoalViewer

        viewer = GoalViewer(
            principal=context.principal,
            subtree=(context.principal,),
            is_root=False,
            standalone=context.standalone,
        )
        rows = AIStateManager().list_all_employees(viewer)
    except Exception:  # noqa: BLE001 - 员工库损坏不应让整包导出失败
        return []
    return [
        row for row in rows
        if str(row.get("owner_account_id") or context.principal) == context.principal
        # ``liuhao-default`` 是网关自带的种子员工，不是用户数据；放进个人
        # 归档只会让用户以为"我雇了一个默认员工"。
        and str(row.get("name") or "") != _SEED_EMPLOYEE_NAME
    ]


def collect_goals(context: AccountContext) -> List[Dict[str, Any]]:
    """只导出**本人**的目标（根账号在这里也只带自己的，不聚合成员）。"""
    try:
        from .ai_management import AIStateManager, GoalViewer

        viewer = GoalViewer(
            principal=context.principal,
            subtree=(context.principal,),
            is_root=False,
            standalone=context.standalone,
        )
        rows = AIStateManager().list_goals(viewer)
    except Exception:  # noqa: BLE001 - 目标库损坏不应让整包导出失败
        return []
    return [
        row for row in rows
        if str(row.get("owner_account_id") or context.principal) == context.principal
    ]


def _employees_markdown(employees: List[Dict[str, Any]]) -> str:
    lines = [
        "# 我的员工",
        "",
        f"- 导出时间（UTC）：{_now_iso()}",
        f"- 员工数：{len(employees)}",
        "",
    ]
    if not employees:
        lines.append("（当前账号名下没有员工）")
    for row in employees:
        lines += [
            f"## {row.get('display_name') or row.get('name') or '未命名员工'}",
            "",
            f"- 标识：{row.get('name') or ''}",
            f"- 角色：{row.get('role_description') or '（未填写）'}",
            f"- 状态：{row.get('status_label') or row.get('status') or ''}",
            f"- 代理数：{row.get('agent_count') or 0}",
            f"- 累计任务：{((row.get('stats') or {}).get('total_tasks_completed') or 0)} 完成 / "
            f"{((row.get('stats') or {}).get('total_tasks_failed') or 0)} 失败",
            f"- 能力：{'、'.join(str(c) for c in (row.get('default_capabilities') or [])) or '（未设置）'}",
            "",
        ]
    return "\n".join(lines).rstrip() + "\n"


def _goals_markdown(goals: List[Dict[str, Any]]) -> str:
    lines = [
        "# 我的目标",
        "",
        f"- 导出时间（UTC）：{_now_iso()}",
        f"- 目标数：{len(goals)}",
        "",
    ]
    if not goals:
        lines.append("（当前账号名下没有目标）")
    for row in goals:
        lines += [
            f"## {str(row.get('natural_language') or '').strip() or row.get('goal_id') or '未命名目标'}",
            "",
            f"- 目标 id：{row.get('goal_id') or ''}",
            f"- 状态：{row.get('state') or ''}",
            f"- 创建时间：{row.get('created_at') or ''}",
            f"- 任务：{row.get('task_count') or 0} 个，"
            f"完成 {row.get('completed_tasks') or 0}，失败 {row.get('failed_tasks') or 0}",
        ]
        if row.get("error"):
            lines.append(f"- 失败原因：{row['error']}")
        if row.get("assigned_employee"):
            lines.append(f"- 负责员工：{row['assigned_employee']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def collect_usage(principal: str) -> Dict[str, Any]:
    from .account_usage import usage_summary

    return usage_summary(principal, include_callers=False, owners=(principal,))


def collect_account_settings(principal: str) -> Dict[str, Any]:
    """账号侧真实存在的设置；界面偏好存在浏览器里，这里如实说明。"""
    custody: Dict[str, Any] = {"enabled": False, "updated_at": ""}
    try:
        from .account_memory import _connect

        with _connect() as connection:
            row = connection.execute(
                "SELECT enabled, updated_at FROM account_memory_custody WHERE owner = ?",
                (principal,),
            ).fetchone()
        if row is not None:
            custody = {"enabled": bool(row["enabled"]), "updated_at": row["updated_at"] or ""}
    except Exception:  # noqa: BLE001 - 设置缺失不应阻断导出
        pass
    return {
        "note": "这里只有账号标识与服务端真实保存的设置；主题等界面偏好保存在浏览器 localStorage。",
        "account": {"principal": principal},
        "memory_custody": custody,
    }


def _usage_csv(summary: Dict[str, Any]) -> str:
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["类型", "Key 归属", "调用次数", "输入 tokens", "输出 tokens", "总 tokens"])
    for row in summary.get("by_key_owner") or []:
        writer.writerow([
            "按 Key 归属",
            _csv_cell(row.get("key_owner") or "（无归属）"),
            row.get("calls", 0),
            row.get("tokens_in", 0),
            row.get("tokens_out", 0),
            row.get("total_tokens", 0),
        ])
    writer.writerow([
        "合计",
        _csv_cell(summary.get("principal") or ""),
        summary.get("calls", 0),
        summary.get("tokens_in", 0),
        summary.get("tokens_out", 0),
        summary.get("total_tokens", 0),
    ])
    return stream.getvalue()


# --------------------------------------------------------------------------- #
# 知识库：导出 / 回导
# --------------------------------------------------------------------------- #

def _knowledge_scope(context: AccountContext):
    """备份导出的知识库范围：主账号覆盖整棵子树（核心能力），成员只导出自己。

    日常读接口（列表/检索/分块）按"知识库是账号自有资产"只给本人；但备份
    导出是主账号的整树数据迁移能力（``docs/account-hierarchy.md`` 第 21 条），
    两者边界不同，因此这里不复用路由侧的读范围。
    """
    from src.knowledge.documents import KnowledgeScope

    if context.standalone:
        return KnowledgeScope(
            principal=context.principal,
            owners=(context.principal,),
            include_unowned=True,
            owns_legacy=True,
            unowned_writes=True,
        )
    if context.is_member:
        return KnowledgeScope(
            principal=context.principal,
            owners=(context.principal,),
            include_unowned=False,
            owns_legacy=False,
        )
    return KnowledgeScope(
        principal=context.principal,
        owners=tuple(context.subtree),
        include_unowned=True,
        owns_legacy=True,
    )


def _strip_owner_prefix(document_id: str, principal: str) -> str:
    from src.knowledge.documents import _owner_token

    raw = str(document_id or "")
    prefix = f"{_owner_token(principal)}--"
    return raw[len(prefix):] if raw.startswith(prefix) else raw


def export_knowledge(
    context: AccountContext, service: Optional[Any] = None
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """把当前账号的文档还原成**文本**（chunk 拼接），可再次 ingest。"""
    if service is None:
        from src.knowledge.documents import get_knowledge_service

        service = get_knowledge_service()
    scope = _knowledge_scope(context)
    documents: List[Dict[str, Any]] = []
    offset = 0
    while True:
        page = service.list_documents(limit=200, offset=offset, scope=scope)
        rows = page.get("documents") or []
        total = int(page.get("total") or 0)
        for row in rows:
            document_id = str(row.get("document_id") or "")
            if not document_id:
                continue
            chunks = service.document_chunks(document_id, scope=scope).get("chunks") or []
            texts = [str(chunk.get("text") or "") for chunk in chunks]
            text = "\n\n".join(part for part in texts if part.strip())
            documents.append(
                {
                    "document_id": _strip_owner_prefix(document_id, context.principal),
                    "title": str(row.get("title") or ""),
                    "source": str(row.get("source") or ""),
                    "chunks": len(chunks),
                    "text": text,
                }
            )
        offset += len(rows)
        if not rows or offset >= total:
            break
    return documents, {"documents": len(documents)}


def _knowledge_markdown(doc: Dict[str, Any]) -> str:
    lines = [
        f"# {doc.get('title') or doc.get('document_id') or '未命名文档'}",
        "",
        f"- 文档 id：{doc.get('document_id') or ''}",
        f"- 来源：{doc.get('source') or ''}",
        f"- 分块数：{doc.get('chunks') or 0}",
        f"- 导出时间（UTC）：{_now_iso()}",
        "",
        str(doc.get("text") or ""),
    ]
    return "\n".join(lines).rstrip() + "\n"


class KnowledgeImportRequest(BaseModel):
    documents: List[Dict[str, Any]] = Field(default_factory=list)


def import_knowledge(
    context: AccountContext,
    documents: List[Dict[str, Any]],
    service: Optional[Any] = None,
) -> Dict[str, Any]:
    """把导出 JSON 里的文档重新 ingest 到**当前账号**，按内容去重。"""
    from src.knowledge.embedding import EmbeddingUnavailableError

    if not documents:
        raise deny(400, "KNOWLEDGE_IMPORT_EMPTY", "没有可导入的文档")
    if service is None:
        from src.knowledge.documents import get_knowledge_service

        service = get_knowledge_service()
    status = service.status()
    if not status.get("enabled"):
        # 原因由知识库服务给出（如"没有可用的 embedding provider，请启动
        # Ollama…"），是照着做就能恢复的运维文案；安全闸仍会兜住路径/SQL。
        raise UserReadableHTTPException(
            status_code=503,
            detail=f"{status.get('reason') or '知识库不可用'}",
        )
    scope = _knowledge_scope(context)
    imported = 0
    deduplicated = 0
    skipped = 0
    errors: List[str] = []
    for index, doc in enumerate(documents):
        if not isinstance(doc, dict):
            skipped += 1
            continue
        text = str(doc.get("text") or "").strip()
        if not text:
            skipped += 1
            continue
        doc_id = str(doc.get("document_id") or f"import-{index + 1}")
        try:
            outcome = service.ingest(
                text,
                doc_id=_strip_owner_prefix(doc_id, context.principal),
                metadata={
                    "source": str(doc.get("source") or "import"),
                    "title": str(doc.get("title") or ""),
                    "imported_from": "liuhao.knowledge.export.v1",
                },
                owner_account_id=scope.write_owner,
            )
        except EmbeddingUnavailableError as exc:
            logger.warning("knowledge import: embedding unavailable: %s", exc)
            # 可操作：等恢复后重试即可，不含内部细节 → 允许原样透传。
            raise UserReadableHTTPException(
                status_code=503, detail="知识库暂时不可用，请稍后重试。"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - 单篇失败不拖垮整批
            logger.warning("knowledge import failed for %s: %s", doc_id, exc)
            errors.append(f"{doc_id}: 导入失败")
            skipped += 1
            continue
        if outcome.get("deduplicated"):
            deduplicated += 1
        else:
            imported += 1
    get_identity_manager().record_event(
        action="knowledge.import",
        actor=context.principal,
        target=context.principal,
        outcome="allowed",
        details={
            "submitted": len(documents),
            "imported": imported,
            "deduplicated": deduplicated,
            "skipped": skipped,
            "errors": len(errors),
        },
    )
    return {
        "ok": True,
        "submitted": len(documents),
        "imported": imported,
        "deduplicated": deduplicated,
        "skipped": skipped,
        "errors": errors,
    }


# --------------------------------------------------------------------------- #
# 记忆：回导
# --------------------------------------------------------------------------- #

class MemoryImportRequest(BaseModel):
    entries: List[Dict[str, Any]] = Field(default_factory=list)


def import_memories(context: AccountContext, entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """把导出 JSON 的记忆条目写回**当前账号**；按（正文, 来源, 时间）去重。"""
    from .account_memory import _connect

    if not entries:
        raise deny(400, "MEMORY_IMPORT_EMPTY", "没有可导入的记忆")
    imported = 0
    skipped = 0
    with _connect() as connection:
        for row in entries:
            if not isinstance(row, dict):
                skipped += 1
                continue
            text = str(row.get("text") or "").strip()
            if not text:
                skipped += 1
            source = str(row.get("source") or "import")[:64]
            created_at = str(row.get("created_at") or "").strip() or _now_iso()
            existing = connection.execute(
                "SELECT 1 FROM account_memory_entries "
                "WHERE owner = ? AND text = ? AND source = ? AND created_at = ? LIMIT 1",
                (context.principal, text, source, created_at),
            ).fetchone()
            if existing is not None:
                skipped += 1
                continue
            tags = row.get("tags") if isinstance(row.get("tags"), list) else []
            connection.execute(
                "INSERT INTO account_memory_entries "
                "(id, owner, text, source, tags, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    context.principal,
                    text,
                    source,
                    json.dumps([str(tag) for tag in tags], ensure_ascii=False),
                    created_at,
                ),
            )
            imported += 1
        connection.commit()
    get_identity_manager().record_event(
        action="memory.import",
        actor=context.principal,
        target=context.principal,
        outcome="allowed",
        details={"submitted": len(entries), "imported": imported, "skipped": skipped},
    )
    return {"ok": True, "submitted": len(entries), "imported": imported, "skipped": skipped}


# --------------------------------------------------------------------------- #
# 工作区产物（只带自己名下的文件）
# --------------------------------------------------------------------------- #

def collect_workspace_files(context: AccountContext) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    from src.ai.workspace import workspace_root
    from .ai_management import AIStateManager

    root = workspace_root()
    index = AIStateManager().artifact_owner_index()
    entries: List[Dict[str, Any]] = []
    skipped: List[str] = []
    protected: List[str] = []
    total = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            name for name in dirnames
            if name not in (".git", "__pycache__", "node_modules")
        ]
        for name in filenames:
            absolute = os.path.join(dirpath, name)
            rel = str(os.path.relpath(absolute, root)).replace("\\", "/").strip("/")
            if _looks_like_credential(name):
                # 人可读导出不含任何凭据：哪怕文件落在用户产物目录里也跳过。
                protected.append(rel)
                continue
            owners = set(index.get(rel, set()))
            # 公共文件和他人的产物一律不带走（一键导出的是"我的数据"）。
            if context.principal not in owners:
                continue
            try:
                stat = os.stat(absolute)
            except OSError:
                skipped.append(rel)
                continue
            if stat.st_size > MAX_FILE_BYTES:
                skipped.append(rel)
                continue
            if total + stat.st_size > MAX_FILES_BYTES:
                skipped.append(rel)
                continue
            try:
                with open(absolute, "rb") as handle:
                    raw = handle.read()
            except OSError:
                skipped.append(rel)
                continue
            entries.append({"path": rel, "size": len(raw), "content": raw})
            total += len(raw)
    return entries, {
        "files": len(entries),
        "bytes": total,
        "skipped": skipped,
        "skipped_credentials": protected,
    }


_CREDENTIAL_NAME_HINTS = (
    ".env", "auth_secrets", "recovery_code", "recovery_handoff",
    "model_config_key", "id_rsa", "id_ed25519", ".pem", ".pfx", ".p12",
    ".key", ".kdbx",
)


def _looks_like_credential(name: str) -> bool:
    """按文件名识别凭据：人可读导出绝不带 Key、口令或恢复码。"""
    lowered = str(name or "").lower()
    if lowered.startswith(".env") or lowered.endswith(".env"):
        return True
    return any(hint in lowered for hint in _CREDENTIAL_NAME_HINTS)


# --------------------------------------------------------------------------- #
# 一键导出 ZIP
# --------------------------------------------------------------------------- #

class ExportAllRequest(BaseModel):
    include_files: bool = Field(True, description="是否带上自己名下的工作区产物")


def build_export_archive(
    context: AccountContext,
    *,
    include_files: bool = True,
    knowledge_service: Optional[Any] = None,
) -> Tuple[bytes, Dict[str, Any]]:
    """组装 ZIP，返回 ``(bytes, manifest)``。所有成员按账号过滤并如实计数。"""
    principal = context.principal
    conversations, conversation_manifest = collect_conversations(context)
    memories = collect_memories(principal)
    goals = collect_goals(context)
    employees = collect_employees(context)
    usage = collect_usage(principal)
    knowledge: List[Dict[str, Any]] = []
    knowledge_status = "ok"
    try:
        knowledge, _ = export_knowledge(context, service=knowledge_service)
    except Exception:  # noqa: BLE001 - 知识库未启用/向量库不可用时不阻断整包
        knowledge_status = "unavailable"
    files, file_manifest = (
        collect_workspace_files(context) if include_files else ([], {"files": 0, "bytes": 0, "skipped": []})
    )

    manifest = {
        "format": "liuhao.personal-data.export.v1",
        "exported_at": _now_iso(),
        "principal": principal,
        "privacy": {
            "other_accounts_data": False,
            "credentials_or_keys": False,
            "recovery_codes": False,
            "absolute_paths": False,
            "login_tokens": False,
            "sqlite_files": False,
        },
        "contents": {
            "conversations": conversation_manifest,
            "memories": len(memories),
            "goals": len(goals),
            "employees": len(employees),
            "knowledge": len(knowledge),
            "knowledge_status": knowledge_status,
            "usage_calls": usage.get("calls", 0),
            "files": file_manifest,
        },
        # 明确双向边界：只有记忆与知识库能导回；其余是人可读归档。
        "import_supported": {
            "memories": {"endpoint": "POST /v1/memory/import", "format": "json"},
            "knowledge": {"endpoint": "POST /v1/knowledge/import", "format": "json"},
        },
        "read_only_archive": [
            "conversations",
            "goals",
            "employees",
            "usage",
            "settings",
            "files",
        ],
        "migration_hint": (
            "想完整搬到另一台机器（会话、目标、员工、模型 Key）请用命令行加密备份："
            "python -m src.cli backup create --encrypt --password \"你的口令\"；"
            "这个 ZIP 只用于阅读与回导记忆/知识库。"
        ),
        "not_included": [
            "别人的会话、记忆、目标、员工或知识库",
            "模型 Key、登录令牌、恢复码与任何凭据",
            "本机安装路径与内部数据库文件",
        ],
    }

    readme = "\n".join([
        "# 我的数据导出",
        "",
        f"- 账号：{principal}",
        f"- 导出时间（UTC）：{manifest['exported_at']}",
        "- 本目录是人可读归档：直接双击 Markdown 就能看，不需要任何程序。",
        "",
        "> **想完整搬到另一台机器？请用命令行加密备份，而不是这个 ZIP。**",
        ">",
        "> 这个 ZIP 只用于阅读与回导记忆/知识库；会话、目标、员工清单、模型 Key",
        "> 不会随它迁移。完整换机请执行：",
        ">",
        "> `python -m src.cli backup create --encrypt --password \"你的口令\"`",
        ">",
        "> 然后把生成的加密备份文件拷到新机器导入（图形界面：系统设置 → 备份与迁移）。",
        "",
        "## 包含什么",
        "",
        f"- 会话：{conversation_manifest['sessions']} 个，共 {conversation_manifest['messages']} 条消息"
        "（conversations/*.md，每段对话带时间）",
        f"- 记忆：{len(memories)} 条（memories.md / memories.json）",
        f"- 目标：{len(goals)} 个（goals.md / goals.json）",
        f"- 员工：{len(employees)} 个（employees.md / employees.json，只含你自己的）",
        f"- 知识库：{len(knowledge)} 篇"
        + ("（knowledge/*.md 供阅读；knowledge.json 可回导）"
           if knowledge_status == "ok" else "（知识库不可用，本次未包含）"),
        "- 用量：usage.csv / usage.json（按 Key 归属汇总）",
        f"- 工作区产物：{file_manifest['files']} 个（files/ 目录，只含归属你的文件）",
        "- 每个文件是什么：见 INDEX.md（索引页）。",
        "",
        "## 不包含什么",
        "",
        "- 其他成员的会话、记忆、目标、员工、知识库与文件；",
        "- 任何模型 Key、登录令牌、恢复码或凭据（连疑似凭据的产物文件也会被跳过）；",
        "- 内部数据库（.sqlite/.db）与安装路径。",
        "",
        "## 能不能导回来：只有两项可以",
        "",
        "| 数据 | 能否导回 | 怎么做 |",
        "| ---- | -------- | ------ |",
        "| 记忆 | 可以 | 把 memories.json 提交到 `POST /v1/memory/import` |",
        "| 知识库 | 可以 | 把 knowledge.json 提交到 `POST /v1/knowledge/import`（需已配置向量化模型） |",
        "| 会话 | 只读归档 | 给人读的 Markdown；完整换机请用命令行加密备份（见页首） |",
        "| 目标 | 只读归档 | 同上：命令行 `backup create --encrypt` |",
        "| 员工清单 | 只读归档 | 同上（在新机上重新雇佣即可，不必手工搬） |",
        "| 工作区文件 | 只读归档 | 需要时自己拷回工作区 |",
        "",
        "重复导入不会产生副本：记忆按（正文/来源/时间）去重，知识库按内容哈希去重。",
        "",
    ])

    index_lines = [
        "# 索引",
        "",
        "本页说明目录里每一份文件是什么。Markdown 供人阅读，JSON 供程序或以后导入使用。",
        "",
        "| 文件 | 是什么 |",
        "| ---- | ------ |",
        "| README.md | 这份导出的说明：包含什么、不包含什么、哪些能导回 |",
        "| INDEX.md | 本页：文件清单与用途 |",
        "| manifest.json | 机器可读清单：条数、隐私边界、回导支持范围 |",
        f"| memories.md | 我的记忆，人可读（{len(memories)} 条） |",
        f"| memories.json | 我的记忆，可回导（{len(memories)} 条） |",
        f"| goals.md | 我的目标（{len(goals)} 个） |",
        f"| goals.json | 我的目标，机器可读（{len(goals)} 个） |",
        f"| employees.md | 我的员工清单（{len(employees)} 个） |",
        f"| employees.json | 员工实体明细（{len(employees)} 个），只读归档 |",
        "| usage.csv | 用量汇总（Excel 可直接打开） |",
        "| usage.json | 用量汇总，机器可读 |",
        "| settings.json | 账号侧真实保存的设置（界面偏好存在浏览器里） |",
    ]
    if conversations:
        index_lines.append(
            f"| conversations/index.json | 会话清单（{conversation_manifest['sessions']} 个） |"
        )
        for number, item in enumerate(conversations, start=1):
            name = _safe_name(item["session_id"], f"session-{number}")
            index_lines.append(
                f"| conversations/{number:03d}-{name}.md | 会话「{item['session_id']}」完整对话 |"
            )
    if knowledge:
        index_lines.append(f"| knowledge.json | 知识库文档全文，可回导（{len(knowledge)} 篇） |")
        for number, doc in enumerate(knowledge, start=1):
            name = _safe_name(doc.get("title") or doc.get("document_id"), f"document-{number}")
            index_lines.append(
                f"| knowledge/{number:03d}-{name}.md | 知识库文档「{doc.get('title') or name}」 |"
            )
    if file_manifest["files"]:
        index_lines.append(
            f"| files/ | 你名下的工作区产物（{file_manifest['files']} 个，按原相对路径存放） |"
        )
    index_lines.append("")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("README.md", readme.encode("utf-8"))
        archive.writestr("INDEX.md", "\n".join(index_lines).encode("utf-8"))
        archive.writestr("manifest.json", _json_bytes(manifest))
        for index, item in enumerate(conversations, start=1):
            name = _safe_name(item["session_id"], f"session-{index}")
            archive.writestr(
                f"conversations/{index:03d}-{name}.md",
                item["markdown"].encode("utf-8"),
            )
        archive.writestr(
            "conversations/index.json",
            _json_bytes([{"session_id": item["session_id"]} for item in conversations]),
        )
        archive.writestr(
            "memories.json",
            _json_bytes({"format": "liuhao.memory.export.v1", "principal": principal, "entries": memories}),
        )
        archive.writestr("memories.md", _memory_markdown(principal, memories).encode("utf-8"))
        archive.writestr(
            "goals.json",
            _json_bytes({"format": "liuhao.goals.export.v1", "principal": principal, "goals": goals}),
        )
        archive.writestr("goals.md", _goals_markdown(goals).encode("utf-8"))
        archive.writestr("employees.json", _json_bytes({"employees": employees}))
        archive.writestr("employees.md", _employees_markdown(employees).encode("utf-8"))
        archive.writestr(
            "knowledge.json",
            _json_bytes({"format": "liuhao.knowledge.export.v1", "principal": principal, "documents": knowledge}),
        )
        for index, doc in enumerate(knowledge, start=1):
            name = _safe_name(doc.get("title") or doc.get("document_id"), f"document-{index}")
            archive.writestr(f"knowledge/{index:03d}-{name}.md", _knowledge_markdown(doc).encode("utf-8"))
        archive.writestr("usage.csv", _usage_csv(usage).encode("utf-8"))
        archive.writestr("usage.json", _json_bytes(usage))
        archive.writestr(
            "settings.json",
            _json_bytes(collect_account_settings(principal)),
        )
        for entry in files:
            archive.writestr(f"files/{entry['path']}", entry["content"])
    get_identity_manager().record_event(
        action="account.data.export",
        actor=principal,
        target=principal,
        outcome="allowed",
        details={
            "format": "zip",
            "conversations": conversation_manifest["messages"],
            "memories": len(memories),
            "goals": len(goals),
            "employees": len(employees),
            "knowledge": len(knowledge),
            "files": file_manifest["files"],
            "skipped_credentials": len(file_manifest.get("skipped_credentials") or []),
            "include_files": bool(include_files),
        },
    )
    return buffer.getvalue(), manifest


def _filename(principal: str) -> str:
    # 路径不含账号名：HTTP 头必须是 latin-1 可编码，且导出文件名会被用户
    # 保存在磁盘上——用时间戳 + 去标识短哈希即可区分，不写用户名。
    digest = hashlib.sha256(str(principal or "").encode("utf-8")).hexdigest()[:6]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"liuhao-my-data-{stamp}-{digest}.zip"


# --------------------------------------------------------------------------- #
# HTTP 面
# --------------------------------------------------------------------------- #

@accounts_router.post("/export/all")
def export_all(
    payload: Optional[ExportAllRequest] = None,
    context: AccountContext = Depends(require_account_principal),
) -> Response:
    """一键导出「我的全部数据」：普通人可直接打开的 ZIP（明文，仅自己）。"""
    request = payload or ExportAllRequest()
    content, manifest = build_export_archive(
        context, include_files=bool(request.include_files)
    )
    return Response(
        content=content,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{_filename(context.principal)}"',
            "X-LiuHao-Contents": json.dumps(manifest["contents"], ensure_ascii=False),
        },
    )


class ChatExportRequest(BaseModel):
    session_id: Optional[str] = Field(None, max_length=128)
    format: str = Field("md", description="md 或 json")


@chat_router.post("/export")
def export_chat(
    payload: ChatExportRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """导出会话为**对话文本**；不传 session_id 时导出自己的全部会话。"""
    fmt = str(payload.format or "md").strip().lower()
    if fmt not in ("md", "json"):
        raise deny(400, "ACCOUNT_FORMAT_INVALID", "导出格式无效")
    if payload.session_id:
        turns = _load_own_session(payload.session_id, context.principal)
        if not turns:
            raise deny(404, _accounts.CODE_SUBTREE_DENIED, _accounts.NEUTRAL_NOT_FOUND_MESSAGE)
        files = [(payload.session_id, turns)]
    else:
        files, _ = collect_conversations(context)
        files = [
            (item["session_id"], _load_own_session(item["session_id"], context.principal))
            for item in files
        ]
    get_identity_manager().record_event(
        action="chat.export",
        actor=context.principal,
        target=context.principal,
        outcome="allowed",
        details={"sessions": len(files), "format": fmt},
    )
    if fmt == "json":
        return {
            "exported_at": _now_iso(),
            "sessions": [
                {"session_id": session_id, "turns": turns}
                for session_id, turns in files
            ],
            "filename": "conversations.json",
        }
    combined = "\n\n---\n\n".join(
        _conversation_markdown(session_id, turns) for session_id, turns in files
    )
    return {
        "content": combined or "# 暂无会话\n",
        "filename": "conversations.md",
        "sessions": len(files),
        "messages": sum(len(turns) for _, turns in files),
    }


@knowledge_router.post("/export")
def export_knowledge_endpoint(
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """导出自己的知识库为可回导 JSON（另附纯文本正文）。"""
    documents, manifest = export_knowledge(context)
    return {
        "format": "liuhao.knowledge.export.v1",
        "exported_at": _now_iso(),
        "documents": documents,
        "count": manifest["documents"],
        "filename": "knowledge.json",
    }


@knowledge_router.post("/import")
def import_knowledge_endpoint(
    payload: KnowledgeImportRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    return import_knowledge(context, payload.documents)


@memory_router.post("/import")
def import_memory_endpoint(
    payload: MemoryImportRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    return import_memories(context, payload.entries)


__all__ = [
    "accounts_router",
    "chat_router",
    "knowledge_router",
    "memory_router",
    "build_export_archive",
    "collect_conversations",
    "collect_employees",
    "collect_memories",
    "collect_account_settings",
    "collect_goals",
    "collect_usage",
    "collect_workspace_files",
    "export_knowledge",
    "import_knowledge",
    "import_memories",
]
