"""启动阶段计时（可复现的冷启动分解）。

为什么需要它
------------
安装版实测"双击快捷方式 → ``/v1/health`` 可用"要 14~15 秒。仅凭
``python -X importtime`` 只能看到"导入耗时"，无法回答用户真正的问题：
**哪个阶段花了多少秒**。本模块在进程内记录时间戳打点，把启动拆成可命名的
阶段（包导入 / 依赖导入 / 配置加载 / 密钥与身份 / 路由注册 / lifespan 各段 /
后台初始化），并在开启时输出一张表。

怎么开启
--------
* ``LIUHAO_STARTUP_PROFILE=1``（或 ``true`` / ``yes`` / ``on``）
* 命令行 ``python -m src.gateway --profile-startup``

开启后，启动末尾会向 stdout 与日志输出阶段表；设置
``LIUHAO_STARTUP_PROFILE_OUT=<path>`` 还能落一份 JSON（便于 CI / 对比）。

没有开启时也照常记录（只是几十次 list.append，成本可忽略），因为
``GET /v1/startup`` 需要随时能读到真实的阶段信息，而不是等运维先开开关。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

#: 进程内最早可得的时钟基准。``src.gateway.__init__`` 在导入任何重依赖之前
#: 会先导入本模块，因此 T0 ≈ 解释器完成启动 + 进入本包的时刻。
T0 = time.perf_counter()

logger = logging.getLogger("liuhao.startup")

_LOCK = threading.Lock()
_MARKS: List[Dict[str, Any]] = []
_LAST: float = T0

#: 后台（不阻塞就绪）初始化的真实状态，供 /v1/startup 与日志使用。
_BACKGROUND: Dict[str, Any] = {
    "status": "pending",  # pending | running | done | failed
    "started_at": None,
    "finished_at": None,
    "errors": [],
    "steps": [],
}

_ENABLED = False
_READY = False
_RUNS = 0
_BASELINE: List[Dict[str, Any]] = []


def begin_run() -> None:
    """标记一次 lifespan 启动开始（同一进程内可能多次：测试会反复建 app）。

    首次启动保留"模块导入阶段"的打点（它们发生在 lifespan 之前）；后续启动
    回到这份基线，避免阶段表与后台错误在不同 app 实例之间串味——否则上一次
    的失败会让下一次的 ``/v1/startup`` 永远显示 ``degraded``。
    """
    global _READY, _RUNS, _LAST
    with _LOCK:
        _RUNS += 1
        if _RUNS == 1:
            _BASELINE.extend(_MARKS)
        else:
            _MARKS[:] = list(_BASELINE)
        _READY = False
        _LAST = time.perf_counter()
        _BACKGROUND.update(
            status="pending",
            started_at=None,
            finished_at=None,
            errors=[],
            steps=[],
        )


def _truthy(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def enable_from_env() -> None:
    """按 ``LIUHAO_STARTUP_PROFILE`` 决定是否打印阶段表。"""
    global _ENABLED
    if _truthy(os.environ.get("LIUHAO_STARTUP_PROFILE")):
        _ENABLED = True


def enable_from_argv(argv: Optional[List[str]] = None) -> None:
    """支持 ``--profile-startup``（它是本进程的开关，不会传给 uvicorn）。"""
    global _ENABLED
    args = list(argv if argv is not None else [])
    if "--profile-startup" in args:
        _ENABLED = True


def enabled() -> bool:
    return _ENABLED


def mark(name: str, **extra: Any) -> None:
    """记录一个阶段结束点（相对于 T0 的秒数 + 与上一个打点的差）。"""
    global _LAST
    now = time.perf_counter()
    with _LOCK:
        delta = now - _LAST
        _LAST = now
        entry = {
            "name": name,
            "elapsed_ms": round((now - T0) * 1000, 1),
            "delta_ms": round(delta * 1000, 1),
        }
        if extra:
            entry.update(extra)
        _MARKS.append(entry)
    if _ENABLED:
        # 走到这里说明用户要的是"看得见的阶段"，所以这里用 INFO；
        # logging.yaml 里 root=INFO 会同时进控制台（只 WARNING+）与日志文件。
        logger.info(
            "[startup] %7.1f ms (+%6.1f ms) %s",
            entry["elapsed_ms"], entry["delta_ms"], name,
        )


def elapsed_ms() -> float:
    return round((time.perf_counter() - T0) * 1000, 1)


def mark_ready() -> None:
    """核心就绪（登录 / 对话 / 模型配置可用），可开始接流量。"""
    global _READY
    _READY = True
    mark("就绪：开始接受请求（/v1/health 可返回 200）")


def ready() -> bool:
    return _READY


def background_started() -> None:
    with _LOCK:
        _BACKGROUND["status"] = "running"
        _BACKGROUND["started_at"] = round((time.perf_counter() - T0) * 1000, 1)


def background_step(name: str, ok: bool, elapsed_ms: float, error: str = "") -> None:
    with _LOCK:
        _BACKGROUND["steps"].append(
            {
                "name": name,
                "ok": ok,
                "elapsed_ms": round(elapsed_ms, 1),
                "error": error,
            }
        )
        if not ok:
            _BACKGROUND["errors"].append({"step": name, "error": error})


def background_finished() -> None:
    with _LOCK:
        _BACKGROUND["finished_at"] = round((time.perf_counter() - T0) * 1000, 1)
        _BACKGROUND["status"] = "failed" if _BACKGROUND["errors"] else "done"


def background_state() -> Dict[str, Any]:
    with _LOCK:
        return {
            "status": _BACKGROUND["status"],
            "started_at_ms": _BACKGROUND["started_at"],
            "finished_at_ms": _BACKGROUND["finished_at"],
            "errors": list(_BACKGROUND["errors"]),
            "steps": [dict(step) for step in _BACKGROUND["steps"]],
        }


def stages() -> List[Dict[str, Any]]:
    with _LOCK:
        return [dict(entry) for entry in _MARKS]


def snapshot() -> Dict[str, Any]:
    """``GET /v1/startup`` 的响应体（只读、诚实：未完成的阶段不会伪装完成）。"""
    return {
        "state": "ready" if _READY else "starting",
        "elapsed_ms": elapsed_ms(),
        "started_at_unix": round(time.time() - (time.perf_counter() - T0), 3),
        "stages": stages(),
        "background": background_state(),
    }


def report(path: Optional[str] = None) -> str:
    """返回（并在开启时输出）阶段表；``path`` 或 env 可落 JSON。"""
    rows = stages()
    lines = [
        "启动阶段耗时分解（T0 = 进入 src.gateway 包）：",
        f"{'阶段':<48}{'累计(ms)':>12}{'本段(ms)':>12}",
    ]
    for row in rows:
        lines.append(
            f"{row['name']:<48}{row['elapsed_ms']:>12.1f}{row['delta_ms']:>12.1f}"
        )
    lines.append(f"{'【总计时】':<48}{elapsed_ms():>12.1f}")
    text = "\n".join(lines)

    if _ENABLED:
        for line in text.splitlines():
            logger.info("[startup] %s", line)
        # logging.yaml 的控制台 handler 只放 WARNING+；显式开启剖析时用户就是
        # 想在终端立刻看到这张表，因此额外直接打印一份（flush 保证顺序）。
        print(text, flush=True)

    out = path or os.environ.get("LIUHAO_STARTUP_PROFILE_OUT")
    if out:
        try:
            payload = snapshot()
            payload["report"] = text
            with open(out, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
        except Exception as exc:  # noqa: BLE001 - 采样失败不能影响启动
            logger.warning("无法写出启动阶段 JSON（%s）：%s", out, exc)
    return text
