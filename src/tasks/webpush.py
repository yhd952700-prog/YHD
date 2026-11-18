"""PWA Web Push（大升级第二季 V2c）：简报与提醒推到已安装的手机端。

口径（写死，不留模糊地带）：
* **默认关**：总开关 ``enabled`` 默认 False；关着时不发任何推送、
  订阅接口也拒绝登记（先开开关再订阅，顺序不许反）。
* **VAPID 密钥本地生成**：EC P-256 密钥对在本机数据目录生成，私钥
  文件 0600、绝不进 git/日志/接口响应；公钥（applicationServerKey）
  供浏览器订阅用，可以公开。
* **推送内容最小化**：提醒推送只写「贾维斯提醒 / 你有一条提醒到了」，
  简报推送只写「晨间简报 / 今日简报已生成」，**绝不带提醒标题、简报
  正文等敏感内容**——锁屏上谁都能看见通知栏，正文要打开应用才看得到。
* **协议自实现**：Web Push（RFC 8291 aes128gcm 加密 + VAPID
  RFC 8292 ES256 JWT）用已在册的 ``cryptography`` 实现，不新增依赖。
* 订阅按账号存（``<数据目录>/push/subscriptions.json``）；推送端点
  返回 404/410 的订阅视为已失效，发送时自动清除，不反复打死端点。
* 提醒通知器是机器级通道（与 outbox/托盘同口径）：提醒响时给本机
  全部订阅推一条通用提醒；简报推送只发简报所属账号的订阅。
"""

from __future__ import annotations

import base64
import hashlib
import hmac as hmac_mod
import json
import logging
import os
import struct
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger("liuhao.webpush")

#: VAPID JWT 的 sub（联系邮箱，公司公开邮箱，非密钥）。
VAPID_SUBJECT = "mailto:info@liuhaoglobal.com"
#: 推送正文（最小化口径，见模块说明；这两组文案是唯一允许的推送正文）。
REMINDER_PUSH = {"title": "贾维斯提醒", "body": "你有一条提醒到了，打开贾维斯查看。"}
BRIEFING_PUSH = {"title": "晨间简报", "body": "今日简报已生成，打开贾维斯查看。"}
TEST_PUSH = {"title": "贾维斯推送测试", "body": "推送通道已打通。"}


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def push_dir() -> Path:
    from src.common.engines import data_dir

    return data_dir() / "push"


def _store_path() -> Path:
    return push_dir() / "subscriptions.json"


def _key_path() -> Path:
    return push_dir() / "vapid_private.pem"


# --------------------------------------------------------------------------- #
# VAPID 密钥（本地生成，私钥 0600）
# --------------------------------------------------------------------------- #


def _load_or_create_private_key():
    from cryptography.hazmat.primitives.asymmetric import ec

    path = _key_path()
    if path.is_file():
        from cryptography.hazmat.primitives import serialization

        try:
            os.chmod(path, 0o600)  # 存量私钥文件权限不对时收紧
        except OSError:
            pass
        return serialization.load_pem_private_key(path.read_bytes(), password=None)
    push_dir().mkdir(parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    from cryptography.hazmat.primitives import serialization

    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    # 先以 0600 落盘再写内容，避免窗口期被读。
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, pem)
    finally:
        os.close(fd)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    logger.info("已在本机生成 VAPID 密钥对（私钥仅存数据目录）")
    return key


def vapid_public_key() -> str:
    """applicationServerKey：EC 公钥未压缩点（65 字节）的 base64url。"""
    from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePublicNumbers

    numbers = _load_or_create_private_key().public_key().public_numbers()
    assert isinstance(numbers, EllipticCurvePublicNumbers)
    raw = b"\x04" + numbers.x.to_bytes(32, "big") + numbers.y.to_bytes(32, "big")
    return _b64url(raw)


def _vapid_jwt(audience: str) -> str:
    """VAPID JWT（ES256）：aud=推送端点源，exp=+12h，sub=联系邮箱。"""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec as ec_mod
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    header = {"typ": "JWT", "alg": "ES256"}
    claims = {"aud": audience, "exp": int(time.time()) + 12 * 3600, "sub": VAPID_SUBJECT}
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64url(json.dumps(claims, separators=(",", ":")).encode())
    )
    key = _load_or_create_private_key()
    der = key.sign(signing_input.encode("ascii"), ec_mod.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return f"{signing_input}.{_b64url(signature)}"


# --------------------------------------------------------------------------- #
# RFC 8291 aes128gcm 负载加密
# --------------------------------------------------------------------------- #


def encrypt_payload(plaintext: bytes, ua_public_b64: str, auth_b64: str) -> bytes:
    """把推送负载加密成 aes128gcm body（接收端用订阅密钥解）。"""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    ua_public = _b64url_decode(ua_public_b64)
    auth_secret = _b64url_decode(auth_b64)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    sender = ec.generate_private_key(ec.SECP256R1())
    sender_numbers = sender.public_key().public_numbers()
    sender_public = (
        b"\x04"
        + sender_numbers.x.to_bytes(32, "big")
        + sender_numbers.y.to_bytes(32, "big")
    )
    ecdh_secret = sender.exchange(ec.ECDH(), ua_key)

    def hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
        return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)

    # RFC 8291 §3.4：先以 auth_secret 为盐从 ECDH 秘密导出 IKM。
    ikm = hkdf(
        auth_secret,
        ecdh_secret,
        b"WebPush: info\x00" + ua_public + sender_public,
        32,
    )
    salt = os.urandom(16)
    cek = hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    record_size = 4096
    # 单条记录：负载 + 0x02 结尾填充标记（最后一条记录）。
    ciphertext = AESGCM(cek).encrypt(nonce, plaintext + b"\x02", None)
    header = salt + struct.pack(">I", record_size) + bytes([len(sender_public)]) + sender_public
    return header + ciphertext


def decrypt_payload(body: bytes, ua_private_key: Any, auth_b64: str) -> bytes:
    """接收端解密（演练/测试用：证明上面的加密可被标准口径解开）。"""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    salt, record_size, id_len = body[:16], struct.unpack(">I", body[16:20])[0], body[20]
    sender_public = body[21 : 21 + id_len]
    ciphertext = body[21 + id_len :]
    sender_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), sender_public)
    ecdh_secret = ua_private_key.exchange(ec.ECDH(), sender_key)
    auth_secret = _b64url_decode(auth_b64)

    def hkdf(salt_: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
        return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt_, info=info).derive(ikm)

    ua_public_numbers = ua_private_key.public_key().public_numbers()
    ua_public = (
        b"\x04"
        + ua_public_numbers.x.to_bytes(32, "big")
        + ua_public_numbers.y.to_bytes(32, "big")
    )
    ikm = hkdf(auth_secret, ecdh_secret, b"WebPush: info\x00" + ua_public + sender_public, 32)
    cek = hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    plain = AESGCM(cek).decrypt(nonce, ciphertext, None)
    assert plain.endswith(b"\x02")
    return plain[:-1]


# --------------------------------------------------------------------------- #
# 订阅存储 + 开关
# --------------------------------------------------------------------------- #

_LOCK = threading.Lock()


def _load_store() -> Dict[str, Any]:
    try:
        loaded = json.loads(_store_path().read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            loaded.setdefault("enabled", False)
            loaded.setdefault("subscriptions", [])
            return loaded
    except Exception:  # noqa: BLE001 - 文件不存在/损坏按空库处理，不炸推送
        pass
    return {"enabled": False, "subscriptions": []}


def _save_store(store: Dict[str, Any]) -> None:
    push_dir().mkdir(parents=True, exist_ok=True)
    path = _store_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    # 订阅库含 auth 密钥，与 VAPID 私钥同口径收紧到 0600（落地修正）。
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def push_enabled() -> bool:
    with _LOCK:
        return bool(_load_store().get("enabled"))


def set_push_enabled(enabled: bool) -> Dict[str, Any]:
    with _LOCK:
        store = _load_store()
        store["enabled"] = bool(enabled)
        _save_store(store)
        return {"enabled": store["enabled"], "subscriptions": len(store["subscriptions"])}


def add_subscription(account: str, subscription: Dict[str, Any]) -> Dict[str, Any]:
    """登记一条浏览器订阅（同账号同 endpoint 覆盖更新密钥）。"""
    endpoint = str(subscription.get("endpoint") or "").strip()
    keys = subscription.get("keys") or {}
    p256dh = str(keys.get("p256dh") or "").strip()
    auth = str(keys.get("auth") or "").strip()
    if not endpoint.startswith("https://") or not p256dh or not auth:
        raise ValueError("订阅不完整：需要 https endpoint 与 keys.p256dh / keys.auth。")
    with _LOCK:
        store = _load_store()
        if not store.get("enabled"):
            raise ValueError("推送总开关还没开：先开启推送，再登记订阅。")
        rows = [
            row
            for row in store["subscriptions"]
            if not (row.get("account") == account and row.get("endpoint") == endpoint)
        ]
        rows.append(
            {
                "account": account,
                "endpoint": endpoint,
                "keys": {"p256dh": p256dh, "auth": auth},
                "created_at": int(time.time()),
            }
        )
        store["subscriptions"] = rows
        _save_store(store)
        return {"subscriptions": len(rows)}


def remove_subscription(account: str, endpoint: str) -> Dict[str, Any]:
    with _LOCK:
        store = _load_store()
        before = len(store["subscriptions"])
        store["subscriptions"] = [
            row
            for row in store["subscriptions"]
            if not (row.get("account") == account and row.get("endpoint") == endpoint)
        ]
        _save_store(store)
        return {"removed": before - len(store["subscriptions"])}


def list_subscriptions(account: Optional[str] = None) -> List[Dict[str, Any]]:
    with _LOCK:
        rows = list(_load_store()["subscriptions"])
    if account is not None:
        rows = [row for row in rows if row.get("account") == account]
    return rows


def push_status(account: Optional[str] = None) -> Dict[str, Any]:
    with _LOCK:
        store = _load_store()
    mine = (
        len(store["subscriptions"])
        if account is None
        else sum(1 for row in store["subscriptions"] if row.get("account") == account)
    )
    return {
        "enabled": bool(store.get("enabled")),
        "subscriptions_here": mine,
        "subscriptions_total": len(store["subscriptions"]),
        "vapid_public_key": vapid_public_key() if store.get("enabled") else "",
        "note": "推送默认关；开启后简报与提醒只推通用文案，不带正文。",
    }


# --------------------------------------------------------------------------- #
# 发送
# --------------------------------------------------------------------------- #

#: 推送 HTTP 发送（可注入，演练用假传输）。
Poster = Callable[[str, bytes, Dict[str, str]], int]


def _default_poster(url: str, body: bytes, headers: Dict[str, str]) -> int:
    import requests

    response = requests.post(url, data=body, headers=headers, timeout=20)
    return int(response.status_code)


def send_push(
    payload: Dict[str, str],
    *,
    account: Optional[str] = None,
    poster: Optional[Poster] = None,
) -> Dict[str, Any]:
    """给订阅发一条推送。开关关着/无订阅时如实返回，不假装发过。

    account=None 表示机器级广播（提醒通道口径）；给了 account 只发该账号。
    """
    if not push_enabled():
        return {"sent": 0, "failed": 0, "reason": "disabled"}
    rows = list_subscriptions(account)
    if not rows:
        return {"sent": 0, "failed": 0, "reason": "no_subscriptions"}
    post = poster or _default_poster
    plaintext = json.dumps(
        {"title": payload.get("title", ""), "body": payload.get("body", "")},
        ensure_ascii=False,
    ).encode("utf-8")
    sent = failed = 0
    dead: List[str] = []
    for row in rows:
        endpoint = str(row["endpoint"])
        origin = "{0.scheme}://{0.netloc}".format(urlparse(endpoint))
        try:
            body = encrypt_payload(plaintext, row["keys"]["p256dh"], row["keys"]["auth"])
            headers = {
                "Authorization": f"vapid t={_vapid_jwt(origin)}, k={vapid_public_key()}",
                "Content-Encoding": "aes128gcm",
                "Content-Type": "application/octet-stream",
                "TTL": "86400",
            }
            code = post(endpoint, body, headers)
        except Exception as exc:  # noqa: BLE001 - 单条失败不拖垮其余订阅
            logger.warning("推送发送失败（%s）：%s", origin, type(exc).__name__)
            failed += 1
            continue
        if code in (200, 201, 202):
            sent += 1
        elif code in (404, 410):
            dead.append(endpoint)
            failed += 1
        else:
            failed += 1
    for endpoint in dead:
        with _LOCK:
            store = _load_store()
            store["subscriptions"] = [
                row for row in store["subscriptions"] if row.get("endpoint") != endpoint
            ]
            _save_store(store)
    return {"sent": sent, "failed": failed, "expired_removed": len(dead)}
