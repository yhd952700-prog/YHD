"""TOTP 双重验证 + 备用码（恢复码）：真后端。

Why this module exists
----------------------
账号安全页此前只有改密与"退出其他设备"；双重验证与"查看当前恢复码"都没有
后端。这一模块把两件事一次做真：

* **TOTP 双重验证**：生成密钥 → otpauth 地址 + 二维码 → 输验证码校验通过
  才真正启用 → 启用后登录多一步验证码 → 备用码生成 → 关掉也要验证。
* **查看当前备用码（恢复码）**：只能看到"共几张 / 已用几张 / 还剩几张"
  与生成时间，看不到码本身的明文（明文只在生成/重新生成那一次出现）；
  查看与重新生成都要先再验证一次身份（当前密码或验证器验证码），且写审计。

安全取舍（写在代码里，免得后人"优化"掉）
--------------------------------------
* **密钥与备用码不住账号树**：账号记录是白名单归一化落盘
  （``kernels.identity._persistence._normalize_account_record``），新字段
  会被悄悄丢掉；更重要的是账号树会进备份包。TOTP 材料住在独立文件
  （``config/totp.json`` + ``config/totp.key``，见 :func:`store_path`），
  备份收集只认账号记录与工作区文件，天然不带 TOTP；再加一道防线——
  ``src/upgrade/backups.py`` 的 ``_PROTECTED_BACKUP_NAMES`` 也把这两个
  文件名列进"不得打包"，哪怕有人把工作区指到数据目录也进不去。
* **密钥加密存**：32 字节 AES-GCM 密钥单独存 ``totp.key``（0600），密钥
  本身是随机生成、只写一次；TOTP 密钥用它加密后才落盘，AAD 绑定账号名
  与用途。密钥文件丢了 → 旧密钥解不开 → 用户仍可用备用码登录后重新
  开启（备用码只存哈希，不依赖这把钥匙）。
* **备用码只存哈希**：``_accounts.hash_code``（SHA-256），明文只在生成
  响应里出现一次；用过一张就删一条哈希，一张只能用一次。
* **防重放**：记下最近一次通过的 TOTP 时间步，同一窗口的码第二次提交
  直接拒绝（验证码 30 秒内只能用一次）。
* **查看有门槛**：备用码的张数与重新生成都要密码或验证码二选一再验证；
  失败记审计、记限流；成功也记审计（谁、何时、用什么方式看的）。
* **老恢复码合同不动**：``/v1/accounts/recovery/reset``（密码重置用的
  单张恢复码）不在这里，查看备用码另起接口；那张码只有哈希、看不到明文，
  本模块不碰它。

登录接线见 :mod:`src.gateway.auth` 的 :func:`authenticate`：密码通过后，
若该账号启用了 TOTP，没有 ``totp_code`` 回 401 ``TOTP_REQUIRED``，错码
回 401 ``TOTP_INVALID``，备用码通过则消费一张后放行。

本模块不碰 ``src/gateway/accounts.py:324`` 与
``src/gateway/local_proof.py:161`` 附近的逻辑（恢复码交接的本机证明），
也不碰网关 code=1 的启动问题。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import struct
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field

from src.common.atomic_write import atomic_write_bytes, atomic_write_text
from src.common.data_paths import data_root
from ..kernels.identity import _accounts, get_identity_manager
from .account_context import AccountContext, deny, require_account_principal
from .policy import TOKEN_HEADER, extract_bearer_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["totp"])

#: 验证器品牌名：otpauth 地址与二维码里显示的发行方。
ISSUER = "Jarvis"
#: 待确认密钥的有效期（秒）：扫码后 10 分钟内必须输验证码确认，过期作废。
PENDING_TTL_SECONDS = 600
#: 一次生成的备用码张数。
BACKUP_CODE_COUNT = 8
#: TOTP 码位数与时间步（RFC 6238 出厂口径：6 位 / 30 秒）。
TOTP_DIGITS = 6
TOTP_PERIOD_SECONDS = 30
#: 校验窗口：当前步 ±1（前后各 30 秒，容忍手机与服务端时钟偏差）。
TOTP_WINDOW = 1
#: 同一账号连续验证失败的上限（5 分钟内 5 次），防在线枚举验证码/备用码。
ATTEMPT_LIMIT = 5
ATTEMPT_WINDOW_SECONDS = 300

TOTP_STORE_ENV = "LIUHAO_TOTP_STORE_FILE"
TOTP_KEY_ENV = "LIUHAO_TOTP_KEY_FILE"

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_STORE_PATH = _REPO_ROOT / "config" / "totp.json"
_DEFAULT_KEY_PATH = _REPO_ROOT / "config" / "totp.key"

#: 备用码字符表：去掉 0/O/1/I，抄写时不易认错。
_BACKUP_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_BACKUP_GROUPS = (4, 4, 4, 4)

_store_lock = threading.RLock()
_attempts: Dict[str, List[float]] = {}
_attempts_lock = threading.Lock()


class TotpError(Exception):
    """TOTP 校验失败（错码 / 重放 / 未启用 / 未设置），不带细节到调用方。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class TotpStoreError(Exception):
    """TOTP 存储不可用（文件损坏 / 密钥损坏 / 写不进去）：fail-closed。"""


# --------------------------------------------------------------------------- #
# 路径
# --------------------------------------------------------------------------- #


def store_path() -> str:
    """TOTP 记录文件位置：显式环境变量 > 数据目录 > 仓库 config/（开发）。"""
    explicit = (os.environ.get(TOTP_STORE_ENV) or "").strip()
    if explicit:
        return explicit
    root = data_root()
    if root is not None:
        return str(root / "config" / "totp.json")
    return str(_DEFAULT_STORE_PATH)


def key_path() -> str:
    """AES-GCM 密钥文件位置：与记录文件同口径，永不进备份。"""
    explicit = (os.environ.get(TOTP_KEY_ENV) or "").strip()
    if explicit:
        return explicit
    root = data_root()
    if root is not None:
        return str(root / "config" / "totp.key")
    return str(_DEFAULT_KEY_PATH)


# --------------------------------------------------------------------------- #
# TOTP 核心（只用标准库：RFC 6238 HMAC-SHA1，与 pyotp 同口径）
# --------------------------------------------------------------------------- #


def generate_secret() -> str:
    """生成 20 字节随机密钥，Base32（去填充，与验证器 App 通用）。"""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def provisioning_uri(secret: str, principal: str, issuer: str = ISSUER) -> str:
    """给验证器 App 扫码 / 手动输入的 otpauth 地址。"""
    from urllib.parse import quote

    return (
        f"otpauth://totp/{quote(issuer)}:{quote(principal)}"
        f"?secret={secret}&issuer={quote(issuer)}"
        f"&algorithm=SHA1&digits={TOTP_DIGITS}&period={TOTP_PERIOD_SECONDS}"
    )


def _decode_secret(secret: str) -> Optional[bytes]:
    text = (secret or "").strip().upper()
    if not text:
        return None
    try:
        return base64.b32decode(text + "=" * (-len(text) % 8))
    except Exception:  # noqa: BLE001 - 坏密钥就是"验不过"，不是崩溃
        return None


def totp_code_at(secret: str, counter: int, digits: int = TOTP_DIGITS) -> Optional[str]:
    """指定时间步的验证码（测试与窗口校验共用同一实现）。"""
    key = _decode_secret(secret)
    if key is None or counter < 0:
        return None
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10**digits)).zfill(digits)


def verify_totp_code(
    secret: str, code: str, *, now: Optional[float] = None, window: int = TOTP_WINDOW
) -> Optional[int]:
    """校验验证码，返回匹配到的时间步；不匹配返回 ``None``。

    只认 6 位数字（空格容忍）；备用码（带横线、16 位）走另一条路，
    不会在这里误判。
    """
    normalized = re.sub(r"\D", "", str(code or ""))
    if len(normalized) != TOTP_DIGITS:
        return None
    moment = time.time() if now is None else float(now)
    step = int(moment // TOTP_PERIOD_SECONDS)
    for offset in range(-window, window + 1):
        counter = step + offset
        expected = totp_code_at(secret, counter)
        if expected is not None and hmac.compare_digest(expected, normalized):
            return counter
    return None


def qr_svg_data_uri(otpauth_uri: str) -> Tuple[Optional[str], Optional[str]]:
    """把 otpauth 地址画成二维码（SVG data URI，前端直接当 <img> 用）。

    ``segno`` 是纯 Python 库（BSD-3-Clause，已声明在 pyproject 基础依赖）；
    没装时返回 ``(None, 原因)``，前端退到手动输入密钥——不编一个假图，
    也不把密钥发给任何在线二维码服务。
    """
    try:
        import segno
    except ImportError:
        return None, "二维码生成组件未安装，请在验证器 App 里手动输入密钥"
    try:
        qr = segno.make(otpauth_uri, error="m")
        svg = qr.svg_inline(scale=6, dark="#000000", light="#ffffff")
        return "data:image/svg+xml;utf8," + urllib.parse.quote(svg, safe=""), None
    except Exception as exc:  # noqa: BLE001 - 画不出来就老实说，别拦着设置
        logger.warning("二维码生成失败：%s", exc)
        return None, "二维码生成失败，请在验证器 App 里手动输入密钥"


# --------------------------------------------------------------------------- #
# 备用码
# --------------------------------------------------------------------------- #


def generate_backup_codes(count: int = BACKUP_CODE_COUNT) -> List[str]:
    """生成备用码：每张 16 位，分 4 组显示（``XXXX-XXXX-XXXX-XXXX``）。"""
    codes = []
    for _ in range(count):
        raw = "".join(secrets.choice(_BACKUP_ALPHABET) for _ in range(16))
        codes.append("-".join(raw[i : i + 4] for i in range(0, 16, 4)))
    return codes


def _canonical_backup_code(code: str) -> str:
    """备用码的比对形态：去横线/空格、全大写——抄的时候少打横线也能用。"""
    return re.sub(r"[^A-Z0-9]", "", str(code or "").upper())


def _hash_backup_code(code: str) -> str:
    return _accounts.hash_code(_canonical_backup_code(code))


def _backup_code_index(hashes: List[str], code: str) -> Optional[int]:
    target = _hash_backup_code(code)
    for index, stored in enumerate(hashes):
        if hmac.compare_digest(str(stored), target):
            return index
    return None


# --------------------------------------------------------------------------- #
# 加密与存储
# --------------------------------------------------------------------------- #


def _load_or_create_key() -> bytes:
    """读 AES-GCM 密钥；没有就生成一把 32 字节的写下（0600，只写一次）。"""
    path = Path(key_path())
    if path.is_file():
        try:
            key = path.read_bytes()
        except OSError as exc:
            raise TotpStoreError(f"无法读取 TOTP 密钥文件：{exc}") from exc
        if len(key) != 32:
            raise TotpStoreError("TOTP 密钥文件损坏（长度不对），请删除后重新开启双重验证")
        return key
    key = secrets.token_bytes(32)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(str(path), key)
        _secure_chmod(str(path))
    except OSError as exc:
        raise TotpStoreError(f"无法保存 TOTP 密钥文件：{exc}") from exc
    return key


def _secure_chmod(path: str) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        # Windows 上 0600 没有同等语义：不阻断，文件仍在数据目录内。
        pass


def _encrypt_secret(principal: str, secret: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = _load_or_create_key()
    nonce = secrets.token_bytes(12)
    aad = f"totp-secret-v1:{principal}".encode("utf-8")
    blob = AESGCM(key).encrypt(nonce, secret.encode("utf-8"), aad)
    return base64.b64encode(nonce + blob).decode("ascii")


def _decrypt_secret(principal: str, stored: str) -> Optional[str]:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        raw = base64.b64decode(str(stored or "").encode("ascii"), validate=True)
        nonce, blob = raw[:12], raw[12:]
        key = _load_or_create_key()
        aad = f"totp-secret-v1:{principal}".encode("utf-8")
        return AESGCM(key).decrypt(nonce, blob, aad).decode("utf-8")
    except Exception:  # noqa: BLE001 - 解不开就是"没有可用密钥"，不展开原因
        logger.warning("TOTP 密钥解密失败 principal=%s", principal)
        return None


def _empty_record() -> Dict[str, Any]:
    return {
        "enabled": False,
        "secret_enc": "",
        "enabled_at": None,
        "backup_hashes": [],
        "backup_total": 0,
        "backup_generated_at": None,
        "backup_last_used_at": None,
        "pending_secret_enc": None,
        "pending_expires_at": None,
        "last_used_step": None,
    }


def _load_store() -> Dict[str, Any]:
    path = Path(store_path())
    if not path.is_file():
        return {"version": 1, "principals": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TotpStoreError(f"TOTP 记录文件损坏，拒绝读写：{exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("principals"), dict):
        raise TotpStoreError("TOTP 记录文件格式不对，拒绝读写")
    return data


def _save_store(data: Dict[str, Any]) -> None:
    path = Path(store_path())
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(str(path), json.dumps(data, ensure_ascii=False))
        _secure_chmod(str(path))
    except OSError as exc:
        raise TotpStoreError(f"无法保存 TOTP 记录：{exc}") from exc


def _get_record(principal: str) -> Dict[str, Any]:
    """读一条记录（不存在返回空记录的副本；调用方改完要自己调 ``_put_record``）。"""
    data = _load_store()
    raw = data["principals"].get(principal)
    record = _empty_record()
    if isinstance(raw, dict):
        for key in record:
            if key in raw:
                record[key] = raw[key]
    return record


def _put_record(principal: str, record: Dict[str, Any]) -> None:
    data = _load_store()
    data["principals"][principal] = record
    _save_store(data)


def _delete_record(principal: str) -> None:
    data = _load_store()
    data["principals"].pop(principal, None)
    _save_store(data)


# --------------------------------------------------------------------------- #
# 限流与审计
# --------------------------------------------------------------------------- #


def _check_rate_limit(principal: str) -> None:
    now = time.time()
    with _attempts_lock:
        hits = [t for t in _attempts.get(principal, []) if now - t < ATTEMPT_WINDOW_SECONDS]
        _attempts[principal] = hits
        if len(hits) >= ATTEMPT_LIMIT:
            raise deny(
                429,
                "TOTP_RATE_LIMITED",
                "尝试次数过多，请 5 分钟后再试。",
            )


def _record_failure(principal: str) -> None:
    with _attempts_lock:
        _attempts.setdefault(principal, []).append(time.time())


def _clear_failures(principal: str) -> None:
    with _attempts_lock:
        _attempts.pop(principal, None)


def reset_rate_limit() -> None:
    """测试用：清掉进程内限流计数。"""
    with _attempts_lock:
        _attempts.clear()


def _audit_totp(
    *,
    action: str,
    principal: str,
    outcome: str,
    details: Optional[Dict[str, Any]] = None,
    error_code: str = "",
    actor: Optional[str] = None,
    target: Optional[str] = None,
) -> bool:
    """写一条 TOTP 审计（哈希链，与账号事件同一条流）。

    细节里永远不带密钥、验证码、备用码明文。审计写失败不阻断业务，
    返回值让调用方在响应里如实告知。

    默认 actor 与 target 都是 ``principal``（自己动自己的 TOTP）；主账号
    帮子账号重置时（``totp_admin_reset``）显式传 ``actor=主账号``、
    ``target=子账号``，审计链里才能查到"谁对谁做的"。
    """
    effective_actor = actor or principal
    effective_target = target or principal
    payload: Dict[str, Any] = {
        "action": f"account.totp.{action}",
        "actor": effective_actor,
        "target": effective_target,
    }
    if error_code:
        # 审计脱敏规则会把全大写多段常量当环境变量名：落小写原因。
        payload["error_code"] = error_code.lower()
    if details:
        payload.update(details)
    try:
        return bool(
            get_identity_manager().record_event(
                action=f"account.totp.{action}",
                actor=effective_actor,
                target=effective_target,
                outcome=outcome,
                details=payload,
            )
        )
    except Exception as exc:  # noqa: BLE001 - 审计不可用不阻断业务
        logger.warning("TOTP 审计写入失败 action=%s：%s", action, exc)
        return False


# --------------------------------------------------------------------------- #
# 登录用的服务函数（供 src.gateway.auth 调用）
# --------------------------------------------------------------------------- #


def is_totp_enabled(principal: str) -> bool:
    """该账号是否开启了双重验证。存储损坏时抛错（fail-closed，不当作未开启）。"""
    with _store_lock:
        return bool(_get_record(principal).get("enabled"))


def backup_codes_remaining(principal: str) -> int:
    with _store_lock:
        record = _get_record(principal)
    if not record.get("enabled"):
        return 0
    return len(record.get("backup_hashes") or [])


def verify_login_second_factor(
    principal: str, code: str, *, now: Optional[float] = None
) -> Tuple[str, Optional[int]]:
    """登录第二步校验。

    返回 ``(方式, 备用码余量)``：方式为 ``"totp"`` 或 ``"backup_code"``；
    备用码通过时余量为用完这张后还剩几张，TOTP 通过时为 ``None``。
    失败抛 :class:`TotpError`（``reason`` 为 ``invalid`` / ``replay`` /
    ``not_enabled`` / ``unavailable``）。
    """
    with _store_lock:
        record = _get_record(principal)
        if not record.get("enabled"):
            raise TotpError("not_enabled")
        secret = _decrypt_secret(principal, str(record.get("secret_enc") or ""))
        if secret is None:
            raise TotpError("unavailable")
        counter = verify_totp_code(secret, code, now=now)
        if counter is not None:
            last_used = record.get("last_used_step")
            if last_used is not None and counter <= int(last_used):
                raise TotpError("replay")
            record["last_used_step"] = counter
            _put_record(principal, record)
            return ("totp", None)
        index = _backup_code_index(list(record.get("backup_hashes") or []), code)
        if index is not None:
            record["backup_hashes"].pop(index)
            record["backup_last_used_at"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            )
            _put_record(principal, record)
            return ("backup_code", len(record["backup_hashes"]))
        raise TotpError("invalid")


# --------------------------------------------------------------------------- #
# 恢复码重置后一并关双重验证（任务 4(a)）
# --------------------------------------------------------------------------- #


def disable_totp_after_recovery(principal: str) -> Dict[str, Any]:
    """恢复码重置成功后关掉**该账号**的双重验证。

    恢复码本来就是开锁的最后手段：验证器丢了、备用码也丢了，用户只能靠
    恢复码重新进来；如果 TOTP 还开着，进来之后照样被第二步拦住，等于彻底
    锁死。所以"用恢复码重置密码"必须一并关掉 TOTP，之后用户重新绑定验证器。

    只关 ``principal`` 自己的 TOTP：调用方（恢复重置钩子）从重置成功的响应
    里取 ``account.principal``，绝不按请求体里的任何名字关别人的。

    故意**不吊销**该账号的其他令牌：恢复重置刚签发的新令牌就是用户这次的
    登录，吊销全部会把新令牌一起杀掉，用户进不来。旧会话 15 分钟后自然过期，
    且密码已经换了，刷新令牌续期也要重新登录。
    """
    with _store_lock:
        record = _get_record(principal)
        if not record.get("enabled"):
            return {"was_enabled": False, "disabled": False, "audit_written": True}
        had_backup_codes = len(record.get("backup_hashes") or []) > 0
        _delete_record(principal)
    _clear_failures(principal)
    written = _audit_totp(
        action="disabled",
        principal=principal,
        outcome="allowed",
        details={
            "reason": "recovery_reset",
            "verified_with": "recovery_code",
            "had_backup_codes": had_backup_codes,
        },
    )
    return {"was_enabled": True, "disabled": True, "audit_written": written}


#: 恢复码重置的路由：钩子只认这一条，其他请求零影响。
_RECOVERY_RESET_PATH = "/v1/accounts/recovery/reset"


def _apply_recovery_totp_outcome(status_code: int, body: bytes) -> Optional[Dict[str, Any]]:
    """给恢复重置的成功响应补上 TOTP 关闭结果；不动的响应返回 ``None``。

    只认 200 + 能解析的 JSON + 响应里有 ``account.principal``。任何一步对不
    上都返回 ``None``（调用方原样转发响应，绝不把一次已成功的密码重置变成失败）。
    """
    if status_code != 200:
        return None
    try:
        payload = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        logger.warning("恢复重置钩子：响应不是 JSON，原样转发（TOTP 未动）。")
        return None
    if not isinstance(payload, dict):
        return None
    account = payload.get("account")
    principal = account.get("principal") if isinstance(account, dict) else None
    if not isinstance(principal, str) or not principal:
        logger.warning("恢复重置钩子：响应里没有 account.principal，原样转发（TOTP 未动）。")
        return None
    try:
        result = disable_totp_after_recovery(principal)
    except Exception:  # noqa: BLE001 - 关 TOTP 失败不能反咬已成功的密码重置
        logger.exception("恢复重置钩子：关闭 %s 的双重验证失败", principal)
        payload["totp_disabled_by_recovery"] = False
        payload["totp_warning"] = (
            "双重验证没能随本次恢复关闭，请让主账号在成员管理里为你重置双重验证，"
            "或重新绑定验证器前先联系主账号。"
        )
        return payload
    payload["totp_was_enabled"] = bool(result["was_enabled"])
    payload["totp_disabled_by_recovery"] = bool(result["disabled"])
    if result["disabled"] and not result["audit_written"]:
        payload["totp_warning"] = "双重验证已随本次恢复关闭，但审计未写入记录。"
    return payload


class RecoveryTotpHookMiddleware:
    """恢复码重置成功后一并关闭该账号双重验证的窄钩子（纯 ASGI，任务 4(a)）。

    为什么是中间件、而不是改 ``recovery_reset`` 本体：本体在禁区
    ``src/gateway/accounts.py`` 里（恢复码交接的本机证明也在禁区文件里），
    这一轮不许动。其他路子都试过、都不成立，见模块底部的
    :func:`install_recovery_totp_hook` 与交卷记录：

    * 内核钩子（``manager.consume_recovery_code`` 里调 TOTP）——分层反了：
      身份内核在下、TOTP 存储在网关层，内核调网关是向上的依赖；而且
      ``consume_recovery_code`` 只被 ``recovery_reset`` 调用，在内核里挂钩子
      等于把同一个行为藏到更难找的地方，失败时还没法往响应里写警告。
    * 前端引导（重置后让用户自己关 TOTP）——走不通：现有关闭接口要验证码
      或备用码，而用户丢的正是这两样；没有服务端的新证明，任何"重置后凭
      密码关 TOTP"的接口都是安全倒退（拿到密码就能拆掉双重验证）。
    * 给恢复签发的令牌打标记、再凭标记关 TOTP——标记要加在
      ``accounts.py`` 的签发调用处，同样要动禁区。

    所以只剩这一条干净路子：响应回来后、发给客户端之前，看一眼"是不是
    ``POST /v1/accounts/recovery/reset`` 成功了"，是就按响应里的
    ``account.principal`` 关掉**这个账号**的 TOTP，并把结果写进响应体
    （``totp_was_enabled`` / ``totp_disabled_by_recovery``），前端据此
    告诉用户"双重验证已随恢复关闭，请重新绑定验证器"。

    钩子绝不改变重置本身的成败：密码已经改了、令牌已经签了，关 TOTP 失败
    也只在响应里加 ``totp_warning``，不翻案。
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        if (
            str(scope.get("method") or "").upper() != "POST"
            or str(scope.get("path") or "") != _RECOVERY_RESET_PATH
        ):
            await self.app(scope, receive, send)
            return
        # 只缓存这一条路由的响应：先扣住 response.start（要改 content-length），
        # 等 body 收齐、关完 TOTP、改完 JSON，再原样发出去。
        state: Dict[str, Any] = {"start": None, "body": bytearray(), "sent": False}

        async def capturing_send(message: Dict[str, Any]) -> None:
            message_type = message.get("type")
            if message_type == "http.response.start":
                state["start"] = message
                return
            if message_type == "http.response.pathsend":
                # 文件响应不在本钩子的处理范围：原样放行。
                if state["start"] is not None:
                    await send(state["start"])
                    state["start"] = None
                state["sent"] = True
                await send(message)
                return
            if message_type == "http.response.body":
                state["body"].extend(message.get("body") or b"")
                if message.get("more_body"):
                    return
                await self._finish(state, send)
                return
            await send(message)

        try:
            await self.app(scope, receive, capturing_send)
        except Exception:
            # 下游崩了（端点异常早被内层的 ExceptionMiddleware 转成错误响应，
            # 走不到这里）：已扣住的响应尽量原样补发，别让客户端空等；异常本身
            # 记下来，不吞。
            logger.exception("恢复重置钩子：下游处理异常，尝试原样补发已缓存的响应。")
            if not state["sent"] and state["start"] is not None:
                try:
                    await send(state["start"])
                    await send(
                        {
                            "type": "http.response.body",
                            "body": bytes(state["body"]),
                            "more_body": False,
                        }
                    )
                except Exception:  # noqa: BLE001 - 补发失败也只记日志
                    logger.exception("恢复重置钩子：补发缓存响应失败。")
            raise

    async def _finish(self, state: Dict[str, Any], send: Any) -> None:
        start = state["start"]
        body = bytes(state["body"])
        state["sent"] = True
        if start is None:
            return
        try:
            payload = _apply_recovery_totp_outcome(int(start.get("status") or 0), body)
        except Exception:  # noqa: BLE001 - 钩子自身的任何异常都不能断了响应
            logger.exception("恢复重置钩子：处理响应失败，原样转发。")
            payload = None
        if payload is None:
            await send(start)
            await send({"type": "http.response.body", "body": body, "more_body": False})
            return
        new_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = [
            (key, value)
            for key, value in (start.get("headers") or [])
            if bytes(key).lower() != b"content-length"
        ]
        headers.append((b"content-length", str(len(new_body)).encode("latin-1")))
        new_start = dict(start)
        new_start["headers"] = headers
        await send(new_start)
        await send({"type": "http.response.body", "body": new_body, "more_body": False})


def install_recovery_totp_hook(app: Any) -> None:
    """把恢复重置钩子挂进网关（由 ``src.gateway.main.create_app`` 调用）。

    必须在所有中间件之前注册 = 最内层：后注册的中间件在外层，GZip 在外层
    会把响应体压成 gzip，钩子就读不到原始 JSON；最内层时下游直接就是路由，
    拿到的是恢复接口原样返回的 JSONResponse 字节。
    """
    app.add_middleware(RecoveryTotpHookMiddleware)


# --------------------------------------------------------------------------- #
# 端点
# --------------------------------------------------------------------------- #


def _require_self(context: AccountContext) -> str:
    """TOTP 只管自己的账号：ViewAs 只读视角下，安全设置不对别人可见。"""
    if getattr(context, "viewed_as_by", None):
        raise deny(403, "ACCOUNT_SCOPE_DENIED", "无权执行该操作")
    return context.principal


def _revoke_other_tokens(
    principal: str,
    request_headers: Tuple[Optional[str], Optional[str]],
    refresh_token: Optional[str] = None,
) -> int:
    """吊销该账号除当前这次登录外的全部令牌（开启/关闭双重验证时调用）。

    当前会话是刚通过验证的（确认开启时输了验证码、关闭时也输了），留着它，
    让用户有时间保存备用码；别的设备/会话一律踢掉，下次请求即 401。
    只覆盖本服务启动后签发的令牌（与"退出其他设备"同一范围）。
    失败返回 -1（调用方在响应里如实告知，不谎报"已踢掉"）。
    """
    from ..security import get_jwt_handler

    keep: set = set()
    token = extract_bearer_token(*request_headers)
    handler = get_jwt_handler()
    for candidate, must_match in ((token, None), (refresh_token, principal)):
        if not candidate:
            continue
        try:
            payload = handler.decode_signature_only(candidate, verify_exp=False)
        except Exception:  # noqa: BLE001 - 坏令牌不当"当前登录"，直接跳过
            continue
        if must_match is not None and str(getattr(payload, "sub", "") or "") != must_match:
            continue
        jti = str(getattr(payload, "jti", "") or "")
        if jti:
            keep.add(jti)
    try:
        return int(handler.revoke_all_user_tokens_except(principal, keep))
    except Exception as exc:  # noqa: BLE001 - 吊销失败不掩盖主操作
        logger.warning("TOTP 变更后吊销其他令牌失败 principal=%s：%s", principal, exc)
        return -1


def _revoke_all_tokens(principal: str) -> int:
    """吊销该账号的全部令牌（主账号帮子账号重置双重验证时用）。

    与 :func:`_revoke_other_tokens` 不同：这里没有"当前这次登录"要保留 ——
    当前登录是主账号的，不属于目标账号；目标账号的旧会话必须全部重登，
    否则一把旧令牌还能顶着已关掉的双重验证继续用。
    失败返回 -1（调用方在响应里如实告知，不谎报"已踢掉"）。
    """
    from ..security import get_jwt_handler

    try:
        return int(get_jwt_handler().revoke_all_user_tokens_except(principal, set()))
    except Exception as exc:  # noqa: BLE001 - 吊销失败不掩盖主操作
        logger.warning("TOTP 管理员重置后吊销令牌失败 principal=%s：%s", principal, exc)
        return -1


class TotpCodeRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=64, description="验证器 6 位验证码或备用码")
    refresh_token: Optional[str] = Field(
        default=None, max_length=4096, description="当前会话的刷新令牌（用于保留本次登录）"
    )


class TotpGateRequest(BaseModel):
    password: Optional[str] = Field(default=None, max_length=512, description="当前密码")
    totp_code: Optional[str] = Field(
        default=None, max_length=64, description="验证器 6 位验证码"
    )


def _verify_view_gate(principal: str, payload: TotpGateRequest) -> str:
    """备用码查看/重新生成的再验证：密码与验证码二选一。返回用的方式。"""
    password = payload.password
    totp_code = (payload.totp_code or "").strip()
    if bool(password) == bool(totp_code):
        raise deny(
            400,
            "TOTP_GATE_REQUIRED",
            "请再验证一次身份：输入当前密码，或输入验证器上的 6 位验证码（二选一）。",
        )
    if password:
        # 密码校验走登录同一套 PBKDF2 记录（auth 模块的凭据库），这里不自造
        # 第二份密码判定；循环导入用函数内导入避开。
        from .auth import resolve_secret_store, verify_credential

        record = resolve_secret_store().credential_for(principal)
        if not verify_credential(password, record):
            raise deny(403, "TOTP_GATE_DENIED", "密码不正确，无法查看备用码。")
        return "password"
    with _store_lock:
        record = _get_record(principal)
    if not record.get("enabled"):
        raise deny(409, "TOTP_NOT_ENABLED", "该账号没有开启双重验证。")
    secret = _decrypt_secret(principal, str(record.get("secret_enc") or ""))
    if secret is None or verify_totp_code(secret, totp_code) is None:
        raise deny(403, "TOTP_GATE_DENIED", "验证码不正确，无法查看备用码。")
    return "totp"


@router.get("/accounts/totp/status", response_model=Dict[str, Any])
def totp_status(
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """双重验证状态：只报"开没开"，备用码张数要过再验证门槛才给看。"""
    principal = _require_self(context)
    with _store_lock:
        record = _get_record(principal)
    pending_expires = record.get("pending_expires_at")
    has_pending = bool(record.get("pending_secret_enc")) and bool(pending_expires) and (
        time.time() < float(pending_expires)
    )
    return {
        "enabled": bool(record.get("enabled")),
        "enabled_at": record.get("enabled_at"),
        "has_pending": has_pending,
        "pending_expires_at": pending_expires if has_pending else None,
    }


@router.post("/accounts/totp/setup/start", response_model=Dict[str, Any])
def totp_setup_start(
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """开始设置：生成待确认密钥，返回 otpauth 地址 + 二维码 + 明文密钥。

    密钥在确认前只以"待确认"形态存放（``pending_*``），不生效；确认失败
    或过期就作废，不会留下一个"开了一半"的双重验证。
    """
    principal = _require_self(context)
    with _store_lock:
        record = _get_record(principal)
        if record.get("enabled"):
            raise deny(409, "TOTP_ALREADY_ENABLED", "双重验证已经开启，先关闭才能重新设置。")
        secret = generate_secret()
        record["pending_secret_enc"] = _encrypt_secret(principal, secret)
        record["pending_expires_at"] = time.time() + PENDING_TTL_SECONDS
        _put_record(principal, record)
    uri = provisioning_uri(secret, principal)
    qr_uri, qr_unavailable = qr_svg_data_uri(uri)
    _audit_totp(action="setup.started", principal=principal, outcome="allowed")
    return {
        "otpauth_uri": uri,
        "secret": secret,
        "qr_svg_data_uri": qr_uri,
        "qr_unavailable": qr_unavailable,
        "expires_in_seconds": PENDING_TTL_SECONDS,
    }


@router.post("/accounts/totp/setup/confirm", response_model=Dict[str, Any])
def totp_setup_confirm(
    payload: TotpCodeRequest,
    context: AccountContext = Depends(require_account_principal),
    x_liuhao_token: Optional[str] = Header(default=None, alias=TOKEN_HEADER),
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    """确认开启：验证码对上待确认密钥才真正启用，并一次生成备用码。

    启用后立即吊销该账号的其他令牌（当前这次登录保留，让用户有时间保存
    备用码；15 分钟后 access 过期，续期/重登都要走第二步验证）。
    """
    principal = _require_self(context)
    _check_rate_limit(principal)
    with _store_lock:
        record = _get_record(principal)
        if record.get("enabled"):
            raise deny(409, "TOTP_ALREADY_ENABLED", "双重验证已经开启。")
        pending_enc = str(record.get("pending_secret_enc") or "")
        expires = record.get("pending_expires_at")
        if not pending_enc or expires is None or time.time() >= float(expires):
            if pending_enc:
                record["pending_secret_enc"] = None
                record["pending_expires_at"] = None
                _put_record(principal, record)
            _audit_totp(
                action="setup.confirm",
                principal=principal,
                outcome="denied",
                error_code="totp_pending_expired",
            )
            raise deny(
                409,
                "TOTP_PENDING_EXPIRED",
                "设置已过期，请重新开始设置并在 10 分钟内确认。",
            )
        secret = _decrypt_secret(principal, pending_enc)
        if secret is None:
            raise deny(503, "TOTP_UNAVAILABLE", "服务暂时不可用，请稍后重试。")
        counter = verify_totp_code(secret, payload.code)
        if counter is None:
            _record_failure(principal)
            _audit_totp(
                action="setup.confirm",
                principal=principal,
                outcome="denied",
                error_code="totp_code_invalid",
            )
            raise deny(403, "TOTP_CODE_INVALID", "验证码不正确，请核对验证器上的 6 位数字后重试。")
        backup_codes = generate_backup_codes()
        record["enabled"] = True
        record["secret_enc"] = pending_enc
        record["enabled_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        record["backup_hashes"] = [_hash_backup_code(code) for code in backup_codes]
        record["backup_total"] = len(backup_codes)
        record["backup_generated_at"] = record["enabled_at"]
        record["backup_last_used_at"] = None
        record["pending_secret_enc"] = None
        record["pending_expires_at"] = None
        record["last_used_step"] = counter
        _put_record(principal, record)
    _clear_failures(principal)
    revoked = _revoke_other_tokens(
        principal, (x_liuhao_token, authorization), payload.refresh_token
    )
    written = _audit_totp(
        action="enabled",
        principal=principal,
        outcome="allowed",
        details={"backup_total": len(backup_codes), "revoked_others": revoked},
    )
    response: Dict[str, Any] = {
        "enabled": True,
        "backup_codes": backup_codes,
        "backup_total": len(backup_codes),
        "revoked_others": revoked,
        "notice": "双重验证已开启。备用码只显示这一次，请立刻保存；其他设备上的登录已退出，当前会话 15 分钟后过期，届时请用验证码重新登录。",
        "audit_written": written,
    }
    if not written:
        response["audit_warning"] = "审计未写入：双重验证已开启，但未留下审计记录。"
    if revoked < 0:
        response["revocation_warning"] = "其他设备上的登录未能确认退出，请在账号安全页再点一次「退出其他设备」。"
    return response


@router.post("/accounts/totp/setup/cancel", response_model=Dict[str, Any])
def totp_setup_cancel(
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """取消设置：丢弃待确认密钥（已开启的不受影响）。"""
    principal = _require_self(context)
    with _store_lock:
        record = _get_record(principal)
        record["pending_secret_enc"] = None
        record["pending_expires_at"] = None
        _put_record(principal, record)
    return {"cancelled": True}


@router.post("/accounts/totp/disable", response_model=Dict[str, Any])
def totp_disable(
    payload: TotpCodeRequest,
    context: AccountContext = Depends(require_account_principal),
    x_liuhao_token: Optional[str] = Header(default=None, alias=TOKEN_HEADER),
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    """关闭双重验证：必须用当前验证码或一张未用的备用码验证。

    关闭后该账号的密钥与全部备用码一并销毁；其他设备上的登录同样退出，
    只留当前这次登录（15 分钟后过期，届时按无双重验证登录）。
    """
    principal = _require_self(context)
    _check_rate_limit(principal)
    with _store_lock:
        record = _get_record(principal)
        if not record.get("enabled"):
            raise deny(409, "TOTP_NOT_ENABLED", "双重验证本来就没有开启。")
        secret = _decrypt_secret(principal, str(record.get("secret_enc") or ""))
        if secret is None:
            raise deny(503, "TOTP_UNAVAILABLE", "服务暂时不可用，请稍后重试。")
        counter = verify_totp_code(secret, payload.code)
        consumed_backup = False
        if counter is not None:
            last_used = record.get("last_used_step")
            if last_used is not None and counter <= int(last_used):
                counter = None
        if counter is None:
            index = _backup_code_index(list(record.get("backup_hashes") or []), payload.code)
            if index is None:
                _record_failure(principal)
                _audit_totp(
                    action="disable",
                    principal=principal,
                    outcome="denied",
                    error_code="totp_code_invalid",
                )
                raise deny(
                    403,
                    "TOTP_CODE_INVALID",
                    "验证码不正确，请核对验证器上的 6 位数字（或一张未用的备用码）后重试。",
                )
            consumed_backup = True
        had_backup_codes = len(record.get("backup_hashes") or []) > 0
        _delete_record(principal)
    _clear_failures(principal)
    revoked = _revoke_other_tokens(
        principal, (x_liuhao_token, authorization), payload.refresh_token
    )
    written = _audit_totp(
        action="disabled",
        principal=principal,
        outcome="allowed",
        details={
            "revoked_others": revoked,
            "had_backup_codes": had_backup_codes,
            "verified_with": "backup_code" if consumed_backup else "totp",
        },
    )
    response: Dict[str, Any] = {
        "disabled": True,
        "revoked_others": revoked,
        "notice": "双重验证已关闭，密钥与备用码已销毁；其他设备上的登录已退出。",
        "audit_written": written,
    }
    if not written:
        response["audit_warning"] = "审计未写入：双重验证已关闭，但未留下审计记录。"
    if revoked < 0:
        response["revocation_warning"] = "其他设备上的登录未能确认退出，请在账号安全页再点一次「退出其他设备」。"
    return response


class TotpAdminResetRequest(BaseModel):
    target: str = Field(..., min_length=1, max_length=128, description="要重置双重验证的子账号")


@router.post("/accounts/totp/admin-reset", response_model=Dict[str, Any])
def totp_admin_reset(
    payload: TotpAdminResetRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """主账号帮子账号重置双重验证（任务 4(b)）。

    子账号验证器丢了、备用码也丢了，自己关不掉（关闭接口要验证码或备用码），
    又没有恢复码可走：只能由主账号在成员管理里帮他重置。重置后该账号的密钥
    与全部备用码一并销毁，他其他设备上的登录全部退出，之后只用密码登录，
    再到自己的账号安全页重新绑定验证器。

    规矩（Joseph 2026-11-15 ~10:03：子账号只能动自己的东西）：

    * 只有主账号能调；子账号调一律 403（对别人、对主账号、对自己都一样）；
    * 目标必须是已存在的普通成员；主账号不能拿这个接口重置自己的 TOTP
      （自己的走账号安全页现有流程），也不能重置主账号；
    * 目标没开 TOTP → 409，不谎报重置成功；
    * 审计记 actor=主账号、target=子账号（``account.totp.reset_by_admin``），
      主账号在审计链里能查到，子账号那边不另行通知——按 2026-11-14 定好的
      主/子账号规矩，主账号的操作不在子账号侧留痕。
    """
    actor = context.principal
    if getattr(context, "viewed_as_by", None):
        raise deny(403, "ACCOUNT_SCOPE_DENIED", "无权执行该操作")
    if not context.is_root:
        raise deny(403, "TOTP_ADMIN_ROOT_ONLY", "只有主账号能帮别人重置双重验证。")
    target = (payload.target or "").strip()
    if target == actor:
        raise deny(
            403,
            "TOTP_ADMIN_SELF",
            "不能用这个接口重置自己的双重验证，请到账号安全页关闭。",
        )
    node = get_identity_manager().get_account(target)
    if node is None:
        raise deny(404, "ACCOUNT_NOT_FOUND", "账号不存在。")
    if node.is_root:
        raise deny(403, "TOTP_ADMIN_TARGET_ROOT", "不能重置主账号的双重验证。")
    with _store_lock:
        record = _get_record(target)
        if not record.get("enabled"):
            raise deny(409, "TOTP_NOT_ENABLED", "该账号没有开启双重验证，无需重置。")
        had_backup_codes = len(record.get("backup_hashes") or []) > 0
        _delete_record(target)
    _clear_failures(target)
    revoked = _revoke_all_tokens(target)
    written = _audit_totp(
        action="reset_by_admin",
        principal=target,
        actor=actor,
        target=target,
        outcome="allowed",
        details={"had_backup_codes": had_backup_codes, "revoked_others": revoked},
    )
    response: Dict[str, Any] = {
        "reset": True,
        "target": target,
        "revoked_others": revoked,
        "notice": (
            "该账号的双重验证已关闭，密钥与备用码已销毁；他其他设备上的登录已退出，"
            "之后只用密码就能登录，请让他到自己的账号安全页重新绑定验证器。"
        ),
        "audit_written": written,
    }
    if not written:
        response["audit_warning"] = "审计未写入：双重验证已重置，但未留下审计记录。"
    if revoked < 0:
        response["revocation_warning"] = (
            "该账号其他设备上的登录未能确认退出，请让他手动退出或稍后重试。"
        )
    return response


@router.post("/accounts/totp/backup-codes/view", response_model=Dict[str, Any])
def totp_backup_codes_view(
    payload: TotpGateRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """查看当前备用码：只返回张数（共几张 / 已用几张 / 还剩几张），不返回明文。

    明文只在生成/重新生成那一次出现——因为服务端只存哈希，"查看明文"
    在技术上就不存在；要码就重新生成（旧码全部作废）。
    查看本身要再验证一次身份（密码或验证码），成功与失败都写审计。
    """
    principal = _require_self(context)
    _check_rate_limit(principal)
    try:
        gate = _verify_view_gate(principal, payload)
    except Exception:
        _record_failure(principal)
        _audit_totp(
            action="backup_codes.view",
            principal=principal,
            outcome="denied",
            error_code="totp_gate_denied",
        )
        raise
    with _store_lock:
        record = _get_record(principal)
    total = int(record.get("backup_total") or 0)
    remaining = len(record.get("backup_hashes") or [])
    _clear_failures(principal)
    written = _audit_totp(
        action="backup_codes.viewed",
        principal=principal,
        outcome="allowed",
        details={"gate": gate, "remaining": remaining, "total": total},
    )
    return {
        "total": total,
        "remaining": remaining,
        "used": max(0, total - remaining),
        "generated_at": record.get("backup_generated_at"),
        "last_used_at": record.get("backup_last_used_at"),
        "audit_written": written,
    }


@router.post("/accounts/totp/backup-codes/regenerate", response_model=Dict[str, Any])
def totp_backup_codes_regenerate(
    payload: TotpGateRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """重新生成备用码：同样要先再验证一次身份；旧码全部立即作废，新码只显示这一次。"""
    principal = _require_self(context)
    _check_rate_limit(principal)
    try:
        gate = _verify_view_gate(principal, payload)
    except Exception:
        _record_failure(principal)
        _audit_totp(
            action="backup_codes.regenerate",
            principal=principal,
            outcome="denied",
            error_code="totp_gate_denied",
        )
        raise
    with _store_lock:
        record = _get_record(principal)
        if not record.get("enabled"):
            raise deny(409, "TOTP_NOT_ENABLED", "该账号没有开启双重验证。")
        backup_codes = generate_backup_codes()
        record["backup_hashes"] = [_hash_backup_code(code) for code in backup_codes]
        record["backup_total"] = len(backup_codes)
        record["backup_generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        record["backup_last_used_at"] = None
        _put_record(principal, record)
    _clear_failures(principal)
    written = _audit_totp(
        action="backup_codes.regenerated",
        principal=principal,
        outcome="allowed",
        details={"gate": gate, "backup_total": len(backup_codes)},
    )
    response: Dict[str, Any] = {
        "backup_codes": backup_codes,
        "backup_total": len(backup_codes),
        "remaining": len(backup_codes),
        "notice": "新的备用码只显示这一次，旧码已全部作废，请立刻保存。",
        "audit_written": written,
    }
    if not written:
        response["audit_warning"] = "审计未写入：备用码已重新生成，但未留下审计记录。"
    return response
