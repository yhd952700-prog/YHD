"""``python -m src.cli`` 入口：让备份/迁移命令可以直接在终端里跑。"""
from src.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
