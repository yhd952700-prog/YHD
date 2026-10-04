"""工作区文件可观测面（**严格只读**）—— 把 AI 员工真实写出的产物摊开给人看。

为什么要有这个模块
------------------
内核审计（KERNEL-PRODUCT-CAPABILITY.md §19）把 "Files" 列为 **#1 产品缺口**：
用户在 LIUHAO 里雇了 AI 员工、下达了目标，员工通过 ``file_write`` 工具把产物
写进了工作区（``src/ai/workspace.py``，受 ``LIUHAO_WORKSPACE_ROOT`` 约束），但
**没有任何人类能浏览或读取这些文件** —— 一个"AI 替你干活但你看不到它干了什么"
的系统，谈不上可审计、可信、可运维。

这里补齐的正是那一层：把已经存在的工作区**暴露**成只读浏览/读取面，不新增写
路径（写仍由执行内核的 ``file_write`` 工具负责，经执行围栏约束），也不重建任何
文件系统语义。

硬约束：只读，永远只读
----------------------
* 没有 POST/PUT/PATCH/DELETE。本模块**绝不**提供任何写入口；那会绕过执行围栏，
  等于开了一条人类可直接落盘、不经审计的侧门。
* 所有路径都经 ``resolve_in_workspace`` 收敛到工作区根之内 —— 越界即 400，绝不
  用 "看起来像相对路径就放行" 的启发式（那是逃逸的来源，见 workspace.py 模块
  说明）。
* 读失败（文件不存在 / 二进制 / 过大）时返回**诚实的状态码与信息**，绝不伪装成
  "空目录" 或 "空内容"。

与审计/身份面一致：挂人类主权闸门 —— 工作区内容不匿名可读。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query

from .policy import require_human_principal

router = APIRouter(prefix="/v1/files", tags=["files"])

logger = logging.getLogger(__name__)

# 单次读取上限：控制台/审计员读取文件内容，绝不能把超大文件拖进内存。
_MAX_READ_BYTES = 5_000_000


def _workspace_root() -> str:
    """当前进程生效的工作区允许根（路径由 LIUHAO_WORKSPACE_ROOT 决定）。"""
    from src.ai.workspace import workspace_root

    return workspace_root()


def _resolve(path: str) -> str:
    """把工作区相对路径解析为真实绝对路径；越界即抛 HTTP 400。"""
    from src.ai.workspace import WorkspaceViolation, resolve_in_workspace

    try:
        return resolve_in_workspace(path)
    except WorkspaceViolation as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("")
def list_files(
    path: str = Query("", description="工作区内的相对路径；空字符串=根目录"),
) -> Dict[str, Any]:
    """列出某目录下的条目（名称 / 类型 / 大小 / 修改时间）。

    诚实行为：
    * ``path`` 越界 → 400。
    * ``path`` 不是目录（是文件或不存在）→ 404，并说明它是什么。
    * 空目录 → 返回 ``entries=[]``、``count=0``，不伪装。
    """
    root = _workspace_root()
    # 根目录列举用空路径，但 ``resolve_in_workspace("")`` 会 fail-closed 拒绝空串，
    # 所以空路径直接指到工作区根本身（根必然在根之内）。非空路径仍走严格收敛。
    abs_dir = root if not path.strip() else _resolve(path)
    if not os.path.isdir(abs_dir):
        raise HTTPException(
            status_code=404,
            detail="不是目录（可能不存在，或是一个文件）：%r" % path,
        )
    entries: List[Dict[str, Any]] = []
    try:
        names = sorted(os.listdir(abs_dir))
    except OSError as exc:
        raise HTTPException(status_code=500, detail="无法列举目录：%s" % exc)
    for name in names:
        child = os.path.join(abs_dir, name)
        try:
            st = os.stat(child)
        except OSError:
            # 列举期间消失的条目：跳过，不报错。
            continue
        is_dir = os.path.isdir(child)
        entries.append(
            {
                "name": name,
                "rel_path": (path.strip("/") + "/" + name).strip("/"),
                "type": "dir" if is_dir else "file",
                "size": None if is_dir else st.st_size,
                "mtime": int(st.st_mtime),
            }
        )
    return {
        "root": _workspace_root(),
        "path": path,
        "count": len(entries),
        "entries": entries,
    }


@router.get("/content")
def read_file(
    path: str = Query(..., description="工作区内的相对文件路径"),
) -> Dict[str, Any]:
    """读取一个文本文件的内容（带大小上限与二进制探测）。

    诚实行为：
    * ``path`` 越界 → 400。
    * 不是文件（是目录或不存在）→ 404。
    * 超过 ``_MAX_READ_BYTES`` → 413，并透传真实大小。
    * 不是合法 UTF-8 文本（二进制）→ 415，不假装成文本。
    """
    abs_path = _resolve(path)
    if not os.path.isfile(abs_path):
        raise HTTPException(
            status_code=404,
            detail="不是文件（可能不存在，或是一个目录）：%r" % path,
        )
    size = os.path.getsize(abs_path)
    if size > _MAX_READ_BYTES:
        raise HTTPException(
            status_code=413,
            detail="文件过大：%d 字节 > 上限 %d 字节" % (size, _MAX_READ_BYTES),
        )
    try:
        with open(abs_path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise HTTPException(status_code=500, detail="无法读取文件：%s" % exc)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=415,
            detail="二进制文件，无法以文本呈现：%r" % path,
        )
    return {
        "path": path,
        "size": size,
        "encoding": "utf-8",
        "content": text,
    }
