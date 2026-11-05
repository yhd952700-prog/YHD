"""命令行入口：``python -m src.reminders list|failures|history [--json]``。

实现放在 :mod:`src.tasks.reminders_cli`；这里只做一行转发，保证用户要求的
命令形式（``python -m src.reminders``）可以直接使用。
"""

from src.tasks.reminders_cli import main

if __name__ == "__main__":
    raise SystemExit(main())
