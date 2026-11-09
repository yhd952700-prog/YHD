"""模型管理 HTTP 面：鉴权/管理员收敛、脱敏、连通性测试分类。

用真实 ``TestClient`` + 真 JWT（与 ``tests/test_gateway_auth.py`` 同款），
不 stub 掉签名校验；这样"管理写入只给管理员或持有账号作用域的账号"是经过
真实鉴权链验证的。成员只能维护自己的 Key 归属，由账号树用例覆盖。
"""
from __future__ import annotations

import json
import ssl

import pytest
from fastapi.testclient import TestClient

from src.ai.providers import BaseProvider, ProviderTimeoutError
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
        # 未测试通过的模型不允许设为默认：后端必须拒绝并给中文提示。
        refused = client.post(
            f"/v1/models/{second['id']}/default", headers=admin_headers
        )
        assert refused.status_code == 409
        assert "测试连接" in refused.json()["detail"]
        # 「测试连接」通过后落库 verified，才允许设为默认。
        probe = client.post(f"/v1/models/{second['id']}/test", headers=admin_headers)
        assert probe.status_code == 200, probe.text
        assert probe.json()["ok"] is True, probe.text
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

    def test_openai_provider_can_use_loopback_model_server(self, client, admin_headers):
        """回环例外对每个 provider 生效：LM Studio/vLLM 走 OpenAI 兼容协议。

        ``127.0.0.0/8`` / ``::1`` / ``localhost`` 到不了内网，端口不限，
        所以不能再按"provider 必须是 ollama"来挡。
        """
        for name, base_url in (
            ("lmstudio", "http://127.0.0.1:1234/v1"),
            ("vllm", "http://[::1]:8080/v1"),
            ("localai", "http://localhost:8080/v1"),
        ):
            created = client.post(
                "/v1/models",
                json=_payload(
                    name=f"loopback-{name}",
                    provider="openai",
                    model="qwen2.5:3b",
                    base_url=base_url,
                    api_key="sk-local",
                ),
                headers=admin_headers,
            )
            # 保存不做连通性探测，因此只可能是校验失败；这里必须 200。
            assert created.status_code == 200, created.text
            assert created.json()["base_url"] == base_url


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

    def test_presets_include_domestic_openai_compatible_vendors(
        self, client, reader_headers
    ):
        """通义/智谱/方舟：端点预先填好，仍需用户自己填 Key。

        方舟的模型名是推理接入点 ID（``ep-…``），预设里留空但必须带提示，
        不能让用户以为是模型显示名。MiniMax 路径形态尚未实测，宁可不给。
        """
        response = client.get("/v1/models/presets", headers=reader_headers)
        assert response.status_code == 200, response.text
        presets = {p["id"]: p for p in response.json()["presets"]}

        dashscope = presets["dashscope"]
        assert dashscope["provider"] == "openai"
        assert dashscope["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"
        assert dashscope["model"] == "qwen-plus"
        assert dashscope["requires_api_key"] is True
        assert dashscope["api_key"] is None
        assert dashscope["hint"]

        zhipu = presets["zhipu"]
        assert zhipu["provider"] == "openai"
        assert zhipu["base_url"] == "https://open.bigmodel.cn/api/paas/v4"
        assert zhipu["model"] == "glm-4-flash"
        assert zhipu["api_key"] is None

        ark = presets["volcengine_ark"]
        assert ark["provider"] == "openai"
        assert ark["base_url"] == "https://ark.cn-beijing.volces.com/api/v3"
        # 接入点 ID 无法预填，但必须写明"要填接入点 ID"，不能留空让人瞎猜。
        assert ark["model"] == ""
        assert "接入点 ID" in ark["hint"]

        # 没实测过端点形态的厂商不进入预设列表。
        assert "minimax" not in presets


class TestConnectionProbe:
    def test_mock_provider_probe_succeeds(self, client, admin_headers):
        result = client.post(
            "/v1/models/test",
            json={"provider": "mock", "model": "mock-model", "timeout": 5},
            headers=admin_headers,
        ).json()
        assert result["ok"] is True

    def test_key_with_surrounding_spaces_is_trimmed_before_probe(self, client, admin_headers):
        """带首尾空格的 Key 不能再让请求"坏在请求头"上：测试连接前统一 strip。"""
        result = client.post(
            "/v1/models/test",
            json={
                "provider": "mock",
                "model": "mock-model",
                "api_key": "   sk-probe-with-spaces   ",
                "timeout": 5,
            },
            headers=admin_headers,
        )
        assert result.status_code == 200, result.text
        assert result.json()["ok"] is True

    def test_key_with_interior_newline_is_refused_with_chinese_hint(self, client, admin_headers):
        result = client.post(
            "/v1/models/test",
            json={
                "provider": "mock",
                "model": "mock-model",
                "api_key": "sk-abc\ndef",
                "timeout": 5,
            },
            headers=admin_headers,
        )
        assert result.status_code == 400, result.text
        assert "重新复制" in result.json()["detail"]

    def test_overlong_key_is_refused(self, client, admin_headers):
        result = client.post(
            "/v1/models/test",
            json={
                "provider": "mock",
                "model": "mock-model",
                "api_key": "sk-" + "a" * 5000,
                "timeout": 5,
            },
            headers=admin_headers,
        )
        assert result.status_code == 400, result.text
        assert "过长" in result.json()["detail"]

    def test_loopback_base_url_passes_the_policy_gate(self, client, admin_headers):
        """回环地址不再按 SSRF 拒绝：探测如实返回"连不上"，而不是 400。"""
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
        assert result.status_code == 200, result.text
        assert result.json()["ok"] is False
        assert result.json()["reason"] in ("unreachable", "timeout")

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

    def test_certificate_failures_are_not_reported_as_unreachable(self):
        """自签/私有 CA 的证书失败在 SDK 里也是连接错误，不能报"地址不可达"。"""

        def cert(message: str, code: int, verify_message: str) -> ssl.SSLCertVerificationError:
            exc = ssl.SSLCertVerificationError(message)
            exc.verify_code = code
            exc.verify_message = verify_message
            return exc

        def wrapped(exc: BaseException) -> APIConnectionError:
            outer = APIConnectionError("Connection error.")
            outer.__cause__ = exc
            return outer

        untrusted = wrapped(
            cert(
                "certificate verify failed: unable to get local issuer certificate",
                20,
                "unable to get local issuer certificate",
            )
        )
        hostname = wrapped(
            cert(
                "certificate verify failed: Hostname mismatch, certificate is not valid for 'api.example.com'.",
                62,
                "Hostname mismatch, certificate is not valid for 'api.example.com'.",
            )
        )
        expired = wrapped(
            cert(
                "certificate verify failed: certificate has expired",
                10,
                "certificate has expired",
            )
        )

        assert _classify_error(untrusted) == "certificate_untrusted"
        assert _classify_error(hostname) == "certificate_hostname"
        assert _classify_error(expired) == "certificate_expired"

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
        """同一已保存配置，但地址被改成另一台内网机器 -> 仍是 SSRF，拒绝。"""
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
                    base_url="http://10.0.0.5:11434",
                    timeout=2,
                )
            )
        assert exc_info.value.status_code == 400
        assert "SSRF" in exc_info.value.detail

    def test_probe_failure_never_echoes_upstream_english(self, monkeypatch):
        """失败提示只给"下一步改什么"，上游英文原文与 Key 都不进响应。"""
        from src.ai.providers import BaseProvider
        from src.gateway import models as models_mod

        class BoomProvider(BaseProvider):
            def __init__(self):
                super().__init__("boom", "no-such-model", api_key="sk-leaky-123456")

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


class TestProbeRetryNotice:
    """测试页与聊天共用重试策略：重试要告知，短超时一次都不重试。"""

    class _Flaky(BaseProvider):
        def __init__(self, *, timeout: float):
            super().__init__("flaky", "flaky-model", api_key="sk-flaky-123456", timeout=timeout, retry_base_delay=0.0)
            self.calls = 0

        def generate(self, prompt, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise ProviderTimeoutError("模型服务", self.timeout, before_first_token=True)
            return "pong"

        def chat(self, messages, **kwargs):
            return self.generate("ping", **kwargs)

    def _cfg(self, timeout: float):
        return {
            "provider": "openai",
            "model": "flaky-model",
            "base_url": "",
            "timeout": timeout,
            "api_key": "sk-flaky-123456",
        }

    def test_retry_notice_is_returned_to_the_test_page(self, monkeypatch):
        from src.gateway import models as models_mod

        provider = self._Flaky(timeout=30)
        monkeypatch.setattr(models_mod, "provider_from_config", lambda cfg: provider)
        result = models_mod._probe(self._cfg(30))
        assert result["ok"] is True
        assert provider.calls == 2
        assert result["notice"], "测试页必须告知发生过自动重试（不能只有流式聊天有）"
        assert "重试" in result["notice"]

    def test_short_timeout_probe_does_not_retry(self, monkeypatch):
        from src.gateway import models as models_mod

        class _AlwaysTimeout(self._Flaky):
            def generate(self, prompt, **kwargs):
                self.calls += 1
                raise ProviderTimeoutError("模型服务", self.timeout, before_first_token=True)

        provider = _AlwaysTimeout(timeout=2)
        monkeypatch.setattr(models_mod, "provider_from_config", lambda cfg: provider)
        result = models_mod._probe(self._cfg(2))
        assert result["ok"] is False
        assert result["reason"] == "timeout"
        assert provider.calls == 1, "timeout=2 时测试连接也只试一次"
        assert result["notice"] == ""
        # 分类与文案不变：仍是超时，不是"证书/地址"之类的误判。
        assert "超时" in result["detail"]


class TestBaseUrlTargetPolicy:
    """回环全放行（端口不限）；RFC1918/链路本地/元数据仍拒绝。"""

    @pytest.mark.parametrize(
        "base_url",
        [
            "http://127.0.0.1:1234/v1",
            "http://127.0.0.1:8000/v1",
            "http://127.0.0.1:11434/v1",
            "http://localhost:8080/v1",
            "http://[::1]:8080/v1",
        ],
    )
    def test_loopback_allowed_for_any_provider(self, base_url):
        from src.gateway.models import _validate_base_url

        for provider in ("openai", "deepseek", "anthropic", "google", "ollama"):
            assert _validate_base_url(base_url, provider=provider) == base_url

    @pytest.mark.parametrize(
        "base_url",
        [
            "http://192.168.1.10:1234/v1",
            "http://10.0.0.5:11434/v1",
            "http://172.16.3.4:8000/v1",
            "http://169.254.169.254/latest",
            "http://0.0.0.0:1234/v1",
        ],
    )
    def test_private_and_metadata_targets_still_rejected(self, base_url):
        from fastapi import HTTPException

        from src.gateway.models import _validate_base_url

        with pytest.raises(HTTPException) as exc_info:
            _validate_base_url(base_url, provider="openai")
        assert exc_info.value.status_code == 400

    def test_ollama_env_still_only_scoped_to_ollama(self, monkeypatch):
        """``OLLAMA_BASE_URL`` 指向的局域网地址只对 ollama 生效。"""
        from fastapi import HTTPException

        from src.gateway.models import _validate_base_url

        monkeypatch.setenv("OLLAMA_BASE_URL", "http://10.0.0.5:11434")
        assert (
            _validate_base_url("http://10.0.0.5:11434", provider="ollama")
            == "http://10.0.0.5:11434"
        )
        for provider in ("openai", "deepseek", "anthropic"):
            with pytest.raises(HTTPException):
                _validate_base_url("http://10.0.0.5:11434", provider=provider)


class _FakeUpstreamResponse:
    def __init__(self, status_code: int, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or json.dumps(payload or {})

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class TestModelDiscovery:
    """「填 Key 后直接拉可用模型列表」：成功可点选，失败仍可手填。"""

    def _patch_upstream(self, monkeypatch, response):
        import src.ai.model_discovery as discovery

        captured: dict = {}

        def _fake_get(url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return response

        monkeypatch.setattr(discovery.requests, "get", _fake_get)
        # 目标校验由 TestBaseUrlTargetPolicy 单独覆盖；这里避免测试依赖外网 DNS。
        monkeypatch.setattr(discovery, "assert_safe_runtime_target", lambda *a, **k: None)
        return captured

    def test_openai_compatible_lists_models(self, client, admin_headers, monkeypatch):
        captured = self._patch_upstream(
            monkeypatch,
            _FakeUpstreamResponse(
                200, {"object": "list", "data": [{"id": "qwen2.5:3b"}, {"id": "llama3.1:8b"}]}
            ),
        )
        response = client.post(
            "/v1/models/discover",
            json={
                "provider": "openai",
                "base_url": "http://127.0.0.1:1234/v1",
                "api_key": "sk-local-123456",
            },
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["ok"] is True
        assert body["models"] == ["qwen2.5:3b", "llama3.1:8b"]
        assert captured["url"] == "http://127.0.0.1:1234/v1/models"
        assert captured["allow_redirects"] is False
        assert captured["headers"]["Authorization"] == "Bearer sk-local-123456"

    def test_ollama_uses_api_tags(self, client, admin_headers, monkeypatch):
        captured = self._patch_upstream(
            monkeypatch,
            _FakeUpstreamResponse(200, {"models": [{"name": "qwen2.5:3b"}, {"name": "llava:7b"}]}),
        )
        response = client.post(
            "/v1/models/discover",
            json={"provider": "ollama", "base_url": "http://127.0.0.1:11434"},
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        assert response.json()["models"] == ["qwen2.5:3b", "llava:7b"]
        assert captured["url"] == "http://127.0.0.1:11434/api/tags"

    def test_anthropic_preset_url_is_not_doubled(self, client, admin_headers, monkeypatch):
        """Anthropic 预设端点自带 ``/v1``：不能拼成 ``/v1/v1/models``。"""
        captured = self._patch_upstream(
            monkeypatch,
            _FakeUpstreamResponse(
                200, {"data": [{"id": "claude-3-5-sonnet-latest", "type": "model"}]}
            ),
        )
        body = client.post(
            "/v1/models/discover",
            json={
                "provider": "anthropic",
                "base_url": "https://api.anthropic.com/v1",
                "api_key": "sk-ant-123456",
            },
            headers=admin_headers,
        ).json()
        assert body["ok"] is True, body
        assert body["models"] == ["claude-3-5-sonnet-latest"]
        assert captured["url"] == "https://api.anthropic.com/v1/models"
        assert captured["headers"]["x-api-key"] == "sk-ant-123456"
        assert "anthropic-version" in captured["headers"]

    def test_gemini_strips_models_prefix(self, client, admin_headers, monkeypatch):
        self._patch_upstream(
            monkeypatch,
            _FakeUpstreamResponse(
                200,
                {
                    "models": [
                        {"name": "models/gemini-2.0-flash", "supportedGenerationMethods": ["generateContent"]},
                        {"name": "models/embedding-001", "supportedGenerationMethods": ["embedContent"]},
                    ]
                },
            ),
        )
        response = client.post(
            "/v1/models/discover",
            json={"provider": "google", "api_key": "gm-key-123456"},
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        assert response.json()["models"] == ["gemini-2.0-flash"]

    def test_invalid_key_is_actionable_and_still_manual(self, client, admin_headers, monkeypatch):
        self._patch_upstream(
            monkeypatch,
            _FakeUpstreamResponse(
                401, {"error": {"message": "Incorrect API key provided", "code": "invalid_api_key"}}
            ),
        )
        body = client.post(
            "/v1/models/discover",
            json={
                "provider": "openai",
                "base_url": "http://127.0.0.1:1234/v1",
                "api_key": "sk-bad-123456",
            },
            headers=admin_headers,
        ).json()
        assert body["ok"] is False
        assert body["reason"] == "invalid_key"
        assert body["manual"] is True
        assert "手填" in body["detail"]
        assert "Incorrect API key" not in body["detail"]

    def test_unsupported_listing_endpoint_still_allows_manual(self, client, admin_headers, monkeypatch):
        self._patch_upstream(monkeypatch, _FakeUpstreamResponse(404, {"error": "not found"}))
        body = client.post(
            "/v1/models/discover",
            json={
                "provider": "openai",
                "base_url": "http://127.0.0.1:1234/v1",
                "api_key": "sk-local-123456",
            },
            headers=admin_headers,
        ).json()
        assert body["ok"] is False
        assert body["reason"] == "unsupported"
        assert body["manual"] is True
        assert "手填" in body["detail"]

    def test_redirect_is_not_followed(self, client, admin_headers, monkeypatch):
        captured = self._patch_upstream(
            monkeypatch,
            _FakeUpstreamResponse(302, {"error": "moved"}, text="Found"),
        )
        body = client.post(
            "/v1/models/discover",
            json={
                "provider": "openai",
                "base_url": "http://127.0.0.1:1234/v1",
                "api_key": "sk-local-123456",
            },
            headers=admin_headers,
        ).json()
        assert body["ok"] is False
        assert body["reason"] == "redirect"
        assert captured["allow_redirects"] is False

    def test_unreachable_endpoint_is_reported(self, client, admin_headers, monkeypatch):
        import requests as requests_mod

        import src.ai.model_discovery as discovery

        def _boom(url, **kwargs):
            raise requests_mod.exceptions.ConnectionError("refused")

        monkeypatch.setattr(discovery.requests, "get", _boom)
        body = client.post(
            "/v1/models/discover",
            json={
                "provider": "openai",
                "base_url": "http://127.0.0.1:1234/v1",
                "api_key": "sk-local-123456",
            },
            headers=admin_headers,
        ).json()
        assert body["ok"] is False
        assert body["reason"] == "unreachable"
        assert "手填" in body["detail"]

    def test_private_target_is_rejected_before_request(self, client, admin_headers, monkeypatch):
        import src.ai.model_discovery as discovery

        called = {"n": 0}

        def _fake_get(url, **kwargs):
            called["n"] += 1
            return _FakeUpstreamResponse(200, {"data": [{"id": "x"}]})

        monkeypatch.setattr(discovery.requests, "get", _fake_get)
        response = client.post(
            "/v1/models/discover",
            json={
                "provider": "openai",
                "base_url": "http://10.0.0.5:11434/v1",
                "api_key": "sk-local-123456",
            },
            headers=admin_headers,
        )
        assert response.status_code == 400, response.text
        assert called["n"] == 0

    def test_requires_write_permission(self, client, reader_headers):
        response = client.post(
            "/v1/models/discover",
            json={"provider": "openai", "base_url": "http://127.0.0.1:1234/v1"},
            headers=reader_headers,
        )
        assert response.status_code == 403, response.text


