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

import hashlib
import logging
import os
import re
import shutil
import socket
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

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


#: 本机绝对路径（``C:\...`` / ``D:/...``）；前面紧贴字母时不算（排除 ``http://``）。
_DRIVE_PATH_RE = re.compile(r"(?<![\w/])[A-Za-z]:[\\/][^\s\"'，,；;）)\]]*")


def fold_local_paths(text: str, tail: int = 2) -> str:
    """把任意文本里的本机绝对路径折成 ``<本地路径>/尾段``。

    用于"异常原文"这类无法预先知道形状的字符串：保留最后 ``tail`` 段
    （通常是 ``logs/liuhao.log`` 这种能对上自己文件的线索），丢掉盘符与用户名目录。
    """
    def _replacement(match: "re.Match[str]") -> str:
        return display_path(match.group(0), tail=tail)

    return _DRIVE_PATH_RE.sub(_replacement, text)


def path_kind(path: Any) -> str:
    """目标路径的类型：``directory`` / ``file`` / ``missing`` / ``other`` / ``inaccessible``。

    故障 1（数据目录被同名普通文件挡住）里 ``winerror=183`` 只说明"已存在"，
    真正要告诉用户的是"它存在，但不是文件夹"——所以类型必须显式落盘。
    """
    try:
        candidate = Path(str(path)).expanduser()
        if candidate.is_dir():
            return "directory"
        if candidate.is_file():
            return "file"
        if candidate.exists():
            return "other"
        return "missing"
    except OSError:
        return "inaccessible"


def exc_text(exc: Optional[BaseException], target: Any = None, tail: int = 2) -> str:
    """异常原文（类型 + 消息），路径已折叠，可直接写进日志/诊断包。"""
    if exc is None:
        return "-"
    text = f"{type(exc).__name__}: {exc}"
    if target not in (None, ""):
        raw = str(target)
        if raw:
            text = text.replace(raw, display_path(raw, tail=tail))
    return fold_local_paths(text.replace('"', "'"), tail=tail)


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
    """从 ``OSError`` 家族取出 ``(winerror, errno)``。

    Windows 的 ``open()`` 在 ACL 拒绝时只带 ``errno=13``，不带 ``winerror``；
    此时按系统自带映射补成 ``ERROR_ACCESS_DENIED(5)``，让日志和安装器都拿到
    可检索的 Win32 错误码（Python ``errmap.h``：EACCES ↔ 5）。
    """
    if exc is None:
        return None, None
    winerror = getattr(exc, "winerror", None)
    errno_v = getattr(exc, "errno", None)
    if errno_v is None and not isinstance(exc, OSError):
        errno_v = None
    if winerror is None and errno_v == 13 and os.name == "nt":
        winerror = 5
    return (int(winerror) if isinstance(winerror, int) else None,
            int(errno_v) if isinstance(errno_v, int) else None)


def _bounded(fn, seconds: float = 2.0, step: Optional[Dict[str, Any]] = None):
    """在守护线程里执行一次探测，超时就放弃。

    只看权限位不够：某些安全软件/文件系统过滤驱动会让"被拒绝的创建"在
    ``open`` 里挂住几十秒。启动自检本身绝不能拖住启动——否则最需要被记录的
    那一条 ``startup.check`` 永远写不出来（故障 2B 实测就是这样）。

    但"超时"不能变成一句没有信息量的 ``probe did not finish``：``step`` 由探测
    函数逐阶段更新，超时时把**最后一步**和**最后拿到的异常**一起带出去。
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
        # 线程可能恰在 join 返回后、这里判断前结束并留下真实异常：有异常就报异常，
        # 不再退回"超时"这种更弱的信息。
        late_exc = (step or {}).get("exception")
        if late_exc is not None:
            raise late_exc
        last = (step or {}).get("at") or "unknown"
        last_error = (step or {}).get("error")
        detail = f"probe did not finish within {seconds}s (last step: {last}"
        if last_error:
            detail += f"; last error: {last_error}"
        timeout = TimeoutError(detail + ")")
        # 真实异常往往在超时之后才返回（ACL 拒绝会被系统过滤驱动拖慢）：把工作线程
        # 交给调用方，等它真正结束后补一条带真实异常的后续事件，而不是永远吞掉。
        try:
            setattr(timeout, "liuhao_probe_thread", worker)
        except Exception:  # noqa: BLE001 - 属性设置失败不影响超时语义
            pass
        raise timeout
    if "error" in box:
        raise box["error"]
    return box["value"]


def _report_late_probe(
    directory: Any,
    worker: threading.Thread,
    step: Dict[str, Any],
    started: float,
    label: str = "write_probe",
) -> None:
    """探测已超时、但工作线程终会返回：把**真实异常**补记成一条独立事件。

    启动不能为一次写探测等上几秒，但"超时"不等于"没有原因"。等线程真正结束
    后写 ``evt=boot.<label>.probe_late``，包含异常类型、``winerror``、``errno``、
    停在的步骤与总耗时；日志里既保留了超时那一刻的最后一步，也不丢真实原因。
    """
    def _wait() -> None:
        worker.join()
        exc = step.get("exception")
        if exc is None:
            return  # 超时后其实成功了，没有可报的失败
        winerror, errno_v = _errno_fields(exc)
        elapsed = time.monotonic() - started
        event = f"boot.{label}.probe_late"
        fields = dict(
            path=display_path(directory, tail=2),
            path_kind=path_kind(directory),
            step=step.get("at") or "-",
            winerror=winerror,
            errno=errno_v,
            elapsed_s=f"{elapsed:.1f}",
            exc=exc_text(exc, directory),
            note="写探测超过启动预算后返回，以上为真实原因",
        )
        logger.warning(
            'evt=%s path=%s path_kind=%s step=%s winerror=%s '
            'errno=%s elapsed_s=%.1f exc="%s" note="%s"',
            event,
            fields["path"],
            fields["path_kind"],
            fields["step"],
            winerror if winerror is not None else "-",
            errno_v if errno_v is not None else "-",
            elapsed,
            fields["exc"].replace('"', "'"),
            fields["note"],
        )
        try:
            from .. import bootstrap_log

            bootstrap_log.write_event(event, **fields)
        except Exception:  # noqa: BLE001 - 引导日志失败不影响主流程
            pass

    watcher = threading.Thread(target=_wait, name="boot-probe-late", daemon=True)
    watcher.start()


def probe_writable(
    directory: Any, label: str = "write_probe"
) -> Tuple[bool, Optional[BaseException]]:
    """真的写一个临时文件再删掉——``os.access`` 在 Windows ACL 下并不可信。

    失败时异常对象上会带上 ``liuhao_probe_step`` / ``liuhao_probe_error``，
    调用方据此把"卡在哪一步"和"真实异常"一起写进日志与 installation.json。
    超时后工作线程若继续跑完并抛错，会由 :func:`_report_late_probe` 补记。
    """
    step: Dict[str, Any] = {"at": "start"}
    started = time.monotonic()
    try:
        _bounded(lambda: _write_probe(directory, step), step=step)
        return True, None
    except BaseException as exc:  # noqa: BLE001 - 任何失败都如实上报，且不能抛
        for name, value in (
            ("liuhao_probe_step", step.get("at")),
            ("liuhao_probe_error", step.get("error")),
        ):
            try:
                setattr(exc, name, value)
            except Exception:  # noqa: BLE001 - 某些异常对象不允许设置属性
                pass
        worker = getattr(exc, "liuhao_probe_thread", None)
        if isinstance(worker, threading.Thread) and worker.is_alive():
            _report_late_probe(directory, worker, step, started, label)
        return False, exc


def _write_probe(directory: Any, step: Optional[Dict[str, Any]] = None) -> bool:
    def mark(stage: str) -> None:
        if step is not None:
            step["at"] = stage

    def note(exc: BaseException) -> None:
        if step is None:
            return
        step["exception"] = exc
        step["error"] = exc_text(exc, root, tail=1)

    mark("resolve_path")
    root = Path(str(directory)).expanduser()
    mark("create_directory")
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        note(exc)
        raise
    mark("create_temp_file")
    name = str(root / f".liuhao-startup-probe-{os.getpid()}-{time.time_ns()}.tmp")
    try:
        # 不用 tempfile.mkstemp：在 Windows ACL 拒绝写的目录里它可能卡住数十秒
        # （系统过滤驱动），而 open("xb") 同一路径会**立刻**抛 PermissionError。
        # 启动探测必须要么给出真实异常，要么在 2 秒内明确超时，不能悬着。
        handle = open(name, "xb")
    except OSError as exc:
        note(exc)
        raise
    mark("write_byte")
    try:
        handle.write(b"ok")
    except OSError as exc:
        note(exc)
        raise
    finally:
        handle.close()
        try:
            mark("remove_temp_file")
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
    data_ok, data_exc = probe_writable(data_dir, "data_dir")
    log_ok, log_exc = probe_writable(log_dir, "log_dir")

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
        "data_dir_kind": path_kind(data_dir),
        "data_writable": data_ok,
        "log_dir": display_path(log_dir, tail=2),
        "log_dir_kind": path_kind(log_dir),
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
        "evt=startup.check data_dir=%s data_dir_kind=%s data_writable=%s "
        "log_dir=%s log_dir_kind=%s log_writable=%s db=[%s] db_open=%s host=%s "
        "port=%s port_bindable=%s port_state=%s console_dist=%s console_index=%s "
        "provider=%s model=%s key_fp=%s provider_source=%s",
        payload["data_dir"],
        payload["data_dir_kind"],
        _flag(data_ok),
        payload["log_dir"],
        payload["log_dir_kind"],
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
        kind = path_kind(raw_path)
        stage = getattr(exc, "liuhao_probe_step", None) or "-"
        last_error = getattr(exc, "liuhao_probe_error", None) or ""
        action = (
            "授予写权限 / 更换到可写目录 / 关闭安全软件拦截 / 清理磁盘后重启"
        )
        add_degraded(label, display_path(raw_path), str(exc or "unwritable"), action)
        logger.error(
            'evt=boot.%s.unwritable path=%s path_kind=%s winerror=%s errno=%s '
            'free_bytes=%s step=%s exc="%s" action="%s"',
            label,
            display_path(raw_path),
            kind,
            winerror if winerror is not None else "-",
            errno_v if errno_v is not None else "-",
            free_bytes(raw_path),
            stage,
            exc_text(exc, raw_path).replace('"', "'"),
            action,
        )
        try:
            from .. import bootstrap_log

            bootstrap_log.write_event(
                f"boot.{label}.unwritable",
                path=str(raw_path),
                path_kind=kind,
                winerror=winerror,
                errno=errno_v,
                free_bytes=free_bytes(raw_path),
                step=stage,
                exc=exc_text(exc, raw_path),
                last_error=last_error or None,
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
    "exc_text",
    "fold_local_paths",
    "free_bytes",
    "key_fingerprint",
    "last_startup_check",
    "path_kind",
    "port_bindable",
    "probe_db",
    "probe_writable",
    "provider_summary",
    "record_db_open_failure",
    "reset_degraded",
]
