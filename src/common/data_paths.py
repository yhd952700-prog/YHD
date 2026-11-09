"""统一解析本地持久化的落盘路径。

为什么需要这个入口
==================
此前每个 store 各自读环境变量，而且各自只认其中一部分（``CONVERSATION_DB_PATH``
/ ``MEMORY_DB_PATH`` / ``LIUHAO_WORKSPACE_ROOT`` / ``LIUHAO_MODEL_CONFIG_DB``
/ ``LIUHAO_KNOWLEDGE_DB`` …）。于是只设 ``LIUHAO_DATA_DIR`` 时，会话库、记忆库、
员工名册、目标状态仍然写回仓库根目录：一套"数据目录"被拆成好几处，隔离脚本
以为已经干净，实际仍在污染共享开发目录。

所有新增的、需要落盘的存储都必须经过 ``resolve_store_path``，不要再自己拼
路径、也不要再自己读这些环境变量。

优先级（从高到低）
------------------
1. 单个存储的显式环境变量（``env_var``，例如 ``CONVERSATION_DB_PATH``）；
2. ``LIUHAO_WORKSPACE_ROOT``（仅工作区类存储，``workspace=True``；安装版启动器
   把它指向 ``<data>/workspace``，这是既有契约）；
3. ``LIUHAO_DATA_DIR``（数据根）；
4. 各存储的历史默认值（源码模式不设置任何变量时，维持现有位置不变）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Union

DATA_DIR_ENV = "LIUHAO_DATA_DIR"
WORKSPACE_ROOT_ENV = "LIUHAO_WORKSPACE_ROOT"

PathLike = Union[str, "os.PathLike[str]"]


def repo_root() -> Path:
    """源码/安装树根目录（``src/common/data_paths.py`` 的上两级）。"""
    return Path(__file__).resolve().parents[2]


def _env_path(name: Optional[str]) -> Optional[Path]:
    if not name:
        return None
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def data_root() -> Optional[Path]:
    """显式设置的 ``LIUHAO_DATA_DIR``；未设置时返回 ``None``。

    与 :func:`resolve_store_path` 的区别：这里不提供任何回退。适用于"没有数据根
    就必须保持 fail-closed（不落盘）"的存储，例如身份 JSON 库 —— 它的历史语义
    是"未配置即不持久化"，不能被悄悄改成写 ``%LOCALAPPDATA%``。
    """
    return _env_path(DATA_DIR_ENV)


def default_data_file(
    filename: str, legacy_default: Optional[PathLike] = None
) -> Optional[str]:
    """数据根下的文件路径；没有数据根时返回 ``legacy_default``（可为 ``None``）。"""
    root = data_root()
    if root is not None:
        return str(root / filename)
    if legacy_default is None:
        return None
    return str(Path(legacy_default).expanduser().resolve())


def resolve_store_path(
    filename: str,
    legacy_default: PathLike,
    *,
    env_var: Optional[str] = None,
    workspace: bool = False,
) -> str:
    """返回某个存储文件的绝对路径。

    Args:
        filename: 落在数据根下的文件名（如 ``conversation_store.db``）。
        legacy_default: 所有覆盖都未设置时使用的历史默认路径。
        env_var: 该存储自身的显式覆盖变量（最高优先级）。
        workspace: 该存储是否属于工作区域（员工/目标/产物/执行日志）。
            为 ``True`` 时 ``LIUHAO_WORKSPACE_ROOT`` 优先于 ``LIUHAO_DATA_DIR``，
            与安装版启动器的既有行为保持一致。
    """
    explicit = _env_path(env_var)
    if explicit is not None:
        return str(explicit)

    if workspace:
        workspace_root = _env_path(WORKSPACE_ROOT_ENV)
        if workspace_root is not None:
            return str(workspace_root / filename)

    data_root = _env_path(DATA_DIR_ENV)
    if data_root is not None:
        return str(data_root / filename)

    return str(Path(legacy_default).expanduser().resolve())
