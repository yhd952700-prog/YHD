"""本机恢复码交接：DPAPI 加密落盘、一次性取用、旧明文迁移。

安全口径（与 ``docs/account-hierarchy.md`` 对齐）：

* 恢复码**永不**以明文长期落盘。Windows 优先 DPAPI（``CryptProtectData``，
  CurrentUser 作用域）：进程重启后仍可解密，同机其他账户解不开；
* DPAPI 不可用（非 Windows / 调用失败）才降级为“ACL 收紧 + 短 TTL”，并记录
  降级原因；降级文件到期或取用后立即删除，不允许长期驻留；
* 文件权限不能只看 ``chmod``：Windows 上它只等于只读位，必须显式去掉 ACL
  继承、只保留当前用户，并在启动自检里复核实际 DACL；
* 交接文件只在 ``/recovery/local`` 被请求时解密一次；返回后立即删除，
  不缓存、不写第二份。
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..common.atomic_write import atomic_write_bytes
from ..kernels.identity import _accounts, _persistence

logger = logging.getLogger(__name__)

#: 交接文件格式（加密与降级两种保护方式共享同一个外层 JSON）。
SCHEMA = "liuhao.recovery-handoff/1"

#: 文件与安装版布局一致：``<data>/config/recovery_code.txt``。
LOCAL_RECOVERY_FILE_NAME = "recovery_code.txt"

#: 旧明文迁移标记（不含任何密钥/码，仅用于“登录后建议重新生成”的提示）。
LEGACY_NOTICE_FILE_NAME = "recovery_handoff_legacy.json"
_LEGACY_NOTICE_SCHEMA = "liuhao.recovery-handoff-notice/1"

#: 降级模式的短 TTL；可用环境变量覆盖（测试用它验证过期即焚）。
FALLBACK_TTL_ENV = "LIUHAO_RECOVERY_HANDOFF_TTL_SECONDS"
DEFAULT_FALLBACK_TTL_SECONDS = 10 * 60

#: 远程恢复开关：默认关闭；打开后启动日志必须明确记录。
REMOTE_RECOVERY_ENV = "LIUHAO_ALLOW_REMOTE_RECOVERY"

_DPAPI_DESCRIPTION = "liuhao-recovery-handoff"
_LEGACY_CODE_LINE = re.compile(r"^RC-[A-Z0-9-]{10,}$")

#: 进程内仅记录“降级原因/擦除证据”，**从不**保存码本身。
_state: Dict[str, Any] = {
    "degradation": "",
    "scratch": None,
    "last_wiped_bytes": 0,
}


# --------------------------------------------------------------------------- #
# 路径与开关
# --------------------------------------------------------------------------- #

def handoff_dir() -> Path:
    return _persistence.default_data_dir() / "config"


def handoff_path() -> Path:
    return handoff_dir() / LOCAL_RECOVERY_FILE_NAME


def legacy_notice_path() -> Path:
    return handoff_dir() / LEGACY_NOTICE_FILE_NAME


def _env_truthy(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in {"1", "true", "on", "yes"}


def remote_recovery_enabled() -> bool:
    """非本机恢复是否被显式放行（默认关闭）。"""
    return _env_truthy(REMOTE_RECOVERY_ENV)


def last_degradation() -> str:
    return str(_state.get("degradation") or "")


def scratch_buffer() -> Optional[bytearray]:
    """测试与自检用的“解密暂存区”观察点；正常情况下始终为 ``None``。"""
    return _state.get("scratch")


def last_wiped_bytes() -> int:
    return int(_state.get("last_wiped_bytes") or 0)


# --------------------------------------------------------------------------- #
# DPAPI
# --------------------------------------------------------------------------- #

def dpapi_available() -> bool:
    if os.name != "nt":
        return False
    try:
        import win32crypt  # noqa: F401
    except Exception:  # noqa: BLE001 - 缺依赖即视为不可用
        return False
    return True


def _dpapi_protect(data: bytes) -> bytes:
    import win32crypt

    return win32crypt.CryptProtectData(data, _DPAPI_DESCRIPTION, None, None, None, 0)


def _dpapi_unprotect(blob: bytes) -> bytes:
    import win32crypt

    _description, data = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
    return data


# --------------------------------------------------------------------------- #
# ACL
# --------------------------------------------------------------------------- #

def _current_user_sid():
    import win32api
    import win32security

    token = win32security.OpenProcessToken(
        win32api.GetCurrentProcess(), win32security.TOKEN_ALL_ACCESS
    )
    return win32security.GetTokenInformation(token, win32security.TokenUser)[0]


def _tighten_acl(path: Path) -> bool:
    """去掉继承，只给当前用户；返回“实际生效”而不是“调用没报错”。"""
    if os.name != "nt":
        try:
            os.chmod(path, 0o600)
            return (stat.S_IMODE(path.stat().st_mode) & 0o077) == 0
        except OSError:
            return False
    try:
        import ntsecuritycon
        import win32security

        sid = _current_user_sid()
        dacl = win32security.ACL()
        dacl.AddAccessAllowedAce(
            win32security.ACL_REVISION, ntsecuritycon.FILE_ALL_ACCESS, sid
        )
        descriptor = win32security.SECURITY_DESCRIPTOR()
        descriptor.SetSecurityDescriptorDacl(1, dacl, 0)
        flags = win32security.DACL_SECURITY_INFORMATION
        flags |= getattr(win32security, "PROTECTED_DACL_SECURITY_INFORMATION", 0x80000000)
        win32security.SetFileSecurity(str(path), flags, descriptor)
        return acl_is_current_user_only(path)
    except Exception:  # noqa: BLE001 - ACL 失败必须按失败处理
        logger.warning("收紧恢复码文件 ACL 失败：%s", path, exc_info=True)
        return False


def acl_is_current_user_only(path: Path) -> bool:
    """实际 DACL 是否“只有当前用户、无继承、无其他主体”。"""
    if os.name != "nt":
        try:
            return (stat.S_IMODE(path.stat().st_mode) & 0o077) == 0
        except OSError:
            return False
    try:
        import win32security

        sid = _current_user_sid()
        wanted = win32security.ConvertSidToStringSid(sid)
        descriptor = win32security.GetFileSecurity(
            str(path), win32security.DACL_SECURITY_INFORMATION
        )
        dacl = descriptor.GetSecurityDescriptorDacl()
        if dacl is None:
            return False
        inherited_flag = getattr(win32security, "INHERITED_ACE", 0x10)
        allowed_type = getattr(win32security, "ACCESS_ALLOWED_ACE_TYPE", 0)
        for index in range(dacl.GetAceCount()):
            (ace_type, ace_flags), _mask, ace_sid = dacl.GetAce(index)
            if ace_type != allowed_type:
                return False
            if ace_flags & inherited_flag:
                return False
            if win32security.ConvertSidToStringSid(ace_sid) != wanted:
                return False
        return True
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- #
# 写入 / 取用
# --------------------------------------------------------------------------- #

def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def _fallback_ttl_seconds() -> int:
    raw = (os.environ.get(FALLBACK_TTL_ENV) or "").strip()
    try:
        return int(raw) if raw else DEFAULT_FALLBACK_TTL_SECONDS
    except ValueError:
        return DEFAULT_FALLBACK_TTL_SECONDS


def _remove(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("删除恢复码交接文件失败：%s", path, exc_info=True)


def _wipe(buffer: Optional[bytearray]) -> None:
    """把解密后的暂存字节清零，再丢掉引用。"""
    if buffer is None:
        return
    _state["last_wiped_bytes"] = len(buffer)
    for index in range(len(buffer)):
        buffer[index] = 0
    _state["scratch"] = None


def write_handoff(
    principal: str,
    code: str,
    *,
    target: Optional[Path] = None,
) -> Dict[str, Any]:
    """把恢复码写成交接文件；返回实际使用的保护方式。"""
    path = Path(target) if target is not None else handoff_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    created = datetime.now(timezone.utc)
    payload = {
        "code": code,
        "principal": principal,
        "created_at": _iso(created),
    }

    reason = ""
    wrapper: Dict[str, Any]
    if dpapi_available():
        try:
            blob = _dpapi_protect(
                json.dumps(payload, ensure_ascii=False).encode("utf-8")
            )
            wrapper = {
                "schema": SCHEMA,
                "protection": "dpapi-current-user",
                "description": _DPAPI_DESCRIPTION,
                "blob": base64.b64encode(blob).decode("ascii"),
            }
        except Exception as exc:  # noqa: BLE001 - 调用失败走降级
            reason = f"DPAPI 加密失败：{exc}"
    else:
        reason = "当前平台没有可用的 DPAPI（非 Windows 或 pywin32 不可用）"

    if reason:
        wrapper = {
            "schema": SCHEMA,
            "protection": "acl-fallback",
            "created_at": _iso(created),
            "expires_at": _iso(created + timedelta(seconds=_fallback_ttl_seconds())),
            "payload": payload,
        }
        _state["degradation"] = reason
        logger.warning("恢复码交接降级为 ACL + 短 TTL（%s）：%s", path, reason)

    atomic_write_bytes(
        path,
        json.dumps(wrapper, ensure_ascii=False, indent=2).encode("utf-8"),
    )
    if not _tighten_acl(path):
        # 权限收不紧就绝不留下：宁可让调用方回滚哈希，也不留一份可被同机
        # 其他用户读到的文件。
        _remove(path)
        raise OSError("无法把恢复码交接文件权限收紧到当前用户")
    return {"protection": wrapper["protection"], "degraded": bool(reason)}


def take_handoff() -> Optional[str]:
    """解密一次并立即删除文件；文件不存在返回 ``None``。"""
    path = handoff_path()
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError:
        logger.warning("读取恢复码交接文件失败：%s", path, exc_info=True)
        return None

    try:
        try:
            wrapper = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            logger.warning("恢复码交接文件格式不可识别，按失效处理并删除：%s", path)
            return None

        protection = str(wrapper.get("protection") or "")
        if protection == "dpapi-current-user":
            try:
                blob = base64.b64decode(str(wrapper.get("blob") or ""))
            except (ValueError, TypeError):
                logger.warning("恢复码交接密文损坏，按失效处理并删除：%s", path)
                return None
            plain = bytearray(_dpapi_unprotect(blob))
            _state["scratch"] = plain
            try:
                payload = json.loads(bytes(plain).decode("utf-8"))
            finally:
                _wipe(plain)
        elif protection == "acl-fallback":
            if _expired(wrapper.get("expires_at")):
                logger.warning("恢复码交接文件已过期，删除：%s", path)
                return None
            payload = wrapper.get("payload") or {}
        else:
            logger.warning("恢复码交接文件保护方式未知，按失效处理并删除：%s", path)
            return None

        code = str(payload.get("code") or "").strip()
        return code or None
    except Exception:  # noqa: BLE001 - 解密失败一律按失效处理
        logger.warning("恢复码交接文件解密失败，按失效处理并删除：%s", path, exc_info=True)
        return None
    finally:
        _remove(path)


def clear_handoff() -> None:
    """作废当前交接文件（生成新码 / 重置成功后调用）。"""
    _remove(handoff_path())


def _parse_time(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _expired(value: Any) -> bool:
    expires = _parse_time(value)
    return expires is None or datetime.now(timezone.utc) >= expires


def purge_expired() -> bool:
    """删除过期的降级文件；加密文件没有 TTL，不受影响。"""
    path = handoff_path()
    if not path.is_file():
        return False
    try:
        wrapper = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(wrapper, dict):
        return False
    if wrapper.get("protection") == "dpapi-current-user":
        return False
    if _expired(wrapper.get("expires_at")):
        logger.warning("删除已过期的明文降级恢复码文件：%s", path)
        _remove(path)
        return True
    return False


# --------------------------------------------------------------------------- #
# 旧明文迁移与提示
# --------------------------------------------------------------------------- #

def _write_legacy_notice() -> None:
    payload = {
        "schema": _LEGACY_NOTICE_SCHEMA,
        "legacy_plaintext_migrated": True,
        "migrated_at": _iso(datetime.now(timezone.utc)),
        "risk": "旧版本曾以明文保存恢复码，窗口期可能已被同机其他用户读取；"
                "建议登录后重新生成恢复码。",
    }
    try:
        atomic_write_bytes(
            legacy_notice_path(),
            json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        _tighten_acl(legacy_notice_path())
    except Exception:  # noqa: BLE001 - 提示失败不影响迁移本身
        logger.warning("写入旧明文迁移提示失败", exc_info=True)


def rotate_recommended() -> bool:
    """是否建议登录后重新生成恢复码（旧明文迁移过且用户尚未换新）。"""
    try:
        data = json.loads(legacy_notice_path().read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return bool(isinstance(data, dict) and data.get("legacy_plaintext_migrated"))


def clear_legacy_notice() -> None:
    _remove(legacy_notice_path())


def migrate_legacy_plaintext(manager) -> str:
    """把旧版明文恢复码文件原地转成加密交接文件。

    返回 ``absent`` / ``already-protected`` / ``migrated`` / ``removed-stale`` /
    ``removed-unusable`` / ``kept``。只有 ``migrated`` 会写“建议换新”的提示；
    ``kept`` 表示加密写盘失败、原明文仍保留（启动自检会持续告警）。
    """
    path = handoff_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "absent"
    except (OSError, UnicodeDecodeError):
        logger.warning("读取旧恢复码文件失败：%s", path, exc_info=True)
        return "kept"

    if text.lstrip().startswith("{"):
        return "already-protected"

    code = ""
    for line in text.splitlines():
        candidate = line.strip()
        if candidate and not candidate.startswith("#"):
            code = candidate
            break

    root = manager.account_root()
    if root is None or not code:
        _remove(path)
        logger.warning("删除无法迁移的旧恢复码文件（无对应根或内容为空）：%s", path)
        return "removed-unusable"

    if not code_matches_root(code, root):
        # 旧文件里的码已被轮换/作废：它对新密码无效，留着只是明文泄漏面。
        _remove(path)
        logger.warning("删除已失效的旧明文恢复码文件（与当前哈希不符）：%s", path)
        return "removed-stale"

    staged = path.with_name(path.name + ".new")
    try:
        write_handoff(root.principal, code, target=staged)
        os.replace(staged, path)
        # 重命名后再复核一次实际 ACL：移动/替换不能假设描述符一定跟着走。
        if not _tighten_acl(path):
            raise OSError("替换后 ACL 复核失败")
    except Exception:  # noqa: BLE001 - 不删原文件，等下次启动重试
        _remove(staged)
        logger.error(
            "旧明文恢复码转加密失败，原文件保留等待重试：%s", path, exc_info=True
        )
        return "kept"

    _write_legacy_notice()
    logger.warning(
        "已把旧版明文恢复码转为 DPAPI/ACL 交接文件（旧明文已删除）：%s", path
    )
    return "migrated"


def code_matches_root(code: str, root: Any) -> bool:
    stored = str(getattr(root, "recovery_hash", "") or "")
    if not stored:
        return False
    if _accounts.code_matches(code, stored):
        return True
    # 兼容早期绑定本机完整性密钥的恢复码。
    legacy_key = _persistence.integrity_key() or ""
    return bool(legacy_key and _accounts.code_matches(code, stored, legacy_key))


# --------------------------------------------------------------------------- #
# 启动自检
# --------------------------------------------------------------------------- #

def handoff_selfcheck() -> List[str]:
    """返回自检问题列表；空列表表示通过。不会解密任何内容。"""
    purge_expired()
    problems: List[str] = []
    path = handoff_path()
    if not path.is_file():
        return problems

    if not acl_is_current_user_only(path):
        problems.append(
            f"恢复码交接文件 ACL 未收紧到当前用户：{path.name}"
        )

    try:
        raw = path.read_bytes()
    except OSError:
        problems.append(f"恢复码交接文件不可读：{path.name}")
        return problems

    if not raw.lstrip().startswith(b"{"):
        if b"RC-" in raw:
            problems.append("恢复码交接文件仍是明文恢复码")
        return problems

    try:
        wrapper = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        problems.append("恢复码交接文件不是合法 JSON")
        return problems

    protection = str(wrapper.get("protection") or "")
    if protection == "dpapi-current-user":
        if b"RC-" in raw:
            problems.append("DPAPI 交接文件疑似包含明文恢复码")
    elif protection == "acl-fallback":
        if not wrapper.get("expires_at"):
            problems.append("降级交接文件缺少 TTL")
        if b"RC-" not in raw:
            logger.debug("降级交接文件暂无可读恢复码（可能已损坏）")
    else:
        problems.append("恢复码交接文件保护方式未知")
    return problems
