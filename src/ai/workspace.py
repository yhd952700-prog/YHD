"""工作区约束 —— 把「世界接口」的文件访问收敛到一个允许根之内。

为什么单独成模块，而不是把判断塞进 ``world_interface.py``
--------------------------------------------------------
``FilesystemAdapter`` 自身**没有任何路径约束**：``read`` / ``write`` 可及全盘。
既有三个调用方（``e2e_demo`` / ``hardening`` / ``vhl_benchmark``）各自在
``WorldInterface`` 的 ``authorize`` 回调里圈地，且那套写法是
**「只约束写、读一律放行」**（见 ``e2e_demo._world_authorize`` 的 docstring）。

当文件访问改由 **LLM 工具调用**驱动时，"读"同样是风险面：模型可以把
密钥库、凭据文件、私人文档读出来并写进回复或落盘外泄。因此工具层需要一条
**读写统一收敛**的路径判定，并且是唯一的入口 —— 散落的 ``startswith`` 判断
迟早会出现一处漏写。

判据（fail-closed）
------------------
把入参解析成**真实路径**（``realpath``，消解 ``..`` 与符号链接）后，必须落在
允许根之内；空路径、非字符串、越界一律拒绝并抛出 ``WorkspaceViolation``。
不做 "看起来像相对路径就放行" 这类启发式判断 —— 那种判断正是逃逸的来源。

已知取舍
--------
``realpath`` 会消解符号链接，因此**指向根外的符号链接也会被拒**（哪怕是出于
善意的软链）。这是有意的：宁可在联调时多一次显式拒绝，也不留一条绕过闸门的缝。
"""

from __future__ import annotations

import os
import sys
from typing import Optional

__all__ = [
    "WorkspaceViolation",
    "default_root",
    "workspace_root",
    "is_inside",
    "resolve_in_workspace",
]

_ENV_KEY = "LIUHAO_WORKSPACE_ROOT"
_WINDOWS_PREFERRED = "D:\\LiuHao-Workspace"


class WorkspaceViolation(ValueError):
    """路径落在允许根之外，或入参非法。fail-closed 的显式拒绝。"""


def default_root() -> str:
    """未设环境变量时的默认根。

    Windows 上优先 D 盘（本机交付物约定在 D 盘，见用户长期偏好）；
    其他平台退回家目录，避免把盘符硬编码进跨平台代码。
    """
    if sys.platform.startswith("win") and os.path.isdir("D:\\"):
        return _WINDOWS_PREFERRED
    return os.path.join(os.path.expanduser("~"), "LiuHao-Workspace")


def workspace_root() -> str:
    """当前允许根（绝对路径）。环境变量 ``LIUHAO_WORKSPACE_ROOT`` 优先。"""
    raw = (os.environ.get(_ENV_KEY) or "").strip()
    return os.path.abspath(raw) if raw else os.path.abspath(default_root())


def _norm(path: str) -> str:
    """归一化用于比较：Windows 上路径大小写不敏感，直接 startswith 会误判。"""
    return os.path.normcase(os.path.normpath(path))


def is_inside(root: str, path: str) -> bool:
    """``path`` 的真实路径是否落在 ``root`` 之内（含根本身）。不抛异常。"""
    try:
        return _is_inside(root, path)
    except (TypeError, ValueError, OSError):
        return False


def _is_inside(root: str, path: str) -> bool:
    root_real = os.path.realpath(root)
    candidate = path if os.path.isabs(path) else os.path.join(root_real, path)
    real = os.path.realpath(candidate)
    root_n = _norm(root_real)
    real_n = _norm(real)
    return real_n == root_n or real_n.startswith(root_n + os.sep)


def resolve_in_workspace(path: str, root: Optional[str] = None) -> str:
    """把 ``path`` 解析为工作区内的真实绝对路径；越界即抛 ``WorkspaceViolation``。

    Args:
        path: 相对路径（相对根解释）或绝对路径（仍必须位于根内）。
        root: 允许根；默认取 :func:`workspace_root`。

    Returns:
        消解 ``..`` / 符号链接后的真实绝对路径。

    Raises:
        WorkspaceViolation: 路径为空、类型非法、或解析后落在根外。
    """
    if not isinstance(path, str) or not path.strip():
        raise WorkspaceViolation("路径为空；请给出工作区内的相对路径")

    root_real = os.path.realpath(root or workspace_root())
    raw = path.strip()
    candidate = raw if os.path.isabs(raw) else os.path.join(root_real, raw)
    real = os.path.realpath(candidate)

    root_n = _norm(root_real)
    real_n = _norm(real)
    if real_n != root_n and not real_n.startswith(root_n + os.sep):
        raise WorkspaceViolation(
            f"路径越界：{raw!r} 不在允许的工作区内（{root_real}）"
        )
    return real
