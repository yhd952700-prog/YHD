"""开源登记册 `used` 字段的机械一致性护栏（2026-09-13）。

背景：`oss-registry.yaml` 的 `used` 字段初版由**朴素扫描**得出，只认顶层 `import`，
于是把 `mem0ai` / `langgraph` / `langfuse` 三个**受保护导入**（写在
`try: ... except ImportError:` 里、缺失时优雅降级）误判为「src/ 零引用（假能力）」。
这三项实际都有引用，误判会把读者引向错误的「要么接线要么移除」结论。

本护栏用与运行时一致的正则（**允许任意缩进**）重算引用，钉死两件事：

1. `used: no` 的组件在 `src/` 里**必须真的没有任何导入** —— 这正是能机械判定、
   且初版出错的方向；
2. `used` 取值必须落在文档化的枚举内。

**刻意不做**「`used: yes` 必须能在 src/ 找到导入」这条断言：`alembic` / `pytest` 等
是经配置或测试链使用的项目级依赖，不在 `src/` 里 import，硬断言会误伤。
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
_REGISTRY = _REPO_ROOT / "oss-registry.yaml"

#: 允许任意缩进 —— 初版扫描漏掉 try 块内导入的根因就是只匹配了行首 import。
_IMPORT_RE = re.compile(r"^\s*(?:import|from)\s+([A-Za-z_][\w.]*)")

_USED_ENUM = {"yes", "partial", "guarded", "no"}


def _src_import_roots():
    """返回 src/ 下所有被导入的顶层模块名（含 try 块内的受保护导入）。"""
    roots = set()
    for path in _SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            m = _IMPORT_RE.match(line)
            if m:
                roots.add(m.group(1).split(".")[0])
    return roots


def _components():
    assert _REGISTRY.is_file(), f"登记册缺失：{_REGISTRY}"
    data = yaml.safe_load(_REGISTRY.read_text(encoding="utf-8"))
    return data.get("components", [])


def test_used_no_components_are_really_unreferenced():
    """`used: no` 的组件必须真的零引用，否则就是受保护导入被漏算。"""
    imports = _src_import_roots()
    offenders = []
    for comp in _components():
        if str(comp.get("used", "")).lower() != "no":
            continue
        mod = (comp.get("import_name") or comp["name"]).split(".")[0]
        if mod in imports:
            offenders.append(f"{comp['name']} (import {mod})")
    assert not offenders, (
        "登记册把以下组件标为 `used: no`，但 src/ 里确有导入（大概率是写在 "
        f"try/except 里的受保护导入被漏算）：{offenders}。请改为 guarded / partial / yes。"
    )


def test_used_values_are_from_the_documented_enum():
    bad = {
        comp["name"]: comp.get("used")
        for comp in _components()
        if str(comp.get("used", "")).lower() not in _USED_ENUM
    }
    assert not bad, f"used 取值不在文档化枚举 {sorted(_USED_ENUM)} 内：{bad}"


def test_scanner_detects_guarded_imports():
    """元护栏：证明扫描器不是空转 —— 它必须能看见 ``try`` 块内的受保护导入。

    若这条挂了，说明 ``_src_import_roots`` 退化成了「只认顶层 import」，
    那么上面那条 ``used: no`` 断言就会变成永远通过的**假护栏** —— 这正是
    2026-09-13 初版把 mem0 / langgraph / langfuse 误判为「零引用」的同款错误。
    """
    roots = _src_import_roots()
    assert "langgraph" in roots, "扫描器看不到 src/ai/langgraph_workflow.py 里的受保护导入"
    assert "mem0" in roots, "扫描器看不到 src/knowledge/memory.py 里的受保护导入"
    assert "langfuse" in roots, "扫描器看不到 src/adapters/observability 里的受保护导入"
