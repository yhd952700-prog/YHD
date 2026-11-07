"""品牌字面必须收敛到唯一来源：``src/brand.json``。

2026-11 的核对发现同一个产品在不同界面自称为不同主名：
``LiuHao AI OS · 鎏灏智能中枢``、``鎏灏 LIUHAO X``、``鎏灏 AI OS``，
另外还出现过错字 ``镭灏``。这条测试把口径钉死，避免下次再漂移。

口径：主品牌 = ``LiuHao AI OS · 鎏灏智能中枢``；``LIUHAO X / 鎏灏 X``
只作为旧称出现，不得再自称权威主名。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

from src.brand import BRAND_LOCKUP, BRAND_NAME_EN, BRAND_NAME_ZH  # noqa: E402
from src.version import APP_VERSION  # noqa: E402


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def _brand_json() -> dict:
    return json.loads(_read(REPO / "src" / "brand.json"))


def test_single_source_matches_exports():
    data = _brand_json()
    assert BRAND_NAME_EN == data["name_en"] == "LiuHao AI OS"
    assert BRAND_NAME_ZH == data["name_zh"] == "鎏灏智能中枢"
    assert BRAND_LOCKUP == f"{BRAND_NAME_EN} · {BRAND_NAME_ZH}"


def test_frontend_reads_the_same_json():
    brand_ts = _read(REPO / "apps" / "console" / "console" / "src" / "brand.ts")
    assert "../../../../src/brand.json" in brand_ts
    assert "BRAND_LOCKUP" in brand_ts
    assert "鎏灏 LIUHAO X" not in brand_ts


def test_typo_is_gone():
    for rel in ("pyproject.toml", "src/knowledge/memory.py"):
        assert "镭灏" not in _read(REPO / rel), rel


def test_readme_states_one_main_brand_and_legacy_names():
    readme = _read(REPO / "README.md")
    head = "\n".join(readme.splitlines()[:11])
    assert head.startswith(f"# {BRAND_LOCKUP}")
    assert "唯一字面来源" in head
    assert "旧称" in head
    assert "唯一权威" not in head


def test_readme_model_claim_matches_installer_reality():
    readme = _read(REPO / "README.md")
    assert "安装包本身不包含任何模型权重" in readme


def test_installer_and_desktop_use_the_lockup():
    iss = _read(REPO / "installer" / "liuhao.iss")
    assert re.findall(r'#define AppName "([^"]+)"', iss) == [BRAND_LOCKUP]

    about = _read(REPO / "src" / "desktop" / "about.py")
    assert "from ..brand import BRAND_LOCKUP" in about
    assert "APP_NAME = BRAND_LOCKUP" in about

    for rel in ("src/desktop/app.py", "src/desktop/tray.py"):
        text = _read(REPO / rel)
        assert "BRAND_LOCKUP" in text or "APP_NAME" in text, rel


def test_diagnostic_bundle_readme_and_about_json_share_one_name():
    bundle = _read(REPO / "src" / "gateway" / "diagnostic_bundle.py")
    assert "from ..desktop.about import APP_NAME, collect_about" in bundle
    assert "_README = APP_NAME" in bundle
    assert '"app": str(about.get("app") or APP_NAME)' in bundle


def test_gateway_and_openapi_report_the_same_version():
    main = _read(REPO / "src" / "gateway" / "main.py")
    assert "from ..version import APP_VERSION" in main
    assert "version=APP_VERSION" in main
    assert 'version="1.0.0"' not in main
    assert 'title=f"{BRAND_LOCKUP} Gateway"' in main

    openapi = _read(REPO / "openapi.yaml")
    assert f"  title: {BRAND_LOCKUP} Gateway API" in openapi
    assert f"  version: {APP_VERSION}" in openapi
    assert "api.liuhao-ai-os.com" not in openapi


def test_static_shells_use_the_lockup():
    index = _read(REPO / "apps" / "console" / "console" / "index.html")
    assert f"<title>{BRAND_LOCKUP}</title>" in index
    assert f'content="{BRAND_NAME_ZH}"' in index

    manifest = json.loads(
        _read(REPO / "apps" / "console" / "console" / "public" / "manifest.webmanifest")
    )
    assert manifest["name"] == BRAND_LOCKUP
    assert manifest["short_name"] == BRAND_NAME_ZH


def test_mobile_nav_points_at_butler():
    root = REPO / "apps" / "console" / "console" / "src"
    nav = _read(root / "lib" / "nav.ts")
    assert "export const MOBILE_NAV_KEYS = ['overview', 'my-employees', 'butler', 'approval', 'settings']" in nav
    assert "return ['overview', 'my-employees', 'butler', fourth, last]" in nav

    shell = _read(root / "components" / "Shell.tsx")
    assert "item.key === 'butler'" in shell
    assert "'管家'" in shell

    css = _read(root / "os.css")
    assert "[data-key='butler']" in css
    assert "[data-key='workbench']" not in css


def test_third_party_trademark_notice():
    notices = _read(REPO / "packaging" / "third-party-notices.template.txt")
    assert "商标归其各自所有者所有" in notices
