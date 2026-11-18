"""a2a/a2b/a3s/a3m + voice/ocr 路由专属 smoke（完善第二轮 A4）。

此前这批 0.4.6 新路由没有任何专属测试。本文件只钉两条底线契约：
1. 未登录访问一律 401（路由真实存在且要求鉴权，不是 404、更不是裸奔）；
2. 登录后挑每组一个只读端点 smoke：200 且返回结构是 dict（含期望键）。
深度业务行为由各模块自己的测试负责，这里不冒充全覆盖。
"""

from __future__ import annotations

from typing import Dict

import pytest
from fastapi.testclient import TestClient

PASSWORD = "a-series-" + "test-2026"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    base = tmp_path
    values = {
        "LIUHAO_HUMAN_IDENTITIES_BACKEND": "file",
        "LIUHAO_HUMAN_IDENTITIES_FILE": str(base / "identities.json"),
        "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY": "a-series-test-key",
        "LIUHAO_AUTH_SECRETS_FILE": str(base / "auth_secrets.json"),
        "LIUHAO_WORKBENCH_DB": str(base / "workbench.db"),
        "LIUHAO_DATA_DIR": str(base / "data"),
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    import src.kernels.identity as identity_module

    monkeypatch.setattr(identity_module, "_global_manager", None)
    yield values


@pytest.fixture()
def client(env):
    from src.gateway.main import get_app

    with TestClient(get_app()) as test_client:
        yield test_client


def _auth(client: TestClient) -> Dict[str, str]:
    r = client.post("/v1/accounts/register",
                    json={"principal": "boss", "password": PASSWORD, "display_name": "Boss"})
    assert r.status_code in (200, 409), r.text
    r = client.post("/v1/auth/login", json={"principal": "boss", "secret": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


PROTECTED_GETS = [
    "/v1/a2a/backup/status",
    "/v1/a2a/backup/list",
    "/v1/a2b/tasks/stalled",
    "/v1/a2b/human-waits",
    "/v1/a3s/upgrade/auto-rollback/status",
    "/v1/a3s/models/custom",
    "/v1/a3m/me/key-status",
    "/v1/voice/status",
    "/v1/ocr/status",
]


@pytest.mark.parametrize("path", PROTECTED_GETS)
def test_routes_require_auth_401(client, path):
    r = client.get(path)
    assert r.status_code == 401, f"{path} -> {r.status_code} (期望 401)"


@pytest.mark.parametrize("path", PROTECTED_GETS)
def test_routes_reject_garbage_token_401(client, path):
    r = client.get(path, headers={"Authorization": "Bearer not-a-token"})
    assert r.status_code == 401, f"{path} -> {r.status_code}"


def test_backup_status_smoke(client):
    r = client.get("/v1/a2a/backup/status", headers=_auth(client))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["auto_backup_hour"] == 3
    assert "ran" in body and "message" in body


def test_voice_status_smoke(client):
    r = client.get("/v1/voice/status", headers=_auth(client))
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), dict)


def test_ocr_status_smoke(client):
    r = client.get("/v1/ocr/status", headers=_auth(client))
    assert r.status_code == 200, r.text
    body = r.json()
    assert "available" in body  # 引擎在不在是环境事实，只钉结构


def test_a3m_key_status_smoke(client):
    r = client.get("/v1/a3m/me/key-status", headers=_auth(client))
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), dict)


def test_a2b_stalled_smoke(client):
    r = client.get("/v1/a2b/tasks/stalled", headers=_auth(client))
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), (dict, list))
