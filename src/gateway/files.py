"""工作区文件可观测面（**严格只读**）—— 按账号空间分区。

为什么要有这个模块
------------------
内核审计把 "Files" 列为产品缺口：员工通过 ``file_write`` 把产物写进工作区，
但人类没有浏览入口。这里补齐只读浏览/读取，不新增写路径。

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
* 没有 POST/PUT/PATCH/DELETE；写仍由执行内核的 ``file_write`` 负责。
* 跨账号/未授权空间一律 404 中性错误（不区分"越权"与"不存在"，避免成为
  探测面）。判定口径：
  - 从**账号空间**（``self``/``subtree``）里用 ``..`` 逃逸 = 跨账号尝试 → 404；
  - 从工作区根（``all``）逃逸到工作区外 = 非法路径 → 400。
* 所有路径经 ``resolve_in_workspace(path, root=物理根)`` 收敛。
* 读失败（不存在 / 二进制 / 过大）返回诚实状态码，不伪装成空目录。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..kernels.identity import _accounts
from .account_context import AccountContext, deny, require_account_principal

router = APIRouter(prefix="/v1/files", tags=["files"])

logger = logging.getLogger(__name__)

# 单次读取上限：控制台/审计员读取文件内容，绝不能把超大文件拖进内存。
_MAX_READ_BYTES = 5_000_000


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
            return rel[len(prefix) + 1 :]
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
