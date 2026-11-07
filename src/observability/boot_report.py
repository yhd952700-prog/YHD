"""启动自检与降级登记：让"坏了"在日志里带上路径、错误码和处置动作。

为什么需要这个模块
------------------
用户能拿到的排障材料通常只有日志。历史上最难受的两类失败是：

* 数据目录 / 日志目录不可写 —— 只有一句 stderr，没有路径、没有 ``winerror``，
  日志文件本身也建不出来；
* 数据库打不开（权限 / 损坏）—— ``unable to open database file`` 与
  ``file is not a database`` 都不带文件名，而文件日志的脱敏又把绝对路径折叠成
  ``<本地路径>``，于是"哪个库坏了"永远看不出来。

所以这里的契约只有三条：

1. **路径脱敏但不丢线索**：数据目录内的文件写成相对数据目录的形态
   （``data/audit.db``），目录外的只保留文件名；
2. **失败必须带动作**：每条降级都写明 ``action``（改权限 / 换目录 / 隔离文件）；
3. **同一份降级列表**既进日志（``evt=startup.degraded``），也进
   ``GET /v1/startup``，两边永远一致。

事件格式是单行 ``key=value``（``evt=...`` 开头），便于 ``Select-String``。
"""
from __future__ import annotations

import errno as _errno
import hashlib
import logging
import os
import shutil
import socket
import sqlite3
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

logger = logging.getLogger("liuhao.boot")

_DEGRADED: List[Dict[str, str]] = []
_LOCK = threading.Lock()
_LAST_STARTUP_CHECK: Dict[str, Any] = {}


# --------------------------------------------------------------------------- #
# 路径脱敏
# --------------------------------------------------------------------------- #

def display_path(path: Any, data_dir: Any = None, tail: int = 1) -> str:
    """脱敏路径：数据目录内 -> ``data/<相对>``；否则保留末尾 ``tail`` 段。

    不返回任何盘符/用户目录，因此即使配置的 formatter 忘了脱敏也不会泄露目录
    结构；但"哪个子系统 / 哪个文件"这个排障线索一定保留（例如
    ``<本地路径>/console/console/dist``）。
    """
    if path in (None, ""):
        return "unset"
    raw = str(path)
    candidate = Path(raw).expanduser()
    if data_dir not in (None, ""):
        try:
            relative = candidate.resolve().relative_to(Path(str(data_dir)).expanduser().resolve())
            text = relative.as_posix()
            return f"data/{text}" if text and text != "." else "data"
        except (ValueError, OSError):
            pass
    if not candidate.is_absolute():
        # 已经是相对路径 / 占位符（例如 "<AUDIT_DB_PATH 未配置>"）原样保留。
        return raw
    parts = [part for part in candidate.parts if part not in (candidate.anchor, "\\", "/")]
    keep = max(1, int(tail))
    name = "/".join(parts[-keep:]) if parts else (candidate.name or raw)
    return f"<本地路径>/{name}"


def key_fingerprint(api_key: Optional[str]) -> str:
    """密钥指纹（``sha256:`` + 前 8 位十六进制），绝不出密钥本身。"""
    value = (api_key or "").strip()
    if not value or value == "[REDACTED]":
        return "none"
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def free_bytes(path: Any) -> Optional[int]:
    """``path`` 所在卷的剩余字节；取不到返回 None（不让自检变成失败源）。"""
    try:
        probe = Path(str(path)).expanduser()
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        return int(shutil.disk_usage(str(probe)).free)
    except Exception:  # noqa: BLE001 - 自检不能因为取不到就崩
        return None


def _errno_fields(exc: Optional[BaseException]) -> Tuple[Optional[int], Optional[int]]:
    """从 ``OSError`` 家族取出 ``(winerror, errno)``。"""
    if exc is None:
        return None, None
    winerror = getattr(exc, "winerror", None)
    errno_v = getattr(exc, "errno", None)
    if errno_v is None and not isinstance(exc, OSError):
        errno_v = None
    return (int(winerror) if isinstance(winerror, int) else None,
            int(errno_v) if isinstance(errno_v, int) else None)


def _bounded(fn, seconds: float = 2.0):
    """在守护线程里执行一次探测，超时就放弃。

    只看权限位不够：某些安全软件/文件系统过滤驱动会让"被拒绝的创建"在
    ``open`` 里挂住几十秒。启动自检本身绝不能拖住启动——否则最需要被记录的
    那一条 ``startup.check`` 永远写不出来（故障 2B 实测就是这样）。
    """
    box: Dict[str, Any] = {}

    def _run() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - 交给调用方判断
            box["error"] = exc

    worker = threading.Thread(target=_run, name="boot-probe", daemon=True)
    worker.start()
    worker.join(seconds)
    if worker.is_alive():
        raise TimeoutError(f"probe did not finish within {seconds}s")
    if "error" in box:
        raise box["error"]
    return box["value"]


def probe_writable(directory: Any) -> Tuple[bool, Optional[BaseException]]:
    """真的写一个临时文件再删掉——``os.access`` 在 Windows ACL 下并不可信。"""
    try:
        _bounded(lambda: _write_probe(directory))
        return True, None
    except BaseException as exc:  # noqa: BLE001 - 任何失败都如实上报，且不能抛
        return False, exc


def _write_probe(directory: Any) -> bool:
    root = Path(str(directory)).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix=".liuhao-startup-probe-", dir=str(root))
    try:
        os.write(handle, b"ok")
    finally:
        os.close(handle)
        try:
            os.remove(name)
        except OSError:
            pass
    return True


def probe_db(path: Any) -> Tuple[Optional[bool], str]:
    """轻量探测一个已存在的 SQLite 文件能否打开。

    返回 ``(None, "absent")`` 表示文件还不存在（首启 / 尚未创建），
    ``(True, "")`` 表示可读，``(False, msg)`` 表示存在但打不开（权限 / 损坏）。
    **不会**为了探测而创建一个空库。
    """
    candidate = Path(str(path)).expanduser()
    if not candidate.exists():
        return None, "absent"
    try:
        _bounded(lambda: _open_db(candidate))
        return True, ""
    except Exception as exc:  # noqa: BLE001 - 任何打开失败都要如实上报
        return False, f"{type(exc).__name__}: {exc}"


def _open_db(candidate: Path) -> None:
    conn = sqlite3.connect(str(candidate), timeout=1.0)
    try:
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    finally:
        conn.close()


def port_bindable(host: str, port: int) -> bool:
    """``host:port`` 当前是否可绑定（与网关真实绑定的口径一致）。"""
    probe_host = host if host not in ("0.0.0.0", "::", "[::]", "::0") else "127.0.0.1"
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        sock.bind((probe_host, int(port)))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def port_state(host: str, port: int) -> Tuple[bool, str]:
    """返回 ``(端口可绑定, 状态)``。

    ``port_bindable`` 报告**真实 bind 结果**：Windows 允许非独占的重复绑定，
    所以正常启动时常常是 ``True/free``；而在故障 1（占用方设置了
    ``SO_EXCLUSIVEADDRUSE``）里 bind 会失败。此时再用一次 TCP 连接区分
    ``serving``（有进程在监听）与 ``occupied``（既不能绑也连不上）。
    """
    if port_bindable(host, port):
        return True, "free"
    probe_host = host if host not in ("0.0.0.0", "::", "[::]", "::0") else "127.0.0.1"
    try:
        with socket.create_connection((probe_host, int(port)), timeout=0.5):
            return False, "serving"
    except OSError:
        return False, "occupied"


# --------------------------------------------------------------------------- #
# 模型身份（provider / model / key 指纹）
# --------------------------------------------------------------------------- #

def provider_summary() -> Dict[str, str]:
    """当前生效的 provider/model/key 指纹；配置中心优先，其次环境变量。"""
    provider = model = api_key = ""
    source = "env"
    try:
        from ..ai.model_config import get_model_config_store

        cfg = get_model_config_store().resolve()
        if cfg:
            provider = str(cfg.get("provider") or "")
            model = str(cfg.get("model") or "")
            api_key = str(cfg.get("api_key") or "")
            source = "registry"
    except Exception:  # noqa: BLE001 - 自检不因配置中心异常而失败
        pass
    if not provider:
        provider = os.environ.get("AI_PROVIDER_TYPE") or "mock"
        model = os.environ.get("AI_PROVIDER_MODEL") or "mock-model"
        api_key = os.environ.get("AI_PROVIDER_KEY") or ""
    return {
        "provider": provider,
        "model": model,
        "key_fp": key_fingerprint(api_key),
        "source": source,
    }


# --------------------------------------------------------------------------- #
# 降级登记
# --------------------------------------------------------------------------- #

def add_degraded(subsystem: str, path: str, message: str, action: str) -> None:
    """登记一个降级组件（按 subsystem 去重，保留首次失败原因）。"""
    entry = {
        "subsystem": str(subsystem),
        "path": str(path),
        "sqlite_msg": str(message),
        "action": str(action),
    }
    with _LOCK:
        if any(item["subsystem"] == entry["subsystem"] for item in _DEGRADED):
            return
        _DEGRADED.append(entry)


def degraded_components() -> List[Dict[str, str]]:
    """``GET /v1/startup`` 与 ``evt=startup.degraded`` 共用的同一份列表。"""
    with _LOCK:
        return [dict(item) for item in _DEGRADED]


def reset_degraded() -> None:
    """测试与同一进程内多次 lifespan 使用。"""
    with _LOCK:
        _DEGRADED.clear()


def record_db_open_failure(
    subsystem: str,
    path: Any,
    exc: BaseException,
    *,
    action: Optional[str] = None,
    data_dir: Any = None,
) -> Dict[str, str]:
    """``evt=db.open.failed``：库打不开时唯一需要看的那一行。"""
    base = data_dir if data_dir not in (None, "") else os.environ.get("LIUHAO_DATA_DIR")
    shown = display_path(path, base)
    message = f"{type(exc).__name__}: {exc}"
    winerror, errno_v = _errno_fields(exc)
    if action is None:
        action = (
            f"degraded: {subsystem} 不可用；确认数据目录可写/磁盘有余量，"
            "或隔离损坏文件后重启"
        )
    # 同一次启动里同一个库会被多处尝试打开（审计链、启动自检、业务请求）。
    # 首次失败已经写清原因，后续重试不再重复刷同一行——否则 error.log 被同一条
    # 事实淹没，真正不同的失败反而更难看见。
    with _LOCK:
        already = any(item["subsystem"] == str(subsystem) for item in _DEGRADED)
    if not already:
        logger.error(
            'evt=db.open.failed subsystem=%s path=%s sqlite_msg="%s" winerror=%s '
            'errno=%s action="%s"',
            subsystem,
            shown,
            message.replace('"', "'"),
            winerror if winerror is not None else "-",
            errno_v if errno_v is not None else "-",
            action,
        )
    add_degraded(subsystem, shown, message, action)
    return next(
        item for item in degraded_components() if item["subsystem"] == str(subsystem)
    )


def emit_startup_degraded() -> List[Dict[str, str]]:
    """把当前降级列表写成 ``evt=startup.degraded``（逐组件 + 一行汇总）。"""
    components = degraded_components()
    for item in components:
        logger.error(
            'evt=startup.degraded subsystem=%s path=%s sqlite_msg="%s" action="%s"',
            item["subsystem"],
            item["path"],
            item["sqlite_msg"].replace('"', "'"),
            item["action"],
        )
    if components:
        logger.error(
            "evt=startup.degraded components=[%s] ready=false action=\"按各组件 "
            "action 处理后重启；在修复前对应功能不可用\"",
            ", ".join(item["subsystem"] for item in components),
        )
    return components


# --------------------------------------------------------------------------- #
# 启动自检
# --------------------------------------------------------------------------- #

def _flag(value: Any) -> str:
    if value is None:
        return "unknown"
    return "1" if bool(value) else "0"


def emit_startup_check(
    *,
    data_dir: Any,
    log_dir: Any,
    db_paths: Optional[Mapping[str, Any]] = None,
    host: str = "127.0.0.1",
    port: int = 8080,
    console_dist: Any = None,
) -> Dict[str, Any]:
    """输出**一条** ``evt=startup.check``，并返回同一份结构化结果。

    字段固定为：``data_dir/data_writable/log_dir/log_writable/db/db_open/
    port/port_bindable/console_dist/console_index/provider/model/key_fp``。
    """
    data_ok, data_exc = probe_writable(data_dir)
    log_ok, log_exc = probe_writable(log_dir)

    db_rows: List[str] = []
    db_open = True
    for name, path in (db_paths or {}).items():
        if path in (None, ""):
            db_rows.append(f"{name}=unset")
            continue
        ok, message = probe_db(path)
        shown = display_path(path, data_dir)
        if ok is None:
            db_rows.append(f"{name}={shown}:absent")
        elif ok:
            db_rows.append(f"{name}={shown}:open")
        else:
            db_open = False
            db_rows.append(f"{name}={shown}:fail")
            record_db_open_failure(name, path, RuntimeError(message), data_dir=data_dir)

    bindable, bind_state = port_state(host, port)
    dist_path = Path(str(console_dist)).expanduser() if console_dist else None
    console_index = bool(dist_path and (dist_path / "index.html").is_file())
    info = provider_summary()

    payload: Dict[str, Any] = {
        "data_dir": display_path(data_dir, tail=2),
        "data_writable": data_ok,
        "log_dir": display_path(log_dir, tail=2),
        "log_writable": log_ok,
        "db": ", ".join(db_rows),
        "db_open": db_open,
        "host": host,
        "port": int(port),
        "port_bindable": bindable,
        "port_state": bind_state,
        "console_dist": display_path(dist_path, tail=3) if dist_path else "none",
        "console_index": console_index,
        "provider": info["provider"],
        "model": info["model"],
        "key_fp": info["key_fp"],
        "provider_source": info["source"],
    }
    logger.info(
        "evt=startup.check data_dir=%s data_writable=%s log_dir=%s "
        "log_writable=%s db=[%s] db_open=%s host=%s port=%s port_bindable=%s "
        "port_state=%s console_dist=%s console_index=%s provider=%s model=%s "
        "key_fp=%s provider_source=%s",
        payload["data_dir"],
        _flag(data_ok),
        payload["log_dir"],
        _flag(log_ok),
        payload["db"],
        _flag(db_open),
        host,
        int(port),
        _flag(bindable),
        bind_state,
        payload["console_dist"],
        _flag(console_index),
        payload["provider"],
        payload["model"],
        payload["key_fp"],
        payload["provider_source"],
    )

    # 写不进去是最需要被看见的一类失败：额外落一条 ERROR，并留一份引导日志。
    for label, ok, exc, raw_path in (
        ("data_dir", data_ok, data_exc, data_dir),
        ("log_dir", log_ok, log_exc, log_dir),
    ):
        if ok:
            continue
        winerror, errno_v = _errno_fields(exc)
        action = (
            "授予写权限 / 更换到可写目录 / 关闭安全软件拦截 / 清理磁盘后重启"
        )
        add_degraded(label, display_path(raw_path), str(exc or "unwritable"), action)
        logger.error(
            'evt=boot.%s.unwritable path=%s winerror=%s errno=%s free_bytes=%s '
            'action="%s"',
            label,
            display_path(raw_path),
            winerror if winerror is not None else "-",
            errno_v if errno_v is not None else "-",
            free_bytes(raw_path),
            action,
        )
        try:
            from .. import bootstrap_log

            bootstrap_log.write_event(
                f"boot.{label}.unwritable",
                path=str(raw_path),
                winerror=winerror,
                errno=errno_v,
                free_bytes=free_bytes(raw_path),
                action=action,
            )
        except Exception:  # noqa: BLE001 - 引导日志失败不能影响主流程
            pass

    with _LOCK:
        _LAST_STARTUP_CHECK.clear()
        _LAST_STARTUP_CHECK.update(payload)
    return dict(payload)


def last_startup_check() -> Dict[str, Any]:
    """最近一次 ``evt=startup.check`` 的结构化副本（供 /v1/startup）。"""
    with _LOCK:
        return dict(_LAST_STARTUP_CHECK)


__all__ = [
    "add_degraded",
    "degraded_components",
    "display_path",
    "emit_startup_check",
    "emit_startup_degraded",
    "free_bytes",
    "key_fingerprint",
    "last_startup_check",
    "port_bindable",
    "probe_db",
    "probe_writable",
    "provider_summary",
    "record_db_open_failure",
    "reset_degraded",
]
