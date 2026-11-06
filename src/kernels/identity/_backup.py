"""账号凭据包：口令加密的整机迁移格式。

Why this module exists
----------------------
产品要求"主账号可以把整棵树打包带走，在另一台机器上导入认领"。这需要一个
**离线可解密、但落盘不可读**的格式，且必须防重放：

* 口令只来自请求体，**不落盘、不进日志**；
* 用 PBKDF2-HMAC-SHA256 派生 256-bit key，再用 AES-GCM 加密（带随机 nonce）；
* 包内带 ``bundle_id``（UUID4）与 ``issued_at``，导入侧持久化回执，重复导入
  直接拒绝；
* 解密失败不区分"口令错"与"包损坏"，避免成为口令 oracle。

格式（JSON，utf-8）::

    {
      "format": "liuhao.account.bundle.v1",
      "bundle_id": "...",
      "issued_at": "...",
      "kdf": {"algo": "pbkdf2-sha256", "iterations": 200000, "salt": "<b64>"},
      "cipher": "aes-256-gcm",
      "nonce": "<b64>",
      "payload": "<b64 密文>"
    }

包内 payload 才是明文清单（human identities / account tree / credentials /
models / files / memory）。本模块**不**决定要装什么，只负责加解密与封装校
验；收集与恢复在 ``IdentityManager`` / gateway 完成。
"""
from __future__ import annotations

import base64
import json
import os
import uuid
from typing import Any, Dict

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

from src._time import utc_now

BUNDLE_FORMAT = "liuhao.account.bundle.v1"
KDF_ALGO = "pbkdf2-sha256"
KDF_ITERATIONS = 200_000
CIPHER = "aes-256-gcm"
MAX_BUNDLE_BYTES = 256 * 1024 * 1024  # 256MB：防止一次导入打爆内存


class BundleError(Exception):
    """凭据包不可用。文案中性，不区分口令错/包损坏。"""

    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: Any) -> bytes:
    if not isinstance(text, str) or not text:
        raise BundleError("BUNDLE_INVALID", "凭据包无效或口令错误")
    try:
        return base64.b64decode(text.encode("ascii"), validate=True)
    except Exception as exc:  # noqa: BLE001
        raise BundleError("BUNDLE_INVALID", "凭据包无效或口令错误") from exc


def _derive(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=KDF_ITERATIONS,
    )
    return kdf.derive(password.encode("utf-8"))


def encrypt_bundle(payload: Dict[str, Any], password: str) -> Dict[str, Any]:
    """把 payload 加密成可导出 JSON。``password`` 不保存、不写日志。"""
    if not isinstance(payload, dict):
        raise BundleError("BUNDLE_INVALID", "凭据包内容必须是对象")
    text = str(password or "").strip()
    if len(text) < 8:
        raise BundleError("BUNDLE_PASSPHRASE_WEAK", "口令至少需要 8 个字符")
    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = _derive(text, salt)
    aad = BUNDLE_FORMAT.encode("utf-8")
    plaintext = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad)
    return {
        "format": BUNDLE_FORMAT,
        "bundle_id": str(uuid.uuid4()),
        "issued_at": utc_now().isoformat(),
        "kdf": {
            "algo": KDF_ALGO,
            "iterations": KDF_ITERATIONS,
            "salt": _b64(salt),
        },
        "cipher": CIPHER,
        "nonce": _b64(nonce),
        "payload": _b64(ciphertext),
    }


def decrypt_bundle(bundle: Dict[str, Any], password: str) -> Dict[str, Any]:
    """校验封装并解密。任何失败都返回同一句中性文案。"""
    if not isinstance(bundle, dict):
        raise BundleError("BUNDLE_INVALID", "凭据包无效或口令错误")
    if bundle.get("format") != BUNDLE_FORMAT:
        raise BundleError("BUNDLE_INVALID", "凭据包无效或口令错误")
    if bundle.get("cipher") != CIPHER:
        raise BundleError("BUNDLE_INVALID", "凭据包无效或口令错误")
    kdf = bundle.get("kdf") or {}
    if not isinstance(kdf, dict) or kdf.get("algo") != KDF_ALGO:
        raise BundleError("BUNDLE_INVALID", "凭据包无效或口令错误")
    try:
        iterations = int(kdf.get("iterations") or 0)
    except (TypeError, ValueError):
        raise BundleError("BUNDLE_INVALID", "凭据包无效或口令错误")
    if iterations < 100_000 or iterations > 1_000_000:
        raise BundleError("BUNDLE_INVALID", "凭据包无效或口令错误")
    salt = _unb64(kdf.get("salt"))
    nonce = _unb64(bundle.get("nonce"))
    ciphertext = _unb64(bundle.get("payload"))
    if len(ciphertext) > MAX_BUNDLE_BYTES:
        raise BundleError("BUNDLE_TOO_LARGE", "凭据包过大，无法导入", 413)
    text = str(password or "").strip()
    if not text:
        raise BundleError("BUNDLE_INVALID", "凭据包无效或口令错误")
    key = _derive(text, salt)
    try:
        plaintext = AESGCM(key).decrypt(
            nonce, ciphertext, BUNDLE_FORMAT.encode("utf-8")
        )
    except Exception as exc:  # noqa: BLE001 - invalid tag == wrong password
        raise BundleError("BUNDLE_INVALID", "凭据包无效或口令错误") from exc
    try:
        payload = json.loads(plaintext.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise BundleError("BUNDLE_INVALID", "凭据包无效或口令错误") from exc
    if not isinstance(payload, dict):
        raise BundleError("BUNDLE_INVALID", "凭据包无效或口令错误")
    return payload


def bundle_id_of(bundle: Dict[str, Any]) -> str:
    value = str((bundle or {}).get("bundle_id") or "").strip()
    if not value:
        raise BundleError("BUNDLE_INVALID", "凭据包无效或口令错误")
    return value
