"""登录端点与凭据存储的测试。

这里最重要的一组是 :class:`TestTokenIsReal` —— 它证明登录签发出来的令牌
**确实被 Policy 端点接受**。否则"登录页能进"只说明前端没拦，不等于后端真的
认这个身份。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from src.gateway import auth
from src.gateway.auth import (
    AuthError,
    SecretStore,
    authenticate,
    describe_credential,
    hash_credential,
    verify_credential,
)
from src.gateway.main import get_app

PASSWORD = "correct horse battery staple"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def auth_env(monkeypatch, tmp_path):
    """Isolate both stores (identity + credential) and reset the singleton.

    The identity manager is a module-level singleton, so a temp identities file
    only takes effect if the singleton is rebuilt afterwards -- otherwise the
    test would silently assert against whatever store the import happened to
    pick up.
    """
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
    """Register a real human identity in the kernel **and** set a credential."""
    from src.kernels.identity import get_identity_manager

    identity = get_identity_manager().create_human_identity(principal, **kwargs)
    assert identity is not None, f"{principal} should not already exist"
    assert auth.resolve_secret_store().set_credential(principal, password)
    return identity


# ---------------------------------------------------------------------------
# credential hashing
# ---------------------------------------------------------------------------


class TestCredentialHashing:
    def test_record_never_contains_the_plaintext(self):
        record = hash_credential(PASSWORD)
        assert PASSWORD not in json.dumps(record)
        assert set(record) == {"algo", "iterations", "salt", "hash"}

    def test_correct_secret_verifies(self):
        assert verify_credential(PASSWORD, hash_credential(PASSWORD)) is True

    def test_wrong_secret_is_refused(self):
        assert verify_credential("wrong", hash_credential(PASSWORD)) is False

    def test_same_secret_gets_a_fresh_salt_each_time(self):
        first = hash_credential(PASSWORD)
        second = hash_credential(PASSWORD)
        assert first["salt"] != second["salt"]
        assert first["hash"] != second["hash"]

    @pytest.mark.parametrize(
        "record",
        [
            None,
            {},
            "not-a-dict",
            {"algo": "md5", "iterations": 1, "salt": "AA==", "hash": "AA=="},
            {"algo": auth.PBKDF2_ALGO, "iterations": 0, "salt": "AA==", "hash": "AA=="},
            {"algo": auth.PBKDF2_ALGO, "iterations": 1000, "salt": "", "hash": ""},
            {"algo": auth.PBKDF2_ALGO, "iterations": 1000, "salt": "!!!", "hash": "!!!"},
        ],
    )
    def test_malformed_records_are_refused_not_crashed(self, record):
        assert verify_credential(PASSWORD, record) is False

    def test_empty_secret_cannot_be_hashed(self):
        with pytest.raises(ValueError):
            hash_credential("")

    def test_describe_leaks_neither_salt_nor_hash(self):
        described = describe_credential(hash_credential(PASSWORD))
        assert described["configured"] is True
        assert "salt" not in described and "hash" not in described
        assert "salt" not in json.dumps(described)
        assert json.dumps(described).count(auth.PBKDF2_ALGO) == 1

    def test_describe_handles_a_missing_credential(self):
        assert describe_credential(None) == {"configured": False}
        assert describe_credential({}) == {"configured": False}


# ---------------------------------------------------------------------------
# secret store
# ---------------------------------------------------------------------------


class TestSecretStore:
    def test_missing_file_is_unconfigured_not_an_error(self, tmp_path):
        store = SecretStore(str(tmp_path / "absent.json"))
        assert store.available() is False
        assert store.load() == {}
        assert store.credential_for("anyone") is None

    def test_empty_path_is_unconfigured(self):
        assert SecretStore("").available() is False

    def test_round_trip(self, tmp_path):
        store = SecretStore(str(tmp_path / "auth.json"))
        assert store.set_credential("xin.hongda", PASSWORD) is True
        assert store.available() is True
        assert verify_credential(PASSWORD, store.credential_for("xin.hongda")) is True
        assert verify_credential("nope", store.credential_for("xin.hongda")) is False

    def test_remove(self, tmp_path):
        store = SecretStore(str(tmp_path / "auth.json"))
        store.set_credential("a.b", PASSWORD)
        assert store.remove_credential("a.b") is True
        assert store.credential_for("a.b") is None
        assert store.remove_credential("a.b") is False

    def test_write_is_atomic_no_tmp_left_behind(self, tmp_path):
        path = tmp_path / "auth.json"
        SecretStore(str(path)).set_credential("a.b", PASSWORD)
        assert path.exists()
        assert list(tmp_path.glob("*.tmp")) == []

    def test_set_credential_refuses_empty_inputs(self, tmp_path):
        store = SecretStore(str(tmp_path / "auth.json"))
        assert store.set_credential("", PASSWORD) is False
        assert store.set_credential("a.b", "") is False

    def test_bare_mapping_is_accepted(self, tmp_path):
        # Operators hand-edit these files; a bare {principal: record} map is a
        # natural thing to write and must not silently mean "nobody".
        path = tmp_path / "auth.json"
        path.write_text(
            json.dumps({"a.b": hash_credential(PASSWORD)}), encoding="utf-8"
        )
        store = SecretStore(str(path))
        assert verify_credential(PASSWORD, store.credential_for("a.b")) is True

    def test_canonical_document_shape_round_trips(self, tmp_path):
        path = tmp_path / "auth.json"
        SecretStore(str(path)).set_credential("a.b", PASSWORD)
        document = json.loads(path.read_text(encoding="utf-8"))
        assert set(document) == {"version", "credentials"}
        assert "a.b" in document["credentials"]

    def test_non_object_document_is_refused(self, tmp_path):
        path = tmp_path / "auth.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")
        assert SecretStore(str(path)).load() == {}

    def test_unparsable_document_fails_closed(self, tmp_path):
        path = tmp_path / "auth.json"
        path.write_text("{not json", encoding="utf-8")
        assert SecretStore(str(path)).load() == {}


# ---------------------------------------------------------------------------
# authenticate() -- no HTTP involved
# ---------------------------------------------------------------------------


class TestAuthenticate:
    def test_unknown_principal_is_401_with_a_generic_message(self, auth_env):
        with pytest.raises(AuthError) as caught:
            authenticate("nobody.at.all", PASSWORD)
        assert caught.value.status_code == 401
        # Same wording as a wrong password: a distinct message would make this
        # endpoint an oracle for which principals exist.
        assert caught.value.detail == "凭据无效"

    def test_wrong_password_is_401(self, auth_env):
        _register_human("xin.hongda")
        with pytest.raises(AuthError) as caught:
            authenticate("xin.hongda", "wrong")
        assert caught.value.status_code == 401

    def test_non_human_identity_with_a_valid_credential_is_403(self, auth_env):
        # A service identity must never be able to hold sovereignty (OD-010),
        # even if somebody sets a password for it.
        from src.kernels.identity import IdentityScope, get_identity_manager

        get_identity_manager().create_identity("svc.batch", scope=IdentityScope.L4)
        auth.resolve_secret_store().set_credential("svc.batch", PASSWORD)
        with pytest.raises(AuthError) as caught:
            authenticate("svc.batch", PASSWORD)
        assert caught.value.status_code == 403

    def test_suspended_human_is_403(self, auth_env):
        from src.kernels.identity import IdentityStatus, get_identity_manager

        identity = _register_human("xin.hongda")
        get_identity_manager()._identities[identity.id].status = IdentityStatus.SUSPENDED
        with pytest.raises(AuthError) as caught:
            authenticate("xin.hongda", PASSWORD)
        assert caught.value.status_code == 403

    def test_successful_login_returns_a_usable_token(self, auth_env):
        _register_human("xin.hongda", display_name="张三")
        result = authenticate("xin.hongda", PASSWORD, client="mobile")

        assert result["principal"] == "xin.hongda"
        assert result["display_name"] == "张三"
        assert result["scope"] == "L0"
        assert result["client"] == "mobile"
        assert result["token_type"] == "bearer"
        assert result["expires_in"] > 0

        from src.security import get_jwt_handler

        payload = get_jwt_handler().validate_access_token(result["access_token"])
        assert payload.sub == "xin.hongda"
        assert payload.metadata.get("kind") == "human"
        assert payload.metadata.get("client") == "mobile"

    def test_blank_inputs_are_400(self, auth_env):
        for principal, secret in (("", PASSWORD), ("xin.hongda", ""), ("  ", PASSWORD)):
            with pytest.raises(AuthError) as caught:
                authenticate(principal, secret)
            assert caught.value.status_code == 400

    def test_unknown_client_class_falls_back_to_web(self, auth_env):
        _register_human("xin.hongda")
        result = authenticate("xin.hongda", PASSWORD, client="smart-fridge")
        assert result["client"] == "web"

    def test_principal_is_whitespace_trimmed(self, auth_env):
        _register_human("xin.hongda")
        assert authenticate("  xin.hongda  ", PASSWORD)["principal"] == "xin.hongda"


class TestThrottle:
    def test_repeated_failures_eventually_return_429(self, auth_env):
        _register_human("xin.hongda")
        for _ in range(auth.MAX_FAILURES):
            with pytest.raises(AuthError) as caught:
                authenticate("xin.hongda", "wrong")
            assert caught.value.status_code == 401
        with pytest.raises(AuthError) as caught:
            authenticate("xin.hongda", "wrong")
        assert caught.value.status_code == 429

    def test_a_successful_login_clears_the_counter(self, auth_env):
        _register_human("xin.hongda")
        for _ in range(auth.MAX_FAILURES - 1):
            with pytest.raises(AuthError):
                authenticate("xin.hongda", "wrong")
        authenticate("xin.hongda", PASSWORD)
        # Counter cleared: the next failure is a plain 401, not a 429.
        with pytest.raises(AuthError) as caught:
            authenticate("xin.hongda", "wrong")
        assert caught.value.status_code == 401

    def test_throttling_is_per_principal(self, auth_env):
        _register_human("a.one")
        _register_human("b.two")
        for _ in range(auth.MAX_FAILURES):
            with pytest.raises(AuthError):
                authenticate("a.one", "wrong")
        # b.two has its own counter and is unaffected.
        assert authenticate("b.two", PASSWORD)["principal"] == "b.two"


class TestTtl:
    def test_env_override_is_honoured(self, auth_env, monkeypatch):
        _register_human("xin.hongda")
        monkeypatch.setenv(auth.AUTH_TTL_ENV, "300")
        assert authenticate("xin.hongda", PASSWORD)["expires_in"] == 300

    def test_absurd_values_are_clamped(self, auth_env, monkeypatch):
        _register_human("xin.hongda")
        monkeypatch.setenv(auth.AUTH_TTL_ENV, "1")
        assert authenticate("xin.hongda", PASSWORD)["expires_in"] == auth.MIN_TTL_SECONDS
        monkeypatch.setenv(auth.AUTH_TTL_ENV, str(10**9))
        assert authenticate("xin.hongda", PASSWORD)["expires_in"] == auth.MAX_TTL_SECONDS

    def test_garbage_value_falls_back_to_default(self, auth_env, monkeypatch):
        _register_human("xin.hongda")
        monkeypatch.setenv(auth.AUTH_TTL_ENV, "not-a-number")
        assert authenticate("xin.hongda", PASSWORD)["expires_in"] == auth.DEFAULT_TTL_SECONDS


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------


class TestAuthEndpoints:
    def test_login_then_me(self, client, auth_env):
        _register_human("xin.hongda", display_name="张三")
        response = client.post(
            "/v1/auth/login",
            json={"principal": "xin.hongda", "secret": PASSWORD, "client": "desktop"},
        )
        assert response.status_code == 200, response.text
        token = response.json()["access_token"]

        me = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        body = me.json()
        assert body["principal"] == "xin.hongda"
        assert body["display_name"] == "张三"
        assert body["still_human"] is True
        assert body["client"] == "desktop"

    def test_login_failure_is_json_401(self, client, auth_env):
        _register_human("xin.hongda")
        response = client.post(
            "/v1/auth/login", json={"principal": "xin.hongda", "secret": "wrong"}
        )
        assert response.status_code == 401
        assert "application/json" in response.headers["content-type"]

    def test_me_without_a_token_is_401(self, client, auth_env):
        assert client.get("/v1/auth/me").status_code == 401

    def test_me_with_a_garbage_token_is_401(self, client, auth_env):
        response = client.get(
            "/v1/auth/me", headers={"Authorization": "Bearer not.a.jwt"}
        )
        assert response.status_code == 401

    def test_login_requires_both_fields(self, client, auth_env):
        assert client.post("/v1/auth/login", json={"principal": "x"}).status_code == 422
        assert client.post("/v1/auth/login", json={}).status_code == 422

    def test_logout_revokes_the_token(self, client, auth_env):
        _register_human("xin.hongda")
        token = client.post(
            "/v1/auth/login", json={"principal": "xin.hongda", "secret": PASSWORD}
        ).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        assert client.get("/v1/auth/me", headers=headers).status_code == 200
        out = client.post("/v1/auth/logout", headers=headers)
        assert out.status_code == 200 and out.json()["revoked"] is True
        assert client.get("/v1/auth/me", headers=headers).status_code == 401

    def test_logout_without_a_token_is_idempotent(self, client, auth_env):
        response = client.post("/v1/auth/logout")
        assert response.status_code == 200
        assert response.json()["logged_out"] is True


class TestAuthConfigEndpoint:
    def test_shape_and_honesty_when_unconfigured(self, client, auth_env):
        body = client.get("/v1/auth/config").json()
        assert body["auth_required"] is True
        assert body["methods"] == ["password"]
        # Not wired up -- reporting otherwise would make the login page lie.
        assert body["oauth"] == {"wechat": False, "sms": False}
        assert body["secret_store"]["configured"] is False
        assert body["login_eligible_humans"] == 0
        assert set(body["client_modes"]) == {"desktop", "web", "mobile"}

    def test_counts_eligible_humans(self, client, auth_env):
        _register_human("xin.hongda")
        body = client.get("/v1/auth/config").json()
        assert body["secret_store"]["configured"] is True
        assert body["registered_humans"] == 1
        assert body["login_eligible_humans"] == 1

    def test_a_human_without_a_credential_is_not_eligible(self, client, auth_env):
        from src.kernels.identity import get_identity_manager

        get_identity_manager().create_human_identity("no.password")
        body = client.get("/v1/auth/config").json()
        assert body["registered_humans"] == 1
        assert body["login_eligible_humans"] == 0

    def test_it_never_leaks_principals_hashes_or_salts(self, client, auth_env):
        _register_human("xin.hongda")
        raw = client.get("/v1/auth/config").text
        assert "xin.hongda" not in raw
        assert "salt" not in raw and "hash" not in raw
        assert auth.PBKDF2_ALGO not in raw


class TestTokenIsReal:
    """The login token must be accepted by the endpoints that guard sovereignty."""

    def test_the_token_unlocks_the_policy_endpoints(self, client, auth_env):
        _register_human("xin.hongda", display_name="张三")
        token = client.post(
            "/v1/auth/login", json={"principal": "xin.hongda", "secret": PASSWORD}
        ).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 401 without a token: the guard is genuinely in force.
        assert client.get("/v1/policy/approvals").status_code == 401

        response = client.get("/v1/policy/approvals", headers=headers)
        assert response.status_code == 200, response.text
        assert "enforcement" in response.json()

    def test_a_token_signed_for_a_non_human_still_fails_the_grant_check(
        self, client, auth_env
    ):
        # Defence in depth: the token only names a principal. Whether that
        # principal may actually hold sovereignty is decided by the identity
        # kernel at grant time, not by whoever minted the token.
        from src.security.jwt_handler import create_access_token

        token, _ = create_access_token(subject="svc.forged", scopes=["console"])
        response = client.post(
            "/v1/policy/approvals",
            headers={"Authorization": f"Bearer {token}"},
            json={"actions": [_enforced_action()], "reason": "should not work"},
        )
        assert response.status_code == 400, response.text
        detail = response.json()["detail"]
        # The refusal must name the PRINCIPAL. If it named the action instead,
        # this test would still pass while the real guard stayed open.
        assert "svc.forged" in detail
        assert _enforced_action() not in detail

    def test_a_real_human_does_obtain_a_grant_for_the_same_action(
        self, client, auth_env
    ):
        # Positive control for the test above: without it, "400" could just
        # mean the chosen action was not enforceable and the pair of tests
        # would prove nothing.
        _register_human("xin.hongda")
        token = client.post(
            "/v1/auth/login", json={"principal": "xin.hongda", "secret": PASSWORD}
        ).json()["access_token"]
        response = client.post(
            "/v1/policy/approvals",
            headers={"Authorization": f"Bearer {token}"},
            json={"actions": [_enforced_action()], "reason": "positive control"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["approved"] is True


def _enforced_action() -> str:
    """A real enforcement-gated action name, taken from the risk registry.

    Named rather than hardcoded so the test cannot silently drift onto an
    action that is no longer HIGH/CRITICAL (which would make it pass for the
    wrong reason).
    """
    from src.kernels._risk_classification import ENFORCED_TIERS, KERNEL_ACTION_RISK

    enforced = sorted(
        name for name, risk in KERNEL_ACTION_RISK.items() if risk.tier in ENFORCED_TIERS
    )
    assert enforced, "risk registry exposes no enforced action -- test premise broken"
    return enforced[0]
