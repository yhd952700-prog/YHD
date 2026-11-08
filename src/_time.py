"""UTC time helper — the single, deprecation-free source of "now".

Why **naive** (``tzinfo=None``) and not timezone-aware
-----------------------------------------------------
``datetime.utcnow()`` is deprecated from Python 3.12 onwards, but it returns a
*naive* datetime, and a naive UTC value is what this codebase stores,
serialises and compares:

* SQLAlchemy ``Column(DateTime, default=...)`` columns are naive. Several
  backends reject an aware value outright and others silently mangle it.
* ``.isoformat()`` on an aware datetime appends ``+00:00``, which would change
  the on-the-wire / on-disk format of audit records, events and API payloads.
* Persisted rows and existing fixtures are naive; comparing aware with naive
  raises ``TypeError``.
* ``.timestamp()`` on a naive datetime interprets it as *local* time, so
  silently switching to aware would also silently change epoch conversions.

Therefore ``utc_now()`` is a **drop-in, value-identical** replacement for
``datetime.utcnow()``: same instant, same naivety, no ``DeprecationWarning``.

Migrating the codebase to timezone-aware datetimes is a deliberate, separate
piece of work (schema + wire format + persisted rows + fixtures). When that is
decided, it is changed **here and nowhere else** — which is the entire point of
funnelling every "now" through this module.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone

#: 系统墙钟与单调时钟偏差超过这个阈值，就认定为「时间跳变」（手动改时间、
#: 虚拟机恢复快照、NTP 大幅校正），此后按单调时钟推进，不再跟随墙钟。
CLOCK_JUMP_THRESHOLD_SECONDS = float(
    os.environ.get("LIUHAO_CLOCK_JUMP_THRESHOLD_SECONDS", "60")
)

_anchor_lock = threading.Lock()
_anchor_wall = time.time()
_anchor_mono = time.monotonic()


def _reanchor(wall: float, mono: float) -> None:
    global _anchor_wall, _anchor_mono
    with _anchor_lock:
        _anchor_wall, _anchor_mono = wall, mono


def stable_now_epoch() -> float:
    """A wall clock that **ignores large jumps**, for security-sensitive time.

    为什么需要它
    -------------
    ``time.time()`` 直接跟随系统时钟。用户手动改时间、笔记本从睡眠恢复时
    BIOS/NTP 一次性校正数小时、虚拟机回滚快照，都会让它突变：

    * 向前跳 → 已签发的 JWT 瞬间"过期"，所有人被踢下线；
    * 向后跳 → 吊销表按 ``expires_at`` 清理时把仍在有效期内的记录误删。

    这里把「已流逝时间」钉在 ``time.monotonic()`` 上（它不受墙钟调整影响），
    每次调用与墙钟对比：偏差在阈值内就跟随墙钟（正常走时、小幅校时），
    超过阈值就继续用单调推算值，并保留旧锚点，直到进程重启。

    边界（如实说明）：进程重启后锚点会重置为新的墙钟。即"跳变 + 重启"这一
    组合会让跳变后的墙钟重新生效；这与"用户真的等了这么久"在行为上等价，
    不属于本模块要消除的偶发误伤。
    """
    wall = time.time()
    mono = time.monotonic()
    with _anchor_lock:
        projected = _anchor_wall + (mono - _anchor_mono)
    if abs(wall - projected) <= CLOCK_JUMP_THRESHOLD_SECONDS:
        # 正常走时（或很小的校时）：以墙钟为准并更新锚点。
        _reanchor(wall, mono)
        return wall
    # 跳变：保持单调推算值，锚点不动，后续调用继续忽略这次跳变。
    return projected


def utc_now() -> datetime:
    """Return the current UTC time as a **naive** datetime.

    Exactly equivalent to the deprecated ``datetime.utcnow()`` (same value,
    same ``tzinfo is None``), so it is safe as a drop-in replacement.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def stable_utc_now() -> datetime:
    """``utc_now()`` 的防跳变版本：忽略大幅墙钟改动，按单调时钟推进。

    用于"时间回拨不能让已过期的东西复活、前跳不能误删证据"这类安全敏感
    判断（记忆 TTL、审计保留期）。库里的 ``created_at`` / ``expires_at``
    仍是普通 naive UTC 时间戳，格式不变。
    """
    return datetime.fromtimestamp(stable_now_epoch(), timezone.utc).replace(tzinfo=None)


__all__ = [
    "CLOCK_JUMP_THRESHOLD_SECONDS",
    "stable_now_epoch",
    "stable_utc_now",
    "utc_now",
]
