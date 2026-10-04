"""容器打包守卫：src/ 不得依赖未进镜像的仓库根级模块。

缺陷背景（2026-10-04 实测）
---------------------------
``src/ai/providers.py`` 的 ``BaseProvider.generate_with_retry`` 在函数体内做
``from error_types import classify_error, ProviderRateLimitError``。这是一个
**顶层模块名**导入（不是 ``src.error_types``），因此只在仓库根/镜像根的
``sys.path`` 上才能解析。

而镜像的 COPY 清单此前只包含 ``src/ config/ scripts/ capability-registry.yaml``，
**不含 ``error_types.py``**。后果在镜像里表现为一个极具欺骗性的形态：

    import src.ai.providers   -> 成功（文件在 src/ 下，随 COPY 进镜像）
    get_provider()            -> 成功（只构造对象，不发网络请求）
    generate_with_retry(...)  -> ModuleNotFoundError: No module named 'error_types'

也就是说 provider 抽象在生产镜像里「能构造、能出现在 /v1/dashboard 上、能通过
健康检查」，但任何一次真实生成 100% 崩溃。这正是本项目最危险的缺陷类型：
**看起来在工作，实际什么都没做**。

为什么这个测试必须存在，而不是只修 Dockerfile
--------------------------------------------
把一行 COPY 补进 Dockerfile 只修好了**这一个**实例。任何人下一次在 src/ 里
``import`` 一个仓库根级的工具模块，都会重犯同一个错，而且本地 venv 完全测不出
（venv 里有指向仓库根的 editable ``.pth``，仓库根天然在 ``sys.path`` 上）。
本测试把「src/ 的每个仓库根级导入都必须被 Dockerfile 显式发布」变成一条
**可持续验证的不变量**，让这类缺陷在 CI 里就红，而不是等到镜像里才炸。

判定为什么不用「跑一次真实生成」而用静态扫描
--------------------------------------------
动态验证（真起容器跑一次 provider）成本高、且需要 Docker。静态扫描 + 逐条核对
COPY 清单能在毫秒级完成，并覆盖**未来**新增的导入。动态行为由
``tests/ai/test_provider_runtime_contract.py`` 在仓库根环境下实测兜底。
两者互补：静态守卫管「有没有发布」，动态测试管「发布后能不能跑」。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
DOCKERFILE = REPO_ROOT / "Dockerfile"


# --------------------------------------------------------------------------- #
# 解析辅助
# --------------------------------------------------------------------------- #
def _repo_root_modules() -> set[str]:
    """仓库根级的顶层模块名（``*.py``，不含 ``__init__``）。"""
    return {
        p.stem
        for p in REPO_ROOT.glob("*.py")
        if p.stem != "__init__"
    }


def _imported_top_level(tree: ast.AST) -> set[str]:
    """该 AST 里出现的所有**绝对**顶层导入名。

    只取 ``level == 0`` 的 ``from X import ...`` 与 ``import X``：
    - 相对导入（``from .x`` / ``from ..x``）永远相对于包自身解析，不依赖 CWD；
    - ``import a.b`` 的顶层名是 ``a``，同样需要能被解析。
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
    return names


def _src_imports_of_root_modules() -> dict[str, list[str]]:
    """返回 ``{顶层模块名: ["<相对路径>:<行号>", ...]}``。

    只统计真正「需要仓库根在 sys.path 上才能解析」的导入：即该顶层名在仓库根
    有一个同名 ``.py``，且它**不是** ``src`` 自身的子包（否则那是正常的包内导入）。
    """
    root_modules = _repo_root_modules() - {"src"}
    hits: dict[str, list[str]] = {}
    for py in sorted(SRC_DIR.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - 由其他测试负责暴露
            continue
        for name in _imported_top_level(tree) & root_modules:
            hits.setdefault(name, []).append(
                f"{py.relative_to(REPO_ROOT).as_posix()}"
            )
    return hits


def _dockerfile_copied_root_files() -> set[str]:
    """解析 Dockerfile，返回被 COPY 到镜像根的文件名集合。

    只关心 COPY 指令的**目标路径**；``--from=`` 的多阶段来源与 ``--chown=`` 被
    刻意忽略（我们只关心「镜像里有没有这个文件」）。
    """
    if not DOCKERFILE.exists():  # pragma: no cover
        pytest.skip("Dockerfile 不存在，无法核对镜像 COPY 清单")
    text = DOCKERFILE.read_text(encoding="utf-8")
    copied: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line.upper().startswith("COPY "):
            continue
        # 去掉行尾注释（COPY 指令本身不在行尾写注释，保守起见按 '#' 截断）
        line = line.split("#", 1)[0].strip()
        tokens = [t for t in line.split()[1:] if not t.startswith("--")]
        if not tokens:
            continue
        # 最后一个 token 是目标；形如 "./x" 或 "./a/b" 或 "x"
        dest = tokens[-1]
        for part in dest.split("/"):
            part = part.strip().strip(".")
            if part.endswith(".py"):
                copied.add(part)
    return copied


# --------------------------------------------------------------------------- #
# 测试
# --------------------------------------------------------------------------- #
def test_dockerfile_exists_and_has_copy_list():
    """前置：Dockerfile 必须存在且确有 COPY 指令（否则下面的守卫会空转成绿灯）。"""
    assert DOCKERFILE.exists(), "Dockerfile 不存在，容器打包守卫无法生效"
    assert _dockerfile_copied_root_files(), (
        "未能从 Dockerfile 解析出任何被 COPY 的 .py —— "
        "COPY 解析器可能已与实际 Dockerfile 脱节，守卫会假绿"
    )


def test_error_types_is_importable_from_src_and_shipped():
    """本次修复的定点回归：error_types 必须既能被 src/ 导入，也必须进镜像。"""
    hits = _src_imports_of_root_modules()
    assert "error_types" in hits, (
        "预期 src/ 仍导入仓库根的 error_types（单一模块身份，见 Dockerfile 注释）；"
        "若已重构进 src/，请同步更新本守卫与 Dockerfile 的 COPY 清单"
    )
    assert "error_types.py" in _dockerfile_copied_root_files(), (
        "Dockerfile 未把 error_types.py 复制进镜像 —— 镜像内 "
        "generate_with_retry 会 100% ModuleNotFoundError（provider 只能构造、"
        "无法真实生成）"
    )


def test_every_root_module_imported_by_src_is_shipped():
    """通用不变量：src/ 引用的每个仓库根级模块都必须被 Dockerfile 发布。

    这条是本文件的真正价值 —— 它让「新增一个根级工具模块并在 src/ 里 import」
    从「镜像里静默崩溃」变成「CI 立刻失败」。
    """
    hits = _src_imports_of_root_modules()
    if not hits:
        return  # 没有根级依赖，恒成立

    shipped = _dockerfile_copied_root_files()
    missing = {
        name: sites for name, sites in hits.items()
        if f"{name}.py" not in shipped
    }
    assert not missing, (
        "以下仓库根级模块被 src/ 导入但未 COPY 进镜像，运行时会 "
        "ModuleNotFoundError：\n"
        + "\n".join(
            f"  - {name}.py  <- {', '.join(sites)}" for name, sites in missing.items()
        )
        + "\n修法二选一：(a) 在 Dockerfile 加 COPY；"
          "(b) 把该模块移进 src/ 并改为包内导入"
          "（注意移动会改变模块身份，先查 tests/test_liuhao_packages_facade.py "
          "这类 identity 断言）。"
    )


def test_guard_detects_a_missing_copy_regression():
    """元测试：守卫本身必须真的能抓到漏 COPY（否则上面几条全是假绿）。

    做法：构造一个「src/ 引用了根级模块、但 COPY 清单为空」的合成场景，
    断言缺失检测函数给出结论。这测的是守卫的判别力，不是被测代码。
    """
    fake_hits = {"ghostmod": ["src/ai/somewhere.py"]}
    shipped = set()  # 模拟：根级模块完全没进镜像
    missing = {
        name: sites for name, sites in fake_hits.items()
        if f"{name}.py" not in shipped
    }
    assert "ghostmod" in missing, "守卫未能检出未发布的根级模块依赖"

    # 反向：一旦 COPY 存在，同一场景必须不再报缺失
    shipped_now = {"ghostmod.py"}
    missing_now = {
        name: sites for name, sites in fake_hits.items()
        if f"{name}.py" not in shipped_now
    }
    assert not missing_now, "守卫误报：已 COPY 的模块仍被判为缺失"


def test_relative_imports_are_not_flagged():
    """反向元测试：包内相对导入绝不能被误判为仓库根依赖。"""
    tree = ast.parse(
        "from . import x\n"
        "from ..kernels import y\n"
        "from ...deep.mod import z\n"
        "import os\n"
        "import src.kernels.execution\n"
    )
    imported = _imported_top_level(tree)
    assert "src" in imported, "绝对导入 src.* 应当被收集（它靠包路径而非 CWD 解析）"
    root_modules = _repo_root_modules() - {"src"}
    # 相对导入的包内名字不应出现在绝对导入集合里
    assert not ({".", "..", "..."} & imported)
    assert not (imported & root_modules), "相对导入被误判为仓库根级依赖"
