"""工作台名字配置（2026-11-12 G 组：产品改名 鎏灏 → 贾维斯，且名字可改）。

唯一事实源：``src/brand.py`` 的 ``resolve_brand`` / ``set_workspace_name``；
出厂默认值来自 ``src/brand.json``（Jarvis · 贾维斯），用户改名后存在
数据目录的 ``workspace_settings.json`` 里，全站只读这一处。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.brand import (
    BRAND_LOCKUP,
    BRAND_NAME_EN,
    BRAND_NAME_ZH,
    load_workspace_settings,
    resolve_brand,
    set_workspace_name,
)


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    settings_file = tmp_path / "workspace_settings.json"
    monkeypatch.setenv("LIUHAO_WORKSPACE_SETTINGS_FILE", str(settings_file))
    return settings_file


def test_factory_default_is_jarvis(isolated_settings):
    assert BRAND_NAME_EN == "Jarvis"
    assert BRAND_NAME_ZH == "贾维斯"
    assert BRAND_LOCKUP == "Jarvis · 贾维斯"
    assert resolve_brand() == {
        "name_en": "Jarvis",
        "name_zh": "贾维斯",
        "lockup": "Jarvis · 贾维斯",
        "category": "Human-Sovereign Agent Operating System",
        "customized": False,
    }


def test_set_custom_name_is_read_back(isolated_settings):
    result = set_workspace_name("阿强", "Ah Qiang")
    assert result["lockup"] == "Ah Qiang · 阿强"
    assert result["customized"] is True
    assert resolve_brand()["name_zh"] == "阿强"
    assert json.loads(isolated_settings.read_text(encoding="utf-8")) == {
        "name_en": "Ah Qiang",
        "name_zh": "阿强",
    }


def test_set_name_without_english_uses_chinese_only(isolated_settings):
    result = set_workspace_name("阿强")
    assert result["name_en"] == "阿强"
    assert result["lockup"] == "阿强"


def test_set_name_rejects_empty_and_too_long(isolated_settings):
    with pytest.raises(ValueError):
        set_workspace_name("   ")
    with pytest.raises(ValueError):
        set_workspace_name("x" * 41)
    with pytest.raises(ValueError):
        set_workspace_name("阿强", "y" * 81)
    # 校验失败不写文件，出厂默认继续生效。
    assert not isolated_settings.exists()
    assert resolve_brand()["customized"] is False


def test_corrupt_settings_file_falls_back_to_default(tmp_path, monkeypatch):
    settings_file = tmp_path / "workspace_settings.json"
    settings_file.write_text("{ not json", encoding="utf-8")
    monkeypatch.setenv("LIUHAO_WORKSPACE_SETTINGS_FILE", str(settings_file))
    assert load_workspace_settings() == {}
    assert resolve_brand()["name_zh"] == "贾维斯"


def test_missing_settings_file_falls_back_to_default(isolated_settings):
    assert load_workspace_settings() == {}
    assert resolve_brand()["lockup"] == "Jarvis · 贾维斯"


def test_gateway_workspace_endpoint_roundtrip(isolated_settings):
    """GET 公开读、PUT 仅根账号写；改名后 GET 立刻返回新名字。"""
    from fastapi.testclient import TestClient

    from src.gateway.main import get_app

    client = TestClient(get_app(), raise_server_exceptions=False)

    got = client.get("/v1/workspace/name")
    assert got.status_code == 200
    assert got.json()["lockup"] == "Jarvis · 贾维斯"

    # 未登录写名字：必须被鉴权拦住，不能悄悄改了全站名字。
    denied = client.put("/v1/workspace/name", json={"name_zh": "阿强"})
    assert denied.status_code in (401, 403)
    assert resolve_brand()["customized"] is False


@pytest.fixture()
def _root_client(tmp_path, monkeypatch):
    """真实网关 + 临时数据目录 + 首个注册账号（即根账号）。"""
    base = tmp_path
    for key, value in {
        "LIUHAO_HUMAN_IDENTITIES_BACKEND": "file",
        "LIUHAO_HUMAN_IDENTITIES_FILE": str(base / "identities.json"),
        "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY": "workspace-name-test-key",
        "LIUHAO_AUTH_SECRETS_FILE": str(base / "auth_secrets.json"),
        "LIUHAO_DATA_DIR": str(base / "data"),
    }.items():
        monkeypatch.setenv(key, value)
    import src.kernels.identity as identity_module

    monkeypatch.setattr(identity_module, "_global_manager", None)
    from fastapi.testclient import TestClient

    from src.gateway.main import get_app

    with TestClient(get_app()) as client:
        reg = client.post(
            "/v1/accounts/register",
            json={"principal": "owner1", "password": "owner-pass-123"},
        )
        assert reg.status_code == 200, reg.text
        login = client.post(
            "/v1/auth/login",
            json={"principal": "owner1", "secret": "owner-pass-123"},
        )
        assert login.status_code == 200, login.text
        assert login.json()["is_root"] is True
        yield client, {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_gateway_root_can_rename_and_rename_back(_root_client):
    """根账号改名 → GET 立刻返回新名字 → 改回贾维斯 → 恢复默认。

    这就是“把名字改成另一个测试名，确认跟着变，再改回默认贾维斯”的
    后端实测；前端顶栏 / 登录页 / 助手名都只读这个端点
    （见 apps/console/console/src/lib/workspaceName.test.ts 的阿强用例）。
    """
    client, auth = _root_client

    renamed = client.put(
        "/v1/workspace/name",
        json={"name_zh": "阿强", "name_en": "Ah Qiang"},
        headers=auth,
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["lockup"] == "Ah Qiang · 阿强"
    assert client.get("/v1/workspace/name").json()["name_zh"] == "阿强"

    restored = client.put(
        "/v1/workspace/name",
        json={"name_zh": "贾维斯", "name_en": "Jarvis"},
        headers=auth,
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["lockup"] == "Jarvis · 贾维斯"
    assert client.get("/v1/workspace/name").json()["customized"] is True
    # 配置文件里仍是显式写回的默认值（与出厂默认同值），界面显示一致。
    assert resolve_brand()["name_zh"] == "贾维斯"
