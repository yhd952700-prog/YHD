"""F 组（0.4.6）共享小工具：可选引擎的\"没装就如实说\"错误类型。

语音（faster-whisper / Kokoro）、OCR（RapidOCR）、日历（icalendar /
caldav）都是**可选依赖**：没装、模型文件没下载时，调用方必须返回
503 + 中文原因 + 安装提示，绝不能编一个结果出来。这个错误类型就是
三处共用的载体。
"""

from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    """Jarvis 本机数据目录（与 ``src/gateway/lan_access.py`` 一致的顺序）。

    ``LIUHAO_DATA_DIR`` 优先；Windows 用 ``%LOCALAPPDATA%\\LiuHao\\data``；
    其余用 ``$XDG_DATA_HOME/liuhao`` 或 ``~/.local/share/liuhao``。
    语音/OCR 模型文件、日历与记账库都落在这里，**不在代码仓里**。
    """
    raw = (os.environ.get("LIUHAO_DATA_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
        return base / "LiuHao" / "data"
    base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return base / "liuhao"


class EngineUnavailableError(RuntimeError):
    """引擎没装 / 模型文件没下载：如实报 503，不编结果。

    ``detail`` 是给用户看的中文原因；``install_hint`` 是下一步怎么做
    （装哪个包、模型去哪下、放哪），前端原样展示。
    """

    def __init__(self, detail: str, install_hint: str = "") -> None:
        super().__init__(detail)
        self.detail = detail
        self.install_hint = install_hint
