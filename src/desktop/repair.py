"""六类故障的一键修复动作：幂等、可回退、每次都留证据与日志。

设计约束（与排障线同一套要求）
----------------------------
  * **不做"弹个提示"**：每个动作都真实改本机状态（换端口、改目录、移动损坏库、
    复制静态资源），并返回可核对的证据（改了什么、改完自检是什么结果）。
  * **幂等**：同一动作重复执行不会叠加破坏 —— 端口已换过就停在同一个空闲端口，
    目录已指向目标就不再改动，库已完好就不动文件，资源已补齐就不复制。
  * **可回退**：结果里带 ``revert`` 文案与 ``revert_action``（可调用对象），
    界面据此给出「回退这次操作」；回退本身也写日志。
  * **有日志**：每条动作按步骤写入 ``<日志目录>/repair.log``（轮转 5 MiB × 2），
    写前统一脱敏（``src.common.redaction.redact_text``），不会把用户名/Key 落盘。
  * **不假装**：拿不到事实就如实返回 ``ok=False`` 与中文原因，不写"已修复"。

本模块只操作本机，不导入网关业务代码；唯一的外部依赖是
``GatewayService``（由调用方传入，便于测试注入桩对象）。
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import socket
import sqlite3
import ssl
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..common.redaction import mask_user_path, redact_text
from ..logging_config import resolve_data_dir
from . import LOG_DIR, REPO_ROOT
from .diagnostics import _read_providers, probe_directory_writable
from .probe import http_json, port_is_free

logger = logging.getLogger(__name__)

#: 用户选择的补丁会写在这里；``LIUHAO_DESKTOP_CONFIG`` 供演练/测试隔离。
CONFIG_ENV = "LIUHAO_DESKTOP_CONFIG"
DATA_DIR_ENV = "LIUHAO_DATA_DIR"
LOG_DIR_ENV = "LIUHAO_LOG_DIR"
CONSOLE_DIST_ENV = "LIUHAO_CONSOLE_DIST"

REPAIR_LOG_NAME = "repair.log"
REPAIR_LOG_MAX_BYTES = 5 * 1024 * 1024
REPAIR_LOG_BACKUPS = 2

#: 一条修复动作在日志里的统一字段顺序（人读日志时不用满屏找）。
_LOG_LOCK = threading.Lock()

_SQLITE_MAGIC = b"SQLite format 3\x00"
_SQLITE_SIDECARS = ("-wal", "-shm", "-journal")
_MAX_DB_WALK = 2000
_MAX_DB_BYTES = 512 * 1024 * 1024

#: 端口换绑时默认等待 ``/v1/health`` 的上限（秒）。冷启动实测通常 5-15s。
START_TIMEOUT = 90.0


# ---------------------------------------------------------------------- #
# 日志（脱敏 + 轮转，不依赖全局 logging 配置）
# ---------------------------------------------------------------------- #
def repair_log_path(log_dir: str | Path | None = None) -> Path:
    """``repair.log`` 的位置：显式目录 > ``LIUHAO_LOG_DIR`` > 桌面壳默认日志目录。"""
    base = log_dir or os.environ.get(LOG_DIR_ENV) or LOG_DIR
    return Path(base) / REPAIR_LOG_NAME


def _rotate_log(path: Path) -> None:
    try:
        if path.stat().st_size < REPAIR_LOG_MAX_BYTES:
            return
    except OSError:
        return
    for index in range(REPAIR_LOG_BACKUPS - 1, 0, -1):
        source = path.with_name(f"{path.name}.{index}")
        if source.exists():
            with contextlib.suppress(OSError):
                source.replace(path.with_name(f"{path.name}.{index + 1}"))
    with contextlib.suppress(OSError):
        path.replace(path.with_name(f"{path.name}.1"))


def _log(action: str, step: str, log_dir: str | Path | None = None, **fields: Any) -> None:
    """写一行 ``repair.log``。字段值统一脱敏；写失败不影响修复动作本身。"""
    path = repair_log_path(log_dir)
    detail = ""
    if fields:
        try:
            detail = " " + json.dumps(fields, ensure_ascii=False, default=str, sort_keys=True)
        except (TypeError, ValueError):
            detail = f" {fields}"
    line = f"{datetime.now().isoformat(timespec='seconds')} {action}.{step}{detail}"
    line = redact_text(line).replace("\r", " ").replace("\n", " ")
    with _LOG_LOCK:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            _rotate_log(path)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            logger.warning("repair.log 写入失败（%s），动作本身继续", mask_user_path(str(path)))


def _result(
    action: str,
    *,
    ok: bool,
    changed: bool,
    summary: str,
    evidence: list[dict[str, Any]] | None = None,
    revert: str | None = None,
    revert_action: Any = None,
    log_dir: str | Path | None = None,
    mode: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": action,
        "ok": bool(ok),
        "changed": bool(changed),
        "summary": summary,
        "evidence": [item for item in (evidence or []) if item],
        "revert": revert,
        "revert_action": revert_action,
        "log": mask_user_path(str(repair_log_path(log_dir))),
    }
    if mode:
        payload["mode"] = mode
    _log(
        action,
        "result",
        log_dir=log_dir,
        ok=payload["ok"],
        changed=payload["changed"],
        summary=summary,
    )
    return payload


def _evidence(label: str, value: Any) -> dict[str, Any]:
    if isinstance(value, Path):
        value = mask_user_path(str(value))
    elif isinstance(value, str):
        value = mask_user_path(value)
    if isinstance(value, dict):
        value = {key: (mask_user_path(str(item)) if isinstance(item, Path) else item) for key, item in value.items()}
    return {"label": label, "value": value}


def _err(exc: BaseException) -> str:
    """异常只留类型与错误码：原文常带绝对路径（含用户名）。"""
    parts = [type(exc).__name__]
    winerror = getattr(exc, "winerror", None)
    errno = getattr(exc, "errno", None)
    if winerror:
        parts.append(f"winerror={winerror}")
    elif errno:
        parts.append(f"errno={errno}")
    return " ".join(parts)


# ---------------------------------------------------------------------- #
# 配置：数据目录的持久化与启动时应用
# ---------------------------------------------------------------------- #
def desktop_config_path() -> Path:
    """桌面壳的用户级配置（默认 ``%LOCALAPPDATA%\\LiuHao\\desktop.json``）。"""
    override = (os.environ.get(CONFIG_ENV) or "").strip()
    if override:
        return Path(override).expanduser()
    local = (os.environ.get("LOCALAPPDATA") or "").strip()
    base = Path(local) if local else Path.home()
    return base / "LiuHao" / "desktop.json"


def _read_config() -> dict[str, Any]:
    path = desktop_config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_config(data: dict[str, Any]) -> Path:
    path = desktop_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def saved_data_dir() -> Path | None:
    raw = str(_read_config().get("data_dir") or "").strip()
    return Path(raw).expanduser() if raw else None


def apply_saved_data_dir(log_dir: str | Path | None = None) -> dict[str, Any]:
    """启动时应用用户改过的数据目录。

    显式环境变量优先：``LIUHAO_DATA_DIR`` 已被调用方设置（安装版启动器、演练脚本）
    时不再覆盖，避免"修复动作把别人的隔离实例改回真实目录"。
    保存的目录若已不可写，不硬套 —— 记日志并退回默认目录，保证还能启动。
    """
    if (os.environ.get(DATA_DIR_ENV) or "").strip():
        return {"applied": False, "reason": "环境变量已显式指定数据目录"}
    target = saved_data_dir()
    if target is None:
        return {"applied": False, "reason": "没有保存过数据目录"}
    probe = probe_directory_writable(target)
    if not probe.get("writable"):
        _log("data_dir", "saved_invalid", log_dir=log_dir, path=str(target))
        return {
            "applied": False,
            "reason": "保存的数据目录当前不可写，已退回默认目录",
            "path": mask_user_path(str(target)),
        }
    os.environ[DATA_DIR_ENV] = str(target)
    os.environ.setdefault(LOG_DIR_ENV, str(target / "logs"))
    _log("data_dir", "saved_applied", log_dir=log_dir, path=str(target))
    return {"applied": True, "path": mask_user_path(str(target))}


# ---------------------------------------------------------------------- #
# 1. 端口被占用 → 换端口并原地重启
# ---------------------------------------------------------------------- #
def switch_port(
    service: Any,
    *,
    requested: int | None = None,
    timeout: float = START_TIMEOUT,
    log_dir: str | Path | None = None,
) -> dict[str, Any]:
    """停掉网关、换到 ``requested`` 起的第一个空闲端口、原地重启。

    **幂等**：重复执行会停在同一个空闲端口（先停后起，上一个端口空出来后
    ``pick_port`` 又会选中它），不会每次往上漂一个端口。
    """
    action = "switch_port"
    original = int(requested or service.port or 0)
    if not original:
        return _result(
            action, ok=False, changed=False,
            summary="当前没有已知端口，无法换端口；请先启动服务。",
            log_dir=log_dir,
        )
    _log(action, "start", log_dir=log_dir, from_port=original, free=port_is_free(original))
    was_running = bool(service.is_running())
    stop_result: dict[str, Any] = {"stopped": False}
    if was_running:
        stop_result = service.stop()
        _log(action, "stopped", log_dir=log_dir, **{k: stop_result.get(k) for k in ("stopped", "graceful", "forced", "elapsed")})

    service.port = original
    started = service.start(timeout=timeout, allow_port_fallback=True)
    new_port = int(service.port or 0)
    base_url = service.base_url
    health = http_json(f"{base_url}/v1/health", timeout=3.0) if base_url else {"ok": False}
    evidence = [
        _evidence("原端口", original),
        _evidence("现在端口", new_port),
        _evidence("停止结果", f"stopped={stop_result.get('stopped')} graceful={stop_result.get('graceful')}"),
        _evidence("新地址", base_url),
        _evidence("健康检查", f"{health.get('status')} {health.get('error') or ''}".strip()),
    ]
    if not started:
        reason = service.last_error or "网关未在限定时间内通过 /v1/health"
        _log(action, "failed", log_dir=log_dir, reason=reason)
        return _result(
            action, ok=False, changed=bool(new_port != original and was_running),
            summary=f"换端口失败：{reason}",
            evidence=evidence, log_dir=log_dir,
        )

    if new_port == original:
        summary = f"端口 {original} 已空闲，服务已在原地址 {base_url} 重新启动（未换端口）。"
    else:
        summary = f"网关已从 {original} 换到 {new_port} 并重启，新地址 {base_url}（书签需要更新）。"

    def _revert() -> dict[str, Any]:
        if service.is_running():
            service.stop()
        service.port = original
        ok = service.start(timeout=timeout, allow_port_fallback=False)
        return _result(
            "switch_port.revert", ok=ok, changed=new_port != original,
            summary=(
                f"已回到端口 {original} 并重启，地址 {service.base_url}。"
                if ok else
                f"端口 {original} 仍被占用，无法回退；服务当前在 {service.base_url}。"
            ),
            evidence=[_evidence("端口", int(service.port or 0)), _evidence("原错误", service.last_error)],
            log_dir=log_dir,
        )

    _log(action, "ok", log_dir=log_dir, new_port=new_port)
    return _result(
        action, ok=True, changed=new_port != original, summary=summary,
        evidence=evidence,
        revert=f"停止服务并把端口改回 {original} 后重启（端口仍被占用时会失败并如实报错）。",
        revert_action=_revert, log_dir=log_dir,
    )


# ---------------------------------------------------------------------- #
# 2. 数据 / 日志目录不可写 → 改目录（自检通过才生效）
# ---------------------------------------------------------------------- #
def change_data_dir(
    new_dir: str | Path,
    *,
    service: Any = None,
    restart: bool = True,
    timeout: float = START_TIMEOUT,
    log_dir: str | Path | None = None,
) -> dict[str, Any]:
    """把网关的数据目录（日志随之）改到 ``new_dir``，写配置并重启验证。

    顺序刻意如此：**先真实写入探测，通过后才落配置**。探测失败就什么都不改，
    用户看到的是"这个目录也不能写 + 原因"。
    """
    action = "change_data_dir"
    raw = str(new_dir or "").strip()
    if not raw:
        return _result(action, ok=False, changed=False, summary="没有选择目录。", log_dir=log_dir)
    target = Path(raw).expanduser()
    try:
        target = target.resolve()
    except OSError:
        target = Path(os.path.abspath(raw))
    previous = Path(os.environ.get(DATA_DIR_ENV) or resolve_data_dir())
    _log(action, "start", log_dir=log_dir, target=str(target), previous=str(previous))

    if target == previous:
        probe = probe_directory_writable(target)
        if probe.get("writable"):
            return _result(
                action, ok=True, changed=False,
                summary=f"数据目录已经指向 {mask_user_path(str(target))}，且当前可写，无需改动。",
                evidence=[_evidence("目录", target), _evidence("写入探测", "通过")],
                log_dir=log_dir,
            )

    if target.is_file():
        return _result(
            action, ok=False, changed=False,
            summary=f"{mask_user_path(str(target))} 是一个文件，不能作为数据目录。",
            log_dir=log_dir,
        )

    try:
        target.mkdir(parents=True, exist_ok=True)
        (target / "logs").mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _log(action, "mkdir_failed", log_dir=log_dir, error=_err(exc))
        return _result(
            action, ok=False, changed=False,
            summary=f"无法创建目录（{_err(exc)}），没有做任何改动。",
            evidence=[_evidence("目标目录", target)],
            log_dir=log_dir,
        )

    probe = probe_directory_writable(target)
    logs_probe = probe_directory_writable(target / "logs")
    if not probe.get("writable") or not logs_probe.get("writable"):
        reason = (probe.get("error") or logs_probe.get("error") or {}).get("type") or "未知原因"
        _log(action, "not_writable", log_dir=log_dir, error=reason)
        return _result(
            action, ok=False, changed=False,
            summary=f"目标目录写入探测失败（{reason}），没有改动配置；请换一个当前用户可写的目录。",
            evidence=[_evidence("数据目录探测", probe), _evidence("日志目录探测", logs_probe)],
            log_dir=log_dir,
        )

    stop_result: dict[str, Any] = {"stopped": False}
    restarted: dict[str, Any] = {"attempted": False}
    if restart and service is not None:
        if service.is_running():
            stop_result = service.stop()

    config_path = _write_config({**_read_config(), "data_dir": str(target)})
    read_back = saved_data_dir()
    if read_back != target:
        return _result(
            action, ok=False, changed=False,
            summary="配置写入后回读不一致，已放弃切换（数据目录仍是原值）。",
            evidence=[_evidence("配置", config_path), _evidence("回读", read_back)],
            log_dir=log_dir,
        )

    os.environ[DATA_DIR_ENV] = str(target)
    os.environ[LOG_DIR_ENV] = str(target / "logs")

    if restart and service is not None:
        restarted = {"attempted": True, "started": bool(service.start(timeout=timeout, allow_port_fallback=False))}
        restarted["error"] = service.last_error
        restarted["address"] = service.base_url

    post = probe_directory_writable(target)
    health = http_json(f"{service.base_url}/v1/health", timeout=3.0) if (service and service.base_url) else {"ok": False}
    evidence = [
        _evidence("新数据目录", target),
        _evidence("原数据目录", previous),
        _evidence("写入自检", "通过" if post.get("writable") else "未通过"),
        _evidence("配置", config_path),
        _evidence("网关重启", restarted.get("started") if restarted.get("attempted") else "未重启"),
        _evidence("健康检查", f"{health.get('status')} {health.get('error') or ''}".strip()),
        _evidence("停止结果", f"stopped={stop_result.get('stopped')}"),
    ]
    if restarted.get("attempted") and not restarted.get("started"):
        _log(action, "restart_failed", log_dir=log_dir, error=restarted.get("error"))
        return _result(
            action, ok=False, changed=True,
            summary=(
                f"目录已改为 {mask_user_path(str(target))} 且写入自检通过，"
                f"但网关重启失败：{restarted.get('error') or '未知原因'}"
            ),
            evidence=evidence,
            revert=(
                f"把数据目录改回 {mask_user_path(str(previous))}"
                "（设置 LIUHAO_DATA_DIR，或再次执行「改数据目录」选原目录）。"
            ),
            log_dir=log_dir,
        )

    summary = (
        f"数据目录已改到 {mask_user_path(str(target))}，写入自检通过"
        + ("，网关已按新目录重启。" if restarted.get("started") else "，网关未运行，下次启动生效。")
    )

    def _revert() -> dict[str, Any]:
        return change_data_dir(
            previous, service=service, restart=restart, timeout=timeout, log_dir=log_dir
        )

    _log(action, "ok", log_dir=log_dir, target=str(target))
    return _result(
        action, ok=True, changed=True, summary=summary, evidence=evidence,
        revert=f"把数据目录改回 {mask_user_path(str(previous))}（新目录里的文件不会被自动搬回）。",
        revert_action=_revert, log_dir=log_dir,
    )


# ---------------------------------------------------------------------- #
# 3. 模型 Key 无效 → 打开对应模型的设置页
# ---------------------------------------------------------------------- #
def model_settings_target(service: Any, model_id: str | None = None) -> dict[str, Any]:
    """返回控制台「系统设置」地址 + 要用户处理的模型。

    这一版驾驶舱产物不支持"定位到某个模型的 Key 输入框"（前端没有该深链参数），
    所以这里只做**真跳转**（``?p=settings`` 是构建产物里真实支持的路由），并
    如实说明不能自动聚焦，不假装已经聚焦。
    """
    base = (service.base_url if service is not None else None) or "http://127.0.0.1:8080"
    url = f"{base.rstrip('/')}/?p=settings"
    return {
        "url": url,
        "model": model_id or "",
        "focus_supported": False,
        "note": (
            "已打开「系统设置」页并在剪贴板放入模型名；当前驾驶舱构建产物没有"
            "“定位到某个模型编辑框”的深链，需要在该页手动点「编辑」后填写 Key。"
        ),
    }


def open_model_settings(
    service: Any,
    model_id: str | None = None,
    *,
    opener: Any = None,
    clipboard: Any = None,
    log_dir: str | Path | None = None,
) -> dict[str, Any]:
    """打开设置页并把模型名写进剪贴板（``opener`` / ``clipboard`` 可注入，便于测试）。"""
    target = model_settings_target(service, model_id)
    opened = False
    error: str | None = None
    try:
        (opener or (lambda url: None))(target["url"])
        opened = True
    except Exception as exc:  # noqa: BLE001 - 打不开浏览器也要给出可复制的地址
        error = _err(exc)
    copied = False
    if target["model"] and clipboard is not None:
        try:
            clipboard(target["model"])
            copied = True
        except Exception as exc:  # noqa: BLE001 - 剪贴板被占用不影响跳转
            _log("open_model_settings", "clipboard_failed", log_dir=log_dir, error=_err(exc))
    _log("open_model_settings", "open", log_dir=log_dir, opened=opened, model=model_id or "")
    evidence = [
        _evidence("地址", target["url"]),
        _evidence("模型", target["model"] or "（未指定，设置页里手动选择）"),
        _evidence("剪贴板", "已写入模型名" if copied else "未写入"),
    ]
    summary = (
        f"已打开系统设置页：{target['url']}。"
        + ("模型名已放入剪贴板。" if copied else "")
        + "这一版界面不能自动把光标放进 Key 输入框，需要点「编辑」后填写。"
        if opened
        else f"没能自动打开浏览器（{error}）；请手动访问 {target['url']}。"
    )
    return _result(
        "open_model_settings",
        ok=opened,
        changed=False,
        summary=summary,
        evidence=evidence,
        log_dir=log_dir,
    )


# ---------------------------------------------------------------------- #
# 4. 网络 / DNS / TCP 不通 → 分步测连接
# ---------------------------------------------------------------------- #
def probe_connection(url: str, *, timeout: float = 5.0, log_dir: str | Path | None = None) -> dict[str, Any]:
    """按 DNS → TCP → HTTP 三步实测，返回失败在哪一步、哪个域名。

    只发一次不携带任何凭据的请求（GET，无 Authorization），不会触发计费调用；
    代理一律绕开 —— 否则本机代理会让"直连不通"看起来像"服务挂了"。
    """
    action = "probe_connection"
    raw = str(url or "").strip()
    target = raw if "://" in raw else f"https://{raw}"
    parsed = urlsplit(target)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    steps: list[dict[str, Any]] = []
    failed_step: str | None = None
    detail = ""

    if not host:
        return _result(
            action, ok=False, changed=False,
            summary=f"地址无效：{raw or '（空）'}",
            evidence=[_evidence("地址", raw)], log_dir=log_dir,
        )

    started = time.perf_counter()
    addresses: list[str] = []
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        addresses = sorted({item[4][0] for item in infos})
        steps.append({"step": "dns", "ok": True, "detail": f"解析到 {', '.join(addresses[:3])}"})
    except (socket.gaierror, OSError, UnicodeError) as exc:
        failed_step = "dns"
        detail = f"域名解析失败（{_err(exc)}）"
        steps.append({"step": "dns", "ok": False, "detail": detail})
        _log(action, "dns_failed", log_dir=log_dir, host=host, error=_err(exc))
        return _result(
            action, ok=False, changed=False,
            summary=f"{host}:{port} DNS 解析失败（{_err(exc)}）：域名不存在、DNS 不可用或被公司网络拦截。",
            evidence=[_evidence("域名", host), _evidence("端口", port), _evidence("DNS", detail)],
            log_dir=log_dir,
        )
    dns_ms = round((time.perf_counter() - started) * 1000, 1)

    tcp_started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
        tcp_ms = round((time.perf_counter() - tcp_started) * 1000, 1)
        steps.append({"step": "tcp", "ok": True, "detail": f"TCP 已连通（{tcp_ms} ms）"})
    except OSError as exc:
        failed_step = "tcp"
        tcp_ms = round((time.perf_counter() - tcp_started) * 1000, 1)
        detail = f"TCP 连接失败（{_err(exc)}）"
        steps.append({"step": "tcp", "ok": False, "detail": detail})
        _log(action, "tcp_failed", log_dir=log_dir, host=host, port=port, error=_err(exc))
        return _result(
            action, ok=False, changed=False,
            summary=(
                f"{host}:{port} 域名能解析（{', '.join(addresses[:2])}），但 TCP 连不上"
                f"（{_err(exc)}）：端口被封、防火墙拦截，或需要走代理。"
            ),
            evidence=[
                _evidence("域名", host), _evidence("端口", port),
                _evidence("DNS", f"ok {dns_ms}ms"), _evidence("TCP", detail),
            ],
            log_dir=log_dir,
        )

    status: int | None = None
    http_ok = False
    http_detail = ""
    http_started = time.perf_counter()
    try:
        request = urllib.request.Request(target, method="GET")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=timeout) as response:
            status = int(response.status)
            http_ok = True
            http_detail = f"HTTP {status}"
    except urllib.error.HTTPError as exc:
        # 401/403/404 都说明"网络通、服务在"，对连通性判断是成功。
        status = int(exc.code)
        http_ok = True
        http_detail = f"HTTP {status}（服务有响应）"
    except ssl.SSLError as exc:
        http_detail = f"TLS 握手失败（{_err(exc)}）"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        http_detail = f"HTTP 请求失败（{_err(exc)}）"
    http_ms = round((time.perf_counter() - http_started) * 1000, 1)
    steps.append({"step": "http", "ok": http_ok, "detail": f"{http_detail}（{http_ms} ms）"})
    if not http_ok:
        failed_step = "http"

    _log(
        action, "done", log_dir=log_dir,
        host=host, port=port, dns_ms=dns_ms, tcp_ms=tcp_ms, http_ms=http_ms,
        http_ok=http_ok, status=status,
    )
    if failed_step:
        summary = (
            f"{host}:{port} DNS 与 TCP 都通，但 HTTP 这一步失败：{http_detail}。"
            "多为 TLS 证书、代理或网关侧拒绝。"
        )
    else:
        summary = f"{host}:{port} 三步都通：DNS → TCP → HTTP（{http_detail}）。"

    return _result(
        action, ok=not failed_step, changed=False, summary=summary,
        evidence=[
            _evidence("域名", host), _evidence("端口", port),
            _evidence("DNS", f"ok {dns_ms}ms"),
            _evidence("TCP", f"ok {tcp_ms}ms"),
            _evidence("HTTP", f"{http_detail} {http_ms}ms"),
        ],
        log_dir=log_dir,
    )


def configured_endpoints() -> list[dict[str, Any]]:
    """已配置模型的名称 / 地址（只读，不解密任何密钥）。"""
    registry = _read_providers()
    items = []
    for model in registry.get("models") or []:
        url = str(model.get("base_url") or "").strip()
        if not url:
            continue
        items.append(
            {
                "name": model.get("name"),
                "provider": model.get("provider"),
                "base_url": url,
                "has_key": model.get("has_key"),
            }
        )
    return items


def probe_endpoints(urls: list[str], *, timeout: float = 5.0, log_dir: str | Path | None = None) -> dict[str, Any]:
    """逐个测连接（只测连接，不发任何带凭据的请求）。"""
    results = [probe_connection(url, timeout=timeout, log_dir=log_dir) for url in urls]
    ok = bool(results) and all(item.get("ok") for item in results)
    summary = (
        "全部端点三步都通。" if ok and results
        else ("没有可测试的模型端点（模型未填 base_url）。" if not results
              else "；".join(item["summary"] for item in results if not item.get("ok")))
    )
    return _result(
        "probe_endpoints", ok=ok, changed=False, summary=summary,
        evidence=[_evidence(url, item.get("summary")) for url, item in zip(urls, results, strict=False)],
        log_dir=log_dir,
    )


# ---------------------------------------------------------------------- #
# 5. 本地库损坏 → 从备份恢复 / 重建空库（原件保留）
# ---------------------------------------------------------------------- #
def _sqlite_candidates(data_dir: Path) -> list[Path]:
    found: list[Path] = []
    try:
        for root, dirs, names in os.walk(data_dir):
            dirs[:] = sorted(
                item for item in dirs
                if item not in {"node_modules", ".git", "__pycache__"} and not item.startswith("recovered-")
            )
            for name in sorted(names):
                if name.endswith(_SQLITE_SIDECARS):
                    continue
                path = Path(root) / name
                try:
                    if path.stat().st_size <= 0 or path.stat().st_size > _MAX_DB_BYTES:
                        continue
                    with open(path, "rb") as handle:
                        header = handle.read(16)
                except OSError:
                    continue
                if header == _SQLITE_MAGIC:
                    found.append(path)
                if len(found) >= _MAX_DB_WALK:
                    return found
    except OSError:
        return found
    return found


def quick_check(path: Path) -> str:
    """``ok`` / ``failed``（确认损坏）/ ``error``（这次没检查成）。

    损坏的库常常在 ``PRAGMA quick_check`` 这一步就抛异常（"not a database" /
    "malformed"），所以异常也要按错误原文归类，不能一律当作"没检查成"。
    """
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return "failed" if _is_corruption_error(exc) else "error"
    try:
        conn.execute("PRAGMA busy_timeout=2000")
        rows = conn.execute("PRAGMA quick_check").fetchall()
        messages = [str(row[0]) for row in rows if row]
        if messages and all(item.lower() == "ok" for item in messages):
            return "ok"
        return "failed" if messages else "error"
    except sqlite3.Error as exc:
        return "failed" if _is_corruption_error(exc) else "error"
    finally:
        conn.close()


def _is_corruption_error(exc: BaseException) -> bool:
    detail = str(exc).lower()
    return any(
        token in detail
        for token in ("malformed", "corrupt", "not a database", "disk image")
    )


def _backup_for(path: Path, data_dir: Path) -> Path | None:
    name = path.name
    for candidate in (
        path.with_name(name + ".bak"),
        data_dir / "backups" / name,
        path.parent / "backups" / name,
    ):
        try:
            if candidate.is_file() and candidate.stat().st_size > 0:
                return candidate
        except OSError:
            continue
    return None


def _new_timestamp_dir(data_dir: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = data_dir / f"recovered-{stamp}"
    index = 2
    while target.exists():
        target = data_dir / f"recovered-{stamp}-{index}"
        index += 1
    return target


def recover_database(
    *,
    data_dir: str | Path | None = None,
    mode: str = "rebuild",
    service: Any = None,
    timeout: float = START_TIMEOUT,
    log_dir: str | Path | None = None,
) -> dict[str, Any]:
    """把损坏的 SQLite 文件移到 ``recovered-<时间戳>``，再恢复备份或重建空库。

    ``mode="rebuild"`` 重建空库（原有数据进 recovered 目录，不删）；
    ``mode="restore"`` 优先用 ``<name>.bak`` / ``backups/<name>``，找不到就重建。
    """
    action = "recover_database"
    root = Path(data_dir or resolve_data_dir())
    _log(action, "start", log_dir=log_dir, root=str(root), mode=mode)
    if not root.is_dir():
        return _result(
            action, ok=False, changed=False,
            summary=f"数据目录不存在或不可访问：{mask_user_path(str(root))}",
            log_dir=log_dir,
        )
    candidates = _sqlite_candidates(root)
    corrupt = [path for path in candidates if quick_check(path) == "failed"]
    if not corrupt:
        return _result(
            action, ok=True, changed=False,
            summary=f"没有发现损坏的数据库（{len(candidates)} 个文件通过 quick_check），未做任何改动。",
            evidence=[_evidence("检查文件数", len(candidates))],
            log_dir=log_dir,
        )

    was_running = bool(service is not None and service.is_running())
    stop_result: dict[str, Any] = {"stopped": False}
    if was_running:
        stop_result = service.stop()

    recovered_dir = _new_timestamp_dir(root)
    moved: list[str] = []
    rebuilt: list[str] = []
    restored: list[str] = []
    failed: list[str] = []
    for path in corrupt:
        relative = path.relative_to(root)
        backup = _backup_for(path, root) if mode == "restore" else None
        try:
            recovered_dir.mkdir(parents=True, exist_ok=True)
            destination = recovered_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(destination))
            moved.append(relative.as_posix())
            for suffix in _SQLITE_SIDECARS:
                sidecar = path.with_name(path.name + suffix)
                if sidecar.exists():
                    shutil.move(str(sidecar), str(destination.with_name(destination.name + suffix)))
            if backup is not None:
                shutil.copy2(backup, path)
                restored.append(f"{relative.as_posix()} ← {backup.name}")
            else:
                with contextlib.suppress(sqlite3.Error):
                    sqlite3.connect(str(path)).close()
                if not path.exists():
                    path.touch()
                rebuilt.append(relative.as_posix())
            if quick_check(path) != "ok":
                failed.append(relative.as_posix())
        except OSError as exc:
            failed.append(f"{relative.as_posix()}（{_err(exc)}）")

    restarted: dict[str, Any] = {"attempted": False}
    if was_running:
        restarted = {"attempted": True, "started": bool(service.start(timeout=timeout, allow_port_fallback=False))}
        restarted["error"] = service.last_error
        restarted["address"] = service.base_url
    health = http_json(f"{service.base_url}/v1/health", timeout=3.0) if (service and service.base_url) else {"ok": False}

    evidence = [
        _evidence("损坏文件", moved),
        _evidence("原件保留", recovered_dir),
        _evidence("恢复方式", "从备份恢复：" + "、".join(restored) if restored else "重建空库：" + "、".join(rebuilt)),
        _evidence("恢复后 quick_check", "失败：" + "、".join(failed) if failed else "通过"),
        _evidence("停止结果", f"stopped={stop_result.get('stopped')}"),
        _evidence("网关重启", restarted.get("started") if restarted.get("attempted") else "未重启"),
        _evidence("健康检查", f"{health.get('status')} {health.get('error') or ''}".strip()),
    ]
    ok = not failed
    summary = (
        f"已把 {len(moved)} 个损坏文件移到 {recovered_dir.name}，"
        + ("并用备份恢复。" if restored else "并重建了空库（原有数据仍在该目录里）。")
        + ("恢复后 quick_check 未通过：" + "、".join(failed) if failed else "恢复后 quick_check 通过。")
    )

    def _revert() -> dict[str, Any]:
        if service is not None and service.is_running():
            service.stop()
        restored_count = 0
        for relative in moved:
            source = recovered_dir / relative
            if not source.exists():
                continue
            target = root / relative
            with contextlib.suppress(OSError):
                target.unlink()
            with contextlib.suppress(OSError):
                for suffix in _SQLITE_SIDECARS:
                    target.with_name(target.name + suffix).unlink(missing_ok=True)
                shutil.move(str(source), str(target))
                restored_count += 1
        restarted_ok = None
        if service is not None:
            restarted_ok = service.start(timeout=timeout, allow_port_fallback=False)
        _log("recover_database", "reverted", log_dir=log_dir, files=restored_count)
        return _result(
            "recover_database.revert", ok=restored_count == len(moved), changed=restored_count > 0,
            summary=f"已把 {restored_count}/{len(moved)} 个原件移回原位"
                    + ("，网关已重启。" if restarted_ok else "。"),
            evidence=[_evidence("移回文件数", restored_count)],
            log_dir=log_dir,
        )

    _log(action, "ok" if ok else "partial", log_dir=log_dir, moved=len(moved), failed=len(failed))
    return _result(
        action, ok=ok, changed=bool(moved), summary=summary, evidence=evidence,
        revert=f"把 {recovered_dir.name} 里的文件移回原位置（会覆盖重建的空库），需要先停止服务。",
        revert_action=_revert, log_dir=log_dir,
    )


# ---------------------------------------------------------------------- #
# 6. 静态资源缺失 → 重新展开前端资源
# ---------------------------------------------------------------------- #
def console_dir_default() -> Path:
    """与网关 ``console_static`` 一致的驾驶舱产物目录。"""
    configured = (os.environ.get(CONSOLE_DIST_ENV) or "").strip()
    if configured:
        return Path(configured).expanduser()
    installed = REPO_ROOT / "console"
    if installed.is_dir():
        return installed
    return REPO_ROOT / "apps" / "console" / "console" / "dist"


def _asset_references(dist: Path) -> list[str]:
    from .diagnostics import _ASSET_REF_RE, _local_asset_path

    try:
        html = (dist / "index.html").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    found: list[str] = []
    for match in _ASSET_REF_RE.finditer(html):
        relative = _local_asset_path(dist, match.group(1))
        if relative and relative not in found:
            found.append(relative)
    return found


def _source_candidates(target: Path) -> list[Path]:
    """可以"重新展开"的来源目录：安装包 payload 优先，其次仓库里的构建产物。"""
    roots = [
        REPO_ROOT / "dist" / "payload" / "app" / "console",
        REPO_ROOT / "dist" / "payload" / "app" / "apps" / "console" / "console" / "dist",
        REPO_ROOT / "apps" / "console" / "console" / "dist",
    ]
    sources: list[Path] = []
    for candidate in roots:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved == target or not (candidate / "index.html").is_file():
            continue
        if resolved not in [item.resolve() for item in sources]:
            sources.append(candidate)
    return sources


def asset_state(console_dir: str | Path | None = None) -> dict[str, Any]:
    """当前驾驶舱静态资源状态（只读，供界面展示与修复前后对比）。"""
    dist = Path(console_dir) if console_dir else console_dir_default()
    index_present = (dist / "index.html").is_file()
    if index_present:
        references = _asset_references(dist)
        missing = [item for item in references if not (dist / item).is_file()]
    else:
        references = []
        missing = ["index.html"]
    return {
        "dist": mask_user_path(str(dist)),
        "index_present": index_present,
        "references": len(references),
        "missing": missing,
        "sources": [mask_user_path(str(item)) for item in _source_candidates(dist)],
    }


def _best_source(missing: list[str], sources: list[Path]) -> Path | None:
    """挑一个能真正补齐的来源：优先"全都有"，否则取命中最多的一份。

    安装包 payload 与仓库构建产物可能是**两次构建**，文件名（带 hash）对不上；
    只要 index.html 引用的是这一份构建，就只能用对应那一份补，拿错来源会
    补 0 个文件还报成功。
    """
    best: tuple[int, Path] | None = None
    for candidate in sources:
        hits = sum(1 for relative in missing if (candidate / relative).is_file())
        if hits == len(missing):
            return candidate
        if hits and (best is None or hits > best[0]):
            best = (hits, candidate)
    return best[1] if best else None


def repair_static_assets(
    *,
    console_dir: str | Path | None = None,
    sources: list[str | Path] | None = None,
    service: Any = None,
    log_dir: str | Path | None = None,
) -> dict[str, Any]:
    """从安装包 payload（或仓库构建产物）补齐缺失的前端文件。

    只**新增**不覆盖：已存在的文件一个字节都不动，因此这个动作天然可回退
    （回退就是删掉本次新增的文件）。``sources`` 可显式指定来源目录（演练/测试
    用临时副本，不必碰真实产物目录）。
    """
    action = "repair_static_assets"
    dist = Path(console_dir) if console_dir else console_dir_default()
    _log(action, "start", log_dir=log_dir, dist=str(dist))
    state = asset_state(dist)
    if not state["missing"]:
        return _result(
            action, ok=True, changed=False,
            summary=f"静态资源齐全（{state['references']} 个引用都在），无需改动。",
            evidence=[_evidence("目录", state["dist"]), _evidence("引用数", state["references"])],
            log_dir=log_dir,
        )

    sources = (
        _source_candidates(dist)
        if sources is None
        else [Path(item) for item in sources]
    )
    if not sources:
        _log(action, "no_source", log_dir=log_dir, missing=len(state["missing"]))
        return _result(
            action, ok=False, changed=False, mode="reinstall",
            summary=(
                f"缺少 {len(state['missing'])} 个前端文件，且本机没有可展开的副本（安装包 payload 不在）。"
                "请用同一个安装包重新安装（修复安装），不要只复制可执行文件。"
            ),
            evidence=[_evidence("目录", state["dist"]), _evidence("缺失", state["missing"][:10])],
            log_dir=log_dir,
        )

    if state["index_present"]:
        source = _best_source(list(state["missing"]), sources)
        if source is None:
            _log(action, "no_matching_source", log_dir=log_dir, missing=len(state["missing"]))
            return _result(
                action, ok=False, changed=False, mode="reinstall",
                summary=(
                    f"缺少 {len(state['missing'])} 个前端文件，本机找到的副本都不是同一版构建"
                    "（文件名对不上），无法安全补齐；请用同一个安装包重新安装。"
                ),
                evidence=[_evidence("目录", state["dist"]), _evidence("缺失", state["missing"][:10])],
                log_dir=log_dir,
            )
    else:
        source = sources[0]

    created: list[str] = []
    # index.html 在：按引用逐个补；index.html 缺失：整目录展开（来源可能与原构建不同）。
    wanted = list(state["missing"]) if state["index_present"] else _walk_files(source)
    for relative in wanted:
        origin = source / relative
        destination = dist / relative
        try:
            if not origin.is_file() or destination.exists():
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origin, destination)
            created.append(relative)
        except OSError as exc:
            _log(action, "copy_failed", log_dir=log_dir, file=relative, error=_err(exc))

    after = asset_state(dist)
    http_status: Any = None
    if service is not None and service.base_url:
        http_status = http_json(f"{service.base_url}/", timeout=3.0).get("status")
    evidence = [
        _evidence("目录", after["dist"]),
        _evidence("来源", mask_user_path(str(source))),
        _evidence("补齐文件", created[:20] or "无"),
        _evidence("修复后缺失", after["missing"][:10] or "无"),
        _evidence("HTTP /", http_status),
    ]
    ok = not after["missing"]
    summary = (
        f"已从 {mask_user_path(str(source))} 补齐 {len(created)} 个文件，修复后静态资源齐全。"
        if ok else
        f"补齐了 {len(created)} 个文件，但仍缺 {len(after['missing'])} 个：{'、'.join(after['missing'][:5])}；请重新安装。"
    )

    def _revert() -> dict[str, Any]:
        removed = 0
        for relative in created:
            with contextlib.suppress(OSError):
                (dist / relative).unlink()
                removed += 1
        _log("repair_static_assets", "reverted", log_dir=log_dir, removed=removed)
        return _result(
            "repair_static_assets.revert", ok=True, changed=removed > 0,
            summary=f"已删除本次新增的 {removed} 个文件（原有文件未被动过）。",
            log_dir=log_dir,
        )

    _log(action, "ok" if ok else "partial", log_dir=log_dir, created=len(created))
    return _result(
        action, ok=ok, changed=bool(created), summary=summary, evidence=evidence,
        revert="删除本次新增的前端文件（原有文件未改动）。",
        revert_action=_revert, log_dir=log_dir,
    )


def _walk_files(root: Path) -> list[str]:
    files: list[str] = []
    try:
        for base, _dirs, names in os.walk(root):
            for name in sorted(names):
                path = Path(base) / name
                with contextlib.suppress(OSError):
                    files.append(path.relative_to(root).as_posix())
    except OSError:
        return files
    return files


# ---------------------------------------------------------------------- #
# 汇总状态（修复面板打开时展示"当前是哪几类问题"）
# ---------------------------------------------------------------------- #
def collect_status(
    service: Any = None,
    *,
    data_dir: str | Path | None = None,
    console_dir: str | Path | None = None,
) -> dict[str, Any]:
    """六类问题当前的实测状态（只读，不做任何改动）。"""
    root = Path(data_dir or resolve_data_dir())
    port = int(getattr(service, "port", 0) or 0) if service is not None else 0
    data_probe = probe_directory_writable(root)
    logs_probe = probe_directory_writable(root / "logs")
    endpoints = configured_endpoints()
    databases = list(_sqlite_candidates(root)) if root.is_dir() else []
    corrupt = [path.name for path in databases if quick_check(path) == "failed"]
    assets = asset_state(console_dir)
    return {
        "port": {"port": port, "free": port_is_free(port) if port else None},
        "data_dir": {
            "path": mask_user_path(str(root)),
            "writable": bool(data_probe.get("writable")),
            "logs_writable": bool(logs_probe.get("writable")),
            "error": (data_probe.get("error") or {}).get("type"),
        },
        "models": {
            "configured": len(endpoints),
            "endpoints": endpoints,
            "without_key": [item["name"] for item in endpoints if item.get("has_key") is False],
        },
        "databases": {"checked": len(databases), "corrupt": corrupt},
        "assets": assets,
    }


