"""``scripts/issue_console_token.py`` 签发的令牌声明必须反映身份记录。

背景（真实缺陷，非假设）：脚本头部一直打印 ``scope=identity.scope``，但旧实现
只传 ``subject``，于是签出的令牌 ``roles=[] / permissions=[] / metadata={}``。
后果是"提示一套、读取一套"：``/v1/auth/me`` 读不到 scope，``/v1/models`` 的
管理员闸门（admin 权限或 L0 主权）也读不到，本地唯一人类 ``liuhao``
（``permissions=[]`` + ``scope=L0``）用它做写操作会被 403。

这些用例锁住的是"脚本与登录路径同源"：声明直接取自身份记录，并断言该令牌
**真的能过** ``require_model_admin``，而不只是"看起来有 scope 字段"。
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "issue_console_token.py"


@pytest.fixture
def script(monkeypatch, tmp_path):
    """独立的身份库 + 固定的 JWT 密钥，避免读到本机真实状态。"""
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_FILE", str(tmp_path / "humans.json"))
    # 身份注册表的完整性密钥是强制的：没有它，行无法被认证。
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY", "test-integrity-key")
    monkeypatch.delenv("LIUHAO_HUMAN_IDENTITIES_BACKEND", raising=False)
    monkeypatch.delenv("LIUHAO_HUMAN_IDENTITIES_DB", raising=False)
    # >=32 字节，避免 pyjwt 的 InsecureKeyLengthWarning 噪音（与正确性无关）。
    monkeypatch.setenv("LIUHAO_JWT_SECRET", "unit-test-jwt-secret-0123456789ab")

    import src.kernels.identity as identity_module
    monkeypatch.setattr(identity_module, "_global_manager", None)

    import src.security.jwt_handler as jwt_module
    monkeypatch.setattr(jwt_module, "_default_handler", None)

    # 脚本在 CWD 写运行期文件（审计库），指到 tmp_path。
    monkeypatch.chdir(tmp_path)

    spec = importlib.util.spec_from_file_location("issue_console_token", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _register_human(*, permissions: set[str] | None = None):
    from src.kernels.identity import get_identity_manager

    ident = get_identity_manager().create_human_identity(
        "unit-human",
        permissions=set(permissions or set()),
        display_name="Unit Human",
    )
    assert ident is not None, "fresh store should accept the first human"
    return ident


def _mint(script, monkeypatch, capsys) -> tuple[str, object]:
    """跑一次脚本，从输出里取出令牌并验证签名。"""
    from src.security.jwt_handler import validate_token

    monkeypatch.setattr(sys, "argv", ["issue_console_token.py", "--principal", "unit-human"])
    assert script.main() == 0
    out = capsys.readouterr().out
    token = next((line.strip() for line in out.splitlines() if line.startswith("eyJ")), "")
    assert token, f"script printed no token:\n{out}"
    payload = validate_token(token)
    assert payload is not None, "token minted by the script must verify"
    return token, payload


def test_claims_mirror_the_identity_record(script, monkeypatch, capsys):
    ident = _register_human(permissions={"admin"})
    _, payload = _mint(script, monkeypatch, capsys)

    assert payload.sub == ident.id
    assert payload.scopes == ["console"]
    assert sorted(payload.roles) == ["admin"]
    assert sorted(payload.permissions) == ["admin"]
    assert payload.metadata.get("kind") == "human"
    assert payload.metadata.get("scope") == ident.scope.value


def test_l0_human_without_permissions_passes_the_admin_gate(script, monkeypatch, capsys):
    """真实形态：``permissions=[]`` + ``scope=L0`` —— 旧令牌在这里会 403。"""
    ident = _register_human(permissions=set())
    _, payload = _mint(script, monkeypatch, capsys)

    assert payload.permissions == []
    assert payload.metadata.get("scope") == ident.scope.value == "L0"

    from src.gateway.models import require_model_admin

    # 不抛出 = 通过；旧实现 metadata={} 会让这一步抛 403。
    assert require_model_admin(payload) is payload
