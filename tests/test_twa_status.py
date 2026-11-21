"""大升级第二季 V3（安卓 TWA）专项测试。

覆盖三层，全是文件事实级断言，不联网、不跑 Bubblewrap：
1. 仓库自带的 PWA 资产（console/manifest + 图标）确实满足 TWA 硬要求；
2. packaging/twa 套件配置可解析、包名定死、host/指纹如实呈占位状态、
   仓库里绝无签名密钥文件；
3. src.twa.status 的自查口径与聊天工具 android_app_status 的接线。
"""

from __future__ import annotations

import json
from pathlib import Path

from src.twa.status import inspect_kit, inspect_pwa, twa_status

REPO_ROOT = Path(__file__).resolve().parents[1]
CONSOLE_DIR = REPO_ROOT / "console"
KIT_DIR = REPO_ROOT / "packaging" / "twa"


def test_repo_console_pwa_ready_for_twa():
    """仓库 console 的 manifest + 512 any/maskable 图标必须真在位。"""
    result = inspect_pwa(CONSOLE_DIR)
    assert result["ready"] is True, result["missing"]
    assert "Jarvis" in result["name"]
    assert result["display"] == "standalone"
    assert result["icons_ok"] is True


def test_inspect_pwa_missing_dir_is_honest():
    result = inspect_pwa(None)
    assert result["ready"] is False
    assert result["missing"], "缺失时必须说清缺什么，不许空着假报"


def test_inspect_pwa_broken_manifest(tmp_path):
    (tmp_path / "manifest.webmanifest").write_text("{not json", encoding="utf-8")
    result = inspect_pwa(tmp_path)
    assert result["ready"] is False
    assert any("解析失败" in item for item in result["missing"])


def test_kit_config_parses_and_identity_is_fixed():
    config = json.loads((KIT_DIR / "twa-manifest.json").read_text(encoding="utf-8"))
    # 包名一旦发布永不更改，测试钉死防手滑。
    assert config["packageId"] == "com.liuhaoglobal.jarvis"
    assert config["name"] == "Jarvis · 贾维斯"
    assert config["themeColor"] == "#050912"
    assert config["backgroundColor"] == "#050912"
    assert config["iconUrl"] == "/icon-512.png"
    assert config["maskableIconUrl"] == "/icon-512-maskable.png"
    assert config["signingKey"]["path"].endswith(".keystore")


def test_kit_files_present_and_placeholders_honest():
    result = inspect_kit(KIT_DIR)
    assert result["present"] is True
    assert result["build_script_present"] is True
    assert result["guide_present"] is True
    assert result["package_id"] == "com.liuhaoglobal.jarvis"
    # 仓库里的 host/指纹就是占位状态：状态面必须如实报「没配好」，
    # 配好只能发生在 Hughes 的打包机上（他填真实地址与真实指纹）。
    assert result["host_configured"] is False
    assert result["assetlinks_filled"] is False


def test_assetlinks_shape():
    links = json.loads((KIT_DIR / "assetlinks.json").read_text(encoding="utf-8"))
    assert isinstance(links, list) and len(links) == 1
    target = links[0]["target"]
    assert target["namespace"] == "android_app"
    assert target["package_name"] == "com.liuhaoglobal.jarvis"
    assert links[0]["relation"] == ["delegate_permission/common.handle_all_urls"]


def test_no_signing_key_material_in_repo():
    """签名密钥绝不进仓库：套件目录里不许有 keystore/jks，也不许有真指纹。"""
    assert not list(KIT_DIR.glob("*.keystore"))
    assert not list(KIT_DIR.glob("*.jks"))
    assert inspect_kit(KIT_DIR)["keystore_present"] is False


def test_twa_status_shape_and_next_steps():
    status = twa_status()
    assert status["pwa"]["ready"] is True
    assert status["ready_to_build"] is False  # host 还是占位，如实报不可打包
    assert status["next_steps"], "有缺口时必须给出人话下一步"
    assert any("HTTPS" in step for step in status["next_steps"])


def test_android_app_status_tool_registered_and_answers():
    from src.ai.tools import make_tools

    tools = make_tools("alice", status_fn=lambda: {"turn": 0})
    tool = next(t for t in tools if t.name == "android_app_status")
    assert tool.risk == "LOW"
    out = json.loads(tool.fn())
    assert out["status"] == "ok"
    assert out["pwa_ready"] is True
    assert out["package_id"] == "com.liuhaoglobal.jarvis"
    assert out["host_configured"] is False
