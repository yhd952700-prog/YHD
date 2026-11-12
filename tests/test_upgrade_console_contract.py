"""前后端契约门禁：签章、拒绝升级、运行期比对。"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from src.upgrade import PreflightError, UpgradeConfig, UpgradeManager, LocalDirectorySource
from src.upgrade.console_contract import (
    CONTRACT_RELPATH,
    CONTRACT_SCHEMA,
    FrontendContractError,
    build_stamp,
    console_fingerprint,
    read_backend_contract_from_source,
    verify_health_contract,
    verify_installed,
    verify_package,
)


CONSOLE = {"console/index.html": b"<html>v2</html>", "console/assets/app.js": b"console.log(1)"}
BACKEND = 'API_CONTRACT_VERSION = "1"\n'


def _package(tmp_path: Path, *, version: str = "0.2.0", console=CONSOLE, backend=BACKEND,
             stamp_patch=None) -> Path:
    console = dict(console)
    files = dict(console)
    files["src/gateway/contract.py"] = backend
    stamp = build_stamp(
        console, backend_contract=read_backend_contract_from_source(backend), version=version
    )
    if stamp_patch:
        stamp.update(stamp_patch)
    files[CONTRACT_RELPATH] = json.dumps(stamp).encode("utf-8")
    path = tmp_path / f"liuhao-{version}.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return path


def _verify(path: Path, version: str = "0.2.0") -> dict:
    with zipfile.ZipFile(path) as zf:
        return verify_package(zf, version)


def test_valid_package_passes(tmp_path: Path) -> None:
    stamp = _verify(_package(tmp_path))
    assert stamp["schema"] == CONTRACT_SCHEMA
    assert stamp["api_contract"] == "1"
    assert stamp["console_files"] == len(CONSOLE)


def test_missing_console_is_refused(tmp_path: Path) -> None:
    path = _package(tmp_path, console={"console/assets/app.js": b"x"})
    with pytest.raises(FrontendContractError, match="缺少控制台构建产物"):
        _verify(path)


def test_missing_stamp_is_refused(tmp_path: Path) -> None:
    path = _package(tmp_path)
    with zipfile.ZipFile(path) as zf:
        files = {n: zf.read(n) for n in zf.namelist() if n != CONTRACT_RELPATH}
    path.unlink()
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    with pytest.raises(FrontendContractError, match="缺少前后端契约签章"):
        _verify(path)


def test_backend_contract_mismatch_is_refused(tmp_path: Path) -> None:
    path = _package(tmp_path, stamp_patch={"api_contract": "2"})
    with pytest.raises(FrontendContractError, match="前后端契约不一致"):
        _verify(path)


def test_console_bytes_mismatch_is_refused(tmp_path: Path) -> None:
    path = _package(tmp_path, console={"console/index.html": b"<html>new</html>"})
    # Rewrite index.html after the stamp was computed: a stale console build.
    with zipfile.ZipFile(path) as zf:
        files = {n: zf.read(n) for n in zf.namelist()}
    files["console/index.html"] = b"<html>tampered</html>"
    path.unlink()
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    with pytest.raises(FrontendContractError, match="控制台产物与契约签章不匹配"):
        _verify(path)


def test_version_mismatch_is_refused(tmp_path: Path) -> None:
    path = _package(tmp_path)
    with pytest.raises(FrontendContractError, match="前端版本与本次发布版本不一致"):
        _verify(path, "0.3.0")


def test_health_contract_compare() -> None:
    stamp = {"api_contract": "1"}
    verify_health_contract(stamp, {"api_contract": "1"})
    with pytest.raises(FrontendContractError, match="不一致"):
        verify_health_contract(stamp, {"api_contract": "2"})
    with pytest.raises(FrontendContractError, match="没有在 /v1/health 报告"):
        verify_health_contract(stamp, {})


def test_installed_tree_without_console_is_not_applicable(tmp_path: Path) -> None:
    applicable, stamp, note = verify_installed(tmp_path)
    assert applicable is False
    assert stamp == {}
    assert "不适用" in note


def test_installed_tree_stamp_round_trip(tmp_path: Path) -> None:
    root = tmp_path / "app"
    for name, data in CONSOLE.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (root / "src" / "gateway").mkdir(parents=True)
    (root / "src" / "gateway" / "contract.py").write_text(BACKEND, encoding="utf-8")
    stamp = build_stamp(
        CONSOLE, backend_contract="1", version="0.2.0"
    )
    (root / CONTRACT_RELPATH).write_text(json.dumps(stamp), encoding="utf-8")
    applicable, read_back, _note = verify_installed(root)
    assert applicable is True
    assert read_back["console_fingerprint"] == console_fingerprint(CONSOLE)


def test_manager_refuses_package_without_console(tmp_path: Path) -> None:
    root = tmp_path / "app"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "liuhao-ai-os"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "console").mkdir()
    (root / "console" / "index.html").write_text("<html>old</html>", encoding="utf-8")
    (root / "data").mkdir()

    releases = tmp_path / "releases"
    releases.mkdir()
    zip_path = releases / "liuhao-0.2.0.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(
            "pyproject.toml", '[project]\nname = "liuhao-ai-os"\nversion = "0.2.0"\n'
        )
        zf.writestr("src/app.py", "VALUE = 2\n")
    manifest = {
        "schema": "liuhao.release-manifest/1",
        "releases": [{
            "version": "0.2.0",
            "artifact": zip_path.name,
            "sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
            "size": zip_path.stat().st_size,
        }],
    }
    (releases / "release-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    manager = UpgradeManager(UpgradeConfig(app_root=root, source=LocalDirectorySource(releases)))
    try:
        checked = manager.check()
        assert checked.release is not None
        downloaded = manager.download(checked.release)
        with pytest.raises(PreflightError, match="缺少控制台构建产物"):
            manager.apply(downloaded.path, downloaded.release)
    finally:
        manager.close()

    # 拒绝必须发生在任何改动之前。
    assert (root / "src" / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert not (root / "upgrade-backups").exists() or not any(
        (root / "upgrade-backups").iterdir()
    )
