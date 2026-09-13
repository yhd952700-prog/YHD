"""src/adapters 运行时不生效的接线护栏。

Round 82 评审核实：``src/adapters/`` 整个目录在运行时不被任何 src/ 模块消费，
只有 ``tests/test_mcp_adapter.py`` 引用它（且只引用 mcp 子包）。网关与 AI 层
实际使用的权威可观测性实现位于顶层 ``src/observability/``（见
``src/gateway/main.py:23`` 的 ``from ..observability.tracing import get_tracer``）。

``src/adapters/observability/`` 是一套与权威实现**意图重复、但公开接口完全不同**
的遗留实现（自定义 correlation-id / Span vs OpenTelemetry 封装），已被取代且全程
未被运行时走到。

这一组护栏保证：

* MCP 适配器保持 ``not-wired`` 声明（真正接线需引入外部 MCP server 与沙箱，
  超出本轮范围；详见 mcp_adapter.py 顶部安全前置条件）；
* 网关装配处不得把被取代的 ``src/adapters/observability`` 接进去；
* 被标为未接线的 src/adapters 模块不得被 ``src/gateway/`` 或 ``src/ai/``
  偷偷 import（防止"声明未接线、事实已漂移"）。

全部断言都用读源码文本 / AST 的方式，不依赖运行时 import src.adapters。
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
MCP_ADAPTER_PY = REPO_ROOT / "src" / "adapters" / "mcp" / "mcp_adapter.py"
GATEWAY_MAIN_PY = REPO_ROOT / "src" / "gateway" / "main.py"
ADAPTERS_DIR = REPO_ROOT / "src" / "adapters"
GATEWAY_DIR = REPO_ROOT / "src" / "gateway"
AI_DIR = REPO_ROOT / "src" / "ai"

#: 任何 src/ 运行时模块都不应出现的 import 前缀（未接线的遗留实现）。
_ADAPTERS_IMPORT_RE = re.compile(
    r"(?:^|\b)(?:import|from)\s+.*\b(?:src\.adapters|adapters\.mcp|adapters\.observability)\b"
)


def _read(p: pathlib.Path) -> str:
    return p.read_text(encoding="utf-8")


class TestMCPWiring:
    def test_mcp_adapter_declares_not_wired(self):
        """MCP 适配器必须保持 not-wired；一旦有人接线须同步改声明与测试。"""
        tree = ast.parse(_read(MCP_ADAPTER_PY))
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "MCP_WIRING_STATUS":
                        assert node.value.value == "not-wired", (
                            "MCP_WIRING_STATUS 不再是 'not-wired'；"
                            "若确实已接线，请同步更新本测试与模块 docstring"
                        )
                        return
        raise AssertionError("mcp_adapter.py 未找到 MCP_WIRING_STATUS 常量")


class TestGatewayUsesAuthoritativeObservability:
    def test_gateway_imports_authoritative_tracing(self):
        """网关装配处必须 import 权威的 src/observability/tracing。"""
        source = _read(GATEWAY_MAIN_PY)
        assert "from ..observability.tracing import get_tracer" in source, (
            "gateway/main.py 未从权威 src/observability 导入 get_tracer"
        )

    def test_gateway_does_not_import_adapters_observability(self):
        """源码护栏：网关不得 import 被取代的 src/adapters/observability。"""
        source = _read(GATEWAY_MAIN_PY)
        assert "adapters.observability" not in source, (
            "gateway/main.py 不应 import src/adapters/observability"
            "（那是被取代的遗留实现，权威在 src/observability）"
        )
        assert "src.adapters" not in source, (
            "gateway/main.py 不应 import src.adapters"
        )


class TestAdaptersNotImportedByRuntime:
    @pytest.mark.parametrize("runtime_dir", [GATEWAY_DIR, AI_DIR])
    def test_runtime_does_not_import_adapters_modules(self, runtime_dir):
        """src/gateway/ 与 src/ai/ 不得 import src/adapters 下的任何模块。"""
        offenders = []
        for py in sorted(runtime_dir.rglob("*.py")):
            for lineno, line in enumerate(_read(py).splitlines(), 1):
                if _ADAPTERS_IMPORT_RE.search(line):
                    offenders.append(
                        f"{py.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}"
                    )
        assert not offenders, (
            "如下 src/ 运行时文件引用了未接线的 src/adapters 模块，"
            "声明与事实漂移：\n" + "\n".join(offenders)
        )
