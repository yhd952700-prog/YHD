"""工作区文件面 —— 按账号空间分区（浏览/读取/下载 + 上传）。

为什么要有这个模块
------------------
内核审计把 "Files" 列为产品缺口：员工通过 ``file_write`` 把产物写进工作区，
但人类没有浏览入口。这里补齐浏览/读取；0.4.6（C 组，5-5）补了上传接口
（``POST /v1/files/upload``），因为工作区此前只有列表/读，人没法直接把
文件放进自己的工作区。上传只写进**自己的账号空间**（根默认写公共区），
不开放改/删他人文件。

账号分区（``docs/account-hierarchy.md`` §3.5）
--------------------------------------------
物理约定：``<workspace>/accounts/<owner>/<relative>`` 是账号私有空间。

* **成员**：逻辑根恒为 ``accounts/<自己>``；不管传什么 ``account`` 参数，
  都只能落在自己的空间。跨账号路径一律 404（与"不存在"不可区分）。
* **根账号**：默认浏览工作区根（公共区 + ``accounts`` 目录），也可用
  ``account=<子树内成员>`` 只读浏览该成员空间；子树外 → 404。
* 旧公共区（工作区根下、不含 owner 前缀的文件）只对根可见；成员看不到，
  这是"跨账号读一律 404"的必要代价，宁可少看，不可越权。

硬约束
------
* 写入口：上传（``POST /upload``，只写自己的账号空间）、新建文件夹
  （``POST /mkdir``）、重命名（``PATCH /rename``）；删除不走直接删，
  只走审批（``DELETE`` 建待办 → 批复 → ``POST /deletions/.../execute``
  移进回收站，可恢复）。改/删他人的文件一律不允许。
* 跨账号/未授权空间一律 404 中性错误（不区分"越权"与"不存在"，避免成为
  探测面）。判定口径：
  - 从**账号空间**（``self``/``subtree``）里用 ``..`` 逃逸 = 跨账号尝试 → 404；
  - 从工作区根（``all``）逃逸到工作区外 = 非法路径 → 400。
* 所有路径经 ``resolve_in_workspace(path, root=物理根)`` 收敛。
* 读失败（不存在 / 二进制 / 过大）返回诚实状态码，不伪装成空目录。
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..kernels.identity import _accounts
from .account_context import AccountContext, deny, require_account_principal
from .decision_approval import create_decision_approval, decision_status

router = APIRouter(prefix="/v1/files", tags=["files"])

logger = logging.getLogger(__name__)

# 单次读取上限：控制台/审计员读取文件内容，绝不能把超大文件拖进内存。
_MAX_READ_BYTES = 5_000_000

# 单次下载上限：下载是流式回传（不进内存），上限只防把超大文件当附件拖走。
_MAX_DOWNLOAD_BYTES = 200_000_000

# 单次搜索读内容的文件上限：内容搜索只读 1MB 以下的文本文件，
# 再大的不读（不编"无命中"，也不把大文件拖进内存）。
_MAX_CONTENT_SEARCH_BYTES = 1_000_000

#: 列表页不展示的运行期存储文件（数据库与其临时文件）：搜索同样不返回，
#: 与列表口径一致，不让用户在搜索里翻到数据库文件。
_STORAGE_ARTIFACT_RE = re.compile(r"(?:\.db|\.sqlite|\.sqlite3)(?:-shm|-wal)?$", re.IGNORECASE)


def _trash_root() -> str:
    """删文件备份（回收站）根目录：数据根下 ``file_trash``。

    没配数据根时落工作区 ``.data/file_trash``（与工作区文件分开，
    不进列表/搜索）。备份只是一次"移动"，不是删除，恢复就是移回去。
    """
    from src.common.data_paths import data_root

    root = data_root()
    if root is not None:
        return str(root / "file_trash")
    return os.path.join(_workspace_root(), ".data", "file_trash")


def _trash_record_path(backup_id: str) -> str:
    return os.path.join(_trash_root(), backup_id, "backup.json")


def _read_trash_record(backup_id: str) -> Optional[Dict[str, Any]]:
    path = _trash_record_path(backup_id)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            record = json.load(handle)
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def _find_trash_by_approval(approval_id: str) -> Optional[Dict[str, Any]]:
    """按审批找已执行的删除备份（执行接口幂等：批了两次也不删两次）。"""
    root = _trash_root()
    if not os.path.isdir(root):
        return None
    for name in sorted(os.listdir(root)):
        record = _read_trash_record(name)
        if record and str(record.get("approval_id") or "") == approval_id:
            return record
    return None


def _content_snippet(abs_path: str, needle: str) -> Optional[str]:
    """在文本文件里找关键词，返回带上下文的片段；不是文本/过大返回 None。"""
    try:
        size = os.path.getsize(abs_path)
    except OSError:
        return None
    if size > _MAX_CONTENT_SEARCH_BYTES:
        return None
    try:
        with open(abs_path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    index = text.lower().find(needle)
    if index < 0:
        return None
    start = max(0, index - 40)
    end = min(len(text), index + len(needle) + 80)
    snippet = text[start:end].replace("\n", " ").replace("\r", " ")
    return ("…" if start > 0 else "") + snippet + ("…" if end < len(text) else "")


def _write_not_in_subtree(context: AccountContext, mode: str) -> None:
    """写入口禁区：根账号浏览成员空间（subtree）时只读，不能改别人的文件。

    与上传接口同一口径（见 ``upload_file``）；已批准的删除执行/恢复
    不走这里 —— 审批本身就是授权，执行时只校验审批结论与归属。
    """
    if mode == "subtree":
        raise HTTPException(
            status_code=403,
            detail="不能改别人的账号空间里的文件（subtree 视角只读）",
        )

# 单次上传上限 10MB：工作区文件以文档为主；再大请走企业知识库上传。
_MAX_UPLOAD_BYTES = 10_000_000

#: 拒绝直接落盘的可执行后缀（工作区不是程序安装位置）。
_BLOCKED_UPLOAD_SUFFIXES = frozenset(
    {".exe", ".bat", ".cmd", ".ps1", ".sh", ".dll", ".scr", ".msi", ".com"}
)


def _neutral_not_found() -> HTTPException:
    """跨账号/越界统一成 404：不给"存在但你不能看"这种可枚举信号。"""
    return deny(
        404, _accounts.CODE_SUBTREE_DENIED, _accounts.NEUTRAL_NOT_FOUND_MESSAGE
    )


def _normalise_rel_path(path: str) -> str:
    return str(path or "").replace("\\", "/").strip().strip("/")


def _safe_owner(name: Optional[str]) -> Optional[str]:
    """账号目录名：拒绝分隔符与 ``..``，避免用账号名做路径逃逸。"""
    text = str(name or "").strip()
    if not text:
        return None
    if "/" in text or "\\" in text or text in (".", ".."):
        raise _neutral_not_found()
    return text


def _workspace_root() -> str:
    from src.ai.workspace import workspace_root

    return workspace_root()


def _account_space(context: AccountContext, account: Optional[str]) -> tuple[str, str, str]:
    """把请求视角收敛成 ``(物理根, 账号, 读取模式)``。

    模式仅供响应标记：``self``（自己的空间）、``subtree``（根只读浏览某成员）、
    ``all``（根默认的公共区 + 账号目录）。
    """
    root = _workspace_root()
    target = _safe_owner(account)
    if target and target != context.principal:
        # 只有根可以浏览别人；成员（含 standalone）一律 404。
        if not context.is_root or not context.can_read(target):
            raise _neutral_not_found()
    if target:
        return (
            os.path.join(root, "accounts", target),
            target,
            "self" if target == context.principal else "subtree",
        )
    if context.is_member:
        return os.path.join(root, "accounts", context.principal), context.principal, "self"
    return root, "", "all"


def _invalid_path() -> HTTPException:
    """路径逃逸（``..``/绝对路径）：非法请求，与账号越权分开。"""
    return HTTPException(status_code=400, detail="路径越界，拒绝访问")


def _resolve(path: str, physical_root: str, *, within_account: bool) -> str:
    """把相对路径解析到指定账号空间。

    从账号空间里逃逸（``../other-account/...``）就是跨账号尝试：给 404，且
    文案与"不存在"一致；从工作区根逃逸到工作区外是非法请求：给 400。
    """
    from src.ai.workspace import WorkspaceViolation, resolve_in_workspace

    try:
        return resolve_in_workspace(path, physical_root)
    except WorkspaceViolation:
        raise _neutral_not_found() if within_account else _invalid_path()


def _scope_block(account: str, mode: str) -> Dict[str, Any]:
    return {
        "mode": mode,
        "account": account or None,
        "root_view": mode != "self",
    }


def _logical_prefix(owner: str, mode: str) -> str:
    """账号空间的**工作区相对前缀**：``accounts/<owner>``；根默认视角为空。"""
    if mode == "all" or not owner:
        return ""
    return f"accounts/{owner}"


def _assert_subtree_workspace_path(
    context: AccountContext, rel: str, mode: str
) -> None:
    """根默认视角按子树收敛 ``accounts/<owner>/...``。

    ``mode="all"`` 是根在工作区根上的默认浏览（公共区 + ``accounts`` 目录）。
    归属校验此前只发生在 ``account=`` 参数上，于是
    ``path=accounts/<别人的账号>/secret.txt`` 能绕过 :meth:`can_read`：
    单根部署里账号目录都是自己树内成员，看不出问题；多根并存时就是跨根
    下载别人的产物。这里与 ``GoalViewer.sees`` 同一口径——只认自己子树，
    其余一律与"文件不存在"同形的 404。
    """
    if mode != "all":
        return
    parts = rel.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "accounts" and not context.can_read(parts[1]):
        raise _neutral_not_found()


def _strip_owner_prefix(path: str, owner: str, mode: str) -> str:
    """接受 ``accounts/<owner>/...`` 形式的工作区相对路径。

    列表返回的 ``rel_path`` 统一是工作区相对路径（带账号前缀），前端回填后
    再拿来 list/read 时必须能原样用；这里剥掉自己的前缀，两种写法都稳定。
    """
    rel = _normalise_rel_path(path)
    prefix = _logical_prefix(owner, mode)
    if prefix:
        if rel == prefix:
            return ""
        if rel.startswith(prefix + "/"):
            return rel[len(prefix) + 1:]
    return rel


@router.get("")
def list_files(
    path: str = Query("", description="账号空间内的相对路径；空字符串=空间根"),
    account: Optional[str] = Query(
        None, description="根账号可指定子树成员；成员只能省略或填自己"
    ),
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """列出某目录下的条目（名称 / 类型 / 大小 / 修改时间）。

    诚实行为：物理空间尚不存在时，成员得到空列表（还没产出文件）；根得到
    空列表而不是 500。``path`` 是文件或不存在 → 404。
    """
    physical_root, owner, mode = _account_space(context, account)
    within_account = mode != "all"
    rel = _strip_owner_prefix(path, owner, mode)
    _assert_subtree_workspace_path(context, rel, mode)
    logical_dir = f"{_logical_prefix(owner, mode)}/{rel}".strip("/")
    if not rel:
        abs_dir = physical_root
    else:
        abs_dir = _resolve(rel, physical_root, within_account=within_account)
    if not os.path.isdir(abs_dir):
        if not os.path.exists(physical_root):
            # 该账号还没有任何产物：空空间是事实，不是错误。
            return {
                # 成员响应不下发本机绝对路径；根账号保留完整路径用于排障。
                "root": physical_root if context.is_root else "<账号空间>",
                "path": logical_dir,
                "account": owner or None,
                "count": 0,
                "entries": [],
                "scope": _scope_block(owner, mode),
            }
        raise _neutral_not_found()

    entries: List[Dict[str, Any]] = []
    try:
        names = sorted(os.listdir(abs_dir))
    except OSError as exc:
        # 原始 OSError 只进日志（含路径/errno）；用户侧只给可行动的下一步。
        logger.warning("无法列举目录 path=%s: %s", abs_dir, exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "code": "fs_unavailable",
                "message": (
                    "无法打开这个文件夹：可能已被移动、删除，或当前账号没有权限。"
                    "请换一个位置后重试。"
                ),
            },
        )
    for name in names:
        child = os.path.join(abs_dir, name)
        try:
            st = os.stat(child)
        except OSError:
            continue
        entries.append(
            {
                "name": name,
                # 统一为「工作区相对路径」：带着 accounts/<owner> 前缀，
                # 前端回填到 list/read 时不会再把视角切回公共根。
                "rel_path": f"{logical_dir}/{name}".strip("/"),
                "type": "dir" if os.path.isdir(child) else "file",
                "size": None if os.path.isdir(child) else st.st_size,
                "mtime": int(st.st_mtime),
            }
        )
    return {
        # 成员响应不下发本机绝对路径；根账号保留完整路径用于排障。
        "root": physical_root if context.is_root else "<账号空间>",
        "path": logical_dir,
        "account": owner or None,
        "count": len(entries),
        "entries": entries,
        "scope": _scope_block(owner, mode),
    }


_SEARCH_LIMIT = 200


def _search_workspace_files(
    needle: str, physical_root: str, prefix: str, limit: int
) -> tuple[List[Dict[str, Any]], bool]:
    """在工作区文件系统里搜：文件名、路径与文件内容都匹配。

    从 ``search_files`` 抽出来的纯走查逻辑（行为不变）：数据库等运行期
    存储文件不返回；内容搜索只读 1MB 以下的 UTF-8 文本文件。合并搜索
    （``search_all_files``）复用同一路，所以两处搜工作区的结果永远一致，
    不会各说各话。
    """
    hits: List[Dict[str, Any]] = []
    truncated = False
    for dirpath, dirnames, filenames in os.walk(physical_root):
        dirnames.sort()
        names = sorted(dirnames) + sorted(filenames)
        for name in names:
            if _STORAGE_ARTIFACT_RE.search(name):
                continue
            abs_child = os.path.join(dirpath, name)
            rel = os.path.relpath(abs_child, physical_root).replace(os.sep, "/")
            logical = f"{prefix}/{rel}".strip("/") if prefix else rel
            name_hit = needle in name.lower()
            path_hit = needle in logical.lower()
            match = "文件名命中" if name_hit else ("路径命中" if path_hit else None)
            snippet: Optional[str] = None
            if match is None and os.path.isfile(abs_child):
                snippet = _content_snippet(abs_child, needle)
                if snippet is not None:
                    match = "内容命中"
            if match is None:
                continue
            if len(hits) >= limit:
                truncated = True
                break
            try:
                st = os.stat(abs_child)
            except OSError:
                continue
            entry: Dict[str, Any] = {
                "name": name,
                "rel_path": logical,
                "type": "dir" if os.path.isdir(abs_child) else "file",
                "size": None if os.path.isdir(abs_child) else st.st_size,
                "mtime": int(st.st_mtime),
                "match": match,
            }
            if snippet is not None:
                entry["snippet"] = snippet
            hits.append(entry)
        if truncated:
            break
    return hits, truncated


@router.get("/search")
def search_files(
    q: str = Query("", description="关键词；匹配文件名、路径与文件内容"),
    limit: int = Query(_SEARCH_LIMIT, ge=1, le=_SEARCH_LIMIT),
    account: Optional[str] = Query(
        None, description="根账号可指定子树成员；成员只能省略或填自己"
    ),
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """在整个账号空间里搜文件：文件名、路径与文件内容都匹配。

    内容搜索只读 1MB 以下的 UTF-8 文本文件（二进制/超大文件不读，
    命中时 ``match`` 为"内容命中"并带 ``snippet``）；数据库等运行期
    存储文件不返回。上限 200 条：命中更多时 ``truncated=true``，
    请缩小范围。
    """
    needle = q.strip().lower()
    if not needle:
        raise HTTPException(status_code=400, detail="请输入要搜的关键词")
    physical_root, owner, mode = _account_space(context, account)
    prefix = _logical_prefix(owner, mode)
    base: Dict[str, Any] = {
        "q": q.strip(),
        "limit": limit,
        "count": 0,
        "truncated": False,
        "entries": [],
        "scope": _scope_block(owner, mode),
    }
    if not os.path.isdir(physical_root):
        return base
    hits, truncated = _search_workspace_files(needle, physical_root, prefix, limit)
    base["entries"] = hits
    base["count"] = len(hits)
    base["truncated"] = truncated
    return base


# ─── 三库合并搜索 ─────────────────────────────────────────────
#
# 文件来源的三库（从代码里查清的，不是猜的）：
#
# 1. **工作区文件** —— 本模块（``src/gateway/files.py``）：磁盘工作区里
#    的文件，``GET /v1/files/search``，文件名/路径/内容都匹配。
# 2. **知识库** —— ``src/knowledge/documents.py`` 的向量知识库：
#    ``POST /v1/knowledge/search``，混合检索（向量 + 关键词），按文档
#    归属只返回自己的（``_scope_from_account_context``）。
# 3. **企业知识库** —— ``src/ai/knowledge/service.py`` 的企业文档库：
#    ``POST /v1/knowledge/enterprise/search``，FTS5 全文检索（中文
#    bigram + BM25），与知识库是**两套独立存储**（记忆与知识库分库，
#    见 ``knowledge_enterprise.py`` 模块说明）。
#
# 合并搜索一次调三路，每条结果带 ``library`` / ``library_label`` 标明
# 出处；哪一路查不成（比如知识库没配向量模型），那一路在 ``sources``
# 里如实报 ``unavailable`` + 原因，不编"没结果"，也不拖垮另外两路。


def _search_knowledge_library(
    q: str, context: AccountContext, limit: int
) -> tuple[List[Dict[str, Any]], str, Optional[str]]:
    """查知识库一路。返回 ``(条目, 状态, 备注)``。

    状态：``ok``（有命中）/ ``empty``（查成了、没命中）/
    ``unavailable``（这次没查成，备注写清原因）。
    """
    try:
        from src.api.routes.knowledge import _scope_from_account_context
        from src.knowledge.documents import get_knowledge_service

        scope = _scope_from_account_context(context)
        result = get_knowledge_service().search(q, top_k=limit, scope=scope)
    except Exception as exc:  # noqa: BLE001 - 一路查不到不拖垮别的库
        return [], "unavailable", f"知识库这次没查成（{exc}），不是没结果。"
    if not result.get("enabled", True):
        reason = str(
            result.get("message") or result.get("reason") or "知识库未启用"
        )
        return [], "unavailable", reason
    entries: List[Dict[str, Any]] = []
    for hit in result.get("hits") or []:
        if not isinstance(hit, dict):
            continue
        meta = hit.get("metadata") or {}
        if not isinstance(meta, dict):
            meta = {}
        doc_id = str(hit.get("doc_id") or "")
        title = str(
            meta.get("title") or meta.get("source") or doc_id or "知识库文档"
        )
        text = str(hit.get("text") or "")
        entries.append(
            {
                "library": "knowledge",
                "library_label": "知识库",
                "name": title,
                "ref": doc_id,
                "type": "file",
                "size": None,
                "mtime": None,
                "match": "知识库命中",
                "snippet": text[:200] if text else None,
            }
        )
    return entries, ("ok" if entries else "empty"), None


def _search_enterprise_library(
    q: str, context: AccountContext, limit: int
) -> tuple[List[Dict[str, Any]], str, Optional[str]]:
    """查企业知识库一路。返回 ``(条目, 状态, 备注)``（状态口径同上）。"""
    try:
        from src.ai.knowledge import service as enterprise_service
        from src.ai.knowledge.service import Viewer

        viewer = Viewer(
            principal=context.principal,
            subtree=tuple(context.subtree or (context.principal,)),
            is_root=bool(context.is_root),
        )
        result = enterprise_service.search(viewer, q, top_k=limit)
    except Exception as exc:  # noqa: BLE001 - 一路查不到不拖垮别的库
        return [], "unavailable", f"企业知识库这次没查成（{exc}），不是没结果。"
    entries: List[Dict[str, Any]] = []
    for hit in result.get("hits") or []:
        if not isinstance(hit, dict):
            continue
        doc_id = str(hit.get("document_id") or "")
        title = str(hit.get("title") or hit.get("origin_filename") or doc_id or "企业文档")
        snippet = str(hit.get("excerpt") or hit.get("text") or "")[:200]
        category_path = hit.get("category_path") or []
        entries.append(
            {
                "library": "enterprise",
                "library_label": "企业知识库",
                "name": title,
                "ref": doc_id,
                "type": "file",
                "size": None,
                "mtime": None,
                "match": "内容命中",
                "snippet": snippet or None,
                "category_path": category_path if isinstance(category_path, list) else [],
            }
        )
    return entries, ("ok" if entries else "empty"), None


@router.get("/search-all")
def search_all_files(
    q: str = Query("", description="关键词；三库一次搜全"),
    limit: int = Query(60, ge=1, le=_SEARCH_LIMIT, description="三库合计返回上限"),
    account: Optional[str] = Query(
        None, description="根账号可指定子树成员；成员只能省略或填自己"
    ),
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """三库合并搜索：工作区文件 + 知识库 + 企业知识库，一次出结果。

    每条结果带 ``library``（``workspace`` / ``knowledge`` /
    ``enterprise``）与 ``library_label``（中文库名）；``sources`` 逐库
    汇报状态与条数，查不成的库如实写原因。工作区这一路就是本地搜索
    （``GET /v1/files/search``）同一套走查逻辑，不是另起一套假搜索。
    """
    needle = q.strip().lower()
    if not needle:
        raise HTTPException(status_code=400, detail="请输入要搜的关键词")
    physical_root, owner, mode = _account_space(context, account)
    prefix = _logical_prefix(owner, mode)
    sources: List[Dict[str, Any]] = []
    entries: List[Dict[str, Any]] = []
    truncated = False

    # 1. 工作区文件（本地搜索同一路）。
    ws_hits: List[Dict[str, Any]] = []
    ws_status = "empty"
    ws_note: Optional[str] = None
    if os.path.isdir(physical_root):
        ws_hits, ws_truncated = _search_workspace_files(
            needle, physical_root, prefix, limit
        )
        truncated = truncated or ws_truncated
        ws_status = "ok" if ws_hits else "empty"
    else:
        ws_note = "工作区还没有文件。"
    for hit in ws_hits:
        entries.append({**hit, "library": "workspace", "library_label": "工作区文件"})
    sources.append(
        {
            "key": "workspace",
            "label": "工作区文件",
            "status": ws_status,
            "count": len(ws_hits),
            **({"note": ws_note} if ws_note else {}),
        }
    )

    # 2. 知识库。3. 企业知识库（各路查不成只记状态，不抛 500）。
    kb_hits, kb_status, kb_note = _search_knowledge_library(q.strip(), context, limit)
    sources.append(
        {
            "key": "knowledge",
            "label": "知识库",
            "status": kb_status,
            "count": len(kb_hits),
            **({"note": kb_note} if kb_note else {}),
        }
    )
    entries.extend(kb_hits)
    ent_hits, ent_status, ent_note = _search_enterprise_library(
        q.strip(), context, limit
    )
    sources.append(
        {
            "key": "enterprise",
            "label": "企业知识库",
            "status": ent_status,
            "count": len(ent_hits),
            **({"note": ent_note} if ent_note else {}),
        }
    )
    entries.extend(ent_hits)

    if len(entries) > limit:
        entries = entries[:limit]
        truncated = True
    return {
        "q": q.strip(),
        "limit": limit,
        "count": len(entries),
        "truncated": truncated,
        "entries": entries,
        "sources": sources,
        "scope": _scope_block(owner, mode),
    }


@router.get("/content")
def read_file(
    path: str = Query(..., description="账号空间内的相对文件路径"),
    account: Optional[str] = Query(None, description="根账号可指定子树成员"),
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """读取一个文本文件（带大小上限与二进制探测）。

    * 越界 / 跨账号 → 404，与"文件不存在"完全相同的错误体；
    * 不是文件 → 404；超过上限 → 413 并透传真实大小；
    * 不是合法 UTF-8 → 415。
    """
    physical_root, owner, mode = _account_space(context, account)
    rel = _strip_owner_prefix(path, owner, mode)
    _assert_subtree_workspace_path(context, rel, mode)
    if not rel:
        raise _neutral_not_found()
    abs_path = _resolve(rel, physical_root, within_account=(mode != "all"))
    logical_path = f"{_logical_prefix(owner, mode)}/{rel}".strip("/")
    if not os.path.isfile(abs_path):
        raise _neutral_not_found()
    try:
        size = os.path.getsize(abs_path)
    except OSError:
        raise _neutral_not_found()
    if size > _MAX_READ_BYTES:
        raise HTTPException(
            status_code=413,
            detail="文件过大，拒绝整块读入：%d 字节（上限 %d）"
            % (size, _MAX_READ_BYTES),
        )
    try:
        with open(abs_path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        # 同上：路径与 errno 只进日志，响应体只保留人话。
        logger.warning("读取文件失败 path=%s: %s", abs_path, exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "code": "fs_unavailable",
                "message": (
                    "无法打开这个文件夹：可能已被移动、删除，或当前账号没有权限。"
                    "请换一个位置后重试。"
                ),
            },
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=415,
            detail="不是 UTF-8 文本（二进制文件请在文件系统查看）",
        )
    return {
        # 成员响应不下发本机绝对路径；根账号保留完整路径用于排障。
        "root": physical_root if context.is_root else "<账号空间>",
        "path": logical_path,
        "account": owner or None,
        "size": size,
        "mtime": int(os.path.getmtime(abs_path)),
        "content": text,
        "encoding": "utf-8",
        "scope": _scope_block(owner, mode),
    }


@router.get("/download")
def download_file(
    path: str = Query(..., description="账号空间内的相对文件路径"),
    account: Optional[str] = Query(None, description="根账号可指定子树成员"),
    context: AccountContext = Depends(require_account_principal),
) -> FileResponse:
    """下载一个文件（只读，流式回传，不进内存）。

    与 ``GET /content`` 同一套账号空间与路径口径：越界 / 跨账号 →
    404，与"文件不存在"完全相同的错误体；不是文件 → 404；超过
    200MB → 413 并透传真实大小。响应带 ``Content-Disposition:
    attachment``，浏览器直接存成本地文件。
    """
    import mimetypes

    physical_root, owner, mode = _account_space(context, account)
    rel = _strip_owner_prefix(path, owner, mode)
    _assert_subtree_workspace_path(context, rel, mode)
    if not rel:
        raise _neutral_not_found()
    abs_path = _resolve(rel, physical_root, within_account=(mode != "all"))
    if not os.path.isfile(abs_path):
        raise _neutral_not_found()
    try:
        size = os.path.getsize(abs_path)
    except OSError:
        raise _neutral_not_found()
    if size > _MAX_DOWNLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail="文件过大，拒绝下载：%d 字节（上限 %d）"
            % (size, _MAX_DOWNLOAD_BYTES),
        )
    media_type, _encoding = mimetypes.guess_type(abs_path)
    return FileResponse(
        abs_path,
        media_type=media_type or "application/octet-stream",
        filename=os.path.basename(abs_path),
    )


@router.post("/upload", status_code=201)
async def upload_file(
    file: UploadFile = File(...),
    path: str = Form("", description="目标目录（账号空间内的相对路径；空=空间根）"),
    account: Optional[str] = Form(
        None, description="根账号可指定自己；成员只能省略或填自己"
    ),
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """上传一个文件到自己的账号空间（0.4.6，5-5）。

    * 只写自己的空间：成员恒为自己的 ``accounts/<自己>``；根默认写工作区
      公共区。根想往某成员空间写时请用 ``account=<该成员>``——本接口拒绝
      （``subtree`` 只读浏览），避免以“代传”名义改别人的文件。
    * 文件名只取 basename，拒绝空名与 ``..``；路径经 ``_resolve`` 收敛，
      逃逸一律按跨账号处理（404）或非法请求（400）。
    * 10MB 上限，可执行后缀（.exe/.bat/.ps1 等）拒绝落盘；先写临时文件
      再原子改名，失败不留半截文件。
    """
    filename = os.path.basename((file.filename or "").strip())
    if not filename or filename in (".", ".."):
        raise HTTPException(status_code=422, detail="请选择要上传的文件")
    suffix = os.path.splitext(filename)[1].lower()
    if suffix in _BLOCKED_UPLOAD_SUFFIXES:
        raise HTTPException(
            status_code=415,
            detail=f"不接受 {suffix} 这类可执行文件上传到工作区",
        )
    physical_root, owner, mode = _account_space(context, account)
    if mode == "subtree":
        raise HTTPException(
            status_code=403,
            detail="不能往别人的账号空间上传文件（subtree 视角只读）",
        )
    rel_dir = _strip_owner_prefix(path, owner, mode)
    _assert_subtree_workspace_path(context, rel_dir, mode)
    within_account = mode != "all"
    if rel_dir:
        abs_dir = _resolve(rel_dir, physical_root, within_account=within_account)
    else:
        abs_dir = physical_root
    os.makedirs(abs_dir, exist_ok=True)
    abs_path = _resolve(
        f"{rel_dir}/{filename}".strip("/") if rel_dir else filename,
        physical_root,
        within_account=within_account,
    )
    logical_path = f"{_logical_prefix(owner, mode)}/{rel_dir}/{filename}".strip("/")
    logical_path = "/".join(part for part in logical_path.split("/") if part)
    tmp_path = abs_path + f".upload-{os.getpid()}.tmp"
    written = 0
    try:
        with open(tmp_path, "wb") as sink:
            while True:
                block = await file.read(1024 * 256)
                if not block:
                    break
                written += len(block)
                if written > _MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"文件超过 {_MAX_UPLOAD_BYTES // (1024 * 1024)}MB 上限",
                    )
                sink.write(block)
        await file.close()
        os.replace(tmp_path, abs_path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:  # pragma: no cover - 清理失败不影响业务结果
                pass
    logger.info(
        "files: uploaded %s (%d bytes) by %s", logical_path, written, context.principal
    )
    return {
        "path": logical_path,
        "account": owner or None,
        "size": written,
        "mtime": int(os.path.getmtime(abs_path)),
        "scope": _scope_block(owner, mode),
    }


# ─── 写入口：新建文件夹 / 重命名 ──────────────────────────────
#
# 删除单独走审批（见下节）：删文件是不可逆操作里最危险的一档，
# 必须先经审批中心拍板、执行时先备份进回收站、还能恢复 —— 三步缺一不可。


class MkdirRequest(BaseModel):
    path: str = Field(..., min_length=1, description="要新建的文件夹相对路径")
    account: Optional[str] = Field(
        default=None, description="根账号可指定自己；成员只能省略或填自己"
    )


class RenameFileRequest(BaseModel):
    path: str = Field(..., min_length=1, description="要改名的文件/文件夹相对路径")
    new_name: str = Field(..., min_length=1, max_length=255, description="新名字（只要名，不要路径）")
    account: Optional[str] = Field(
        default=None, description="根账号可指定自己；成员只能省略或填自己"
    )


@router.post("/mkdir")
def make_directory(
    body: MkdirRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """新建文件夹（含多级父目录；已存在时如实返回 created=false，不报错）。"""
    physical_root, owner, mode = _account_space(context, body.account)
    _write_not_in_subtree(context, mode)
    rel = _strip_owner_prefix(body.path, owner, mode)
    _assert_subtree_workspace_path(context, rel, mode)
    if not rel:
        raise HTTPException(status_code=400, detail="不能在这里新建文件夹：路径为空")
    abs_dir = _resolve(rel, physical_root, within_account=(mode != "all"))
    if os.path.isfile(abs_dir):
        raise HTTPException(
            status_code=409, detail="这个位置已经有一个同名文件，不能再建文件夹"
        )
    existed = os.path.isdir(abs_dir)
    os.makedirs(abs_dir, exist_ok=True)
    logical_path = f"{_logical_prefix(owner, mode)}/{rel}".strip("/")
    logger.info("files: mkdir %s by %s", logical_path, context.principal)
    return {
        "path": logical_path,
        "account": owner or None,
        "created": not existed,
        "scope": _scope_block(owner, mode),
    }


@router.patch("/rename")
def rename_file(
    body: RenameFileRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """重命名文件或文件夹（只改名，不移动位置；目标已存在时 409）。"""
    new_name = str(body.new_name or "").strip()
    if (
        not new_name
        or new_name in (".", "..")
        or "/" in new_name
        or "\\" in new_name
    ):
        raise HTTPException(
            status_code=422, detail="新名字只能是一个文件名，不能带路径或特殊符号"
        )
    if _STORAGE_ARTIFACT_RE.search(new_name):
        raise HTTPException(status_code=400, detail="不能把文件改成数据库这类系统文件名")
    physical_root, owner, mode = _account_space(context, body.account)
    _write_not_in_subtree(context, mode)
    rel = _strip_owner_prefix(body.path, owner, mode)
    _assert_subtree_workspace_path(context, rel, mode)
    if not rel or rel == "accounts":
        raise HTTPException(status_code=400, detail="这个位置不能改名")
    if _STORAGE_ARTIFACT_RE.search(os.path.basename(rel)):
        raise HTTPException(status_code=400, detail="系统数据文件不能在这里改名")
    abs_src = _resolve(rel, physical_root, within_account=(mode != "all"))
    if not os.path.exists(abs_src):
        raise _neutral_not_found()
    parent_rel = "/".join(rel.split("/")[:-1])
    new_rel = f"{parent_rel}/{new_name}".strip("/") if parent_rel else new_name
    abs_dst = _resolve(new_rel, physical_root, within_account=(mode != "all"))
    if os.path.exists(abs_dst):
        raise HTTPException(
            status_code=409, detail=f"已经有同名文件或文件夹：{new_name}"
        )
    try:
        os.rename(abs_src, abs_dst)
    except OSError as exc:
        logger.warning("files: rename failed %s: %s", abs_src, exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "code": "fs_unavailable",
                "message": "改名失败：文件可能已被移动、删除，或当前账号没有权限。",
            },
        )
    logical_path = f"{_logical_prefix(owner, mode)}/{new_rel}".strip("/")
    logger.info(
        "files: renamed %s -> %s by %s",
        body.path, logical_path, context.principal,
    )
    return {
        "path": logical_path,
        "account": owner or None,
        "renamed": True,
        "scope": _scope_block(owner, mode),
    }


# ─── 删除：审批 → 执行（先备份）→ 可恢复 ─────────────────────
#
# 三步缺一不可，绝不静默删：
#
# 1. ``DELETE /v1/files`` 只建一条审批待办（``files.delete``），文件原地不动；
# 2. 批复后 ``POST /v1/files/deletions/{approval_id}/execute`` 才动手，
#    动手也是"移动"进回收站（``file_trash/<backup_id>/``），不是 unlink；
# 3. ``POST /v1/files/trash/{backup_id}/restore`` 把备份原样移回去。
#
# 拒绝/未批复时执行一律 409；重复执行同一条审批只返回第一次的备份
# （不删两次）。回收站条目按账号过滤：普通用户只见自己的，根账号见子树。


@router.delete("")
def request_delete_file(
    path: str = Query(..., description="要删除的文件/文件夹相对路径"),
    account: Optional[str] = Query(
        None, description="根账号可指定自己；成员只能省略或填自己"
    ),
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """申请删除：只建审批待办，文件现在还在，等批复后才能执行。"""
    physical_root, owner, mode = _account_space(context, account)
    _write_not_in_subtree(context, mode)
    rel = _strip_owner_prefix(path, owner, mode)
    _assert_subtree_workspace_path(context, rel, mode)
    if not rel or rel == "accounts":
        raise HTTPException(status_code=400, detail="这个位置不能删除")
    if _STORAGE_ARTIFACT_RE.search(os.path.basename(rel)):
        raise HTTPException(status_code=400, detail="系统数据文件不能在这里删除")
    abs_path = _resolve(rel, physical_root, within_account=(mode != "all"))
    if not os.path.exists(abs_path):
        raise _neutral_not_found()
    kind = "文件夹" if os.path.isdir(abs_path) else "文件"
    logical_path = f"{_logical_prefix(owner, mode)}/{rel}".strip("/")
    # A2b 第 15 项：删文件审批人按规矩定——提交人不是 Owner 时须 Owner 批，
    # Owner 本人删自己文件可自批。Owner 就是账号树根（``node.root_id``）。
    # 非 Owner 提交时归属也写成 Owner：审批中心允许 owner_account_id 本人
    # 拍板，只改 approvers 的话提交人仍能以归属人身份自己批掉。
    if context.is_owner:
        approval_owner = context.principal
        approvers: Optional[List[str]] = None  # 自批：默认 approvers=[提交人]
        approver_note = "你是 Owner，删自己的文件，自己批就行。"
    else:
        approval_owner = str(context.node.root_id)
        approvers = [approval_owner]
        approver_note = "这条删除要 Owner 批了才能执行，你自己批不了。"
    created = create_decision_approval(
        title=f"删除{kind}：{logical_path}",
        operation="files.delete",
        reason=(
            f"{context.principal} 申请删除{kind}「{logical_path}」。"
            "通过后文件会先移进回收站（可恢复），不是直接删掉。"
            f"{approver_note}"
        ),
        submitted_by=context.principal,
        owner_account_id=approval_owner,
        approvers=approvers,
        extra_inputs={"path": logical_path, "account": owner or "", "kind": kind},
    )
    return {
        "status": "pending_approval",
        "approval_id": created["approval_id"],
        "run_id": created["run_id"],
        "path": logical_path,
        "kind": kind,
        "approver": approval_owner,
        "message": "已送进审批中心等拍板，文件现在还在；通过后才能执行删除。"
                   f"{approver_note}",
    }


@router.post("/deletions/{approval_id}/execute")
def execute_delete_file(
    approval_id: str,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """执行一条已通过的删除审批：把目标移进回收站（可恢复）。"""
    approval = decision_status(approval_id)
    if approval is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "approval_not_found", "message": "没有这条审批。"},
        )
    if str(approval.get("operation") or "") != "files.delete":
        raise HTTPException(
            status_code=400,
            detail={"code": "approval_wrong_operation", "message": "这条审批不是删除文件。"},
        )
    if str(approval.get("status") or "") != "approved":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "approval_not_approved",
                "message": "这条删除还没通过审批，不能执行；拒绝后也不能执行。",
            },
        )
    existing = _find_trash_by_approval(approval_id)
    if existing is not None:
        return {
            "deleted": True,
            "already_executed": True,
            "path": existing.get("path"),
            "backup_id": existing.get("backup_id"),
            "message": "这条删除已经执行过了，文件已在回收站里。",
        }
    from src.workflow.store import get_workflow_store

    run = get_workflow_store().get_run(str(approval.get("run_id") or ""))
    inputs = dict((run or {}).get("inputs") or {})
    logical_path = str(inputs.get("path") or "")
    account = str(inputs.get("account") or "")
    if not logical_path:
        raise HTTPException(
            status_code=500,
            detail={"code": "approval_missing_path", "message": "这条审批里没有要删的文件路径，无法执行。"},
        )
    # 归属再校验：只能删自己账号空间里的（根账号可删子树内的）；执行时
    # 不再拦 subtree —— 审批本身就是授权，拦了等于批了也删不了。
    if account and account != context.principal:
        if not context.is_root or not context.can_read(account):
            raise _neutral_not_found()
    physical_root, owner, mode = _account_space(context, account or None)
    rel = _strip_owner_prefix(logical_path, owner, mode)
    _assert_subtree_workspace_path(context, rel, mode)
    if not rel:
        raise HTTPException(status_code=400, detail="这条审批的路径无效，无法执行")
    abs_path = _resolve(rel, physical_root, within_account=(mode != "all"))
    if not os.path.exists(abs_path):
        raise HTTPException(
            status_code=409,
            detail={"code": "file_already_gone", "message": "文件已经不在了（可能已被删掉），没有可备份的内容。"},
        )
    backup_id = uuid.uuid4().hex[:12]
    backup_dir = os.path.join(_trash_root(), backup_id)
    os.makedirs(backup_dir, exist_ok=True)
    dest = os.path.join(backup_dir, os.path.basename(rel))
    try:
        shutil.move(abs_path, dest)
    except OSError as exc:
        logger.warning("files: delete move failed %s: %s", abs_path, exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "code": "fs_unavailable",
                "message": "删除失败：文件可能已被移动，或当前账号没有权限。",
            },
        )
    record = {
        "backup_id": backup_id,
        "approval_id": approval_id,
        "account": owner or "",
        "path": logical_path,
        "kind": str(inputs.get("kind") or ("文件夹" if os.path.isdir(dest) else "文件")),
        "deleted_by": context.principal,
        "deleted_at": time.time(),
        "stored_name": os.path.basename(dest),
    }
    try:
        with open(_trash_record_path(backup_id), "w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False)
    except OSError as exc:  # pragma: no cover - 备份记录写失败时尽力而为
        logger.warning("files: trash record write failed: %s", exc)
    logger.info(
        "files: deleted %s -> trash %s by %s", logical_path, backup_id, context.principal
    )
    return {
        "deleted": True,
        "already_executed": False,
        "path": logical_path,
        "backup_id": backup_id,
        "restore": f"POST /v1/files/trash/{backup_id}/restore",
        "message": "文件已移进回收站，可恢复，不是直接删掉。",
    }


@router.get("/trash")
def list_trash(
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """回收站列表（普通用户只见自己的，根账号见子树内的）。"""
    root = _trash_root()
    entries: List[Dict[str, Any]] = []
    if os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            record = _read_trash_record(name)
            if record is None:
                continue
            account = str(record.get("account") or "")
            if not context.is_root and account != context.principal:
                continue
            if context.is_root and account and not context.can_read(account):
                continue
            entries.append(
                {
                    "backup_id": str(record.get("backup_id") or name),
                    "approval_id": str(record.get("approval_id") or ""),
                    "account": account or None,
                    "path": str(record.get("path") or ""),
                    "kind": str(record.get("kind") or ""),
                    "deleted_by": str(record.get("deleted_by") or ""),
                    "deleted_at": record.get("deleted_at"),
                }
            )
    return {"count": len(entries), "entries": entries}


@router.post("/trash/{backup_id}/restore")
def restore_deleted_file(
    backup_id: str,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """把回收站里的备份原样移回去（回滚一次删除）。"""
    record = _read_trash_record(backup_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "backup_not_found", "message": "回收站里没有这份备份。"},
        )
    account = str(record.get("account") or "")
    if account and account != context.principal:
        if not context.is_root or not context.can_read(account):
            raise _neutral_not_found()
    logical_path = str(record.get("path") or "")
    physical_root, owner, mode = _account_space(context, account or None)
    rel = _strip_owner_prefix(logical_path, owner, mode)
    if not rel:
        raise HTTPException(status_code=400, detail="这份备份的路径无效，无法恢复")
    abs_path = _resolve(rel, physical_root, within_account=(mode != "all"))
    if os.path.exists(abs_path):
        raise HTTPException(
            status_code=409,
            detail={"code": "restore_target_exists", "message": "原位置已经有同名文件，先改名或移走它再恢复。"},
        )
    stored = os.path.join(_trash_root(), backup_id, str(record.get("stored_name") or ""))
    if not os.path.exists(stored):
        raise HTTPException(
            status_code=500,
            detail={"code": "backup_missing", "message": "备份文件找不到了，恢复不了。"},
        )
    parent = os.path.dirname(abs_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    try:
        shutil.move(stored, abs_path)
    except OSError as exc:
        logger.warning("files: restore failed %s: %s", stored, exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "code": "fs_unavailable",
                "message": "恢复失败：目标位置可能已被占用，或当前账号没有权限。",
            },
        )
    try:
        os.unlink(_trash_record_path(backup_id))
        os.rmdir(os.path.join(_trash_root(), backup_id))
    except OSError:  # pragma: no cover - 残留空目录不影响恢复结果
        pass
    logger.info(
        "files: restored %s from trash %s by %s", logical_path, backup_id, context.principal
    )
    return {"restored": True, "path": logical_path, "backup_id": backup_id}
