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
import functools
import importlib
import importlib.util
import pkgutil
import sys
from pathlib import Path
from typing import Iterable, List, Set, Tuple

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
_STAGE_PAYLOAD = _REPO_ROOT / "packaging" / "stage_payload.py"


@functools.lru_cache(maxsize=1)
def _payload_manifest():
    """加载 ``packaging/stage_payload.py`` 里的常量（整轮测试只读一次）。

    ``packaging`` 不是包（没有 ``__init__.py``），所以按文件加载；该模块
    自己会把仓库根补进 ``sys.path``。
    """
    spec = importlib.util.spec_from_file_location(
        "stage_payload_root_files_guard", _STAGE_PAYLOAD
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _payload_root_py_modules() -> Set[str]:
    """``stage_payload.ROOT_FILES`` 里声明的根级 ``.py`` 模块名。"""
    return {
        name[:-3]
        for name in _payload_manifest().ROOT_FILES
        if name.endswith(".py")
    }


def _startup_scripts() -> List[Path]:
    """安装版启动链：``PAYLOAD_SCRIPTS`` 里当前存在的 ``.py`` 入口。

    只扫启动链而不是 ``scripts/`` 全树：验收/探针脚本不进 payload，把它们
    算进「根模块必须随包」会制造无关的红色。
    """
    return [
        _REPO_ROOT / rel
        for rel in _payload_manifest().PAYLOAD_SCRIPTS
        if rel.endswith(".py") and (_REPO_ROOT / rel).is_file()
    ]


def _iter_statements(statements: Iterable[ast.stmt]) -> Iterable[ast.stmt]:
    """递归遍历语句树，跳过表达式节点。

    ``import`` 只会出现在语句位置（模块体、函数/类体、if/try/for/with/match
    的分支里）。只下钻这些「容器语句」而不走 ``ast.walk``，可以覆盖函数体内
    的延迟导入，同时避免遍历海量表达式节点——实测省约 1.7 秒/560 文件。
    """
    stack = list(statements)
    while stack:
        node = stack.pop()
        yield node
        for field in ("body", "orelse", "finalbody"):
            stack.extend(getattr(node, field, ()) or ())
        for handler in getattr(node, "handlers", ()) or ():
            stack.extend(getattr(handler, "body", ()) or ())
        for case in getattr(node, "cases", ()) or ():
            stack.extend(getattr(case, "body", ()) or ())


def _root_module_imports(paths: Iterable[Path]) -> Set[str]:
    """从给定源文件里收集「以顶层模块名 import 仓库根模块」的名字。

    候选集 = 仓库根现存 ``*.py`` 的 stem ∪ ``ROOT_FILES`` 声明的 ``.py``：
    前者保证「文件在、清单漏登记」能被看见，后者保证「清单有、文件没了」
    不会因为文件消失而从候选集里静默退出。
    """
    candidates = {
        path.stem for path in _REPO_ROOT.glob("*.py")
    } | _payload_root_py_modules()
    imported: Set[str] = set()
    for path in paths:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in _iter_statements(tree.body):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif (
                isinstance(node, ast.ImportFrom)
                and node.level == 0
                and node.module
            ):
                names = [node.module.split(".")[0]]
            else:
                continue
            imported.update(name for name in names if name in candidates)
    return imported


def _root_modules_imported_by_product_code() -> Set[str]:
    """产品代码实际 import 的仓库根模块名。

    扫描面 = ``src/`` 全部 + 安装版启动链（``scripts/`` 里随包的入口）。
    扫到函数体而不是只看模块体：函数体内的延迟导入同样会在装到用户机器后
    因为缺根文件而炸，只是炸得晚一点（到那次请求才炸）。
    """
    return _root_module_imports(
        list(_SRC_ROOT.rglob("*.py")) + _startup_scripts()
    )


def test_root_level_modules_are_declared_in_payload_root_files():
    """根级模块必须与 ``stage_payload.ROOT_FILES`` 完全一致。

    payload 只按白名单复制仓库根文件；``src/knowledge/embedding.py`` 里的
    ``from error_types import ProviderError`` 在仓库里能跑，是因为仓库根恰好
    在 ``sys.path`` 上。装到用户机器后没有这个便宜，漏一个就是「装完第一次
    启动就崩」——而 ``pkgutil.walk_packages("src")`` 只扫 ``src`` 内部，
    看不到这一类。
    """
    declared = _payload_root_py_modules()
    actual = _root_modules_imported_by_product_code()
    assert actual == declared, (
        "产品代码以顶层模块名 import 的仓库根模块与 ROOT_FILES 不一致：\n"
        f"  实际 import（src/ 与 scripts/ 启动链）：{sorted(actual)}\n"
        f"  ROOT_FILES 声明：{sorted(declared)}\n"
        "漏声明会让安装包缺根模块；多声明则是清单过期。两边都要改齐。"
    )


def test_root_level_payload_modules_exist_and_import():
    """``ROOT_FILES`` 声明的每个根级模块都必须真实存在且能 import。

    对账用例有盲区：若有人把 ``error_types.py`` 改名/删掉，同时把
    ``ROOT_FILES`` 里那一行也删了，两边会一起变空而静默全绿。这里不看
    清单是不是「看起来对齐」，而是按声明落到文件：缺文件要点名是哪个模块，
    import 失败要贴异常，避免装到用户机器才发现。
    """
    failures: List[str] = []
    for name in sorted(_payload_root_py_modules()):
        path = _REPO_ROOT / f"{name}.py"
        if not path.is_file():
            failures.append(
                f"{name}: ROOT_FILES 声明了 {name}.py，但仓库根不存在该文件"
            )
            continue
        guard = f"_root_payload_guard_{name}"
        spec = importlib.util.spec_from_file_location(guard, path)
        if spec is None or spec.loader is None:
            failures.append(f"{name}: 无法为 {path} 构造 import spec")
            continue
        module = importlib.util.module_from_spec(spec)
        sys.modules[guard] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:  # noqa: BLE001 —— 任何导入失败都意味着安装即崩
            failures.append(
                f"{name}: import {path} 失败 —— {type(exc).__name__}: {exc}"
            )
        finally:
            sys.modules.pop(guard, None)
    assert not failures, (
        "ROOT_FILES 里的根级模块在实际仓库中不可用（安装版会在启动时缺失）：\n"
        + "\n".join(failures)
    )
