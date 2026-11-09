"""生成升级用的发布包与发布清单（严格按 docs/self-upgrade.md §2.2 规范 v1）。

产物（同一目录，默认 ``dist\\release``）：

    liuhao-<version>-upgrade.zip  升级包（安装树 ZIP；不能单独运行）
    LIUHAO_RELEASE.json       发布清单（schema = liuhao.release-manifest/1）
    dev-path-report.txt       开发目录路径扫描结果（排障用，不参与升级）

规范要点：
  * ZIP 只允许 ``src/``、``docs/``、``pyproject.toml``、``requirements.txt``、
    ``oss-registry.yaml`` 与根目录包内清单 ``LIUHAO_RELEASE.json``；
  * 禁止 ``data/``、``.git/``、``.venv/``、``.env``、``secrets/``、``*.pem``/``*.key``/``*.crt``；
  * ``sha256`` 对**整包字节**计算，``size`` 为字节数；
  * 清单内嵌 ``files`` 文件级清单（相对路径 + SHA256 + 字节数）与
    ``data_schema_version``：整包哈希证明不了包内文件齐全，安装端按文件级
    清单逐项校验后才允许落盘；
  * 包内版本（包根 LIUHAO_RELEASE.json + pyproject.toml）必须与清单 ``version`` 完全相等；
  * 有签名私钥才写 ``signature`` / ``signature_algorithm``，没有就**不写**空值或假签名；
  * 自己产生清单、自己回读校验；任何不一致直接失败，不产出坏清单。

用法（在仓库根执行，必须显式指定安装树）：

    .venv\\Scripts\\python.exe packaging\\make_release.py --app-root dist\\payload\\app
    .venv\\Scripts\\python.exe packaging\\make_release.py --app-root dist\\payload\\app --strict-dev-paths
    $env:LIUHAO_UPGRADE_SIGNING_KEY = "D:\\keys\\liuhao-release-ed25519.pem"
    .venv\\Scripts\\python.exe packaging\\make_release.py --app-root dist\\payload\\app --signing-key $env:LIUHAO_UPGRADE_SIGNING_KEY

仓库根模式（不带 --app-root）已停用：它不会把根级 ``error_types.py`` /
``capability-registry.yaml`` 打进包，安装后自检会失败并回滚。安装树由
``packaging/stage_payload.py`` 生成；控制台必须先由
``packaging/build_console.py`` 按当前源码构建并写入来源记录。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MANIFEST_NAME = "LIUHAO_RELEASE.json"
PACKAGE_SCHEMA = "liuhao.release-package/1"
MANIFEST_SCHEMA = "liuhao.release-manifest/1"
PRODUCT = "liuhao-ai-os"

#: 旧「仓库根」模式的打包输入（仅在显式 ``--root <仓库>`` 时使用）。
#: ``docs`` 不再整目录进包：用户文档由 ``DOC_ALLOWLIST`` 逐文件挑选。
PACKAGE_ROOTS = ("src", "pyproject.toml", "requirements.txt", "oss-registry.yaml")

#: 「安装树」模式的打包输入。升级包就是安装树本身，必须与
#: ``packaging/stage_payload.py`` 组装出的 ``payload/app`` 一致：
#: 根级模块（``error_types.py``）、``config/``、启动脚本都在里面，
#: 否则干净机器 apply 后自检会以 ModuleNotFoundError 失败。
APP_TREE_DIRS = ("src", "config", "scripts", "console", "assets")
APP_TREE_ROOT_FILES = (
    "capability-registry.yaml",
    "pyproject.toml",
    "requirements.txt",
    "oss-registry.yaml",
    "LiuHao.bat",
    "START-HERE.txt",
    # 合规材料：安装树里位于包根，出升级包时由 _relocate_for_upgrade 挪到
    # docs/compliance/ —— 0.1.1 的升级校验只认它自己的 INSTALL_ROOTS。
    "THIRD-PARTY-NOTICES.txt",
    "sbom-cyclonedx.json",
)
APP_TREE_ROOT_GLOBS = ("*.py",)
APP_TREE_SKIP_SUFFIXES = {
    ".pyc", ".pyo", ".db", ".db-wal", ".db-shm", ".sqlite3", ".sqlite3-wal", ".sqlite3-shm",
}
APP_TREE_SKIP_PREFIXES = ("unins", "upgrade-backups", "upgrade-state", ".liuhao")

#: 升级包随包的用户文档白名单：**逐文件列出，不用通配**。
#: 只允许普通用户真的会打开的文档；内部审计、spec、known-gaps、evidence 截图
#: 一律不进包。新增/改名文档必须主动加到这里，否则不会进包。
#: 名单内文件缺失时 ``_collect_docs`` 直接中止构建，避免用户指南静默少带。
#:
#: 暂未列入的两类（仓库里没有可发布的干净文件，补好后再各加一行）：
#:   * 安装说明：``docs/INSTALL-WINDOWS.md`` 当前是内部交付状态页（含构建时间、
#:     SHA256、文件数），不能给用户看；用户侧安装说明由 ``START-HERE.txt`` 承担。
#:   * 变更记录：``docs/knowledge_base/CHANGELOG.md`` 是 3.0.0 旧稿（含未交付能力）。
DOC_ALLOWLIST = (
    "docs/END-USER-GUIDE.md",
    "docs/USER-GUIDE.md",
    "docs/faq.md",
)

#: 升级包内合规材料的落点。必须是 ``INSTALL_ROOTS`` 允许的 ``docs`` 之下，
#: 否则升级器会因路径不在白名单而拒绝整包。
UPGRADE_COMPLIANCE_DIR = "docs/compliance"
#: 第三方许可证全文（安装树 ``licenses/`` 下）：同样逐文件列出。
#: 新增依赖带来新的许可证文件时必须显式加入；缺失同样让构建失败。
APP_TREE_LICENSE_FILES = (
    "licenses/adodbapi-LGPL-2.1.txt",
    "licenses/Apache-2.0.txt",
    "licenses/expat-MIT.txt",
    "licenses/fastmcp-slim-Apache-2.0.txt",
    "licenses/LGPL-2.1.txt",
    "licenses/LGPL-SOURCE-OFFER.txt",
    "licenses/libffi-MIT.txt",
    "licenses/libiconv-NOTICE.txt",
    "licenses/libmpdec-BSD-2-Clause.txt",
    "licenses/Microsoft-VC-Runtime.txt",
    "licenses/opentelemetry-util-http-Apache-2.0.txt",
    "licenses/proxy_tools-BSD.txt",
    "licenses/pywin32-BSD-3-Clause.txt",
    "licenses/React-MIT.txt",
    "licenses/SQLite-Public-Domain.txt",
    "licenses/Tcl-Tk-license.terms",
    "licenses/Tix-License.txt",
    "licenses/Unicode-License.txt",
    "licenses/zlib-License.txt",
)

#: 缺少这些文件时不允许产出升级包：整包哈希再一致，装上去也是新旧代码混跑。
REQUIRED_RELEASE_FILES = (
    "src/gateway/main.py",
    "src/gateway/chat.py",
    "src/upgrade/manager.py",
    "assets/liuhao.ico",
)

# 与 src/upgrade/manager.py 的默认 install_roots / protected_paths 保持一致。
from src.upgrade.manager import INSTALL_ROOTS  # noqa: E402
from src.upgrade.dataversion import DATA_SCHEMA_VERSION  # noqa: E402
from src.upgrade.errors import PackageValidationError  # noqa: E402
from src.upgrade.filemanifest import (  # noqa: E402
    build_file_manifest_for_zip,
    verify_file_manifest,
)
from src.upgrade.console_contract import (  # noqa: E402
    CONTRACT_RELPATH,
    FrontendContractError,
    build_stamp,
    console_files_under,
    read_backend_contract_from_source,
    verify_package as verify_console_contract,
)

_PACKAGING_DIR = Path(__file__).resolve().parent
if str(_PACKAGING_DIR) not in sys.path:
    sys.path.insert(0, str(_PACKAGING_DIR))
import console_provenance  # noqa: E402

PROTECTED_PATHS = (
    "data",
    ".git",
    ".venv",
    ".env",
    "secrets",
    "*.pem",
    "*.key",
    "*.crt",
    "upgrade-backups",
    "upgrade-state",
)
SKIP_DIR_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".git"}
SKIP_SUFFIXES = {".pyc", ".pyo"}
TEXT_SUFFIXES = {
    ".py", ".md", ".toml", ".txt", ".yaml", ".yml", ".json", ".iss",
    ".ps1", ".bat", ".cmd", ".ini", ".cfg", ".sh", ".sql",
}
# 开发机绝对路径 / 开发虚拟环境路径（含转义写法 D:\\... 与 .venv\\Scripts）。
DEV_PATH_RE = re.compile(
    r"(?i)(?:D:(?:\\\\|\\|/)LiuHao-AI-OS|\.venv(?:\\\\|\\|/)(?:Scripts|bin))"
)
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
MAX_ENTRIES = 100_000
MAX_UNCOMPRESSED = 2 * 1024 * 1024 * 1024
MAX_SIGNING_BYTES = 512 * 1024 * 1024


class BuildError(Exception):
    """构建失败（打印原因后以非零退出码结束）。"""


def _log(message: str = "") -> None:
    print(message, flush=True)


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )


def _read_pyproject_version(path: Path) -> str:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise BuildError(f"无法读取 {path}：{exc}") from exc
    project = data.get("project")
    value = project.get("version") if isinstance(project, dict) else None
    if not isinstance(value, str) or not value.strip():
        raise BuildError(f"{path} 缺少 [project].version")
    return value.strip()


def _validate_version(version: str, context: str) -> None:
    try:
        from packaging.version import InvalidVersion, Version

        Version(version)
    except ImportError:  # packaging 是运行依赖，缺了就直接失败而不是跳过校验
        raise BuildError("缺少 packaging 库，无法校验版本号（请先安装项目依赖）")
    except InvalidVersion as exc:
        raise BuildError(f"{context} 不是合法的 PEP 440 版本号：{version!r}") from exc


def _collect_files(root: Path, *, app_tree: bool = False) -> List[Tuple[str, Path]]:
    """按白名单收集待打包文件（相对 POSIX 路径 + 绝对路径），排序保证可复现。"""
    items: List[Tuple[str, Path]] = []
    seen: set[str] = set()
    candidates: List[Path] = []
    if app_tree:
        for entry in APP_TREE_DIRS:
            path = root / entry
            if path.is_dir():
                candidates.extend(sorted(path.rglob("*")))
            elif entry == "src":
                raise BuildError(f"安装树缺少目录：{entry}")
            else:
                _log(f"  [跳过] 安装树没有 {entry}/")
        for name in APP_TREE_ROOT_FILES:
            path = root / name
            if path.is_file():
                candidates.append(path)
            else:
                # 硬门禁：升级按「上一版有、新版没有就删」处理，缺入口文件会让
                # 升级后的桌面快捷方式直接断掉。任何一项缺失都必须拒绝出包。
                raise BuildError(f"安装树缺少必需根文件：{name}")
        for name in APP_TREE_LICENSE_FILES:
            path = root / name
            if not path.is_file():
                # 白名单缺失必须失败：静默少带会让用户的合规材料凭空消失。
                raise BuildError(f"安装树缺少许可证文件：{name}")
            candidates.append(path)
        for pattern in APP_TREE_ROOT_GLOBS:
            candidates.extend(sorted(root.glob(pattern)))
    else:
        for entry in PACKAGE_ROOTS:
            path = root / entry
            if not path.exists():
                if entry in ("requirements.txt", "oss-registry.yaml"):
                    _log(f"  [跳过] 未找到 {entry}（允许缺失）")
                    continue
                raise BuildError(f"缺少打包输入：{entry}")
            if path.is_dir():
                candidates.extend(sorted(path.rglob("*")))
            else:
                candidates.append(path)

    for candidate in candidates:
        if candidate.is_symlink():
            raise BuildError(f"不允许符号链接：{candidate.relative_to(root).as_posix()}")
        if not candidate.is_file():
            continue
        parts = candidate.relative_to(root).parts
        if any(part in SKIP_DIR_NAMES for part in parts):
            continue
        if app_tree:
            # 安装树里的运行时、用户数据、卸载器、升级状态与冒烟残留都不进升级包。
            if candidate.suffix.lower() in APP_TREE_SKIP_SUFFIXES:
                continue
            if parts[0].startswith(APP_TREE_SKIP_PREFIXES):
                continue
            if candidate.relative_to(root).as_posix() == MANIFEST_NAME:
                continue
        elif candidate.suffix.lower() in SKIP_SUFFIXES:
            continue
        rel = candidate.relative_to(root).as_posix()
        if app_tree:
            rel = _relocate_for_upgrade(rel)
        if rel in seen:
            raise BuildError(f"重复路径：{rel}")
        seen.add(rel)
        items.append((rel, candidate))
    items.sort(key=lambda item: item[0])
    if not items:
        raise BuildError("没有可打包的文件")
    if len(items) > MAX_ENTRIES:
        raise BuildError(f"文件数量过多（{len(items)} > {MAX_ENTRIES}）")
    total = sum(path.stat().st_size for _rel, path in items)
    if total > MAX_UNCOMPRESSED:
        raise BuildError(f"解压后体积超过上限（{total} 字节 > {MAX_UNCOMPRESSED}）")
    return items


def _relocate_for_upgrade(rel: str) -> str:
    """把合规材料从包根移到 ``docs/compliance/``（仅升级包布局）。

    升级器只写 ``INSTALL_ROOTS`` 白名单内的顶层目录；根目录出现 ``licenses/``
    或 ``THIRD-PARTY-NOTICES.txt`` 会被整包拒绝。安装包本体仍保留根目录副本
    （安装器直接铺到 ``{app}``），这里只改升级 zip 内的相对路径。
    """
    if rel in ("THIRD-PARTY-NOTICES.txt", "sbom-cyclonedx.json") or rel.startswith("licenses/"):
        return f"{UPGRADE_COMPLIANCE_DIR}/{rel}"
    return rel


def _collect_docs(root: Path) -> List[Tuple[str, Path]]:
    """按 ``DOC_ALLOWLIST`` 收集用户文档；任何一项缺失都中止构建。

    ``root`` 是文档来源根（安装树模式取仓库根，因为它与安装树同一次构建）。
    不使用通配：新增文件必须有人主动写进白名单，才会进包。
    """
    missing = [rel for rel in DOC_ALLOWLIST if not (root / rel).is_file()]
    if missing:
        raise BuildError(
            "升级包文档白名单缺失文件："
            + "、".join(missing)
            + "。白名单缺失时拒绝出包——否则用户指南会静默少带。"
            "新增或改名文档请同步更新 packaging/make_release.py::DOC_ALLOWLIST。"
        )
    return [(rel, root / rel) for rel in DOC_ALLOWLIST]


def _zip_write(zf: zipfile.ZipFile, rel: str, data: bytes) -> None:
    info = zipfile.ZipInfo(filename=rel, date_time=FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    zf.writestr(info, data)


def _scan_dev_paths(entries: List[Tuple[str, Path]]) -> List[Tuple[str, int, str]]:
    hits: List[Tuple[str, int, str]] = []
    for rel, path in entries:
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            if path.stat().st_size > 4 * 1024 * 1024:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if DEV_PATH_RE.search(line):
                hits.append((rel, lineno, line.strip()[:200]))
    return hits


def _write_report(out_dir: Path, hits: List[Tuple[str, int, str]]) -> Path:
    report = out_dir / "dev-path-report.txt"
    runtime = [item for item in hits if not item[0].startswith("docs/")]
    docs = [item for item in hits if item[0].startswith("docs/")]
    lines = [
        "开发目录路径扫描（packaging/make_release.py）",
        f"生成时间：{_now_iso()}",
        f"运行文件命中：{len(runtime)}（src/ 等参与运行的文件，属于缺陷）",
        f"文档引用命中：{len(docs)}（docs/ 里的历史链接，不影响安装与升级）",
        "",
        "说明：--strict-dev-paths 会把“运行文件命中”当作构建失败。",
        "",
        "[运行文件命中]",
    ]
    for rel, lineno, text in runtime:
        lines.append(f"{rel}:{lineno}: {text}")
    lines.append("")
    lines.append("[文档引用命中]")
    for rel, lineno, text in docs:
        lines.append(f"{rel}:{lineno}: {text}")
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def _load_signing_key(path: Path) -> bytes:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    try:
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    except Exception as exc:  # noqa: BLE001 - 任何解析失败都要明确报错
        raise BuildError(f"签名私钥无法解析（需要未加密的 Ed25519 PEM）：{path}（{exc}）") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise BuildError(f"签名私钥不是 Ed25519：{path}（实际 {type(key).__name__}）")
    return key


def _verify_with_updater(zf: zipfile.ZipFile) -> None:
    """复用升级器的归档校验，确保打包端与安装端同一套规则。"""
    from src.upgrade.archive import validate_release_zip

    members = validate_release_zip(
        zf,
        install_roots=INSTALL_ROOTS,
        protected_prefixes=PROTECTED_PATHS,
        max_uncompressed_bytes=MAX_UNCOMPRESSED,
        max_entries=MAX_ENTRIES,
    )
    if not any(rel == "pyproject.toml" for rel, _info in members):
        raise BuildError("发布包内缺少 pyproject.toml")


def _verify_package(zip_path: Path, expected_version: str, *, require_console: bool = False) -> Tuple[str, int]:
    """回读 ZIP，校验版本标记与 pyproject 版本；返回 (sha256, size)。"""
    sha = hashlib.sha256()
    with open(zip_path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(chunk)
    size = zip_path.stat().st_size

    with zipfile.ZipFile(zip_path) as zf:
        _verify_with_updater(zf)
        if require_console:
            # 同一个校验函数升级器落盘前也会跑：打包端与安装端共用一套规则。
            try:
                verify_console_contract(zf, expected_version)
            except FrontendContractError as exc:
                raise BuildError(str(exc)) from exc
        try:
            marker = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BuildError(f"发布包根目录缺少可解析的 {MANIFEST_NAME}：{exc}") from exc
        if marker.get("schema") != PACKAGE_SCHEMA:
            raise BuildError(f"包内清单 schema 必须是 {PACKAGE_SCHEMA}，实际 {marker.get('schema')!r}")
        if marker.get("product") != PRODUCT:
            raise BuildError(f"包内清单 product 必须是 {PRODUCT}，实际 {marker.get('product')!r}")
        marker_version = str(marker.get("version") or "").strip()
        if marker_version != expected_version:
            raise BuildError(
                f"包内 LIUHAO_RELEASE.json 版本 {marker_version!r} 与清单版本 {expected_version!r} 不一致"
            )
        try:
            pyproject_version = _read_pyproject_version_from_bytes(zf.read("pyproject.toml"))
        except KeyError as exc:
            raise BuildError("发布包内缺少 pyproject.toml") from exc
        if pyproject_version != expected_version:
            raise BuildError(
                f"包内 pyproject.toml 版本 {pyproject_version!r} 与清单版本 {expected_version!r} 不一致"
            )
    return sha.hexdigest(), size


def _read_pyproject_version_from_bytes(payload: bytes) -> str:
    data = tomllib.loads(payload.decode("utf-8"))
    project = data.get("project")
    value = project.get("version") if isinstance(project, dict) else None
    if not isinstance(value, str) or not value.strip():
        raise BuildError("包内 pyproject.toml 缺少 [project].version")
    return value.strip()


def _merge_history(manifest_path: Path, new_release: Dict[str, Any]) -> List[Dict[str, Any]]:
    releases: List[Dict[str, Any]] = []
    if manifest_path.is_file():
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
            for item in previous.get("releases") or []:
                version = str(item.get("version") or "").strip()
                if not version or version == new_release["version"]:
                    continue
                releases.append(item)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            _log("  [提示] 既有发布清单不可解析，将只写入本次发布")
    releases.append(new_release)
    try:
        from packaging.version import Version

        releases.sort(key=lambda item: Version(str(item.get("version"))))
    except Exception:  # noqa: BLE001 - 排序失败不影响清单有效性
        pass
    return releases


def _verify_manifest_file(manifest_path: Path, zip_path: Path, version: str) -> None:
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BuildError(f"回读发布清单失败：{exc}") from exc
    if data.get("schema") != MANIFEST_SCHEMA:
        raise BuildError(f"发布清单 schema 必须是 {MANIFEST_SCHEMA}")
    releases = data.get("releases")
    if not isinstance(releases, list) or not releases:
        raise BuildError("发布清单 releases 必须是非空数组")
    try:
        from src.upgrade.sources import parse_manifest, select_latest

        parsed = parse_manifest(json.dumps(data, ensure_ascii=False))
        latest = select_latest(parsed)
    except Exception as exc:  # noqa: BLE001 - 升级器的校验失败就是构建失败
        raise BuildError(f"发布清单未通过升级器校验：{exc}") from exc
    if latest.version != version:
        raise BuildError(f"发布清单最新版本 {latest.version!r} 不是本次版本 {version!r}")
    if latest.artifact != zip_path.name:
        raise BuildError(f"发布清单 artifact {latest.artifact!r} 与产物文件名 {zip_path.name!r} 不一致")
    if not latest.files:
        raise BuildError("发布清单缺少 files 文件级清单，安装端会拒绝该包")
    if latest.data_schema_version != DATA_SCHEMA_VERSION:
        raise BuildError(
            f"发布清单 data_schema_version {latest.data_schema_version!r} "
            f"与当前代码 {DATA_SCHEMA_VERSION} 不一致"
        )
    sha, size = _verify_package(zip_path, version)
    if latest.sha256 != sha:
        raise BuildError("发布清单 sha256 与回读 ZIP 计算值不一致")
    if latest.size != size:
        raise BuildError(f"发布清单 size {latest.size} 与回读 ZIP 字节数 {size} 不一致")
    with zipfile.ZipFile(zip_path) as zf:
        try:
            verify_file_manifest(zf, latest.files, source="发布包")
        except PackageValidationError as exc:
            raise BuildError(str(exc)) from exc


def _run_cli_check(root: Path, out_dir: Path) -> Tuple[int, str]:
    """用真实升级 CLI 读一遍刚产出的清单（版本与当前源码一致时应为“已是最新”）。

    检查过程不得往安装树写任何东西：状态/备份目录指向临时目录，且禁止
    生成 ``.pyc``。否则构建流水线会把 ``upgrade-state/``、``__pycache__``
    直接打进安装包。
    """
    state_dir = Path(tempfile.mkdtemp(prefix="liuhao-release-check-"))
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYTHONPATH", None)
    cmd = [
        sys.executable,
        "-m",
        "src.upgrade",
        "check",
        "--app-root",
        str(root),
        "--release-dir",
        str(out_dir),
        "--state-dir",
        str(state_dir / "state"),
        "--backup-root",
        str(state_dir / "backups"),
    ]
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
    finally:
        shutil.rmtree(state_dir, ignore_errors=True)
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def _verify_console_provenance(entries, version: str) -> None:
    """校验包内控制台确实由当前前端源码构建，且产物未被替换。"""
    console_bytes = {
        rel: path.read_bytes() for rel, path in entries if rel.startswith("console/")
    }
    meta_rel = console_provenance.BUILD_META_RELPATH
    if meta_rel not in console_bytes:
        raise BuildError(
            "升级包里的控制台缺少 console/console-build.json 构建来源记录："
            "无法证明界面来自当前源码，升级后可能仍是旧界面。请先执行 "
            "packaging/build_console.py --dest <安装树>/console "
            f"--version {version} 再重新打包。"
        )
    try:
        meta = json.loads(console_bytes[meta_rel].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BuildError(f"控制台构建来源记录无法解析：{exc}") from exc

    source_dir = REPO_ROOT / "apps" / "console" / "console"
    if not source_dir.is_dir():
        raise BuildError(
            f"无法校验控制台来源：前端源码目录不存在（{source_dir}）。"
            "请在完整仓库内打包，或先恢复 apps/console/console。"
        )
    expected_source = console_provenance.frontend_source_fingerprint(source_dir)
    recorded_source = str(meta.get("source_sha256") or "")
    if recorded_source != expected_source:
        raise BuildError(
            "包内控制台不是由当前前端源码构建的："
            f"源码指纹 {expected_source[:12]}…，构建记录 {recorded_source[:12] or '<缺失>'}…。"
            "升级后界面仍是旧版，拒绝出包。请重新执行 packaging/build_console.py "
            f"--version {version}。"
        )

    recorded_version = str(meta.get("version") or "")
    if recorded_version != version:
        raise BuildError(
            f"控制台构建记录版本 {recorded_version!r} 与发布版本 {version!r} 不一致："
            "请按当前版本重新构建控制台后再打包。"
        )

    actual_files = {
        rel: data
        for rel, data in console_bytes.items()
        if rel not in (meta_rel, CONTRACT_RELPATH)
    }
    actual_fp = console_provenance.console_files_fingerprint(actual_files)
    recorded_files = str(meta.get("files_sha256") or "")
    if recorded_files != actual_fp:
        raise BuildError(
            "包内控制台文件在构建后被改动："
            f"文件指纹 {actual_fp[:12]}…，构建记录 {recorded_files[:12] or '<缺失>'}…。"
            "拒绝出包；请重新构建控制台。"
        )
    _log(
        f"  控制台  来源指纹 {expected_source[:12]}… 与当前源码一致，"
        f"产物 {len(actual_files)} 个文件（{actual_fp[:12]}…）"
    )


def build(args: argparse.Namespace) -> int:
    app_tree = bool(getattr(args, "app_root", None))
    if not app_tree:
        raise BuildError(
            "拒绝使用默认仓库根模式打包：该模式不会把根级 error_types.py 与 "
            "capability-registry.yaml 打进升级包，安装后自检会以 ModuleNotFoundError "
            "失败并触发回滚（默认值等于会踩）。请显式传入 --app-root <安装树>；"
            "安装树由 packaging/stage_payload.py 生成。"
        )
    root = Path(args.app_root).resolve()
    out_dir = (
        Path(args.out).resolve()
        if args.out
        else REPO_ROOT / "dist" / "release"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    pyproject = root / "pyproject.toml"
    version = (args.version or _read_pyproject_version(pyproject)).strip()
    _validate_version(version, "发布版本号")
    if args.version:
        _validate_version(_read_pyproject_version(pyproject), "pyproject.toml 版本号")
        if version != _read_pyproject_version(pyproject):
            raise BuildError(
                "--version 与 pyproject.toml 版本不一致："
                f"{version!r} != {_read_pyproject_version(pyproject)!r}"
            )

    released_at = args.released_at or _now_iso()
    zip_name = f"liuhao-{version}-upgrade.zip"
    zip_path = out_dir / zip_name
    manifest_path = out_dir / MANIFEST_NAME

    _log(f"鎏灏发布包构建  version={version}")
    _log(f"  来源    {'安装树 ' if app_tree else '仓库 '}{root}")
    _log(f"  输出    {out_dir}")

    entries = _collect_files(root, app_tree=app_tree)
    # 文档不整目录进包：只带用户真的会打开的白名单文件。安装树模式从仓库取
    # （安装树本身没有 docs/），仓库模式直接从打包根取。
    doc_entries = _collect_docs(REPO_ROOT if app_tree else root)
    existing = {rel for rel, _path in entries}
    duplicated = [rel for rel, _path in doc_entries if rel in existing]
    if duplicated:
        raise BuildError("文档白名单与打包输入重复：" + ", ".join(duplicated))
    entries.extend(doc_entries)
    entries.sort(key=lambda item: item[0])
    _log(f"  文档    用户文档白名单 {len(doc_entries)} 个文件（内部文档不进包）")
    if app_tree and not any(rel == "console/index.html" for rel, _path in entries):
        raise BuildError(
            "安装树缺少驾驶舱构建产物 console/index.html："
            "此包升级后界面不会更新，且可能与新后端接口错配，拒绝产出。"
            "请先在 apps/console/console 执行 npm install && npm run build，再重建安装树。"
        )
    _verify_console_provenance(entries, version)
    total_bytes = sum(path.stat().st_size for _rel, path in entries)
    _log(f"  输入    {len(entries)} 个文件 / {total_bytes / 1024 / 1024:.1f} MB（未压缩）")

    present = {rel for rel, _path in entries}
    missing_required = [rel for rel in REQUIRED_RELEASE_FILES if rel not in present]
    if missing_required:
        raise BuildError(
            "打包输入缺少关键文件："
            + ", ".join(missing_required)
            + "。整包哈希无法证明包内文件齐全，装上去会新旧代码混跑，拒绝产出。"
        )

    hits = _scan_dev_paths(entries)
    runtime_hits = [item for item in hits if not item[0].startswith("docs/")]
    report = _write_report(out_dir, hits)
    for scope in ("src", "docs"):
        count = sum(1 for rel, _ln, _text in hits if rel.startswith(scope + "/"))
        if count:
            _log(f"  [开发路径] {scope}/ 命中 {count} 个文件（详见 {report}）")
    if runtime_hits and args.strict_dev_paths:
        for rel, lineno, text in runtime_hits[:20]:
            _log(f"    {rel}:{lineno}: {text}")
        raise BuildError(
            f"--strict-dev-paths：发布包仍有 {len(runtime_hits)} 处开发目录路径字面量，"
            f"需先由源码归属方清理（报告：{report}）"
        )

    marker = {
        "schema": PACKAGE_SCHEMA,
        "product": PRODUCT,
        "version": version,
        "released_at": released_at,
    }
    marker_bytes = (json.dumps(marker, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    # 重新签章：安装树里的旧签章带着旧版本号/旧指纹，不能原样带进新包。
    console_stamp_bytes = b""
    if app_tree:
        console_files = console_files_under(
            {rel: path.read_bytes() for rel, path in entries if rel.startswith("console/")}
        )
        backend_contract = read_backend_contract_from_source(
            (root / "src" / "gateway" / "contract.py").read_text(encoding="utf-8")
        )
        stamp = build_stamp(
            console_files,
            backend_contract=backend_contract,
            version=version,
            built_at=released_at,
        )
        console_stamp_bytes = (
            json.dumps(stamp, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        _log(
            f"  前端    console {stamp['console_files']} 个文件，"
            f"api_contract={stamp['api_contract']}，指纹 {stamp['console_fingerprint'][:12]}…"
        )

    tmp_zip = out_dir / f".{zip_name}.tmp"
    tmp_zip.unlink(missing_ok=True)
    with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        _zip_write(zf, MANIFEST_NAME, marker_bytes)
        for rel, path in entries:
            if rel == CONTRACT_RELPATH:
                continue
            _zip_write(zf, rel, path.read_bytes())
        if console_stamp_bytes:
            _zip_write(zf, CONTRACT_RELPATH, console_stamp_bytes)
    os.replace(tmp_zip, zip_path)

    sha256, size = _verify_package(zip_path, version, require_console=app_tree)
    _log(f"  产物    {zip_name}  {size} 字节（{size / 1024 / 1024:.1f} MB）")
    _log(f"  SHA256  {sha256}")

    file_entries = build_file_manifest_for_zip(zip_path)
    _log(f"  清单    {len(file_entries)} 个文件（逐文件 SHA256 已写入发布清单）")

    signature: str | None = None
    key_path = args.signing_key or os.environ.get("LIUHAO_UPGRADE_SIGNING_KEY") or ""
    if key_path:
        key_file = Path(key_path).expanduser()
        if not key_file.is_file():
            raise BuildError(f"配置了签名私钥但文件不存在：{key_file}（拒绝产出未签名清单冒充已签名）")
        if size > MAX_SIGNING_BYTES:
            raise BuildError(f"发布包超过 {MAX_SIGNING_BYTES} 字节，拒绝在内存中签名")
        private_key = _load_signing_key(key_file)
        signature = base64.b64encode(private_key.sign(zip_path.read_bytes())).decode("ascii")
        _log("  签名    Ed25519（对 ZIP 完整字节）已写入清单")
    else:
        _log("  签名    未配置私钥，清单不含 signature 字段（不写空值/假签名）")

    release: Dict[str, Any] = {
        "version": version,
        "artifact": zip_name,
        "sha256": sha256,
        "size": size,
        "notes": args.notes or (
            f"LiuHao AI OS · 鎏灏智能中枢 {version}：源码与文档整包；不含模型权重、不含任何密钥。"
            "安装器未做代码签名，Windows 首次运行需选择“仍要运行”。"
        ),
        "released_at": released_at,
        "files": file_entries,
        "data_schema_version": DATA_SCHEMA_VERSION,
    }
    if signature:
        release["signature"] = signature
        release["signature_algorithm"] = "ed25519"
    if runtime_hits:
        release["notes"] = (
            release["notes"]
            + f"（打包扫描发现 {len(runtime_hits)} 处运行文件里的开发目录路径字面量，见 dev-path-report.txt）"
        )

    releases = _merge_history(manifest_path, release)
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "channel": args.channel,
        "generated_at": _now_iso(),
        "releases": releases,
    }
    tmp_manifest = out_dir / f".{MANIFEST_NAME}.tmp"
    tmp_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(tmp_manifest, manifest_path)
    _log(f"  清单    {manifest_path}")

    _verify_manifest_file(manifest_path, zip_path, version)
    _log("  自检    清单 size/sha256/版本/artifact 与回读 ZIP 一致")

    if not args.no_cli_check:
        code, output = _run_cli_check(root, out_dir)
        _log(f"  升级器  python -m src.upgrade check --release-dir {out_dir} → exit {code}")
        for line in output.strip().splitlines():
            _log(f"          {line}")
        if code != 0:
            _log("  [失败] 升级器没有读取通过这份清单（见上方输出）")
            manifest_path.unlink(missing_ok=True)
            raise BuildError("升级器校验未通过：已删除本次清单，不产出坏清单")

    _log("")
    _log(f"完成：{zip_path}")
    _log(f"      {manifest_path}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python packaging/make_release.py",
        description="生成升级发布包 ZIP 与 LIUHAO_RELEASE.json（规范见 docs/self-upgrade.md §2.2）",
    )
    parser.add_argument("--root", default=str(REPO_ROOT), help="（保留参数；仓库根模式已停用，必须用 --app-root）")
    parser.add_argument(
        "--app-root",
        default=None,
        help="必需：按安装树打包（指向 payload/app）；升级包从此目录收集，与安装后结构一致",
    )
    parser.add_argument("--out", default=None, help="输出目录（默认仓库根 dist/release）")
    parser.add_argument("--version", default=None, help="发布版本号（默认读 pyproject.toml，必须一致）")
    parser.add_argument("--signing-key", default=None, help="Ed25519 私钥 PEM；也可用环境变量 LIUHAO_UPGRADE_SIGNING_KEY")
    parser.add_argument("--notes", default=None, help="清单 notes（默认自动生成）")
    parser.add_argument("--channel", default="stable", help="清单 channel 字段（升级器忽略）")
    parser.add_argument("--released-at", default=None, help="released_at（默认当前 UTC 时间）")
    parser.add_argument(
        "--strict-dev-paths",
        action="store_true",
        help="开发目录路径字面量直接判定构建失败（源码清理完成后建议打开）",
    )
    parser.add_argument(
        "--no-cli-check",
        action="store_true",
        help="不调用 src.upgrade check 做端到端读取验证（仅排查环境问题用）",
    )
    return parser


def main(argv: List[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = _build_parser().parse_args(argv)
    try:
        return build(args)
    except BuildError as exc:
        print(f"[失败] {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
