"""版本号必须全链一致：0.1.1。

旧包 0.1.0 与本版内容完全不同却同号，升级判定会失效，两份同名包也无法区分。
这条测试把「版本号写在哪些地方」变成可执行断言，避免下次升版只改了一处。
"""

import json
import re
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

from src.version import APP_VERSION  # noqa: E402


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def test_app_version_is_a_release_number():
    assert re.fullmatch(r"\d+\.\d+\.\d+", APP_VERSION), APP_VERSION
    assert APP_VERSION == "0.1.1"


def test_pyproject_version_matches():
    with open(REPO / "pyproject.toml", "rb") as handle:
        assert tomllib.load(handle)["project"]["version"] == APP_VERSION


def test_frontend_package_versions_match():
    assert json.loads(_read(REPO / "frontend" / "package.json"))["version"] == APP_VERSION
    lock = json.loads(_read(REPO / "frontend" / "package-lock.json"))
    assert lock["version"] == APP_VERSION
    assert lock["packages"][""]["version"] == APP_VERSION


def test_installer_default_version_matches():
    iss = _read(REPO / "installer" / "liuhao.iss")
    matches = re.findall(r'#define AppVersion "([^"]+)"', iss)
    assert matches == [APP_VERSION], matches
    assert f"OutputBaseFilename=LiuHao-Setup-{{#AppVersion}}" in iss


def test_build_script_substitutes_version_by_pattern():
    script = _read(REPO / "packaging" / "build_windows_installer.ps1")
    assert f'return "{APP_VERSION}"' in script
    # 不能再按固定版本字面量替换，否则 .iss 升版后静默不替换。
    assert "Replace('#define AppVersion \"" not in script
    assert "#define AppVersion \"[^\"]*\"" in script


def test_health_endpoints_report_app_version():
    gateway = _read(REPO / "src" / "gateway" / "health.py")
    assert "from ..version import APP_VERSION" in gateway
    assert '"version": APP_VERSION' in gateway
    assert '"version": "1.0.0"' not in gateway

    api = _read(REPO / "src" / "api" / "health_api.py")
    assert "from ..version import APP_VERSION" in api
    assert '"version": APP_VERSION' in api
    assert '"version": "1.0.0"' not in api


def test_launcher_log_records_version():
    launcher = _read(REPO / "scripts" / "launch_installed.py")
    assert "version: {_app_version()}" in launcher
    assert '"pyproject.toml"' in launcher
