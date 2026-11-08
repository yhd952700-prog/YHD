"""升级包文件级完整性与数据 schema 降级守卫。

覆盖两处回归：

* P2 —— 整包 SHA256 一致、但包内缺文件（或文件被替换、混入未声明文件）时，
  apply 必须在改动任何文件之前拒绝；安装成功后要清掉清单外的陈旧文件，
  但不能误删启动器生成的运行期配置。
* P5 —— 数据由更高 schema 版本写入时，安装更低版本必须显式确认，不能
  让旧代码静默读取新数据（典型表现：会话列表为空）。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from src.upgrade import LocalDirectorySource, PreflightError, UpgradeConfig, UpgradeManager
from src.upgrade.dataversion import (
    DATA_SCHEMA_FILENAME,
    DATA_SCHEMA_VERSION,
    read_data_schema,
    stamp_data_schema,
)
from src.upgrade.filemanifest import (
    build_file_manifest_for_zip,
    normalize_entries,
    verify_file_manifest,
)

HEALTHY_SERVICE = (
    "from fastapi import FastAPI\n\n"
    "app = FastAPI()\n\n"
    "@app.get('/health')\n"
    "def health():\n"
    "    return {'ok': True}\n"
)


def _setup_app(root: Path, *, version: str = "0.1.0") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "liuhao-ai-os"\nversion = "{version}"\n', encoding="utf-8"
    )
    (root / "src" / "gateway").mkdir(parents=True, exist_ok=True)
    (root / "src" / "gateway" / "main.py").write_text(HEALTHY_SERVICE, encoding="utf-8")
    (root / "data").mkdir(exist_ok=True)
    conn = sqlite3.connect(root / "data" / "app.db")
    conn.execute("CREATE TABLE t(v TEXT)")
    conn.execute("INSERT INTO t VALUES ('keep')")
    conn.commit()
    conn.close()
    return root


def _package_files(version: str = "0.2.0") -> dict[str, str]:
    return {
        "pyproject.toml": f'[project]\nname = "liuhao-ai-os"\nversion = "{version}"\n',
        "src/gateway/main.py": HEALTHY_SERVICE,
        "src/app.py": "VALUE = 2\n",
    }


def _write_release(
    release_dir: Path,
    files: dict[str, str],
    *,
    version: str = "0.2.0",
    with_file_manifest: bool = True,
    data_schema_version: int | None = DATA_SCHEMA_VERSION,
) -> tuple[Path, dict]:
    release_dir.mkdir(parents=True, exist_ok=True)
    artifact = f"liuhao-{version}.zip"
    zip_path = release_dir / artifact
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    release: dict = {
        "version": version,
        "artifact": artifact,
        "sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
        "size": zip_path.stat().st_size,
    }
    if with_file_manifest:
        release["files"] = build_file_manifest_for_zip(zip_path)
    if data_schema_version is not None:
        release["data_schema_version"] = data_schema_version
    (release_dir / "release-manifest.json").write_text(
        json.dumps({"schema": "liuhao.release-manifest/1", "releases": [release]}),
        encoding="utf-8",
    )
    return zip_path, release


def _replace_zip_keep_stale_manifest(
    zip_path: Path, release_dir: Path, files: dict[str, str]
) -> None:
    """重写 ZIP 并同步整包 sha/size，但保留旧的文件级清单。"""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    manifest_path = release_dir / "release-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = manifest["releases"][0]
    entry["sha256"] = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    entry["size"] = zip_path.stat().st_size
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def _manager(
    app_root: Path,
    release_dir: Path,
    *,
    allow_same_or_downgrade: bool = False,
    allow_data_downgrade: bool = False,
) -> UpgradeManager:
    return UpgradeManager(
        UpgradeConfig(
            app_root=app_root,
            data_dir=app_root / "data",
            source=LocalDirectorySource(release_dir),
            allow_same_or_downgrade=allow_same_or_downgrade,
            allow_data_downgrade=allow_data_downgrade,
        )
    )


def _apply(manager: UpgradeManager):
    check = manager.check()
    assert check.release is not None, check.message
    download = manager.download(check.release)
    return manager.apply(download.path, check.release)


# ---------------------------------------------------------------------------
# P2: file-level manifest
# ---------------------------------------------------------------------------


def test_missing_file_is_rejected_before_any_mutation(tmp_path: Path) -> None:
    app_root = _setup_app(tmp_path / "app")
    release_dir = tmp_path / "releases"
    files = _package_files()
    zip_path, _release = _write_release(release_dir, files)
    broken = dict(files)
    broken.pop("src/app.py")
    _replace_zip_keep_stale_manifest(zip_path, release_dir, broken)

    manager = _manager(app_root, release_dir)
    try:
        with pytest.raises(PreflightError) as exc:
            _apply(manager)
        assert "缺少" in str(exc.value)
        assert "src/app.py" in str(exc.value)
    finally:
        manager.close()

    assert (app_root / "pyproject.toml").read_text(encoding="utf-8").count("0.1.0") == 1
    assert not (app_root / "src" / "app.py").exists()
    assert not list((app_root / "data" / "upgrade-backups").glob("*"))


def test_tampered_file_content_is_rejected(tmp_path: Path) -> None:
    app_root = _setup_app(tmp_path / "app")
    release_dir = tmp_path / "releases"
    files = _package_files()
    zip_path, _release = _write_release(release_dir, files)
    tampered = dict(files)
    # 同字节数、不同内容：只可能被哈希校验发现。
    tampered["src/app.py"] = "VALUE = 9\n"
    _replace_zip_keep_stale_manifest(zip_path, release_dir, tampered)

    manager = _manager(app_root, release_dir)
    try:
        with pytest.raises(PreflightError) as exc:
            _apply(manager)
        assert "哈希" in str(exc.value)
    finally:
        manager.close()
    assert not (app_root / "src" / "app.py").exists()


def test_undeclared_extra_file_is_rejected(tmp_path: Path) -> None:
    app_root = _setup_app(tmp_path / "app")
    release_dir = tmp_path / "releases"
    files = _package_files()
    zip_path, _release = _write_release(release_dir, files)
    with_extra = dict(files)
    with_extra["src/backdoor.py"] = "print('extra')\n"
    _replace_zip_keep_stale_manifest(zip_path, release_dir, with_extra)

    manager = _manager(app_root, release_dir)
    try:
        with pytest.raises(PreflightError) as exc:
            _apply(manager)
        assert "多出" in str(exc.value)
    finally:
        manager.close()
    assert not (app_root / "src" / "backdoor.py").exists()


def test_manifest_without_file_list_is_rejected(tmp_path: Path) -> None:
    app_root = _setup_app(tmp_path / "app")
    release_dir = tmp_path / "releases"
    _write_release(release_dir, _package_files(), with_file_manifest=False)

    manager = _manager(app_root, release_dir)
    try:
        with pytest.raises(PreflightError) as exc:
            _apply(manager)
        assert "文件级清单" in str(exc.value)
    finally:
        manager.close()


def test_stale_files_are_pruned_but_runtime_config_is_kept(tmp_path: Path) -> None:
    app_root = _setup_app(tmp_path / "app")
    # 旧版本留下的：被移除的模块、已不随包的配置项。
    (app_root / "src" / "legacy.py").write_text("OLD = 1\n", encoding="utf-8")
    (app_root / "config").mkdir()
    (app_root / "config" / "production.yaml").write_text("old: true\n", encoding="utf-8")
    # 启动器生成、必须保留的运行期文件。
    (app_root / "config" / "app.env").write_text("LIUHAO_PORT=1\n", encoding="utf-8")
    (app_root / "config" / "auth_secrets.json").write_text("{}", encoding="utf-8")

    release_dir = tmp_path / "releases"
    files = _package_files()
    files["config/logging.yaml"] = "level: info\n"
    _write_release(release_dir, files)

    manager = _manager(app_root, release_dir)
    try:
        result = _apply(manager)
        assert result.to_version == "0.2.0"
    finally:
        manager.close()

    assert not (app_root / "src" / "legacy.py").exists()
    assert not (app_root / "config" / "production.yaml").exists()
    assert (app_root / "config" / "app.env").is_file()
    assert (app_root / "config" / "auth_secrets.json").is_file()
    assert (app_root / "config" / "logging.yaml").is_file()


def test_console_build_output_is_pruned_strictly(tmp_path: Path) -> None:
    """console/ 是纯构建产物：旧哈希资源必须清掉，且不受“父目录覆盖”限制。"""
    app_root = _setup_app(tmp_path / "app")
    (app_root / "console" / "assets").mkdir(parents=True)
    (app_root / "console" / "index.html").write_text("<html>new</html>", encoding="utf-8")
    (app_root / "console" / "assets" / "index-OLD.js").write_text("old", encoding="utf-8")
    (app_root / "docs").mkdir()
    (app_root / "docs" / "notes.md").write_text("keep", encoding="utf-8")

    release_dir = tmp_path / "releases"
    release_dir.mkdir()

    manager = _manager(app_root, release_dir)
    try:
        pruned = manager._prune_stale_files(["console/index.html", "pyproject.toml"])
    finally:
        manager.close()

    assert pruned["deleted"] == ["console/assets/index-OLD.js"]
    assert (app_root / "console" / "index.html").is_file()
    # 不在本次安装根覆盖范围内（docs 无包内文件）的额外内容不删。
    assert (app_root / "docs" / "notes.md").is_file()


def test_apply_stamps_the_data_schema(tmp_path: Path) -> None:
    app_root = _setup_app(tmp_path / "app")
    release_dir = tmp_path / "releases"
    _write_release(release_dir, _package_files())

    manager = _manager(app_root, release_dir)
    try:
        _apply(manager)
    finally:
        manager.close()

    state = read_data_schema(app_root / "data")
    assert state is not None
    assert state.version == DATA_SCHEMA_VERSION
    assert state.written_by == "0.2.0"


# ---------------------------------------------------------------------------
# P5: data schema downgrade guard
# ---------------------------------------------------------------------------


def _stamp_newer_data(
    app_root: Path, *, schema_version: int = DATA_SCHEMA_VERSION
) -> Path:
    data_dir = app_root / "data"
    stamp_data_schema(data_dir, app_version="0.9.0", schema_version=schema_version)
    return data_dir


def test_downgrade_over_newer_data_is_refused(tmp_path: Path) -> None:
    app_root = _setup_app(tmp_path / "app", version="0.2.0")
    _stamp_newer_data(app_root)
    release_dir = tmp_path / "releases"
    # 旧版本发布清单没有 data_schema_version：按 schema 1 保守处理。
    _write_release(
        release_dir,
        _package_files("0.1.0"),
        version="0.1.0",
        data_schema_version=None,
    )

    manager = _manager(app_root, release_dir, allow_same_or_downgrade=True)
    try:
        with pytest.raises(PreflightError) as exc:
            _apply(manager)
        assert "更高版本写入" in str(exc.value)
        assert "--allow-data-downgrade" in str(exc.value)
    finally:
        manager.close()

    # 拒绝必须发生在任何改动之前：代码版本与数据都保持原样。
    assert "0.2.0" in (app_root / "pyproject.toml").read_text(encoding="utf-8")
    assert (app_root / "data" / "app.db").is_file()


def test_downgrade_over_newer_data_runs_with_explicit_confirmation(
    tmp_path: Path,
) -> None:
    app_root = _setup_app(tmp_path / "app", version="0.2.0")
    _stamp_newer_data(app_root)
    release_dir = tmp_path / "releases"
    _write_release(
        release_dir,
        _package_files("0.1.0"),
        version="0.1.0",
        data_schema_version=None,
    )

    manager = _manager(
        app_root,
        release_dir,
        allow_same_or_downgrade=True,
        allow_data_downgrade=True,
    )
    try:
        result = _apply(manager)
        assert result.to_version == "0.1.0"
    finally:
        manager.close()

    # 标记只增不减：确认降级也不能把「数据曾被更高版本写过」的证据抹掉。
    state = read_data_schema(app_root / "data")
    assert state is not None and state.version == DATA_SCHEMA_VERSION


def test_data_schema_status_reports_incompatibility(tmp_path: Path) -> None:
    app_root = _setup_app(tmp_path / "app", version="0.2.0")
    newer = DATA_SCHEMA_VERSION + 1
    _stamp_newer_data(app_root, schema_version=newer)
    release_dir = tmp_path / "releases"
    release_dir.mkdir()

    manager = _manager(app_root, release_dir)
    try:
        status = manager.data_schema_status()
    finally:
        manager.close()
    assert status["compatible"] is False
    assert status["version"] == newer
    assert status["supported_version"] == DATA_SCHEMA_VERSION
    assert "更高版本" in status["action"]


# ---------------------------------------------------------------------------
# file manifest unit coverage
# ---------------------------------------------------------------------------


def test_file_manifest_round_trip_and_duplicate_rejection(tmp_path: Path) -> None:
    zip_path = tmp_path / "pkg.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("a.txt", "alpha")
        archive.writestr("dir/b.txt", "beta")
    entries = build_file_manifest_for_zip(zip_path)
    assert [item["path"] for item in entries] == ["a.txt", "dir/b.txt"]
    with zipfile.ZipFile(zip_path) as archive:
        summary = verify_file_manifest(archive, normalize_entries(entries, context="t"))
    assert summary["files"] == 2

    duplicated = entries + [entries[0]]
    with pytest.raises(Exception):
        normalize_entries(duplicated, context="t")
