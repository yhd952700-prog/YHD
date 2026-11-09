"""Load uvicorn's log configuration as **UTF-8**, never the locale default.

Why this module exists
----------------------
``config/logging.yaml`` is a UTF-8 file with Chinese comments. uvicorn's own
loader does ``open(self.log_config)`` with no ``encoding=`` argument, so on a
Chinese Windows (``locale.getpreferredencoding(False) == "cp936"``) the first
launch dies with ``UnicodeDecodeError`` before the app is even imported. The
distribution workaround was to set ``PYTHONUTF8=1`` by hand -- something a
user who double-clicks the executable never does.

Every entry point therefore reads the file here (explicit UTF-8) and hands
uvicorn the resulting **mapping** instead of a path. A dict takes uvicorn's
``logging.config.dictConfig`` branch, which never touches the filesystem.

This module deliberately lives **outside** ``src.gateway``: importing
``src.gateway`` executes its ``__init__``, which builds the FastAPI app. The
desktop child (``src.desktop._runner``) must configure logging *before* that
happens, so it cannot import a helper from inside the gateway package.

Do not "simplify" this back to ``log_config=str(path)``: that reintroduces the
crash, and ``tests/test_gateway_logging_encoding.py`` simulates cp936 to catch
exactly that regression.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from .common.atomic_write import atomic_write_text, exclusive_file_lock, read_text_with_retry
from .common.redaction import REDACTION_VERSION, redact_log_text

#: Repo-root ``config/logging.yaml``. The installed build copies it into the
#: user data directory and points ``LIUHAO_LOG_CONFIG`` at that copy.
DEFAULT_LOG_CONFIG = Path(__file__).resolve().parents[1] / "config" / "logging.yaml"

#: Lets a launcher point the gateway child at the installed copy without
#: passing a uvicorn ``--log-config`` path (which uvicorn would read as cp936).
LOG_CONFIG_ENV = "LIUHAO_LOG_CONFIG"

#: User data root -- the same contract ``scripts/launch_installed.py`` uses.
DATA_DIR_ENV = "LIUHAO_DATA_DIR"
LOG_DIR_ENV = "LIUHAO_LOG_DIR"

#: stdlib handler the configs used before the multi-process fix; still rewritten
#: at load time because installed data dirs keep their generated copy of the YAML
#: (``scripts/launch_installed.py::_ensure_logging_config`` never overwrites it).
_STDLIB_ROTATING_CLASS = "logging.handlers.RotatingFileHandler"


def load_uvicorn_log_config(path: str | os.PathLike[str] | None) -> dict[str, Any] | None:
    """Read a uvicorn YAML log config as UTF-8 and return its mapping.

    ``None``/missing file keeps uvicorn's built-in defaults (the previous
    behaviour of the gateway entry point). A malformed file raises instead of
    silently falling back: a config that does not load must be visible.

    The returned mapping is normalized for the runtime environment:

    * relative ``filename`` values resolve under the **user data directory**
      (or ``LIUHAO_LOG_DIR``), never the repository/CWD -- otherwise every
      process started from the repo root writes ``<repo>/logs/liuhao.log``;
    * ``logging.handlers.RotatingFileHandler`` becomes
      :class:`MultiProcessRotatingFileHandler`, so an *old* installed config
      copy gets the multi-process-safe rotation without needing an upgrade.
    """
    if path is None:
        return None
    config_path = Path(path)
    if not config_path.is_file():
        return None
    try:
        document = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - surface the real parse failure
        raise RuntimeError(
            f"日志配置 {config_path} 无法按 UTF-8 解析：{type(exc).__name__}: {exc}"
        ) from exc
    if not isinstance(document, dict):
        raise RuntimeError(
            f"日志配置 {config_path} 的顶层必须是映射，实际是 {type(document).__name__}"
        )
    config = _normalize_runtime_config(document)
    # 旧版本的日志文件里可能还留着明文凭据（见 ``scrub_legacy_log_leaks``）。
    # 入口都从这里读配置，所以一次性清理挂在这里最省事；目录取配置里
    # handler 的实际落点，失败不能拦住启动。
    with contextlib.suppress(Exception):
        handlers = config.get("handlers") or {}
        log_dirs = {
            Path(item["filename"]).parent
            for item in handlers.values()
            if isinstance(item, dict)
            and isinstance(item.get("filename"), str)
            and item["filename"]
        }
        for directory in log_dirs or [resolve_log_file(Path("logs") / "liuhao.log").parent]:
            scrub_legacy_log_leaks(directory)
            # 启动先按「天数 + 总量」清一轮：长期运行由桌面壳的巡检线程兜底。
            prune_log_retention(directory)
    return config


def resolve_data_dir() -> Path:
    """用户数据目录：``LIUHAO_DATA_DIR`` 覆盖，否则与安装版一致的默认位置。

    与 ``src/kernels/identity/_persistence.py::default_data_dir`` 同一契约；
    在这里按调用时求值，安装版启动器先设环境变量再导入网关。
    """
    raw = (os.environ.get(DATA_DIR_ENV) or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    return (base / "LiuHao" / "data").resolve()


def resolve_log_file(filename: str) -> Path:
    """把配置里的日志文件名钉到数据目录/实例目录，绝不落仓库根。

    覆盖优先级：``LIUHAO_LOG_DIR``（显式实例目录）> ``LIUHAO_DATA_DIR``
    （数据根）> 配置里的路径 > 平台默认数据目录。

    关键点：**绝对路径也必须接受覆盖**。安装包里的 logging 配置常把
    ``filename`` 写死成真实数据目录（``%LOCALAPPDATA%/LiuHao/data/logs/...``）
    的绝对路径；隔离实例只设置了 LIUHAO_DATA_DIR 时，旧实现看到绝对路径就直接
    返回，于是文件日志继续写进真实数据目录（soak 证据里就是这样污染的）。
    覆盖生效时只保留 basename/相对子路径，目录由覆盖根决定。
    """
    path = Path(filename).expanduser()
    raw_log_dir = (os.environ.get(LOG_DIR_ENV) or "").strip()
    data_override = (os.environ.get(DATA_DIR_ENV) or "").strip()

    if raw_log_dir:
        # LIUHAO_LOG_DIR IS the log directory: place the file directly there,
        # even if the config shipped a "logs/..." prefix for the default root.
        root = Path(raw_log_dir).expanduser().resolve()
        return root / path.name
    if data_override:
        root = resolve_data_dir()
        if not path.is_absolute():
            return root / path
        # Rebase an absolute filename while keeping its meaningful tail
        # (``.../logs/liuhao.log`` -> ``<data>/logs/liuhao.log``) so installs
        # that hard-code the real data dir cannot keep writing there under
        # LIUHAO_DATA_DIR isolation.
        parts = path.parts
        if "logs" in parts:
            return root.joinpath(*parts[parts.index("logs"):])
        return root / path.name
    if path.is_absolute():
        return path
    return resolve_data_dir() / path


def _normalize_runtime_config(config: dict[str, Any]) -> dict[str, Any]:
    handlers = config.get("handlers")
    if isinstance(handlers, dict):
        for handler in handlers.values():
            if not isinstance(handler, dict):
                continue
            if handler.get("class") == _STDLIB_ROTATING_CLASS:
                handler["class"] = f"{__name__}.MultiProcessRotatingFileHandler"
            filename = handler.get("filename")
            if isinstance(filename, str) and filename:
                handler["filename"] = str(resolve_log_file(filename))
    # 所有 formatter 套一层脱敏：日志允许保留技术细节（异常、堆栈、文件名），
    # 但不允许带出内部政策编号与本机绝对路径。安装版留下的旧配置副本也走这里，
    # 不需要用户手动升级配置。
    formatters = config.get("formatters")
    if isinstance(formatters, dict):
        for formatter in formatters.values():
            if isinstance(formatter, dict) and "()" not in formatter and "class" not in formatter:
                formatter["()"] = f"{__name__}.RedactingFormatter"
    return config


class RedactingFormatter(logging.Formatter):
    """日志落盘前的脱敏：保留技术细节，去掉内部编号与本机绝对路径。

    ``error.log`` 是排障入口，异常类型、堆栈行号、文件名都要保留；但
    ``D:\\<仓库>\\.venv\\...`` 这类绝对路径和 ``OD-010`` 这类内部编号
    属于"不该离开本机"的信息，统一折叠成占位符。
    """

    def __init__(
        self,
        format: str | None = None,  # noqa: A002 - dictConfig 的键名就是 ``format``
        datefmt: str | None = None,
        style: str = "%",
        **kwargs: Any,
    ) -> None:
        # dictConfig 走自定义工厂时会把 ``format``/``datefmt`` 原样当 kwargs
        # 传进来；``logging.Formatter`` 的参数名是 ``fmt``，这里做一次映射。
        super().__init__(fmt=format, datefmt=datefmt, style=style, **kwargs)

    def format(self, record: logging.LogRecord) -> str:
        return redact_log_text(super().format(record))


class MultiProcessRotatingFileHandler(logging.Handler):
    """Size-rotated file handler that is safe when **several processes** share it.

    stdlib ``RotatingFileHandler`` keeps the file open and renames it during
    ``doRollover``. On Windows a file another process holds open cannot be
    renamed, so concurrent gateways got ``PermissionError [WinError 32]``,
    dropped the record being emitted, left ``--- Logging error ---`` lines on
    stdout, and let the file grow past ``maxBytes`` (实测 314 次 / 单实例).

    Contract implemented here:

    * the file is open only for the duration of one append, so a rename never
      collides with another process's handle;
    * size check + rollover + append happen under an OS-wide lock file,
      so two processes cannot interleave a rollover with a write;
    * a record is never dropped: if rotation fails, the record is still
      appended to the current file (the size bound may be exceeded until the
      next successful rollover -- retaining the record wins).
    """

    def __init__(
        self,
        filename: str,
        maxBytes: int = 10 * 1024 * 1024,
        backupCount: int = 5,
        encoding: str = "utf-8",
        delay: bool = False,
    ) -> None:
        super().__init__()
        self.baseFilename = os.path.abspath(os.fspath(filename))
        self.maxBytes = int(maxBytes)
        self.backupCount = int(backupCount)
        self.encoding = encoding
        # 兼容旧配置里的 ``delay``：本实现每次 emit 才打开文件，天然"延迟"。
        self.delay = delay
        self._lock_path = self.baseFilename + ".lock"
        self._directory = os.path.dirname(self.baseFilename)
        self._warned_unwritable = False
        self.terminator = "\n"

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record) + self.terminator
        except Exception:  # noqa: BLE001 - 格式化失败由 logging 自己上报
            self.handleError(record)
            return
        try:
            with exclusive_file_lock(self._lock_path, description="日志"):
                self._write_locked(message)
        except Exception:  # noqa: BLE001 - 锁超时/轮转失败：宁可超限也不丢记录
            self._write_unlocked(message)

    def _write_locked(self, message: str) -> None:
        incoming = len(message.encode(self.encoding))
        if self._needs_rollover(incoming):
            self._rollover()
        self._append(message)

    def _write_unlocked(self, message: str) -> None:
        try:
            self._append(message)
        except Exception:  # noqa: BLE001 - 磁盘满/权限等，记录确实落不下
            # 刻意不调 ``handleError``：stdlib 会打出整段 "--- Logging error ---"
            # traceback 刷屏（实测单实例 314 次）。只提示一次，保留可诊断性。
            if not self._warned_unwritable:
                self._warned_unwritable = True
                sys.stderr.write(
                    f"[log] 无法写入 {self.baseFilename}（文件锁超时或磁盘/权限问题）\n"
                )

    def _append(self, message: str) -> None:
        if self._directory:
            os.makedirs(self._directory, exist_ok=True)
        with open(self.baseFilename, "a", encoding=self.encoding, newline="") as handle:
            handle.write(message)

    def _needs_rollover(self, incoming: int) -> bool:
        if self.maxBytes <= 0:
            return False
        try:
            size = os.path.getsize(self.baseFilename)
        except OSError:
            return False
        return size > 0 and size + incoming > self.maxBytes

    def _rollover(self) -> None:
        rotate_backups(self.baseFilename, self.backupCount)


def rotate_backups(base_filename: str, backup_count: int) -> None:
    """把 ``base_filename`` 滚成 ``.1``，旧的依次后移并丢弃最老的一份。

    调用方必须已经持有同一路径的 OS 写锁（见
    :class:`MultiProcessRotatingFileHandler` / :class:`RotatingTextWriter`）。
    """
    if backup_count <= 0:
        with contextlib.suppress(FileNotFoundError):
            os.remove(base_filename)
        _prune_extra_backups(base_filename, 0)
        return
    for index in range(backup_count - 1, 0, -1):
        source = f"{base_filename}.{index}"
        if os.path.exists(source):
            replace_with_retry(source, f"{base_filename}.{index + 1}")
    replace_with_retry(base_filename, f"{base_filename}.1")
    # 旧版本可能留下比当前 backupCount 更大的序号；不清掉它们，"占用有上限"
    # 就只是一句口号（例如从 backupCount=12 调到 5 后 .6~.12 会永久占用）。
    _prune_extra_backups(base_filename, backup_count)


_BACKUP_INDEX = re.compile(r"^\.(\d+)$")


def _prune_extra_backups(base_filename: str, backup_count: int) -> None:
    directory = os.path.dirname(base_filename) or "."
    prefix = os.path.basename(base_filename)
    try:
        entries = os.listdir(directory)
    except OSError:
        return
    for name in entries:
        if not name.startswith(prefix):
            continue
        match = _BACKUP_INDEX.match(name[len(prefix):])
        if match and int(match.group(1)) > backup_count:
            with contextlib.suppress(OSError):
                os.remove(os.path.join(directory, name))


def replace_with_retry(source: str, target: str) -> None:
    """``os.replace`` 短重试：读取方（诊断包等）瞬时占位是瞬态冲突。"""
    for attempt in range(8):
        try:
            os.replace(source, target)
            return
        except OSError:
            if attempt == 7:
                raise
            time.sleep(0.02 * (attempt + 1))


#: 旧版本（≤ 2026-11-08）把子进程 stdout 原样写进 ``desktop-gateway.log``，
#: 应用日志里也留有本机路径。一次性清理完成后在日志目录落这个 marker；
#: 它带上 ``REDACTION_VERSION``，规则再扩展时会自动重新扫描一轮。
LEGACY_SCRUB_MARKER_PREFIX = ".redaction-scrub-v"

#: 清理范围：只认日志文件（含 ``.1`` / ``.2`` 备份），不碰数据库与导出包。
_SCRUB_LOG_NAMES = (
    "liuhao.log",
    "error.log",
    "launcher.log",
    "bootstrap.log",
    "desktop.log",
    "desktop-gateway.log",
)
_SCRUB_BACKUP = re.compile(r"^\.\d+$")

#: 单个文件扫描上限。一次性清理不能把启动拖成磁盘全扫；更大的文件交给
#: 轮转自然滚出即可。
_MAX_SCRUB_BYTES = 64 * 1024 * 1024


def scrub_log_file(path: Path, *, max_bytes: int = _MAX_SCRUB_BYTES) -> bool:
    """就地脱敏一个历史日志文件；返回是否有内容被改写。

    只按 UTF-8 读取（``errors="replace"`` 容忍旧编码残留）。原子改写
    （同目录临时文件 + ``os.replace``）保证中途失败不会留下半份日志。
    """
    try:
        if not path.is_file() or path.stat().st_size > max_bytes:
            return False
    except OSError:
        return False
    try:
        with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            original = handle.read()
    except OSError:
        return False
    cleaned = redact_log_text(original)
    if cleaned == original:
        return False
    temp = path.with_name(f".{path.name}.scrub.tmp")
    try:
        with temp.open("w", encoding="utf-8", newline="") as handle:
            handle.write(cleaned)
        replace_with_retry(str(temp), str(path))
        return True
    except OSError:
        with contextlib.suppress(OSError):
            temp.unlink()
    # Windows 上文件被另一个句柄占用时不允许改名（``WinError 32``）。桌面壳
    # 自己的 ``desktop.log`` 在启动清理时就是这种状态，退回就地改写：写入方
    # 以 append 模式打开，下次写会落到新的文件末尾。
    try:
        with path.open("r+", encoding="utf-8", errors="replace", newline="") as handle:
            handle.seek(0)
            handle.write(cleaned)
            handle.truncate()
    except OSError:
        return False
    return True


def scrub_legacy_log_leaks(log_dir: Path | None = None) -> list[Path]:
    """一次性清理历史日志里的明文；返回被就地改写的文件列表。

    ``log_dir`` 省略时用 ``liuhao.log`` 的落点（即数据目录的日志目录）。
    目录不存在、或当前 ``REDACTION_VERSION`` 的 marker 已存在时直接返回。
    """
    directory = (
        Path(log_dir)
        if log_dir is not None
        else resolve_log_file(Path("logs") / "liuhao.log").parent
    )
    if not directory.is_dir():
        return []
    marker = directory / f"{LEGACY_SCRUB_MARKER_PREFIX}{REDACTION_VERSION}"
    if marker.exists():
        return []
    try:
        entries = list(directory.iterdir())
    except OSError:
        return []
    rewritten: list[Path] = []
    for entry in entries:
        try:
            if not entry.is_file():
                continue
        except OSError:
            continue
        name = entry.name
        for log_name in _SCRUB_LOG_NAMES:
            if name == log_name or (
                name.startswith(log_name) and _SCRUB_BACKUP.match(name[len(log_name):])
            ):
                if scrub_log_file(entry):
                    rewritten.append(entry)
                break
    with contextlib.suppress(OSError):
        marker.write_text(f"redaction_version={REDACTION_VERSION}\n", encoding="utf-8")
    return rewritten


# --------------------------------------------------------------------------- #
# 保留策略：按天 + 按量双上限
# --------------------------------------------------------------------------- #

#: 可用环境变量覆盖；默认保守：14 天、每个日志目录最多 256 MiB。
RETENTION_DAYS_ENV = "LIUHAO_LOG_RETENTION_DAYS"
RETENTION_BYTES_ENV = "LIUHAO_LOG_RETENTION_BYTES"
DEFAULT_RETENTION_DAYS = 14
DEFAULT_RETENTION_BYTES = 256 * 1024 * 1024

#: 最近一次清理的报告（放日志目录；只含文件名与字节数，可进诊断包）。
RETENTION_REPORT_NAME = "retention.json"

#: 界面上保存的保留偏好；与上面的 env 同时存在时 env 优先（界面会明示）。
RETENTION_SETTINGS_ENV = "LIUHAO_LOG_RETENTION_SETTINGS_FILE"
RETENTION_SETTINGS_NAME = "log_retention_settings.json"

_ACTIVE_LOG = re.compile(r".+\.log$")
_BACKUP_LOG = re.compile(r".+\.log\.\d+$")


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def retention_settings_path() -> Path:
    """界面保存的保留偏好文件（安装版落在用户数据目录）。"""
    raw = (os.environ.get(RETENTION_SETTINGS_ENV) or "").strip()
    if raw:
        return Path(raw).expanduser()
    return resolve_data_dir() / RETENTION_SETTINGS_NAME


def _read_retention_settings() -> dict[str, Any]:
    try:
        payload = json.loads(read_text_with_retry(retention_settings_path()))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _stored_int(payload: dict[str, Any], key: str, default: int) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(0, value)


def save_retention_limits(retention_days: int, max_total_bytes: int) -> dict[str, int]:
    """持久化界面上的保留设置；重启后仍生效。非法值按 0（不清理）处理。"""
    days = max(0, int(retention_days))
    cap = max(0, int(max_total_bytes))
    payload = _read_retention_settings()
    payload["retention_days"] = days
    payload["max_total_bytes"] = cap
    payload["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    target = retention_settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(target, json.dumps(payload, ensure_ascii=False, indent=2))
    return {"retention_days": days, "max_total_bytes": cap}


def retention_env_overrides() -> dict[str, bool]:
    """哪些维度正被环境变量覆盖（界面需要明示「改了也不生效」）。"""
    return {
        "retention_days": bool((os.environ.get(RETENTION_DAYS_ENV) or "").strip()),
        "max_total_bytes": bool((os.environ.get(RETENTION_BYTES_ENV) or "").strip()),
    }


def retention_limits() -> tuple[int, int]:
    """读取保留策略：``(天数, 总字节上限)``；0 表示该维度不清理。

    优先级：环境变量 > 界面保存的设置 > 默认值。后者让「改完重启仍生效」
    成立，前者给运维保留强制覆盖能力。
    """
    stored = _read_retention_settings()
    return (
        _env_int(
            RETENTION_DAYS_ENV,
            _stored_int(stored, "retention_days", DEFAULT_RETENTION_DAYS),
        ),
        _env_int(
            RETENTION_BYTES_ENV,
            _stored_int(stored, "max_total_bytes", DEFAULT_RETENTION_BYTES),
        ),
    )


def log_usage(log_dir: Path | None = None) -> dict[str, Any]:
    """统计一个日志目录的日志占用（只算 ``*.log`` 与 ``*.log.N``）。"""
    directory = (
        Path(log_dir)
        if log_dir is not None
        else resolve_log_file(Path("logs") / "liuhao.log").parent
    )
    active_bytes = backup_bytes = 0
    active_count = backup_count = 0
    for entry in _retention_candidates(directory):
        try:
            size = entry.stat().st_size
        except OSError:
            continue
        if _ACTIVE_LOG.match(entry.name):
            active_count += 1
            active_bytes += size
        else:
            backup_count += 1
            backup_bytes += size
    return {
        "log_dir": str(directory),
        "total_bytes": active_bytes + backup_bytes,
        "active_bytes": active_bytes,
        "backup_bytes": backup_bytes,
        "active_count": active_count,
        "backup_count": backup_count,
    }


def _retention_candidates(directory: Path) -> list[Path]:
    try:
        entries = list(directory.iterdir())
    except OSError:
        return []
    result: list[Path] = []
    for entry in entries:
        try:
            if not entry.is_file():
                continue
        except OSError:
            continue
        if _ACTIVE_LOG.match(entry.name) or _BACKUP_LOG.match(entry.name):
            result.append(entry)
    return result


def _remove_with_reason(path: Path) -> str | None:
    """删除一个备份；成功返回 ``None``，失败返回用户可读的原因。"""
    try:
        path.unlink()
    except FileNotFoundError:
        return None
    except PermissionError:
        return "文件被其它程序占用，或当前目录没有删除权限"
    except OSError as exc:
        return f"删除失败：{exc.strerror or exc.__class__.__name__}"
    return None


def _write_retention_report(directory: Path, report: dict[str, Any]) -> bool:
    temp = directory / f".{RETENTION_REPORT_NAME}.tmp"
    try:
        temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        replace_with_retry(str(temp), str(directory / RETENTION_REPORT_NAME))
        return True
    except OSError:
        with contextlib.suppress(OSError):
            temp.unlink()
        return False


def prune_log_retention(
    log_dir: Path | None = None,
    *,
    retention_days: int | None = None,
    max_total_bytes: int | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """按「天数 + 总字节」双上限清理日志目录，并写 ``retention.json`` 报告。

    规则（保守优先，绝不删正在写入的活动日志）：

    * 只删除轮转备份 ``*.log.N``；``*.log`` 由轮转机制自己控制大小；
    * 先按 mtime 删掉超过 ``retention_days`` 的备份（0 = 不按天清理）；
    * 再按 mtime 从最旧开始删，直到日志总字节不超过 ``max_total_bytes``
      （0 = 不按量清理）；
    * 报告写明保留了什么、删了什么、哪些没删掉及原因，只含文件名与字节数，
      可直接进入诊断包。
    """
    directory = (
        Path(log_dir)
        if log_dir is not None
        else resolve_log_file(Path("logs") / "liuhao.log").parent
    )
    default_days, default_bytes = retention_limits()
    days = default_days if retention_days is None else max(0, retention_days)
    cap = default_bytes if max_total_bytes is None else max(0, max_total_bytes)
    stamp = time.time() if now is None else now

    report: dict[str, Any] = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "policy": (
            f"只删除轮转备份（*.log.N）：超过 {days} 天或目录内日志总量超过 "
            f"{cap} 字节时从最旧开始清理；活动日志不删除。0 表示该维度不清理。"
        ),
        "retention_days": days,
        "max_total_bytes": cap,
        "env_override": retention_env_overrides(),
        "kept": [],
        "deleted": [],
        "failed": [],
        "total_bytes_before": 0,
        "total_bytes_after": 0,
    }
    if not directory.is_dir():
        report["status"] = "日志目录不存在，未执行"
        report["notice"] = "没有找到日志目录，本次没有执行清理。"
        report["report_written"] = False
        return report

    candidates = _retention_candidates(directory)
    sizes: dict[Path, int] = {}
    mtimes: dict[Path, float] = {}
    for path in candidates:
        try:
            stat = path.stat()
        except OSError:
            continue
        sizes[path] = stat.st_size
        mtimes[path] = stat.st_mtime
    report["total_bytes_before"] = sum(sizes.values())

    deleted: list[Path] = []
    reasons: dict[Path, str] = {}
    failed: dict[Path, str] = {}
    for path in sorted(candidates, key=lambda item: mtimes.get(item, stamp)):
        if not _BACKUP_LOG.match(path.name):
            continue
        age_days = (stamp - mtimes.get(path, stamp)) / 86400.0
        if days > 0 and age_days > days:
            error = _remove_with_reason(path)
            if error is None:
                deleted.append(path)
                reasons[path] = f"超过保留天数（{age_days:.1f} 天 > {days} 天）"
            else:
                failed[path] = error

    remaining = [
        path for path in candidates if path not in deleted and path.exists()
    ]
    total = sum(sizes.get(path, 0) for path in remaining)
    if cap > 0:
        for path in sorted(
            (item for item in remaining if _BACKUP_LOG.match(item.name)),
            key=lambda item: mtimes.get(item, stamp),
        ):
            if total <= cap:
                break
            if path in failed:
                continue
            size = sizes.get(path, 0)
            error = _remove_with_reason(path)
            if error is None:
                total -= size
                deleted.append(path)
                reasons[path] = f"超过总量上限（目录内共 {total + size} 字节 > {cap} 字节）"
            else:
                failed[path] = error

    kept = [path for path in candidates if path.exists() and path not in deleted]
    report["total_bytes_after"] = sum(
        sizes.get(path, 0) for path in kept if path.exists()
    )
    report["deleted"] = [
        {
            "name": path.name,
            "bytes": sizes.get(path, 0),
            "reason": reasons.get(path, "清理"),
        }
        for path in sorted(deleted, key=lambda item: item.name)
    ]
    report["kept"] = [
        {
            "name": path.name,
            "bytes": sizes.get(path, 0),
            "age_days": round((stamp - mtimes.get(path, stamp)) / 86400.0, 2),
            "active": bool(_ACTIVE_LOG.match(path.name)),
        }
        for path in sorted(kept, key=lambda item: item.name)
    ]
    report["failed"] = [
        {
            "name": path.name,
            "bytes": sizes.get(path, 0),
            "reason": failed[path],
        }
        for path in sorted(failed, key=lambda item: item.name)
    ]

    if failed:
        report["status"] = "部分完成"
        report["notice"] = (
            f"{len(failed)} 份日志备份没能删除（可能正被其它程序占用，或目录没有"
            f"删除权限）；其余已按策略处理，正在写入的日志未受影响。"
        )
    elif deleted:
        report["status"] = "ok"
        report["notice"] = (
            f"已清理 {len(deleted)} 份日志备份，正在写入的日志未受影响。"
        )
    else:
        report["status"] = "ok"
        report["notice"] = "没有需要清理的日志备份。"

    report["report_written"] = True
    written = _write_retention_report(directory, report)
    report["report_written"] = written
    if not written:
        report["notice"] = (
            f"{report['notice']} 清理报告未能写入（日志目录可能只读），"
            "本次结果仅在本进程内可见。"
        )
    return report


def read_retention_report(log_dir: Path) -> dict[str, Any] | None:
    """读最近一次清理报告；不存在或损坏时返回 ``None``。"""
    try:
        payload = json.loads((Path(log_dir) / RETENTION_REPORT_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


class RotatingTextWriter:
    """启动器日志（``launcher.log``）的有界写入器：与网关日志同一套上限。

    启动器日志是 stdout/stderr 的镜像，不是 ``logging`` 记录，所以不能直接挂
    ``logging.Handler``。契约与 :class:`MultiProcessRotatingFileHandler` 一致：

    * 每次 ``write`` 才打开文件，并在 OS 文件锁内完成「看大小 → 轮转 → 追加」，
      另一个进程/另一个启动器实例不会撞上 Windows 的改名共享冲突；
    * **只在行首轮转**，一条记录不会被拆到两个文件里；
    * 落盘前走 :func:`redact_log_text`：启动器会原样打印 argv、数据目录与
      子进程输出，这些内容里带本机用户名路径与环境变量名，不能留给用户转发。
    """

    def __init__(
        self,
        filename: str,
        maxBytes: int = 10 * 1024 * 1024,
        backupCount: int = 3,
        encoding: str = "utf-8",
        redact: bool = True,
    ) -> None:
        self.baseFilename = os.path.abspath(os.fspath(filename))
        self.maxBytes = int(maxBytes)
        self.backupCount = int(backupCount)
        self.encoding = encoding
        self.redact = redact
        self._lock_path = self.baseFilename + ".lock"
        self._directory = os.path.dirname(self.baseFilename)
        self._at_line_start = True

    def write(self, data: str) -> int:
        if not data:
            return 0
        text = redact_log_text(data) if self.redact else data
        incoming = len(text.encode(self.encoding))
        try:
            with exclusive_file_lock(self._lock_path, description="启动器日志"):
                if self._at_line_start and self._needs_rollover(incoming):
                    rotate_backups(self.baseFilename, self.backupCount)
                self._append(text)
        except Exception:  # noqa: BLE001 - 日志写不下去也不能打断启动流程
            with contextlib.suppress(OSError):
                self._append(text)
        self._at_line_start = text.endswith(("\n", "\r"))
        return len(data)

    def flush(self) -> None:
        # 每次 write 都是 open/append/close，无需额外 flush。
        return None

    def close(self) -> None:
        return None

    def isatty(self) -> bool:
        return False

    def _needs_rollover(self, incoming: int) -> bool:
        if self.maxBytes <= 0:
            return False
        try:
            size = os.path.getsize(self.baseFilename)
        except OSError:
            return False
        return size > 0 and size + incoming > self.maxBytes

    def _append(self, text: str) -> None:
        if self._directory:
            os.makedirs(self._directory, exist_ok=True)
        with open(self.baseFilename, "a", encoding=self.encoding, newline="") as handle:
            handle.write(text)



def resolve_log_config_path(default: Path | None = None) -> Path:
    """Return the configured log-config path (env override wins)."""
    raw = (os.environ.get(LOG_CONFIG_ENV) or "").strip()
    if raw:
        return Path(raw).expanduser()
    return default or DEFAULT_LOG_CONFIG


def ensure_utf8_streams() -> None:
    """被重定向时按 UTF-8 写出，供「日志/诊断」跨工具读取。

    交互式控制台保持系统编码（中文 Windows 的 cp936 控制台仍能正常显示中文）；
    一旦 stdout/stderr 被重定向到文件（桌面壳的 ``desktop-gateway.log``、
    运维把启动器输出存盘），就切成 UTF-8，避免中文被写成 cp936 后其它工具
    按 UTF-8 解码得到替换字符。
    """
    for stream in (sys.stdout, sys.stderr):
        if stream is None or stream.isatty():
            continue
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
