"""把系统时钟回拨 2 小时后，在同一进程内再读一次已过期的记忆。

父测试用 ``LIUHAO_ALLOW_CLOCK_CHANGE_TESTS=1`` 显式开启，并在子进程异常
退出时兜底把时钟调回。期望：TTL 判断不因墙钟回拨而复活过期条目。
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import time

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


def _set_date(value: datetime.datetime) -> None:
    subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"Set-Date -Date '{value:%Y-%m-%d %H:%M:%S}'",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )


def main() -> int:
    from src.kernels.memory import MemoryKernel, MemoryTier

    kernel = MemoryKernel(db_path=sys.argv[1])
    # 5 秒前就应过期。
    kernel.store(
        "expired",
        "secret",
        tier=MemoryTier.PERSISTENT,
        ttl=datetime.timedelta(seconds=-5),
    )
    before = kernel.recall("expired")

    original = datetime.datetime.now()
    mono_start = time.monotonic()
    after = None
    try:
        _set_date(original - datetime.timedelta(hours=2))
        time.sleep(0.2)
        after = kernel.recall("expired")
    finally:
        restore = original + datetime.timedelta(seconds=time.monotonic() - mono_start)
        _set_date(restore)

    print(
        json.dumps(
            {"before_visible": before is not None, "after_rollback_visible": after is not None},
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
