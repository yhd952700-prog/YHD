"""离线许可证：Ed25519 签名的 JSON 许可证文件校验。

格式 ``license.json``::

    {
      "licensee": "某某公司",
      "edition": "pro",
      "issued_at": "2026-01-01",
      "expires_at": "2027-01-01",          # 可空 = 永久
      "features": ["sync", "voice"],
      "nonce": "随机串",
      "signature": "<base64 Ed25519 签名>"  # 对其余字段的规范化 JSON 签名
    }

安全口径：
- 验签公钥内置于本模块；**私钥不在仓库里**——签发是 Hughes 离线保管私钥
  后用 :func:`issue_license` 完成的事。本仓库内置的是一把「演示公钥」
  （对应私钥已销毁），商业发布前必须换成正式公钥、离线生成对应私钥
  （见商业化缺口清单）。
- 默认关：:func:`licensing_enabled` 只认环境变量
  ``LIUHAO_LICENSE_REQUIRED=1``；自用版从不设置它，:func:`enforce` 也没有
  被任何启动路径调用——本模块对自用完全零影响。
- 全程离线：无任何网络请求、无机器指纹采集。
"""
from __future__ import annotations

import base64
import json
import os
import secrets
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

#: 演示公钥（Ed25519, base64）。商业发布前替换为正式公钥。
DEMO_PUBLIC_KEY_B64 = "5cHYks0CxSR7+BF+S/85AFVMuUx7sB3AQX8veCBuY4g="

_PAYLOAD_KEYS = ("licensee", "edition", "issued_at", "expires_at", "features", "nonce")


def _canonical(payload: Dict[str, Any]) -> bytes:
    body = {key: payload.get(key) for key in _PAYLOAD_KEYS}
    return json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")


def issue_license(
    private_key_b64: str,
    *,
    licensee: str,
    edition: str = "pro",
    expires_at: Optional[str] = None,
    features: Optional[List[str]] = None,
    issued_at: Optional[str] = None,
) -> Dict[str, Any]:
    """用私钥签发一份许可证（离线签发工具函数；私钥由签发方保管）。"""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_key_b64))
    payload: Dict[str, Any] = {
        "licensee": str(licensee),
        "edition": str(edition),
        "issued_at": issued_at or date.today().isoformat(),
        "expires_at": expires_at,
        "features": list(features or []),
        "nonce": secrets.token_hex(8),
    }
    payload["signature"] = base64.b64encode(key.sign(_canonical(payload))).decode("ascii")
    return payload


def verify_license(
    path: str | Path,
    *,
    public_key_b64: str = DEMO_PUBLIC_KEY_B64,
    today: Optional[date] = None,
) -> Dict[str, Any]:
    """校验许可证文件。返回 {status: valid/expired/invalid/missing, ...}。"""
    file_path = Path(path)
    if not file_path.exists():
        return {"status": "missing", "reason": "没有找到许可证文件"}
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"status": "invalid", "reason": "许可证文件读不了或不是合法 JSON"}
    if not isinstance(payload, dict) or not payload.get("signature"):
        return {"status": "invalid", "reason": "许可证缺少签名字段"}

    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
        public_key.verify(base64.b64decode(str(payload["signature"])), _canonical(payload))
    except (InvalidSignature, ValueError, TypeError):
        return {"status": "invalid", "reason": "签名验不过（文件被改过或不是本方签发的）"}

    expires = payload.get("expires_at")
    if expires:
        try:
            expiry_date = date.fromisoformat(str(expires))
        except ValueError:
            return {"status": "invalid", "reason": "到期日格式不对"}
        if (today or date.today()) > expiry_date:
            return {
                "status": "expired",
                "reason": f"许可证已于 {expires} 到期",
                "licensee": payload.get("licensee"),
                "edition": payload.get("edition"),
            }
    return {
        "status": "valid",
        "licensee": payload.get("licensee"),
        "edition": payload.get("edition"),
        "expires_at": expires,
        "features": payload.get("features") or [],
    }


def licensing_enabled() -> bool:
    """授权校验总开关：默认关，只有显式设置环境变量才开。"""
    return os.environ.get("LIUHAO_LICENSE_REQUIRED", "").strip() == "1"


def default_license_path() -> Path:
    from ..common.data_paths import data_root

    root = data_root()
    if root is not None:
        return Path(root) / "license.json"
    return Path.home() / ".liuhao" / "license.json"


def license_status() -> Dict[str, Any]:
    """当前授权状态（供设置页/聊天查询；关着时明确说关着）。"""
    if not licensing_enabled():
        return {"enabled": False, "status": "disabled", "note": "授权校验未开启（自用版默认关，不影响任何功能）"}
    result = verify_license(default_license_path())
    return {"enabled": True, **result}


def enforce() -> Dict[str, Any]:
    """商业构建的接入口：开启校验且许可证无效时返回拦截结论。

    自用版**没有任何启动路径调用本函数**；商业版构建时在启动处显式接入。
    """
    status = license_status()
    if not status.get("enabled"):
        return {"allowed": True, "status": status}
    return {"allowed": status.get("status") == "valid", "status": status}
