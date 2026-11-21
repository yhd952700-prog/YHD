#!/usr/bin/env python3
"""前端反漂移护栏检查（第二季 V4 落地《前端两代对齐审计》护栏 2/3/4/6 的可执行部分）。

用法（仓库根目录执行）：
    python scripts/check_frontend_guardrails.py all [--strict]
    python scripts/check_frontend_guardrails.py canon|reachability|fingerprint|dom|copy

检查项：
- canon        禁重建断言：console/index.html 引用的主包 chunk 含 ``wb-shell``，
               且 console/theme/ 四文件（双主题 CSS/JS）齐全。任一不满足 = 发布
               前端已被 apps 源码重建覆盖，立即中止。
- reachability 资产可达性：从 index.html 引用图出发按文件名传递可达，列出
               console/assets 下的孤儿文件（--strict 时孤儿非空即失败）。
- fingerprint  指纹对账：用 src/upgrade/console_contract.py 的官方算法重算
               console/ 指纹，与 console/console-contract.json 签章比对。
- dom          DOM 契约对账：主题叠加层用到的每个 .wb-* 选择器类名必须能在
               发布 dist（index.html + assets）文本中找到，缺失 = 叠加层钩子
               已失效（dist 被换代/重建）。
- copy         文案黑名单（范围：console/theme/ 叠加层自有文件——我们亲手写
               的用户可见面）：开发话/内部串不得出现。dist bundle 本体不在
               本项范围（其已知隐藏项由主题层处理，见 FRONTEND-CANON.md）。

退出码：0 = 全部通过；1 = 有检查失败（细节打印在 stdout）。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONSOLE = ROOT / "console"
THEME_FILES = [
    "theme/jarvis-theme.css",
    "theme/jarvis-theme.js",
    "theme/jarvis-future-theme.css",
    "theme/jarvis-future-theme.js",
]

# 文案黑名单（叠加层口径）：内部路径、包管理命令、调试串。
COPY_BLACKLIST = [
    r"pip install",
    r"src/[\w./]+\.py",
    r"localhost",
    r"127\.0\.0\.1",
    r"已登记账号",
    r"会话有效期",
    r"TODO:",
    r"FIXME:",
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def check_canon() -> list[str]:
    problems: list[str] = []
    index = CONSOLE / "index.html"
    if not index.exists():
        return ["console/index.html 不存在"]
    html = _read(index)
    m = re.search(r'src="[^"]*assets/(index-[^"]+\.js)"', html)
    if not m:
        return ["index.html 未引用 assets/index-*.js 主包"]
    main_chunk = CONSOLE / "assets" / m.group(1)
    if not main_chunk.exists():
        problems.append(f"主包 {m.group(1)} 不存在")
    # wb-shell 在 Workbench 懒加载 chunk 里（不在入口包）：断言口径 = 入口
    # 引用图的可达资产集内含 wb-shell 标记，即发布前端仍是 wb 世代。
    _, seen = _reachability()
    if not any("wb-shell" in _read(CONSOLE / a) for a in seen):
        problems.append(
            "可达资产集内找不到 wb-shell —— 疑似被 apps 源码重建覆盖（违反正典裁决）"
        )
    for rel in THEME_FILES:
        if not (CONSOLE / rel).exists():
            problems.append(f"主题叠加层文件缺失：console/{rel}")
    return problems


def _reachability() -> tuple[set[str], set[str]]:
    def norm(p: str) -> str:
        return p.lstrip("/").removeprefix("./")

    html = _read(CONSOLE / "index.html")
    refs = {
        norm(m)
        for m in re.findall(r'(?:src|href)="([^"]+)"', html)
        if "assets/" in m or "theme/" in m
    }
    assets = {f"assets/{p.name}" for p in (CONSOLE / "assets").iterdir() if p.is_file()}
    names = {Path(a).name for a in assets}
    graph: dict[str, set[str]] = {}
    for a in assets:
        txt = _read(CONSOLE / a)
        graph[a] = {f"assets/{n}" for n in names if n in txt and f"assets/{n}" != a}
    seen = set(refs) & assets
    stack = list(seen)
    while stack:
        cur = stack.pop()
        for nxt in graph.get(cur, ()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return assets, seen


def check_reachability(strict: bool) -> list[str]:
    assets, seen = _reachability()
    orphans = sorted(assets - seen)
    if orphans:
        listing = "\n".join(f"  孤儿：console/{o}" for o in orphans)
        msg = f"console/assets 有 {len(orphans)} 个不可达文件：\n{listing}"
        return [msg] if strict else [f"（警告）{msg}"]
    return []


def check_fingerprint() -> list[str]:
    sys.path.insert(0, str(ROOT / "src"))
    from upgrade.console_contract import console_fingerprint, console_files_under

    files = {
        str(p.relative_to(ROOT)): p.read_bytes()
        for p in CONSOLE.rglob("*")
        if p.is_file()
    }
    picked = console_files_under(files)
    stamp_path = CONSOLE / "console-contract.json"
    if not stamp_path.exists():
        return ["console/console-contract.json 不存在"]
    stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
    actual = console_fingerprint(picked)
    problems = []
    if stamp.get("console_fingerprint") != actual:
        problems.append(
            "指纹对账不一致：签章 "
            f"{str(stamp.get('console_fingerprint'))[:16]}… ≠ 实际 {actual[:16]}…"
            "（dist 有变更未重新签章，或签章过期；见 FRONTEND-CANON.md 变更流程）"
        )
    if int(stamp.get("console_files") or 0) != len(picked):
        problems.append(
            f"文件数对账不一致：签章 {stamp.get('console_files')} ≠ 实际 {len(picked)}"
        )
    return problems


def theme_selectors() -> set[str]:
    selectors: set[str] = set()
    for rel in THEME_FILES:
        txt = _read(CONSOLE / rel)
        selectors.update(re.findall(r"\.wb-[a-zA-Z0-9_-]+", txt))
    return selectors


def _selector_groups() -> list[set[str]]:
    """把主题层用到的 .wb-* 选择器按「组」收集：同一逗号列表里的备选选择器
    算一组（主选命中 dist 即算钩子成立，备选是给旧标记变体的保险，不算断钩）。
    JS 里 querySelector/closest/matches 的字符串参数、CSS 规则的选择器列表
    各成一组；其余单独出现的类名各自成组。"""
    groups: list[set[str]] = []
    grouped: set[str] = set()
    for rel in THEME_FILES:
        txt = _read(CONSOLE / rel)
        for m in re.finditer(r"""(?:querySelector(?:All)?|closest|matches)\(\s*["']([^"']+)["']""", txt):
            classes = set(re.findall(r"\.wb-[a-zA-Z0-9_-]+", m.group(1)))
            if classes:
                groups.append(classes)
                grouped.update(classes)
        if rel.endswith(".css"):
            for m in re.finditer(r"([^{}]+)\{", txt):
                classes = set(re.findall(r"\.wb-[a-zA-Z0-9_-]+", m.group(1)))
                if len(classes) > 1:
                    groups.append(classes)
                    grouped.update(classes)
    for sel in theme_selectors() - grouped:
        groups.append({sel})
    return groups


def check_dom() -> list[str]:
    corpus = _read(CONSOLE / "index.html")
    for p in sorted((CONSOLE / "assets").iterdir()):
        if p.is_file():
            corpus += _read(p)
    broken = []
    for group in _selector_groups():
        if not any(s[1:] in corpus for s in group):
            broken.append(" / ".join(sorted(group)))
    if broken:
        return [
            "DOM 契约对账失败：以下选择器组在发布 dist 中全军覆没（钩子失效）：\n"
            + "\n".join(f"  {g}" for g in sorted(broken))
        ]
    return []


# 检测/替换上下文标记：黑名单串出现在「找出来删掉/换掉」的代码里（或注释里
# 说明该修复时）是正当用途，不算用户可见文案。
_DETECTOR_MARKERS = ("indexOf", "includes(", ".test(", ".match(", ".replace(", "RegExp")


def _is_detector_context(line: str) -> bool:
    stripped = line.strip()
    if stripped.startswith(("//", "/*", "*")):
        return True
    return any(marker in line for marker in _DETECTOR_MARKERS)


def check_copy() -> list[str]:
    problems = []
    for rel in THEME_FILES:
        lines = _read(CONSOLE / rel).splitlines()
        in_block_comment = False
        for lineno, line in enumerate(lines, start=1):
            stripped = line.strip()
            if in_block_comment:
                if "*/" in line:
                    in_block_comment = False
                continue
            if stripped.startswith("/*"):
                if "*/" not in stripped[2:]:
                    in_block_comment = True
                continue
            if _is_detector_context(line):
                continue
            for pattern in COPY_BLACKLIST:
                if re.search(pattern, line):
                    problems.append(f"console/{rel}:{lineno} 命中文案黑名单 /{pattern}/")
    return problems


def main(argv: list[str]) -> int:
    strict = "--strict" in argv
    args = [a for a in argv if not a.startswith("--")]
    which = args[0] if args else "all"
    checks = {
        "canon": lambda: check_canon(),
        "reachability": lambda: check_reachability(strict),
        "fingerprint": lambda: check_fingerprint(),
        "dom": lambda: check_dom(),
        "copy": lambda: check_copy(),
    }
    names = list(checks) if which == "all" else [which]
    failed = False
    for name in names:
        problems = checks[name]()
        hard = [p for p in problems if not p.startswith("（警告）")]
        for p in problems:
            print(f"[{name}] {p}")
        if hard:
            failed = True
        print(f"[{name}] {'FAIL' if hard else 'PASS'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
