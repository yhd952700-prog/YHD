"""本机恢复码交接的进程级证明：通道命名与桌面端取令牌客户端。

威胁模型
--------
回环地址只证明“请求来自这台机器”，不证明“请求来自这个 Windows 账户”。
共享电脑上，另一个账户同样能连 ``127.0.0.1``，因此恢复码交接不能只靠
IP 回环，必须再加一层**进程级证明**：

* 网关进程内起一个带 DACL 的命名管道（POSIX 下是 0600 的 Unix socket），
  DACL 只授予当前 Windows 用户；其他账户连管道本身就被拒绝；
* 桌面壳/启动器通过该通道申请一次性令牌，再把令牌放进控制台 URL 的
  fragment；HTTP 接口只认令牌，不认“看起来像本机”；
* 令牌单次有效、带 TTL，并绑定到发起申请的进程（进程退出即失效）。

本模块只负责“怎么找到通道、怎么申请令牌”，令牌存储与校验在
:mod:`src.gateway.local_proof`，避免桌面端 import 网关模块。
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .integrity_key import resolve_data_dir

#: HTTP 头：交接令牌与重置令牌各一枚，前端按用途分别携带。
HANDOFF_HEADER = "X-LiuHao-Local-Proof"
RESET_HEADER = "X-LiuHao-Reset-Proof"

#: URL fragment 中的参数名（不会进入浏览器历史/服务器访问日志）。
HANDOFF_FRAGMENT_KEY = "liuhao-local-proof"
RESET_FRAGMENT_KEY = "liuhao-reset-proof"

#: 由桌面壳/启动器注入：表示这个网关进程是“可信本机入口”，首次注册、取码、
#: 重置都必须同时满足“回环 + 桌面端证明 + HTTP 对端与网关同用户”。
#: 直接用 ``python -m src.gateway`` 开发启动时不设置，只放宽为告警。
TRUSTED_LAUNCH_ENV = "LIUHAO_TRUSTED_LAUNCH"

_PIPE_PREFIX = "liuhao-local-proof"
_SOCKET_NAME = "local_proof.sock"
_REQUEST = b"issue-local-proof\n"
_MAX_REPLY = 16 * 1024


class LocalProofUnavailable(RuntimeError):
    """通道不存在、权限不足或网关未启动。"""


def _resolve(data_dir: Optional[Path]) -> Path:
    if data_dir is not None:
        return Path(data_dir).expanduser().resolve()
    return resolve_data_dir()


def channel_id(data_dir: Optional[Path] = None) -> str:
    base = str(_resolve(data_dir))
    if os.name == "nt":
        base = base.casefold()
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


def channel_name(data_dir: Optional[Path] = None) -> str:
    """按数据目录派生通道名：不同实例/不同测试互不串线。"""
    if os.name == "nt":
        return rf"\\.\pipe\{_PIPE_PREFIX}-{channel_id(data_dir)}"
    base = _resolve(data_dir) / "config"
    return str(base / _SOCKET_NAME)


def request_token_bundle(
    *, data_dir: Optional[Path] = None, timeout: float = 5.0
) -> Dict[str, Any]:
    """向网关的 DACL 通道申请一对一次性令牌。"""
    reply = _transact(data_dir=data_dir, timeout=timeout)
    try:
        payload = json.loads(reply.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LocalProofUnavailable("本机证明通道返回了无法解析的响应") from exc
    if not isinstance(payload, dict):
        raise LocalProofUnavailable("本机证明通道返回了意外的响应")
    handoff = str(payload.get("handoff_token") or "")
    reset = str(payload.get("reset_token") or "")
    if not handoff or not reset:
        raise LocalProofUnavailable("本机证明通道没有返回完整令牌")
    return {
        "handoff_token": handoff,
        "reset_token": reset,
        "expires_in": payload.get("expires_in"),
    }


def request_token(*, data_dir: Optional[Path] = None, timeout: float = 5.0) -> str:
    """只取交接令牌（占位/排障用；正式流程请用 bundle）。"""
    return str(request_token_bundle(data_dir=data_dir, timeout=timeout)["handoff_token"])


def console_fragment(
    *, data_dir: Optional[Path] = None, timeout: float = 5.0
) -> str:
    """返回可直接拼到控制台 URL 后面的 fragment（失败时报错，由调用方降级）。"""
    bundle = request_token_bundle(data_dir=data_dir, timeout=timeout)
    return (
        f"{HANDOFF_FRAGMENT_KEY}={bundle['handoff_token']}"
        f"&{RESET_FRAGMENT_KEY}={bundle['reset_token']}"
    )


def _transact(*, data_dir: Optional[Path], timeout: float) -> bytes:
    if os.name == "nt":
        return _transact_windows(data_dir=data_dir, timeout=timeout)
    return _transact_posix(data_dir=data_dir, timeout=timeout)


def _transact_windows(*, data_dir: Optional[Path], timeout: float) -> bytes:
    try:
        import pywintypes
        import win32file
        import win32pipe
    except Exception as exc:  # noqa: BLE001
        raise LocalProofUnavailable("Windows 上缺少 pywin32，无法申请本机证明") from exc

    name = channel_name(data_dir)
    try:
        win32pipe.WaitNamedPipe(name, max(1, int(timeout * 1000)))
    except pywintypes.error as exc:
        raise LocalProofUnavailable(f"本机证明通道不可用（{name}）") from exc
    try:
        handle = win32file.CreateFile(
            name,
            win32file.GENERIC_READ | win32file.GENERIC_WRITE,
            0,
            None,
            win32file.OPEN_EXISTING,
            0,
            None,
        )
    except pywintypes.error as exc:
        raise LocalProofUnavailable("无法打开本机证明通道（可能没有权限）") from exc
    try:
        win32pipe.SetNamedPipeHandleState(
            handle, win32pipe.PIPE_READMODE_MESSAGE, None, None
        )
        win32file.WriteFile(handle, _REQUEST)
        _err, data = win32file.ReadFile(handle, _MAX_REPLY)
        return bytes(data)
    except pywintypes.error as exc:
        raise LocalProofUnavailable("本机证明通道通信失败") from exc
    finally:
        try:
            handle.Close()
        except Exception:  # noqa: BLE001
            pass


def _transact_posix(*, data_dir: Optional[Path], timeout: float) -> bytes:
    name = channel_name(data_dir)
    deadline = time.monotonic() + max(0.1, timeout)
    last_error: Optional[BaseException] = None
    while time.monotonic() < deadline:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(max(0.1, deadline - time.monotonic()))
                client.connect(name)
                client.sendall(_REQUEST)
                return client.recv(_MAX_REPLY)
        except (FileNotFoundError, ConnectionRefusedError, socket.timeout) as exc:
            last_error = exc
            time.sleep(0.05)
        except OSError as exc:
            raise LocalProofUnavailable(f"本机证明通道不可用（{name}）") from exc
    raise LocalProofUnavailable(f"本机证明通道不可用（{name}）") from last_error
