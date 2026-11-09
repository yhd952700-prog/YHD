"""版本号必须全链一致：0.1.2。

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
    assert APP_VERSION == "0.1.2"


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


def test_build_script_reads_version_from_src_version():
    """构建脚本不再兜底版本号字面量，只从唯一真源 ``src/version.py`` 取值。"""
    script = _read(REPO / "packaging" / "build_windows_installer.ps1")
    assert 'src\\version.py' in script
    assert "APP_VERSION" in script
    # 任何 `return "x.y.z"` 兜底都会在升版时悄悄漂移，必须不存在。
    assert not re.search(r'return\s+"\d+\.\d+\.\d+"', script)
    # 不能再按固定版本字面量替换，否则 .iss 升版后静默不替换。
    assert "Replace('#define AppVersion \"" not in script
    assert "#define AppVersion \"[^\"]*\"" in script


def test_stage_payload_reads_version_from_src_version():
    """payload 组装同样只认真源，`--version` 缺省值不再写死版本号。"""
    stage = _read(REPO / "packaging" / "stage_payload.py")
    assert 'REPO_ROOT / "src" / "version.py"' in stage
    assert r"APP_VERSION\s*=" in stage
    assert 'add_argument("--version", default=""' in stage


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


def test_upgrade_version_reader_prefers_src_version():
    """单一真源：升级检查优先读 src/version.py，而不是另一份投影。

    pyproject.toml / LIUHAO_RELEASE.json 只是打包投影；仓库与安装包里
    src/version.py 都在，运行期版本判定必须收敛到这一处。
    """
    from src.upgrade.versioning import read_current_version

    version, source = read_current_version(REPO)
    assert version == APP_VERSION
    assert source == "src/version.py"

