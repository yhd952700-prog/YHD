"""REPO_ROOT 的语义：由文件位置推算，环境变量优先，两种形态都指向包根。

开发机：``<仓库根>/src/selfupgrade/paths.py`` → 仓库根。
装机后：``{app}\\src\\selfupgrade\\paths.py``（runtime\\python 直接跑源码，
不是 PyInstaller 冻结） → ``{app}``，其下同样有 ``src/``。
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

from src.selfupgrade import paths

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_paths_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


@pytest.fixture()
def fake_tree(tmp_path: Path) -> Path:
    """在临时目录里复刻 ``<root>/src/selfupgrade/paths.py`` 布局。"""
    source = REPO_ROOT / "src" / "selfupgrade" / "paths.py"
    target = tmp_path / "src" / "selfupgrade" / "paths.py"
    target.parent.mkdir(parents=True)
    target.write_bytes(source.read_bytes())
    return tmp_path


def test_repo_root_follows_file_location(fake_tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """没有环境变量时，REPO_ROOT 必须跟着文件位置走，而不是写死某个盘符。"""
    monkeypatch.delenv("LIUHAO_REPO_ROOT", raising=False)
    module = _load_paths_module(fake_tree / "src" / "selfupgrade" / "paths.py", "_paths_probe")
    assert module.REPO_ROOT == fake_tree.resolve()
    assert module.LOCK_DIR == fake_tree.resolve() / "release-lock"


def test_env_override_still_wins(fake_tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``LIUHAO_REPO_ROOT`` 的优先级不变：设置了就以它为准。"""
    custom = fake_tree / "custom-root"
    monkeypatch.setenv("LIUHAO_REPO_ROOT", str(custom))
    module = _load_paths_module(fake_tree / "src" / "selfupgrade" / "paths.py", "_paths_probe_env")
    assert module.REPO_ROOT == custom


def test_live_module_points_at_this_repo() -> None:
    """真实模块在本仓库里必须解析到仓库根，release-lock 就在那里。"""
    if os.environ.get("LIUHAO_REPO_ROOT"):
        pytest.skip("环境变量已覆盖，推导语义由 fake_tree 两条用例覆盖")
    assert paths.REPO_ROOT == REPO_ROOT
    assert paths.LOCK_DIR.is_dir()
