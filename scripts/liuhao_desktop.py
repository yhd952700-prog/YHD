"""自启入口：供 ``HKCU\\...\\Run`` 用 ``pythonw.exe`` 调用。

注册表启动项的当前目录不可预期，因此这里显式把仓库根加入 ``sys.path``，
再交给 ``src.desktop`` 的命令行入口；``--autostart`` 表示只驻留托盘、
不弹窗口、不自动开浏览器。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.desktop.__main__ import main  # noqa: E402, I001


if __name__ == "__main__":
    raise SystemExit(main())
