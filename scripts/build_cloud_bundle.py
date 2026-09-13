#!/usr/bin/env python
"""Build a self-contained, single-port bundle that can be published anywhere.

Why a bundle instead of publishing the repository
-------------------------------------------------
Three things in the repository root make it unpublishable as-is:

1. ``docker-compose.yml`` declares a **Postgres service**. A single-port host
   (the publishing sandbox, most PaaS) rejects a project that expects an
   external database, because the app would start without it and fail at the
   first query. The gateway does not actually use Postgres -- see
   ``docker-compose.prod.yml`` for the measurement.
2. ``requirements.txt`` is a full ``uv`` lockfile for the whole monorepo:
   hundreds of distributions, most of which the gateway never imports.
   Installing that inside a sandbox is slow and fragile.
3. ``node_modules``, ``.git`` and a previous build output are pure weight.

So this script produces ``deploy/cloud/``: the source tree, the built console,
and a **requirements list derived from the gateway's actual import graph**
rather than a hand-maintained list that drifts the moment someone adds an
import.

Measured, not assumed
---------------------
The startup import graph was verified to be the *complete* closure for every
endpoint the console calls: exercising ``/v1/ready``, ``/v1/dashboard/*``,
``/v1/profile``, ``/v1/chat/*``, ``/v1/policy/*`` and the knowledge routes added
**zero** new third-party top-level modules. That is why importing the app once
is enough here.

What is deliberately not here
-----------------------------
Postgres and Redis. Needing them is precisely what makes a project
unpublishable, and the gateway uses neither. Human identity persistence uses
SQLite (``src/kernels/identity/_persistence.py``), which is a file inside the
bundle -- no service required.

Usage
-----
    .venv\\Scripts\\python.exe scripts\\build_cloud_bundle.py
    .venv\\Scripts\\python.exe scripts\\build_cloud_bundle.py --out deploy/cloud
"""
from __future__ import annotations

import argparse
import importlib.metadata as md
import os
import shutil
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "deploy" / "cloud"

#: Copied verbatim into the bundle.
SOURCE_DIRS = ("src", "config")

#: Dropped from ``config/`` after copying. Neither is read by the gateway --
#: they configure the Prometheus/Grafana stack and the Kubernetes production
#: profile, both of which live in their own compose files. They are excluded
#: because they carry dev-only placeholder secrets and monitoring scrape rules
#: that have no business in a bundle served from a public URL.
CONFIG_EXCLUDES = ("production", "monitoring")

#: Built console output. Copied to a directory named ``console`` rather than
#: ``dist`` because the publishing uploader **strips conventional build-output
#: directories**. Measured: shipping it as ``apps/console/console/dist`` made
#: the deployed link answer ``/`` with a 404 while every API route worked --
#: the bundle was uploaded without its frontend. A plain name survives, and
#: ``serve.py`` points the gateway at it via ``LIUHAO_CONSOLE_DIST``.
CONSOLE_DIST_SRC = REPO_ROOT / "apps" / "console" / "console" / "dist"
CONSOLE_DIST_REL = Path("console")

#: Entry point written into the bundle. It sets the console location and then
#: defers to the gateway's own PORT-aware runner, so the start command needs no
#: shell features and no environment plumbing on the host side.
LAUNCHER_NAME = "serve.py"

_LAUNCHER = '''#!/usr/bin/env python
"""Bundle entry point: start the gateway with its console beside it.

Why this file exists
--------------------
The publishing uploader drops conventional build-output directories, so the
built console ships in a plain ``console/`` directory instead of ``dist/``.
The gateway finds it through ``LIUHAO_CONSOLE_DIST`` -- set here rather than in
a start command, because that does not depend on the host running the command
through a shell.

Everything else (port, host, logging level) comes from the environment;
see ``src/gateway/__main__.py``.
"""
import os
import sys

BUNDLE_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BUNDLE_ROOT)

_console = os.path.join(BUNDLE_ROOT, "console")
if os.path.isdir(_console):
    os.environ.setdefault("LIUHAO_CONSOLE_DIST", _console)

# The README/runbook convention is to point the kernel at the bundled seed
# file. The seed ships empty, so this registers nobody and the fail-closed
# default stands -- it only turns "(unconfigured)" into a real path in the
# boot log, and gives a would-be operator a name for the file to edit.
_seed = os.path.join(BUNDLE_ROOT, "config", "human_identities.json")
if os.path.isfile(_seed):
    os.environ.setdefault("LIUHAO_HUMAN_IDENTITIES_FILE", _seed)

from src.gateway.__main__ import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
'''

#: Runtime state that must never travel inside a published bundle. The
#: ``*.db`` trio covers the SQLite audit/conversation stores the gateway
#: creates on first start; the ``*.sqlite3`` trio covers human identities.
_IGNORED = shutil.ignore_patterns(
    "__pycache__", "*.pyc", "*.pyo",
    "*.sqlite3", "*.sqlite3-wal", "*.sqlite3-shm",
    "*.db", "*.db-wal", "*.db-shm",
)


def _third_party_top_level() -> Set[str]:
    """Every non-stdlib, non-project top-level module the gateway pulls in."""
    stdlib = set(sys.stdlib_module_names)
    # Names that show up in ``sys.modules`` without belonging to any
    # distribution. ``cython_runtime`` is injected by any Cython-compiled
    # extension; the rest are Windows interpreter shims. Reporting them as
    # "unmapped" would be noise that hides a genuine gap.
    pseudo = {"cython_runtime", "sitecustomize", "pywin32_bootstrap"}
    return {
        name.split(".", 1)[0]
        for name in sys.modules
        if not name.startswith(("src", "_"))
        and name.split(".", 1)[0] not in stdlib
        and name.split(".", 1)[0] not in pseudo
        and not name.split(".", 1)[0].startswith("pywin32")
    }


def _distributions_for(modules: Iterable[str]) -> Tuple[Dict[str, str], List[str]]:
    """Map imported module names to installed distributions with versions."""
    mapping = md.packages_distributions()
    found: Dict[str, str] = {}
    unmapped: List[str] = []
    for module in sorted(modules):
        candidates = list(mapping.get(module) or [])
        if not candidates:
            try:
                candidates = [md.distribution(module).metadata["Name"]]
            except Exception:  # noqa: BLE001 - a namespace package has no dist
                unmapped.append(module)
                continue
        for candidate in candidates:
            try:
                distribution = md.distribution(candidate)
            except Exception:  # noqa: BLE001
                unmapped.append(f"{module}->{candidate}")
                continue
            name = distribution.metadata["Name"] or candidate
            found[name] = distribution.version
    return found, unmapped


def _import_gateway_and_measure() -> Tuple[Dict[str, str], List[str]]:
    """Import the gateway here and report what it actually needs."""
    sys.path.insert(0, str(REPO_ROOT))
    before = set(sys.modules)
    import src.gateway.main  # noqa: F401  (imported for its side effect)
    del before
    return _distributions_for(_third_party_top_level())


def _copy_tree(source: Path, destination: Path) -> int:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination, ignore=_IGNORED)
    return sum(1 for _ in destination.rglob("*") if _.is_file())


def _reset_bundle_root(out_dir: Path) -> None:
    """Empty ``out_dir`` so nothing stale survives a rebuild.

    The per-directory copies below refresh ``src/`` and ``config/`` only; every
    other root entry was left untouched forever. Measured: an earlier ``apps/``
    layout and the ``audit_store.db`` / ``conversation_store.db`` files written
    when the bundle was started locally both **accumulated across rebuilds**
    and would have been shipped. Rebuilding must be a clean slate, not a merge.
    """
    resolved = out_dir.resolve()
    root = REPO_ROOT.resolve()
    if resolved == root or resolved in root.parents:
        raise SystemExit(f"拒绝清空 {resolved}：它与仓库根 {root} 重叠")
    out_dir.mkdir(parents=True, exist_ok=True)
    for entry in out_dir.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def _floor(version: str) -> str:
    """Turn an installed version into a *releasable* lower bound.

    Measuring the exact installed version is useful, but writing it as ``==``
    or even as a full ``>=`` fails in a different environment:

    * an exact pin breaks whenever the target interpreter differs from this
      venv's (measured: the publish sandbox rejected the whole file with
      "requires a different python version");
    * a full ``>=x.y.z`` breaks when that one release was **yanked** from PyPI,
      which is exactly what happened with ``numpy>=2.5.2`` -- nothing else
      satisfied the constraint for the sandbox's interpreter.

    A **major-version floor** keeps the useful claim ("this major line is known
    to work with this code") while leaving patch and minor selection to pip,
    which can see the target interpreter and the yank list. For ``0.x``
    packages, where the major carries no compatibility promise, the floor is
    major.minor instead.
    """
    parts = (version or "").split(".")
    try:
        major = int(parts[0])
    except (ValueError, IndexError):
        return version
    if major >= 1:
        return str(major)
    return ".".join(parts[:2]) if len(parts) >= 2 else str(major)


def build(out_dir: Path) -> int:
    if not (CONSOLE_DIST_SRC / "index.html").is_file():
        print(
            f"!! 驾驶舱构建产物缺失：{CONSOLE_DIST_SRC}/index.html\n"
            "   发布包会变成纯 API（链接打开是 404）。\n"
            "   先执行：cd apps/console/console && npm install && npm run build",
            file=sys.stderr,
        )
        return 1

    print("=" * 66)
    print("构建发布包")
    print("=" * 66)

    print("[1/3] 测量网关真实依赖（import 图）")
    requirements, unmapped = _import_gateway_and_measure()
    print(f"      发行包 {len(requirements)} 个")
    if unmapped:
        print(f"      !! 无法映射到发行包的模块（需人工确认）：{unmapped}")

    _reset_bundle_root(out_dir)
    print("[2/3] 复制源码与配置")
    for name in SOURCE_DIRS:
        source = REPO_ROOT / name
        if not source.is_dir():
            print(f"      !! 缺少 {source}", file=sys.stderr)
            return 1
        count = _copy_tree(source, out_dir / name)
        details = ""
        if name == "config":
            for excluded in CONFIG_EXCLUDES:
                target = out_dir / name / excluded
                if target.exists():
                    shutil.rmtree(target)
                    details = f"（已剔除 {', '.join(CONFIG_EXCLUDES)}）"
        print(f"      {name:8s} -> {count} 个文件{details}")

    print("[3/3] 复制驾驶舱构建产物 + 写入口脚本")
    console_target = out_dir / CONSOLE_DIST_REL
    console_count = _copy_tree(CONSOLE_DIST_SRC, console_target)
    print(f"      dist     -> {console_count} 个文件  →  {CONSOLE_DIST_REL}/")
    (out_dir / LAUNCHER_NAME).write_text(_LAUNCHER, encoding="utf-8")
    print(f"      入口脚本 -> {LAUNCHER_NAME}")

    lines = [
        "# 由 scripts/build_cloud_bundle.py 自动生成 —— 不要手改。",
        "# 内容来自网关的真实 import 图，因此不会与代码漂移。",
        "# 重新生成：python scripts/build_cloud_bundle.py",
        "#",
        "# 约束形式是「主版本下界」（numpy>=2），不是精确钉死。原因（均为实测）：",
        "#   * 精确 ==：版本号取自本机 venv（Python 3.12），沙箱解释器不同时 pip",
        "#     直接失败，报一串 'requires a different python version'；",
        "#   * 完整 >=x.y.z：一旦该版本被 PyPI yank（numpy 2.5.2 就是这样），",
        "#     在沙箱解释器下无任何版本可满足，同样失败。",
        "# 主版本下界保留了「这条主版本线已验证可用」的结论，其余交给 pip 按目标",
        "# 解释器与 yank 列表选择。0.x 包用 major.minor（0.x 的主版本不构成兼容承诺）。",
        "",
    ]
    lines += [f"{name}>={_floor(version)}" for name, version in
              sorted(requirements.items(), key=lambda kv: kv[0].lower())]
    (out_dir / "requirements.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    total = sum(1 for _ in out_dir.rglob("*") if _.is_file())
    print()
    print(f"发布包就绪：{out_dir}")
    print(f"  文件总数      {total}")
    print(f"  依赖数        {len(requirements)}")
    print(f"  启动命令      python {LAUNCHER_NAME}   (读取 $PORT，默认 8080)")
    print(f"  驾驶舱        同端口，来自 {CONSOLE_DIST_REL}/")
    print()
    print("  本地自测      cd 本目录 && PORT=8080 python serve.py")
    return 0


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="构建可发布的单端口自包含包")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help=f"输出目录（默认 {DEFAULT_OUT}）")
    args = parser.parse_args(argv)
    # Keep bytecode out of the bundle for the measurement import.
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    return build(Path(args.out).expanduser().resolve())


if __name__ == "__main__":
    raise SystemExit(main())
