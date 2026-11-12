"""启动门：只认网关 ``GET /v1/startup`` 的 state，把"连接失败"当成 starting。

为什么需要它
------------
网关在导入 FastAPI / 路由 / 跑完 lifespan 启动之后才绑定端口（见
``docs/startup-profile.md``）。在这之前浏览器拿到的是**连接失败**，不是 503。
桌面壳若在此时把浏览器叫起来，用户第一眼就是一张错误页。

这里的判定口径：

* 连接失败 / 非 JSON / 还没端口 → ``starting``（继续等，不叫浏览器）；
* ``state="starting"`` → 继续等；
* ``state="ready"`` → 进控制台；
* ``state="degraded"`` → **也进**（核心可用），并把 ``background.errors``
  原样带出来，由界面显示真实原因，不许吞成"启动失败"；
* 到点仍未就绪 → ``timeout``，附带人话提示（日志与诊断包命令），不无限转圈；
* 网关进程中途退出 → ``exited``，不假装还在启动。

旧版网关没有 ``/v1/startup``（404）时退回 ``/v1/health`` 判定，避免把用户
卡在门口。

探测间隔从 250ms 起、每次翻倍、上限 500ms（对启动这种短窗口既灵敏又不空转）。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .probe import http_json

STARTUP_PATH = "/v1/startup"
HEALTH_PATH = "/v1/health"

#: 启动门自身等待上限（秒）。``GatewayService.start()`` 已经等 ``/v1/health``，
#: 到这里网关通常已就绪；这个上限只防"端口通了但状态一直不 ready"的异常。
STARTUP_TIMEOUT = 60.0

MIN_INTERVAL = 0.25
MAX_INTERVAL = 0.50

#: 走进控制台的状态（degraded 也进：核心可用，不能让用户卡在门口）。
ENTER_STATES = ("ready", "degraded")

DIAGNOSE_HINT = "python -m src.desktop diagnose --verify"


def _base(base_url: str) -> str:
    # 不写死 8080 兜底：调用方没给地址时宁可按连接失败处理，也不能去探测
    # 端口上可能属于别的程序的页面。
    return (base_url or "").rstrip("/")


def probe_startup(base_url: str, timeout: float = 2.0) -> dict[str, Any]:
    """探测一次启动状态；连接失败按 ``starting`` 处理（不抛异常）。"""
    info = http_json(f"{_base(base_url)}{STARTUP_PATH}", timeout=timeout)
    body = info.get("body") if info.get("ok") else None
    if isinstance(body, dict) and body.get("state") in {"starting", "ready", "degraded"}:
        return {"reachable": True, "state": str(body["state"]), "body": body, "error": None, "legacy": False}
    if info.get("status") == 404:
        # 旧版网关没有这个端点：退回 /v1/health，但必须核对身份 ——
        # 端口上别人的服务也会对 /v1/health 回 200，不能因此把用户放进去。
        health = http_json(f"{_base(base_url)}{HEALTH_PATH}", timeout=timeout)
        body = health.get("body")
        if (
            health.get("ok")
            and isinstance(body, dict)
            and body.get("service") == "liuhao-gateway"
        ):
            return {"reachable": True, "state": "ready", "body": None, "error": None, "legacy": True}
        return {
            "reachable": False,
            "state": "starting",
            "body": None,
            "error": health.get("error"),
            "legacy": True,
        }
    return {
        "reachable": False,
        "state": "starting",
        "body": None,
        "error": info.get("error"),
        "legacy": False,
    }


def _errors_text(errors: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in errors:
        step = str(item.get("step") or "未知步骤").strip()
        error = str(item.get("error") or "").strip()
        parts.append(f"{step}：{error}" if error else step)
    return "；".join(parts)


def _log_hint(log_dir: Path | str | None, hint: str | None = None) -> str:
    # 不同壳的日志文件名不同（桌面壳是 desktop*.log，安装版启动器是
    # liuhao.log/error.log），所以允许调用方自带提示，避免指到不存在的文件。
    if hint:
        return hint
    if log_dir:
        base = str(log_dir).rstrip("/\\")
        return f"{base}/desktop.log、{base}/desktop-gateway.log"
    return "logs/desktop.log、logs/desktop-gateway.log"


def describe_startup(result: dict[str, Any]) -> str:
    """把启动门结果翻译成用户能看懂的一句话（含可执行的下一步）。"""
    state = result.get("state")
    elapsed = result.get("elapsed", 0.0)
    hint = _log_hint(result.get("log_dir"), result.get("log_hint") or None)
    diag = result.get("diagnose_hint") or DIAGNOSE_HINT
    if state == "ready":
        text = f"已就绪（{elapsed}s）"
        if result.get("legacy"):
            text += "（旧版网关无 /v1/startup，按 /v1/health 判定）"
        return text
    if state == "degraded":
        errors = _errors_text(list(result.get("errors") or []))
        return (
            f"核心可用，但后台组件未就绪（{elapsed}s）：{errors or '原因未上报'}"
            f"。日志：{hint}；诊断包：{diag}"
        )
    if state == "timeout":
        stage = result.get("last_stage") or "未知"
        waited = float(result.get("timeout") or 0.0)
        return (
            f"等待 {waited:.0f}s 仍未就绪（最后阶段：{stage}）"
            f"。日志：{hint}；诊断包：{diag}"
        )
    if state == "exited":
        return f"网关进程在启动过程中退出。日志：{hint}；诊断包：{diag}"
    return str(result.get("detail") or "启动未完成")


def _last_stage(snapshot: dict[str, Any]) -> str:
    body = snapshot.get("body")
    if not isinstance(body, dict):
        return ""
    stages = body.get("stages")
    if not isinstance(stages, list) or not stages:
        return ""
    last = stages[-1]
    return str(last.get("name") or "") if isinstance(last, dict) else ""


def _build_result(
    snapshot: dict[str, Any],
    *,
    state: str,
    elapsed: float,
    polls: int,
    timeout: float,
    log_dir: Path | str | None,
    log_hint: str | None = None,
    diagnose_hint: str | None = None,
) -> dict[str, Any]:
    body = snapshot.get("body")
    if not isinstance(body, dict):
        body = {}
    background = body.get("background")
    if not isinstance(background, dict):
        background = {}
    errors = [item for item in (background.get("errors") or []) if isinstance(item, dict)]
    result: dict[str, Any] = {
        "state": state,
        "elapsed": elapsed,
        "polls": polls,
        "timeout": timeout,
        "reachable": bool(snapshot.get("reachable")),
        "legacy": bool(snapshot.get("legacy")),
        "errors": errors,
        "background": background,
        "last_stage": _last_stage(snapshot),
        "console_ok": state in ENTER_STATES,
        "log_dir": str(log_dir) if log_dir else "",
        "log_hint": log_hint or "",
        "diagnose_hint": diagnose_hint or "",
    }
    result["detail"] = describe_startup(result)
    return result


def unavailable_gate(
    reason: str | None,
    *,
    log_dir: Path | str | None = None,
    log_hint: str | None = None,
    diagnose_hint: str | None = None,
) -> dict[str, Any]:
    """网关根本没起来（``start()`` 返回 False）时的统一结果。"""
    hint = _log_hint(log_dir, log_hint)
    diag = diagnose_hint or DIAGNOSE_HINT
    # 调用方给的原因可能自带句号（端口类提示都带），拼接前去掉，避免窗口里
    # 出现"指定。。日志："这种双标点。
    reason_text = (reason or "网关未启动").rstrip("。.；;，, ")
    detail = f"{reason_text}。日志：{hint}；诊断包：{diag}"
    return {
        "state": "failed",
        "elapsed": 0.0,
        "polls": 0,
        "timeout": 0.0,
        "reachable": False,
        "legacy": False,
        "errors": [],
        "background": {},
        "last_stage": "",
        "console_ok": False,
        "log_dir": str(log_dir) if log_dir else "",
        "log_hint": log_hint or "",
        "diagnose_hint": diagnose_hint or "",
        "detail": detail,
    }


def wait_until_ready(
    base_url: str,
    *,
    timeout: float = STARTUP_TIMEOUT,
    log_dir: Path | str | None = None,
    log_hint: str | None = None,
    diagnose_hint: str | None = None,
    is_alive: Callable[[], bool] | None = None,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    min_interval: float = MIN_INTERVAL,
    max_interval: float = MAX_INTERVAL,
) -> dict[str, Any]:
    """轮询 ``/v1/startup`` 直到 ready / degraded / 超时 / 进程退出。

    ``min_interval`` / ``max_interval`` 供"双击后要尽快看见登录框"的启动器把
    就绪探测从 250/500ms 收紧到 100/200ms；默认值保持不变，桌面壳与既有调用
    方的行为不受影响。
    """
    started_at = clock()
    interval = max(0.01, min_interval)
    polls = 0
    snapshot: dict[str, Any] = {
        "reachable": False,
        "state": "starting",
        "body": None,
        "error": "尚未探测",
        "legacy": False,
    }
    while True:
        polls += 1
        snapshot = probe_startup(base_url)
        elapsed = round(clock() - started_at, 2)
        if on_progress is not None:
            on_progress(
                {
                    "state": snapshot["state"],
                    "elapsed": elapsed,
                    "reachable": snapshot["reachable"],
                    "last_stage": _last_stage(snapshot),
                }
            )
        if snapshot["state"] in ENTER_STATES:
            # 就绪响应到了不代表进程还活着：网关可能在返回后立刻崩溃。
            # 先确认进程，再报"已就绪"，避免把用户送进一个正在死的页面。
            if is_alive is not None and not is_alive():
                return _build_result(
                    snapshot,
                    state="exited",
                    elapsed=elapsed,
                    polls=polls,
                    timeout=timeout,
                    log_dir=log_dir,
                    log_hint=log_hint,
                    diagnose_hint=diagnose_hint,
                )
            return _build_result(
                snapshot,
                state=snapshot["state"],
                elapsed=elapsed,
                polls=polls,
                timeout=timeout,
                log_dir=log_dir,
                log_hint=log_hint,
                diagnose_hint=diagnose_hint,
            )
        if is_alive is not None and not is_alive():
            return _build_result(
                snapshot,
                state="exited",
                elapsed=elapsed,
                polls=polls,
                timeout=timeout,
                log_dir=log_dir,
                log_hint=log_hint,
                diagnose_hint=diagnose_hint,
            )
        if clock() - started_at >= timeout:
            return _build_result(
                snapshot,
                state="timeout",
                elapsed=elapsed,
                polls=polls,
                timeout=timeout,
                log_dir=log_dir,
                log_hint=log_hint,
                diagnose_hint=diagnose_hint,
            )
        sleep(interval)
        interval = min(max(0.01, max_interval), interval * 2)


__all__ = [
    "ENTER_STATES",
    "STARTUP_TIMEOUT",
    "describe_startup",
    "probe_startup",
    "unavailable_gate",
    "wait_until_ready",
]
