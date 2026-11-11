"""本机免密登录：用 Windows DPAPI 保存凭据，换机/换 Windows 用户即失效。

安全边界（写死在本模块，不依赖调用方自觉）
--------------------------------------------
* **只在本机用**：解密后的口令只允许发往回环地址的 ``POST /v1/auth/login``；
  其它地址一律拒绝（防止把口令发给局域网里的另一台机器）。
* **DPAPI CurrentUser + 固定 entropy**：密文绑定当前 Windows 用户；换账户或
  换机器后 ``CryptUnprotectData`` 直接失败，不需要额外判断"是不是本人"。
  密文里另存 SID，解密成功后再核对一次（防止 roaming profile 等少见情况）。
* **不落日志**：本模块所有日志只写事件名、状态码与异常类型，绝不写
  principal/secret/请求体/响应体。异常不携带请求体（``HTTPError`` 只带状态）。
* **同意门在写盘之前**：``save()`` 要求同意状态已是 ``granted``，否则抛
  ``PermissionError``；同意状态由上层弹窗后调用 ``record_consent()`` 持久化。
* **清除不需要解密**：``forget()`` 只删文件（外加清掉本对象缓存），即便密文
  已损坏、换过 Windows 用户、DPAPI 不可用也能执行。

存储位置：``<DATA_DIR>/desktop/auto-login.bin``（密文）与
``<DATA_DIR>/desktop/auto-login-consent.json``（同意状态，明文、无敏感信息）。
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

#: 密文格式版本：以后改结构时据此判断能否解密。
CREDENTIAL_VERSION = 1
#: DPAPI 附加熵：同一 Windows 用户下的其它程序即使拿到密文也不能解开。
_ENTROPY = b"LiuHaoDesktopAutoLogin:v1"
#: 凭据与同意状态的文件名（放在 ``<DATA_DIR>/desktop`` 下）。
CREDENTIAL_FILE = "auto-login.bin"
CONSENT_FILE = "auto-login-consent.json"
#: 登录客户端标识：网关据此把令牌标成桌面端（不参与权限判定）。
LOGIN_CLIENT = "desktop"
#: 登录请求超时：本机回环，正常 <100ms；给 5s 已足够覆盖冷启动。
LOGIN_TIMEOUT = 5.0


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> tuple[_DataBlob, Any]:
    buffer = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


def _crypt32() -> Any:
    from ctypes import WinDLL

    library = WinDLL("crypt32", use_last_error=True)
    library.CryptProtectData.argtypes = (
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    )
    library.CryptProtectData.restype = wintypes.BOOL
    library.CryptUnprotectData.argtypes = (
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    )
    library.CryptUnprotectData.restype = wintypes.BOOL
    return library


def _crypt(data: bytes, *, protect: bool) -> bytes:
    """DPAPI 加/解密（CurrentUser + 固定熵）。

    失败时抛 ``OSError``，异常消息只含 Win32 错误码，不含明文数据。
    """
    if os.name != "nt":
        raise OSError("DPAPI 仅 Windows 可用")
    library = _crypt32()
    data_blob, data_buffer = _blob(data)
    entropy_blob, entropy_buffer = _blob(_ENTROPY)
    out = _DataBlob()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    flags = 0x1  # CRYPTPROTECT_UI_FORBIDDEN：任何情况下都不弹系统 UI
    if protect:
        ok = library.CryptProtectData(
            ctypes.byref(data_blob),
            "LiuHao desktop auto-login",
            ctypes.byref(entropy_blob),
            None,
            None,
            flags,
            ctypes.byref(out),
        )
    else:
        ok = library.CryptUnprotectData(
            ctypes.byref(data_blob),
            None,
            ctypes.byref(entropy_blob),
            None,
            None,
            flags,
            ctypes.byref(out),
        )
    # 两个输入缓冲区必须活到调用结束，否则 GC 可能提前释放。
    _ = (data_buffer, entropy_buffer)
    if not ok:
        raise OSError(ctypes.get_last_error(), "DPAPI 调用失败")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        if out.pbData:
            kernel32.LocalFree(out.pbData)


def _current_sid() -> str:
    """当前 Windows 用户 SID 字符串；拿不到返回空串（按不可用处理）。"""
    if os.name != "nt":
        return ""
    try:
        import win32api
        import win32security

        token = win32security.OpenProcessToken(
            win32api.GetCurrentProcess(), win32security.TOKEN_QUERY
        )
        try:
            sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
            return str(win32security.ConvertSidToStringSid(sid))
        finally:
            token.Close()
    except Exception:  # noqa: BLE001 - SID 只用于二次核对
        return ""


def session_from_login(body: dict[str, Any]) -> dict[str, Any]:
    """把 ``/v1/auth/login`` 的响应转成前端 ``Session`` 结构。

    字段名必须与 ``apps/console/console/src/lib/auth.ts`` 的 ``Session`` 一致；
    ``client`` 固定为 ``desktop``（不信任响应里回显的其它值）。
    """
    token = str(body.get("access_token") or body.get("token") or "")
    permissions = body.get("permissions")
    if not isinstance(permissions, list):
        permissions = []
    expires_at = body.get("expires_at") or 0
    try:
        expires_at = float(expires_at)
    except (TypeError, ValueError):
        expires_at = 0.0
    return {
        "token": token,
        "principal": str(body.get("principal") or ""),
        "displayName": str(body.get("display_name") or body.get("principal") or ""),
        "scope": str(body.get("scope") or ""),
        "permissions": [str(item) for item in permissions],
        "expiresAt": expires_at,
        "client": LOGIN_CLIENT,
        "isRoot": bool(body.get("is_root")),
        "canRotateRecovery": bool(body.get("can_rotate_recovery")),
    }


class AutoLogin:
    """本机免密凭据的读写与登录；所有方法都可从任意线程调用。"""

    def __init__(self, data_dir: Path | str) -> None:
        desktop = Path(data_dir) / "desktop"
        self.credential_path = desktop / CREDENTIAL_FILE
        self.consent_path = desktop / CONSENT_FILE
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # 状态
    # ------------------------------------------------------------------ #
    def supported(self) -> bool:
        return os.name == "nt"

    def has_credential(self) -> bool:
        return self.credential_path.is_file()

    def consent(self) -> str | None:
        """返回 ``"granted"`` / ``"denied"`` / ``None``（尚未问过）。"""
        try:
            payload = json.loads(self.consent_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        value = str(payload.get("consent") or "") if isinstance(payload, dict) else ""
        return value if value in ("granted", "denied") else None

    def record_consent(self, granted: bool) -> None:
        """持久化同意状态（只记结论与时间，不记任何账号信息）。"""
        self.consent_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "consent": "granted" if granted else "denied",
            "decided_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        tmp = self.consent_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.consent_path)
        logger.info("evt=autologin.consent consent=%s", payload["consent"])

    # ------------------------------------------------------------------ #
    # 写 / 读 / 删
    # ------------------------------------------------------------------ #
    def save(self, principal: str, secret: str) -> bool:
        """加密保存凭据；未获同意时拒绝写盘并抛 ``PermissionError``。"""
        # 同意门先于平台判断：未获同意时任何平台都不得写盘，且错误类型一致。
        if self.consent() != "granted":
            raise PermissionError("未获得本机免密同意，拒绝保存凭据")
        if not self.supported():
            raise OSError("本机免密仅支持 Windows")
        if not principal or not secret:
            raise ValueError("账号或密码为空")
        payload = {
            "version": CREDENTIAL_VERSION,
            "sid": _current_sid(),
            "principal": principal,
            "secret": secret,
            "client": LOGIN_CLIENT,
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        encrypted = _crypt(
            json.dumps(payload, ensure_ascii=False).encode("utf-8"), protect=True
        )
        with self._lock:
            self.credential_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.credential_path.with_suffix(".tmp")
            tmp.write_bytes(encrypted)
            self._restrict(tmp)
            os.replace(tmp, self.credential_path)
        logger.info("evt=autologin.saved")
        return True

    def load(self) -> dict[str, Any] | None:
        """解密并返回凭据；任何失败都返回 ``None``（日志不含明文）。"""
        try:
            encrypted = self.credential_path.read_bytes()
        except OSError:
            return None
        try:
            payload = json.loads(_crypt(encrypted, protect=False).decode("utf-8"))
        except (OSError, ValueError):
            logger.warning("evt=autologin.unreadable 凭据无法解密（换用户/密文损坏）")
            return None
        if not isinstance(payload, dict) or int(payload.get("version") or 0) != CREDENTIAL_VERSION:
            logger.warning("evt=autologin.unreadable 凭据版本不匹配")
            return None
        recorded_sid = str(payload.get("sid") or "")
        if recorded_sid and _current_sid() and recorded_sid != _current_sid():
            logger.warning("evt=autologin.foreign_user 凭据不属于当前 Windows 用户")
            return None
        if not payload.get("principal") or not payload.get("secret"):
            return None
        return payload

    def forget(self) -> bool:
        """删除凭据文件（不解密；不存在也算成功），并清掉内存中的缓存。"""
        with self._lock:
            try:
                self.credential_path.unlink()
                removed = True
            except FileNotFoundError:
                removed = False
            except OSError:
                logger.warning("evt=autologin.clear_failed", exc_info=False)
                return False
        logger.info("evt=autologin.cleared removed=%s", removed)
        return True

    @staticmethod
    def _restrict(path: Path) -> None:
        """把密文文件的 ACL 收紧到当前用户（DPAPI 已是第二层保护）。

        ACL 失败只降级为警告：文件已经过 DPAPI 加密，别的账户拿到也解不开，
        不能因为 ACL API 不可用就把"记住登录"整体判失败。
        """
        try:
            from ..common.fs_permissions import restrict_to_current_user

            restrict_to_current_user(path)
        except Exception:  # noqa: BLE001 - 见上
            logger.warning("evt=autologin.acl_failed 文件仍受 DPAPI 保护")

    # ------------------------------------------------------------------ #
    # 登录
    # ------------------------------------------------------------------ #
    def login(
        self, base_url: str, principal: str, secret: str, *, timeout: float = LOGIN_TIMEOUT
    ) -> dict[str, Any] | None:
        """本机 ``POST /v1/auth/login``；成功返回前端 Session，失败返回 None。

        地址必须是回环：口令只允许发给这台电脑上的网关。
        """
        if not _is_loopback(base_url):
            logger.warning("evt=autologin.refused reason=non_loopback")
            return None
        request = Request(
            base_url.rstrip("/") + "/v1/auth/login",
            data=json.dumps(
                {"principal": principal, "secret": secret, "client": LOGIN_CLIENT}
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:  # noqa: S310 - 回环地址
                body = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            # 只记录状态码：响应体可能回显账号信息，不写日志。
            logger.warning("evt=autologin.login_failed status=%s", exc.code)
            return None
        except (URLError, OSError, ValueError) as exc:
            logger.warning("evt=autologin.login_failed error=%s", type(exc).__name__)
            return None
        if not isinstance(body, dict):
            logger.warning("evt=autologin.login_failed reason=bad_response")
            return None
        session = session_from_login(body)
        if not session["token"]:
            logger.warning("evt=autologin.login_failed reason=no_token")
            return None
        logger.info("evt=autologin.login_ok")
        return session

    def bootstrap(self, base_url: str) -> dict[str, Any] | None:
        """启动时自动登录：同意过且有凭据才尝试；否则返回 None（显示登录页）。"""
        if self.consent() != "granted" or not self.has_credential():
            return None
        credential = self.load()
        if credential is None:
            return None
        return self.login(
            base_url, str(credential["principal"]), str(credential["secret"])
        )


def _is_loopback(base_url: str) -> bool:
    host = (urlsplit(base_url or "").hostname or "").lower()
    return host in ("127.0.0.1", "localhost", "::1")


__all__ = [
    "AutoLogin",
    "CONSENT_FILE",
    "CREDENTIAL_FILE",
    "CREDENTIAL_VERSION",
    "LOGIN_CLIENT",
    "session_from_login",
]
