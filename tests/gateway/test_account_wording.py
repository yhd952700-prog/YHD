"""账号体系面向用户文案：不得泄露层级/角色结构词。

产品反馈：``403 ACCOUNT_SELF_REGISTER_DISABLED`` 的说明写着"管理员已关闭
自助注册"，而"管理员"正是子账号视角不应出现的词。这里用真实 HTTP 走一遍
自助注册与成员可见的拒绝路径，把所有 ``detail`` / ``notice`` / ``message``
捞出来做禁用词断言；扫到任一条就失败。
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from src.gateway.display import ACCOUNT_HIERARCHY_WORDS, machine_fragments
from src.gateway.identity import _registry_report
from src.gateway.main import get_app

#: 响应里可能承载"给用户看的话"的键。
_USER_TEXT_KEYS = ("detail", "notice", "message", "user_message", "hint")


def _user_texts(value) -> list[str]:
    texts: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _USER_TEXT_KEYS and isinstance(item, str):
                texts.append(item)
            texts.extend(_user_texts(item))
    elif isinstance(value, list):
        for item in value:
            texts.extend(_user_texts(item))
    return texts


def _assert_neutral(body) -> None:
    hits = [
        (word, text)
        for text in _user_texts(body)
        for word in ACCOUNT_HIERARCHY_WORDS
        if word in text
    ]
    assert not hits, f"用户可见文案出现层级词：{hits}"


@pytest.fixture()
def _isolate(tmp_path, monkeypatch):
    """身份库/凭据/开关都落到临时目录，绝不碰真实数据。"""
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_BACKEND", "file")
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_FILE", str(tmp_path / "identities.json"))
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY", "wording-test-key")
    monkeypatch.setenv("LIUHAO_AUTH_SECRETS_FILE", str(tmp_path / "auth_secrets.json"))
    monkeypatch.setenv("LIUHAO_ACCOUNT_SETTINGS_FILE", str(tmp_path / "account_settings.json"))
    monkeypatch.setenv("LIUHAO_ALERTS_STORE_PATH", str(tmp_path / "alerts.json"))
    yield


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_account_user_text_has_no_hierarchy_words(_isolate):
    app = get_app()
    with TestClient(app) as client:
        # 1) 未初始化时的公开自助注册：拒绝文案也必须中性
        response = client.post(
            "/v1/accounts/self-register",
            json={"principal": "early", "password": "earlypass123"},
        )
        assert response.status_code == 409, response.text
        _assert_neutral(response.json())

        # 2) 建立根账号并登录
        response = client.post(
            "/v1/accounts/register",
            json={"principal": "root1", "password": "rootpass123"},
        )
        assert response.status_code == 200, response.text
        _assert_neutral(response.json())
        login = client.post(
            "/v1/auth/login", json={"principal": "root1", "secret": "rootpass123"}
        )
        assert login.status_code == 200, login.text
        root = _bearer(login.json()["access_token"])

        # 3) 关闭开关后的拒绝文案（子账号视角最敏感的一条）
        assert client.patch(
            "/v1/accounts/self-registration", json={"allow": False}, headers=root
        ).status_code == 200
        response = client.post(
            "/v1/accounts/self-register",
            json={"principal": "blocked", "password": "blockedpass1"},
        )
        assert response.status_code == 403, response.text
        _assert_neutral(response.json())

        # 4) 恢复开关，成功注册：返回的 notice 不含层级词
        assert client.patch(
            "/v1/accounts/self-registration", json={"allow": True}, headers=root
        ).status_code == 200
        response = client.post(
            "/v1/accounts/self-register",
            json={"principal": "self1", "password": "selfpass123"},
        )
        assert response.status_code == 200, response.text
        _assert_neutral(response.json())
        member = _bearer(response.json()["access_token"])

        # 5) 重复注册的拒绝文案
        response = client.post(
            "/v1/accounts/self-register",
            json={"principal": "self1", "password": "selfpass123"},
        )
        assert response.status_code == 409, response.text
        _assert_neutral(response.json())

        # 6) 成员可见的拒绝路径：整树、建号、跨主体读、开关
        member_denials = [
            ("get", "/v1/accounts/tree", {}),
            ("post", "/v1/accounts", {"json": {"principal": "child1"}}),
            ("patch", "/v1/accounts/self-registration", {"json": {"allow": True}}),
            ("get", "/v1/accounts/root1", {}),
            ("get", "/v1/audit/events?principal=root1", {}),
        ]
        for method, path, kwargs in member_denials:
            response = getattr(client, method)(path, headers=member, **kwargs)
            assert response.status_code == 404, (path, response.status_code, response.text)
            _assert_neutral(response.json())

        # 7) 成员自己的账号详情（正常响应）同样不带结构词
        response = client.get("/v1/accounts/self1", headers=member)
        assert response.status_code == 200, response.text
        _assert_neutral(response.json())


def test_registry_warnings_are_plain_chinese_when_integrity_key_is_unset() -> None:
    """首启未配置完整性密钥时，身份页拿到的警告必须是给人看的话。

    真实证据（2026-11-07，临时数据目录 + TestClient，未设置
    ``LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY``）：旧实现返回的 warnings 里含
    环境变量名、绝对路径、``fail-closed`` / ``HC-11`` 代号和登记脚本路径；
    控制台又会把"整段英文"替换成与密钥无关的权限串说明，等于对用户说了
    一句错话。这里直接盯住后端产出，任何机器串都算回归。
    """

    class _Manager:
        def describe_identity_namespaces(self):
            return {
                "store": {
                    "backend": "file",
                    # 中文用户名 + 空格路径，正是本轮回测的目标环境。
                    "location": r"C:\Users\张三\AppData\Local\LiuHao AI OS\config\human_identities.json",
                    "configured": True,
                },
                "registry_integrity": {
                    "integrity_enforced": False,
                    "integrity_key_env": "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY",
                    "last_load": {"admitted": 0, "rejected": ["alice"]},
                },
                "registry_refusals": [
                    "Traceback (most recent call last): "
                    "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY is unset"
                ],
            }

    registry, warnings = _registry_report(_Manager())

    assert registry["integrity_enforced"] is False
    assert len(warnings) == 3
    joined = "".join(warnings)
    forbidden = (
        "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY",
        "fail-closed",
        "HC-11",
        "U6",
        "scripts/register_human_identity.py",
        "Traceback",
        "\\",
        "/",
    )
    for fragment in forbidden:
        assert fragment not in joined, (fragment, joined)
    assert machine_fragments(joined) == []
    assert not [word for word in ACCOUNT_HIERARCHY_WORDS if word in joined]
    # 除账号名本身外不应出现英文（不能夹带实现细节）。
    assert set(re.findall(r"[A-Za-z]{4,}", joined)) == {"alice"}
    assert "alice" in joined
