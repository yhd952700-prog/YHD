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

import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

import yaml

from .common.atomic_write import exclusive_file_lock

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
    return _normalize_runtime_config(document)


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
    """把配置里的日志文件名钉到数据目录/实例目录，绝不落仓库根。"""
    path = Path(filename).expanduser()
    if path.is_absolute():
        return path
    raw_log_dir = (os.environ.get(LOG_DIR_ENV) or "").strip()
    if raw_log_dir:
        return Path(raw_log_dir).expanduser().resolve() / path.name
    return resolve_data_dir() / path


def _normalize_runtime_config(config: dict[str, Any]) -> dict[str, Any]:
    handlers = config.get("handlers")
    if not isinstance(handlers, dict):
        return config
    for handler in handlers.values():
        if not isinstance(handler, dict):
            continue
        if handler.get("class") == _STDLIB_ROTATING_CLASS:
            handler["class"] = f"{__name__}.MultiProcessRotatingFileHandler"
        filename = handler.get("filename")
        if isinstance(filename, str) and filename:
            handler["filename"] = str(resolve_log_file(filename))
    return config


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
        if self.backupCount <= 0:
            try:
                os.remove(self.baseFilename)
            except FileNotFoundError:
                pass
            return
        for index in range(self.backupCount - 1, 0, -1):
            source = f"{self.baseFilename}.{index}"
            if os.path.exists(source):
                self._replace(source, f"{self.baseFilename}.{index + 1}")
        self._replace(self.baseFilename, f"{self.baseFilename}.1")

    @staticmethod
    def _replace(source: str, target: str) -> None:
        """``os.replace`` 短重试：读取方（诊断包等）瞬时占位是瞬态冲突。"""
        for attempt in range(8):
            try:
                os.replace(source, target)
                return
            except OSError:
                if attempt == 7:
                    raise
                time.sleep(0.02 * (attempt + 1))



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
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, ValueError):
            pass
