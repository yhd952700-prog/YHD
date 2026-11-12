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
import threading
import time
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

    def notify(
        self,
        title: str,
        body: str,
        *,
        idempotency_key: Optional[str] = None,
    ) -> List[NotifyResult]:
        ...


def maybe_test_crash(point: str) -> None:
    """仅测试/冒烟：在指定投递阶段模拟进程硬死。

    ``LIUHAO_TEST_FAULT_FILE`` 指向一个文本文件，内容是要命中的点
    （``before_notify`` / ``during_notify`` / ``after_notify``）。命中后先打印
    ``FAULT_READY`` 再卡住，等外部 ``kill -9``（超时则硬退出）。未设置该环境
    变量或内容不匹配时**什么都不做**，生产行为零变化。
    """

    path = os.environ.get("LIUHAO_TEST_FAULT_FILE")
    if not path:
        return
    try:
        with open(path, "r", encoding="utf-8") as handle:
            target = handle.read().strip()
    except OSError:
        return
    if target != point:
        return
    print(f"FAULT_READY point={point} pid={os.getpid()}", file=sys.stderr, flush=True)
    time.sleep(120)
    # 外部没能及时 kill 时自己硬退出：带着"通知已发出、终态未落盘"的半截状态
    # 继续跑会让后续判断失真，宁可让进程直接死掉。
    os._exit(98)


def default_reminder_outbox() -> str:
    """出站队列（JSONL）落点；旧位置有内容时按逐字节校验迁移。"""
    from src.tasks._queue_paths import resolve_queue_file

    return resolve_queue_file(
        "reminders_outbox.jsonl",
        env_var="LIUHAO_REMINDER_OUTBOX",
        kind="text",
    )


def _no_window_flag() -> int:
    """让子进程不弹控制台窗口；非 Windows 上该参数无意义，返回 0。"""
    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def _outbox_has_key(path: str, idempotency_key: str) -> bool:
    """出站队列里是否已有同一幂等键（O(行数)，提醒量级下可忽略）。"""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                if idempotency_key not in line:
                    continue
                try:
                    payload = json.loads(line)
                except ValueError:
                    continue
                if payload.get("idempotency_key") == idempotency_key:
                    return True
    except OSError:
        return False
    return False


class ConsoleOutboxNotifier:
    """落地 + 控制台横幅：无桌面时也能让用户看到。"""

    channel = "console"

    def __init__(self, outbox_path: Optional[str] = None) -> None:
        self.outbox_path = outbox_path or default_reminder_outbox()

    def notify(
        self,
        title: str,
        body: str,
        *,
        idempotency_key: Optional[str] = None,
    ) -> List[NotifyResult]:
        if idempotency_key and _outbox_has_key(self.outbox_path, idempotency_key):
            # 同一幂等键已经落过盘：说明这条提醒在本进程或上一个进程里已经响过，
            # 不再重复打印横幅 / 追加第二行。
            return [
                NotifyResult(
                    self.channel,
                    True,
                    f"duplicate suppressed: {idempotency_key}",
                )
            ]
        record = {
            "created_at": utc_now().isoformat(),
            "title": title,
            "body": body,
        }
        if idempotency_key:
            record["idempotency_key"] = idempotency_key
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.outbox_path)) or ".", exist_ok=True)
            with open(self.outbox_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            return [NotifyResult(self.channel, False, f"outbox write failed: {exc}")]
        banner = f"\n===== {title} =====\n{body}\n{'=' * (len(title) + 12)}\n"
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
# 3 秒：短于 1.5 秒时 Win10/11 转成的 toast 常常还没来得及进通知中心就被回收，
# 用户只看到托盘图标闪一下。该进程由调用方后台线程承担，不会卡住主流程。
Start-Sleep -Milliseconds 3000
$icon.Dispose()
Write-Output "BALLOON_OK"
# 异步（合并）模式下由本进程自己清理 payload；同步模式下调用方也会删，
# 重复删除用 SilentlyContinue 忽略。
Remove-Item -LiteralPath $PayloadPath -Force -ErrorAction SilentlyContinue
"""


class WindowsBalloonNotifier:
    """从任意进程弹 Windows 托盘气泡（PowerShell + NotifyIcon）。

    排程路径（带 ``idempotency_key``）**不阻塞**：到点只把事件投进内存队列，
    由后台线程在 0.5 秒合并窗口内弹**一条**摘要气泡；逐条明细已经由 outbox
    落盘，所以气泡失败/合并都不影响"提醒被记录"。手工/测试路径（不带 key）
    仍同步等待 ``BALLOON_OK``，便于确认通道真实可用。
    """

    channel = "windows_balloon"
    MERGE_WINDOW_SECONDS = 0.5

    def __init__(self, timeout_seconds: float = 25.0) -> None:
        self.timeout_seconds = timeout_seconds
        self._script_path: Optional[str] = None
        self._pending: List[tuple] = []
        self._pending_lock = threading.Lock()
        self._worker: Optional[threading.Thread] = None

    #: 遗留气泡脚本的保留天数。每个进程写一份，异常退出不会自动删除；
    #: 7 天后清理可以避免 ``%TEMP%`` 长期堆积，又不会误删仍在运行的实例
    #: （脚本被删后本进程下次调用会按下面的 ``exists`` 检查自动重建）。
    _STALE_SCRIPT_SECONDS = 7 * 24 * 3600

    def _script(self) -> str:
        if self._script_path and os.path.exists(self._script_path):
            return self._script_path
        directory = tempfile.gettempdir()
        path = os.path.join(directory, f"liuhao_reminder_balloon_{os.getpid()}.ps1")
        self._sweep_stale_scripts(directory, keep=path)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(_PS_SCRIPT)
        self._script_path = path
        return path

    def _sweep_stale_scripts(self, directory: str, *, keep: str) -> None:
        """清掉超过保留期的同类脚本（只在本进程写脚本时顺手清理）。"""
        cutoff = time.time() - self._STALE_SCRIPT_SECONDS
        try:
            candidates = os.listdir(directory)
        except OSError:
            return
        for name in candidates:
            if not name.startswith("liuhao_reminder_balloon_") or not name.endswith(
                ".ps1"
            ):
                continue
            stale = os.path.join(directory, name)
            if stale == keep:
                continue
            try:
                if os.path.getmtime(stale) < cutoff:
                    os.unlink(stale)
            except OSError:
                # 文件正被其他实例/杀软占用：下次再试，不能因为清理失败影响提醒。
                continue

    def notify(
        self,
        title: str,
        body: str,
        *,
        idempotency_key: Optional[str] = None,
    ) -> List[NotifyResult]:
        # 托盘气泡是系统级弹窗，没有可复用的去重 token；跨进程去重由调用方的
        # dispatch marker 保证（这里只接受参数，保持接口一致）。
        if idempotency_key:
            self._queue_balloon(str(title), str(body))
            return [
                NotifyResult(
                    self.channel,
                    True,
                    "queued (non-blocking, merged); outbox is the durable record",
                )
            ]
        return self._notify_now(str(title), str(body))

    def _queue_balloon(self, title: str, body: str) -> None:
        with self._pending_lock:
            self._pending.append((title, body))
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(
                target=self._drain,
                name="liuhao-reminder-balloon",
                daemon=True,
            )
            self._worker.start()

    def _drain(self) -> None:
        # 合并窗口：同一批到点的提醒只弹一条，避免 N 条各自阻塞/轰炸。
        time.sleep(self.MERGE_WINDOW_SECONDS)
        while True:
            with self._pending_lock:
                batch = self._pending
                self._pending = []
                if not batch:
                    self._worker = None
                    return
            if len(batch) == 1:
                title, body = batch[0]
            else:
                title = f"鎏昊提醒（{len(batch)} 条）"
                titles = [item[0] for item in batch[:5]]
                body = "；".join(titles)
                if len(batch) > 5:
                    body += f" 等 {len(batch)} 条（详见提醒清单）"
            try:
                self._notify_now(title, body)
            except Exception:  # noqa: BLE001 - 气泡失败不能拖垮后台线程
                pass
            time.sleep(self.MERGE_WINDOW_SECONDS)

    def _notify_now(self, title: str, body: str) -> List[NotifyResult]:
        if os.name != "nt":
            return [NotifyResult(self.channel, False, "not Windows")]
        executable = shutil.which("powershell") or shutil.which("powershell.exe")
        if not executable:
            return [NotifyResult(self.channel, False, "powershell not found")]

        # 气泡标题/正文有长度限制，超长会被系统截断甚至拒绝。
        payload = {"title": str(title)[:63], "body": str(body)[:255]}
        handle, payload_path = tempfile.mkstemp(
            prefix=f"liuhao_reminder_{os.getpid()}_", suffix=".json"
        )
        os.close(handle)
        try:
            with open(payload_path, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False)
            completed = subprocess.run(
                [
                    executable,
                    "-NoProfile",
                    "-NonInteractive",
                    "-WindowStyle",
                    "Hidden",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    self._script(),
                    payload_path,
                ],
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                # 网关/桌面壳都是无控制台进程；不加这个标志，每次提醒都会
                # 闪一个黑色 PowerShell 窗口，比提醒本身更打扰。
                creationflags=_no_window_flag(),
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

    def notify(
        self,
        title: str,
        body: str,
        *,
        idempotency_key: Optional[str] = None,
    ) -> List[NotifyResult]:
        results: List[NotifyResult] = []
        for notifier in self.notifiers:
            try:
                results.extend(
                    notifier.notify(title, body, idempotency_key=idempotency_key)
                )
            except Exception as exc:  # noqa: BLE001 - 单通道异常不能拖垮其它通道
                results.append(
                    NotifyResult(
                        getattr(notifier, "channel", "unknown"),
                        False,
                        f"{type(exc).__name__}: {exc}",
                    )
                )
            # 冒烟用：第一条通道成功后再卡住，模拟"响了一次、终态还没落盘"。
            if any(result.ok for result in results):
                maybe_test_crash("during_notify")
        return results


def build_default_notifier(outbox_path: Optional[str] = None) -> MultiNotifier:
    """默认：先写 outbox（持久记录），再尽力弹托盘气泡（异步合并、不阻塞）。"""
    notifiers: List[Notifier] = [ConsoleOutboxNotifier(outbox_path)]
    if os.name == "nt" and _os_notify_enabled():
        notifiers.append(WindowsBalloonNotifier())
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
