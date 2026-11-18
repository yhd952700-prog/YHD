"""V2(c)：Web Push——加密往返、开关口径、订阅门禁、失效清理、JWT 验签。"""

from __future__ import annotations

import base64
import json
import os
import stat

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from src.tasks import webpush


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(tmp_path))
    return tmp_path


def _ua_subscription():
    """造一对浏览器侧密钥（模拟 PushManager.subscribe 的产物）。"""
    key = ec.generate_private_key(ec.SECP256R1())
    numbers = key.public_key().public_numbers()
    raw = b"\x04" + numbers.x.to_bytes(32, "big") + numbers.y.to_bytes(32, "big")
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
    return key, {
        "endpoint": "https://push.example.com/sub/abc",
        "keys": {"p256dh": b64(raw), "auth": b64(os.urandom(16))},
    }


def test_encrypt_decrypt_roundtrip():
    ua_key, sub = _ua_subscription()
    payload = json.dumps(webpush.REMINDER_PUSH, ensure_ascii=False).encode()
    body = webpush.encrypt_payload(payload, sub["keys"]["p256dh"], sub["keys"]["auth"])
    assert webpush.REMINDER_PUSH["body"].encode() not in body  # 密文里没有明文
    plain = webpush.decrypt_payload(body, ua_key, sub["keys"]["auth"])
    assert json.loads(plain.decode())["title"] == "贾维斯提醒"


def test_vapid_jwt_verifies_with_public_key():
    token = webpush._vapid_jwt("https://push.example.com")
    head_b64, claims_b64, sig_b64 = token.split(".")
    decode = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    claims = json.loads(decode(claims_b64))
    assert claims["aud"] == "https://push.example.com"
    assert claims["sub"].startswith("mailto:")
    raw_sig = decode(sig_b64)
    r, s = int.from_bytes(raw_sig[:32], "big"), int.from_bytes(raw_sig[32:], "big")
    public = serialization.load_pem_private_key(
        webpush._key_path().read_bytes(), password=None
    ).public_key()
    public.verify(
        encode_dss_signature(r, s),
        f"{head_b64}.{claims_b64}".encode(),
        ec.ECDSA(hashes.SHA256()),
    )


def test_private_key_file_is_0600(_data_dir):
    webpush.vapid_public_key()
    mode = stat.S_IMODE(os.stat(webpush._key_path()).st_mode)
    assert mode == 0o600


def test_disabled_by_default_and_subscribe_rejected():
    assert webpush.push_enabled() is False
    _ua_key, sub = _ua_subscription()
    with pytest.raises(ValueError, match="总开关"):
        webpush.add_subscription("owner1", sub)
    result = webpush.send_push(webpush.TEST_PUSH, account="owner1")
    assert result == {"sent": 0, "failed": 0, "reason": "disabled"}


def test_send_flow_and_expired_cleanup():
    webpush.set_push_enabled(True)
    ua_key, sub = _ua_subscription()
    webpush.add_subscription("owner1", sub)
    assert webpush.push_status("owner1")["subscriptions_here"] == 1

    seen = {}

    def poster(url, body, headers):
        seen["url"] = url
        seen["body"] = body
        seen["headers"] = headers
        return 201

    result = webpush.send_push(webpush.REMINDER_PUSH, account="owner1", poster=poster)
    assert result["sent"] == 1
    assert seen["headers"]["Authorization"].startswith("vapid t=")
    assert seen["headers"]["Content-Encoding"] == "aes128gcm"
    plain = webpush.decrypt_payload(seen["body"], ua_key, sub["keys"]["auth"])
    assert json.loads(plain.decode()) == webpush.REMINDER_PUSH

    # 端点失效（410）→ 订阅被清，不再反复打
    result = webpush.send_push(
        webpush.REMINDER_PUSH, account="owner1", poster=lambda u, b, h: 410
    )
    assert result["expired_removed"] == 1
    assert webpush.list_subscriptions("owner1") == []


def test_account_isolation():
    webpush.set_push_enabled(True)
    _k1, sub1 = _ua_subscription()
    _k2, sub2 = _ua_subscription()
    sub2 = {**sub2, "endpoint": "https://push.example.com/sub/xyz"}
    webpush.add_subscription("owner1", sub1)
    webpush.add_subscription("owner2", sub2)
    hits = []
    webpush.send_push(
        webpush.TEST_PUSH,
        account="owner1",
        poster=lambda u, b, h: hits.append(u) or 200,
    )
    assert hits == ["https://push.example.com/sub/abc"]
