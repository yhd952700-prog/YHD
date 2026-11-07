#!/usr/bin/env python
"""构建/冒烟前置环境闸。

构建机与开发机共用一台机器时，残留的 ``LIUHAO_*`` / ``AUDIT_DB_*`` 环境变量会
把安装版/probe 指向源码仓库或开发临时目录，导致"注册写到 A 库、启动读 B 库"
这类静默错位。产物本身没问题，问题在测量环境，所以必须在开跑前中止。

用法::

    python packaging\\env_guard.py --repo <仓库根> [--json]

发现脏变量时打印变量名、值、作用域与清除提示，退出码 2。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

#: 名字匹配这些前缀/精确名的变量都参与检查（大小写不敏感）。
WATCHED_PREFIXES: Tuple[str, ...] = ("LIUHAO_",)
WATCHED_EXACT: Tuple[str, ...] = (
    "AUDIT_DB_PATH",
    "AUDIT_DB_BACKUP_PATH",
    "CONVERSATION_DB_PATH",
    "MEMORY_DB_PATH",
    "PERSONAL_CONTEXT_DB_PATH",
    "WORKSPACE_ROOT",
)

#: 明确点名的关键变量：报告时排在最前，方便一眼看到。
PRIORITY_NAMES: Tuple[str, ...] = (
    "LIUHAO_HUMAN_IDENTITIES_DB",
    "LIUHAO_AUTH_SECRETS_FILE",
    "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY",
    "AUDIT_DB_PATH",
    "LIUHAO_DATA_DIR",
)

_CLEAR_HINT = (
    "清除方法：\n"
    "  当前进程：Remove-Item Env:<变量名>\n"
    "  用户作用域：[Environment]::SetEnvironmentVariable('<变量名>', $null, 'User')\n"
    "  机器作用域：[Environment]::SetEnvironmentVariable('<变量名>', $null, 'Machine')"
)


def is_watched(name: str) -> bool:
    upper = name.upper()
    return upper.startswith(WATCHED_PREFIXES) or upper in WATCHED_EXACT


def _normalise(value: str) -> Optional[Path]:
    text = (value or "").strip().strip('"').strip("'")
    if not text:
        return None
    # 非路径值（例如完整性密钥）没有盘符/分隔符，不参与目录判断。
    if not (len(text) > 2 and text[1] == ":" or text.startswith("\\\\") or "/" in text or "\\" in text):
        return None
    try:
        return Path(text)
    except (OSError, ValueError):
        return None


def _inside(path: Path, root: Path) -> bool:
    try:
        resolved = path.resolve()
        base = root.resolve()
    except (OSError, ValueError):
        return False
    return resolved == base or base in resolved.parents


def dirty_reason(
    value: str,
    repo_root: Path,
    temp_roots: Iterable[Path],
) -> Optional[str]:
    path = _normalise(value)
    if path is None:
        return None
    if _inside(path, repo_root):
        return f"指向源码仓库 {repo_root}"
    for root in temp_roots:
        if _inside(path, root):
            return f"指向开发临时目录 {root}"
    return None


def scan(
    env: Mapping[str, str],
    repo_root: Path,
    temp_roots: Iterable[Path],
    scope: str = "process",
) -> List[Dict[str, str]]:
    findings: List[Dict[str, str]] = []
    roots = list(temp_roots)
    for name, value in env.items():
        if not is_watched(name):
            continue
        reason = dirty_reason(value, repo_root, roots)
        if reason is None:
            continue
        findings.append(
            {"scope": scope, "name": name, "value": value, "reason": reason}
        )
    findings.sort(key=lambda item: (ITEM_ORDER.get(item["name"].upper(), 99), item["name"].upper()))
    return findings


ITEM_ORDER = {name: index for index, name in enumerate(PRIORITY_NAMES)}


def _scopes() -> Dict[str, Mapping[str, str]]:
    scopes: Dict[str, Mapping[str, str]] = {"process": dict(os.environ)}
    if sys.platform == "win32":
        try:
            import winreg
        except ImportError:  # pragma: no cover - Windows only
            return scopes
        for scope, root in (
            ("user", winreg.HKEY_CURRENT_USER),
            ("machine", winreg.HKEY_LOCAL_MACHINE),
        ):
            values: Dict[str, str] = {}
            try:
                with winreg.OpenKey(root, r"Environment") as key:
                    index = 0
                    while True:
                        try:
                            name, value, _ = winreg.EnumValue(key, index)
                        except OSError:
                            break
                        values[name] = str(value)
                        index += 1
            except OSError:
                continue
            scopes[scope] = values
    return scopes


def collect(repo_root: Path, temp_roots: Iterable[Path]) -> List[Dict[str, str]]:
    findings: List[Dict[str, str]] = []
    for scope, env in _scopes().items():
        findings.extend(scan(env, repo_root, temp_roots, scope=scope))
    return findings


def default_temp_roots(repo_root: Path) -> List[Path]:
    roots = [Path(tempfile.gettempdir())]
    for candidate in (Path(r"D:\cache\temp"), repo_root / "dist"):
        if candidate.exists() or candidate == repo_root / "dist":
            roots.append(candidate)
    return roots


def format_findings(findings: List[Dict[str, str]]) -> str:
    lines = [f"[env-guard] 发现 {len(findings)} 个脏环境变量，构建/冒烟必须中止："]
    for item in findings:
        lines.append(
            f"  - {item['name']} [{item['scope']}] = {item['value']}  ({item['reason']})"
        )
    names = sorted({item["name"] for item in findings})
    lines.append(_CLEAR_HINT.replace("<变量名>", names[0] if names else "LIUHAO_xxx"))
    if len(names) > 1:
        lines.append("  其余变量：" + "、".join(names[1:]))
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="构建/冒烟前置环境闸")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo).resolve()
    findings = collect(repo_root, default_temp_roots(repo_root))
    if args.json:
        print(json.dumps(findings, ensure_ascii=False, indent=2))
    elif findings:
        print(format_findings(findings))
    else:
        print("[env-guard] OK：未发现指向源码仓库/开发临时目录的 LIUHAO_*/AUDIT_DB_* 变量")
    return 2 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
