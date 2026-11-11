"""AI 写升级请求的薄封装；Broker/Boss 读取后仍需手动授权。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def main() -> int:
    return subprocess.call(
        [sys.executable, "-m", "src.selfupgrade", "request", *sys.argv[1:]],
        cwd=str(REPO),
    )


if __name__ == "__main__":
    sys.exit(main())
