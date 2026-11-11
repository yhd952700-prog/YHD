#!/usr/bin/env python
"""冻结打包前置硬门禁（历史踩坑 → 硬失败，不靠人记）。

六个子命令，全部「通过退出 0、不通过退出 2」：

``deps``            运行时依赖链完整性（``_MEASURE_CODE`` 传递依赖遗漏）
``env``             机器级/用户级 ``LIUHAO_*`` 环境变量污染 + 冒烟隔离回归
``frontend-source`` 前端源码指纹与模块数基线（旧工作树 → 模块数不一致）
``frontend-payload`` payload 内控制台与当前构建产物是否同源
``log-leak``        日志 / 诊断包 / 审计库里的明文凭据（泄露即失败）
``runbook``         收口清单单一事实源（``dist`` 副本只能由根文件生成）

用法（由 ``scripts/release/package-frozen.ps1`` 调用，也可单独跑）::

    python scripts/release/freeze_checks.py env --repo .
    python scripts/release/freeze_checks.py deps --repo . --python D:\\cache\\venvs\\...\\python.exe
    python scripts/release/freeze_checks.py frontend-source --repo . --module-count 80
    python scripts/release/freeze_checks.py frontend-payload --repo . --payload dist\\freeze\\payload\\app --version 0.1.1
    python scripts/release/freeze_checks.py log-leak --repo .
    python scripts/release/freeze_checks.py log-leak --repo . --data-dir D:\\LiuHaoData
    python scripts/release/freeze_checks.py runbook --repo .
    python scripts/release/freeze_checks.py runbook --repo . --sync

设计说明
--------
* 这些拦截都是**实测**，不是读注释：``deps`` 真的在子进程里跑一遍
  ``stage_payload._MEASURE_CODE``；``env`` 真的读注册表；``frontend`` 真的
  对字节取指纹。
* 失败信息写成中文可执行提示，报错即停（调用方 ``$ErrorActionPreference=Stop``）。
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

STAGE_PAYLOAD = Path("packaging/stage_payload.py")
SMOKE_PAYLOAD = Path("packaging/smoke_payload.py")
BASELINE_ENV = "LIUHAO_FREEZE_BASELINE"

#: ``_MEASURE_CODE`` 必须显式导入的模块。少一行，离线运行时就会漏掉它和它的
#: 传递依赖，实机表现是「对话一按就 ModuleNotFoundError」。这里的清单与
#: ``src/ai/providers.py`` / 知识导入 / 任务代理的延迟导入一一对应。
REQUIRED_MODULE_IMPORTS: Tuple[str, ...] = (
    "openai",
    "httpx",
    "httpcore",
    "httpx2",
    "httpcore2",
    "truststore",
    "anthropic",
    "google.genai",
    "docx",
    "pypdf",
    "sqlalchemy",
    "taskiq",
    "uvicorn",
    "dotenv",
    "aiosqlite",
    # 桌面壳（pywebview）整条运行栈：窗口后端在 native_window.py 的函数体里
    # 才导入，启动图看不到；缺任何一条，安装版双击就是 ImportError 或开不出窗。
    "webview",
    "bottle",
    "proxy_tools",
    "clr_loader",
    "clr",
)

#: 必须由 **真实 import 图** 带上的发行包。只写进 ``EXPLICIT_DISTS`` 是不够的：
#: 那里只记包名，不会带上传递依赖（历史故障：httpx / google.auth 漏包）。
REQUIRED_MEASURED_DISTS: Tuple[str, ...] = (
    "openai",
    "httpx",
    "httpcore",
    "httpx2",
    "httpcore2",
    "truststore",
    "anthropic",
    "google-genai",
    "python-docx",
    "lxml",
    "pypdf",
    "sqlalchemy",
    "greenlet",
    "taskiq",
    "uvicorn",
    "python-dotenv",
    "aiosqlite",
    "websockets",
    "python-multipart",
    "jiter",
    "h11",
    "click",
    # 桌面壳运行栈必须由真实 import 图带上（不是 EXPLICIT_DISTS 兜底）：
    # pywebview 的 WinForms 后端开窗时才真正用到 pythonnet/clr_loader/bottle/
    # proxy_tools，漏一个都表现为窗口起不来。
    "pywebview",
    "pythonnet",
    "clr-loader",
    "bottle",
    "proxy-tools",
)

#: 由 ``EXPLICIT_DISTS`` 兜底、不进 import 图的发行包（子进程入口/条件导入）。
REQUIRED_EXPLICIT_DISTS: Tuple[str, ...] = (
    "RestrictedPython",
    "cffi",
    "pywin32",
)

#: 传递依赖：上一版真的漏过 ``google.auth``（google-genai 的导入副作用），
#: 缺了只在用户选 Gemini 时才炸。这里把它们钉死。
REQUIRED_TRANSITIVE: Tuple[str, ...] = (
    "google-auth",
    "pydantic",
    "pydantic-core",
    "anyio",
    "sniffio",
    "certifi",
    "typing-extensions",
    "urllib3",
    "requests",
)

#: pywin32 没有 top_level.txt，``importlib.metadata.packages_distributions()``
#: 映射不到它的 ``win32con`` / ``ntsecuritycon`` 等子模块；这些模块由
#: ``EXPLICIT_DISTS`` 里的 pywin32 整包提供，不属于「漏包」。
PYWIN32_MODULES: frozenset = frozenset({
    "win32api", "win32con", "win32gui", "win32file", "win32process",
    "win32event", "win32clipboard", "win32com", "win32ts", "win32job",
    "ntsecuritycon", "pythoncom", "pywintypes", "winerror", "servicemanager",
})

#: 由 ``.pth`` 提供顶层导入名、``packages_distributions()`` 映射不到的模块，
#: 值指明它属于哪个发行包（该发行包整体随包，不属于「漏包」）。
#: ``pythonnet`` 安装的 ``pythonnet.pth`` 会把 ``CLR`` 顶层名指到它的运行时，
#: 因此 ``import CLR`` 在 import 图里没有任何 dist-info 映射。
PTH_PROVIDED_MODULES: Dict[str, str] = {
    "CLR": "pythonnet",
}

#: ``smoke_payload`` 必须剥离的变量样板。少一个就会出现「注册写仓库、登录读
#: 数据目录」的 403 假失败。
REQUIRED_SMOKE_EXACT: Tuple[str, ...] = (
    "AUDIT_DB_PATH",
    "CONVERSATION_DB_PATH",
    "MEMORY_DB_PATH",
    "PERSONAL_CONTEXT_DB_PATH",
    "WORKSPACE_ROOT",
)

#: 前端产物指纹覆盖的文件（相对 ``apps/console/console``）。
#: 只有会改变 ``dist`` 字节或 tsc 结果的输入才计入；测试文件计入是为了让
#: 「旧工作树缺文件」也能撞上基线。
FRONTEND_INCLUDE_DIRS: Tuple[str, ...] = ("src", "public")
FRONTEND_INCLUDE_FILES: Tuple[str, ...] = (
    "index.html",
    "package.json",
    "package-lock.json",
    "vite.config.ts",
    "tsconfig.json",
    "tsconfig.app.json",
    "tsconfig.node.json",
)
FRONTEND_SKIP_SUFFIXES: Tuple[str, ...] = (".tsbuildinfo",)


def _fail(tag: str, message: str) -> int:
    print(f"[{tag}] FAIL: {message}")
    return 2


def _ok(tag: str, message: str) -> int:
    print(f"[{tag}] PASS: {message}")
    return 0


def _warn(tag: str, message: str) -> None:
    print(f"[{tag}] WARN: {message}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _set_fingerprint(pairs: Iterable[Tuple[str, str]]) -> str:
    """与 ``console_contract.console_fingerprint`` 同构：路径 + 内容哈希排序后整体再哈希。"""
    digest = hashlib.sha256()
    for rel, file_hash in sorted(pairs):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_hash.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _normalise_dist(name: str) -> str:
    return name.strip().lower().replace("_", "-")


# --------------------------------------------------------------------------- deps
def _load_measure_code(repo: Path, stage: Path) -> str:
    source = (repo / stage).read_text(encoding="utf-8-sig")
    tree = ast.parse(source)
    measure: Optional[str] = None
    pseudo: List[str] = []
    for node in tree.body:
        target = None
        value = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            # stage_payload 里 ``PSEUDO_MODULES: Set[str] = {...}`` 是带注解赋值，
            # 只认 ast.Assign 会静默取不到，伪模块过滤器就失效了。
            target, value = node.target, node.value
        if not isinstance(target, ast.Name):
            continue
        if target.id == "_MEASURE_CODE" and isinstance(value, ast.Constant):
            measure = str(value.value)
        if target.id == "PSEUDO_MODULES" and isinstance(value, (ast.Set, ast.Tuple, ast.List)):
            for item in value.elts:
                if isinstance(item, ast.Constant) and isinstance(item.value, str):
                    pseudo.append(item.value)
    if measure is None:
        raise SystemExit("[gate-deps] FAIL: packaging/stage_payload.py 里找不到 _MEASURE_CODE 字符串")
    return measure % {"pseudo": sorted(pseudo)}


def _run_measure(repo: Path, python: str, code: str) -> Dict[str, Any]:
    env = {key: value for key, value in os.environ.items() if key.upper() != "PYTHONPATH"}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [python, "-c", code],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    if proc.returncode != 0:
        raise SystemExit(
            "[gate-deps] FAIL: 依赖测量子进程失败（exit %s）\n--- stdout ---\n%s\n--- stderr ---\n%s"
            % (proc.returncode, proc.stdout.strip(), proc.stderr.strip())
        )
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT:"):
            return json.loads(line[len("RESULT:"):])
    raise SystemExit(f"[gate-deps] FAIL: 依赖测量没有输出 RESULT 行\n{proc.stdout}\n{proc.stderr}")


def _load_stage_tuple(repo: Path, name: str, stage: Path) -> List[str]:
    """从 stage_payload.py 里读一个字符串元组常量（含带注解赋值）。"""
    tree = ast.parse((repo / stage).read_text(encoding="utf-8-sig"))
    for node in tree.body:
        target = None
        value = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        if isinstance(target, ast.Name) and target.id == name and isinstance(
            value, (ast.Tuple, ast.List, ast.Set)
        ):
            return [item.value for item in value.elts if isinstance(item, ast.Constant)]
    return []


def check_deps(repo: Path, python: str, stage: Optional[Path] = None) -> int:
    tag = "gate-deps"
    stage_file = stage or STAGE_PAYLOAD
    code = _load_measure_code(repo, stage_file)

    missing_imports = [
        module for module in REQUIRED_MODULE_IMPORTS
        if f"import {module}" not in code
    ]
    if missing_imports:
        return _fail(
            tag,
            "_MEASURE_CODE 缺少关键导入：" + "、".join(missing_imports)
            + "。这些模块只在业务函数体里延迟导入，删掉这一行，离线运行时就会漏掉"
              "它和它的传递依赖（历史故障：No module named 'httpx'）。",
        )

    payload = _run_measure(repo, python, code)
    dists = {_normalise_dist(name): version for name, version in (payload.get("dists") or {}).items()}
    unmapped = payload.get("unmapped") or []

    print(f"[{tag}] 测量环境：{python}")
    print(f"[{tag}] import 图解析发行包：{len(dists)} 个")

    if unmapped:
        unexpected = [
            name for name in unmapped
            if name.split("->")[0] not in PYWIN32_MODULES
            and name.split("->")[0] not in PTH_PROVIDED_MODULES
        ]
        if unexpected:
            return _fail(tag, f"有模块无法映射到发行包（离线运行时会缺文件）：{unexpected}")
        print(f"[{tag}] pywin32 子模块无发行包映射（整个 pywin32 由 EXPLICIT_DISTS 兜底）：{unmapped}")
        print(f"[{tag}] .pth 提供的顶层名已按发行包归位：{PTH_PROVIDED_MODULES}")

    missing = [name for name in REQUIRED_MEASURED_DISTS if _normalise_dist(name) not in dists]
    missing += [name for name in REQUIRED_TRANSITIVE if _normalise_dist(name) not in dists]
    if missing:
        return _fail(
            tag,
            "以下运行期依赖没有被 import 图带上：" + "、".join(sorted(set(missing)))
            + "。把它们（或其真实导入入口）加进 _MEASURE_CODE，不要只加 EXPLICIT_DISTS："
              "EXPLICIT_DISTS 只记发行包名，不会带上传递依赖。",
        )

    explicit = _load_stage_tuple(repo, "EXPLICIT_DISTS", stage_file)
    shipped = set(dists) | {_normalise_dist(name) for name in explicit}
    missing_explicit = [
        name for name in REQUIRED_EXPLICIT_DISTS if _normalise_dist(name) not in shipped
    ]
    if missing_explicit:
        return _fail(
            tag,
            "以下发行包既不在 import 图里，也不在 EXPLICIT_DISTS 里："
            + "、".join(missing_explicit),
        )
    only_explicit = sorted(set(REQUIRED_EXPLICIT_DISTS) - {_normalise_dist(n) for n in dists})
    if only_explicit:
        print(f"[{tag}] 仅由 EXPLICIT_DISTS 兜底（不进 import 图）：" + "、".join(only_explicit))

    print(f"[{tag}] 关键发行包：" + "、".join(
        f"{name}={dists[_normalise_dist(name)]}" for name in REQUIRED_MEASURED_DISTS
    ))
    return _ok(
        tag,
        "运行时依赖链完整（含 google.auth / httpx / httpx2 等已知漏点，"
        f"{len(dists)} 个 import 图发行包 + {len(explicit)} 个显式兜底）",
    )


# ---------------------------------------------------------------------------- env
def _registry_env(scope: str) -> Dict[str, str]:
    """读注册表里的用户/机器级环境变量（与当前进程环境分开看）。"""
    if sys.platform != "win32":
        return {}
    import winreg

    root = winreg.HKEY_CURRENT_USER if scope == "user" else winreg.HKEY_LOCAL_MACHINE
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
        return {}
    return values


def _load_module_by_path(path: Path, name: str):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"[gate-env] FAIL: 无法加载 {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_env(repo: Path) -> int:
    tag = "gate-env"
    guard = _load_module_by_path(repo / "packaging/env_guard.py", "liuhao_env_guard")

    findings = guard.collect(repo, guard.default_temp_roots(repo))
    if findings:
        print(guard.format_findings(findings))
        return _fail(tag, "环境里存在指向源码仓库/开发临时目录的变量，构建与冒烟都会错位。按上面的提示清除后重试。")

    # env_guard 只看「值是否指向仓库/临时目录」；这里再加一条更硬的：
    # 用户级/机器级**存在**任何 LIUHAO_*/AUDIT_DB_* 就停，因为它们会被
    # 子进程（npm、uvicorn、安装版冒烟）继承，表现为只在别人机器上复现的 403。
    watched_scopes: List[str] = []
    for scope in ("user", "machine"):
        for name, value in _registry_env(scope).items():
            if guard.is_watched(name):
                watched_scopes.append(f"{name} [{scope}] = {value}")
    if watched_scopes:
        return _fail(
            tag,
            "注册表里残留机器级/用户级配置（会污染冒烟与安装版）：\n  - "
            + "\n  - ".join(watched_scopes)
            + "\n清除：[Environment]::SetEnvironmentVariable('<变量名>', $null, 'User'|'Machine')",
        )

    # 冒烟隔离回归：smoke_payload 必须按前缀整体剥离，而不是逐个枚举。
    source = (repo / SMOKE_PAYLOAD).read_text(encoding="utf-8")
    tree = ast.parse(source)
    prefixes: List[str] = []
    exact: List[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name == "_ISOLATED_PREFIXES" and isinstance(node.value, (ast.Tuple, ast.List)):
                prefixes = [e.value for e in node.value.elts if isinstance(e, ast.Constant)]
            if name == "_ISOLATED_EXACT" and isinstance(node.value, (ast.Call, ast.Set, ast.Tuple, ast.List)):
                target = node.value.args[0] if isinstance(node.value, ast.Call) else node.value
                if isinstance(target, (ast.Set, ast.Tuple, ast.List)):
                    exact = [e.value for e in target.elts if isinstance(e, ast.Constant)]
    if "LIUHAO_" not in prefixes:
        return _fail(tag, "smoke_payload._ISOLATED_PREFIXES 不再包含 'LIUHAO_'：冒烟会被机器级变量污染（历史故障：登录 403）。")
    missing_exact = [name for name in REQUIRED_SMOKE_EXACT if name not in exact]
    if missing_exact:
        return _fail(tag, "smoke_payload._ISOLATED_EXACT 缺少：" + "、".join(missing_exact))

    return _ok(tag, "进程/用户/机器三级环境干净；冒烟隔离仍按 LIUHAO_* 前缀整体剥离")


# ----------------------------------------------------------------------- frontend
def _frontend_files(console: Path) -> List[Path]:
    picked: List[Path] = []
    for rel in FRONTEND_INCLUDE_FILES:
        path = console / rel
        if path.is_file():
            picked.append(path)
    for rel_dir in FRONTEND_INCLUDE_DIRS:
        root = console / rel_dir
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.is_file() and not path.name.endswith(FRONTEND_SKIP_SUFFIXES):
                picked.append(path)
    return sorted(set(picked))


def frontend_source_state(console: Path) -> Dict[str, Any]:
    files = _frontend_files(console)
    pairs = [(path.relative_to(console).as_posix(), _sha256_file(path)) for path in files]
    return {
        "console_dir": str(console),
        "file_count": len(pairs),
        "fingerprint": _set_fingerprint(pairs),
        "newest_mtime": max((path.stat().st_mtime for path in files), default=0),
        "files": [rel for rel, _ in pairs],
    }


def _baseline_path() -> Path:
    """前端基线是**构建机状态**，不写进仓库：写仓库会让冻结树在构建后变脏。"""
    override = os.environ.get(BASELINE_ENV)
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "LiuHaoBuild" / "frontend-baseline.json"


def _load_baseline() -> Dict[str, Any]:
    path = _baseline_path()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def check_frontend_source(repo: Path, module_count: Optional[int], capture: bool) -> int:
    tag = "gate-frontend"
    console = repo / "apps/console/console"
    if not console.is_dir():
        return _fail(tag, f"找不到驾驶舱目录 {console}（工作树不完整？）")
    state = frontend_source_state(console)
    if state["file_count"] == 0:
        return _fail(tag, "驾驶舱源码为空：apps/console/console/src 下没有文件（旧工作树/浅检出？）")

    baseline = _load_baseline()
    print(f"[{tag}] 前端源码：{state['file_count']} 个文件，指纹 {state['fingerprint'][:16]}…")
    print(f"[{tag}] 基线：{baseline.get('source_fingerprint', '（未采集）')[:16]}…，"
          f"模块数 {baseline.get('module_count', '（未记录）')}"
          f"（来源 {baseline.get('captured_from', '—')}）")

    captured_fp = str(baseline.get("source_fingerprint") or "")
    if captured_fp and captured_fp != state["fingerprint"]:
        if not capture:
            return _fail(
                tag,
                "前端源码与冻结基线不一致（典型原因：在旧工作树/旧提交上打包，前端模块数会少）。\n"
                f"  基线 {captured_fp[:16]}… ≠ 当前 {state['fingerprint'][:16]}…\n"
                "确认当前工作树就是本次要发布的代码后，用 -AcceptFrontendBaseline 重新采集基线；"
                "否则请切到冻结提交再打包。",
            )
        _warn(tag, "基线与当前源码不一致，按 -AcceptFrontendBaseline 重新采集。")
    if not captured_fp:
        _warn(tag, "尚未采集前端源码基线：首次冻结构建会写入（之后每次构建都会比对）。")

    baseline_count = baseline.get("module_count")
    if module_count is not None:
        if (
            isinstance(baseline_count, int)
            and baseline_count > 0
            and module_count != baseline_count
        ):
            if not capture:
                return _fail(
                    tag,
                    f"前端模块数与基线不一致：基线 {baseline_count}，本次构建 {module_count}。"
                    "模块数变少通常意味着打包用错了工作树（旧前端），或前端删文件未经确认。"
                    "确认后用 -AcceptFrontendBaseline 更新基线。",
                )
            _warn(tag, f"模块数 {baseline_count} → {module_count}，按 -AcceptFrontendBaseline 更新基线。")
        if not isinstance(baseline_count, int) or baseline_count <= 0:
            _warn(tag, f"基线未记录模块数，本次构建实测 {module_count}（将写入基线）。")
        print(f"[{tag}] 本次构建模块数：{module_count}（基线 {baseline_count}）")

    if capture:
        payload = {
            "note": "冻结打包前端基线：source_fingerprint 与 module_count 由真实构建采集，不要手改。",
            "module_count": module_count if module_count is not None else baseline_count,
            "source_fingerprint": state["fingerprint"],
            "file_count": state["file_count"],
            "captured_from": baseline.get("captured_from") or "scripts/release/package-frozen.ps1",
            "captured_at": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        target = _baseline_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[{tag}] 基线已写入 {target}")

    if isinstance(baseline_count, int) and baseline_count > 0 and module_count is None:
        print(f"[{tag}] 待本次构建确认模块数是否仍为 {baseline_count}")
        return 0
    if not captured_fp and not capture:
        return _ok(tag, "基线尚未采集（首次真实构建写入；本轮不比对指纹，只记录现状）")
    return _ok(tag, "前端源码与基线一致")


# 打包期由 stage_payload 注入的签章文件，是 console/ 下唯一允许的「清单外」文件。
# 白名单只到这一个具体文件名；除此之外多出任何文件都必须判红。
CONSOLE_CONTRACT_REL = "console/console-contract.json"


def _dir_console_files(app_dir: Path) -> Dict[str, Tuple[str, int]]:
    console = app_dir / "console"
    if not console.is_dir():
        return {}
    files: Dict[str, Tuple[str, int]] = {}
    for path in console.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(app_dir).as_posix()
        if rel == CONSOLE_CONTRACT_REL:
            continue
        files[rel] = (_sha256_file(path), path.stat().st_size)
    return files


def check_frontend_payload(repo: Path, app_dir: Path, version: str) -> int:
    tag = "gate-frontend"
    contract_path = app_dir / "console" / "console-contract.json"
    if not (app_dir / "console" / "index.html").is_file():
        return _fail(tag, f"payload 缺少 console/index.html：{app_dir}")
    if not contract_path.is_file():
        return _fail(tag, f"payload 缺少契约签章 {contract_path}：无法确认前端与后端同源。")
    try:
        stamp = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _fail(tag, f"契约签章无法解析：{exc}")

    if str(stamp.get("frontend_version") or "") != str(version):
        return _fail(
            tag,
            f"payload 前端版本 {stamp.get('frontend_version')!r} ≠ 本次版本 {version!r}："
            "装完界面还是旧版（历史故障：包内容全新却同号 0.1.0）。",
        )

    build_dist = repo / "apps/console/console/dist"
    if not (build_dist / "index.html").is_file():
        return _fail(tag, f"当前工作树没有驾驶舱构建产物 {build_dist}/index.html（未构建就被打包。）")

    dist_pairs = [
        # 键必须与签章里的键同构：console_contract.console_files_under() 用的是
        # 「console/…」这种 app 相对路径。少了这个前缀，指纹永远对不上，
        # 门禁会把每一份正常 payload 都判成「不同源」。
        ("console/" + path.relative_to(build_dist).as_posix(), _sha256_file(path))
        for path in build_dist.rglob("*") if path.is_file()
    ]
    dist_fp = _set_fingerprint(dist_pairs)
    stamp_fp = str(stamp.get("console_fingerprint") or "")
    if dist_fp != stamp_fp:
        return _fail(
            tag,
            "payload 里的控制台与当前工作树的构建产物不同源："
            f"签章 {stamp_fp[:16]}… ≠ apps/console/console/dist {dist_fp[:16]}…。"
            "典型原因：payload 是旧工作树/上一次构建留下的（历史故障：前端模块数不一致）。"
            "删掉旧 OutRoot 后重新打包，不要复用 payload。",
        )

    payload_files = _dir_console_files(app_dir)
    # 逐文件比对清单，不做「数量相等就算过」——数量相同但换了一个文件（多一个、少一个）
    # 同样会让界面跑到旧代码上，而计数发现不了。
    expected = {rel for rel, _ in dist_pairs}
    extra = sorted(set(payload_files) - expected)
    missing = sorted(expected - set(payload_files))
    if extra or missing:
        detail = []
        if extra:
            detail.append("多出 " + "、".join(extra))
        if missing:
            detail.append("缺少 " + "、".join(missing))
        return _fail(
            tag,
            "payload 控制台文件与构建产物清单不一致：" + "；".join(detail)
            + f"（清单外只允许打包期注入的 {CONSOLE_CONTRACT_REL}）。",
        )
    return _ok(tag, f"payload 控制台与当前构建产物同源（{len(dist_pairs)} 个文件，指纹 {dist_fp[:16]}…）")


# ------------------------------------------------------------------- log-leak
def check_log_leak(
    repo: Path,
    *,
    data_dir: Optional[Path] = None,
    extras: Optional[Iterable[Path]] = None,
) -> int:
    """明文凭据闸：调用验收扫描器，命中即失败。

    退出码沿用扫描器语义：``0`` 干净、``1`` 命中、``2`` 没有可扫描目标。
    发布闸把 ``2`` 视为 PASS-with-note（新工作树本来就没有 logs），把 ``1``
    视为硬失败；安装后对真实数据目录的复扫由 runbook 里那条 ``--data-dir``
    命令负责。
    """
    script = repo / "scripts" / "acceptance" / "log_leak_scan.py"
    if not script.is_file():
        return _fail("日志明文凭据", f"找不到扫描脚本 {script}")

    command = [sys.executable, str(script), "--json"]
    if data_dir is not None:
        command += ["--data-dir", str(data_dir)]
    else:
        # 发布闸只对受控目标负责：仓库 logs\ + 显式 --path，不读开发机上的
        # 默认数据目录/诊断包目录，避免"构建结果取决于这台机器装过什么"。
        command += ["--no-data-dir"]
    for extra in extras or ():
        command += ["--extra", str(extra)]

    try:
        completed = subprocess.run(
            command,
            cwd=str(repo),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        return _fail("日志明文凭据", f"扫描器无法启动：{exc}")

    if completed.returncode == 2:
        return _ok(
            "日志明文凭据",
            "没有可扫描目标（新工作树）；安装后请用 --data-dir 对真实数据目录复扫",
        )
    if completed.returncode != 0 and completed.returncode != 1:
        return _fail(
            "日志明文凭据",
            f"扫描器异常退出 {completed.returncode}：{(completed.stderr or '').strip()[:300]}",
        )

    try:
        payload: Dict[str, Any] = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        return _fail("日志明文凭据", "扫描器输出不是合法 JSON，无法判定")

    if completed.returncode == 0:
        return _ok(
            "日志明文凭据",
            f"{len(payload.get('files', []))} 个对象未发现明文凭据",
        )

    leaked = [item for item in payload.get("files", []) if item.get("hits")]
    detail = "；".join(
        f"{Path(str(item.get('path'))).name}={item.get('hits')}" for item in leaked
    )
    return _fail(
        "日志明文凭据",
        f"命中 {payload.get('total_plaintext_hits')} 处：{detail or '见扫描器输出'}",
    )


# ---------------------------------------------------------------------- runbook
#: 仓库根的收口清单是**唯一事实源**（随 git 跟踪）。
RUNBOOK_SOURCE = Path("RELEASE-RUNBOOK.md")
#: ``dist`` 下的副本是**生成物**：由 ``runbook --sync`` 从根文件复制，不允许手改。
RUNBOOK_COPY = Path("dist/release-gate/RELEASE-RUNBOOK.md")
RUNBOOK_GENERATED_HEADER = (
    "<!-- 本文件由仓库根 RELEASE-RUNBOOK.md 生成，请勿手改。 -->\n"
    "<!-- 同步命令：python scripts/release/freeze_checks.py runbook --sync -->\n\n"
)


def _runbook_text(path: Path) -> str:
    """统一换行后再比：Windows 检出可能把 LF 变成 CRLF。"""
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _runbook_body(text: str) -> str:
    return text[len(RUNBOOK_GENERATED_HEADER):] if text.startswith(RUNBOOK_GENERATED_HEADER) else text


def check_runbook(repo: Path, *, sync: bool = False) -> int:
    """单一事实源校验：``dist`` 副本只能由根文件生成，内容必须一致。

    ``--sync`` 会在收口时按根文件重写副本（只写这一个生成物）；不带 ``--sync``
    只读校验，用于人工复核与测试。没有副本时视为通过（构建不一定要副本）。
    """
    tag = "Runbook 同源"
    source = repo / RUNBOOK_SOURCE
    if not source.is_file():
        return _fail(tag, f"找不到权威文件 {RUNBOOK_SOURCE}（仓库根）")

    expected = _runbook_text(source)
    copy = repo / RUNBOOK_COPY
    if sync:
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_text(
            RUNBOOK_GENERATED_HEADER + expected, encoding="utf-8", newline="\n"
        )
        # 生成后立刻回读校验：只写不验等于把"副本没写全"留给下一个人发现。
        if _runbook_body(_runbook_text(copy)).strip() != expected.strip():
            return _fail(tag, f"已写入 {RUNBOOK_COPY}，但回读内容与根文件不一致")
        return _ok(tag, f"已从 {RUNBOOK_SOURCE} 重新生成 {RUNBOOK_COPY}，并校验一致")

    if not copy.is_file():
        return _ok(tag, f"未生成副本（{RUNBOOK_COPY} 不存在；构建需要时加 --sync）")

    if _runbook_body(_runbook_text(copy)).strip() != expected.strip():
        return _fail(
            tag,
            f"{RUNBOOK_COPY} 与根文件不一致（副本是生成物，不要手改）。"
            "请运行：python scripts/release/freeze_checks.py runbook --repo . --sync",
        )
    return _ok(tag, f"{RUNBOOK_COPY} 与根文件一致")


# ---------------------------------------------------------------------------- main
def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="冻结打包前置硬门禁")
    sub = parser.add_subparsers(dest="command", required=True)

    deps = sub.add_parser("deps", help="运行时依赖链完整性")
    deps.add_argument("--repo", default=".")
    deps.add_argument("--python", default=sys.executable)
    deps.add_argument("--stage-payload", default=None, help="覆盖被测的 stage_payload.py（回归测试用）")

    env = sub.add_parser("env", help="环境变量污染与冒烟隔离")
    env.add_argument("--repo", default=".")

    src = sub.add_parser("frontend-source", help="前端源码指纹/模块数基线")
    src.add_argument("--repo", default=".")
    src.add_argument("--module-count", type=int, default=None)
    src.add_argument("--capture", action="store_true", help="写入/更新基线文件")

    payload = sub.add_parser("frontend-payload", help="payload 控制台与构建产物同源")
    payload.add_argument("--repo", default=".")
    payload.add_argument("--payload", required=True, help="payload 的 app 目录")
    payload.add_argument("--version", required=True)

    leak = sub.add_parser("log-leak", help="日志/诊断包/审计库明文凭据扫描")
    leak.add_argument("--repo", default=".")
    leak.add_argument(
        "--data-dir",
        default=None,
        help="安装后的真实数据目录（含 logs/ 与 audit_store.db）；不传只扫仓库 logs/。",
    )
    leak.add_argument(
        "--path",
        action="append",
        default=[],
        help="额外文件/目录（payload、诊断包 zip 等），可重复。",
    )

    runbook = sub.add_parser("runbook", help="收口清单单一事实源（dist 副本是生成物）")
    runbook.add_argument("--repo", default=".")
    runbook.add_argument(
        "--sync",
        action="store_true",
        help=f"按 {RUNBOOK_SOURCE} 重写 {RUNBOOK_COPY}（只写这一个生成物）。",
    )

    args = parser.parse_args(argv)
    repo = Path(args.repo).resolve()

    if args.command == "deps":
        stage = Path(args.stage_payload) if args.stage_payload else None
        return check_deps(repo, args.python, stage)
    if args.command == "env":
        return check_env(repo)
    if args.command == "frontend-source":
        return check_frontend_source(repo, args.module_count, args.capture)
    if args.command == "frontend-payload":
        return check_frontend_payload(repo, Path(args.payload).resolve(), args.version)
    if args.command == "log-leak":
        return check_log_leak(
            repo,
            data_dir=Path(args.data_dir).resolve() if args.data_dir else None,
            extras=[Path(item).resolve() for item in args.path],
        )
    if args.command == "runbook":
        return check_runbook(repo, sync=args.sync)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
