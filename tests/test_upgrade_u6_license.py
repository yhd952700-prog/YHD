"""U6 授权模块：离线验签全链（签发→验真→篡改被拒→过期识别→默认关）。"""
from __future__ import annotations

import base64
import json
from datetime import date

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.licensing import license as lic


def _keypair():
    key = Ed25519PrivateKey.generate()
    priv = base64.b64encode(
        key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
    ).decode()
    pub = base64.b64encode(
        key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    ).decode()
    return priv, pub


def test_issue_and_verify_roundtrip(tmp_path):
    priv, pub = _keypair()
    payload = lic.issue_license(
        priv, licensee="测试公司", edition="pro",
        expires_at="2027-01-01", features=["sync"],
    )
    path = tmp_path / "license.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    result = lic.verify_license(path, public_key_b64=pub, today=date(2026, 6, 1))
    assert result["status"] == "valid"
    assert result["licensee"] == "测试公司"
    assert result["features"] == ["sync"]


def test_tampered_license_rejected(tmp_path):
    priv, pub = _keypair()
    payload = lic.issue_license(priv, licensee="测试公司")
    payload["licensee"] = "被篡改的公司"
    path = tmp_path / "license.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    assert lic.verify_license(path, public_key_b64=pub)["status"] == "invalid"


def test_wrong_key_rejected(tmp_path):
    priv, _pub = _keypair()
    _priv2, pub2 = _keypair()
    payload = lic.issue_license(priv, licensee="测试公司")
    path = tmp_path / "license.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    assert lic.verify_license(path, public_key_b64=pub2)["status"] == "invalid"


def test_expired_license_recognized(tmp_path):
    priv, pub = _keypair()
    payload = lic.issue_license(priv, licensee="测试公司", expires_at="2025-01-01")
    path = tmp_path / "license.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    result = lic.verify_license(path, public_key_b64=pub, today=date(2026, 1, 2))
    assert result["status"] == "expired"


def test_missing_license_file(tmp_path):
    assert lic.verify_license(tmp_path / "nope.json")["status"] == "missing"


def test_default_off_and_enforce_never_blocks_self_use(monkeypatch):
    monkeypatch.delenv("LIUHAO_LICENSE_REQUIRED", raising=False)
    assert lic.licensing_enabled() is False
    status = lic.license_status()
    assert status["enabled"] is False and status["status"] == "disabled"
    # 默认关时 enforce 恒放行：自用版零影响。
    assert lic.enforce()["allowed"] is True


def test_only_public_key_is_embedded():
    # 模块只内置公钥（32 字节 Ed25519 公钥）；没有任何私钥材料属性。
    assert len(base64.b64decode(lic.DEMO_PUBLIC_KEY_B64)) == 32
    assert not hasattr(lic, "DEMO_PRIVATE_KEY_B64")
    assert not hasattr(lic, "PRIVATE_KEY")
