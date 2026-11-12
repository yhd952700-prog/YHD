#!/usr/bin/env python3
"""文档命令块门禁：用户「照抄文档里的命令」能不能真的跑起来。

检查四类问题（对应照抄后最常见的四种失败）：
  1. 路径不存在 —— 命令引用的脚本/文件既不在仓库里，也不是安装版固定布局；
  2. 参数/子命令失效 —— `python -m src.cli backup create --foo` 里的 --foo、
     或 `python -m src.desktop autostart` 的名字，在源码 argparse 定义里找不到；
  3. 占位符没标注 —— 长得像真实密钥（sk- 后接长串）却没写成 sk-... / YOUR_KEY；
  4. 引号与空格 —— 含空格或中文的路径没加引号，照抄会被 shell 拆成两个参数。

只做静态检查，**不执行任何命令**（可进 CI，不会写注册表、不会起进程）。
干跑（--help / --dry-run）由人按 `--explain` 输出的建议单独执行。

用法：
  python scripts/check_docs_commands.py
  python scripts/check_docs_commands.py --list       # 列出提取到的全部命令
  python scripts/check_docs_commands.py --explain    # 每条命令的干跑能力与原因
  python scripts/check_docs_commands.py --verbose

退出码：0 = 通过；1 = 有失败项。
"""

from __future__ import annotations

import argparse
import functools
import re
import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: 仓库根上“用户会照着敲命令”的文档（docs/ 之外的部分）。
USER_DOC_FILES = [
    ROOT / "README.md",
    ROOT / "docs" / "USER-GUIDE.md",
    ROOT / "docs" / "END-USER-GUIDE.md",
    ROOT / "docs" / "INSTALL-WINDOWS.md",
    ROOT / "docs" / "TROUBLESHOOTING-USER-20261108.md",
    ROOT / "docs" / "faq.md",
    ROOT / "docs" / "mobile-access.md",
    ROOT / "docs" / "release-announcement.md",
    ROOT / "installer" / "assets" / "BEFORE-INSTALL.txt",
]

#: 全量覆盖：docs/ 下所有 Markdown 都扫。审计/归档类历史命令必须逐条列进
#: EXEMPT_DOCS 并写理由，不允许用目录前缀默认跳过。
DOC_ROOT = ROOT / "docs"

#: 豁免白名单：文件路径（相对仓库根，正斜杠）→ 理由。只豁免“历史证据”类文档；
#: 其余文档的命令块一律参与检查。新增豁免必须逐条写文件，不许用目录前缀默认跳过。
EXEMPT_DOCS: dict[str, str] = {
    # —— 审计报告快照：命令是当时（2026-11-08）现场执行的记录，保留作证据，不当作可照抄指令。
    "docs/audit-account-matrix-20261108.md": "账号矩阵审计快照，命令为当时现场记录",
    "docs/audit-api-contract-20261108.md": "接口契约审计快照，命令为当时现场记录",
    "docs/audit-copywriting-20261108.md": "文案审计快照，命令为当时现场记录",
    "docs/audit-data-egress-20261108.md": "数据外发审计快照，命令为当时现场记录",
    "docs/audit-data-layout-20261108.md": "数据目录布局审计快照，命令为当时现场记录",
    "docs/audit-docs-vs-ui-20261108.md": "文档↔UI 一致性审计快照，命令为当时现场记录",
    "docs/audit-error-copy-20261108.md": "错误文案审计快照，命令为当时现场记录",
    "docs/audit-first-journey-20261108.md": "首启旅程审计快照，命令为当时现场记录",
    "docs/audit-first-run-disclosure-20261108.md": "首启披露审计快照，命令为当时现场记录",
    "docs/audit-install-fallback-20261108.md": "安装回退审计快照，命令为当时现场记录",
    "docs/audit-local-attack-surface-20261108.md": "本机攻击面审计快照，命令为当时现场记录",
    "docs/audit-log-leakage-20261108.md": "日志泄漏审计快照，命令为当时现场记录",
    "docs/audit-package-content-20261108.md": "安装包内容审计快照，命令为当时现场记录",
    "docs/audit-perf-baseline-20261108.md": "性能基线审计快照，命令为当时现场记录",
    "docs/audit-provider-drift-20261108.md": "提供商漂移审计快照，命令为当时现场记录",
    "docs/audit-size-startup-20261108.md": "体积/启动审计快照，命令为当时现场记录",
    "docs/audit-user-journey-20261108.md": "用户旅程审计快照，命令为当时现场记录",
    "docs/audit-windows-compat-20261108.md": "Windows 兼容审计快照，命令为当时现场记录",
    "docs/a11y-keyboard-audit-20261108/report.md": "键盘可达性审计快照，命令为当时现场记录",
    "docs/a11y-sr-hc-keyboard-20261108/report.md": "读屏/高对比/键盘审计快照，命令为当时现场记录",
    "docs/ui-audit-20261106/report.md": "UI 审计快照（2026-11-06），命令为当时现场记录",
    "docs/WINDOWS-COMPAT-DPI-AUDIT-2026-11-08.md": "DPI/兼容性审计快照，命令为当时现场记录",
    "docs/build-freeze-readiness-20261108.md": "构建冻结就绪度审计快照，保留当时探测命令与结果",
    # —— 归档：历史阶段计划/决策原文，命令对应当时尚未落地的文件。
    "docs/archive/EXECUTION_AND_DECISIONS.md": "阶段执行与决策归档，命令是当时快照",
    "docs/archive/PHASE_ACCEPTANCE_REPORTS.md": "阶段验收报告归档，命令是当时快照",
    "docs/archive/PLANS_ROADMAP.md": "历史路线图归档，命令指向当时计划的测试文件",
    "docs/archive/Y1_AUDIT_REPORTS.md": "Y1 审计报告归档，命令是当时快照",
    "docs/archive/drafts/ARCHITECTURE-v3.0-draft.md": "架构草案归档，命令是示意",
    "docs/archive/drafts/PRD-v3.0-draft.md": "PRD 草案归档，命令是示意",
    "docs/archive/drafts/UIUX-v3.0-draft.md": "UI/UX 草案归档，命令是示意",
    # —— 证据快照：实测记录，命令与输出必须保持原样才能复核。
    "docs/evidence/c7-performance-baseline-2026-11-06.md": "C7 性能基线证据，命令与输出需保持原样",
    "docs/evidence/c9-console-payload-2026-11-06.md": "C9 控制台载荷证据，命令与输出需保持原样",
    "docs/evidence/c9-installed-upgrade-2026-11-06.md": "C9 安装版升级证据，命令与输出需保持原样",
    "docs/evidence/cross-verify-3fa88be0.md": "交叉验证证据（commit 3fa88be0），命令与输出需保持原样",
    "docs/evidence/fault-surfaces-2026-11-06.md": "故障面证据，命令与输出需保持原样",
    "docs/evidence/hc01-pending-decisions-2026-11-06.md": "HC-01 待决证据，命令与输出需保持原样",
    "docs/evidence/log-only-troubleshooting-drill.md": "仅日志排障演练证据，命令与输出需保持原样",
    "docs/evidence/regression-baseline-coverage-2026-11-06.md": "回归基线覆盖证据，命令与输出需保持原样",
    # —— 长跑/CI 执行记录：历史时间点的运行快照。
    "docs/operations/soak-report-2026-11-07.md": "2026-11-07 长跑报告快照，命令是当时记录",
    "docs/operations/soak-report-2026-11-08.md": "2026-11-08 长跑报告快照，命令是当时记录",
    "docs/ci/2026-11-04-execution-record.md": "2026-11-04 CI 执行记录，命令是当时记录",
    # —— 研究记录：命令指向本机临时探针脚本，仓库不保存这些脚本。
    "docs/autonomous/ADR-audit-storage-generation-2.md": "审计存储 ADR 的研究记录，探针脚本在本机临时目录",
    "docs/autonomous/UNKNOWN-TO-OWNER.md": "待决问题研究记录，探针脚本在本机临时目录",
}

#: 安装版文档：真实用户机器上没有把 Python 加进 PATH，裸 `python` 会失败。
INSTALLED_DOCS = {
    ROOT / "docs" / "END-USER-GUIDE.md",
    ROOT / "installer" / "assets" / "BEFORE-INSTALL.txt",
}

COMMAND_LANGS = {
    "bash",
    "sh",
    "shell",
    "zsh",
    "powershell",
    "ps1",
    "pwsh",
    "cmd",
    "bat",
    "batch",
    "console",
    "dos",
    "terminal",
    "windows",
}

#: 安装版 / 运行期目录：仓库里不存在是正常的（打包后才生成）。
INSTALLED_ONLY_PREFIXES = {
    "runtime/": "安装版自带的离线 Python 运行时",
    "dist/": "构建产物目录",
    "console/": "安装版驾驶舱静态资源",
    "node_modules/": "前端依赖，npm install 后生成",
    ".venv/": "开发虚拟环境，按需创建",
    "%localappdata%/": "用户级安装/数据目录",
}
INSTALLED_ONLY_NAMES = {
    "liuhao.bat": "安装版生成的控制台入口（packaging/stage_payload.py）",
    "d:\\liuhao": "安装版默认程序目录",
    "d:\\liuhaodata": "安装版默认数据目录",
    "d:\\liuhaobackups": "安装版默认备份目录",
}

#: 外部部署模型（仓库里没有这些目录）：只标注“未校验”，不判红。
EXTERNAL_PROFILE_PREFIXES = {
    "charts/": "Helm chart 属外部部署模型，本仓库未包含",
    "solutions/": "示例方案目录，按需创建",
    "plugins/": "插件目录，示例值",
}

#: 未标注语言（```）的代码块：只有以这些命令前缀开头的行才算命令，避免把输出当命令。
UNTAGGED_PREFIXES = (
    "python",
    "py ",
    ".venv",
    "runtime\\python",
    "pip",
    "curl",
    "powershell",
    "pwsh",
    "set ",
    "$env:",
    "export ",
    "cd ",
    "node ",
    "npm ",
    "git ",
    "docker",
    "kubectl",
    "helm",
    "iscc",
    "netsh",
    "ipconfig",
    "winget",
    "ollama",
    "liuhao ",
    "alembic",
    "cargo",
    "make ",
)

PROMPT_RE = re.compile(r"^(?:\$|PS[^>]*>|>|[A-Za-z]:\\[^>]*>)\s+")
PY_RE = re.compile(
    r"^(?:(?P<venv>\.venv[\\/]Scripts[\\/]python(?:w)?\.exe)"
    r"|(?P<runtime>runtime[\\/]python(?:w)?\.exe)"
    r"|(?P<bare>python(?:3)?(?:\.exe)?|\bpy(?: -3\.\d+)?))\b\s*(?P<rest>.*)$",
    re.I,
)
FLAG_RE = re.compile(r"^(--?[A-Za-z0-9][\w-]*)(?:=.*)?$")
ARGUMENT_RE = re.compile(r"(\w+)\.add_argument\(\s*[\"'](--?[\w-]+)[\"']", re.S)
PARSER_RE = re.compile(
    r"(?:(\w+)\s*=\s*)?(\w+)\.add_parser\(\s*[\"']([^\"']+)[\"']([^)]*)", re.S
)
SUBPARSERS_RE = re.compile(r"(\w+)\s*=\s*(\w+)\.add_subparsers\(", re.S)
GROUP_RE = re.compile(
    r"(\w+)\s*=\s*(\w+)\.add_(?:mutually_exclusive_|argument_)?group\(", re.S
)
PARENTS_RE = re.compile(r"parents\s*=\s*\[([^\]]*)\]")
SK_RE = re.compile(r"\b(?:sk-[A-Za-z0-9_.\-]{6,}|ghp_[A-Za-z0-9]{10,}|xoxb-[A-Za-z0-9-]{10,}|AKIA[A-Z0-9]{12,})")
PLACEHOLDER_HINT_RE = re.compile(r"(\.\.\.|…|<|>|your|你的|xxx|\*{2,}|x{4,}|X{4,})", re.I)
PATH_TOKEN_RE = re.compile(r"^(?:[\w.~-]+[\\/])+[\w.*-]+$|^[A-Za-z]:[\\/].+$")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
INLINE_RE = re.compile(r"(?<!`)`([^`\n]{2,160})`(?!`)")
INLINE_PREFIXES = (
    "python ",
    "python -m ",
    ".venv\\Scripts\\python",
    "runtime\\python",
    "liuhao ",
    "scripts\\",
    "liuhao.bat",
    "powershell ",
    "curl ",
)
#: 只有这些参数能保证“只打印帮助、不改状态”。
#: 注意 `--version` 在子命令里可能是“要安装的插件版本”，不能当干跑。
DRY_RUN_SAFE_ARGS = {"--help", "-h", "--dry-run"}
#: 这些子命令会改系统/写数据，只允许静态校验，不给干跑建议。
MUTATING_TOKENS = {
    "backup",
    "create",
    "restore",
    "upgrade",
    "enable",
    "disable",
    "uninstall",
    "install",
    "clear",
    "rollback",
    "apply",
    "migrate",
}

#: 明确会写数据/改系统的子命令（命令前两个 token 的组合）。
#: 文档里出现这些命令，必须在附近写明“会直接改动数据，执行前先备份”，否则报黄。
DESTRUCTIVE_COMMANDS = {
    ("backup", "create"),
    ("backup", "restore"),
    ("backup", "prune"),
    ("memory", "clear"),
    ("memory", "import"),
    ("plugin", "install"),
    ("plugin", "enable"),
    ("plugin", "disable"),
    ("plugin", "uninstall"),
    ("plugin", "upgrade"),
    ("agent", "create"),
    ("agent", "delete"),
    ("org", "remove"),
}

#: 文档里的警示语（任一命中即视为已标注）。
DESTRUCTIVE_WARNING_RE = re.compile(
    r"(执行前先备份|先备份|会直接改动数据|会写入数据|不可恢复|会改动数据|备份后再)",
)
#: 警示语可能写在命令上一行注释里，所以按行窗口判定。
DESTRUCTIVE_CONTEXT_LINES = 3


@dataclass
class Site:
    doc: Path
    section: str
    line: int
    text: str
    origin: str  # fence | inline


@dataclass
class Issue:
    doc: Path
    section: str
    line: int
    command: str
    problem: str
    fix: str


@dataclass
class Node:
    name: str
    flags: set[str] = field(default_factory=set)
    required: set[str] = field(default_factory=set)
    children: dict[str, "Node"] = field(default_factory=dict)
    parents: list[str] = field(default_factory=list)
    parent_vars: list[str] = field(default_factory=list)


@functools.lru_cache(maxsize=None)
def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, UnicodeDecodeError):
        return ""


def doc_targets() -> tuple[list[Path], list[tuple[Path, str]]]:
    """(要检查的文档, 豁免的文档及理由)。豁免只来自显式白名单。"""
    targets: list[Path] = []
    exempted: list[tuple[Path, str]] = []
    candidates = list(USER_DOC_FILES)
    if DOC_ROOT.exists():
        candidates.extend(sorted(DOC_ROOT.rglob("*.md")))
    for path in candidates:
        if not path.exists():
            continue
        key = rel(path)
        if key in EXEMPT_DOCS:
            exempted.append((path, EXEMPT_DOCS[key]))
            continue
        if path not in targets:
            targets.append(path)
    return targets, exempted


def build_section_map(lines: list[str]) -> list[str]:
    """每一行所属的小节名。只认代码块外的标题，避免把 `# 安装插件` 这类命令注释当标题。"""
    sections: list[str] = []
    current = lines[0].strip() if lines else "（无标题）"
    in_fence = False
    for line in lines:
        if line.startswith("```"):
            in_fence = not in_fence
        elif not in_fence:
            match = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
            if match:
                current = match.group(1).strip()
        sections.append(current)
    return sections


def extract_sites(doc: Path) -> list[Site]:
    text = read_text(doc)
    if not text:
        return []
    lines = text.splitlines()
    sections = build_section_map(lines)
    sites: list[Site] = []
    fence_ranges: list[tuple[int, int]] = []
    in_fence = False
    fence_start = 0
    fence_lang = ""
    pending: tuple[str, int, str] | None = None  # 反斜杠续行：拼成完整命令再判定

    for index, line in enumerate(lines):
        if line.startswith("```"):
            if in_fence:
                in_fence = False
                fence_ranges.append((fence_start, index))
                if pending is not None:
                    sites.append(Site(doc, pending[2], pending[1] + 1, pending[0], "fence"))
                    pending = None
            else:
                in_fence = True
                fence_start = index
                fence_lang = line[3:].strip().lower().split()[0] if line[3:].strip() else ""
            continue
        if not in_fence:
            continue
        if fence_lang not in COMMAND_LANGS:
            if fence_lang != "" or not line.strip().lower().startswith(UNTAGGED_PREFIXES):
                continue
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "REM ", "::", "//", ";")):
            continue
        if stripped.startswith("```"):
            continue
        if pending is None:
            text_line, start_line, section = stripped, index, sections[index]
        else:
            text_line, start_line, section = pending[0] + " " + stripped, pending[1], pending[2]
            pending = None
        if fence_lang in COMMAND_LANGS and line.rstrip().endswith("\\"):
            pending = (text_line, start_line, section)
            continue
        sites.append(Site(doc, section, start_line + 1, text_line, "fence"))

    for match in INLINE_RE.finditer(text):
        span = match.group(1).strip()
        if span.lower().startswith(INLINE_PREFIXES):
            offset = text[: match.start()].count("\n")
            if any(start <= offset <= end for start, end in fence_ranges):
                continue
            sites.append(
                Site(doc, sections[offset], offset + 1, span, "inline")
            )
    return sites


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def tokenize(line: str) -> list[str]:
    try:
        raw = shlex.split(line, posix=False)
    except ValueError:
        raw = line.split()
    return [item.strip("\"'") for item in raw]


class CliGraph:
    """从 argparse 源码里抽出 子命令树 + 每个节点的参数名。"""

    def __init__(self, names: list[str]):
        self.root = Node("")
        self.names = names
        self.var_nodes: dict[str, Node] = {}
        self.shared_flags: dict[str, set[str]] = {}  # parents=[common] 这类非子命令解析器
        self.derived: dict[str, str] = {}  # group = source.add_..._group() 的父子关系

    def add_file(self, path: Path) -> None:
        text = read_text(path)
        if "add_parser" not in text:
            return
        parsers: list[Node] = []
        containers: dict[str, str] = {}
        for match in PARSER_RE.finditer(text):
            var, parent_var, name, tail = match.groups()
            node = Node(name)
            parents = PARENTS_RE.search(tail)
            if parents:
                node.parents = [part.strip() for part in parents.group(1).split(",") if part.strip()]
            node.parent_vars.append(parent_var)
            setattr(node, "var", var)  # 未赋值给变量的 add_parser 为 None（如 upgrade 的 check）
            parsers.append(node)
        for match in SUBPARSERS_RE.finditer(text):
            child_var, parent_var = match.groups()
            containers[child_var] = parent_var
        for match in GROUP_RE.finditer(text):
            child_var, parent_var = match.groups()
            self.derived[child_var] = parent_var

        node_by_var = {
            getattr(node, "var"): node for node in parsers if getattr(node, "var")
        }
        self.var_nodes.update(node_by_var)
        for node in parsers:
            parent_var = node.parent_vars[0]
            owner_var = containers.get(parent_var)
            owner = node_by_var.get(owner_var) if owner_var else None
            if owner is not None:
                owner.children.setdefault(node.name, node)
            else:
                self.root.children.setdefault(node.name, node)

        for var, flag, chunk in iter_argument_chunks(text):
            node = node_by_var.get(var)
            if node is not None:
                node.flags.add(flag)
                if "required=True" in chunk:
                    node.required.add(flag)
            else:
                self.shared_flags.setdefault(var, set()).add(flag)

        # 参数常常由辅助函数注入：def _add_app_args(parser): parser.add_argument(...)
        # 再把 _add_app_args(app_args) 的调用结果挂回真实解析器变量。
        for def_match in re.finditer(r"def\s+(\w+)\(\s*(\w+)[^)]*\)\s*(?:->[^:]+)?:", text):
            func_name, param = def_match.groups()
            rest = text[def_match.end():]
            next_def = re.search(r"\ndef\s", rest)
            body = rest[: next_def.start()] if next_def else rest
            helper_flags = {
                match.group(1)
                for match in re.finditer(
                    rf"\b{re.escape(param)}\.add_argument\(\s*[\"'](--?[\w-]+)[\"']", body
                )
            }
            if not helper_flags:
                continue
            for call in re.finditer(rf"\b{re.escape(func_name)}\(\s*(\w+)\s*\)", text):
                arg = call.group(1)
                target = node_by_var.get(arg)
                if target is not None:
                    target.flags |= helper_flags
                else:
                    self.shared_flags.setdefault(arg, set()).update(helper_flags)

    def flags_for(self, node: Node, seen: set[int] | None = None) -> set[str]:
        seen = seen or set()
        if id(node) in seen:
            return set()
        seen.add(id(node))
        flags = set(node.flags)
        for parent_name in node.parents:
            parent = self.var_nodes.get(parent_name)
            if parent is not None:
                flags |= self.flags_for(parent, seen)
            flags |= self.shared_for(parent_name)
        return flags

    def required_for(self, node: Node, seen: set[int] | None = None) -> set[str]:
        seen = seen or set()
        if id(node) in seen:
            return set()
        seen.add(id(node))
        required = set(node.required)
        for parent_name in node.parents:
            parent = self.var_nodes.get(parent_name)
            if parent is not None:
                required |= self.required_for(parent, seen)
        return required

    def shared_for(self, name: str) -> set[str]:
        flags = set(self.shared_flags.get(name, set()))
        for child, parent in self.derived.items():
            if parent == name:
                flags |= self.shared_for(child)
        return flags


def iter_argument_chunks(text: str) -> list[tuple[str, str, str]]:
    """(解析器变量, 参数名, 该次 add_argument 调用的前 400 字符) —— 用于判 required=True。"""
    result: list[tuple[str, str, str]] = []
    for chunk in re.split(r"(?=\b\w+\.add_argument\()", text):
        match = re.match(r"(\w+)\.add_argument\(\s*[\"'](--?[\w-]+)[\"']", chunk)
        if match:
            result.append((match.group(1), match.group(2), re.sub(r"\s+", "", chunk[:400])))
    return result


@functools.lru_cache(maxsize=None)
def module_files(module: str) -> list[Path]:
    base = ROOT.joinpath(*module.split("."))
    package = base / "__init__.py"
    if package.exists():
        return sorted([package, *base.glob("*.py")])
    single = base.with_suffix(".py")
    return [single] if single.exists() else []


def module_runnable(module: str) -> bool:
    """`python -m <module>` 能否真的跑起来：包要有 __main__.py，单文件要有 __main__ 守卫。"""
    base = ROOT.joinpath(*module.split("."))
    if (base / "__init__.py").exists():
        return (base / "__main__.py").exists()
    text = read_text(base.with_suffix(".py"))
    return bool(re.search(r"__name__\s*==\s*[\"']__main__[\"']", text))


@functools.lru_cache(maxsize=None)
def build_module_graph(module: str) -> CliGraph | None:
    files = module_files(module)
    if not files:
        return None
    graph = CliGraph([str(path) for path in files])
    for path in files:
        graph.add_file(path)
    if not graph.root.children:
        # 单文件脚本式模块：`python -m src.knowledge.evaluation --provider …`
        graph.root.flags = {
            match.group(2)
            for path in files
            for match in ARGUMENT_RE.finditer(read_text(path))
        } | {flag for flags in graph.shared_flags.values() for flag in flags}
        if not graph.root.flags and not module_runnable(module):
            return None
    return graph


@functools.lru_cache(maxsize=None)
def script_flags(path: Path) -> set[str]:
    text = read_text(path)
    flags = {match.group(2) for match in ARGUMENT_RE.finditer(text)}
    if flags:
        return flags
    # 非 argparse 的脚本（手写 sys.argv / 转发给子进程）：退化为“源码里出现过的 --flag 字面量”。
    return set(re.findall(r"--[A-Za-z][\w-]*", text))


def ps1_params(path: Path) -> set[str]:
    text = read_text(path)
    block = re.search(r"\bparam\s*\(([\s\S]*?)\)", text, re.I)
    scope = block.group(1) if block else text[:2000]
    return {f"-{name}" for name in re.findall(r"\$([A-Za-z_]\w*)", scope)}


def normalize_path(token: str) -> str:
    return token.replace("\\", "/").strip("/")


def strip_trailing_comment(line: str) -> str:
    """去掉 shell/批处理行尾注释，避免把注释里的中文当成未加引号的路径。"""
    quote = ""
    for index, char in enumerate(line):
        if quote:
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char == "#" and (index == 0 or line[index - 1].isspace()):
            return line[:index].rstrip()
    return line


def installed_only_reason(normalized: str) -> str | None:
    lower = normalized.lower()
    for prefix, reason in INSTALLED_ONLY_PREFIXES.items():
        if lower.startswith(prefix):
            return reason
    for name, reason in INSTALLED_ONLY_NAMES.items():
        if lower == name or lower.startswith(name + "/"):
            return reason
    return None


def external_profile_reason(normalized: str) -> str | None:
    lower = normalized.lower()
    for prefix, reason in EXTERNAL_PROFILE_PREFIXES.items():
        if lower.startswith(prefix):
            return reason
    return None


def path_tokens(line: str) -> list[str]:
    result: list[str] = []
    for token in tokenize(line):
        candidate = token
        if candidate.startswith("-") or any(ch in candidate for ch in "<>%${}*?'\""):
            continue
        if not PATH_TOKEN_RE.match(candidate):
            continue
        result.append(candidate)
    return result


def check_paths(doc: Path, site: Site, skip: tuple[str, ...] = ()) -> list[Issue]:
    issues: list[Issue] = []
    for token in path_tokens(site.text):
        if token in skip:
            continue  # 脚本路径已由 check_script_target 判定，避免同因两报
        normalized = normalize_path(token)
        if not normalized:
            continue
        first = normalized.split("/")[0].lower()
        if first in {"", ".", ".."}:
            continue  # ./my-plugin.zip 这类用户自建示例文件，不在仓库契约内
        if not (ROOT / first).exists() and not re.match(r"^[a-z]:/", normalized, re.I):
            continue  # registry.example.com/… 这类外部路径
        if installed_only_reason(normalized):
            continue
        if external_profile_reason(normalized):
            continue
        if (ROOT / normalized).exists():
            continue
        if re.match(r"^[a-z]:/", normalized, re.I):
            # 盘符路径只要不是安装版的固定布局（D:\LiuHao…），就是用户自选目录的示例，
            # 例如 D:\releases、D:\snap，无法在仓库侧判定存在性。
            continue
        if normalized.endswith(".whl"):
            continue
        issues.append(
            Issue(
                doc,
                site.section,
                site.line,
                site.text,
                f"引用的路径不存在：{token}",
                "改成仓库里真实存在的脚本/文件，或标注为安装版路径（runtime\\\\…、dist\\\\…）",
            )
        )
    return issues


def check_script_target(doc: Path, site: Site, script_token: str, extra: list[str], interpreter: str) -> list[Issue]:
    issues: list[Issue] = []
    normalized = normalize_path(script_token)
    if installed_only_reason(normalized) or external_profile_reason(normalized):
        return issues
    script = ROOT / normalized
    if not script.is_file():
        if "/" not in normalized:
            # 裸文件名（python code_review.py）：教程要求读者自己创建的示例脚本，
            # 不是仓库契约，无法也不该按仓库路径判定。
            return issues
        issues.append(
            Issue(
                doc, site.section, site.line, site.text,
                f"脚本不存在：{script_token}",
                "改为真实存在的脚本路径（见 scripts/ 目录）",
            )
        )
        return issues
    flags_used = [token for token in extra if FLAG_RE.match(token)]
    if script.suffix == ".ps1" or interpreter.lower() in {"powershell", "pwsh"}:
        available = ps1_params(script)
    else:
        available = script_flags(script)
    if flags_used and not available:
        issues.append(
            Issue(
                doc, site.section, site.line, site.text,
                f"源码里没有 argparse/param 定义，无法确认参数：{'、'.join(flags_used)}",
                "给脚本补 argparse 定义，或从文档删掉无法验证的参数",
            )
        )
        return issues
    for token in flags_used:
        flag = token.split("=", 1)[0]
        if flag not in available:
            issues.append(
                Issue(
                    doc, site.section, site.line, site.text,
                    f"参数在 {script_token} 的源码里不存在：{flag}",
                    f"可用参数：" + "、".join(sorted(available)[:8]) + ("…" if len(available) > 8 else ""),
                )
            )
    return issues


def check_module_target(doc: Path, site: Site, module: str, tokens: list[str]) -> list[Issue]:
    issues: list[Issue] = []
    top = module.split(".")[0]
    if not (ROOT / top).exists() and not (ROOT / f"{top}.py").exists():
        # 标准库/第三方模块（venv、uvicorn、pytest …）：不是本仓库的契约，静态不校验。
        return issues
    graph = build_module_graph(module)
    if graph is None:
        issues.append(
            Issue(
                doc, site.section, site.line, site.text,
                f"模块不能用 -m 运行（缺 __main__.py 或 __main__ 守卫）：{module}",
                "改用真实入口：python -m uvicorn src.gateway.main:app，或 python scripts\\start_liuhao.py",
            )
        )
        return issues

    node = graph.root
    index = 0
    while index < len(tokens) and not FLAG_RE.match(tokens[index]):
        token = tokens[index]
        if node.children:
            child = node.children.get(token)
            if child is None:
                issues.append(
                    Issue(
                        doc, site.section, site.line, site.text,
                        f"子命令不存在：{module} {' '.join(tokens[: index + 1])}（可选："
                        + "、".join(sorted(node.children))
                        + "）",
                        "改用真实子命令，" + "或" + "在源码里补该子命令",
                    )
                )
                return issues
            node = child
            index += 1
            continue
        break

    allowed = graph.flags_for(node)
    present = {token.split("=", 1)[0] for token in tokens[index:] if FLAG_RE.match(token)}
    for token in tokens[index:]:
        if not FLAG_RE.match(token):
            continue
        flag = token.split("=", 1)[0]
        if flag not in allowed:
            issues.append(
                Issue(
                    doc, site.section, site.line, site.text,
                    f"参数不存在：{module} {' '.join(tokens[: index + 1])} 不支持 {flag}",
                    "可用参数：" + "、".join(sorted(allowed)[:8]) + ("…" if len(allowed) > 8 else ""),
                )
            )
    # argparse 里 required=True 的参数不写就必然报错（照抄最直接的失败）。
    for flag in sorted(graph.required_for(node) - present):
        issues.append(
            Issue(
                doc, site.section, site.line, site.text,
                f"缺少必填参数：{module} {' '.join(tokens[: index + 1])} 需要 {flag}",
                f"补上 {flag}（必填参数：" + "、".join(sorted(graph.required_for(node))) + "）",
            )
        )
    return issues


def check_placeholders(doc: Path, site: Site) -> list[Issue]:
    issues: list[Issue] = []
    for match in SK_RE.finditer(site.text):
        token = match.group(0)
        if PLACEHOLDER_HINT_RE.search(token):
            continue
        issues.append(
            Issue(
                doc, site.section, site.line, site.text,
                f"疑似真实密钥（会被人当真的照抄）：{token}",
                "改成显式占位：sk-... 或 YOUR_API_KEY，并在正文说明去哪填真实值",
            )
        )
    return issues


def check_quoting(doc: Path, site: Site) -> list[Issue]:
    issues: list[Issue] = []
    code = strip_trailing_comment(site.text)
    unquoted = re.sub(r"'[^']*'|\"[^\"]*\"", "\"\"", code)
    if re.search(r"[A-Za-z]:\\[^\"']*Program Files", unquoted, re.I):
        issues.append(
            Issue(
                doc, site.section, site.line, site.text,
                "含空格的路径没加引号（Program Files 会被拆成两个参数）",
                "整条路径加双引号：\"C:\\Program Files\\…\"",
            )
        )
    for token in tokenize(code):
        if not CJK_RE.search(token):
            continue
        if "\\" not in token and "/" not in token:
            continue
        if any(char in token for char in "<>$%{}"):
            continue  # <目录>/… 这类占位符，不是真实路径
        if f'"{token}"' in code or f"'{token}'" in code:
            continue
        issues.append(
            Issue(
                doc, site.section, site.line, site.text,
                f"含中文的路径没加引号：{token}",
                "整条路径加双引号，避免按空格/编码被拆开",
            )
        )
    return issues


def explain_execution(line: str) -> str:
    stripped = PROMPT_RE.sub("", line.strip())
    if stripped.startswith(("set ", "export ", "$env:")):
        return "环境变量赋值：可在当前会话干跑，无副作用"
    py = PY_RE.match(stripped)
    if not py:
        if stripped.startswith("liuhao "):
            return "不可执行：本仓库没有 liuhao 入口（pyproject 无 [project.scripts]）"
        if stripped.lower().startswith("liuhao.bat"):
            return "不可执行：安装版入口（仓库无 LiuHao.bat，packaging 时生成）"
        if stripped.startswith(("powershell", "pwsh")):
            return "不可执行：会调用构建脚本（可能写 dist/）"
        if re.match(r"^(?:scripts|src|packaging|installer)[\\/]\S+$", stripped):
            return "不是命令：正文里只提及了该文件路径"
        return "不可执行：外部工具（docker/helm/kubectl/ollama/pip/git 等），只做静态校验"
    rest = py.group("rest").strip()
    if py.group("venv"):
        venv_python = (ROOT / ".venv" / "Scripts" / "python.exe").exists() or (ROOT / ".venv" / "bin" / "python").exists()
        return (
            "可干跑：.venv 解释器存在，跑 --help 即可"
            if venv_python
            else "不可执行：.venv 不存在（先建开发环境）"
        )
    if py.group("runtime"):
        return "不可执行：runtime\\\\ 只在安装版里（仓库无该目录）"
    tokens = tokenize(rest)
    if not tokens:
        return "可干跑：--help"
    if any(token in DRY_RUN_SAFE_ARGS for token in tokens):
        return "可干跑：命令已带 --help/--dry-run"
    if not rest.startswith("-m ") and tokens[0].endswith(".py"):
        if any(token in MUTATING_TOKENS for token in tokens):
            return "不可干跑：会改数据/系统；只做静态校验"
        return "可干跑：python <脚本> --help（argparse 只打印帮助）"
    if rest.startswith("-m "):
        if any(token in MUTATING_TOKENS for token in tokens):
            return "不可干跑：会改数据/系统；只做静态校验"
        return "可干跑：python -m <模块> --help"
    return "不可执行：外部模块（uvicorn 等）"


_DOC_LINES: dict[Path, list[str]] = {}


def doc_lines(doc: Path) -> list[str]:
    if doc not in _DOC_LINES:
        _DOC_LINES[doc] = read_text(doc).splitlines()
    return _DOC_LINES[doc]


def destructive_pair(line: str) -> tuple[str, str] | None:
    tokens = tokenize(strip_trailing_comment(PROMPT_RE.sub("", line.strip())))
    for index in range(len(tokens) - 1):
        pair = (tokens[index], tokens[index + 1])
        if pair in DESTRUCTIVE_COMMANDS:
            return pair
    return None


def has_destructive_warning(site: Site) -> bool:
    lines = doc_lines(site.doc)
    if site.origin == "fence":
        # 预警写在代码块顶部即可覆盖整块；不必每行都重复。
        start = site.line - 1
        while start > 0 and not lines[start - 1].startswith("```"):
            start -= 1
        end = site.line - 1
        while end < len(lines) - 1 and not lines[end + 1].startswith("```"):
            end += 1
        window = lines[start : end + 1]
    else:
        start = max(0, site.line - 1 - DESTRUCTIVE_CONTEXT_LINES)
        end = min(len(lines), site.line + DESTRUCTIVE_CONTEXT_LINES)
        window = lines[start:end]
    return bool(DESTRUCTIVE_WARNING_RE.search("\n".join(window)))


def check_destructive(site: Site) -> list[Issue]:
    pair = destructive_pair(site.text)
    if pair is None or has_destructive_warning(site):
        return []
    return [
        Issue(
            site.doc, site.section, site.line, site.text,
            f"会改动数据的命令没有安全提示：{' '.join(pair)}（无 --dry-run/--yes）",
            "在命令上方或下方加一句「会直接改动数据，执行前先备份」；"
            "有 --force 的仍要提醒备份",
        )
    ]


def analyze(sites: list[Site]) -> tuple[list[Issue], list[Issue], list[Site]]:
    issues: list[Issue] = []
    warnings: list[Issue] = []
    skipped: list[Site] = []
    for site in sites:
        line = PROMPT_RE.sub("", site.text.strip())
        if line.startswith(("set ", "export ", "$env:")):
            continue
        py = PY_RE.match(line)
        handled = False
        script_token: tuple[str, ...] = ()
        ps = re.match(r"^(?:powershell|pwsh)(?:\.exe)?\b\s*(.*)$", line, re.I)
        if ps and not py:
            tokens = tokenize(ps.group(1))
            lowered = [token.lower() for token in tokens]
            handled = True
            if "-file" in lowered:
                index = lowered.index("-file")
                if index + 1 < len(tokens):
                    script_token = (tokens[index + 1],)
                    issues.extend(
                        check_script_target(
                            site.doc, site, tokens[index + 1], tokens[index + 2:], "powershell"
                        )
                    )
        if py:
            rest = py.group("rest").strip()
            interpreter = py.group(0)[: len(py.group(0)) - len(rest)].strip()
            tokens = tokenize(rest)
            if py.group("bare") and site.doc in INSTALLED_DOCS and site.origin == "fence":
                issues.append(
                    Issue(
                        site.doc, site.section, site.line, site.text,
                        "安装版文档的命令块用了裸 python（用户机器上 PATH 里没有 Python）",
                        "改成 runtime\\\\python.exe（安装版自带运行时）",
                    )
                )
                handled = True
            elif rest.startswith("-m "):
                module = tokens[1] if len(tokens) > 1 else ""
                issues.extend(check_module_target(site.doc, site, module, tokens[2:]))
                handled = True
            elif tokens and tokens[0].endswith((".py", ".ps1")):
                script_token = (tokens[0],)
                issues.extend(check_script_target(site.doc, site, tokens[0], tokens[1:], interpreter))
                handled = True
            elif py.group("bare") and site.doc in INSTALLED_DOCS:
                issues.append(
                    Issue(
                        site.doc, site.section, site.line, site.text,
                        "安装版文档用了裸 python（用户机器上 PATH 里没有 Python）",
                        "改成 runtime\\\\python.exe（安装版自带运行时），或写清需先进入源码仓库的 .venv",
                    )
                )
                handled = True
            elif tokens:
                skipped.append(site)
                handled = True
        if line.startswith("liuhao ") and not handled:
            missing = PY_RE.match("liuhao " + line.split(" ", 1)[1]) is None
            if missing:
                issues.append(
                    Issue(
                        site.doc, site.section, site.line, site.text,
                        "liuhao 命令不由本仓库提供（pyproject 没有 [project.scripts]），照抄会报“不是内部或外部命令”",
                        "改成 python -m src.cli …（子命令以源码 src/cli 为准）",
                    )
                )
            skipped.append(site)
            handled = True
        if not handled:
            skipped.append(site)
            continue
        issues.extend(check_paths(site.doc, site, script_token))
        issues.extend(check_placeholders(site.doc, site))
        issues.extend(check_quoting(site.doc, site))
        warnings.extend(check_destructive(site))
    return issues, warnings, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description="检查用户文档里的命令块是否可照抄执行")
    parser.add_argument("--list", action="store_true", help="列出提取到的全部命令")
    parser.add_argument("--explain", action="store_true", help="打印每条命令的干跑能力与原因")
    parser.add_argument("--verbose", action="store_true", help="打印通过项")
    args = parser.parse_args()

    targets, exempted = doc_targets()
    sites: list[Site] = []
    for doc in targets:
        found = extract_sites(doc)
        if not found and args.verbose:
            print(f"[SKIP] 无命令块：{rel(doc)}")
        sites.extend(found)

    if args.verbose:
        for doc, reason in exempted:
            print(f"[EXEMPT] {rel(doc)} —— {reason}")

    if not sites:
        print("未提取到任何命令块，检查 DOC 列表是否失效", file=sys.stderr)
        return 1

    if args.list:
        # --list / --explain 是信息模式：只列出提取到的命令与干跑建议，不宣告
        # 任何检查结论。这里的退出码 0 不是"伪装通过"。
        print(f"共 {len(sites)} 条命令/命令片段（{len(targets)} 份文档）")
        for site in sites:
            print(f"{rel(site.doc)}#{site.section} L{site.line} [{site.origin}] {site.text}")
        return 0

    if args.explain:
        for site in sites:
            print(f"{rel(site.doc)}#{site.section} L{site.line}\t{site.text}\t=> {explain_execution(site.text)}")
        return 0

    issues, warnings, skipped = analyze(sites)
    if args.verbose:
        print(f"[OK] 已扫描 {len(targets)} 份文档、{len(sites)} 条命令；豁免 {len(exempted)} 份")
        for site in skipped:
            print(f"[SKIP] {rel(site.doc)}#{site.section} L{site.line}（命令类型不适用）")

    if warnings:
        # 黄：不阻断 CI，但必须出现在输出里，提醒人工补备份提示。
        print(f"[WARN] {len(warnings)} 处会改动数据的命令缺少警示语：")
        for warning in warnings:
            print(f"  {rel(warning.doc)}#{warning.section} L{warning.line}")
            print(f"    命令：{warning.command}")
            print(f"    问题：{warning.problem}")
            print(f"    建议：{warning.fix}")

    if issues:
        print(f"[FAIL] {len(issues)} 处命令块问题：")
        for issue in issues:
            print(f"  {rel(issue.doc)}#{issue.section} L{issue.line}")
            print(f"    命令：{issue.command}")
            print(f"    问题：{issue.problem}")
            print(f"    建议：{issue.fix}")
        return 1

    print(
        f"[OK] 命令块检查通过：{len(targets)} 份文档、{len(sites)} 条命令；"
        f"豁免 {len(exempted)} 份历史证据文档，黄牌 {len(warnings)} 条"
        "（脚本存在、参数存在、占位符已标注、含空格/中文路径已加引号）"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
