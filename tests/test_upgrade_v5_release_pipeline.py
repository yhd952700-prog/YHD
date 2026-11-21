"""V5 发布流水线转正：正典控制台来源口径 + 安装器脚本审计守卫。

覆盖 V5 新增/修改的三块：

1. ``console_provenance`` 的 canonical-dist 口径：忠实副本可写记录，
   不忠实副本（多/缺/改）拒绝写记录；
2. ``make_release._verify_console_provenance`` 双口径验锚：canonical
   接受、包内文件被改拒绝、正典树漂移拒绝、版本不一致拒绝；历史
   source-build 口径保持可用（回归）；
3. ``make_release.build`` 用正典控制台组装出真实格式发布包+清单，
   再经既有签名工具签名后可被升级器解析（校验链真实可验）；
4. ``build_windows_installer.ps1`` 审计修复的静态守卫：发布包 glob 与
   make_release 产物名一致、前端默认正典口径、stage 调用传 --console-dist。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, REPO / relpath)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def provenance():
    return _load("v5_console_provenance", "packaging/console_provenance.py")


@pytest.fixture(scope="module")
def make_release():
    return _load("v5_make_release", "packaging/make_release.py")


def _write_console_tree(base: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return base


_CONSOLE_FILES = {
    "index.html": "<html>wb</html>\n",
    "assets/app.js": "console.log('wb')\n",
    "theme/jarvis-theme.css": "/* theme */\n",
}


# ---------------------------------------------------------------- provenance


def test_canonical_metadata_roundtrip(provenance, tmp_path: Path):
    canonical = _write_console_tree(tmp_path / "canonical", _CONSOLE_FILES)
    dest = tmp_path / "dest"
    shutil.copytree(canonical, dest)
    meta = provenance.write_canonical_dist_metadata(
        dest, canonical_dir=canonical, version="0.5.1"
    )
    assert meta["provenance_kind"] == "canonical-dist"
    assert meta["source_sha256"] == meta["files_sha256"]
    assert meta["file_count"] == len(_CONSOLE_FILES)
    written = json.loads((dest / "console-build.json").read_text(encoding="utf-8"))
    assert written["source_sha256"] == meta["source_sha256"]


def test_canonical_metadata_rejects_unfaithful_copy(provenance, tmp_path: Path):
    canonical = _write_console_tree(tmp_path / "canonical", _CONSOLE_FILES)
    dest = tmp_path / "dest"
    shutil.copytree(canonical, dest)
    (dest / "assets" / "app.js").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(ValueError, match="不是正典 dist 的忠实副本"):
        provenance.write_canonical_dist_metadata(
            dest, canonical_dir=canonical, version="0.5.1"
        )


# ------------------------------------------------------------- make_release


def _entries_for(console_dir: Path) -> list[tuple[str, Path]]:
    return [
        ("console/" + path.relative_to(console_dir).as_posix(), path)
        for path in sorted(console_dir.rglob("*"))
        if path.is_file()
    ]


def _stage_canonical_payload(
    provenance, tmp_path: Path, *, version: str = "0.5.1"
) -> tuple[Path, Path]:
    """返回 (仓库锚目录, payload 控制台目录)：锚目录模拟仓库根 console/。"""
    anchor = _write_console_tree(tmp_path / "repo" / "console", _CONSOLE_FILES)
    payload_console = tmp_path / "payload" / "console"
    shutil.copytree(anchor, payload_console)
    provenance.write_canonical_dist_metadata(
        payload_console, canonical_dir=anchor, version=version
    )
    # 契约签章由 stage/make_release 另行写入，来源校验应忽略它
    (payload_console / "console-contract.json").write_text("{}\n", encoding="utf-8")
    return anchor, payload_console


def test_verify_provenance_canonical_accepts(make_release, provenance, tmp_path, monkeypatch):
    anchor, payload_console = _stage_canonical_payload(provenance, tmp_path)
    monkeypatch.setattr(make_release, "REPO_ROOT", anchor.parent)
    make_release._verify_console_provenance(_entries_for(payload_console), "0.5.1")


def test_verify_provenance_canonical_rejects_tampered_payload(
    make_release, provenance, tmp_path, monkeypatch
):
    anchor, payload_console = _stage_canonical_payload(provenance, tmp_path)
    monkeypatch.setattr(make_release, "REPO_ROOT", anchor.parent)
    (payload_console / "assets" / "app.js").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(make_release.BuildError, match="被改动"):
        make_release._verify_console_provenance(_entries_for(payload_console), "0.5.1")


def test_verify_provenance_canonical_rejects_anchor_drift(
    make_release, provenance, tmp_path, monkeypatch
):
    anchor, payload_console = _stage_canonical_payload(provenance, tmp_path)
    monkeypatch.setattr(make_release, "REPO_ROOT", anchor.parent)
    (anchor / "assets" / "app.js").write_text("new canonical\n", encoding="utf-8")
    with pytest.raises(make_release.BuildError, match="正典 dist 不一致"):
        make_release._verify_console_provenance(_entries_for(payload_console), "0.5.1")


def test_verify_provenance_canonical_rejects_version_mismatch(
    make_release, provenance, tmp_path, monkeypatch
):
    anchor, payload_console = _stage_canonical_payload(provenance, tmp_path)
    monkeypatch.setattr(make_release, "REPO_ROOT", anchor.parent)
    with pytest.raises(make_release.BuildError, match="版本"):
        make_release._verify_console_provenance(_entries_for(payload_console), "0.5.2")


def test_verify_provenance_source_build_still_accepted(
    make_release, provenance, tmp_path, monkeypatch
):
    """历史口径回归：apps 源码指纹记录仍可验过，不能被正典口径误伤。"""
    source_dir = tmp_path / "repo" / "apps" / "console" / "console"
    (source_dir / "src").mkdir(parents=True)
    (source_dir / "src" / "main.ts").write_text("export {}\n", encoding="utf-8")
    payload_console = tmp_path / "payload" / "console"
    _write_console_tree(payload_console, _CONSOLE_FILES)
    provenance.write_build_metadata(
        payload_console,
        source_dir=source_dir,
        version="0.5.1",
        command="npm run build",
    )
    (payload_console / "console-contract.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(make_release, "REPO_ROOT", tmp_path / "repo")
    make_release._verify_console_provenance(_entries_for(payload_console), "0.5.1")


# ------------------------------------------------- make_release build (real)


def _fixture_app_tree(root: Path, version: str) -> Path:
    """最小可打包安装树：必需文件齐全，控制台用仓库正典 dist 原样复制。"""
    app = root / "app"
    for rel in (
        "src/gateway/main.py",
        "src/gateway/chat.py",
        "src/upgrade/manager.py",
        "src/version.py",
    ):
        path = app / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# fixture\n", encoding="utf-8")
    # 契约版本读取只解析字面量：给夹具一份与真源同值的 contract.py
    contract_dst = app / "src" / "gateway" / "contract.py"
    contract_dst.write_text(
        (REPO / "src" / "gateway" / "contract.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (app / "pyproject.toml").write_text(
        f'[project]\nname = "liuhao-ai-os"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    for name in (
        "capability-registry.yaml",
        "requirements.txt",
        "oss-registry.yaml",
        "LiuHao.bat",
        "START-HERE.txt",
        "THIRD-PARTY-NOTICES.txt",
        "sbom-cyclonedx.json",
    ):
        (app / name).write_text("fixture\n", encoding="utf-8")
    (app / "assets").mkdir(parents=True, exist_ok=True)
    (app / "assets" / "liuhao.ico").write_bytes(b"\x00\x00\x01\x00")
    licenses = app / "licenses"
    licenses.mkdir(parents=True, exist_ok=True)
    make_release_mod = sys.modules["v5_make_release"]
    for name in make_release_mod.APP_TREE_LICENSE_FILES:
        rel = name.split("/", 1)[1]
        (licenses / rel).write_text("license fixture\n", encoding="utf-8")
    console_src = REPO / "console"
    shutil.copytree(console_src, app / "console")
    provenance_mod = sys.modules["v5_console_provenance"]
    provenance_mod.write_canonical_dist_metadata(
        app / "console", canonical_dir=console_src, version=version
    )
    return app


def test_make_release_builds_signed_real_format_manifest(
    make_release, provenance, tmp_path: Path
):
    version = "0.5.1"
    app = _fixture_app_tree(tmp_path, version)
    out = tmp_path / "release"
    args = argparse.Namespace(
        app_root=str(app),
        out=str(out),
        version=None,
        notes=None,
        channel="stable",
        released_at="2026-11-18T00:00:00Z",
        signing_key=None,
        strict_dev_paths=False,
        no_cli_check=True,  # 夹具 src 是桩，CLI 端到端在 V5 演练里用真树跑
    )
    assert make_release.build(args) == 0
    zip_path = out / f"jarvis-{version}-upgrade.zip"
    manifest_path = out / "LIUHAO_RELEASE.json"
    assert zip_path.is_file() and manifest_path.is_file()

    from src.upgrade.sources import parse_manifest, select_latest

    parsed = parse_manifest(manifest_path.read_bytes())
    latest = select_latest(parsed)
    assert latest.version == version
    assert latest.artifact == zip_path.name
    assert latest.files, "文件级清单必须随清单产出"

    # 用既有签名工具补两级签名后，校验链（require=True）必须真验过。
    tool = _load("v5_sign_tool", "scripts/ops/sign_release_manifest.py")
    keys = tmp_path / "keys"
    tool.generate_keypair(keys)
    signed = tool.sign_manifest(
        manifest_path, keys / tool.PRIVATE_FILE_NAME, sequence=1
    )
    assert signed and signed[0]["version"] == version

    from src.upgrade.manifest_trust import verify_manifest_signature

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    public_b64 = (keys / tool.PUBLIC_B64_NAME).read_text(encoding="utf-8").strip()
    assert verify_manifest_signature(data, public_b64, require=True) is True
    data["notes"] = "tampered"
    with pytest.raises(Exception, match="签名验证失败"):
        verify_manifest_signature(data, public_b64, require=True)


# ------------------------------------------------------------ installer ps1


def test_installer_script_audit_guards():
    text = (REPO / "packaging" / "build_windows_installer.ps1").read_text(
        encoding="utf-8"
    )
    # 发布包 glob 必须与 make_release 的实际产物名一致（旧 liuhao-*.zip 永不命中）
    assert "jarvis-*-upgrade.zip" in text
    assert "liuhao-*.zip" not in text
    # 前端默认正典口径：源码重建必须是显式开关，且 stage 调用显式传来源
    assert "BuildFrontendFromSource" in text
    assert "--console-dist $ConsoleDistForStage" in text
    # 版本仍只从单一真源解析，不许写死字面量兜底
    assert "Resolve-Version" in text and "src\\version.py" in text


def test_stage_payload_defaults_to_canonical_console():
    stage = _load("v5_stage_payload", "packaging/stage_payload.py")
    assert stage.CANONICAL_CONSOLE_DIST == REPO / "console"
    assert (stage.CANONICAL_CONSOLE_DIST / "index.html").is_file()
    import inspect

    assert "console_dist" in inspect.signature(stage.build).parameters
