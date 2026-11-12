"""本机恢复码交接的“进程级证明”：DACL 命名管道 + 一次性令牌。

IP 回环只能说明“请求来自这台机器”，不能说明“请求来自这个账户”。共享
电脑上另一个 Windows 账户同样能访问 ``127.0.0.1``。因此：

* 网关进程内起一个 DACL 只授予当前用户的命名管道（POSIX 为 0600 Unix
  socket）；其他账户连管道就被拒绝；
* 桌面壳/启动器通过管道申请**两枚**一次性令牌：交接令牌（取恢复码）与
  重置令牌（重置密码），再把它们放进控制台 URL fragment；
* HTTP 接口只认令牌，不认回环；令牌带 TTL、单次成功使用即失效，并绑定
  到申请它的进程（进程退出后令牌立即失效）。

令牌只在内存里保存 SHA-256 摘要，不落盘、不写日志。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import socket
import struct
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from ..common.local_proof_ipc import (  # noqa: F401  re-export: src/gateway/accounts.py 按属性引用
    HANDOFF_HEADER,
    RESET_HEADER,
    TRUSTED_LAUNCH_ENV,
    channel_name,
)
from ..common.windows_acl import (
    current_user_sid_string,
    dacl_entries,
    dacl_is_current_user_only,
)

logger = logging.getLogger(__name__)

#: 令牌默认有效期；足够用户从桌面端打开控制台、走完一次忘记密码流程。
PROOF_TTL_ENV = "LIUHAO_LOCAL_PROOF_TTL_SECONDS"
DEFAULT_PROOF_TTL_SECONDS = 15 * 60

SCOPE_HANDOFF = "handoff"
SCOPE_RESET = "reset"
SCOPE_ANY = "any"

_MAX_MESSAGE = 16 * 1024
_STOP_REQUEST = b"__stop-local-proof-broker__\n"


def trusted_launch() -> bool:
    """网关是否由桌面壳/启动器以“可信本机入口”方式拉起。

    未设置时按开发模式处理：只打印告警，不阻断 ``python -m src.gateway``。
    """
    return (os.environ.get(TRUSTED_LAUNCH_ENV) or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


# --------------------------------------------------------------------------- #
# HTTP 对端身份（Windows）：令牌之外再确认“调用者与网关是同一个 Windows 用户”
# --------------------------------------------------------------------------- #

_TCP_TABLE_OWNER_PID_ALL = 5
_TCP_STATE_ESTABLISHED = 5


def _windows_peer_pid(host: str, port: int) -> Optional[int]:
    """用 GetExtendedTcpTable 找到对端 ``host:port`` 在本机的持有进程。

    必须匹配**客户端一侧**的行（``local_addr:local_port``）。服务端接受连接后
    自己那一行的 ``remote_addr:remote_port`` 正是客户端地址，但持有者是网关
    进程本身——按 remote 匹配只会查到自己，SID 恒等，校验形同虚设。
    """
    import ctypes
    from ctypes import wintypes

    class _Row4(ctypes.Structure):
        _fields_ = [
            ("state", wintypes.DWORD),
            ("local_addr", wintypes.DWORD),
            ("local_port", wintypes.DWORD),
            ("remote_addr", wintypes.DWORD),
            ("remote_port", wintypes.DWORD),
            ("pid", wintypes.DWORD),
        ]

    class _Row6(ctypes.Structure):
        _fields_ = [
            ("local_addr", ctypes.c_ubyte * 16),
            ("local_scope", wintypes.DWORD),
            ("local_port", wintypes.DWORD),
            ("remote_addr", ctypes.c_ubyte * 16),
            ("remote_scope", wintypes.DWORD),
            ("remote_port", wintypes.DWORD),
            ("state", wintypes.DWORD),
            ("pid", wintypes.DWORD),
        ]

    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    row_type = _Row6 if family == socket.AF_INET6 else _Row4
    iphlpapi = ctypes.windll.iphlpapi
    size = wintypes.DWORD(0)
    iphlpapi.GetExtendedTcpTable(
        None, ctypes.byref(size), False, family, _TCP_TABLE_OWNER_PID_ALL, 0
    )
    if size.value <= 0:
        return None
    buffer = ctypes.create_string_buffer(size.value)
    result = iphlpapi.GetExtendedTcpTable(
        buffer, ctypes.byref(size), False, family, _TCP_TABLE_OWNER_PID_ALL, 0
    )
    if result != 0:
        return None
    count = wintypes.DWORD.from_buffer(buffer).value
    offset = ctypes.sizeof(wintypes.DWORD)
    rows = (row_type * count).from_buffer(buffer, offset)
    for row in rows:
        if int(row.state) != _TCP_STATE_ESTABLISHED:
            continue
        if family == socket.AF_INET:
            local = socket.inet_ntoa(struct.pack("<L", int(row.local_addr)))
        else:
            local = socket.inet_ntop(socket.AF_INET6, bytes(row.local_addr))
        local_port = socket.ntohs(int(row.local_port) & 0xFFFF)
        if local == host and local_port == port:
            return int(row.pid)
    return None


def _process_sid(pid: int) -> str:
    import win32api
    import win32con
    import win32security

    handle = win32api.OpenProcess(
        win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid
    )
    try:
        token = win32security.OpenProcessToken(handle, win32security.TOKEN_QUERY)
        try:
            sid, _attributes = win32security.GetTokenInformation(
                token, win32security.TokenUser
            )
        finally:
            token.Close()
    finally:
        win32api.CloseHandle(handle)
    return win32security.ConvertSidToStringSid(sid)


def peer_is_same_user(request: Any) -> Optional[bool]:
    """HTTP 对端进程是否与网关属于同一个 Windows 用户。

    返回 ``True`` / ``False``；无法判定（非 Windows、TestClient 的伪造对端、
    连接已断开查不到 PID）返回 ``None``，由调用方按运行模式决定是否放行。
    """
    client = getattr(request, "client", None)
    host = str(getattr(client, "host", "") or "").strip().lower()
    try:
        port = int(getattr(client, "port", 0) or 0)
    except (TypeError, ValueError):
        port = 0
    if not host or port <= 0 or host in {"testclient", "localhost"}:
        return None
    if os.name != "nt":
        return None
    try:
        pid = _windows_peer_pid(host, port)
        if pid is None:
            return None
        return _process_sid(pid) == current_user_sid_string()
    except Exception:  # noqa: BLE001 - 查不到身份时返回 None，由调用方决定
        logger.debug("无法解析 HTTP 对端进程身份", exc_info=True)
        return None


@dataclass
class _Entry:
    scope: str
    expires_at: float
    bound_pid: Optional[int]
    sid: str
    #: 已被某次请求“占用”（校验通过、尚未成功消费）。同一令牌并发只能有一个
    #: 占用者，失败时由占用者释放，成功时直接消费。
    claimed: bool = False


_lock = threading.RLock()
_tokens: Dict[str, _Entry] = {}
_brokers: Dict[str, "_Broker"] = {}


# --------------------------------------------------------------------------- #
# 令牌
# --------------------------------------------------------------------------- #

def ttl_seconds() -> int:
    raw = (os.environ.get(PROOF_TTL_ENV) or "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return DEFAULT_PROOF_TTL_SECONDS


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _prune(now: float) -> None:
    expired = [key for key, entry in _tokens.items() if now >= entry.expires_at]
    for key in expired:
        _tokens.pop(key, None)


def issue_token(
    *, scope: str = SCOPE_ANY, bound_pid: Optional[int] = None
) -> str:
    """生成一次性令牌；只返回明文，内存里只留摘要。"""
    raw = secrets.token_urlsafe(32)
    now = time.time()
    entry = _Entry(
        scope=scope,
        expires_at=now + ttl_seconds(),
        bound_pid=bound_pid,
        sid=current_user_sid_string(),
    )
    with _lock:
        _prune(now)
        _tokens[_digest(raw)] = entry
    return raw


def issue_bundle(*, bound_pid: Optional[int] = None) -> Dict[str, Any]:
    """一次管道请求返回交接 + 重置两枚令牌，避免一次流程要开两次通道。"""
    expiry = ttl_seconds()
    return {
        "handoff_token": issue_token(scope=SCOPE_HANDOFF, bound_pid=bound_pid),
        "reset_token": issue_token(scope=SCOPE_RESET, bound_pid=bound_pid),
        "expires_in": expiry,
        "bound_pid": bound_pid,
    }


def _check(
    raw: str,
    *,
    scope: str,
    bound_pid: Optional[int] = None,
    consume: bool = False,
    claim: bool = False,
) -> bool:
    if not raw:
        return False
    key = _digest(raw)
    now = time.time()
    with _lock:
        entry = _tokens.get(key)
        if entry is None:
            return False
        if now >= entry.expires_at:
            _tokens.pop(key, None)
            return False
        if entry.scope not in (SCOPE_ANY, scope):
            return False
        if entry.claimed:
            return False
        if entry.sid and current_user_sid_string() != entry.sid:
            return False
        if entry.bound_pid is not None:
            if bound_pid is not None and int(bound_pid) != entry.bound_pid:
                return False
            if not _pid_alive(entry.bound_pid):
                _tokens.pop(key, None)
                return False
        if consume:
            _tokens.pop(key, None)
        elif claim:
            entry.claimed = True
        return True


def validate_token(raw: str, *, scope: str = SCOPE_ANY) -> bool:
    """校验但不消费、不占用（只读查询）。"""
    return _check(raw, scope=scope, consume=False)


def claim_token(raw: str, *, scope: str = SCOPE_ANY) -> bool:
    """校验并占用：并发请求里只有一个能拿到占用权。

    占用不是消费。调用方在业务失败时 :func:`release_token` 让用户可以重试；
    业务成功后再 :func:`consume_token` 使其永久失效。
    """
    return _check(raw, scope=scope, claim=True)


def release_token(raw: str) -> None:
    """释放占用（业务失败、允许重试）。"""
    with _lock:
        entry = _tokens.get(_digest(raw))
        if entry is not None:
            entry.claimed = False


def redeem_token(raw: str, *, scope: str = SCOPE_ANY) -> bool:
    """校验并消费；单次成功使用后同一令牌不可再用。"""
    return _check(raw, scope=scope, consume=True)


def consume_token(raw: str) -> None:
    with _lock:
        _tokens.pop(_digest(raw), None)


def request_token(request: Any, header: str) -> str:
    try:
        return str(request.headers.get(header) or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def reset_token_store() -> None:
    """测试/停机用：清空所有未消费令牌。"""
    with _lock:
        _tokens.clear()


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import win32api
            import win32con

            handle = win32api.OpenProcess(
                win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            win32api.CloseHandle(handle)
            return True
        except Exception:  # noqa: BLE001
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# 命名管道 / Unix socket 服务
# --------------------------------------------------------------------------- #

class _Broker:
    def __init__(self, name: str) -> None:
        self.name = name
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._handle: Any = None
        self._socket: Optional[socket.socket] = None
        # 创建时读回的真实 DACL 快照（此时还没有挂起 ConnectNamedPipe，
        # 之后再对句柄取安全描述符会阻塞）。
        self._acl_ok: bool = False
        self._acl_entries: list[dict[str, Any]] = []

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run, name="liuhao-local-proof", daemon=True
        )
        self._thread.start()

    def wait_until_ready(self, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < deadline:
            if self._handle is not None or self._socket is not None:
                return True
            if self._thread is not None and not self._thread.is_alive():
                return False
            time.sleep(0.02)
        return self._handle is not None or self._socket is not None

    def acl_is_current_user_only(self) -> bool:
        if os.name != "nt":
            try:
                return (os.stat(self.name).st_mode & 0o077) == 0
            except OSError:
                return False
        return self._acl_ok

    def acl_entries(self) -> list[dict[str, Any]]:
        return list(self._acl_entries)

    def stop(self) -> None:
        self._stop.set()
        if os.name == "nt":
            self._wake_windows()
            handle, self._handle = self._handle, None
            if handle is not None:
                try:
                    handle.Close()
                except Exception:  # noqa: BLE001
                    pass
        else:
            sock, self._socket = self._socket, None
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
            try:
                Path(self.name).unlink(missing_ok=True)
            except OSError:
                pass

    def _wake_windows(self) -> None:
        try:
            import win32file
            import win32pipe

            win32pipe.WaitNamedPipe(self.name, 200)
            handle = win32file.CreateFile(
                self.name,
                win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                0,
                None,
                win32file.OPEN_EXISTING,
                0,
                None,
            )
            try:
                win32file.WriteFile(handle, _STOP_REQUEST)
            finally:
                handle.Close()
        except Exception:  # noqa: BLE001 - 唤醒失败不影响 daemon 线程退出
            pass

    def _run(self) -> None:
        try:
            if os.name == "nt":
                self._run_windows()
            else:
                self._run_posix()
        except Exception:  # noqa: BLE001
            logger.warning("本机证明通道异常退出", exc_info=True)

    # -- Windows ---------------------------------------------------------- #

    def _run_windows(self) -> None:
        try:
            import win32file
            import win32pipe
        except Exception:  # noqa: BLE001
            logger.warning("缺少 pywin32，本机证明通道未启动")
            return
        try:
            handle = self._create_pipe()
        except Exception:  # noqa: BLE001
            logger.warning("无法创建本机证明命名管道 %s", self.name, exc_info=True)
            return
        self._handle = handle
        while not self._stop.is_set():
            try:
                win32pipe.ConnectNamedPipe(handle, None)
            except Exception as exc:  # noqa: BLE001
                if not _pipe_connected_error(exc):
                    break
            try:
                data = win32file.ReadFile(handle, _MAX_MESSAGE)[1]
                if data.strip() == _STOP_REQUEST.strip():
                    break
                pid = _pipe_client_pid(handle)
                bundle = issue_bundle(bound_pid=pid)
                win32file.WriteFile(
                    handle,
                    (json.dumps(bundle, ensure_ascii=False) + "\n").encode("utf-8"),
                )
                # DisconnectNamedPipe 会丢弃尚未被对端读走的数据。不等这一步，
                # 客户端会间歇性收到 ERROR_PIPE_NOT_CONNECTED(233)——
                # 表现为“本机证明通道通信失败”，约 1/5 概率，冒烟会假红。
                try:
                    win32file.FlushFileBuffers(handle)
                except Exception:  # noqa: BLE001 - 对端提前断开时忽略
                    pass
            except Exception:  # noqa: BLE001
                logger.debug("本机证明通道单次请求失败", exc_info=True)
            finally:
                try:
                    win32pipe.DisconnectNamedPipe(handle)
                except Exception:  # noqa: BLE001
                    pass

    def _create_pipe(self) -> Any:
        import ntsecuritycon
        import win32pipe
        import win32security

        from ..common.windows_acl import current_user_sid

        sid = current_user_sid()
        dacl = win32security.ACL()
        dacl.AddAccessAllowedAce(
            win32security.ACL_REVISION, ntsecuritycon.FILE_ALL_ACCESS, sid
        )
        descriptor = win32security.SECURITY_DESCRIPTOR()
        descriptor.SetSecurityDescriptorDacl(1, dacl, 0)
        try:
            descriptor.SetSecurityDescriptorControl(
                win32security.SE_DACL_PROTECTED, win32security.SE_DACL_PROTECTED
            )
        except Exception:  # noqa: BLE001 - 旧版 pywin32 没有该 API
            pass
        attributes = win32security.SECURITY_ATTRIBUTES()
        attributes.SECURITY_DESCRIPTOR = descriptor
        handle = win32pipe.CreateNamedPipe(
            self.name,
            win32pipe.PIPE_ACCESS_DUPLEX,
            win32pipe.PIPE_TYPE_MESSAGE
            | win32pipe.PIPE_READMODE_MESSAGE
            | win32pipe.PIPE_WAIT,
            win32pipe.PIPE_UNLIMITED_INSTANCES,
            _MAX_MESSAGE,
            _MAX_MESSAGE,
            0,
            attributes,
        )
        # 必须在 ConnectNamedPipe 之前读回 DACL：挂起连接后再取安全描述符会阻塞。
        self._acl_entries = _pipe_handle_acl_entries(handle)
        self._acl_ok = _pipe_handle_acl_is_current_user_only(handle)
        if not self._acl_ok:
            # 创建时继承链可能仍带进了别的 ACE：对句柄再收紧一次并复核。
            flags = win32security.DACL_SECURITY_INFORMATION
            flags |= getattr(
                win32security, "PROTECTED_DACL_SECURITY_INFORMATION", 0x80000000
            )
            try:
                win32security.SetSecurityInfo(
                    handle,
                    win32security.SE_KERNEL_OBJECT,
                    flags,
                    None,
                    None,
                    dacl,
                    None,
                )
            except Exception:  # noqa: BLE001
                pass
            self._acl_entries = _pipe_handle_acl_entries(handle)
            self._acl_ok = _pipe_handle_acl_is_current_user_only(handle)
            if not self._acl_ok:
                try:
                    handle.Close()
                except Exception:  # noqa: BLE001
                    pass
                raise PermissionError("命名管道 DACL 未能收紧为仅当前用户")
        return handle

    # -- POSIX ------------------------------------------------------------ #

    def _run_posix(self) -> None:
        path = Path(self.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._socket = server
        try:
            server.bind(self.name)
            os.chmod(self.name, 0o600)
            server.listen(8)
            server.settimeout(0.5)
        except OSError:
            logger.warning("本机证明 Unix socket 启动失败 %s", self.name, exc_info=True)
            return
        while not self._stop.is_set():
            try:
                conn, _addr = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with conn:
                try:
                    data = conn.recv(_MAX_MESSAGE)
                    if data.strip() == _STOP_REQUEST.strip():
                        break
                    bundle = issue_bundle(bound_pid=_peer_pid(conn))
                    conn.sendall(
                        (json.dumps(bundle, ensure_ascii=False) + "\n").encode("utf-8")
                    )
                except OSError:
                    logger.debug("本机证明 socket 单次请求失败", exc_info=True)


def _pipe_connected_error(exc: BaseException) -> bool:
    winerror = getattr(exc, "winerror", None)
    # ERROR_PIPE_CONNECTED：客户端在 ConnectNamedPipe 之前就连上了。
    return winerror == 535


def _pipe_client_pid(handle: Any) -> Optional[int]:
    try:
        import win32pipe

        return int(win32pipe.GetNamedPipeClientProcessId(handle))
    except Exception:  # noqa: BLE001
        return None


def _pipe_handle_acl(handle: Any) -> Any:
    """命名管道不能按名字取安全描述符，必须对已创建的句柄读 DACL。"""
    if handle is None:
        return None
    try:
        import win32security

        descriptor = win32security.GetSecurityInfo(
            handle,
            win32security.SE_KERNEL_OBJECT,
            win32security.DACL_SECURITY_INFORMATION,
        )
        return descriptor.GetSecurityDescriptorDacl()
    except Exception:  # noqa: BLE001
        return None


def _pipe_handle_acl_is_current_user_only(handle: Any) -> bool:
    return dacl_is_current_user_only(_pipe_handle_acl(handle))


def _pipe_handle_acl_entries(handle: Any) -> list[dict[str, Any]]:
    return dacl_entries(_pipe_handle_acl(handle))


def _peer_pid(conn: socket.socket) -> Optional[int]:
    try:
        if hasattr(socket, "SO_PEERCRED"):
            raw = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            return int(struct.unpack("3i", raw)[0])
        if hasattr(conn, "getpeereid"):
            return int(conn.getpeereid()[0])  # type: ignore[attr-defined]
    except OSError:
        pass
    return None


def start_broker(data_dir: Optional[Path] = None) -> bool:
    """启动（幂等）当前数据目录对应的本机证明通道。"""
    name = channel_name(data_dir)
    with _lock:
        existing = _brokers.get(name)
        if existing is not None:
            existing.start()
            return True
        broker = _Broker(name)
        _brokers[name] = broker
    broker.start()
    return True


def wait_for_broker(data_dir: Optional[Path] = None, timeout: float = 2.0) -> bool:
    name = channel_name(data_dir)
    with _lock:
        broker = _brokers.get(name)
    return bool(broker and broker.wait_until_ready(timeout))


def pipe_acl_is_current_user_only(data_dir: Optional[Path] = None) -> bool:
    """读回命名管道实际 DACL：测试与启动自检共用。"""
    name = channel_name(data_dir)
    with _lock:
        broker = _brokers.get(name)
    return bool(broker and broker.acl_is_current_user_only())


def pipe_acl_entries(data_dir: Optional[Path] = None) -> list[dict[str, Any]]:
    """返回创建时读到的真实 ACE 列表（type/flags/mask/sid）。"""
    name = channel_name(data_dir)
    with _lock:
        broker = _brokers.get(name)
    return broker.acl_entries() if broker else []


def stop_broker(data_dir: Optional[Path] = None) -> None:
    name = channel_name(data_dir)
    with _lock:
        broker = _brokers.pop(name, None)
    if broker is not None:
        broker.stop()


def stop_all_brokers() -> None:
    with _lock:
        brokers = list(_brokers.values())
        _brokers.clear()
    for broker in brokers:
        broker.stop()
