"""设置类写操作的权限边界：成员只读，写只归根账号。

产品决定（2026-11-08）：

* ``PUT /v1/settings/logs``、``POST /v1/settings/logs/prune``、
  ``PUT /v1/network/research`` 只允许根账号；
* ``GET /v1/settings/logs``、``GET /v1/network/research`` 成员可读，
  但响应里不得出现日志目录等绝对路径。

这里用真实 HTTP 走三种角色（未登录 / 成员 / 根账号），既钉住状态码，
也钉住错误码可读（前端要能直接展示），最后用"新建 app"模拟重启，
确认根账号保存的保留策略真的落盘、重启后仍然生效。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.gateway.main import get_app

_ABS_PATH = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]")


@pytest.fixture()
def _isolate(tmp_path, monkeypatch):
    """身份库/凭据/日志/联网偏好都落到临时目录，绝不碰真实数据。"""
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_BACKEND", "file")
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_FILE", str(tmp_path / "identities.json"))
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY", "settings-root-test-key")
    monkeypatch.setenv("LIUHAO_AUTH_SECRETS_FILE", str(tmp_path / "auth_secrets.json"))
    monkeypatch.setenv("LIUHAO_ACCOUNT_SETTINGS_FILE", str(tmp_path / "account_settings.json"))
    monkeypatch.setenv("LIUHAO_ALERTS_STORE_PATH", str(tmp_path / "alerts.json"))
    monkeypatch.setenv("LIUHAO_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv(
        "LIUHAO_LOG_RETENTION_SETTINGS_FILE", str(tmp_path / "log_retention_settings.json")
    )
    monkeypatch.setenv("LIUHAO_PRIVACY_SETTINGS_FILE", str(tmp_path / "privacy_settings.json"))
    monkeypatch.delenv("LIUHAO_WEB_RESEARCH", raising=False)
    monkeypatch.delenv("LIUHAO_LOG_RETENTION_DAYS", raising=False)
    monkeypatch.delenv("LIUHAO_LOG_RETENTION_BYTES", raising=False)
    yield


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _login(client: TestClient, principal: str, secret: str) -> dict:
    response = client.post("/v1/auth/login", json={"principal": principal, "secret": secret})
    assert response.status_code == 200, response.text
    return _bearer(response.json()["access_token"])


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _assert_no_absolute_path(body) -> None:
    for text in _strings(body):
        assert not _ABS_PATH.search(text), f"响应里出现绝对路径：{text}"


def _assert_readable_denial(response) -> None:
    assert response.status_code == 403, response.text
    body = response.json()
    # 顶层 code 是驾驶舱直接读的字段；detail 里也保留一份，便于非驾驶舱客户端。
    assert body.get("code") == "SETTINGS_ROOT_ONLY", body
    assert body.get("message"), body
    assert body.get("detail") == body["message"], body
    assert "只能查看" in body["message"], body


def _root_and_member(client: TestClient) -> tuple[dict, dict]:
    registered = client.post(
        "/v1/accounts/register",
        json={"principal": "root1", "password": "rootpass123"},
    )
    assert registered.status_code == 200, registered.text
    root = _login(client, "root1", "rootpass123")

    created = client.post("/v1/accounts", json={"principal": "member1"}, headers=root)
    assert created.status_code == 200, created.text
    activated = client.post(
        "/v1/accounts/activate",
        json={
            "principal": "member1",
            "code": created.json()["activation_code"],
            "password": "memberpass123",
        },
    )
    assert activated.status_code == 200, activated.text
    member = _login(client, "member1", "memberpass123")
    return root, member


def test_unauthenticated_reads_are_rejected(_isolate):
    with TestClient(get_app()) as client:
        for path in ("/v1/settings/logs", "/v1/network/research"):
            response = client.get(path)
            assert response.status_code == 401, (path, response.text)


def test_member_reads_but_cannot_write(_isolate):
    with TestClient(get_app()) as client:
        _root, member = _root_and_member(client)

        logs = client.get("/v1/settings/logs", headers=member)
        assert logs.status_code == 200, logs.text
        assert "log_dir" not in logs.json(), logs.json()
        _assert_no_absolute_path(logs.json())

        research = client.get("/v1/network/research", headers=member)
        assert research.status_code == 200, research.text
        _assert_no_absolute_path(research.json())

        for method, path, kwargs in (
            ("put", "/v1/settings/logs", {"json": {"retention_days": 3, "max_total_bytes": 1024}}),
            ("post", "/v1/settings/logs/prune", {}),
            ("put", "/v1/network/research", {"json": {"enabled": True}}),
        ):
            response = getattr(client, method)(path, headers=member, **kwargs)
            _assert_readable_denial(response)

        # 被拒绝的写入不能改掉当前策略。
        assert client.get("/v1/settings/logs", headers=member).json()["retention_days"] != 3
        assert client.get("/v1/network/research", headers=member).json()["stored_enabled"] is False


def test_member_about_has_no_absolute_paths(_isolate):
    with TestClient(get_app()) as client:
        root, member = _root_and_member(client)

        member_view = client.get("/v1/about", headers=member)
        assert member_view.status_code == 200, member_view.text
        body = member_view.json()
        _assert_no_absolute_path(body)
        assert body["app_dir"] == "<程序目录>"
        assert body["data_dir"] == "<数据目录>"

        root_view = client.get("/v1/about", headers=root)
        assert root_view.status_code == 200, root_view.text
        root_body = root_view.json()
        # 根账号必须看到真实的绝对程序目录（成员侧是占位符，上方已断言）。
        # 盘符正则只认 Windows 路径；POSIX 上改用 pathlib 的绝对性判据，语义不变。
        assert Path(root_body["app_dir"]).is_absolute(), root_body


def test_member_file_listing_has_no_absolute_root(_isolate):
    with TestClient(get_app()) as client:
        _root, member = _root_and_member(client)

        response = client.get("/v1/files", headers=member)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["root"] == "<账号空间>", body
        _assert_no_absolute_path(body)


def test_root_can_write_and_settings_survive_restart(_isolate):
    with TestClient(get_app()) as client:
        root, member = _root_and_member(client)

        saved = client.put(
            "/v1/settings/logs",
            headers=root,
            json={"retention_days": 7, "max_total_bytes": 12 * 1024 * 1024},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["retention_days"] == 7
        assert saved.json()["max_total_bytes"] == 12 * 1024 * 1024

        pruned = client.post("/v1/settings/logs/prune", headers=root)
        assert pruned.status_code == 200, pruned.text

        toggled = client.put(
            "/v1/network/research", headers=root, json={"enabled": True}
        )
        assert toggled.status_code == 200, toggled.text
        assert toggled.json()["enabled"] is True

    # 新建 app = 模拟重启：保存值从磁盘读取，不依赖进程内状态。
    # 令牌签名密钥同样落盘，根账号/成员的令牌在重启后继续有效。
    with TestClient(get_app()) as client:
        after_restart = client.get("/v1/settings/logs", headers=root)
        assert after_restart.status_code == 200, after_restart.text
        assert after_restart.json()["retention_days"] == 7
        assert after_restart.json()["max_total_bytes"] == 12 * 1024 * 1024

        # 成员重启后仍是只读，且读到的策略就是根账号保存的值。
        member_view = client.get("/v1/settings/logs", headers=member)
        assert member_view.status_code == 200, member_view.text
        assert member_view.json()["retention_days"] == 7
        _assert_no_absolute_path(member_view.json())
