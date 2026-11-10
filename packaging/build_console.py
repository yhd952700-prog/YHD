"""按当前前端源码构建控制台，并写入可校验的构建来源记录。

用法（仓库根执行）：
    .venv\\Scripts\\python.exe packaging\\build_console.py --version 0.1.2.1
    .venv\\Scripts\\python.exe packaging\\build_console.py --dest dist\\payload\\app\\console --version 0.1.2.1

打包端 ``packaging/make_release.py`` 会读取 ``console/console-build.json``，
与当前源码指纹、产物文件指纹对照；缺少记录或不一致都会拒绝出包。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import List

PACKAGING_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGING_DIR.parent
if str(PACKAGING_DIR) not in sys.path:
    sys.path.insert(0, str(PACKAGING_DIR))

import console_provenance  # noqa: E402


def _log(message: str) -> None:
    print(message, flush=True)


def _npm_executable() -> str:
    for name in ("npm.cmd", "npm"):
        found = shutil.which(name)
        if found:
            return found
    raise RuntimeError("找不到 npm：请先安装 Node.js，并确认 npm 在 PATH 中")


def _run_build(source_dir: Path) -> None:
    if not (source_dir / "node_modules").is_dir():
        raise RuntimeError(
            f"前端依赖目录不存在：{source_dir / 'node_modules'}\n"
            "请先在该目录执行 npm install（本工具不自动联网安装依赖）"
        )
    npm = _npm_executable()
    _log(f"[前端] npm run build（{source_dir}）")
    completed = subprocess.run(
        [npm, "run", "build"],
        cwd=str(source_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = ((completed.stdout or "") + (completed.stderr or "")).strip()
    if completed.returncode != 0:
        tail = "\n".join(output.splitlines()[-20:])
        raise RuntimeError(
            f"前端构建失败：npm run build exit {completed.returncode}\n{tail}"
        )
    for line in output.splitlines()[-10:]:
        _log(f"  {line}")


def _read_version(explicit: str | None) -> str:
    if explicit:
        return explicit.strip()
    pyproject = REPO_ROOT / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return str(data["project"]["version"]).strip()


def build(source_dir: Path, dest_dir: Path, version: str, *, skip_build: bool) -> int:
    dist_dir = source_dir / "dist"
    if skip_build:
        if not (dist_dir / "index.html").is_file():
            raise RuntimeError(
                f"--skip-build 要求已有构建产物 {dist_dir / 'index.html'}，但文件不存在"
            )
        _log(f"[前端] 跳过构建，使用已有产物 {dist_dir}")
        command = "npm run build (skipped; existing dist)"
    else:
        _run_build(source_dir)
        command = "npm run build"

    if not (dist_dir / "index.html").is_file():
        raise RuntimeError(f"前端构建完成但缺少入口文件：{dist_dir / 'index.html'}")

    console_provenance.copy_console_dist(dist_dir, dest_dir)
    meta = console_provenance.write_build_metadata(
        dest_dir,
        source_dir=source_dir,
        version=version,
        command=command,
    )
    _log(
        "[前端] 构建记录已写入 "
        f"{dest_dir / 'console-build.json'}  "
        f"source={meta['source_sha256'][:12]}…  files={meta['files_sha256'][:12]}…  "
        f"count={meta['file_count']}"
    )
    return 0


def main(argv: List[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="按当前源码构建控制台并写入来源记录")
    parser.add_argument(
        "--source",
        default=str(REPO_ROOT / "apps" / "console" / "console"),
        help="前端源码目录（默认仓库 apps/console/console）",
    )
    parser.add_argument(
        "--dest",
        default=None,
        help="构建产物目标 console 目录（默认与源码 dist 同目录）",
    )
    parser.add_argument("--version", default=None, help="发布版本号（默认读 pyproject.toml）")
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="跳过 npm run build，仅对已有 dist 写构建来源记录（供流水线复用刚完成的构建）",
    )
    args = parser.parse_args(argv)

    source_dir = Path(args.source).resolve()
    dest_dir = Path(args.dest).resolve() if args.dest else source_dir / "dist"
    try:
        version = _read_version(args.version)
        return build(source_dir, dest_dir, version, skip_build=args.skip_build)
    except (RuntimeError, FileNotFoundError, ValueError) as exc:
        print(f"[失败] {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
