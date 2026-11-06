"""HTTP contract tests for ``src.upgrade.api``.

A tiny standalone FastAPI app mounts the router; no gateway is started.
Authentication is real: the tokens are minted by the same JWT handler the
gateway validates with.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.upgrade import LocalDirectorySource, UpgradeConfig, UpgradeManager
from src.upgrade.api import get_upgrade_manager, router


def _setup_app(root: Path, version: str = "0.1.0") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "liuhao-ai-os"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "src" / "gateway").mkdir(exist_ok=True)
    (root / "src" / "gateway" / "main.py").write_text(
        "from fastapi import FastAPI\n\n"
        "app = FastAPI()\n\n"
        "@app.get('/health')\n"
        "def health():\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )
    (root / "data").mkdir(exist_ok=True)
    conn = sqlite3.connect(root / "data" / "app.db")
    conn.execute("CREATE TABLE t(v TEXT)")
    conn.execute("INSERT INTO t VALUES ('keep')")
    conn.commit()
    conn.close()
    (root / "data" / "state.json").write_text('{"ok": true}', encoding="utf-8")
    return root


def _release(
    release_dir: Path,
    *,
    version: str = "0.2.0",
    files: dict[str, str] | None = None,
    sha_override: str | None = None,
) -> dict:
    release_dir.mkdir(parents=True, exist_ok=True)
    files = files or {
        "pyproject.toml": f'[project]\nname = "liuhao-ai-os"\nversion = "{version}"\n',
        "src/app.py": "VALUE = 2\n",
    }
    artifact = f"liuhao-{version}.zip"
    zip_path = release_dir / artifact
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    release = {
        "version": version,
        "artifact": artifact,
        "sha256": sha_override or hashlib.sha256(zip_path.read_bytes()).hexdigest(),
        "size": zip_path.stat().st_size,
    }
    manifest = {"schema": "liuhao.release-manifest/1", "releases": [release]}
    (release_dir / "release-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return release


def _manager(app_root: Path, release_dir: Path | None) -> UpgradeManager:
    source = LocalDirectorySource(release_dir) if release_dir else None
    return UpgradeManager(UpgradeConfig(app_root=app_root, source=source))


def _headers(scope: str = "L0", *, roles: list[str] | None = None, kind: str = "human"):
    from src.security import get_jwt_handler

    token, _ = get_jwt_handler().create_token(
        subject="upgrade-human",
        metadata={"kind": kind, "scope": scope},
        roles=roles or [],
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client(tmp_path: Path):
    app_root = _setup_app(tmp_path / "app")
    release_dir = tmp_path / "releases"
    _release(release_dir, version="0.2.0")
    manager = _manager(app_root, release_dir)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_upgrade_manager] = lambda: manager
    try:
        yield TestClient(app), manager, app_root, release_dir
    finally:
        manager.close()


# ---------------------------------------------------------------------------
# permission / confirmation
# ---------------------------------------------------------------------------
def test_manager_from_env_reads_the_documented_variables(
    tmp_path: Path, monkeypatch,
) -> None:
    """The API builds its manager from env; this is the only production path."""
    from src.upgrade.update_check import build_manager_from_env

    app_root = _setup_app(tmp_path / "app")
    release_dir = tmp_path / "releases"
    _release(release_dir, version="0.2.0")
    monkeypatch.setenv("LIUHAO_APP_ROOT", str(app_root))
    monkeypatch.setenv("LIUHAO_UPGRADE_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("LIUHAO_UPGRADE_BACKUP_ROOT", str(tmp_path / "backups"))
    monkeypatch.setenv("LIUHAO_UPGRADE_RELEASE_DIR", str(release_dir))
    monkeypatch.delenv("LIUHAO_DATA_DIR", raising=False)

    manager = build_manager_from_env()
    try:
        assert manager.config.app_root == app_root.resolve()
        # 没有安装树标志 → 便携/开发规则：<app-root>/data
        assert manager.config.data_dir == (app_root / "data").resolve()
        assert manager.config.source is not None
        assert manager.current_version()[0] == "0.1.0"
    finally:
        manager.close()


def test_all_endpoints_require_a_bearer_token(client) -> None:
    test_client, *_ = client
    for method, path, body in (
        ("get", "/v1/upgrade/status", None),
        ("post", "/v1/upgrade/check", {}),
        ("post", "/v1/upgrade/apply", {}),
        ("post", "/v1/upgrade/rollback", {}),
    ):
        kwargs = {"json": body} if method == "post" else {}
        response = getattr(test_client, method)(path, **kwargs)
        assert response.status_code == 401, (path, response.text)


def test_all_endpoints_reject_low_privilege_human(client) -> None:
    test_client, *_ = client
    headers = _headers(scope="L1")
    for method, path, body in (
        ("get", "/v1/upgrade/status", None),
        ("post", "/v1/upgrade/check", {}),
        ("post", "/v1/upgrade/apply", {"confirm": True, "version": "0.2.0"}),
        ("post", "/v1/upgrade/rollback", {"confirm": True}),
    ):
        kwargs = {"json": body} if method == "post" else {}
        response = getattr(test_client, method)(path, headers=headers, **kwargs)
        assert response.status_code == 403, (path, response.text)


def test_l7_sovereignty_and_admin_role_both_work(client) -> None:
    test_client, *_ = client
    assert (
        test_client.get("/v1/upgrade/status", headers=_headers(scope="L7")).status_code
        == 200
    )
    assert (
        test_client.get(
            "/v1/upgrade/status",
            headers=_headers(scope="L1", roles=["admin"]),
        ).status_code
        == 200
    )
    service = test_client.get(
        "/v1/upgrade/status",
        headers=_headers(scope="L0", kind="service"),
    )
    assert service.status_code == 403


def test_apply_and_rollback_require_explicit_confirmation(client) -> None:
    test_client, manager, *_ = client
    headers = _headers()
    no_confirm = test_client.post(
        "/v1/upgrade/apply",
        json={"version": "0.2.0"},
        headers=headers,
    )
    assert no_confirm.status_code == 400
    assert no_confirm.json()["detail"]["code"] == "confirmation_required"
    assert "二次确认" in no_confirm.json()["detail"]["message"]

    no_rollback_confirm = test_client.post(
        "/v1/upgrade/rollback", json={}, headers=headers
    )
    assert no_rollback_confirm.status_code == 400
    assert no_rollback_confirm.json()["detail"]["code"] == "confirmation_required"

    # A confirmation alone is not enough: the target version must be explicit.
    no_version = test_client.post(
        "/v1/upgrade/apply", json={"confirm": True}, headers=headers
    )
    assert no_version.status_code == 400
    assert no_version.json()["detail"]["code"] == "version_required"

    bad_version = test_client.post(
        "/v1/upgrade/apply", json={"confirm": True, "version": "abc"}, headers=headers
    )
    assert bad_version.status_code == 400
    assert bad_version.json()["detail"]["code"] == "invalid_version"


# ---------------------------------------------------------------------------
# source configuration
# ---------------------------------------------------------------------------
def test_status_and_actions_are_explicit_when_no_source_configured(tmp_path: Path) -> None:
    app_root = _setup_app(tmp_path / "app")
    manager = _manager(app_root, None)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_upgrade_manager] = lambda: manager
    headers = _headers()
    try:
        with TestClient(app) as test_client:
            status = test_client.get("/v1/upgrade/status", headers=headers)
            assert status.status_code == 200
            payload = status.json()
            assert payload["source_configured"] is False
            assert "未配置更新源" in payload["message"]
            assert payload["update_available"] is False

            check = test_client.post("/v1/upgrade/check", headers=headers)
            assert check.status_code == 409
            assert check.json()["detail"]["code"] == "source_not_configured"
            assert "未配置更新源" in check.json()["detail"]["message"]

            apply = test_client.post(
                "/v1/upgrade/apply",
                json={"confirm": True, "version": "0.2.0"},
                headers=headers,
            )
            assert apply.status_code == 409
            assert apply.json()["detail"]["code"] == "source_not_configured"
    finally:
        manager.close()


# ---------------------------------------------------------------------------
# check / apply / rollback
# ---------------------------------------------------------------------------
def test_check_reports_available_update_without_downloading(client) -> None:
    test_client, manager, app_root, _ = client
    headers = _headers()
    response = test_client.post("/v1/upgrade/check", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "synchronous"
    assert body["check"]["status"] == "update_available"
    assert body["check"]["latest_version"] == "0.2.0"
    assert body["status"]["state"] == "available"
    assert body["status"]["update_available"] is True
    # Check must not install anything.
    assert manager.current_version()[0] == "0.1.0"
    assert (app_root / "src" / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_apply_rejects_version_mismatch(client) -> None:
    test_client, *_ = client
    response = test_client.post(
        "/v1/upgrade/apply",
        json={"confirm": True, "version": "9.9.9"},
        headers=_headers(),
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "version_mismatch"
    assert "当前可用版本是 0.2.0" in detail["message"]


def test_apply_success_and_status_then_rollback(client) -> None:
    test_client, manager, app_root, _ = client
    headers = _headers()
    applied = test_client.post(
        "/v1/upgrade/apply",
        json={"confirm": True, "version": "0.2.0"},
        headers=headers,
    )
    assert applied.status_code == 200, applied.text
    body = applied.json()
    assert body["mode"] == "synchronous"
    assert body["warning"]
    assert body["apply"]["to_version"] == "0.2.0"
    assert body["status"]["state"] == "applied"
    assert manager.current_version()[0] == "0.2.0"

    rolled = test_client.post(
        "/v1/upgrade/rollback",
        json={"confirm": True},
        headers=headers,
    )
    assert rolled.status_code == 200, rolled.text
    rollback_body = rolled.json()
    assert rollback_body["mode"] == "synchronous"
    assert rollback_body["rollback"]["to_version"] == "0.1.0"
    assert rollback_body["status"]["state"] == "rolled_back"
    assert manager.current_version()[0] == "0.1.0"

    final_status = test_client.get("/v1/upgrade/status", headers=headers).json()
    assert final_status["state"] == "rolled_back"
    assert final_status["last_rollback"]["status"] == "rolled_back"


def test_failure_is_queryable_from_status_with_category_and_readable_text(
    client,
) -> None:
    test_client, manager, app_root, release_dir = client
    # Make the manifest's SHA256 wrong: check still sees an update, download
    # fails, and the manager persists the failure for /status.
    _release(release_dir, version="0.2.0", sha_override="0" * 64)
    response = test_client.post(
        "/v1/upgrade/apply",
        json={"confirm": True, "version": "0.2.0"},
        headers=_headers(),
    )
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] != "unknown"
    assert detail["message"]

    status = test_client.get("/v1/upgrade/status", headers=_headers()).json()
    assert status["state"] == "failed"
    assert status["error"] is not None
    assert status["error"]["category"] in {
        "checksum_mismatch",
        "signature_invalid",
    }
    assert status["error"]["message"]
    assert manager.current_version()[0] == "0.1.0"


def test_gateway_mounts_upgrade_router_behind_the_human_gate() -> None:
    """The production app really exposes the four endpoints, human-gated."""
    from src.gateway.main import get_app

    app = get_app()
    paths = app.openapi().get("paths", {})
    for path in (
        "/v1/upgrade/status",
        "/v1/upgrade/check",
        "/v1/upgrade/apply",
        "/v1/upgrade/rollback",
    ):
        assert path in paths, f"{path} 未出现在 /openapi.json"

    client = TestClient(app)
    # 闸门先于 manager 构建执行：未登录/非管理员都不会碰真实升级状态文件。
    assert client.get("/v1/upgrade/status").status_code == 401
    assert (
        client.get(
            "/v1/upgrade/status", headers=_headers(scope="L1", kind="human")
        ).status_code
        == 403
    )


def test_rollback_without_source_is_local_but_explicitly_labelled(tmp_path: Path) -> None:
    """Rollback must work offline, yet never imply that a source was used."""
    app_root = _setup_app(tmp_path / "app")
    release_dir = tmp_path / "releases"
    _release(release_dir, version="0.2.0")
    app = FastAPI()
    app.include_router(router)
    headers = _headers()
    online = _manager(app_root, release_dir)
    app.dependency_overrides[get_upgrade_manager] = lambda: online
    try:
        with TestClient(app) as test_client:
            applied = test_client.post(
                "/v1/upgrade/apply",
                json={"confirm": True, "version": "0.2.0"},
                headers=headers,
            )
            assert applied.status_code == 200, applied.text
    finally:
        online.close()

    offline = _manager(app_root, None)
    app.dependency_overrides[get_upgrade_manager] = lambda: offline
    try:
        with TestClient(app) as test_client:
            rolled = test_client.post(
                "/v1/upgrade/rollback", json={"confirm": True}, headers=headers
            )
            assert rolled.status_code == 200, rolled.text
            body = rolled.json()
            assert body["source_configured"] is False
            assert "未配置更新源" in body["notice"]
            assert body["rollback"]["to_version"] == "0.1.0"
            assert body["status"]["current_version"] == "0.1.0"
    finally:
        offline.close()
