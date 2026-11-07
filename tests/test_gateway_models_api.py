"""模型管理 HTTP 面：鉴权/管理员收敛、脱敏、连通性测试分类。

用真实 ``TestClient`` + 真 JWT（与 ``tests/test_gateway_auth.py`` 同款），
不 stub 掉签名校验；这样"管理写入只给管理员或持有账号作用域的账号"是经过
真实鉴权链验证的。成员只能维护自己的 Key 归属，由账号树用例覆盖。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from src.gateway.main import get_app
from src.gateway.models import _classify_error


class APIConnectionError(Exception):
    """与 openai SDK 同名同文案：真实不可达时 SDK 只抛出这一句 ``Connection error.``。"""


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
        # 定稿契约：权限拒绝一律中性文案，不泄露角色/层级信息。
        assert response.json()["detail"] == "无权执行该操作"

    def test_admin_role_can_create(self, client, admin_headers):
        response = client.post("/v1/models", json=_payload(), headers=admin_headers)
        assert response.status_code == 200, response.text

    def test_l0_scope_also_counts_as_admin(self, client, l0_headers):
        response = client.post("/v1/models", json=_payload(), headers=l0_headers)
        assert response.status_code == 200, response.text

    def test_anonymous_cannot_read_model_detail(self, client, admin_headers):
        """P2-3：详情接口必须登录。

        匿名可读会泄露 ``base_url`` / ``owner_principal`` / ``api_key_masked``，
        还能跨归属读别人的模型；登录后的可见性规则保持不变。
        """
        created = client.post(
            "/v1/models", json=_payload(api_key="sk-anon-123456"), headers=admin_headers
        ).json()

        anonymous = client.get(f"/v1/models/{created['id']}")
        assert anonymous.status_code == 401, anonymous.text
        assert "sk-anon" not in anonymous.text
        assert "owner" not in anonymous.text

        authed = client.get(f"/v1/models/{created['id']}", headers=admin_headers)
        assert authed.status_code == 200, authed.text
        body = authed.json()
        assert body["owner_principal"] == created["owner_principal"]
        assert body["base_url"] == created["base_url"]
        assert body["api_key_set"] is True


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

    def test_openai_protocol_with_deepseek_endpoint_reports_real_vendor(
        self, client, admin_headers
    ):
        """provider=openai 只是协议适配器；base_url 指向 DeepSeek 时厂商是 DeepSeek。"""
        created = client.post(
            "/v1/models",
            json=_payload(
                name="deepseek",
                provider="openai",
                model="deepseek-chat",
                base_url="https://api.deepseek.com/v1",
                api_key="sk-deepseek-abcdefghijklmnop",
            ),
            headers=admin_headers,
        ).json()
        assert created["provider"] == "openai", "已有配置的 provider 字段不得改动"
        assert created["base_url"] == "https://api.deepseek.com/v1"
        assert created["vendor"] == "deepseek"
        assert created["provider_display_name"] == "DeepSeek"
        assert created["display_status"].startswith("DeepSeek")

        listed = client.get("/v1/models", headers=admin_headers).json()["models"][0]
        assert listed["vendor"] == "deepseek"

        # 路由行为不变：仍然按 provider=openai 构造 OpenAI 兼容适配器，端点不变。
        from src.ai.providers import resolve_provider

        provider = resolve_provider(created["id"])
        assert type(provider).__name__ == "OpenAIProvider"
        assert str(getattr(provider, "base_url", "")).startswith(
            "https://api.deepseek.com"
        )

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

    def test_arbitrary_openai_compatible_model_is_accepted(self, client, admin_headers):
        """任意 OpenAI 兼容模型：模型名不做白名单，端点由用户填写。"""
        created = client.post(
            "/v1/models",
            json=_payload(
                name="my-private-model",
                provider="openai",
                model="acme/private-model:v9-beta",
                base_url="https://api.openai.com/v1",
                api_key="sk-custom-abcdefghijklmnop",
            ),
            headers=admin_headers,
        )
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["model"] == "acme/private-model:v9-beta"
        assert body["base_url"] == "https://api.openai.com/v1"
        assert body["provider"] == "openai"
        assert body["is_default"] is True

    def test_put_with_masked_key_keeps_the_real_one(self, client, admin_headers):
        """编辑表单回传 ``api_key_masked`` 不能覆盖已保存的真实 Key。"""
        from src.ai.model_config import get_model_config_store

        created = client.post(
            "/v1/models",
            json=_payload(
                name="masked-api",
                provider="openai",
                model="gpt-4o-mini",
                base_url="https://api.openai.com/v1",
                api_key="sk-real-secret-123456",
            ),
            headers=admin_headers,
        ).json()
        masked = created["api_key_masked"]
        assert masked and masked != "sk-real-secret-123456"

        updated = client.put(
            f"/v1/models/{created['id']}",
            json={"api_key": masked, "name": "masked-api-renamed"},
            headers=admin_headers,
        )
        assert updated.status_code == 200, updated.text
        assert (
            get_model_config_store().resolve(created["id"])["api_key"]
            == "sk-real-secret-123456"
        )

    def test_openai_provider_cannot_reach_local_ollama(self, client, admin_headers):
        """P2-6 的取舍：私网放行只给 provider=ollama。

        即便目标是 ``127.0.0.1:11434``，``provider=openai`` 也按公网规则拒绝；
        本机 Ollama 仍可用，但要走 ``provider=ollama``（见
        ``test_local_ollama_target_is_still_allowed``），避免
        ``OLLAMA_BASE_URL`` 之类的配置把私网白名单扩散到所有 provider。
        """
        created = client.post(
            "/v1/models",
            json=_payload(
                name="ollama-openai-compat",
                provider="openai",
                model="qwen2.5:3b",
                base_url="http://127.0.0.1:11434/v1",
                api_key="ollama",
            ),
            headers=admin_headers,
        )
        assert created.status_code == 400, created.text
        assert "SSRF" in created.json()["detail"]


class TestPresets:
    def test_presets_include_custom_openai_compatible_entry(self, client, reader_headers):
        response = client.get("/v1/models/presets", headers=reader_headers)
        assert response.status_code == 200, response.text
        presets = {p["id"]: p for p in response.json()["presets"]}
        assert "openai_compatible" in presets
        custom = presets["openai_compatible"]
        assert custom["provider"] == "openai"
        assert custom["base_url"] is None
        assert custom["model"] == ""
        assert custom["api_key"] is None
        # 内置服务商仍是"预设"，不是封闭枚举。
        assert {"openai", "anthropic", "google", "ollama"} <= set(presets)


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
            # openai SDK 连接失败时只抛 ``Connection error.``（类名 APIConnectionError）；
            # 不认它就会把"地址填错/端口没开/断网"说成"提供方服务返回错误"。
            (APIConnectionError("Connection error."), "unreachable"),
            (RuntimeError("[Errno 11001] getaddrinfo failed"), "unreachable"),
            (RuntimeError("[WinError 10061] No connection could be made"), "unreachable"),
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

    @pytest.mark.parametrize("provider", ["openai", "deepseek", "anthropic"])
    def test_ollama_env_does_not_widen_other_providers(
        self, client, admin_headers, monkeypatch, provider
    ):
        """P2-6：``OLLAMA_BASE_URL`` 只对 provider=ollama 生效。

        之前 ``allow_private`` 只判断"地址是否等于 ``OLLAMA_BASE_URL``"，
        于是 provider=openai/deepseek 也能借这条配置把 base_url 指向内网。
        """
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://10.0.0.5:11434")

        probed = client.post(
            "/v1/models/test",
            json={
                "provider": provider,
                "model": "some-model",
                "base_url": "http://10.0.0.5:11434/v1",
                "api_key": "sk-test-123456",
                "timeout": 2,
            },
            headers=admin_headers,
        )
        assert probed.status_code == 400, probed.text
        assert "SSRF" in probed.json()["detail"]

        # 保存路径同样收紧，不是只挡"测试连接"。
        saved = client.post(
            "/v1/models",
            json=_payload(
                name=f"private-{provider}",
                provider=provider,
                model="some-model",
                base_url="http://10.0.0.5:11434/v1",
                api_key="sk-test-123456",
            ),
            headers=admin_headers,
        )
        assert saved.status_code == 400, saved.text
        assert "SSRF" in saved.json()["detail"]

    def test_local_ollama_target_is_still_allowed(self):
        """provider=ollama 指本机 11434（含 OpenAI 兼容 /v1）仍放行。"""
        from src.gateway.models import _validate_base_url

        for url in (
            "http://127.0.0.1:11434",
            "http://localhost:11434",
            "http://127.0.0.1:11434/v1",
        ):
            assert _validate_base_url(url, provider="ollama") == url

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

    def test_probe_failure_never_echoes_upstream_english(self, monkeypatch):
        """失败提示只给"下一步改什么"，上游英文原文与 Key 都不进响应。"""
        from src.gateway import models as models_mod

        class BoomProvider:
            def generate(self, *args, **kwargs):
                raise RuntimeError(
                    "HTTP 401 Unauthorized: invalid api key sk-leaky-123456"
                )

        monkeypatch.setattr(
            models_mod, "provider_from_config", lambda cfg: BoomProvider()
        )
        result = models_mod._probe(
            {
                "provider": "openai",
                "model": "no-such-model",
                "base_url": "",
                "timeout": 2,
                "api_key": "sk-leaky-123456",
            }
        )
        assert result["ok"] is False
        assert result["reason"] == "invalid_key"
        assert "HTTP 401" not in result["detail"]
        assert "Unauthorized" not in result["detail"]
        assert "sk-leaky-123456" not in json.dumps(result)
