"""审计链校验密钥（HMAC-SHA256）—— 让“整链重算”不再能伪造。

为什么需要它
------------
旧链是 ``SHA-256(canonical_json) + prev_hash``：能发现“只改一行”，但拿到库
写权限的人可以把所有 ``event_hash`` / ``link_hash`` 连同 ``chain_state`` 一起
重算，验证依然全绿。这是无密钥哈希链的固有限制，不是实现缺陷。

本模块给链加一把**本机密钥**：

* 生产（``APP_ENV=production``）默认开启；开发/测试默认关闭，保持旧库可读；
* 密钥 32 字节，首次使用时用 ``os.urandom`` 生成；
* 落盘优先 DPAPI（CurrentUser）加密，再叠加“仅当前用户”的 ACL；
* 只提供状态/是否可用，**任何日志都不输出密钥本身**；
* 备份/诊断包/账号导出都不包含密钥文件（见 ``upgrade/backups.py`` 的保护名单）。

恢复策略（明确降级，不静默全红/全绿）
--------------------------------------
密钥**不随备份外传**。如果审计库来自另一台机器 / 另一个 Windows 用户，本机
没有对应密钥：

* 追加仍可用（会记录为无密钥事件；状态/校验接口会明确报告缺密钥）；
* 校验会返回 ``degraded``：``缺少校验密钥，完整性只能做结构检查``，
  结构（序号连续、prev 链接、尾锚点）能查，密码学内容校验明确标记为不可用。

环境变量
--------
``LIUHAO_AUDIT_CHAIN_KEY``
    直接给密钥（hex / base64 / 32 字节原文）。用于容器与测试，避免落盘。
``LIUHAO_AUDIT_CHAIN_KEY_FILE``
    覆盖密钥文件路径；缺省取审计库同目录的 ``audit_chain.key``。
``LIUHAO_AUDIT_CHAIN_KEY_MODE``
    ``auto``（默认，生产开）/ ``on`` / ``off``。
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import os
import threading
from pathlib import Path
from typing import Optional

from src.common.hash_chain import DEFAULT_HASH_ALG, HASH_ALGORITHMS

logger = logging.getLogger("liuhao.kernel.audit.chain_key")

HMAC_ALG = "hmac-sha256"
KEY_BYTES = 32
_KEY_ENV = "LIUHAO_AUDIT_CHAIN_KEY"
_KEY_FILE_ENV = "LIUHAO_AUDIT_CHAIN_KEY_FILE"
_MODE_ENV = "LIUHAO_AUDIT_CHAIN_KEY_MODE"
_ENV_MARKERS = ("APP_ENV", "LIUHAO_ENV", "ENV")

_DPAPI_PREFIX = b"LIUHAO-AUDIT-CHAIN-KEY-DPAPI-1\n"
_RAW_PREFIX = b"LIUHAO-AUDIT-CHAIN-KEY-RAW-1\n"

_lock = threading.Lock()
_cached_key: Optional[bytes] = None
_cache_loaded = False


# --------------------------------------------------------------------------- #
# 路径 / 模式
# --------------------------------------------------------------------------- #

def _is_production() -> bool:
    for name in _ENV_MARKERS:
        value = (os.environ.get(name) or "").strip().lower()
        if value in ("production", "prod"):
            return True
    return False


def key_mode() -> str:
    """``on`` / ``off``。``auto`` 只看部署环境。"""
    raw = (os.environ.get(_MODE_ENV) or "auto").strip().lower()
    if raw in ("1", "on", "true", "yes", "required"):
        return "on"
    if raw in ("0", "off", "false", "no", "disabled"):
        return "off"
    return "on" if _is_production() else "off"


def key_file_path() -> Path:
    configured = (os.environ.get(_KEY_FILE_ENV) or "").strip()
    if configured:
        return Path(configured)
    db = (os.environ.get("AUDIT_DB_PATH") or "").strip()
    base = Path(db).parent if db else Path(os.environ.get("LIUHAO_DATA_DIR", "data"))
    return base / "audit_chain.key"


# --------------------------------------------------------------------------- #
# DPAPI（Windows 当前用户）
# --------------------------------------------------------------------------- #

def _dpapi_protect(data: bytes) -> Optional[bytes]:
    if os.name != "nt":
        return None
    try:
        import win32crypt  # type: ignore

        return win32crypt.CryptProtectData(
            data, "liuhao-audit-chain-key", None, None, None, 0
        )
    except Exception:  # noqa: BLE001 - 无 pywin32 时退回原始字节 + ACL
        return None


def _dpapi_unprotect(blob: bytes) -> Optional[bytes]:
    if os.name != "nt":
        return None
    try:
        import win32crypt  # type: ignore

        _description, data = win32crypt.CryptUnprotectData(
            blob, None, None, None, 0
        )
        return data
    except Exception:  # noqa: BLE001 - 其他机器/用户加密的密钥必须按不可用处理
        return None


# --------------------------------------------------------------------------- #
# 读取 / 生成
# --------------------------------------------------------------------------- #

def _decode_env_key(raw: str) -> Optional[bytes]:
    text = raw.strip()
    if not text:
        return None
    for decoder in (bytes.fromhex, base64.b64decode):
        try:
            candidate = decoder(text)
        except (ValueError, binascii.Error):
            continue
        if len(candidate) == KEY_BYTES:
            return candidate
    encoded = text.encode("utf-8", "ignore")
    return encoded if len(encoded) == KEY_BYTES else None


def _read_file(path: Path) -> Optional[bytes]:
    try:
        payload = path.read_bytes()
    except OSError:
        return None
    if payload.startswith(_DPAPI_PREFIX):
        try:
            blob = base64.b64decode(payload[len(_DPAPI_PREFIX):])
        except (ValueError, binascii.Error):
            return None
        plain = _dpapi_unprotect(blob)
        return plain if plain and len(plain) == KEY_BYTES else None
    if payload.startswith(_RAW_PREFIX):
        try:
            raw = base64.b64decode(payload[len(_RAW_PREFIX):])
        except (ValueError, binascii.Error):
            return None
        return raw if len(raw) == KEY_BYTES else None
    # 兼容最早的无头原始 32 字节格式。先按精确长度判断，避免随机密钥
    # 末尾恰好是 0x0A/0x0D 时被下面的 rstrip 误删。
    if len(payload) == KEY_BYTES:
        return payload
    trimmed = payload.rstrip(b"\r\n")
    return trimmed if len(trimmed) == KEY_BYTES else None


def _write_file(path: Path, key: bytes) -> bool:
    """原子写 + DPAPI + ACL；失败返回 False（不抛，避免审计写入被密钥问题拖垮）。"""
    encrypted = _dpapi_protect(key)
    if encrypted:
        payload = _DPAPI_PREFIX + base64.b64encode(encrypted)
    else:
        payload = _RAW_PREFIX + base64.b64encode(key)
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Windows 上 os.open 默认是文本模式（会把 \n 改写成 \r\n，并破坏
        # 二进制密文），所以必须显式带 O_BINARY。
        fd = os.open(
            str(tmp),
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0),
            0o600,
        )
        try:
            os.write(fd, payload)
        finally:
            os.close(fd)
        os.replace(tmp, path)
    except OSError:
        logger.warning("审计链密钥写入失败：%s（本次退回无密钥追加）", path,
                       exc_info=True)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    try:
        from src.common.windows_acl import tighten_current_user_only

        if not tighten_current_user_only(path):
            logger.warning("审计链密钥文件 ACL 未能收紧为仅当前用户：%s", path)
    except Exception:  # noqa: BLE001 - ACL 失败不应影响密钥可用性
        logger.warning("审计链密钥 ACL 设置失败：%s", path, exc_info=True)
    return True


def get_key(*, create: bool) -> Optional[bytes]:
    """读取（必要时生成）本机密钥。返回 ``None`` 表示不可用。

    ``create=False`` 用于校验/状态查询：绝不因为“没有密钥”就临时造一把新的，
    那会让旧密钥事件永远无法验证，还会把问题伪装成“已启用”。
    """
    global _cached_key, _cache_loaded

    configured = (os.environ.get(_KEY_ENV) or "").strip()
    if configured:
        decoded = _decode_env_key(configured)
        if decoded is None:
            if not _cache_loaded:
                logger.error("审计链密钥环境变量格式无效（必须是 32 字节）")
            return None
        return decoded

    with _lock:
        if _cache_loaded:
            return _cached_key
        path = key_file_path()
        if path.exists():
            key = _read_file(path)
            if key is not None:
                _cached_key, _cache_loaded = key, True
                return key
            # 文件存在但解不开：可能是别的机器/用户加密的，明确不可用。
            _cache_loaded = True
            return None
        if not create:
            _cache_loaded = True
            return None
        key = os.urandom(KEY_BYTES)
        if not _write_file(path, key):
            # 写不下去就不要用一把“重启后消失”的密钥去签事件：那会让新事件
            # 在下次启动时永远无法验证。本次退回无密钥追加，并在状态里暴露。
            _cache_loaded = True
            return None
        _cached_key, _cache_loaded = key, True
        return key


def status() -> str:
    """``available`` / ``missing`` / ``unreadable``。"""
    configured = (os.environ.get(_KEY_ENV) or "").strip()
    if configured:
        return "available" if _decode_env_key(configured) is not None else "unreadable"
    path = key_file_path()
    if not path.exists():
        return "missing"
    return "available" if _read_file(path) is not None else "unreadable"


def active_hash_alg(*, create: bool = True) -> str:
    """本次追加应使用的算法；密钥模式关闭时保持旧行为。

    ``create=False`` 用于“库里已有带密钥事件、但本机密钥丢了”的场景：
    绝不能临时生成一把新钥匙，否则新事件会被签成旧库永远验不过的记录。
    """
    if key_mode() != "on":
        return DEFAULT_HASH_ALG
    return HMAC_ALG if get_key(create=create) is not None else DEFAULT_HASH_ALG


def reset_cache() -> None:
    """测试用：清空进程内缓存（文件/环境变量变化后重新读取）。"""
    global _cached_key, _cache_loaded, _pad_cache
    with _lock:
        _cached_key = None
        _cache_loaded = False
        _pad_cache = None


# --------------------------------------------------------------------------- #
# 注册 HMAC 算法（惰性取密钥；缺密钥时抛 ValueError，由调用方降级）
# --------------------------------------------------------------------------- #

_SHA256_BLOCK = 64
_pad_cache: Optional[tuple[bytes, bytes, bytes]] = None


def _pads_for(key: bytes) -> tuple[bytes, bytes]:
    """缓存 HMAC 的 ipad/opad。

    ``hmac.new`` 每条事件都要重新做一次 key padding；审计是热路径（一次写入
    两次 HMAC），实测换成缓存 pad 的等价实现后 HMAC 成本约降一半。
    """
    global _pad_cache
    cached = _pad_cache
    if cached is not None and cached[0] == key:
        return cached[1], cached[2]
    block_key = key if len(key) <= _SHA256_BLOCK else hashlib.sha256(key).digest()
    block_key = block_key + b"\x00" * (_SHA256_BLOCK - len(block_key))
    ipad = bytes(byte ^ 0x36 for byte in block_key)
    opad = bytes(byte ^ 0x5C for byte in block_key)
    _pad_cache = (key, ipad, opad)
    return ipad, opad


def _keyed_sha256(data: bytes) -> str:
    key = get_key(create=False)
    if key is None:
        raise ValueError("audit chain key is unavailable for hmac-sha256")
    ipad, opad = _pads_for(key)
    inner = hashlib.sha256(ipad + data).digest()
    return hashlib.sha256(opad + inner).hexdigest()


HASH_ALGORITHMS.setdefault(HMAC_ALG, _keyed_sha256)
