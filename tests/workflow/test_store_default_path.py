"""工作流 Store 缺省库路径的回归测试。

背景（2026-11-12，CI Build Verification 实测）：容器镜像以 ``USER app``
运行，``/app`` 根目录属 root 不可写，而缺省路径曾直接落根上
（``/app/.liuhao_workflows.db``）——``sqlite3.connect`` 建不了文件，
``sqlite3.OperationalError: unable to open database file``，容器启动即崩
（workflow 路由在 import 期实例化 store）。缺省必须落在已赋权的
``<root>/data/`` 子目录；旧根目录文件若已存在则沿用（不遗弃数据）。
"""

from __future__ import annotations

import os
from pathlib import Path

import src.workflow.store as store_module
from src.workflow.store import WORKFLOW_FILENAME, default_workflow_path


def _repo_root() -> Path:
    return Path(store_module.__file__).resolve().parents[2]


def test_default_path_avoids_unwritable_repo_root(monkeypatch):
    """干净布局下缺省路径必须落在 <root>/data/，不能直接落根上。"""
    # 隔离环境变量：env/workspace 覆盖存在时本断言的布局前提不成立。
    for var in (
        store_module.WORKFLOW_ENV_VAR,
        "LIUHAO_WORKSPACE_ROOT",
        "LIUHAO_DATA_DIR",
    ):
        monkeypatch.delenv(var, raising=False)

    root = _repo_root()
    legacy = root / f".{WORKFLOW_FILENAME}"
    modern = root / "data" / WORKFLOW_FILENAME

    resolved = Path(default_workflow_path())

    if legacy.exists() and not modern.exists():
        # 连续性回退：旧文件存在且新文件不存在 ⇒ 沿用旧路径。
        assert resolved == legacy
    else:
        # 干净布局（CI/全新安装/容器）：必须落已赋权的 data/ 子目录。
        assert resolved == modern
