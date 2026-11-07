"""独立于业务数据目录的引导日志（``bootstrap.log``）。

为什么需要它
------------
``liuhao.log`` / ``error.log`` 都写在数据目录（或 ``LIUHAO_LOG_DIR``）下。
当那个目录不可写时，用户得到的证据只有控制台的一句中文提示——重启一次、
窗口一关就没了，而且不含路径与错误码。这个模块的存在只有一个目的：

    在"数据目录和日志目录都不可写"时，仍然留下一条可追溯的记录。

落点与业务数据无关，按优先级尝试：

1. ``LIUHAO_BOOTSTRAP_LOG``（显式指定，测试与安装器使用）；
2. ``%LOCALAPPDATA%\\LiuHao\\logs\\bootstrap.log``（正常用户目录）；
3. ``%TEMP%\\LiuHao\\bootstrap.log``（用户目录也不可写时）。

写入永远不抛异常：引导日志本身失败，绝不能再把启动流程带崩。
每条记录是单行 ``key=value``，与 ``liuhao.log`` 里的事件格式一致。
"""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

from .common.redaction import mask_user_path

BOOTSTRAP_LOG_ENV = "LIUHAO_BOOTSTRAP_LOG"


def _candidates() -> list[Path]:
    explicit = (os.environ.get(BOOTSTRAP_LOG_ENV) or "").strip()
    if explicit:
        return [Path(explicit).expanduser()]
    candidates: list[Path] = []
    local_appdata = (os.environ.get("LOCALAPPDATA") or "").strip()
    if local_appdata:
        candidates.append(Path(local_appdata) / "LiuHao" / "logs" / "bootstrap.log")
    temp = (os.environ.get("TEMP") or os.environ.get("TMP") or "").strip()
    if temp:
        candidates.append(Path(temp) / "LiuHao" / "bootstrap.log")
    else:
        candidates.append(Path(tempfile.gettempdir()) / "LiuHao" / "bootstrap.log")
    return candidates


def bootstrap_log_path() -> Path:
    """首选写入位置（即使当前不可写也返回，供提示用户去哪里找）。"""
    return _candidates()[0]


def _format_value(value: Any) -> str:
    if value is None:
        return "-"
    text = str(value).replace("\r", " ").replace("\n", " ")
    if not text:
        return '""'
    if any(ch.isspace() for ch in text) or '"' in text:
        return '"' + text.replace('"', "'") + '"'
    return text


def write_event(event: str, **fields: Any) -> str:
    """追加一条引导事件；返回实际写入的文件路径（全部失败时空串）。

    路径先做用户名脱敏（``C:\\Users\\<用户>\\...``），目录结构与错误码保留。
    """
    parts = [time.strftime("%Y-%m-%d %H:%M:%S"), f"evt={event}"]
    for key, value in fields.items():
        if value is None:
            continue
        if key in ("path", "log_dir", "data_dir") and isinstance(value, (str, os.PathLike)):
            value = mask_user_path(str(value))
        parts.append(f"{key}={_format_value(value)}")
    line = " ".join(parts) + "\n"

    for target in _candidates():
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "a", encoding="utf-8", newline="") as handle:
                handle.write(line)
            return str(target)
        except OSError:
            continue
    return ""


def read_bootstrap_log(max_lines: int = 20) -> Optional[str]:
    """读回最后若干行（诊断/演练用；不存在返回 None）。"""
    for target in _candidates():
        try:
            if target.is_file():
                lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
                return "\n".join(lines[-max_lines:])
        except OSError:
            continue
    return None


__all__ = [
    "BOOTSTRAP_LOG_ENV",
    "bootstrap_log_path",
    "read_bootstrap_log",
    "write_event",
]
