#!/usr/bin/env python
"""安装副本同源检查：装上去的到底是不是当前源码构建。

为什么需要它
------------
``D:\\LiuHao`` 这类安装目录是某个候选包展开后的副本。源码修好、但没重新出包
或重装时，装上去的 desktop 代码可能仍是旧的 —— 用户看到的现象与仓库里的修复
对不上，验收也会给出与源码混淆的结论。靠人记"记得重新装"不可靠，这里用关键
文件哈希直接回答。

对照范围
--------
* ``src/desktop/*.py``：托盘壳、DPI、窗口尺寸、服务生命周期；
* ``scripts/first_run_gui.py``、``scripts/launch_installed.py``：首启与安装入口；
* 前端 ``assets/index-*.js``：源码取 ``apps/console/console/dist/assets``，
  安装副本取 ``<install>/console/assets``。

用法
----
    python scripts/acceptance/installed_copy_drift.py --install-dir D:\\LiuHao
    python scripts/acceptance/installed_copy_drift.py --install-dir D:\\LiuHao --repo .

只读两侧文件，不改安装目录、不写 ``dist\\``、不运行 ``setup.exe``。

退出码：``0`` 一致；``1`` 存在差异（安装副本落后于源码）；``2`` 参数或目录无效。
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

#: 源码与安装副本的相对路径一致的两个入口脚本。
SCRIPT_FILES = ("scripts/first_run_gui.py", "scripts/launch_installed.py")

#: 前端构建产物的相对位置：源码是 vite 输出目录，安装副本是 payload 的 console。
FRONTEND_SOURCE = Path("apps/console/console/dist/assets")
FRONTEND_INSTALL = Path("console/assets")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _row(target: str, status: str, detail: str) -> dict[str, str]:
    return {"target": target, "status": status, "detail": detail}


def _compare_file(source_root: Path, install_root: Path, rel: str) -> dict[str, str]:
    source = source_root / rel
    installed = install_root / rel
    source_exists = source.is_file()
    installed_exists = installed.is_file()
    if not source_exists and not installed_exists:
        return _row(rel, "diff", "两侧都不存在（对照范围写错或文件已改名）")
    if not source_exists:
        # 源码侧删了、安装侧还在：这是"安装副本多出旧文件"，不是对照写错。
        return _row(rel, "diff", "仅安装有（源码已删除）")
    if not installed_exists:
        return _row(rel, "diff", "仅源码有（安装副本缺失）")
    if _sha256(source) != _sha256(installed):
        return _row(rel, "diff", "哈希不一致")
    return _row(rel, "ok", "哈希一致")


def _compare_desktop(source_root: Path, install_root: Path) -> list[dict[str, str]]:
    source_dir = source_root / "src" / "desktop"
    install_dir = install_root / "src" / "desktop"
    names = {path.name for path in source_dir.glob("*.py")}
    names |= {path.name for path in install_dir.glob("*.py")}
    return [
        _compare_file(source_root, install_root, f"src/desktop/{name}")
        for name in sorted(names)
    ]


def _compare_frontend(source_root: Path, install_root: Path) -> dict[str, str]:
    source_files = sorted((source_root / FRONTEND_SOURCE).glob("index-*.js"))
    install_files = sorted((install_root / FRONTEND_INSTALL).glob("index-*.js"))
    label = "console/assets/index-*.js"
    if not source_files:
        if install_files:
            return _row(label, "diff", "仅安装有（源码缺少前端构建产物，先构建 console）")
        return _row(label, "diff", "两侧都没有前端构建产物（先构建 console）")
    if not install_files:
        return _row(label, "diff", "仅源码有（安装副本缺少前端构建产物）")
    source_map = {path.name: _sha256(path) for path in source_files}
    install_map = {path.name: _sha256(path) for path in install_files}
    if source_map == install_map:
        return _row(label, "ok", "哈希一致")
    return _row(
        label,
        "diff",
        f"源码 {', '.join(source_map)} / 安装 {', '.join(install_map)}",
    )


def _check(source_root: Path, install_root: Path) -> list[dict[str, str]]:
    rows = _compare_desktop(source_root, install_root)
    rows.extend(_compare_file(source_root, install_root, rel) for rel in SCRIPT_FILES)
    rows.append(_compare_frontend(source_root, install_root))
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="安装副本与当前源码的关键文件哈希对照")
    parser.add_argument("--install-dir", required=True, help="安装目录（如 D:\\LiuHao）")
    parser.add_argument("--repo", default=str(REPO), help="源码仓库根（默认当前仓库）")
    args = parser.parse_args(argv)

    install_root = Path(args.install_dir).expanduser().resolve()
    source_root = Path(args.repo).expanduser().resolve()
    if not install_root.is_dir():
        print(f"[FAIL] 安装目录不存在：{install_root}", file=sys.stderr)
        return 2
    if not source_root.is_dir():
        print(f"[FAIL] 源码目录不存在：{source_root}", file=sys.stderr)
        return 2

    rows = _check(source_root, install_root)
    print(f"对照源码：{source_root}")
    print(f"安装副本：{install_root}")
    print()
    for row in rows:
        print(f"[{' OK ' if row['status'] == 'ok' else 'DIFF'}]  {row['target']:<42} {row['detail']}")

    diffs = [row for row in rows if row["status"] != "ok"]
    print()
    if diffs:
        print(f"结论：安装副本落后于源码（{len(diffs)} 项差异），不是当前源码构建")
        return 1
    print(f"结论：安装副本与当前源码一致（{len(rows)} 项关键文件哈希全部匹配）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
