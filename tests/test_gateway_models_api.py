"""模型管理 HTTP 面：鉴权/管理员收敛、脱敏、连通性测试分类。

用真实 ``TestClient`` + 真 JWT（与 ``tests/test_gateway_auth.py`` 同款），
不 stub 掉签名校验；这样"写权限只给管理员"是经过真实鉴权链验证的。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from src.gateway.main import get_app
from src.gateway.models import _classify_error


@pytest.fixture
def client(monkeypatch, tmp_path):
    """隔离身份库后启动真实 app（JWT 校验走真实 handler，不 stub）。"""
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_BACKEND", "file")
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_FILE", str(tmp_path / "humans.json"))
    import src.kernels.identity as identity_module

    monkeypatch.setattr(identity_module, "_global_manager", None)
    with TestClient(get_app()) as test_client:
        yield test_client


def _token(roles=None, scope=None):
    from src.security import get_jwt_handler

    metadata = {"kind": "human", "scope": scope} if scope else {"kind": "human"}
    token, _ = get_jwt_handler().create_token(
        subject="model-admin", roles=roles or [], metadata=metadata
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_headers():
    return _token(roles=["admin"])


@pytest.fixture
def l0_headers():
    return _token(scope="L0")


@pytest.fixture
def reader_headers():
    return _token()


def _payload(**overrides):
    body = {"name": "console-mock", "provider": "mock", "model": "mock-model", "timeout": 10}
    body.update(overrides)
    return body


class TestAccessControl:
    def test_read_requires_auth(self, client):
        assert client.get("/v1/models").status_code == 401

    def test_reader_can_list_but_not_write(self, client, reader_headers):
        assert client.get("/v1/models", headers=reader_headers).status_code == 200
        response = client.post("/v1/models", json=_payload(), headers=reader_headers)
        assert response.status_code == 403
        assert "管理员" in response.json()["detail"]

    def test_admin_role_can_create(self, client, admin_headers):
        response = client.post("/v1/models", json=_payload(), headers=admin_headers)
        assert response.status_code == 200, response.text

    def test_l0_scope_also_counts_as_admin(self, client, l0_headers):
        response = client.post("/v1/models", json=_payload(), headers=l0_headers)
        assert response.status_code == 200, response.text


class TestCrudAndMasking:
    secret = "sk-console-secret-abcdefghijklmnop"

    def test_list_never_returns_the_key(self, client, admin_headers):
        created = client.post(
            "/v1/models", json=_payload(api_key=self.secret), headers=admin_headers
        ).json()
        assert created["api_key_masked"] == "sk-c…mnop"
        assert created["api_key_set"] is True
        body = client.get("/v1/models", headers=admin_headers).text
        assert self.secret not in body
        assert "enc:v1:" not in body
        assert json.loads(body)["default_id"] == created["id"]

    def test_database_holds_ciphertext(self, client, admin_headers):
        from src.ai.model_config import get_model_config_store

        created = client.post(
            "/v1/models", json=_payload(api_key=self.secret), headers=admin_headers
        ).json()
        store = get_model_config_store()
        row = store._connect().execute(
            "SELECT api_key_enc FROM ai_model_configs WHERE id = ?", (created["id"],)
        ).fetchone()
        assert row["api_key_enc"].startswith("enc:v1:")
        assert self.secret not in row["api_key_enc"]

    def test_update_without_key_keeps_the_existing_one(self, client, admin_headers):
        created = client.post(
            "/v1/models", json=_payload(api_key=self.secret), headers=admin_headers
        ).json()
        updated = client.put(
            f"/v1/models/{created['id']}",
            json={"model": "changed-model"},
            headers=admin_headers,
        ).json()
        assert updated["model"] == "changed-model"
        assert updated["api_key_set"] is True
        assert self.secret not in json.dumps(updated)

    def test_duplicate_name_returns_409(self, client, admin_headers):
        client.post("/v1/models", json=_payload(), headers=admin_headers)
        response = client.post("/v1/models", json=_payload(), headers=admin_headers)
        assert response.status_code == 409

    def test_unknown_model_returns_404(self, client, admin_headers):
        assert (
            client.put("/v1/models/not-there", json={"model": "x"}, headers=admin_headers).status_code
            == 404
        )

    def test_set_default_and_delete_promotes_remaining(self, client, admin_headers):
        first = client.post(
            "/v1/models", json=_payload(name="first"), headers=admin_headers
        ).json()
        second = client.post(
            "/v1/models", json=_payload(name="second"), headers=admin_headers
        ).json()
        assert client.post(
            f"/v1/models/{second['id']}/default", headers=admin_headers
        ).json()["is_default"] is True
        deleted = client.delete(f"/v1/models/{second['id']}", headers=admin_headers).json()
        assert deleted["default_id"] == first["id"]
        assert client.get("/v1/models", headers=admin_headers).json()["default_id"] == first["id"]

    def test_delete_unknown_is_404(self, client, admin_headers):
        assert client.delete("/v1/models/nope", headers=admin_headers).status_code == 404


class TestConnectionProbe:
    def test_mock_provider_probe_succeeds(self, client, admin_headers):
        result = client.post(
            "/v1/models/test",
            json={"provider": "mock", "model": "mock-model", "timeout": 5},
            headers=admin_headers,
        ).json()
        assert result["ok"] is True

    def test_loopback_base_url_is_rejected(self, client, admin_headers):
        """SSRF 防护：调用方现填的回环/私网地址在探测前就被 400 拒绝。"""
        result = client.post(
            "/v1/models/test",
            json={
                "provider": "ollama",
                "model": "qwen2.5:3b",
                "base_url": "http://127.0.0.1:9",
                "timeout": 2,
            },
            headers=admin_headers,
        )
        assert result.status_code == 400, result.text
        assert "SSRF" in result.json()["detail"]

    def test_non_http_scheme_is_rejected(self, client, admin_headers):
        result = client.post(
            "/v1/models/test",
            json={
                "provider": "ollama",
                "model": "qwen2.5:3b",
                "base_url": "file:///etc/passwd",
                "timeout": 2,
            },
            headers=admin_headers,
        )
        assert result.status_code == 400, result.text
        assert "http/https" in result.json()["detail"]

    def test_saved_model_probe_uses_stored_key_but_never_returns_it(self, client, admin_headers):
        secret = "sk-probe-saved-1234567890"
        created = client.post(
            "/v1/models", json=_payload(api_key=secret), headers=admin_headers
        ).json()
        result = client.post(
            f"/v1/models/{created['id']}/test", headers=admin_headers
        ).json()
        assert result["ok"] is True
        assert secret not in json.dumps(result)

    def test_disabled_model_probe_is_404(self, client, admin_headers):
        created = client.post("/v1/models", json=_payload(), headers=admin_headers).json()
        client.put(
            f"/v1/models/{created['id']}", json={"enabled": False}, headers=admin_headers
        )
        assert client.post(
            f"/v1/models/{created['id']}/test", headers=admin_headers
        ).status_code == 404

    @pytest.mark.parametrize(
        "exc, expected",
        [
            (TimeoutError("timed out"), "timeout"),
            (ConnectionError("connection refused"), "unreachable"),
            (RuntimeError("Ollama error 404: model not found"), "model_not_found"),
            (RuntimeError("401 Unauthorized"), "invalid_key"),
            # 吊销/停用与“填错 Key”处理方式不同（换 Key vs 改 Key），必须分开。
            (RuntimeError("401 this api key has been revoked"), "key_revoked"),
            (RuntimeError("403 account deactivated"), "key_revoked"),
            # 额度耗尽是充值问题，不是等一会儿的限流问题。
            (RuntimeError("402 Payment Required: Insufficient Balance"), "quota_exhausted"),
            (RuntimeError("429 quota exceeded"), "quota_exhausted"),
            (RuntimeError("429 Too Many Requests"), "rate_limited"),
            (RuntimeError("boom"), "upstream_error"),
        ],
    )
    def test_error_classification(self, exc, expected):
        assert _classify_error(exc) == expected

    def test_private_url_matching_saved_config_is_allowed(self, monkeypatch):
        """已保存配置批准的本地地址（本机 Ollama）仍可测试连接。"""
        from src.gateway import models as models_mod
        from src.gateway.models import TestPayload

        stored = {
            "name": "local-ollama",
            "provider": "ollama",
            "model": "qwen2.5:3b",
            "base_url": "http://127.0.0.1:11434",
        }
        monkeypatch.setattr(
            models_mod,
            "_store",
            lambda: type(
                "S", (), {"resolve": staticmethod(lambda mid: stored if mid == "local-ollama" else None)}
            )(),
        )
        cfg = models_mod._test_cfg(
            TestPayload(
                provider="ollama",
                model="qwen2.5:3b",
                model_id="local-ollama",
                base_url="http://127.0.0.1:11434",
                timeout=2,
            )
        )
        assert cfg["base_url"] == "http://127.0.0.1:11434"

    def test_private_url_not_matching_saved_config_is_still_rejected(self, monkeypatch):
        """同一已保存配置，但地址被改成另一个内网端口 -> 仍是 SSRF，拒绝。"""
        from fastapi import HTTPException

        from src.gateway import models as models_mod
        from src.gateway.models import TestPayload

        stored = {
            "name": "local-ollama",
            "provider": "ollama",
            "model": "qwen2.5:3b",
            "base_url": "http://127.0.0.1:11434",
        }
        monkeypatch.setattr(
            models_mod,
            "_store",
            lambda: type(
                "S", (), {"resolve": staticmethod(lambda mid: stored if mid == "local-ollama" else None)}
            )(),
        )
        with pytest.raises(HTTPException) as exc_info:
            models_mod._test_cfg(
                TestPayload(
                    provider="ollama",
                    model="qwen2.5:3b",
                    model_id="local-ollama",
                    base_url="http://127.0.0.1:6379",
                    timeout=2,
                )
            )
        assert exc_info.value.status_code == 400
        assert "SSRF" in exc_info.value.detail
