"""UBX-005 接线验证用的独立子进程工人。

让"两个进程"是真的两个 OS 进程（不是线程），以证明跨进程的围栏不变量：
max_executors=1 时，一个进程持租约期间，另一个进程无法获取有效租约。

由 tests/distribution/test_ubx005_production_wiring.py 通过 subprocess 调用，
传入一个 JSON argv[1] 描述（mode / dir / env / 信号文件）。
"""

from __future__ import annotations

import json
import os
import sys
import time


def main() -> int:
    payload = json.loads(sys.argv[1])
    repo_root = payload["repo_root"]
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    for key, value in payload.get("env", {}).items():
        os.environ[key] = value

    from src.kernels.execution.fence import (
        attach_default_executor_fence,
        executor_session,
        ExecutorFenceDenied,
    )

    mode = payload["mode"]
    lease_dir = payload["dir"]
    max_executors = payload.get("max_executors", 1)

    # 安装生产围栏（file 后端，持久目录），与父进程共享同一后端目录。
    attach_default_executor_fence(
        backend="file", lease_dir=lease_dir, max_executors=max_executors
    )

    if mode == "hold":
        # 获取并持有租约（executor_session 退出只解绑、不释放，进程活着即持有）。
        try:
            with executor_session(
                action="ubx005.hold", capabilities=(), ttl_sec=60.0
            ):
                # 通知父进程：已持有有效租约。
                with open(payload["ready"], "w") as fh:
                    fh.write("ready")
                time.sleep(payload.get("hold_for", 40.0))
        except ExecutorFenceDenied as exc:
            with open(payload["denied"], "w") as fh:
                fh.write(f"denied-while-holding: {exc}")
        return 0

    if mode == "try":
        # 尝试获取租约；被 cap 拒绝即写出 denied 信号文件。
        try:
            with executor_session(
                action="ubx005.try", capabilities=(), ttl_sec=60.0
            ):
                with open(payload["ok"], "w") as fh:
                    fh.write("ok")
        except ExecutorFenceDenied as exc:
            with open(payload["denied"], "w") as fh:
                fh.write(f"denied: {exc}")
        return 0

    raise SystemExit(f"unknown mode: {mode!r}")


if __name__ == "__main__":
    raise SystemExit(main())
