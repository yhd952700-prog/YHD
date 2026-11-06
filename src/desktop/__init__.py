"""鎏灏（LIUHAO X）Windows 桌面集成层。

本包只做**系统层**支撑，不承载业务逻辑，也不导入或修改
``src/gateway``、``src/knowledge``、``src/automation``、``src/intel``、
``apps/console`` 与 ``installer`` 的实现。

模块职责：
  * ``tray``        — pywin32 系统托盘；Win32 消息循环跑在独立线程。
  * ``service``     — 网关子进程的启动 / 停止；托盘与主服务的唯一边界。
  * ``app``         — 状态窗口 + 托盘 + 主服务装配；关闭窗口 = 最小化到托盘。
  * ``autostart``   — ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run``
                      自启登记（默认关闭，绝不写 HKLM）。
  * ``firewall``    — 只读检测 + 用户自助指引；**绝不**修改防火墙或安全软件。
  * ``diagnostics`` — 一键诊断 zip；白名单生成 + 强制脱敏。
  * ``about``       — 版本 / 构建时间 / 数据目录读取入口（供关于页使用）。
"""

from __future__ import annotations

import os
from pathlib import Path

#: 仓库根目录（``src/desktop`` 的上两级）。
REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
LOG_DIR = REPO_ROOT / "logs"
CONSOLE_DIST = REPO_ROOT / "apps" / "console" / "console" / "dist"


#: 诊断包与桌面日志的默认落点（可用 ``LIUHAO_DIAGNOSTICS_DIR`` 覆盖）。
def diagnostics_dir() -> Path:
    """诊断包默认输出目录：``%LOCALAPPDATA%\\LiuHao\\diagnostics``。"""
    override = (os.environ.get("LIUHAO_DIAGNOSTICS_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    local = (os.environ.get("LOCALAPPDATA") or "").strip()
    if os.name == "nt" and local:
        return Path(local) / "LiuHao" / "diagnostics"
    return Path.home() / "LiuHao" / "diagnostics"
