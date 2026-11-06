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

import os
import sys
from pathlib import Path
from typing import Any

import yaml

#: Repo-root ``config/logging.yaml``. The installed build copies it into the
#: user data directory and points ``LIUHAO_LOG_CONFIG`` at that copy.
DEFAULT_LOG_CONFIG = Path(__file__).resolve().parents[1] / "config" / "logging.yaml"

#: Lets a launcher point the gateway child at the installed copy without
#: passing a uvicorn ``--log-config`` path (which uvicorn would read as cp936).
LOG_CONFIG_ENV = "LIUHAO_LOG_CONFIG"


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


def load_uvicorn_log_config(path: str | os.PathLike[str] | None) -> dict[str, Any] | None:
    """Read a uvicorn YAML log config as UTF-8 and return its mapping.

    ``None``/missing file keeps uvicorn's built-in defaults (the previous
    behaviour of the gateway entry point). A malformed file raises instead of
    silently falling back: a config that does not load must be visible.
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
    return document
