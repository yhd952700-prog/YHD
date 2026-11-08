"""门禁：全仓业务模块必须能真实 import，跨模块导入的符号必须存在。

这类断裂（把一个符号改名/搬家，另一个模块还在 import 旧名字）会让服务
在启动后的第一个请求上 503，而不是在某个模块的单测里失败 —— 所以这里
不按模块跑单测，而是：

1. 逐个真实 import ``src/`` 下所有业务模块（跳过 ``__main__`` 入口）；
2. 静态扫描 ``from src... import <name>``，确认目标模块上真的有这个符号
   （覆盖函数体内延迟 import，单靠 import 模块抓不到）。

任何 ImportError / AttributeError / ModuleNotFoundError 都直接红。
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
import pkgutil
import sys
from pathlib import Path
from typing import List, Set, Tuple

import src

_SRC_ROOT = Path(src.__file__).resolve().parent


def _all_modules() -> List[str]:
    names = [
        info.name
        for info in pkgutil.walk_packages(src.__path__, prefix="src.")
        if not info.name.endswith(".__main__")
    ]
    return sorted(names)


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(_SRC_ROOT.parent).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imported_symbols() -> List[Tuple[str, str, str]]:
    """``(module, symbol, file)`` for every import of a ``src``-owned symbol.

    Handles both absolute (``from src.ai.llm_support import x``) and relative
    (``from .llm_support import x``) forms, including imports inside functions,
    because that is exactly where a renamed symbol breaks at *request* time.
    """
    found: List[Tuple[str, str, str]] = []
    for path in _SRC_ROOT.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            # 语法错误应该由编译门禁负责；这里不重复报。
            continue
        current = _module_name(path)
        package = current if path.name == "__init__.py" else current.rsplit(".", 1)[0]
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level:
                base = package.split(".")
                keep = len(base) - (node.level - 1)
                if keep < 1:
                    continue
                module = ".".join(base[:keep])
                if node.module:
                    module = f"{module}.{node.module}" if module else node.module
            else:
                module = node.module or ""
            if not module.startswith("src"):
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                found.append((module, alias.name, str(path)))
    return found


def test_every_business_module_imports_cleanly():
    failures: List[str] = []
    modules = _all_modules()
    assert modules, "没有扫描到任何业务模块，门禁本身失效了"
    for name in modules:
        try:
            importlib.import_module(name)
        except (ImportError, AttributeError) as exc:
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
    assert not failures, "以下模块 import 失败（会导致服务 503）：\n" + "\n".join(
        failures
    )


def test_cross_module_imported_symbols_exist():
    failures: List[str] = []
    for module_name, symbol, file in _imported_symbols():
        try:
            module = importlib.import_module(module_name)
        except (ImportError, AttributeError) as exc:
            failures.append(f"{file}: 无法 import {module_name}: {exc}")
            continue
        if hasattr(module, symbol):
            continue
        # ``from src.pkg import submodule`` 也是合法写法：目标是一个模块。
        try:
            spec = importlib.util.find_spec(f"{module_name}.{symbol}")
        except (ImportError, AttributeError, ValueError):
            spec = None
        if spec is not None:
            continue
        failures.append(
            f"{file}: from {module_name} import {symbol} —— 符号不存在"
        )
    assert not failures, "以下跨模块导入指向了不存在的符号：\n" + "\n".join(failures)


_REPO_ROOT = _SRC_ROOT.parent


def _payload_root_py_modules() -> Set[str]:
    """``stage_payload.ROOT_FILES`` 里声明的根级 ``.py`` 模块名。"""
    spec = importlib.util.spec_from_file_location(
        "stage_payload_root_files_guard", _REPO_ROOT / "packaging" / "stage_payload.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return {name[:-3] for name in module.ROOT_FILES if name.endswith(".py")}


def _root_modules_imported_by_src() -> Set[str]:
    """仓库根 ``.py`` 中被 ``src/`` 以顶层模块名 import 的模块名。

    只看模块体：根模块缺失会先炸在模块体这一层；函数体内的延迟导入要等到
    那次请求才炸，但那时根模块早已被 import 过，不影响安装完整性判断。
    """
    root_modules = {path.stem for path in _REPO_ROOT.glob("*.py")}
    imported: Set[str] = set()
    for path in _SRC_ROOT.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in tree.body:
            names: List[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            imported.update(name for name in names if name in root_modules)
    return imported


def test_root_level_modules_are_declared_in_payload_root_files():
    """根级模块必须与 ``stage_payload.ROOT_FILES`` 完全一致。

    payload 只按白名单复制仓库根文件；``src/knowledge/embedding.py`` 里的
    ``from error_types import ProviderError`` 在仓库里能跑，是因为仓库根恰好
    在 ``sys.path`` 上。装到用户机器后没有这个便宜，漏一个就是「装完第一次
    启动就崩」——而现有用例只扫 ``src`` 内部，看不到这一类。
    """
    declared = _payload_root_py_modules()
    actual = _root_modules_imported_by_src()
    assert actual == declared, (
        "仓库根被 src 以顶层模块名 import 的模块与 ROOT_FILES 不一致：\n"
        f"  实际 import：{sorted(actual)}\n"
        f"  ROOT_FILES 声明：{sorted(declared)}\n"
        "漏声明会让安装包缺根模块；多声明则是清单过期。两边都要改齐。"
    )
