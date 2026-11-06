#!/usr/bin/env python
"""组装 Windows 安装包 payload（程序目录内容）。

产物结构（``--out`` 默认 ``dist/payload``）::

    dist/payload/
      app/                     ← 安装到 %LOCALAPPDATA%\\LiuHao\\app 的全部内容
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
import shutil
import struct
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "dist" / "payload"
CONSOLE_DIST = REPO_ROOT / "apps" / "console" / "console" / "dist"
ICON_PNG = REPO_ROOT / "apps" / "console" / "console" / "public" / "icon-192.png"

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
CONFIG_EXCLUDES: Tuple[str, ...] = (
    "production",
    "monitoring",
    "external_anchor",
    "auth_secrets.json",
    "human_identities.json",
    "docker.env",
    "*.pem",
    "*.key",
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
    "scripts/first_run_wizard.py",
    "scripts/register_human_identity.py",
    "scripts/start_liuhao.py",
    "scripts/launch_installed.py",
    # 托盘自启入口：HKCU Run 项直接指向它，缺了就只能拉起网关、没有托盘。
    "scripts/liuhao_desktop.py",
    "scripts/ops/https_proxy.py",
    "scripts/gen_local_cert.py",
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
    "__pycache__", "*.pyc", "*.pyo",
    "*.db", "*.db-wal", "*.db-shm", "*.sqlite3", "*.sqlite3-wal", "*.sqlite3-shm",
)

_MEASURE_CODE = r"""
import importlib.metadata as md
import json
import sys

sys.path.insert(0, ".")
import src.gateway.main  # noqa: F401
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
import uvicorn  # noqa: F401
import uvicorn.main  # noqa: F401
import uvicorn.protocols.http.auto  # noqa: F401
import dotenv  # noqa: F401
import aiosqlite  # noqa: F401

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


def _write_start_bat(app_dir: Path) -> None:
    bat = """@echo off
rem LiuHao AI OS - installed launcher. ASCII-only on purpose.
setlocal
rem Keep the install tree read-only: never write __pycache__ next to the app.
set PYTHONDONTWRITEBYTECODE=1
cd /d "%~dp0"
if not exist "runtime\\python.exe" (
    echo [FAIL] runtime\\python.exe is missing - the installation is incomplete.
    pause
    exit /b 1
)
"runtime\\python.exe" "scripts\\launch_installed.py" %*
echo.
echo LiuHao stopped. Press any key to close.
pause >nul
endlocal
"""
    (app_dir / "LiuHao.bat").write_text(bat, encoding="ascii")


def _write_start_here(app_dir: Path, version: str) -> None:
    text = f"""鎏灏 LiuHao AI OS {version} —— 先看这里

1. 双击本目录下的 LiuHao.bat（或桌面/开始菜单快捷方式）。
2. 首次启动会在命令行窗口里进入向导：先建管理员账号，再选 AI Provider。
   - 选 Ollama：需要先安装 Ollama 并 ollama pull 一个模型（约 2GB）。
   - 选 OpenAI 兼容云：填 base_url、模型名、API Key（加密保存在数据目录）。
3. 向导结束后浏览器会自动打开 http://127.0.0.1:8080 。

数据目录：%LOCALAPPDATA%\\LiuHao\\data（卸载默认保留）。
本安装包不含任何模型权重，也不含任何密钥。
程序未做代码签名，Windows 可能提示“未知发布者”，选择“仍要运行”即可。
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
第三方依赖：按网关真实 import 图从构建虚拟环境复制，见 manifest.json。
还包含：桌面壳所需的 tkinter / Tcl-Tk 数据、pywin32（系统托盘）。
许可证：PSF License（见本目录 LICENSE.txt）、Tcl/Tk 各自许可证。
本目录随安装包分发，收件人无需另行安装 Python。
""",
        encoding="utf-8",
    )
    return sum(1 for entry in runtime.rglob("*") if entry.is_file())


def _copy_distributions(app_dir: Path, dists: Dict[str, str]) -> Tuple[int, List[str]]:
    """把命中的发行包文件复制进 runtime/Lib/site-packages。"""
    import importlib.metadata as md

    target = app_dir / "runtime" / "Lib" / "site-packages"
    target.mkdir(parents=True, exist_ok=True)
    copied = 0
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
                                    dirs_exist_ok=True, ignore=_IGNORE)
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
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copied += 1
    return copied, missing


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
    # 自检覆盖三类真实入口：网关、桌面壳（tkinter）、托盘（pywin32）。
    # `tkinter.Tcl()` 会真正加载 Tcl 的 init 脚本，能验证 tcl 数据目录是否可用。
    code = (
        "import sys, tkinter, win32api, win32gui, win32con, pywintypes, "
        "uvicorn, uvicorn.main, uvicorn.protocols.http.auto, "
        "dotenv, aiosqlite, src.gateway.main, src.desktop.app, "
        "openai, httpx2, httpcore2, truststore, jiter, httpx, httpcore; "
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


def build(out_dir: Path, version: str) -> int:
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
    site_count, missing = _copy_distributions(app_dir, dists)
    print(f"      runtime  -> {runtime_count} 个文件（CPython 标准库）")
    print(f"      deps     -> {site_count} 个文件（{len(dists)} 个发行包）")
    if missing:
        print(f"      !! 未能复制：{missing}", file=sys.stderr)
    _validate_runtime(app_dir)

    print("[5/5] 写入入口脚本与清单")
    _write_start_bat(app_dir)
    _write_start_here(app_dir, version)
    icon_ok = _write_ico(ICON_PNG, app_dir / "assets" / "liuhao.ico")
    print(f"      入口     -> LiuHao.bat / START-HERE.txt"
          f"{' / assets/liuhao.ico' if icon_ok else ''}")

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

    manifest = {
        "version": version,
        "python": sys.version.split()[0],
        "base_python": str(Path(sys.base_prefix)),
        "distributions": dict(sorted(dists.items(), key=lambda kv: kv[0].lower())),
        "skipped_modules": unmapped,
        "file_count": total,
        "size_mb": round(size_mb, 1),
        "contains_model_weights": False,
        "contains_secrets": False,
        "desktop_entry": "scripts/liuhao_desktop.py",
        "desktop_runtime": "runtime/pythonw.exe",
        "dev_path_hits": dev_hits,
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
    try:
        with open(REPO_ROOT / "pyproject.toml", "rb") as handle:
            return tomllib.load(handle)["project"]["version"]
    except Exception:
        return "0.1.0"


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="组装 Windows 安装包 payload")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help=f"输出目录（默认 {DEFAULT_OUT}）")
    parser.add_argument("--version", default="", help="版本号（默认读 pyproject.toml）")
    args = parser.parse_args(argv)
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    return build(Path(args.out).expanduser().resolve(), args.version or _project_version())


if __name__ == "__main__":
    raise SystemExit(main())
