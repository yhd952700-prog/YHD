"""工作台 Bot 分组后端存档（``/v1/workbench/groups``）专属回归。

补缺口轮（2026-11-17）新增：此前分组后端（src/workbench/store.py +
src/gateway/workbench.py）已接线进网关，但仓库里没有任何专属测试，
控制台主题也只把分组存在浏览器 localStorage。本文件覆盖：

1. 分组 CRUD（建/改名/折叠/排序/删）与删组后 Bot 回到未分组；
2. Bot 归属移动（PATCH bots 的 group_id）；
3. 鉴权：未登录一律 401；
4. 账号隔离：子账号看不见、改不动根账号的分组（404 不泄露存在性）；
5. localStorage 快照迁移端点 ``POST /v1/workbench/migrate`` 幂等并入。

全部用独立临时数据目录 + 真实 FastAPI 应用，不触碰真实用户数据。
"""

from __future__ import annotations

from typing import Dict

import pytest
from fastapi.testclient import TestClient

PASSWORD = "<redacted>"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    base = tmp_path
    values = {
        "LIUHAO_HUMAN_IDENTITIES_BACKEND": "file",
        "LIUHAO_HUMAN_IDENTITIES_FILE": str(base / "identities.json"),
        "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY": "workbench-groups-test-key",
        "LIUHAO_AUTH_SECRETS_FILE": str(base / "auth_secrets.json"),
        "LIUHAO_WORKBENCH_DB": str(base / "workbench.db"),
        "LIUHAO_DATA_DIR": str(base / "data"),
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)

    import src.kernels.identity as identity_module
    from src.workbench.store import _reset_workbench_store

    monkeypatch.setattr(identity_module, "_global_manager", None)
    _reset_workbench_store()
    yield values
    _reset_workbench_store()


@pytest.fixture()
def client(env):
    from src.gateway.main import get_app

    with TestClient(get_app()) as test_client:
        yield test_client


def _bearer(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _login(client: TestClient, principal: str) -> Dict[str, str]:
    response = client.post(
        "/v1/auth/login", json={"principal": principal, "secret": PASSWORD}
    )
    assert response.status_code == 200, response.text
    return _bearer(response.json()["access_token"])


def _register_root(client: TestClient, principal: str = "boss") -> Dict[str, str]:
    response = client.post(
        "/v1/accounts/register",
        json={"principal": principal, "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    return _login(client, principal)


def _create_child(client: TestClient, headers: Dict[str, str], principal: str):
    created = client.post("/v1/accounts", json={"principal": principal}, headers=headers)
    assert created.status_code == 200, created.text
    code = created.json()["activation_code"]
    activated = client.post(
        "/v1/accounts/activate",
        json={"principal": principal, "code": code, "password": PASSWORD},
    )
    assert activated.status_code == 200, activated.text
    return _login(client, principal)


# --------------------------------------------------------------------------- #
# 鉴权
# --------------------------------------------------------------------------- #


def test_groups_require_login(client):
    assert client.get("/v1/workbench/groups").status_code == 401
    assert client.post("/v1/workbench/groups", json={"name": "x"}).status_code == 401
    assert client.post("/v1/workbench/migrate", json={}).status_code == 401


# --------------------------------------------------------------------------- #
# CRUD + Bot 归属
# --------------------------------------------------------------------------- #


def test_group_crud_and_bot_assignment(client):
    headers = _register_root(client)

    created = client.post(
        "/v1/workbench/groups", json={"name": "工作分组"}, headers=headers
    )
    assert created.status_code == 201, created.text
    group = created.json()["group"]
    assert group["name"] == "工作分组"
    assert group["folded"] is False
    group_id = group["id"]

    listing = client.get("/v1/workbench/groups", headers=headers)
    assert listing.status_code == 200
    assert [g["id"] for g in listing.json()["groups"]] == [group_id]

    patched = client.patch(
        f"/v1/workbench/groups/{group_id}",
        json={"name": "项目分组", "folded": True},
        headers=headers,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["group"]["name"] == "项目分组"
    assert patched.json()["group"]["folded"] is True

    # Bot 建出来后归入分组
    bot = client.post(
        "/v1/workbench/bots", json={"name": "管账 Bot"}, headers=headers
    )
    assert bot.status_code == 201, bot.text
    bot_id = bot.json()["bot"]["id"]
    moved = client.patch(
        f"/v1/workbench/bots/{bot_id}",
        json={"group_id": group_id},
        headers=headers,
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["bot"]["group_id"] == group_id

    # 删组：Bot 不删，回到未分组
    deleted = client.delete(f"/v1/workbench/groups/{group_id}", headers=headers)
    assert deleted.status_code == 200, deleted.text
    bots = client.get("/v1/workbench/bots", headers=headers).json()["bots"]
    assert [b["id"] for b in bots] == [bot_id]
    assert bots[0]["group_id"] == ""
    assert client.get("/v1/workbench/groups", headers=headers).json()["count"] == 0


def test_group_reorder(client):
    headers = _register_root(client)
    ids = []
    for name in ("甲组", "乙组"):
        resp = client.post("/v1/workbench/groups", json={"name": name}, headers=headers)
        ids.append(resp.json()["group"]["id"])
    resp = client.post(
        "/v1/workbench/groups/reorder", json={"ids": list(reversed(ids))}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    got = client.get("/v1/workbench/groups", headers=headers).json()["groups"]
    assert [g["id"] for g in got] == list(reversed(ids))


# --------------------------------------------------------------------------- #
# 账号隔离
# --------------------------------------------------------------------------- #


def test_groups_are_isolated_between_accounts(client):
    root_headers = _register_root(client)
    created = client.post(
        "/v1/workbench/groups", json={"name": "老板的组"}, headers=root_headers
    )
    group_id = created.json()["group"]["id"]

    child_headers = _create_child(client, root_headers, "clerk1")

    # 子账号列表里没有根账号的分组
    child_listing = client.get("/v1/workbench/groups", headers=child_headers)
    assert child_listing.status_code == 200
    assert child_listing.json()["groups"] == []

    # 子账号改/删根账号的分组：一律 404，不泄露存在性
    assert (
        client.patch(
            f"/v1/workbench/groups/{group_id}",
            json={"name": "改名"},
            headers=child_headers,
        ).status_code
        == 404
    )
    assert (
        client.delete(f"/v1/workbench/groups/{group_id}", headers=child_headers).status_code
        == 404
    )
    # 根账号的分组完好无损
    root_listing = client.get("/v1/workbench/groups", headers=root_headers).json()
    assert root_listing["groups"][0]["name"] == "老板的组"


# --------------------------------------------------------------------------- #
# localStorage 快照迁移
# --------------------------------------------------------------------------- #


def test_migrate_snapshot_is_idempotent(client):
    headers = _register_root(client)
    snapshot = {
        "groups": [
            {"id": "g-legacy-1", "name": "旧分组", "folded": True},
        ]
    }
    first = client.post("/v1/workbench/migrate", json=snapshot, headers=headers)
    assert first.status_code == 200, first.text
    second = client.post("/v1/workbench/migrate", json=snapshot, headers=headers)
    assert second.status_code == 200, second.text

    groups = client.get("/v1/workbench/groups", headers=headers).json()["groups"]
    assert len(groups) == 1
    assert groups[0]["id"] == "g-legacy-1"
    assert groups[0]["name"] == "旧分组"
    assert groups[0]["folded"] is True
