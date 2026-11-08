"""构建期品牌字面硬门禁。

任何会进入安装包的品牌文本，只要出现旧主名、错字、死域名或旧版本号，
就在这里直接失败——不靠人眼，也不等测试事后发现。

用法::

    python packaging/brand_guard.py --payload dist/payload/app \
        --iss dist/installer/build/liuhao.iss

退出码 0 表示通过；1 表示有违规项（逐条打印）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from fnmatch import fnmatch
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

#: 仓库内参与品牌门禁的文本（历史文档/证据不参与，避免把真实历史改成假历史）。
REPO_SURFACES = (
    "installer/liuhao.iss",
    "installer/assets/BEFORE-INSTALL.txt",
    "packaging/third-party-notices.template.txt",
    "pyproject.toml",
    "openapi.yaml",
    "src/gateway/main.py",
    "src/desktop/about.py",
    "src/gateway/diagnostic_bundle.py",
)

#: payload 内逐字核对的固定产物（相对 app 根目录）。
#: BEFORE-INSTALL.txt 不在 payload 里，由安装器从 installer/assets 直接复制，
#: 因此只在 REPO_SURFACES 校验。
PAYLOAD_SURFACES = (
    "START-HERE.txt",
    "THIRD-PARTY-NOTICES.txt",
    "console/index.html",
    "console/manifest.webmanifest",
    "console/sw.js",
)

#: payload 全量扫描时跳过的子树：第三方运行时、二进制资源，不含自家品牌字面。
PAYLOAD_SKIP_DIRS = ("runtime", "assets", "node_modules")

#: 只读这些后缀的文本；其余按二进制跳过，避免误读图片/模型文件。
PAYLOAD_TEXT_SUFFIXES = (
    ".py", ".txt", ".md", ".yaml", ".yml", ".json", ".html", ".js", ".mjs",
    ".css", ".bat", ".cmd", ".ps1", ".toml", ".ini", ".cfg", ".iss",
    ".ts", ".tsx", ".jsx", ".webmanifest", ".map", ".svg", ".env",
)

#: 旧主名、错字、死域名。出现在任何受检文本里都直接失败。
FORBIDDEN = (
    "LIUHAO X",
    "鎏灏 X",
    "镭灏",
    "api.liuhao-ai-os.com",
)

#: 仓库全量品牌扫描的目录：安装器、文档、前端、后端、打包脚本。
REPO_SCAN_DIRS = ("installer", "docs", "apps/console", "src", "packaging")

#: 仓库扫描跳过：构建产物、依赖、缓存。
REPO_SKIP_PARTS = ("node_modules", "dist", "__pycache__", ".venv", ".git", "dist-mid")

#: 仓库扫描的**显式允许清单**：每一项都必须给出理由，否则就应改名而不是放行。
#: 目录以 ``/`` 结尾按前缀匹配，其余按 glob 匹配。
REPO_ALLOW = (
    ("docs/BRAND-CANON.md", "规范自身必须列出禁用写法，才能被引用和测试"),
    ("docs/archive/", "历史归档：保留当时原文，改写会破坏证据"),
    ("docs/spec/", "规范/框架原始定义（含 v3.0 历史命名），作为迁移与审计证据保留"),
    ("docs/architecture/", "架构基线与既有代码审计原件，属于历史事实"),
    ("docs/operations/", "运维 runbook 历史记录，保留原样"),
    ("docs/product/", "产品 PRD 历史版本，保留原样"),
    ("docs/roadmap/", "路线图历史记录，保留原样"),
    ("docs/l10k/", "测试设计历史记录，保留原样"),
    ("docs/decisions/", "决策记录：记录当时的决策语境"),
    ("docs/*-20261108.md", "本次审计/冻结的证据快照，按日期封存"),
    ("docs/CODEX-CONTRACT.md", "与外部 Codex 的契约原文，单方面改会失真"),
    ("docs/REPO-LINE-CLOSURE.md", "仓库线收口记录原文，属于历史事实"),
    ("docs/ARCHITECTURE.md", "现行架构文档中的“曾用名/历史代号”说明段"),
    ("packaging/brand_guard.py", "禁用词表实现本身必须包含这些字面"),
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig", errors="replace")


def brand_lockup() -> str:
    data = json.loads(_read(REPO_ROOT / "src" / "brand.json"))
    return f"{data['name_en']} · {data['name_zh']}"


def app_version() -> str:
    text = _read(REPO_ROOT / "src" / "version.py")
    match = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', text)
    if not match:
        raise SystemExit("无法从 src/version.py 读取 APP_VERSION")
    return match.group(1)


def _forbidden_hits(rel: str, text: str) -> list[str]:
    """逐行定位禁止项，输出 ``文件:行号``，方便直接跳到现场改。"""
    hits: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for item in FORBIDDEN:
            if item in line:
                hits.append(f"{rel}:{lineno}: 出现禁止项 {item!r}")
    return hits


def _check_forbidden(rel: str, text: str) -> list[str]:
    hits = _forbidden_hits(rel, text)
    # 1.0.0 只按“版本字段”查，避免把依赖下限误判成版本号。
    if re.search(r'(?m)^\s*version:\s*1\.0\.0\s*$', text):
        hits.append(f"{rel}: /docs 版本仍为 1.0.0")
    if re.search(r'#define AppVersion "1\.0\.0"', text):
        hits.append(f"{rel}: 安装器版本仍为 1.0.0")
    if re.search(r'version="1\.0\.0"', text):
        hits.append(f"{rel}: FastAPI 版本仍为 1.0.0")
    return hits


def _iter_payload_text(payload: Path):
    """遍历 payload 里需要逐字核对的自有文本（跳过第三方运行时与二进制）。"""
    for path in sorted(payload.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(payload)
        if any(part in PAYLOAD_SKIP_DIRS for part in rel.parts[:-1]):
            continue
        if ".egg-info" in rel.parts:
            continue
        if path.suffix.lower() not in PAYLOAD_TEXT_SUFFIXES:
            continue
        yield rel.as_posix(), path


def _repo_allowed(rel: str) -> bool:
    for pattern, _reason in REPO_ALLOW:
        if pattern.endswith("/"):
            if rel.startswith(pattern):
                return True
        elif fnmatch(rel, pattern):
            return True
    return False


def _scan_repo_tree(root: Path) -> list[str]:
    """扫描 installer/、docs/、apps/console/、src/、packaging/ 的全部文本。"""
    violations: list[str] = []
    for dirname in REPO_SCAN_DIRS:
        base = root / dirname
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            parts = path.relative_to(root).parts[:-1]
            if any(part in REPO_SKIP_PARTS for part in parts):
                continue
            if path.suffix.lower() not in PAYLOAD_TEXT_SUFFIXES:
                continue
            rel = path.relative_to(root).as_posix()
            if _repo_allowed(rel):
                continue
            violations += _forbidden_hits(rel, _read(path))
    return violations


def _check_lockup(rel: str, text: str, lockup: str) -> list[str]:
    if lockup not in text:
        return [f"{rel}: 未出现主名 {lockup!r}"]
    return []


def _check_version(rel: str, text: str, version: str) -> list[str]:
    hits: list[str] = []
    if rel.endswith((".yaml", ".yml")):
        data = yaml.safe_load(text) or {}
        found = str((data.get("info") or {}).get("version", ""))
        if found != version:
            hits.append(f"{rel}: /docs 版本 {found!r} != {version}")
    match = re.search(r'#define AppVersion "([^"]+)"', text)
    if match and match.group(1) != version:
        hits.append(f"{rel}: 安装器 AppVersion {match.group(1)} != {version}")
    return hits


def scan(
    payload: Path | None = None,
    iss: Path | None = None,
    *,
    repo_root: Path = REPO_ROOT,
    skip_repo: bool = False,
) -> list[str]:
    lockup = brand_lockup()
    version = app_version()
    violations: list[str] = []

    repo_files = list(REPO_SURFACES)
    if skip_repo:
        repo_files = []
    for rel in repo_files:
        path = repo_root / rel
        if not path.is_file():
            violations.append(f"{rel}: 文件缺失，品牌门禁无法核对")
            continue
        text = _read(path)
        violations += _check_forbidden(rel, text)
        if rel in (
            "installer/liuhao.iss",
            "installer/assets/BEFORE-INSTALL.txt",
            "packaging/third-party-notices.template.txt",
            "openapi.yaml",
        ):
            violations += _check_lockup(rel, text, lockup)
        if rel in ("installer/liuhao.iss", "openapi.yaml"):
            violations += _check_version(rel, text, version)

    if not skip_repo:
        # 全仓逐行扫描：任何旧主名/错字/死域名都不能留在 installer/docs/前端/后端/打包脚本里。
        violations += _scan_repo_tree(repo_root)

    iss_path = iss or (repo_root / "installer" / "liuhao.iss")
    if iss_path.is_file():
        rel = str(iss_path)
        text = _read(iss_path)
        violations += _check_forbidden(rel, text)
        violations += _check_lockup(rel, text, lockup)
        violations += _check_version(rel, text, version)
        if f'#define AppName "{lockup}"' not in text:
            violations.append(f"{rel}: AppName 不等于 {lockup!r}")

    if payload is not None:
        checked: set[str] = set()
        for rel in PAYLOAD_SURFACES:
            path = payload / rel
            if not path.is_file():
                violations.append(f"{rel}: payload 产物缺失，品牌门禁无法核对")
                continue
            text = _read(path)
            checked.add(rel)
            violations += _check_forbidden(rel, text)
            if rel in (
                "START-HERE.txt",
                "THIRD-PARTY-NOTICES.txt",
                "console/index.html",
                "console/manifest.webmanifest",
            ):
                violations += _check_lockup(rel, text, lockup)
        # 全量逐字扫描：旧主名/错字/死域名不允许藏在任何自有文本里。
        # 版本号 1.0.0 只对固定产物按字段核对，避免把工具 schema 版本误判成产品版本。
        for rel, path in _iter_payload_text(payload):
            if rel in checked:
                continue
            violations += _forbidden_hits(rel, _read(path))

    pyproject = repo_root / "pyproject.toml"
    if not skip_repo and pyproject.is_file():
        project_version = tomllib.loads(_read(pyproject))["project"]["version"]
        if project_version != version:
            violations.append(
                f"pyproject.toml: version {project_version} != src/version.py {version}"
            )

    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="品牌字面构建期硬门禁")
    parser.add_argument("--payload", default="", help="payload 的 app 根目录")
    parser.add_argument("--iss", default="", help="本次实际编译的 liuhao.iss 副本")
    parser.add_argument("--skip-repo", action="store_true", help="只查 payload（自测用）")
    args = parser.parse_args(argv)

    violations = scan(
        Path(args.payload).resolve() if args.payload else None,
        Path(args.iss).resolve() if args.iss else None,
        skip_repo=args.skip_repo,
    )
    if violations:
        print("品牌字面门禁未通过：", file=sys.stderr)
        for item in violations:
            print(f"  - {item}", file=sys.stderr)
        return 1
    print(f"品牌字面门禁通过：{brand_lockup()} / {app_version()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
