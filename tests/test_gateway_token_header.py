"""私有令牌头（``X-Liuhao-Token``）的回归测试。

托管平台的前置网关（``CloudStudio Gateway``）会在请求到达本应用前**改写**
``Authorization`` 成它自己的 JWT —— 实测：不带该头、带 17 字符假令牌、带 824
字符真令牌，应用收到的都是同一个 379 字符的 HS256 令牌。后果是**登录能成功、
但之后每个带鉴权的请求都 401**。

修法是改用网关不认识的私有头：后端优先读它、``Authorization`` 兜底。本文件把
这条契约钉死，覆盖三件事：

1. ``extract_bearer_token`` 的取值顺序（纯函数，不涉 HTTP）；
2. 网关改写场景下私有头**确实**能穿过（否则这套修复等于没做）；
3. 没有私有头时 ``Authorization`` 仍然有效（本机 / Docker 直连部署行为不变）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.gateway import auth
from src.gateway.main import get_app
from src.gateway.policy import (
    STANDARD_TOKEN_HEADER,
    TOKEN_HEADER,
    extract_bearer_token,
)

PASSWORD = "correct horse battery staple"

#: 一个**格式合法但签名不可验证**的令牌。在托管环境下，应用收到的 ``Authorization``
#: 正是这个样子：网关自己的 JWT，本应用既非签发方也无密钥。用固定串而非临时签发，
#: 是为了保证它**永远不会**被本进程接受 —— 否则测试会因为"碰巧签对了"而失去意义。
FOREIGN_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiJjbG91ZHN0dWRpby1nYXRld2F5IiwiaXNzIjoiZ2F0ZXdheSJ9"
    ".Q2xvdWRTdHVkaW9HYXRld2F5U2lnbmF0dXJlTm90RnJvbVRoaXNBcHA"
)


# ---------------------------------------------------------------------------
# fixtures（与 test_gateway_auth.py 同构；身份管理器是模块级单例，必须重建）
# ---------------------------------------------------------------------------


@pytest.fixture
def auth_env(monkeypatch, tmp_path):
    humans_file = tmp_path / "human_identities.json"
    secrets_file = tmp_path / "auth_secrets.json"
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_FILE", str(humans_file))
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_BACKEND", "file")
    monkeypatch.setenv("LIUHAO_AUTH_SECRETS_FILE", str(secrets_file))
    monkeypatch.delenv("LIUHAO_AUTH_TTL_SECONDS", raising=False)

    import src.kernels.identity as identity_module

    monkeypatch.setattr(identity_module, "_global_manager", None)
    auth.reset_throttle()
    try:
        yield humans_file, secrets_file
    finally:
        monkeypatch.setattr(identity_module, "_global_manager", None)
        auth.reset_throttle()


@pytest.fixture
def client(auth_env):
    with TestClient(get_app()) as test_client:
        yield test_client


def _register_human(principal: str, password: str = PASSWORD, **kwargs):
    from src.kernels.identity import get_identity_manager

    identity = get_identity_manager().create_human_identity(principal, **kwargs)
    assert identity is not None, f"{principal} should not already exist"
    assert auth.resolve_secret_store().set_credential(principal, password)
    return identity


def _login(client: TestClient, principal: str = "xin.hongda", **extra) -> str:
    response = client.post(
        "/v1/auth/login",
        json={"principal": principal, "secret": PASSWORD, **extra},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


#: 一个登录后才可用的受保护端点，用作"闸门是否放行"的探针。
GATED = "/v1/dashboard/roster"


# ---------------------------------------------------------------------------
# 1. extract_bearer_token —— 取值顺序是纯函数，先把它钉死
# ---------------------------------------------------------------------------


class TestExtractBearerToken:
    def test_private_header_wins_over_authorization(self):
        assert extract_bearer_token("Bearer real", "Bearer rewritten") == "real"

    def test_falls_back_to_authorization_when_private_absent(self):
        assert extract_bearer_token(None, "Bearer only-auth") == "only-auth"
        assert extract_bearer_token("", "Bearer only-auth") == "only-auth"

    def test_returns_none_when_nothing_usable(self):
        assert extract_bearer_token(None, None) is None
        assert extract_bearer_token("", "") is None
        assert extract_bearer_token("Basic abc", "Token xyz") is None

    def test_scheme_is_case_insensitive(self):
        assert extract_bearer_token("bearer lower") == "lower"
        assert extract_bearer_token("BEARER upper") == "upper"

    def test_token_is_whitespace_trimmed(self):
        assert extract_bearer_token("Bearer   spaced  ") == "spaced"

    def test_a_bare_scheme_without_a_token_is_not_returned(self):
        assert extract_bearer_token("Bearer   ") is None
        assert extract_bearer_token("Bearer   ", None) is None

    def test_a_malformed_first_value_does_not_block_the_second(self):
        # 私有头不是 Bearer 时，仍应继续看 Authorization —— 否则一个无关的头
        # 就能把有效令牌挡在门外。
        assert extract_bearer_token("Basic nope", "Bearer good") == "good"

    def test_the_header_names_are_what_the_console_sends(self):
        # 前端 ``auth.TOKEN_HEADER`` 必须与后端一致；这条串是跨语言的契约。
        assert TOKEN_HEADER == "X-Liuhao-Token"
        assert STANDARD_TOKEN_HEADER == "Authorization"


# ---------------------------------------------------------------------------
# 2. 网关改写场景 —— 本文件存在的理由
# ---------------------------------------------------------------------------


class TestPrivateHeaderSurvivesTheRewrite:
    def test_authorization_alone_cannot_authenticate_a_foreign_token(self, client, auth_env):
        """先把"漏洞症状"复现出来：被改写的 Authorization 一律 401。

        没有这一半，"私有头能用"就无法证明是私有头的功劳。
        """
        _register_human("xin.hongda")
        response = client.get(GATED, headers={STANDARD_TOKEN_HEADER: f"Bearer {FOREIGN_TOKEN}"})
        assert response.status_code == 401

    def test_the_private_header_carries_the_real_token_through(self, client, auth_env):
        """核心回归：Authorization 被改写成网关令牌时，私有头仍能放行。"""
        _register_human("xin.hongda")
        token = _login(client)

        response = client.get(
            GATED,
            headers={
                # 网关改写后的样子（本应用无法验证）
                STANDARD_TOKEN_HEADER: f"Bearer {FOREIGN_TOKEN}",
                # 客户端真正发送的令牌（网关不认识这个头，原样穿过）
                TOKEN_HEADER: f"Bearer {token}",
            },
        )
        assert response.status_code == 200, response.text

    def test_private_header_takes_precedence_even_when_its_token_is_bad(self, client, auth_env):
        """位置优先，失败即关：私有头里的坏令牌不会被 Authorization 里的好令牌救回。

        这是刻意的 fail-closed。若改成"逐个试到成功为止"，等于让基础设施塞进来的
        任意令牌也能获得一次鉴权机会。
        """
        _register_human("xin.hongda")
        token = _login(client)
        response = client.get(
            GATED,
            headers={
                TOKEN_HEADER: f"Bearer {FOREIGN_TOKEN}",
                STANDARD_TOKEN_HEADER: f"Bearer {token}",
            },
        )
        assert response.status_code == 401

    def test_authorization_still_works_without_the_private_header(self, client, auth_env):
        """向后兼容：本机 / Docker 直连部署没有网关改写，行为必须不变。"""
        _register_human("xin.hongda")
        token = _login(client)
        response = client.get(GATED, headers={STANDARD_TOKEN_HEADER: f"Bearer {token}"})
        assert response.status_code == 200, response.text

    def test_both_headers_are_accepted_by_the_gate(self, client, auth_env):
        """前端双写两个头：两条通道都要能放行。"""
        _register_human("xin.hongda")
        token = _login(client)
        both = {TOKEN_HEADER: f"Bearer {token}", STANDARD_TOKEN_HEADER: f"Bearer {token}"}
        assert client.get(GATED, headers=both).status_code == 200


# ---------------------------------------------------------------------------
# 3. 其余读令牌的入口必须用同一条取值规则
# ---------------------------------------------------------------------------


class TestOtherTokenReadersUseTheSameRule:
    def test_logout_revokes_the_token_from_the_private_header(self, client, auth_env):
        """登出必须撤销**调用者**的令牌，而不是网关的。

        若 /auth/logout 只读 Authorization，它会去撤销一个不存在的令牌、报告
        "已登出"，而真正的会话还活着 —— 这是最危险的失败形态：看起来安全。
        """
        _register_human("xin.hongda")
        token = _login(client)
        private = {TOKEN_HEADER: f"Bearer {token}"}

        assert client.get("/v1/auth/me", headers=private).status_code == 200
        out = client.post("/v1/auth/logout", headers=private)
        assert out.status_code == 200 and out.json()["revoked"] is True
        # 真正的令牌确实失效了。
        assert client.get("/v1/auth/me", headers=private).status_code == 401

    def test_logout_revokes_the_token_from_authorization_too(self, client, auth_env):
        _register_human("xin.hongda")
        token = _login(client)
        std = {STANDARD_TOKEN_HEADER: f"Bearer {token}"}
        assert client.post("/v1/auth/logout", headers=std).json()["revoked"] is True
        assert client.get("/v1/auth/me", headers=std).status_code == 401

    def test_logout_without_any_token_is_still_idempotent(self, client, auth_env):
        response = client.post("/v1/auth/logout")
        assert response.status_code == 200 and response.json()["revoked"] is False


# ---------------------------------------------------------------------------
# 4. /v1/auth/config 的部署诊断：有用，但不得泄露任何秘密
# ---------------------------------------------------------------------------


class TestAuthConfigDiagnostics:
    def test_diagnostics_report_public_facts_only(self, client, auth_env):
        body = client.get("/v1/auth/config").json()
        diagnostics = body["diagnostics"]
        assert diagnostics.get("algorithm")
        # 自检必须回答"本进程能否接受本进程签发的令牌"。
        assert diagnostics.get("roundtrip") == "ok"

    def test_diagnostics_do_not_leak_server_filesystem_paths(self, client, auth_env):
        # /v1/auth/config 是公开端点；泄露 jwt 库的绝对路径就是一条无谓的信息
        # 披露。版本号 + 算法列表已足够定位"加载的是哪个库"。
        raw = client.get("/v1/auth/config").text
        assert "site-packages" not in raw
        assert "library_path" not in raw

    def test_request_probe_reflects_but_never_echoes_the_token(self, client, auth_env):
        _register_human("xin.hongda")
        token = _login(client)
        probe = client.get(
            "/v1/auth/config", headers={STANDARD_TOKEN_HEADER: f"Bearer {token}"}
        ).json()["request_probe"]

        assert probe["present"] is True
        assert probe["token_len"] == len(token)
        assert probe["token_segments"] == 3
        # 只描述长度与算法，绝不回显令牌本身。
        assert token not in client.get("/v1/auth/config").text

    def test_request_probe_is_honest_when_the_header_is_absent(self, client, auth_env):
        probe = client.get("/v1/auth/config").json()["request_probe"]
        assert probe["present"] is False
        assert "token_len" not in probe
