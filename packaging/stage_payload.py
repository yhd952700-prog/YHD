#!/usr/bin/env python
"""组装 Windows 安装包 payload（程序目录内容）。

产物结构（``--out`` 默认 ``dist/payload``）::

    dist/payload/
      app/                     ← 安装到 D:\\LiuHao（回退 %LOCALAPPDATA%\\Programs\\LiuHao）的全部内容
        src/                   ← 网关 / 内核源码
        config/                ← 配置模板（已剔除任何凭据与密钥）
        console/               ← 驾驶舱构建产物（前端 build 输出）
        scripts/               ← 启动器 / 首次运行向导 / 身份注册
        runtime/               ← 离线 Python 运行时（含全部第三方依赖）
        capability-registry.yaml
        LiuHao.bat             ← 快捷方式目标
        START-HERE.txt         ← 首次使用说明（中文）
        assets/liuhao.ico      ← 安装器/快捷方式图标（由前端图标自动转换）
      manifest.json            ← 本次构建的内容清单（不随安装复制）

设计约束
--------
* **不含任何密钥**：``config/auth_secrets.json``、``config/human_identities.json``、
  ``external_anchor/anchor_key.pem``、``docker.env`` 等一律不进包。
* **不含模型权重**：Ollama 模型 / 本地 GGUF 由用户自行安装，或首次运行时填
  API Key。这里只带运行时依赖。
* **离线可运行**：运行时来自构建机的 CPython 安装 + 当前 venv 里网关真实
  import 图命中的发行包，收件人无需装 Python、无需联网装依赖。
* 中间产物全部落在 ``dist/``（已被 .gitignore 忽略），不提交。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "dist" / "payload"
CONSOLE_DIST = REPO_ROOT / "apps" / "console" / "console" / "dist"
ICON_PNG = REPO_ROOT / "apps" / "console" / "console" / "public" / "icon-192.png"
LICENSES_DIR = REPO_ROOT / "packaging" / "licenses"

#: 上游 wheel **未随包**许可证文本的发行包：构建时把上游 LICENSE 原文补进
#: ``*.dist-info/licenses/``。key 为 METADATA ``Name`` 的小写形式，
#: value = (``licenses/`` 下的文件名, 上游来源链接)。
#:
#: 为什么必须补进 dist-info：许可证声明生成器（``third_party_notices``）与
#: SBOM 合规门禁（``sbom_audit.py --check``）都以「dist-info 里的真实文本」为准，
#: 补文件而不是放宽这两处校验。
DIST_INFO_LICENSE_SOURCES: Dict[str, Tuple[str, str]] = {
    "fastmcp-slim": (
        "fastmcp-slim-Apache-2.0.txt",
        "https://github.com/PrefectHQ/fastmcp/blob/main/LICENSE",
    ),
    "opentelemetry-util-http": (
        "opentelemetry-util-http-Apache-2.0.txt",
        "https://github.com/open-telemetry/opentelemetry-python-contrib/blob/main/LICENSE",
    ),
    # proxy_tools 0.1.0 只有 sdist，且 dist-info 里不带任何许可证文本
    # （METADATA 声明 MIT，上游实际文件 LICENSE.txt 是 BSD 正文，以实际文本为准）。
    # 不补的话 sbom_audit --check 会以「发行包无随包许可证文本」中止出包。
    "proxy_tools": (
        "proxy_tools-BSD.txt",
        "https://github.com/jtushman/proxy_tools/blob/master/LICENSE.txt",
    ),
}

#: 产品自身的许可证（仓库根，按顺序取第一个存在的，统一落成 ``{app}\LICENSE``）。
#: 仓库里目前没有这类文件时**不伪造许可条款**：安装目录会缺 LICENSE，
#: 安装后自检（dist/release-gate/verify_installed_compliance.py）会把它列为缺项。
PRODUCT_LICENSE_NAMES: Tuple[str, ...] = (
    "LICENSE",
    "LICENSE.txt",
    "LICENSE.md",
    "COPYING",
    "COPYING.txt",
)

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.upgrade.console_contract import (  # noqa: E402
    build_stamp,
    console_files_under,
    read_backend_contract_from_source,
)

#: 随程序走的源码目录。
SOURCE_DIRS: Tuple[str, ...] = ("src",)

#: ``config/`` 里绝不进包的东西：凭据、身份登记表、私钥、容器环境变量。
#: 条目按**完整文件名**或 ``*.ext`` 匹配（见 ``_copy_config``）。
CONFIG_EXCLUDES: Tuple[str, ...] = (
    "production",       # 目录：生产部署脚本/验收清单
    "production.yaml",  # 文件：此前误写成 "production"，只挡住了同名目录
    "external_anchor",  # 目录：外部锚点记录与私钥
    "auth_secrets.json",
    "human_identities.json",
    "docker.env",
    "*.pem",
    "*.key",
    "*.lock",           # 运行时锁文件（如 auth_secrets.json.lock），不进包
    # 开发/部署侧的模板，安装版一律不用：随包会让用户改错文件、也会把
    # 构建机的运维拓扑（端口/告警规则/Grafana 面板）带出去。
    # 对应的打包后断言见 packaging/verify_payload_clean.py::FORBIDDEN_CONFIG_NAMES。
    "development.yaml",
    "testing.yaml",
    "crontab",
    "supervisor.conf",
    "nginx.conf",
    "prometheus.yml",
    "grafana-dashboard.json",
    "alert-rules.yaml",
)

#: 允许"当前 checkout 里没有可匹配对象"的防御性条目：它们保护的是运行时
#: 生成或开发机本地的文件，不能因为目录里暂时没有就删掉。其余条目必须能
#: 匹配 ``config/`` 里的真实文件/目录，否则视为失效（见
#: ``tests/test_stage_payload_config_excludes.py``）。
CONFIG_EXCLUDES_DEFENSIVE = frozenset(
    {"auth_secrets.json", "human_identities.json", "*.pem", "*.key", "*.lock"}
)

#: 仓库根需要随包的文件（roster 从包根读取；内核顶层模块被 src 直接 import）。
ROOT_FILES: Tuple[str, ...] = ("capability-registry.yaml", "error_types.py")

#: 版本/依赖声明。安装目录必须能自行确定版本：升级包应用的第一个前置就是
#: 读当前版本，缺了 ``pyproject.toml`` 会直接以“无法确定当前版本”失败。
ROOT_METADATA_FILES: Tuple[str, ...] = (
    "pyproject.toml",
    "requirements.txt",
    "oss-registry.yaml",
)

#: 开发机路径标记。仓库绝对路径出现在随包文件里属于打包缺陷（硬拦截）；
#: ``.venv`` 相关的是布局探测常量/用法示例，安装目录永远不会命中，记录备查。
DEV_PATH_MARKERS: Tuple[str, ...] = (
    "D:/LiuHao-AI-OS",
    "D:\\LiuHao-AI-OS",
    "D:\\\\LiuHao-AI-OS",   # 源码里被转义后的写法（文件实际内容是两个反斜杠）
    ".venv/Scripts",
    ".venv\\Scripts",
    ".venv\\\\Scripts",
)

#: 安装版运行时需要的脚本（向导 / 启动 / 身份注册 / 托盘自启 / HTTPS）。
PAYLOAD_SCRIPTS: Tuple[str, ...] = (
    # 图形化首启（默认入口：不读 stdin，双击即可完成账号/模型/数据目录）。
    "scripts/first_run_gui.py",
    # 命令行首启（--console 回退）。
    "scripts/first_run_wizard.py",
    "scripts/register_human_identity.py",
    "scripts/start_liuhao.py",
    "scripts/launch_installed.py",
    # 托盘自启入口：HKCU Run 项直接指向它，缺了就只能拉起网关、没有托盘。
    "scripts/liuhao_desktop.py",
    "scripts/ops/https_proxy.py",
    "scripts/gen_local_cert.py",
    # 运行时错误提示里点名要用户去跑的脚本：不随包就等于给用户一条
    # 装完后无路可走的命令（审计哈希迁移 / 审批令牌 / 备份 / 发布清单签名）。
    # 同一 Wi-Fi 手机访问（A 档）由网关进程内加载中继脚本；缺了这一行，
    # 设置里的开关在安装版上会直接报"找不到中继脚本"。
    "scripts/ops/lan_relay.py",
    "scripts/mobile_access.py",
    "scripts/migrate_p08_hash_alg.py",
    "scripts/issue_console_token.py",
    "scripts/ops/backup.py",
    "scripts/ops/sign_release_manifest.py",
    # 覆盖升级后按排除清单清理程序目录里的旧配置（安装器 [Run] 调用）。
    "scripts/purge_installed_config.py",
    # 覆盖升级后按 prune-manifest.json 删除「新版 payload 已剪掉」的旧文件
    # （安装器 [Run] 调用；全新安装时空转）。
    "scripts/purge_pruned_payload.py",
    # 卸载前递归清 __pycache__（安装器 [UninstallRun] 调用；用 Python 而非
    # PowerShell，避免路径里的单引号/元字符破坏命令行）。
    "scripts/purge_pycache.py",
    # 服务未启动时也能出包的双击入口（离线诊断）。
    "scripts/collect_diagnostics.bat",
)

#: import 图看不到、但运行期确实会用的发行包（延迟导入 / 子进程入口）。
EXPLICIT_DISTS: Tuple[str, ...] = (
    "RestrictedPython",  # 沙箱子进程内导入
    "uvicorn",           # 启动器用 ``python -m uvicorn``
    "python-dotenv",     # 启动器读取 app.env
    "aiosqlite",         # 异步 SQLite 驱动（延迟导入）
    "cffi",              # cryptography 的 C 绑定（延迟导入 _cffi_backend）
    "h11",               # uvicorn 的 HTTP 协议实现（延迟导入）
    "click",             # uvicorn CLI 入口
    "websockets",        # 网关 WebSocket 路由（按需导入）
    "python-multipart",  # 表单/文件上传路由（按需导入）
    "jiter",             # openai 的 JSON 解析器，只在首次请求时导入
    "httpx",             # src/ai/providers.py 自建 Client（openai 只带 httpx2）
    "httpcore",          # httpx 的底层传输实现
    "anthropic",         # Anthropic provider（providers.py 方法体内延迟导入）
    "google-genai",      # Gemini provider（providers.py 方法体内延迟导入 google.genai）
    "python-docx",       # .docx 知识导入（模块名 docx）
    "lxml",              # python-docx 的 XML 后端
    "pypdf",             # .pdf 知识导入
    "sqlalchemy",        # 记忆/知识库 ORM（方法体内延迟导入）
    "greenlet",          # SQLAlchemy 的协程运行时
    "taskiq",            # src/tasks/broker.py 任务代理（方法体内延迟导入）
    "pywin32",           # 系统托盘 win32api/win32gui（importlib 延迟导入）
)

#: 不属于任何发行包的伪模块（Cython 注入、Windows 解释器垫片、本仓库根模块）。
PSEUDO_MODULES: Set[str] = {
    "cython_runtime",
    "sitecustomize",
    "pywin32_bootstrap",
    "pywin32_system32",
    "error_types",  # 仓库根 error_types.py
}

#: 运行时不需要的标准库/工具目录（减小体积，不影响网关）。
STDLIB_SKIP_DIRS: Tuple[str, ...] = (
    "site-packages",
    "idlelib",
    "turtledemo",
    "lib2to3",
    "ensurepip",
    "curses",
    "msilib",
    "test",
)

_IGNORE = shutil.ignore_patterns(
    "__pycache__", "*.pyc", "*.pyo", "*.egg-info",
    "*.db", "*.db-wal", "*.db-shm", "*.sqlite3", "*.sqlite3-wal", "*.sqlite3-shm",
)

#: 复制第三方发行包（含无 RECORD 的回退分支）时额外剔除的上游测试内容。
_DIST_IGNORE = shutil.ignore_patterns(
    "__pycache__", "*.pyc", "*.pyo", "*.egg-info",
    "*.db", "*.db-wal", "*.db-shm", "*.sqlite3", "*.sqlite3-wal", "*.sqlite3-shm",
    "conftest.py", "tox.ini", "pytest.ini", "setup.cfg",
    "test", "tests", "testing", "test_*.py", "*_test.py",
)

#: 第三方发行包里只服务于上游测试、不属于运行时交付物的路径。
#: 上一版交付被审计出 344 个包内测试文件（pytest 用例/夹具/覆盖率配置），
#: 既撑大安装包，也让用户目录里出现和产品无关的脚本。
TEST_DIR_NAMES = frozenset({"test", "tests", "testing"})
TEST_FILE_NAMES = frozenset({"conftest.py", "tox.ini", "pytest.ini", "setup.cfg"})
TEST_FILE_GLOBS = ("test_*.py", "*_test.py")


def _is_test_artifact(relative: Path) -> bool:
    """发行包内的相对路径是否属于上游测试文件/目录。"""
    if any(part.lower() in TEST_DIR_NAMES for part in relative.parts[:-1]):
        return True
    name = relative.name.lower()
    if name in TEST_FILE_NAMES:
        return True
    return any(relative.match(pattern) for pattern in TEST_FILE_GLOBS)


# --------------------------------------------------------------------------- #
# 打包剪枝（A1–A13，见 docs/packaging-slimming-spec-20261108.md）
# --------------------------------------------------------------------------- #
#
# 为什么放在打包阶段而不是运行期：这些文件**从来没有被 import 过**，删的是安装
# 体积和首启杀软扫描面；一旦哪天真被 import 到，那是打包配置错了，必须当场炸掉。
# 所以这里同时产出两样东西：
#   * ``prune-manifest.json``（随包安装）：被删路径清单，供升级器清旧文件 + 运行期守卫读；
#   * ``_liuhao_prune_guard.py`` + ``.pth``（随包安装）：site 启动即注册 meta_path
#     查找器，命中清单模块时抛错，不让它退化成普通 ModuleNotFoundError 被吞掉。
#
# 开关：``--no-prune`` 或环境变量 ``LIUHAO_PAYLOAD_PRUNE=0``（默认开，重建即回滚）。

@dataclass(frozen=True)
class PruneRule:
    """一条剪枝规则；``id`` 用于归因（哪条规则拿走了这个文件）。"""

    id: str
    title: str
    patterns: Tuple[str, ...] = ()
    suffixes: Tuple[str, ...] = ()
    tests_dirs: bool = False
    extra_modules: Tuple[str, ...] = ()
    mode: str = "block"
    enabled: bool = True
    note: str = ""


SITE_PACKAGES_PREFIX = "runtime/lib/site-packages/"

PRUNE_RULES: Tuple[PruneRule, ...] = (
    PruneRule(
        "A1", "pythonwin IDE 与 win32ui",
        patterns=("runtime/lib/site-packages/pythonwin/*",),
        extra_modules=("pythonwin", "win32ui", "pywin", "dde"),
        note="托盘只用 win32api/win32gui/win32con/win32file/win32pipe，另在 win32\\ 目录",
    ),
    PruneRule(
        "A2", "PyWin32 离线帮助",
        patterns=("runtime/lib/site-packages/pywin32.chm",),
        note="只有 win32help/pydoc 类工具会读 .chm",
    ),
    PruneRule(
        "A3", "示例目录（win32 / win32com / tcl tk / tix demos）",
        patterns=(
            "runtime/lib/site-packages/pythonwin/pywin/demos/*",
            "runtime/lib/site-packages/win32/demos/*",
            "runtime/lib/site-packages/win32com/demos/*",
            "runtime/lib/site-packages/win32comext/*/demos/*",
            "runtime/tcl/tix8.4.3/demos/*",
            "runtime/tcl/tk8.6/demos/*",
        ),
        note="示例脚本没有任何运行期入口",
    ),
    PruneRule(
        "A4", "lxml.objectify",
        patterns=("runtime/lib/site-packages/lxml/objectify*",),
        extra_modules=("lxml.objectify",),
        note="docx 只走 lxml.etree",
    ),
    PruneRule(
        "A5", "Tcl/Tix 扩展运行库",
        patterns=("runtime/tcl/tix8.4.3/*",),
        extra_modules=("tkinter.tix",),
        note="tix 自 3.7 废弃；首启 GUI 只用 tkinter + ttk",
    ),
    PruneRule(
        "A6", "win32comext 扩展包",
        patterns=("runtime/lib/site-packages/win32comext/*",),
        extra_modules=("win32comext",),
        note="COM 扩展（adsi/mapi/shell/…）零引用；core DLL 在 pywin32_system32",
    ),
    PruneRule(
        "A7", "adodbapi 与 isapi",
        patterns=("runtime/lib/site-packages/adodbapi/*", "runtime/lib/site-packages/isapi/*"),
        extra_modules=("adodbapi", "isapi"),
        note="数据库是 SQLite；isapi 是 IIS 扩展",
    ),
    PruneRule(
        "A8", "第三方包自带 tests/test",
        tests_dirs=True,
        note="不被任何 __init__ 链在运行期导入",
    ),
    PruneRule(
        "A9", "pydoc_data",
        patterns=("runtime/lib/pydoc_data/*",),
        extra_modules=("pydoc_data",),
        note="/docs 是 FastAPI Swagger，不用 pydoc",
    ),
    PruneRule(
        "A10", "zstandard",
        patterns=("runtime/lib/site-packages/zstandard/*",),
        extra_modules=("zstandard",),
        mode="observe",
        note="httpx 在 import 时以 try/ImportError 探测它：硬报错会让网关起不来，"
             "因此只记录不阻断（evt=prune.module_imported + 台账）",
    ),
    PruneRule(
        "A11", "watchfiles",
        patterns=("runtime/lib/site-packages/watchfiles/*",),
        extra_modules=("watchfiles",),
        enabled=False,
        note="实测回滚：uvicorn.supervisors 在顶层无条件 import watchfiles，"
             "删掉后 import uvicorn 直接失败（与是否带 --reload 无关）。保留 0.65 MiB。",
    ),
    PruneRule(
        "A12", "rich + pygments（整条回滚）",
        patterns=(
            "runtime/lib/site-packages/rich/*",
            "runtime/lib/site-packages/pygments/*",
            "runtime/lib/site-packages/rich_rst/*",
        ),
        extra_modules=("rich", "pygments", "rich_rst"),
        enabled=False,
        note="实测回滚：httpx/__init__.py → _main，在顶层无条件 ``import pygments.lexers``"
             " 与 ``import rich.console``（httpx 的 CLI extra 被打进了 wheel）。"
             "删任何一半都会让 import httpx 失败，因此整条保留（约 5.53 MiB）。"
             "要真正省下这块，得先让 httpx 以 base extra 重新安装，属于依赖面改动。",
    ),
    PruneRule(
        "A13", "类型桩 / C 头 / 静态库",
        suffixes=(".pyi", ".pxd", ".pyx", ".c", ".h", ".lib"),
        note="解释器运行期只加载 .py 与 .pyd",
    ),
)

PRUNE_MANIFEST_NAME = "prune-manifest.json"
PRUNE_GUARD_MODULE = "_liuhao_prune_guard"
PRUNE_GUARD_PTH = "liuhao_prune_guard.pth"


def _match_rule(rel: str) -> Tuple[PruneRule, str] | None:
    """按 A1→A13 顺序返回首个命中的规则（首命中归属，保证逐条可对账）。"""
    lower = rel.lower().replace("\\", "/")
    for rule in PRUNE_RULES:
        if not rule.enabled:
            continue
        if rule.patterns and any(fnmatchcase(lower, pattern) for pattern in rule.patterns):
            return rule, "pattern"
        if rule.tests_dirs and lower.startswith(SITE_PACKAGES_PREFIX):
            parts = lower[len(SITE_PACKAGES_PREFIX):].split("/")[:-1]
            if any(part in TEST_DIR_NAMES for part in parts):
                return rule, "tests-dir"
        if rule.suffixes and lower.startswith(SITE_PACKAGES_PREFIX):
            if lower.endswith(rule.suffixes):
                return rule, "suffix"
    return None


def _module_of(rel: str) -> str | None:
    """把被删文件路径换算成可导入模块名（.pyd / .py；``__init__`` 归到包名）。"""
    lower = rel.lower().replace("\\", "/")
    if lower.startswith("runtime/lib/pydoc_data/"):
        parts = lower[len("runtime/lib/pydoc_data/"):].split("/")
        prefix: List[str] = ["pydoc_data"]
    elif lower.startswith(SITE_PACKAGES_PREFIX):
        parts = lower[len(SITE_PACKAGES_PREFIX):].split("/")
        prefix = []
    else:
        return None
    filename = parts[-1]
    if not filename.endswith((".py", ".pyd")):
        # .pyi/.pxd/.pyx 不是可导入模块；删桩不能连带封掉同名包。
        return None
    stem = filename.split(".", 1)[0]
    if stem == "__init__":
        tail: List[str] = parts[:-1]
    else:
        tail = parts[:-1] + [stem]
    pieces = prefix + tail
    if not pieces:
        return None
    if any(piece.endswith((".dist-info", ".egg-info")) for piece in pieces):
        return None
    if not all(piece.isidentifier() or piece == "__init__" for piece in pieces):
        return None
    return ".".join(pieces)


def _prune_modules(paths: Iterable[str]) -> Dict[str, Dict[str, str]]:
    """由剪枝路径 + 规则里显式补充的模块名，生成「模块 → 归属规则」表。"""
    owners: Dict[str, Dict[str, str]] = {}
    for rel in paths:
        rule, _why = _match_rule(rel)  # type: ignore[misc]
        name = _module_of(rel)
        if name is None:
            continue
        current = owners.get(name)
        if current is None:
            owners[name] = {"rule": rule.id, "source": rel, "mode": rule.mode}
    for rule in PRUNE_RULES:
        if not rule.enabled:
            continue
        for name in rule.extra_modules:
            owners.setdefault(name, {
                "rule": rule.id,
                "source": "(目录级规则)",
                "mode": rule.mode,
            })
    # 父包已经整块被剪时，子模块不必重复登记（导入父包就会先爆）。
    minimal: Dict[str, Dict[str, str]] = {}
    for name in sorted(owners, key=lambda item: item.count(".")):
        if any(name.startswith(parent + ".") for parent in minimal):
            continue
        minimal[name] = owners[name]
    return minimal


def _remove_empty_dirs(app_dir: Path, paths: Iterable[str]) -> int:
    """只删除「因为剪枝才变空」的目录，避免误删结构目录。"""
    candidates: Set[Path] = set()
    for rel in paths:
        parent = (app_dir / rel).parent
        while parent != app_dir and app_dir in parent.parents:
            candidates.add(parent)
            parent = parent.parent
    removed = 0
    for directory in sorted(candidates, key=lambda item: len(item.parts), reverse=True):
        try:
            directory.rmdir()
            removed += 1
        except OSError:
            continue
    return removed


_PRUNE_GUARD_SOURCE = '''"""payload 剪枝守卫（由 packaging/stage_payload.py 生成，请勿手改）。

安装包里被剪掉的第三方模块一旦真的被 import，这里会**当场报错**，而不是让
Python 抛一个普通的 ``ModuleNotFoundError`` 被上层 ``except ImportError`` 吞掉、
变成「某个功能静默消失」。可选依赖（例如 httpx 探测 zstandard）标记为
``observe``：保持 ImportError 语义让调用方正常降级，但每次都写一行
``evt=prune.module_imported`` 到 stderr 和台账 ``prune-import-ledger.jsonl``。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# 本文件位于 {app}/runtime/Lib/site-packages/，向上三级即程序根。
_MANIFEST = Path(__file__).resolve().parents[3] / "prune-manifest.json"
_LEDGER = _MANIFEST.with_name("prune-import-ledger.jsonl")


class PrunedPayloadImportError(RuntimeError):
    """被剪枝的模块被真实导入：打包配置错误，不是「可选依赖缺失」。"""


class PrunedOptionalImportError(ImportError):
    """可选依赖被剪枝：保持 ImportError 语义，但留下可追踪的记录。"""


def _load_modules() -> dict:
    try:
        data = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or not data.get("enabled"):
        return {}
    return data.get("modules") or {}


_MODULES = {str(key): dict(value) for key, value in _load_modules().items()}


def _record(name: str, info: dict) -> None:
    line = (
        "evt=prune.module_imported module=%s rule=%s source=%s mode=%s"
        % (name, info.get("rule", "?"), info.get("source", "?"), info.get("mode", "block"))
    )
    print(line, file=sys.stderr)
    try:
        with _LEDGER.open("a", encoding="utf-8") as handle:
            handle.write(line + "\\n")
    except OSError:
        pass


class _PruneFinder:
    """meta_path 首位查找器：命中的名字由我们负责报错，不再落到 PathFinder。"""

    def find_spec(self, fullname, path=None, target=None):
        info = _MODULES.get(fullname)
        if info is None:
            return None
        _record(fullname, info)
        detail = "被剪枝的模块 %s 被导入（规则 %s，来源 %s），该文件不在安装包中。" % (
            fullname, info.get("rule", "?"), info.get("source", "?"))
        if info.get("mode") == "observe":
            raise PrunedOptionalImportError(
                detail + " 这是可选依赖，调用方应降级；如需该能力请重新打包并关闭剪枝。")
        raise PrunedPayloadImportError(
            detail + " 这不是可选依赖缺失：请修正打包规则，或用 --no-prune 重新打包。")


if not any(type(item).__name__ == "_PruneFinder" for item in sys.meta_path):
    sys.meta_path.insert(0, _PruneFinder())
'''


def _write_prune_manifest(app_dir: Path, report: Dict[str, Any]) -> str:
    manifest = {
        "enabled": bool(report["enabled"]),
        "version": report.get("version", ""),
        "rules": [
            {"id": rule.id, "title": rule.title, "enabled": rule.enabled, "note": rule.note}
            for rule in PRUNE_RULES
        ],
        "paths": sorted(report.get("paths", [])),
        "modules": report.get("modules", {}),
        "stats": {
            "files": int(report.get("files", 0)),
            "freed_bytes": int(report.get("freed_bytes", 0)),
            "by_rule": report.get("by_rule", {}),
        },
    }
    target = app_dir / PRUNE_MANIFEST_NAME
    target.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
        newline="\n",
    )
    return str(target)


def _write_prune_guard(app_dir: Path) -> Tuple[Path, Path]:
    """落 site-packages 守卫 + 让 site 启动时执行它的 .pth。"""
    site_packages = app_dir / "runtime" / "Lib" / "site-packages"
    module = site_packages / f"{PRUNE_GUARD_MODULE}.py"
    module.write_text(_PRUNE_GUARD_SOURCE, encoding="utf-8", newline="\n")
    pth = site_packages / PRUNE_GUARD_PTH
    pth.write_text(f"import {PRUNE_GUARD_MODULE}\n", encoding="utf-8", newline="\n")
    return module, pth


def _guard_self_check(app_dir: Path, modules: Dict[str, Dict[str, str]]) -> Dict[str, Any]:
    """反证守卫真的会响：被剪模块必须报错；可选依赖必须仍可按 ImportError 降级。"""
    python = app_dir / "runtime" / "python.exe"
    result: Dict[str, Any] = {"block": None, "observe": None}

    def _run(module: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(python), "-c", f"import {module}"],
            cwd=str(app_dir), capture_output=True, text=True, timeout=60,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )

    blocked = next((name for name, info in modules.items() if info.get("mode") == "block"), None)
    if blocked:
        proc = _run(blocked)
        result["block"] = {
            "module": blocked,
            "returncode": proc.returncode,
            "raised": "PrunedPayloadImportError" in (proc.stderr or ""),
            "output": (proc.stderr or "").strip().splitlines()[-1:] or [""],
        }
        if proc.returncode == 0 or not result["block"]["raised"]:
            raise SystemExit(
                f"剪枝守卫失效：import {blocked} 没有报错（returncode={proc.returncode}）。"
                "拒绝产出安装包。"
            )
    observed = next((name for name, info in modules.items() if info.get("mode") == "observe"), None)
    if observed:
        proc = _run(observed)
        result["observe"] = {
            "module": observed,
            "returncode": proc.returncode,
            "degradable": "PrunedOptionalImportError" in (proc.stderr or ""),
            "output": (proc.stderr or "").strip().splitlines()[-1:] or [""],
        }
        if proc.returncode == 0 or not result["observe"]["degradable"]:
            raise SystemExit(
                f"剪枝守卫的可选依赖分支异常：import {observed} 既没降级也没记录。"
                "拒绝产出安装包。"
            )
    return result


def _prune_payload(app_dir: Path, *, enabled: bool, version: str) -> Dict[str, Any]:
    """把 A1–A13 应用到刚复制好的 payload 树，并落清单/守卫。"""
    report: Dict[str, Any] = {
        "enabled": bool(enabled),
        "version": version,
        "rules": [rule.id for rule in PRUNE_RULES],
        "paths": [],
        "modules": {},
        "files": 0,
        "freed_bytes": 0,
        "by_rule": {},
        "empty_dirs_removed": 0,
        "guard_check": None,
    }
    if enabled:
        for path in sorted(app_dir.rglob("*")):
            if not path.is_file() or path.name == PRUNE_MANIFEST_NAME:
                continue
            rel = path.relative_to(app_dir).as_posix()
            hit = _match_rule(rel)
            if hit is None:
                continue
            rule, _why = hit
            try:
                size = path.stat().st_size
                path.unlink()
            except OSError as exc:
                print(f"[prune] 跳过无法删除的 {rel}: {exc}")
                continue
            report["paths"].append(rel)
            report["files"] += 1
            report["freed_bytes"] += size
            bucket = report["by_rule"].setdefault(rule.id, {"files": 0, "bytes": 0})
            bucket["files"] += 1
            bucket["bytes"] += size
        report["empty_dirs_removed"] = _remove_empty_dirs(app_dir, report["paths"])
    report["modules"] = _prune_modules(report["paths"])
    _write_prune_manifest(app_dir, report)
    if enabled:
        _write_prune_guard(app_dir)
        report["guard_check"] = _guard_self_check(app_dir, report["modules"])
    freed_mb = report["freed_bytes"] / (1024 * 1024)
    if enabled:
        detail = ", ".join(
            f"{rid} {info['files']} 个/{info['bytes'] / (1024 * 1024):.2f} MiB"
            for rid, info in report["by_rule"].items()
        )
        print(
            f"[prune] A1–A13 命中 {report['files']} 个文件 / {freed_mb:.2f} MiB，"
            f"清空目录 {report['empty_dirs_removed']} 个；{detail}"
        )
        guard = report["guard_check"]
        rolled_back = [rule.id for rule in PRUNE_RULES if not rule.enabled]
        if rolled_back:
            print(f"[prune] 已回滚规则 {', '.join(rolled_back)}（被真实 eager import 证明不可剪）")
        print(
            "[prune] 守卫生效：import "
            f"{guard['block']['module']} → 拒绝（{guard['block']['output'][0][:90]}）；"
            f"import {guard['observe']['module']} → 可选降级（{guard['observe']['output'][0][:90]}）"
        )
    else:
        print("[prune] 已按 --no-prune 关闭：候选文件全部保留（回滚模式）")
    return report


_MEASURE_CODE = r"""
import importlib.metadata as md
import json
import sys

sys.path.insert(0, ".")
import src.gateway.main  # noqa: F401
# 本机恢复证明通道只在 lifespan 里延迟导入；冒烟会真实启动它，
# 因此这里也要把模块与 pywin32 依赖拉进离线运行时。
import src.gateway.local_proof  # noqa: F401
import src.common.local_proof_ipc  # noqa: F401
import src.common.windows_acl  # noqa: F401
import ntsecuritycon  # noqa: F401
import win32api  # noqa: F401
import win32con  # noqa: F401
import win32file  # noqa: F401
import win32pipe  # noqa: F401
import win32security  # noqa: F401
import src.ai.providers  # noqa: F401
# The provider HTTP stack is imported lazily inside ``provider.chat()``; a
# startup-only import graph therefore misses ``httpx2`` and its transitive
# ``httpcore2`` / ``truststore``. Import them here so the offline runtime
# contains what a real chat call needs (openai==3.13 uses httpx2).
import openai  # noqa: F401
import httpx2  # noqa: F401
import httpcore2  # noqa: F401
import truststore  # noqa: F401
# ``src/ai/providers.py`` 另建一个 httpx.Client（代理/超时）后再交给 openai；
# 该导入发生在 ``provider.chat()`` 内部，启动图与 openai 的 httpx2 都覆盖不到，
# 漏掉时线上表现是「No module named 'httpx'」、对话与目标拆解全部失败。
import httpx  # noqa: F401
import httpcore  # noqa: F401
# 同类问题：以下第三方包只在业务函数体内延迟导入，启动图看不到，
# 但都属于 requirements.txt 里的运行期硬依赖，缺了就会在真实使用时抛
# ModuleNotFoundError（不是优雅降级）。放在这里让实测 import 图带上它们
# 及各自的传递依赖（docx->lxml、sqlalchemy->greenlet 等）。
import anthropic  # noqa: F401  # src/ai/providers.py 的 Anthropic provider
# google.genai 的 __init__ 里会 ``import google.auth``；只把 google-genai 记进
# EXPLICIT_DISTS 而不真实导入，google-auth / pyasn1-modules 等传递依赖不会被
# 测量到，离线运行时自检会以 ModuleNotFoundError: google.auth 失败。
import google.genai  # noqa: F401  # Gemini provider（方法体内延迟导入）
import docx  # noqa: F401       # python-docx：.docx 知识导入
import pypdf  # noqa: F401      # .pdf 知识导入
import sqlalchemy  # noqa: F401  # 记忆/知识库 ORM
import taskiq  # noqa: F401     # src/tasks/broker.py 任务代理
import uvicorn  # noqa: F401
import uvicorn.main  # noqa: F401
import uvicorn.protocols.http.auto  # noqa: F401
import dotenv  # noqa: F401
import aiosqlite  # noqa: F401
# 下面五个同样只在业务路径/子进程里出现，启动图带不上：
#   requests         —— src/ai/model_discovery.py 模块顶层导入，但该模块不在启动链上；
#                       漏掉时 offline 运行时首调模型发现即 ModuleNotFoundError，
#                       其传递依赖 urllib3 / certifi / idna / charset_normalizer 也一并缺失。
#   fastmcp / mcp    —— src/adapters/mcp/mcp_adapter.py（模块体导入，适配器按需加载）。
#   prometheus_client—— src/observability/metrics.py（指标注册）。
#   psutil           —— src/selfmod/sandbox.py（沙箱子进程守卫）。
import requests  # noqa: F401
import fastmcp  # noqa: F401
import mcp  # noqa: F401
import prometheus_client  # noqa: F401
import psutil  # noqa: F401
# 桌面壳（pywebview 6.2.1）：窗口后端在 src/desktop/native_window.py:243 的函数体
# 里才 ``import webview``，启动图覆盖不到；而 pywebview 的 WinForms 后端又在
# 真正开窗时才经 pythonnet/clr_loader 调 .NET、经 bottle/proxy_tools 起本地
# HTTP 与 JS 桥。少任何一个，安装版双击后不是 ImportError 就是窗口起不来。
# 这里显式导入整条栈，让实测 import 图把 5 个发行包及其传递依赖带进 payload。
import webview  # noqa: F401
import bottle  # noqa: F401
import proxy_tools  # noqa: F401
import clr_loader  # noqa: F401
import clr  # noqa: F401

stdlib = set(sys.stdlib_module_names)
pseudo = set(%(pseudo)r)
modules = {
    name.split(".", 1)[0]
    for name in sys.modules
    if not name.startswith(("src", "_"))
    and name.split(".", 1)[0] not in stdlib
    and name.split(".", 1)[0] not in pseudo
}
mapping = md.packages_distributions()
resolved = {}
unmapped = []
for module in sorted(modules):
    candidates = list(mapping.get(module) or [])
    if not candidates:
        try:
            candidates = [md.distribution(module).metadata["Name"]]
        except Exception:
            unmapped.append(module)
            continue
    for candidate in candidates:
        try:
            distribution = md.distribution(candidate)
        except Exception:
            unmapped.append(f"{module}->{candidate}")
            continue
        resolved[distribution.metadata["Name"] or candidate] = distribution.version
print("RESULT:" + json.dumps({"dists": resolved, "unmapped": unmapped}))
"""


def _measure_distributions() -> Tuple[Dict[str, str], List[str]]:
    """在子进程里按真实 import 图测量依赖，避免污染本进程。"""
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("PYTHONPATH", None)
    code = _MEASURE_CODE % {"pseudo": sorted(PSEUDO_MODULES)}
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )
    if proc.returncode != 0:
        raise SystemExit(f"依赖测量失败：\n{proc.stdout}\n{proc.stderr}")
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT:"):
            payload = json.loads(line[len("RESULT:"):])
            return payload["dists"], payload["unmapped"]
    raise SystemExit(f"依赖测量没有输出结果：\n{proc.stdout}\n{proc.stderr}")


def _copy_tree(source: Path, destination: Path, extra_ignore: Iterable[str] = ()) -> int:
    if destination.exists():
        shutil.rmtree(destination)
    ignore = _IGNORE
    if tuple(extra_ignore):
        ignore = shutil.ignore_patterns(*_IGNORE_SOURCES, *extra_ignore)
    shutil.copytree(source, destination, ignore=ignore)
    return sum(1 for entry in destination.rglob("*") if entry.is_file())


_IGNORE_SOURCES = (
    "__pycache__", "*.pyc", "*.pyo",
    "*.db", "*.db-wal", "*.db-shm", "*.sqlite3", "*.sqlite3-wal", "*.sqlite3-shm",
)


def _reset_out(out_dir: Path) -> None:
    resolved = out_dir.resolve()
    if resolved == REPO_ROOT or resolved in REPO_ROOT.parents:
        raise SystemExit(f"拒绝清空 {resolved}：它与仓库根重叠")
    out_dir.mkdir(parents=True, exist_ok=True)
    for entry in out_dir.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def _compliance_script(name: str) -> Path:
    """定位合规脚本：优先 ``packaging/``，其次发布目录 ``dist/release-gate/``。

    两者都不在时直接失败——发布构建不允许在缺少门禁的情况下继续。
    """
    candidates = (REPO_ROOT / "packaging" / name, REPO_ROOT / "dist" / "release-gate" / name)
    script = next((path for path in candidates if path.is_file()), None)
    if script is None:
        raise SystemExit(
            f"找不到合规脚本 {name}（packaging/ 或 dist/release-gate/）；payload 组装中止。"
        )
    return script


def _run_compliance_gate(app_dir: Path) -> None:
    """组装完 payload 后核对“清单 ↔ 实装 ↔ 许可证文本”，不一致即中止。"""
    script = _compliance_script("sbom_audit.py")
    print(f"[gate] 合规核对 -> {script.name} --check")
    proc = subprocess.run(
        [sys.executable, str(script), "--check", "--app", str(app_dir)],
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise SystemExit("合规门禁未通过：清单/实装/许可证文本不一致，payload 组装中止。")


def _assert_brand_guard(app_dir: Path) -> None:
    """品牌字面硬门禁：旧主名/错字/死域名/旧版本号出现即中止打包。"""
    if str(Path(__file__).resolve().parent) not in sys.path:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
    import brand_guard  # noqa: PLC0415  同目录脚本模块

    violations = brand_guard.scan(payload=app_dir, repo_root=REPO_ROOT)
    if violations:
        raise SystemExit(
            "品牌字面门禁未通过（payload 组装中止）：\n  " + "\n  ".join(violations)
        )
    print("      品牌门禁 -> 主名 / 版本 / 旧名 / 错字 / 死域名 全部通过")


def _seed_missing_dist_licenses(app_dir: Path) -> int:
    """把上游 LICENSE 原文补进缺文本的 ``*.dist-info/licenses/``。

    fastmcp-slim 与 opentelemetry-util-http 的 wheel 不带许可证文件，而
    ``third_party_notices`` / ``sbom_audit --check`` 都只认 dist-info 内的真实
    文本。这里按 ``DIST_INFO_LICENSE_SOURCES`` 显式补入（文本取自
    ``packaging/licenses/``，与随包 ``licenses/`` 目录同一份），不放宽任何校验。
    """
    site_packages = app_dir / "runtime" / "Lib" / "site-packages"
    seeded = 0
    for dist_info in sorted(site_packages.glob("*.dist-info")):
        metadata = dist_info / "METADATA"
        if not metadata.is_file():
            continue
        name = ""
        for line in metadata.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("Name: "):
                name = line[len("Name: "):].strip()
                break
        source = DIST_INFO_LICENSE_SOURCES.get(name.lower())
        if not source:
            continue
        file_name, _url = source
        target = dist_info / "licenses" / file_name
        if target.is_file():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(LICENSES_DIR / file_name, target)
        seeded += 1
    return seeded


def _copy_notices(app_dir: Path) -> int:
    """随包分发第三方许可证声明与运行时/前端许可证文本。

    声明文件由 :mod:`third_party_notices` 按 payload 里实际安装的
    ``*.dist-info`` 现场生成（不再手工维护版本表，避免与实装漂移）；
    OpenSSL / libffi / SQLite / Tcl-Tk / MSVC 与 React 的许可证全文放在
    ``licenses/``。Python 包自带的 ``*.dist-info/licenses/`` 已随运行时复制。
    """
    if not LICENSES_DIR.is_dir():
        raise SystemExit(f"缺少许可证文本目录：{LICENSES_DIR}")
    if str(Path(__file__).resolve().parent) not in sys.path:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
    import third_party_notices  # noqa: PLC0415  同目录脚本模块

    # 先落许可证文本：生成器会校验 licenses/ 下的必需全文，顺序反过来会误报缺失。
    copied = _copy_tree(LICENSES_DIR, app_dir / "licenses")
    third_party_notices.write(app_dir)
    print(f"      THIRD-PARTY-NOTICES.txt 已按 {app_dir.name}/runtime 实装生成")
    return 1 + copied


def _iter_properties(node: Any) -> Iterable[Dict[str, Any]]:
    """递归遍历 CycloneDX 的属性节点（``{"name": ..., "value": ...}``）。"""
    if isinstance(node, dict):
        if isinstance(node.get("name"), str) and "value" in node:
            yield node
        for value in node.values():
            yield from _iter_properties(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_properties(item)


def _write_installed_sbom(app_dir: Path) -> int:
    """把本次构建的 CycloneDX SBOM 落到程序根（``{app}\\sbom-cyclonedx.json``）。

    装到本机后用户不联网也能核对“装了什么、各是什么许可证”。SBOM 由
    ``sbom_audit.py`` 针对**本次 payload**现算（不复制 ``dist/release-gate`` 的
    历史基线），写到临时目录后只搬运 ``sbom-cyclonedx.json`` 一个文件。

    构建机绝对路径不能进安装目录：``liuhao:audit-source-tree`` 改写为 ``.``，
    并在落盘前按 ``DEV_PATH_MARKERS`` 复核一次（命中即中止）。
    """
    script = _compliance_script("sbom_audit.py")
    with tempfile.TemporaryDirectory(prefix="liuhao-sbom-") as tmp:
        out_dir = Path(tmp) / "sbom"
        report = Path(tmp) / "report.md"
        proc = subprocess.run(
            [
                sys.executable, str(script),
                "--app", str(app_dir),
                "--out", str(out_dir),
                "--report", str(report),
            ],
            text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0:
            raise SystemExit("SBOM 生成失败，payload 组装中止（详见上方输出）。")
        source = out_dir / "sbom-cyclonedx.json"
        if not source.is_file():
            raise SystemExit(f"SBOM 生成未产出 sbom-cyclonedx.json：{out_dir}")
        payload = json.loads(source.read_text(encoding="utf-8"))
        for prop in _iter_properties(payload):
            if prop.get("name") == "liuhao:audit-source-tree":
                prop["value"] = "."
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        markers = [marker for marker in DEV_PATH_MARKERS if marker in text]
        drive_paths = sorted(set(re.findall(r'[A-Za-z]:\\[^"]*', text)))
        if markers or drive_paths:
            leaked = "、".join(markers + drive_paths[:3])
            raise SystemExit(f"SBOM 里出现构建机路径，拒绝随安装包分发：{leaked}")
        target = app_dir / "sbom-cyclonedx.json"
        target.write_text(text, encoding="utf-8")
    size = target.stat().st_size
    print(f"      sbom-cyclonedx.json -> {size} 字节（{len(payload.get('components') or [])} 个组件）")
    return size


def _copy_product_license(app_dir: Path) -> int:
    """把产品自身的许可证统一落成 ``{app}\\LICENSE``（「关于与隐私」页按此文件名读取）。

    仓库根没有许可证文件时**不伪造**：打印缺项，由安装后自检把关。
    """
    for name in PRODUCT_LICENSE_NAMES:
        source = REPO_ROOT / name
        if source.is_file() and source.stat().st_size > 0:
            shutil.copy2(source, app_dir / "LICENSE")
            print(f"      产品许可证 -> LICENSE（源 {name}，{source.stat().st_size} 字节）")
            return 1
    print(
        "      !! 产品许可证缺失：仓库根没有 LICENSE/LICENSE.txt/COPYING —— 安装目录将缺 "
        "LICENSE；补齐后自动随包（本步骤不伪造许可条款）",
        file=sys.stderr,
    )
    return 0


#: 任何情况下都不允许进包的凭据/身份文件（相对程序根目录）。
FORBIDDEN_PAYLOAD_FILES: Tuple[str, ...] = (
    "config/auth_secrets.json",
    "config/human_identities.json",
    "config/app.env",
    "configs/mcp/servers.json",
    ".env",
)


def _assert_payload_hygiene(app_dir: Path) -> None:
    """出包前硬拦截：凭据、身份登记表、本地 MCP 配置不得随包。"""
    offenders = [rel for rel in FORBIDDEN_PAYLOAD_FILES if (app_dir / rel).exists()]
    for pattern in ("config/*.pem", "config/*.key"):
        offenders.extend(
            path.relative_to(app_dir).as_posix() for path in app_dir.glob(pattern)
        )
    if offenders:
        raise SystemExit("payload 内含禁止分发的凭据/身份文件：" + "、".join(offenders))


def _write_start_bat(app_dir: Path) -> None:
    bat = """@echo off
rem LiuHao AI OS - command-line troubleshooting entry (--console).
rem Normal users start from the desktop / Start-menu shortcut, which runs
rem runtime\\pythonw.exe directly and shows NO console window. This .bat is
rem only for --console troubleshooting on machines without a desktop session.
rem The console code page is switched to UTF-8 so the Chinese messages below
rem are readable on any system locale. This file never parses data-dir.txt
rem (UTF-8 with BOM); only the launcher reads it. Keep the first lines ASCII.
setlocal DisableDelayedExpansion
chcp 65001 >nul
rem Keep the install tree read-only: never write __pycache__ next to the app.
set PYTHONDONTWRITEBYTECODE=1
cd /d "%~dp0"
rem Capture %~dp0 ONCE while delayed expansion is off: the value is stored
rem verbatim even if it contains a literal exclamation mark.
set "LH_DIR=%~dp0"
setlocal EnableDelayedExpansion
rem Every later echo of the install path must use !LH_DIR! (delayed expansion).
rem Percent-expanding an unquoted %~dp0 that contains an ampersand or a caret
rem makes cmd treat the rest of the line as a new command; because a whole IF
rem block is parsed before it runs, even the "runtime missing" branch would
rem break the launcher on a path holding those characters (for example a
rem temporary folder named a+b&c(1)#2 plus a caret).
rem Keep the failure log bounded: retain only the previous failure as .1.
if exist "!LH_DIR!bootstrap.log" move /y "!LH_DIR!bootstrap.log" "!LH_DIR!bootstrap.log.1" >nul 2>nul
rem An explicit LIUHAO_DATA_DIR still wins.
if not exist "runtime\\python.exe" (
    echo [FAIL] 安装文件不完整：缺少 runtime\\python.exe，程序无法启动。
    echo        修复：重新运行安装包，选择「修复安装」；或重新安装到同一位置（账号与数据会保留）。
    echo        安装位置：!LH_DIR!
    echo        日志：!LH_DIR!bootstrap.log
    echo.
    echo [FAIL] 安装文件不完整：缺少 runtime\\python.exe，请重新安装或修复安装。>> "!LH_DIR!bootstrap.log"
    echo 安装位置：!LH_DIR!>> "!LH_DIR!bootstrap.log"
    echo.
    echo 按任意键关闭此窗口。
    pause >nul
    exit /b 1
)
echo 这是命令行排障入口；正常使用请双击桌面快捷方式（不会出现本窗口）。
"runtime\\python.exe" "scripts\\launch_installed.py" %*
set "LH_CODE=%ERRORLEVEL%"
echo.
if not "%LH_CODE%"=="0" goto :lh_failed
rem 退出码 0 有两种：用户自己停掉服务，或双击时已经有实例在跑（重复启动）。
rem 这两种情况都不该再 pause —— 否则第二个窗口会一直挂着等按键。
exit /b 0
:lh_failed
echo [FAIL] 鎏灏未能正常启动（退出码 %LH_CODE%）。
echo        原因与堆栈见日志：!LH_DIR!bootstrap.log
echo.
echo 按任意键关闭此窗口。
pause >nul
exit /b %LH_CODE%
endlocal
"""
    (app_dir / "LiuHao.bat").write_text(bat, encoding="utf-8")


def _write_start_here(app_dir: Path, version: str) -> None:
    text = f"""LiuHao AI OS · 鎏灏智能中枢 {version} —— 先看这里

1. 双击桌面 / 开始菜单里的快捷方式（托盘壳）。
   关闭窗口只会隐藏到托盘，服务继续运行；托盘菜单里可打开控制台 / 停止服务 / 退出。
   只有排障时才用本目录下的 LiuHao.bat（会打开命令行并带上 --console）。

2. 首次启动会弹出一个图形化设置窗口，按五页完成即可（不需要在命令行输入）：
   ① 第 1 步 · 数据目录：默认 D:\\LiuHaoData（没有 D 盘时预填 %LOCALAPPDATA%\\LiuHao\\data）；
      重装或升级不会动它，换电脑时把整个目录复制过去；确认路径可写后点「下一步」。
   ② 第 2 步 · 管理员账号：填账号（英文）、显示名（可中文）、登录密码、确认密码；
      密码至少 8 位且同时包含字母和数字。请记下来——之后登录就是用它。点「下一步」。
   ③ 第 3 步 · 选择模型（可跳过）：
      a) 「跳过，稍后在『系统设置 → 模型管理』里添加」；
      b) 本机 Ollama：先到 https://ollama.com 下载安装，再执行
         ollama pull qwen2.5:7b（约 2GB），并保持 Ollama 在运行（地址默认 http://localhost:11434）；
      c) 云端 API Key（OpenAI 兼容，如 DeepSeek）：填 base_url、模型名和 API Key。
         没有 Key 也可以先装：DeepSeek 开放平台 https://platform.deepseek.com
         注册后到「API Keys」页创建（形如 sk-...，按量计费）。
         DeepSeek 填 base_url=https://api.deepseek.com/v1、模型名=deepseek-chat。
      同页下方「服务端口」默认 8080；被占用时在这里换一个空闲端口（不会自动换）。点「下一步」。
   ④ 第 4 步 · 完成前请确认：核对数据目录、加密边界、备份、卸载与联网范围；
      本页的下一步按钮显示为「完成并自检」，点它写入设置并开始自检。
   ⑤ 第 5 步 · 自检结果：四项都是刚才在这台电脑上实测的（网关端口、数据目录、
      托盘驻留、默认模型）；失败项右侧有可点的「下一步」，按提示处理后点「重试」；
      全部通过后点「进入控制台」（本页的下一步按钮就是这个字）。
   任意一页点「取消」或直接关窗 = 放弃本次设置：不创建账号、不启动控制台；
   下次双击桌面快捷方式会重新从第 1 页开始。
   没有桌面会话的环境（计划任务 / 远程 shell）可在命令行改用：
     LiuHao.bat --console

3. 向导结束后服务会继续启动，不需要关窗重开。默认会打开原生控制台窗口
   （网页模式 --web 才用浏览器）；
   若端口被占用，窗口会说明是哪个端口、被谁占用：先关闭占用程序再重试，
   或直接在界面里选择「改用其他端口」（不会自动换端口，以免地址对不上）。
   地址同时显示在状态窗口底部的「控制台：」一行。

4. 在登录页用第 2 步刚建的账号和密码登录。登录后落在「总览」，
   聊天入口在左侧「自由对话」→「直接对话」，在输入框里说第一句话即可。

程序目录：默认 D:\\LiuHao。D 盘不可用时会回退：普通双击安装退到
          %LOCALAPPDATA%\\Programs\\LiuHao（当前用户目录，不需要管理员权限）；
          只有你主动「以管理员身份运行」安装包时，才退到 C:\\Program Files\\LiuHao。
          实际位置见安装向导「准备安装」页；回退安装还会在程序目录生成
          「安装位置说明.txt」。
数据目录：默认 D:\\LiuHaoData。安装向导会单独展示并让你确认/改选这个位置；
          D 盘不可用、或升级时读不到/写不进原来的数据目录时，安装器**不会**
          悄悄沿用原目录，而是说明原因并让你确认/改选。
          没有检测到可用非系统盘时，数据目录和备份目录都自动落到
          %LOCALAPPDATA%\\LiuHao，安装继续、不会中断，向导会提示
          「没有检测到非系统盘，数据与备份都在 C 盘，建议预留空间」。
          实际位置见程序目录下的 data-dir.txt；卸载默认保留，连续两次确认才会删除。
          升级备份目录：默认 <数据目录所在盘>:\\LiuHaoBackups（数据在 D 盘时
          即 D:\\LiuHaoBackups；安装器显式下发，不落 C 盘、也不会退回数据目录）。
          它只放升级前的自动备份，不是你的数据备份。没有可用非系统盘时随数据目录
          一起落到 %LOCALAPPDATA%\\LiuHao；/BACKUPS= 显式指定优先于默认推导。
          注意：备份与数据在同一块盘。磁盘损坏、分区丢失或勒索加密时备份会一起丢，
          无法用来恢复——这不是异地备份；要防灾请自己再复制到移动硬盘或另一台电脑。
          手动备份：退出后复制整个数据目录，必须同时带上 data\\model_config.key 与
          data\\model_registry.sqlite3，缺一个已存 Key 就解不开。
          已装在 C 盘的老用户覆盖安装时默认沿用原数据目录（保留数据），
          但可以在向导的「数据目录」页显式改到 D:\\LiuHaoData（旧数据不会自动搬过去）。
本安装包不含任何模型权重，也不含任何密钥。
加密边界：只有 API Key 用 AES-256-GCM 加密后保存在数据目录；
          对话、知识库、审计是本地明文 SQLite，依赖 Windows 账号权限保护。
联网说明：默认不主动联网。只有你自己配置云端模型后，对话内容与检索到的知识片段
          才会发到你填的 base_url；本机 Ollama 只连 127.0.0.1，完全不出网；
          不收集使用统计、不做遥测上报。
程序使用项目自签名证书（签名者 CN=鎏灏智能中枢）：导入安装器附带的信任脚本后
属性里签名状态为「有效」并能看到签名者，但按微软口径自签名在 SmartScreen 上
等同未签名，仍可能提示“未知发布者”，点「更多信息 → 仍要运行」即可继续。
数据与隐私：登录后点左侧导航底部的「关于与隐私」，可看到版本、数据目录、许可证、
          第三方组件声明与联网说明。
第三方软件许可证：优先在控制台「关于与隐私」查看（0.1.1 起）；
程序目录里另有三份离线材料，断网也能打开：
  licenses\\                 第三方许可证全文（18 项）
  THIRD-PARTY-NOTICES.txt    逐项索引（含 LGPL 组件与源码要约位置）
  sbom-cyclonedx.json        软件物料清单（本安装包实际随包组件）
LICENSE 是本产品自身的许可证（若本版本未附带，说明产品许可条款尚未定稿，可向发布方索取）。
卸载默认保留以上许可证文件：它们是你的合规记录，不会随卸载删除。
若程序目录既没有 THIRD-PARTY-NOTICES.txt 也没有 licenses，说明是旧版本（0.1.0），
不影响使用，许可证清单可在发布页索取。
"""
    (app_dir / "START-HERE.txt").write_text(text, encoding="utf-8")


def _copy_config(config_src: Path, config_dst: Path) -> int:
    """复制配置模板，并剔除一切凭据/私钥与开发机路径。"""
    if not config_src.is_dir():
        raise SystemExit(f"缺少配置目录：{config_src}")
    config_dst.mkdir(parents=True, exist_ok=True)
    count = 0
    for entry in config_src.iterdir():
        name = entry.name
        if any(
            name == p
            or (p.startswith("*.") and name.endswith(p[1:]))
            for p in CONFIG_EXCLUDES
        ):
            continue
        if entry.is_dir():
            count += _copy_tree(entry, config_dst / name)
        else:
            destination = config_dst / name
            shutil.copy2(entry, destination)
            count += 1
            if destination.suffix.lower() == ".json":
                removed = _scrub_json_dev_metadata(destination)
                if removed:
                    print(f"      {name}: 移除 {removed} 个含开发机路径的内部字段")
    return count


def _scrub_json_dev_metadata(path: Path) -> int:
    """删除随包 JSON 里含开发机路径的 ``_`` 内部字段。

    ``config/model_pricing.json`` 里有 ``_how_to_verify`` 之类的开发说明，
    带的是构建机的仓库绝对路径。它不参与运行，但会跟着安装包分发出去，
    属于"夹带开发目录"缺陷，打包时必须清掉。
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    removed = 0

    def walk(node: Any) -> None:
        nonlocal removed
        if isinstance(node, dict):
            for key in list(node):
                value = node[key]
                if (
                    isinstance(key, str)
                    and key.startswith("_")
                    and isinstance(value, str)
                    and any(marker in value for marker in DEV_PATH_MARKERS)
                ):
                    del node[key]
                    removed += 1
                    continue
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    if removed:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return removed


def _audit_dev_paths(app_dir: Path) -> List[str]:
    """扫描随包文本文件里的开发机路径，返回 ``相对路径:行号:标记``。

    硬拦截只针对**仓库绝对路径**（``D:\\LiuHao-AI-OS``）出现在自有文件里；
    ``src/**`` 归业务侧所有，只记录不拦截。``.venv\\Scripts`` 是安装版
    布局探测/用法示例（安装目录会先命中 ``runtime\\pythonw.exe``），
    同样记录到清单里供人工核对。
    """
    suffixes = (".py", ".yaml", ".yml", ".json", ".txt", ".bat", ".md", ".cfg", ".ini", ".toml")
    hits: List[str] = []
    for path in app_dir.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        try:
            if path.stat().st_size > 2 * 1024 * 1024:
                continue
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, 1):
            markers = [marker for marker in DEV_PATH_MARKERS if marker in line]
            if markers:
                hits.append(f"{path.relative_to(app_dir).as_posix()}:{number}:{markers[0]}")
                break
    return hits


def _copy_scripts(app_dir: Path) -> int:
    count = 0
    for rel in PAYLOAD_SCRIPTS:
        source = REPO_ROOT / rel
        if not source.is_file():
            raise SystemExit(f"缺少脚本：{source}")
        destination = app_dir / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        count += 1
    return count


def _copy_runtime(app_dir: Path) -> int:
    """复制构建机的 CPython 安装作为离线运行时（不含第三方包）。"""
    base = Path(sys.base_prefix)
    python_exe = base / "python.exe"
    if not python_exe.is_file():
        raise SystemExit(f"构建机 CPython 不完整，找不到 {python_exe}")

    runtime = app_dir / "runtime"
    if runtime.exists():
        shutil.rmtree(runtime)
    runtime.mkdir(parents=True)

    for name in ("python.exe", "pythonw.exe", "python3.dll",
                 f"python{sys.version_info.major}{sys.version_info.minor}.dll",
                 "vcruntime140.dll", "vcruntime140_1.dll", "LICENSE.txt"):
        source = base / name
        if source.is_file():
            shutil.copy2(source, runtime / name)

    shutil.copytree(
        base / "DLLs", runtime / "DLLs",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    shutil.copytree(
        base / "Lib", runtime / "Lib",
        ignore=shutil.ignore_patterns(*STDLIB_SKIP_DIRS, "__pycache__"),
    )

    # 桌面状态窗口（tkinter）需要 Tcl/Tk 的数据目录；CPython 安装把它放在
    # 前缀下的 ``tcl/``，运行时前缀就是 ``runtime/``，所以原样搬过去。
    tcl_source = base / "tcl"
    if not tcl_source.is_dir():
        raise SystemExit(
            f"构建机 CPython 缺少 tcl 数据目录：{tcl_source}\n"
            "桌面壳依赖 tkinter，安装版无法在没有该目录时显示状态窗口。"
        )
    shutil.copytree(
        tcl_source, runtime / "tcl",
        ignore=shutil.ignore_patterns("__pycache__"),
    )

    (runtime / "README.txt").write_text(
        f"""离线 Python 运行时
===================
Python 版本：{sys.version.split()[0]}（Windows x64，随安装包分发）
第三方依赖：按网关真实 import 图从构建虚拟环境复制，完整清单与版本见
            安装目录上一级（程序根目录）的 THIRD-PARTY-NOTICES.txt。
还包含：桌面壳所需的 tkinter / Tcl-Tk 数据、pywin32（系统托盘）。
许可证：CPython 为 PSF License（见本目录 LICENSE.txt）；
        OpenSSL / libffi / SQLite / Tcl-Tk / MSVC 等运行时组件的许可证文本
        见程序根目录 licenses\\ 目录（THIRD-PARTY-NOTICES.txt 有逐项索引）。
        LICENSE.txt 中提到的 docs/license.tcltk 在本安装包中对应
        licenses\\Tcl-Tk-license.terms 与 tcl\\tk8.6\\license.terms。
本目录随安装包分发，收件人无需另行安装 Python。
""",
        encoding="utf-8",
    )
    return sum(1 for entry in runtime.rglob("*") if entry.is_file())


def _copy_distributions(app_dir: Path, dists: Dict[str, str]) -> Tuple[int, List[str], int]:
    """把命中的发行包文件复制进 runtime/Lib/site-packages（剔除上游测试文件）。"""
    import importlib.metadata as md

    target = app_dir / "runtime" / "Lib" / "site-packages"
    target.mkdir(parents=True, exist_ok=True)
    copied = 0
    skipped = 0
    missing: List[str] = []
    for name in sorted(dists):
        try:
            distribution = md.distribution(name)
        except Exception:
            missing.append(name)
            continue
        files = distribution.files
        if files is None:
            # 极少数包没有 RECORD：退回 top_level.txt 指向的顶层包 + dist-info 目录。
            base = Path(distribution.locate_file(""))
            candidates = [base / item for item in
                          (distribution.read_text("top_level.txt") or "").split()]
            dist_info = Path(getattr(distribution, "_path", ""))
            if dist_info.is_dir():
                candidates.append(dist_info)
            for candidate in candidates:
                if candidate.is_dir():
                    shutil.copytree(candidate, target / candidate.name,
                                    dirs_exist_ok=True, ignore=_DIST_IGNORE)
                elif candidate.is_file():
                    destination = target / candidate.name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(candidate, destination)
                    copied += 1
            continue
        for item in files:
            parts = Path(str(item)).parts
            if ".." in parts:
                continue
            source = Path(distribution.locate_file(item))
            if not source.is_file():
                continue
            try:
                relative = source.resolve().relative_to(
                    Path(distribution.locate_file("")).resolve()
                )
            except ValueError:
                continue
            if _is_test_artifact(relative):
                skipped += 1
                continue
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copied += 1
    return copied, missing, skipped


def _validate_runtime(app_dir: Path) -> None:
    python = app_dir / "runtime" / "python.exe"
    pythonw = app_dir / "runtime" / "pythonw.exe"
    if not pythonw.is_file():
        raise SystemExit(
            f"离线运行时缺少 pythonw.exe：{pythonw}\n"
            "开机自启（HKCU Run）必须用无控制台的 pythonw。"
        )
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # 自检覆盖三类真实入口：网关、桌面壳（tkinter）、托盘（pywin32）；
    # provider SDK 也逐个导入，避免"清单登记了、payload 里却没有"。
    # `tkinter.Tcl()` 会真正加载 Tcl 的 init 脚本，能验证 tcl 数据目录是否可用。
    code = (
        "import sys, tkinter, win32api, win32gui, win32con, pywintypes, "
        "uvicorn, uvicorn.main, uvicorn.protocols.http.auto, "
        "dotenv, aiosqlite, src.gateway.main, src.desktop.app, "
        "openai, httpx2, httpcore2, truststore, jiter, httpx, httpcore, "
        "anthropic, google.genai, docx, pypdf, sqlalchemy, taskiq; "
        "tkinter.Tcl().eval('info patchlevel'); "
        "print(sys.version.split()[0])"
    )
    proc = subprocess.run(
        [str(python), "-c", code],
        cwd=str(app_dir), capture_output=True, text=True, env=env,
    )
    if proc.returncode != 0:
        raise SystemExit(
            "离线运行时自检失败 —— 网关 / 桌面壳 / 托盘无法在该运行时中导入：\n"
            f"{proc.stdout}\n{proc.stderr}"
        )
    print(f"      运行时自检通过（Python {proc.stdout.strip()}，含 tkinter 与 pywin32）")


def _write_ico(png: Path, out: Path) -> bool:
    """把 PNG 直接封装成单图 ICO（Windows Vista+ 支持 PNG 压缩图标）。"""
    if not png.is_file():
        return False
    size = 192
    data = png.read_bytes()
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack(
        "<BBBBHHII",
        size % 256, size % 256, 0, 0, 1, 32, len(data), 6 + 16,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(header + entry + data)
    return True


def build(out_dir: Path, version: str, prune: bool = True) -> int:
    app_dir = out_dir / "app"
    _reset_out(out_dir)

    print("=" * 66)
    print("组装安装包 payload")
    print("=" * 66)

    dists, unmapped = _measure_distributions()
    for name in EXPLICIT_DISTS:
        import importlib.metadata as md

        try:
            distribution = md.distribution(name)
        except Exception:
            print(f"      !! 显式依赖未安装：{name}", file=sys.stderr)
            continue
        dists[distribution.metadata["Name"] or name] = distribution.version
    print(f"[1/5] 依赖测量：{len(dists)} 个发行包")
    if unmapped:
        print(f"      跳过非发行包模块：{unmapped}")

    print("[2/5] 复制源码与配置")
    for name in SOURCE_DIRS:
        count = _copy_tree(REPO_ROOT / name, app_dir / name)
        print(f"      {name:8s} -> {count} 个文件")
    count = _copy_config(REPO_ROOT / "config", app_dir / "config")
    print(f"      config   -> {count} 个文件（已剔除凭据/私钥/容器配置）")
    for name in ROOT_FILES:
        shutil.copy2(REPO_ROOT / name, app_dir / name)
        print(f"      {name} -> 1 个文件")
    for name in ROOT_METADATA_FILES:
        source = REPO_ROOT / name
        if not source.is_file():
            print(f"      {name} -> 缺失（可选，跳过）")
            continue
        shutil.copy2(source, app_dir / name)
        print(f"      {name} -> 1 个文件")

    print("[3/5] 复制驾驶舱构建产物与脚本")
    if not (CONSOLE_DIST / "index.html").is_file():
        raise SystemExit(
            f"驾驶舱构建产物缺失：{CONSOLE_DIST}/index.html\n"
            "先执行：cd apps/console/console && npm install && npm run build"
        )
    console_count = _copy_tree(CONSOLE_DIST, app_dir / "console")
    print(f"      console  -> {console_count} 个文件")
    backend_contract = read_backend_contract_from_source(
        (app_dir / "src" / "gateway" / "contract.py").read_text(encoding="utf-8")
    )
    console_files = console_files_under(
        {
            path.relative_to(app_dir).as_posix(): path.read_bytes()
            for path in (app_dir / "console").rglob("*")
            if path.is_file()
        }
    )
    console_stamp = build_stamp(
        console_files, backend_contract=backend_contract, version=version
    )
    (app_dir / "console" / "console-contract.json").write_text(
        json.dumps(console_stamp, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"      console 契约签章 -> api_contract={console_stamp['api_contract']}，"
        f"{console_stamp['console_files']} 个文件，"
        f"指纹 {console_stamp['console_fingerprint'][:12]}…"
    )
    script_count = _copy_scripts(app_dir)
    print(f"      scripts  -> {script_count} 个文件")

    print("[4/5] 组装离线 Python 运行时")
    runtime_count = _copy_runtime(app_dir)
    site_count, missing, test_skipped = _copy_distributions(app_dir, dists)
    print(f"      runtime  -> {runtime_count} 个文件（CPython 标准库）")
    print(f"      deps     -> {site_count} 个文件（{len(dists)} 个发行包，"
          f"剔除上游测试文件 {test_skipped} 个）")
    if missing:
        print(f"      !! 未能复制：{missing}", file=sys.stderr)
    prune_enabled = prune and os.environ.get("LIUHAO_PAYLOAD_PRUNE", "1").strip().lower() not in {
        "0", "false", "off", "no",
    }
    prune_report = _prune_payload(app_dir, enabled=prune_enabled, version=version)
    _validate_runtime(app_dir)
    seeded = _seed_missing_dist_licenses(app_dir)
    if seeded:
        print(f"      许可证补齐 -> {seeded} 个发行包缺随包文本，已按上游 LICENSE 补入 dist-info")

    print("[5/5] 写入入口脚本与清单")
    _write_start_bat(app_dir)
    _write_start_here(app_dir, version)
    notices_count = _copy_notices(app_dir)
    sbom_bytes = _write_installed_sbom(app_dir)
    license_count = _copy_product_license(app_dir)
    icon_ok = _write_ico(ICON_PNG, app_dir / "assets" / "liuhao.ico")
    print(f"      入口     -> LiuHao.bat / START-HERE.txt"
          f"{' / assets/liuhao.ico' if icon_ok else ''}")
    print(f"      合规材料 -> THIRD-PARTY-NOTICES.txt / licenses（{notices_count} 个文件）"
          f" / sbom-cyclonedx.json（{sbom_bytes} 字节）"
          f"{' / LICENSE' if license_count else ' / LICENSE（缺，见上方警告）'}")
    _assert_brand_guard(app_dir)
    _run_compliance_gate(app_dir)
    _assert_payload_hygiene(app_dir)
    print("      凭据检查 -> 未发现 auth_secrets / human_identities / servers.json / .env")

    dev_hits = _audit_dev_paths(app_dir)
    blocking = [
        hit for hit in dev_hits
        if "LiuHao-AI-OS" in hit and not hit.startswith("src/")
    ]
    if blocking:
        raise SystemExit(
            "安装包夹带了开发机仓库路径（打包缺陷）：\n  "
            + "\n  ".join(blocking)
        )
    if dev_hits:
        print(f"      开发路径审计：{len(dev_hits)} 处（均为布局探测/注释，详见 manifest）")
        for hit in dev_hits[:10]:
            print(f"        - {hit}")
    else:
        print("      开发路径审计：未发现开发机路径")

    total = sum(1 for entry in app_dir.rglob("*") if entry.is_file())
    size_mb = sum(
        entry.stat().st_size for entry in app_dir.rglob("*") if entry.is_file()
    ) / (1024 * 1024)
    # 入口文件存在性写进清单，供出包自检核对（升级按「新版缺失就删」处理，
    # 缺一个入口文件，装完的桌面快捷方式就会断）。
    entry_files = {
        rel: (app_dir / rel).is_file()
        for rel in ("LiuHao.bat", "START-HERE.txt", "assets/liuhao.ico")
    }

    manifest = {
        "version": version,
        "python": sys.version.split()[0],
        "base_python": str(Path(sys.base_prefix)),
        "distributions": dict(sorted(dists.items(), key=lambda kv: kv[0].lower())),
        "skipped_modules": unmapped,
        "file_count": total,
        "size_mb": round(size_mb, 1),
        "entry_files": entry_files,
        "contains_model_weights": False,
        "contains_secrets": False,
        "desktop_entry": "scripts/liuhao_desktop.py",
        "desktop_runtime": "runtime/pythonw.exe",
        "dev_path_hits": dev_hits,
        "prune": {
            "enabled": prune_report["enabled"],
            "files": prune_report["files"],
            "freed_mb": round(prune_report["freed_bytes"] / (1024 * 1024), 2),
            "by_rule": prune_report["by_rule"],
            "guard_check": prune_report["guard_check"],
        },
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print()
    print(f"payload 就绪：{app_dir}")
    print(f"  文件总数  {total}")
    print(f"  体积      {size_mb:.1f} MB")
    print(f"  清单      {out_dir / 'manifest.json'}")
    return 0


def _project_version() -> str:
    """版本号唯一可编辑真源：``src/version.py`` 的 ``APP_VERSION``。

    ``pyproject.toml`` 只是构建元数据的镜像。此前这里与
    ``build_windows_installer.ps1`` 各兜底一个 ``"0.1.1"`` 字面量，改版本时
    两处会悄悄漂移；现在只认真源，读不到就明确失败，不再静默用旧值。
    """
    source = (REPO_ROOT / "src" / "version.py").read_text(encoding="utf-8")
    match = re.search(r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']', source, re.M)
    if not match:
        raise RuntimeError(
            "src/version.py 中找不到 APP_VERSION（版本号唯一真源缺失）"
        )
    return match.group(1)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="组装 Windows 安装包 payload")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help=f"输出目录（默认 {DEFAULT_OUT}）")
    parser.add_argument("--version", default="", help="版本号（默认读 src/version.py 的 APP_VERSION）")
    parser.add_argument(
        "--no-prune",
        action="store_true",
        help="关闭 A1–A13 剪枝（回滚用）：保留 pythonwin/rich/tests 等全部候选文件，"
             "也不写导入守卫。等价于 LIUHAO_PAYLOAD_PRUNE=0。",
    )
    args = parser.parse_args(argv)
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    return build(
        Path(args.out).expanduser().resolve(),
        args.version or _project_version(),
        prune=not args.no_prune,
    )


if __name__ == "__main__":
    raise SystemExit(main())
