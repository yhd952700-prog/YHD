#!/usr/bin/env python
"""卸载前递归清理程序目录里的 ``__pycache__``（安装器 [UninstallRun] 调用）。

为什么用随包 Python 而不是 PowerShell/cmd
----------------------------------------
Inno 的 [UninstallRun] 只能把 ``{app}`` 直接套进命令行。路径里一旦出现
单引号（例如 ``D:\\O'Brien 贾维斯\\app``），PowerShell 的 ``'...'`` 字面量就会
被提前闭合；``&``、``^``、``(``、``)`` 在 cmd/PowerShell 里也有各自的解释
规则。用 ``runtime\\python.exe`` 直接接收 argv（Inno 走 CreateProcess，不经过
shell），任何合法 Windows 路径都不会被当成语法。

用法：``python purge_pycache.py --root "<程序目录>"``；省略 --root 时默认
从本脚本位置向上两级（程序目录）。
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parent.parent
    args = argv[1:]
    if "--root" in args:
        index = args.index("--root")
        if index + 1 < len(args):
            root = Path(args[index + 1])
    root = root.expanduser().resolve()
    removed = 0
    try:
        for entry in root.rglob("__pycache__"):
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
    except OSError:
        pass
    print(f"[purge_pycache] removed {removed} director(ies) under {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
