"""签名密钥持久化（env 绑定）的回归测试。

背景：``JWTHandler`` 原先在 ``__init__`` 里现生成密钥，于是一个部署上：

* **重启即全端登出** —— 旧令牌全部失效，用户看不到任何原因；
* **多 worker/replica 直接坏掉** —— 登录在 A 进程签发、下一个请求落到 B 进程
  校验，新登录立刻 401。表现为「登录成功但控制台用不了」。

两条都不是崩溃，是静默的可用性缺陷。修法是把签名密钥固定到环境变量里，
让每个进程用同一把。本文件把契约钉死，覆盖：

1. 没配 env 时**保持原行为**（逐进程密钥）并**出 WARNING**（不静默）；
2. ``LIUHAO_JWT_SECRET`` 配了 ⇒ 跨「重启」的令牌仍有效（同一密钥可复现）；
3. 令牌**确实**由 env 密钥决定（换密钥 ⇒ 旧令牌被拒，证明不是碰巧）；
4. 非对称 PEM 对（``LIUHAO_JWT_PRIVATE_KEY``/``_PUBLIC_KEY``）可用；
5. 自相矛盾的配置**响亮失败**（对称密钥配 RS 算法 / 只给一把 PEM）。
"""

from __future__ import annotations

import logging

import pytest

import src.security.jwt_handler as jwt_mod
from src.security import get_jwt_handler

#: 一把足够长的固定密钥：测试要的是「同一把密钥可复现」，不是强度。
SECRET_A = "liuhao-test-secret-A-0123456789-abcdefghijklmnop"
SECRET_B = "liuhao-test-secret-B-0123456789-abcdefghijklmnop"


@pytest.fixture
def fresh_handler(monkeypatch):
    """每个用例拿到干净的默认 handler 单例（模块级全局，必须重置）。"""
    monkeypatch.setattr(jwt_mod, "_default_handler", None)
    names = (
        jwt_mod.JWT_SECRET_ENV,
        *jwt_mod.JWT_SECRET_ENV_ALIASES,
        jwt_mod.JWT_PRIVATE_KEY_ENV,
        jwt_mod.JWT_PUBLIC_KEY_ENV,
        jwt_mod.JWT_ALGORITHM_ENV,
    )
    for name in names:
        monkeypatch.delenv(name, raising=False)
    yield


def _rsa_pem_pair() -> tuple[str, str]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


# ---------------------------------------------------------------------------
# 1. 未配置 ⇒ 原行为 + 响亮提示
# ---------------------------------------------------------------------------


def test_no_env_keeps_ephemeral_keys_and_warns(fresh_handler, caplog):
    with caplog.at_level(logging.WARNING, logger=jwt_mod.__name__):
        handler = get_jwt_handler()

    # 原行为不变：没配 env 就走逐进程密钥（默认 RS256 现生成）。
    assert handler.algorithm == jwt_mod.JWTHandler.DEFAULT_ALGORITHM
    # 但不许静默：运维必须能在日志里看到「令牌不会跨重启」。
    assert any(
        jwt_mod.JWT_SECRET_ENV in record.getMessage()
        and record.levelno >= logging.WARNING
        for record in caplog.records
    ), "缺少 env 时必须给出 WARNING，否则这个缺陷会一直是静默的"


def test_no_env_warns_only_once(fresh_handler, caplog):
    with caplog.at_level(logging.WARNING, logger=jwt_mod.__name__):
        get_jwt_handler()
        get_jwt_handler()
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1, "单例只应提示一次，不该每次取用都刷日志"


# ---------------------------------------------------------------------------
# 2/3. env 密钥 ⇒ 可复现、跨进程可用
# ---------------------------------------------------------------------------


def test_env_secret_survives_restart(fresh_handler, monkeypatch):
    monkeypatch.setenv(jwt_mod.JWT_SECRET_ENV, SECRET_A)

    # 「进程 1」：签发。
    token, _ = get_jwt_handler().create_token(subject="boss")

    # 「重启」：丢掉单例，就是新进程拿到同一把 env 密钥。
    monkeypatch.setattr(jwt_mod, "_default_handler", None)

    # 「进程 2」：校验通过 —— 这正是修复前会失败的那一步。
    payload = get_jwt_handler().validate_access_token(token)
    assert payload.sub == "boss"
    assert get_jwt_handler().algorithm == "HS256"


def test_token_is_keyed_by_env_secret_not_random(fresh_handler, monkeypatch):
    monkeypatch.setenv(jwt_mod.JWT_SECRET_ENV, SECRET_A)
    token_a, _ = get_jwt_handler().create_token(subject="boss")

    # 换一把密钥并重启 ⇒ 旧令牌必须被拒（证明令牌真的由 env 决定）。
    monkeypatch.setenv(jwt_mod.JWT_SECRET_ENV, SECRET_B)
    monkeypatch.setattr(jwt_mod, "_default_handler", None)

    import jwt as _jwt

    with pytest.raises(_jwt.InvalidTokenError):
        get_jwt_handler().validate_access_token(token_a)


def test_token_from_another_process_is_accepted(fresh_handler, monkeypatch):
    """模拟多 worker：另一个进程用同一 env 密钥签的令牌，本进程必须接受。"""
    monkeypatch.setenv(jwt_mod.JWT_SECRET_ENV, SECRET_A)

    # 另一个进程（直接构造，不经过单例）用同一把密钥签发。
    other_process = jwt_mod.JWTHandler(algorithm="HS256", secret_key=SECRET_A)
    token, _ = other_process.create_token(subject="boss")

    payload = get_jwt_handler().validate_access_token(token)
    assert payload.sub == "boss"


def test_singleton_is_stable(fresh_handler, monkeypatch):
    monkeypatch.setenv(jwt_mod.JWT_SECRET_ENV, SECRET_A)
    assert get_jwt_handler() is get_jwt_handler()


# ---------------------------------------------------------------------------
# 4. 非对称 PEM 对
# ---------------------------------------------------------------------------


def test_env_pem_pair_roundtrip(fresh_handler, monkeypatch):
    private_pem, public_pem = _rsa_pem_pair()
    monkeypatch.setenv(jwt_mod.JWT_PRIVATE_KEY_ENV, private_pem)
    monkeypatch.setenv(jwt_mod.JWT_PUBLIC_KEY_ENV, public_pem)

    token, _ = get_jwt_handler().create_token(subject="boss")
    monkeypatch.setattr(jwt_mod, "_default_handler", None)
    payload = get_jwt_handler().validate_access_token(token)

    assert payload.sub == "boss"
    assert get_jwt_handler().algorithm == "RS256"


# ---------------------------------------------------------------------------
# 5. 配置自相矛盾 ⇒ 响亮失败（而非「登录一直失败」）
# ---------------------------------------------------------------------------


def test_symmetric_secret_with_rs_algorithm_is_rejected(fresh_handler, monkeypatch):
    monkeypatch.setenv(jwt_mod.JWT_SECRET_ENV, SECRET_A)
    monkeypatch.setenv(jwt_mod.JWT_ALGORITHM_ENV, "RS256")
    with pytest.raises(RuntimeError):
        get_jwt_handler()


def test_lone_private_key_is_rejected(fresh_handler, monkeypatch):
    private_pem, _ = _rsa_pem_pair()
    monkeypatch.setenv(jwt_mod.JWT_PRIVATE_KEY_ENV, private_pem)
    with pytest.raises(RuntimeError):
        get_jwt_handler()


# ---------------------------------------------------------------------------
# 6. 兼容既有部署的密钥变量名（否则 compose 里的声明一直是死配置）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("alias", jwt_mod.JWT_SECRET_ENV_ALIASES)
def test_legacy_secret_env_names_are_honoured(fresh_handler, monkeypatch, alias):
    monkeypatch.setenv(alias, SECRET_A)
    token, _ = get_jwt_handler().create_token(subject="boss")
    monkeypatch.setattr(jwt_mod, "_default_handler", None)
    assert get_jwt_handler().validate_access_token(token).sub == "boss"


def test_canonical_name_wins_over_alias(fresh_handler, monkeypatch):
    monkeypatch.setenv(jwt_mod.JWT_SECRET_ENV, SECRET_A)
    monkeypatch.setenv("JWT_SECRET", SECRET_B)  # 更不显眼的别名不该被选中
    token, _ = get_jwt_handler().create_token(subject="boss")

    # 一个只用 canonical 密钥的「另一个进程」必须接受该令牌；若代码实际选了
    # SECRET_B（别名），签名对不上，这里会抛 InvalidTokenError。
    canonical_only = jwt_mod.JWTHandler(algorithm="HS256", secret_key=SECRET_A)
    assert canonical_only.validate_access_token(token).sub == "boss"


def test_placeholder_secret_is_refused(fresh_handler, monkeypatch, caplog):
    """占位值绝不能当签名密钥 —— 已知密钥比随机密钥更不安全。"""
    monkeypatch.setenv(jwt_mod.JWT_SECRET_ENV, "replace-me")
    with caplog.at_level(logging.WARNING, logger=jwt_mod.__name__):
        handler = get_jwt_handler()
    # 未被采用 ⇒ 退回逐进程密钥（原行为），不是拿 "replace-me" 去签。
    assert handler.algorithm == jwt_mod.JWTHandler.DEFAULT_ALGORITHM
    assert any("placeholder" in r.getMessage() for r in caplog.records)
