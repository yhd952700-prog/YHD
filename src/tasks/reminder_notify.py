"""提醒的可见通知通道。

要求是"用户真的看到"，而不是日志里一行字。这里给出两条真实通道：

* **Windows 托盘气泡**（``WindowsBalloonNotifier``）—— 通过 PowerShell 调
  ``System.Windows.Forms.NotifyIcon.ShowBalloonTip``，从**任意进程**都能弹，
  不依赖桌面主程序是否在前台（``src/desktop/tray.py`` 的托盘只在桌面进程内，
  不做跨进程业务通知，所以不能复用）。
* **控制台通知 + 落地出站队列**（``ConsoleOutboxNotifier``）—— 追加一行 JSON
  到 ``reminders_outbox.jsonl`` 并向 stderr 打一条醒目横幅；即使桌面会话不可用
  （服务账户 / 无托盘），提醒仍然可见、可追溯。

``MultiNotifier`` 会依次尝试所有通道，任一通道成功即视为"用户看到了"，
并逐通道记录结果，失败原因写进投递记录（可在"失败提醒"里查到）。

测试与无桌面环境可用 ``LIUHAO_REMINDER_OS_NOTIFY=0`` 关掉托盘气泡，
只保留控制台通道（不影响产品默认行为）。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from typing import List, Optional, Protocol

from src._time import utc_now

_TRUTHY = {"1", "true", "yes", "on", "enabled"}


@dataclass
class NotifyResult:
    channel: str
    ok: bool
    detail: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


class Notifier(Protocol):
    channel: str

    def notify(self, title: str, body: str) -> List[NotifyResult]: ...


def default_reminder_outbox() -> str:
    explicit = os.environ.get("LIUHAO_REMINDER_OUTBOX")
    if explicit:
        return explicit
    workspace = os.environ.get("LIUHAO_WORKSPACE_ROOT")
    base = (
        os.path.join(workspace, "queue")
        if workspace
        else os.path.join(os.path.expanduser("~"), ".liuhao", "queue")
    )
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "reminders_outbox.jsonl")


class ConsoleOutboxNotifier:
    """落地 + 控制台横幅：无桌面时也能让用户看到。"""

    channel = "console"

    def __init__(self, outbox_path: Optional[str] = None) -> None:
        self.outbox_path = outbox_path or default_reminder_outbox()

    def notify(self, title: str, body: str) -> List[NotifyResult]:
        record = {
            "created_at": utc_now().isoformat(),
            "title": title,
            "body": body,
        }
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.outbox_path)) or ".", exist_ok=True)
            with open(self.outbox_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            return [NotifyResult(self.channel, False, f"outbox write failed: {exc}")]
        banner = f"\n===== 鎏灏提醒 =====\n{title}\n{body}\n====================\n"
        try:
            print(banner, file=sys.stderr, flush=True)
        except Exception:  # noqa: BLE001 - 控制台不可用不影响落盘
            pass
        return [NotifyResult(self.channel, True, self.outbox_path)]


_PS_SCRIPT = r"""
param([Parameter(Mandatory=$true)][string]$PayloadPath)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$payload = Get-Content -LiteralPath $PayloadPath -Raw -Encoding UTF8 | ConvertFrom-Json
$icon = New-Object System.Windows.Forms.NotifyIcon
$icon.Icon = [System.Drawing.SystemIcons]::Information
$icon.Visible = $true
$icon.ShowBalloonTip(10000, [string]$payload.title, [string]$payload.body, [System.Windows.Forms.ToolTipIcon]::Info)
Start-Sleep -Milliseconds 1200
$icon.Dispose()
Write-Output "BALLOON_OK"
"""


class WindowsBalloonNotifier:
    """从任意进程弹 Windows 托盘气泡（PowerShell + NotifyIcon）。"""

    channel = "windows_balloon"

    def __init__(self, timeout_seconds: float = 25.0) -> None:
        self.timeout_seconds = timeout_seconds
        self._script_path: Optional[str] = None

    def _script(self) -> str:
        if self._script_path and os.path.exists(self._script_path):
            return self._script_path
        path = os.path.join(
            tempfile.gettempdir(), f"liuhao_reminder_balloon_{os.getpid()}.ps1"
        )
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(_PS_SCRIPT)
        self._script_path = path
        return path

    def notify(self, title: str, body: str) -> List[NotifyResult]:
        if os.name != "nt":
            return [NotifyResult(self.channel, False, "not Windows")]
        executable = shutil.which("powershell") or shutil.which("powershell.exe")
        if not executable:
            return [NotifyResult(self.channel, False, "powershell not found")]

        # 气泡标题/正文有长度限制，超长会被系统截断甚至拒绝。
        payload = {"title": str(title)[:63], "body": str(body)[:255]}
        handle, payload_path = tempfile.mkstemp(prefix="liuhao_reminder_", suffix=".json")
        os.close(handle)
        try:
            with open(payload_path, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False)
            completed = subprocess.run(
                [
                    executable,
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    self._script(),
                    payload_path,
                ],
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return [NotifyResult(self.channel, False, f"balloon failed: {exc}")]
        finally:
            try:
                os.unlink(payload_path)
            except OSError:
                pass

        ok = completed.returncode == 0 and "BALLOON_OK" in (completed.stdout or "")
        detail = "BALLOON_OK" if ok else (
            f"rc={completed.returncode} stderr={(completed.stderr or '').strip()[:300]}"
        )
        return [NotifyResult(self.channel, ok, detail)]


class MultiNotifier:
    """按顺序尝试多个通道；任一成功即视为用户看到了。"""

    channel = "multi"

    def __init__(self, notifiers: List[Notifier]) -> None:
        self.notifiers = list(notifiers)

    def notify(self, title: str, body: str) -> List[NotifyResult]:
        results: List[NotifyResult] = []
        for notifier in self.notifiers:
            try:
                results.extend(notifier.notify(title, body))
            except Exception as exc:  # noqa: BLE001 - 单通道异常不能拖垮其它通道
                results.append(
                    NotifyResult(
                        getattr(notifier, "channel", "unknown"),
                        False,
                        f"{type(exc).__name__}: {exc}",
                    )
                )
        return results


def build_default_notifier(outbox_path: Optional[str] = None) -> MultiNotifier:
    """默认：Windows 上托盘气泡 + 控制台；其它平台只有控制台。"""
    notifiers: List[Notifier] = []
    if os.name == "nt" and _os_notify_enabled():
        notifiers.append(WindowsBalloonNotifier())
    notifiers.append(ConsoleOutboxNotifier(outbox_path))
    return MultiNotifier(notifiers)


def _os_notify_enabled() -> bool:
    raw = (os.environ.get("LIUHAO_REMINDER_OS_NOTIFY") or "1").strip().lower()
    return raw in _TRUTHY


__all__ = [
    "ConsoleOutboxNotifier",
    "MultiNotifier",
    "NotifyResult",
    "Notifier",
    "WindowsBalloonNotifier",
    "build_default_notifier",
    "default_reminder_outbox",
]
