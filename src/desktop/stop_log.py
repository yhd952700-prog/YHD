"""网关停止归因：一条事件说清"谁让网关停的"。

桌面壳按钮、托盘菜单与外部 ``python -m src.desktop stop`` 共用同一格式，
事后只查 ``logs/desktop.log`` 就能区分三者，不必再猜。
只写来源、调用进程 PID、时间与 graceful/forced，不写任何凭据。
"""

from __future__ import annotations

import time
from pathlib import Path

#: 唯一事件名；测试与其他日志检索都按它匹配。
STOP_EVENT = "evt=gateway.stop"

#: 与 desktop.log 的 formatter 保持一致，外部写入的行看起来是同一种日志。
_STAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


def describe_stop_attribution(
    source: str,
    *,
    caller_pid: int | None = None,
    graceful: bool | None = None,
    forced: bool | None = None,
    at: float | None = None,
) -> str:
    """生成归因字段串：``source`` / ``caller_pid`` / 时间 / ``graceful`` / ``forced``。

    PID 拿不到时写 ``unknown(caller_pid_unavailable)``，把原因一并写出来，
    不留下一个无法解释的空值。
    """
    pid = "unknown(caller_pid_unavailable)" if caller_pid is None else str(int(caller_pid))
    moment = time.strftime(
        "%Y-%m-%dT%H:%M:%S",
        time.localtime(at if at is not None else time.time()),
    )

    def flag(value: bool | None) -> str:
        if value is None:
            return "unknown"
        return "True" if value else "False"

    return (
        f"{STOP_EVENT} source={source or 'unknown'} caller_pid={pid} at={moment} "
        f"graceful={flag(graceful)} forced={flag(forced)}"
    )


def append_stop_attribution(
    log_path: Path,
    source: str,
    *,
    caller_pid: int | None = None,
    graceful: bool | None = None,
    forced: bool | None = None,
) -> bool:
    """外部停止进程把归因行追加进桌面日志。

    写失败不抛异常：停止/卸载流程不能因为日志目录不可写而中断，
    返回值只用于调用方决定是否降级。
    """
    line = describe_stop_attribution(
        source,
        caller_pid=caller_pid,
        graceful=graceful,
        forced=forced,
    )
    stamp = time.strftime(_STAMP_FORMAT)
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} [INFO] src.desktop.stop: {line}\n")
        return True
    except OSError:
        return False
