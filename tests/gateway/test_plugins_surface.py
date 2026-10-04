"""P1 诚实 Apps 面 —— /v1/plugins 列表 + /v1/plugins/{id}/activate 真实激活。

为什么这个测试存在
------------------
内核审计把 "load_plugin / activate_plugin" 列为 Top-3 #2 产品缺口：此前
``load_plugin`` 标注 UNIMPLEMENTED，``activate_plugin`` 因找不到 PluginInterface
永远失败，驾驶舱里也没有任何页面渲染"插件"。本测试证明这个面是真的：

- 列表 / 激活 走真实 bearer 令牌闸门（未带令牌 → 401）；
- 系统启动注册的 builtin 插件（clean id，无冒号）能被真实加载并标记 active；
- 激活后再次列表，该插件 status == active / active == True；
- 未知 plugin_id 激活 → 404（诚实拒绝，不静默成功）；
- 加载器失败时 info.error 携带真实原因（不假装成功）。

隔离约定
--------
插件注册表单例 + 注册表落盘路径经 monkeypatch 指向临时目录，绝不会碰真实数据；
鉴权走真实 bearer 令牌（与网关 validator 同一签发/校验路径），不是 stub。
"""

from __future__ import annotations

import os

os.environ.setdefault("LIUHAO_WORKSPACE_ROOT", os.environ.get("LIUHAO_WORKSPACE_ROOT", ""))

import pytest
from fastapi.testclient import TestClient

from src.gateway.main import get_app


BUILTIN_ID = "builtin.example_capability"


def _auth_headers() -> dict:
    """真实 bearer 令牌（网关 validator 同一签发路径）。"""
    from src.security import get_jwt_handler

    token, _payload = get_jwt_handler().create_token(subject="test-human")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """每用例独立临时插件注册表；重定向单例落盘路径。

    关键：把模块级单例 ``_global_plugin_registry`` 直接指向一个临时目录支撑的
    注册表。这样无论谁调用 ``get_plugin_registry()`` —— HTTP 端点、启动时的
    ``register_builtin_plugins``、还是各内核的自注册 —— 都拿到同一份临时注册表，
    落盘路径也指向临时目录，绝不污染仓库里的 ``./plugins/registry_index.json``。
    """
    import src.kernels.plugin as _plugin_mod

    _tmp_reg = _plugin_mod.PluginRegistry(registry_path=str(tmp_path / "plugins"))
    _tmp_reg.initialize()
    monkeypatch.setattr(_plugin_mod, "_global_plugin_registry", _tmp_reg)

    # 重定向其他业务面依赖的环境变量（与 projects 测试一致）。
    monkeypatch.setenv("LIUHAO_WORKSPACE_ROOT", str(tmp_path / "ws"))
    monkeypatch.setenv("LIUHAO_ALERTS_STORE_PATH", str(tmp_path / "alerts.json"))
    monkeypatch.setenv("AUDIT_DB_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setenv("HTTP_PROXY", "")
    monkeypatch.setenv("HTTPS_PROXY", "")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    yield


@pytest.fixture
def client():
    app = get_app()
    with TestClient(app) as c:
        yield c, _auth_headers()


def test_list_requires_auth(client):
    c, _h = client
    r = c.get("/v1/plugins")
    assert r.status_code == 401, r.text


def test_unknown_activate_404(client):
    c, h = client
    r = c.post("/v1/plugins/does-not-exist/activate", headers=h)
    assert r.status_code == 404, r.text


def test_builtin_listed_and_activatable(client):
    c, h = client
    # 启动后 builtin 插件应已注册并在列表中可见。
    r = c.get("/v1/plugins", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] >= 1, body
    by_id = {p["plugin_id"]: p for p in body["plugins"]}
    assert BUILTIN_ID in by_id, by_id
    # 初始未激活。
    assert by_id[BUILTIN_ID]["active"] is False
    assert by_id[BUILTIN_ID]["status"] != "active"

    # 激活：真实加载 → active。
    r2 = c.post(f"/v1/plugins/{BUILTIN_ID}/activate", headers=h)
    assert r2.status_code == 200, r2.text
    b2 = r2.json()
    assert b2["plugin_id"] == BUILTIN_ID
    assert b2["active"] is True, b2
    assert b2["status"] == "active", b2
    assert b2["error"] is None, b2

    # 再次列表：该插件现在 active。
    r3 = c.get("/v1/plugins", headers=h)
    assert r3.status_code == 200, r3.text
    by_id3 = {p["plugin_id"]: p for p in r3.json()["plugins"]}
    assert by_id3[BUILTIN_ID]["active"] is True, by_id3[BUILTIN_ID]


def test_loader_failure_recorded(client):
    """若插件 module_path 指向不存在的模块，激活应失败且 error 真实。"""
    c, h = client
    import src.kernels.plugin as _plugin_mod

    reg = _plugin_mod.get_plugin_registry()
    reg.register_plugin(
        name="broken",
        version="1.0.0",
        kernel_type="builtin",
        capabilities=[],
        scope="L1",
        metadata={"module_path": "src.plugins.builtin.does_not_exist"},
        plugin_id="builtin.broken",
        module_path="src.plugins.builtin.does_not_exist",
    )
    r = c.post("/v1/plugins/builtin.broken/activate", headers=h)
    # 后端诚实返回 200 但 status=failed + error 真实（激活并未成功）。
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["status"] == "failed", b
    assert b["error"], b
    assert b["active"] is False, b
