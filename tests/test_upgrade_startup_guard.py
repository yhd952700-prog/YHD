"""运行期启动门禁的回归测试。

覆盖用户要求的三条真实启动路径约束：
1. 数据 schema 高于本二进制 → **拒绝启动**（不是升级入口，不是状态查询）；
2. 等于/低于/缺失/损坏 → 放行（兼容优先，不误伤首启与正常升级）；
3. 显式放行开关（环境变量 / 启动器 ``--allow-data-downgrade``）生效。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.launch_installed as installed
from src.upgrade.dataversion import (
    DATA_SCHEMA_FILENAME,
    DATA_SCHEMA_VERSION,
    marker_path,
    stamp_data_schema,
)
from src.upgrade.startup_guard import (
    ALLOW_ENV,
    StartupDataBlocked,
    check_startup_data,
    enforce_startup_data,
)


def _data_dir(tmp_path: Path, *, schema_version: int | None = None) -> Path:
    directory = tmp_path / "data"
    directory.mkdir()
    if schema_version is not None:
        stamp_data_schema(
            directory, app_version="0.9.0", schema_version=schema_version
        )
    return directory


def test_newer_data_is_refused_with_actionable_message(tmp_path: Path) -> None:
    data_dir = _data_dir(tmp_path, schema_version=DATA_SCHEMA_VERSION + 1)

    gate = check_startup_data(data_dir, app_root=tmp_path)

    assert gate.ok is False
    assert gate.action == "blocked"
    assert gate.version == DATA_SCHEMA_VERSION + 1
    assert gate.written_by == "0.9.0"
    message = gate.message
    assert "[FAIL]" in message
    assert "更新版本" in message
    assert f"v{DATA_SCHEMA_VERSION + 1}" in message
    assert f"只支持 v{DATA_SCHEMA_VERSION}" in message
    # 三条可行动信息：备份、放行开关、升级程序路径。
    assert "备份" in message
    assert "--allow-data-downgrade" in message
    assert "LiuHao.bat" in message
    assert str(data_dir) in message


def test_same_version_is_allowed(tmp_path: Path) -> None:
    data_dir = _data_dir(tmp_path, schema_version=DATA_SCHEMA_VERSION)
    gate = check_startup_data(data_dir)
    assert gate.ok is True
    assert gate.action == "ok"
    assert gate.version == DATA_SCHEMA_VERSION


def test_older_version_is_allowed(tmp_path: Path) -> None:
    data_dir = _data_dir(tmp_path, schema_version=max(1, DATA_SCHEMA_VERSION - 1))
    gate = check_startup_data(data_dir)
    assert gate.ok is True
    assert gate.action == "ok"


def test_missing_marker_is_allowed_silently(tmp_path: Path) -> None:
    data_dir = _data_dir(tmp_path)
    gate = check_startup_data(data_dir)
    assert gate.ok is True
    assert gate.action == "missing"
    assert gate.message == ""


def test_corrupt_marker_is_allowed_with_readable_diagnostic(tmp_path: Path) -> None:
    data_dir = _data_dir(tmp_path)
    marker_path(data_dir).write_text("{not json", encoding="utf-8")

    gate = check_startup_data(data_dir)

    assert gate.ok is True
    assert gate.action == "unreadable"
    assert "兼容" in gate.message
    assert str(data_dir) in gate.message


@pytest.mark.parametrize(
    "raw",
    [
        json.dumps({"schema": "liuhao.data-schema/1"}),  # 缺 version
        json.dumps({"schema": "other", "version": 99}),  # schema 名不对
        json.dumps({"schema": "liuhao.data-schema/1", "version": "3"}),  # 类型不对
    ],
)
def test_incomplete_marker_is_allowed(tmp_path: Path, raw: str) -> None:
    data_dir = _data_dir(tmp_path)
    marker_path(data_dir).write_text(raw, encoding="utf-8")

    gate = check_startup_data(data_dir)

    assert gate.ok is True
    assert gate.action == "unreadable"


def test_explicit_allow_switch_lets_it_start(tmp_path: Path) -> None:
    data_dir = _data_dir(tmp_path, schema_version=DATA_SCHEMA_VERSION + 1)

    gate = check_startup_data(data_dir, allow_downgrade=True)

    assert gate.ok is True
    assert gate.action == "allowed"
    assert gate.version == DATA_SCHEMA_VERSION + 1
    assert "显式降级开关" in gate.message


def test_env_switch_lets_it_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = _data_dir(tmp_path, schema_version=DATA_SCHEMA_VERSION + 1)
    monkeypatch.setenv(ALLOW_ENV, "1")

    gate = check_startup_data(data_dir)

    assert gate.ok is True
    assert gate.action == "allowed"


def test_enforce_raises_for_newer_data(tmp_path: Path) -> None:
    data_dir = _data_dir(tmp_path, schema_version=DATA_SCHEMA_VERSION + 1)
    with pytest.raises(StartupDataBlocked) as exc:
        enforce_startup_data(data_dir)
    assert "--allow-data-downgrade" in exc.value.message


def test_launcher_refuses_before_writing_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """真实启动路径：安装版入口在写入任何运行数据之前拒绝。"""
    data_dir = _data_dir(tmp_path, schema_version=DATA_SCHEMA_VERSION + 1)
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(data_dir))
    monkeypatch.delenv(ALLOW_ENV, raising=False)

    with pytest.raises(StartupDataBlocked):
        installed.prepare_environment()

    # 拒绝发生在 _ensure_layout 之前：没有生成 config/、logs/ 等目录。
    assert not (data_dir / "config").exists()
    assert not (data_dir / "logs").exists()


def test_launcher_main_returns_actionable_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    data_dir = _data_dir(tmp_path, schema_version=DATA_SCHEMA_VERSION + 1)
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(data_dir))
    monkeypatch.delenv(ALLOW_ENV, raising=False)

    code = installed.main(["--skip-wizard"])

    captured = capsys.readouterr()
    assert code == 4
    assert "已拒绝启动" in captured.err
    assert "--allow-data-downgrade" in captured.err
    assert "已停止启动" in captured.err


def test_gateway_lifespan_refuses_to_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """网关生命周期：lifespan 第一个动作就拒绝，而不是先初始化再报错。"""
    from fastapi.testclient import TestClient

    from src.gateway.main import get_app

    data_dir = _data_dir(tmp_path, schema_version=DATA_SCHEMA_VERSION + 1)
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(data_dir))
    monkeypatch.delenv(ALLOW_ENV, raising=False)

    with pytest.raises(StartupDataBlocked):
        with TestClient(get_app()):
            pass


def test_gateway_starts_when_downgrade_explicitly_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """放行的真实证据：网关 lifespan 走完，``/v1/startup`` 可读。"""
    from fastapi.testclient import TestClient

    from src.gateway.main import get_app

    data_dir = _data_dir(tmp_path, schema_version=DATA_SCHEMA_VERSION + 1)
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(data_dir))
    monkeypatch.setenv(ALLOW_ENV, "1")

    with TestClient(get_app()) as client:
        response = client.get("/v1/startup")

    assert response.status_code == 200
    assert response.json()["state"] in {"starting", "ready", "degraded"}


def test_marker_filename_contract() -> None:
    assert DATA_SCHEMA_FILENAME == "data-schema.json"
