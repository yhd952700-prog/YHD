"""登录页下发的说明文案门禁。

``/v1/auth/config`` 的 ``message`` 会原样显示在用户第一次打开的登录页上，
登录失败的错误文案也会经 ``ApiError`` 落到同一个界面。这些字符串里不能出现
策略代号、脚本名、路径、"主体"、"人类身份"等内部术语。

词表**复用前端门禁**（``apps/console/console/src/lib/uiCopyGate.test.ts`` 的
``BANNED_UI_PATTERNS``）：后端文案和前端门禁读同一份名单，才不会各自漂移。
这里直接解析该 TS 常量，而不是在 Python 里再抄一份。

用例走真实 HTTP（TestClient）取数，覆盖 ``message`` 的四条分支，并把登录
失败路径上的错误文案一并纳入门禁。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FRONTEND_GATE = _REPO_ROOT / "apps/console/console/src/lib/uiCopyGate.test.ts"

#: 用户首次上屏不应出现的身份库/内部措辞。前端门禁未收的词，按本轮要求补上。
_EXTRA_FORBIDDEN = ("个人类身份", "人类身份", "凭据")

_CJK = re.compile(r"[\u4e00-\u9fff]")

_ENTRY = re.compile(
    r"pattern:\s*/(.+?)/([gimsuy]*)\s*,\s*why:\s*'([^']*)'",
    re.DOTALL,
)


def _frontend_banned_patterns() -> list[tuple[re.Pattern[str], str]]:
    """把前端门禁词表读成 Python 正则，确保两边口径一致。"""
    source = _FRONTEND_GATE.read_text(encoding="utf-8")
    block = source.split("export const BANNED_UI_PATTERNS", 1)[1].split("\n]", 1)[0]
    entries = _ENTRY.findall(block)
    assert len(entries) >= 30, (
        f"前端门禁词表结构发生变化（{_FRONTEND_GATE}），请同步本测试的解析规则"
    )
    compiled: list[tuple[re.Pattern[str], str]] = []
    for pattern, flags, why in entries:
        re_flags = 0
        if "i" in flags:
            re_flags |= re.IGNORECASE
        if "m" in flags:
            re_flags |= re.MULTILINE
        if "s" in flags:
            re_flags |= re.DOTALL
        compiled.append((re.compile(pattern, re_flags), why))
    return compiled


BANNED = _frontend_banned_patterns()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """空实例：所有存储都指向临时目录，进程里不存在任何账号。"""
    base = tmp_path
    values = {
        "LIUHAO_HUMAN_IDENTITIES_BACKEND": "file",
        "LIUHAO_HUMAN_IDENTITIES_FILE": str(base / "identities.json"),
        "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY": "login-copy-test-key",
        "LIUHAO_AUTH_SECRETS_FILE": str(base / "auth_secrets.json"),
        "LIUHAO_ACCOUNT_SETTINGS_FILE": str(base / "account_settings.json"),
        "LIUHAO_ALERTS_STORE_PATH": str(base / "alerts.json"),
        "AUDIT_DB_PATH": str(base / "audit_store.db"),
        "AUDIT_DB_BACKUP_PATH": str(base / "audit_store.db.backup"),
        "LIUHAO_WORKSPACE_ROOT": str(base / "workspace"),
        "LIUHAO_MODEL_CONFIG_DB": str(base / "model_config.sqlite3"),
        "LIUHAO_MODEL_CONFIG_KEY_FILE": str(base / "model_config_key.bin"),
        "LIUHAO_JWT_REVOCATION_DB": str(base / "jwt_revocation.db"),
        "LIUHAO_DATA_DIR": str(base / "data"),
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    yield values


def _visible_strings(node) -> list[str]:
    """响应里所有含中文的字符串值 —— 用户真正会读到的部分。

    机器字段（``client_modes`` / ``state`` / 文件名）都是 ASCII 标识符，
    按要求保留但不渲染，因此不进入这条门禁。
    """
    found: list[str] = []
    if isinstance(node, str):
        if _CJK.search(node):
            found.append(node)
    elif isinstance(node, dict):
        for value in node.values():
            found.extend(_visible_strings(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_visible_strings(value))
    return found


def _assert_neutral(text: str) -> None:
    for pattern, why in BANNED:
        assert not pattern.search(text), f"登录页文案命中内部术语（{why}）：{text!r}"
    for word in _EXTRA_FORBIDDEN:
        assert word not in text, f"登录页文案含内部措辞「{word}」：{text!r}"


def _config(client: TestClient) -> dict:
    response = client.get("/v1/auth/config")
    assert response.status_code == 200, response.text
    payload = response.json()
    visible = _visible_strings(payload)
    assert visible, "配置响应里应当存在给用户看的说明文案"
    for text in visible:
        _assert_neutral(text)
    return payload


def test_empty_instance_message_is_neutral(env):
    """空实例：用户第一次打开就看到的文案，全部是中性中文。"""
    from src.gateway.main import get_app

    with TestClient(get_app()) as client:
        payload = _config(client)

    message = payload["message"]
    assert "账号" in message and "密码" in message
    # 机器字段照旧保留（界面据此决定展示哪个入口，但它自己不渲染）。
    assert payload["secret_store"]["configured"] is False
    assert payload["initialization"]["state"] == "pending"
    assert payload["registration_open"] is True


def test_incomplete_initialization_message_is_neutral(env):
    """根已创建、密码没落盘：补全初始化的提示同样不得出现内部术语。"""
    from src.gateway.main import get_app
    from src.kernels.identity import get_identity_manager

    get_identity_manager().register_root_account("alice")

    with TestClient(get_app()) as client:
        payload = _config(client)

    assert payload["initialization"]["state"] == "incomplete"
    assert "初始化未完成" in payload["message"]


def test_message_when_no_account_can_log_in_is_neutral(env):
    """有凭据但没有可登录的账号：文案不得再去数"人类身份"。"""
    from src.gateway import auth
    from src.gateway.main import get_app
    from src.kernels.identity import IdentityScope, get_identity_manager

    get_identity_manager().create_identity("svc.batch", scope=IdentityScope.L4)
    assert auth.resolve_secret_store().set_credential("svc.batch", "svcpass123")

    with TestClient(get_app()) as client:
        payload = _config(client)

    assert payload["login_eligible_humans"] == 0


def test_message_when_an_account_can_log_in_is_neutral(env):
    from src.gateway import auth
    from src.gateway.main import get_app
    from src.kernels.identity import get_identity_manager

    get_identity_manager().create_human_identity("alice")
    assert auth.resolve_secret_store().set_credential("alice", "alicepass123")

    with TestClient(get_app()) as client:
        payload = _config(client)

    assert payload["login_eligible_humans"] == 1
    assert "已登记" in payload["message"]


def test_login_and_logout_error_copy_is_neutral(env):
    """登录失败路径上的错误文案也直接显示在登录页，一并纳入门禁。"""
    from src.gateway import auth
    from src.gateway.main import get_app
    from src.kernels.identity import IdentityScope, get_identity_manager

    manager = get_identity_manager()
    manager.create_identity("svc.batch", scope=IdentityScope.L4)
    auth.resolve_secret_store().set_credential("svc.batch", "svcpass123")

    raised = []
    with pytest.raises(auth.AuthError) as blank:
        auth.authenticate("", "x")
    raised.append(blank.value)
    with pytest.raises(auth.AuthError) as unknown:
        auth.authenticate("nobody.at.all", "x")
    raised.append(unknown.value)
    with pytest.raises(auth.AuthError) as not_human:
        auth.authenticate("svc.batch", "svcpass123")
    raised.append(not_human.value)
    with pytest.raises(auth.AuthError) as token_for:
        auth.issue_token_for("svc.batch")
    raised.append(token_for.value)

    for error in raised:
        detail = error.detail
        text = detail.get("message") if isinstance(detail, dict) else detail
        _assert_neutral(str(text))

    with TestClient(get_app()) as client:
        logout = client.post(
            "/v1/auth/logout", headers={"Authorization": "Bearer not-a-real-token"}
        )
        assert logout.status_code == 401, logout.text
        _assert_neutral(str(logout.json()["detail"]))
