"""tests/desktop 的 Tk 护栏入口（实现在 ``tests/_tk_thread_guard.py``）。

这里只负责把目录级 hook 挂进 conftest：``pytest_runtest_teardown`` 在全部
fixture teardown 之后、pytest 主线程里检查是否还有 Tk 顶层/变量存活。
``tests/test_first_run_gui.py`` 用同一模块里的 autouse fixture，实现不复制第二份。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 测试目录不是 Python 包：保证 tests/ 根下的共享模块可导入（prepend 模式下
# pytest 通常已经插入 tests/，这里显式补齐，避免依赖收集顺序）。
_TESTS_DIR = Path(__file__).resolve().parents[1]
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from _tk_thread_guard import pytest_runtest_teardown  # noqa: E402,F401 - pytest 按名字发现 hook
