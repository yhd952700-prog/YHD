"""安卓 App（TWA）状态自查（大升级第二季 V3）。

回答三件事，必须件件有据、不许假报：

* PWA 底子齐不齐——直接读当前实际在服务的 console 目录里的
  ``manifest.webmanifest`` 与图标文件（TWA 复用的就是这套资产）。
* 打包套件到哪一步——读源码树 ``packaging/twa/`` 的配置文件：
  host 填了真实地址没有、assetlinks 指纹回填了没有。安装包（payload）
  里不带 ``packaging/``，此时如实报「套件在源码里」，指引照样给全。
* 还差什么——把缺口翻成人话步骤（HTTPS 入口 / 指纹部署 / 在 Windows
  上跑一键脚本），与 ``packaging/twa/README.md`` 同口径。

本模块只读文件、只解析 JSON，不联网、不生成任何密钥。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

#: 占位值的识别标记：host 还是 *.example.com、指纹还是 REPLACE_*，都算没填。
_PLACEHOLDER_MARKERS = ("example.com", "REPLACE")

#: TWA 硬性要求的图标：512 any（装机图标）+ 512 maskable（自适应图标）。
_REQUIRED_ICONS = (("512x512", "any"), ("512x512", "maskable"))


def _is_placeholder(value: str) -> bool:
    return any(marker in value for marker in _PLACEHOLDER_MARKERS)


def inspect_pwa(console_dir: Optional[Path]) -> Dict[str, Any]:
    """检查一个 console 目录的 PWA 就绪度（manifest 合法 + 图标文件在位）。"""
    result: Dict[str, Any] = {
        "ready": False,
        "console_dir": str(console_dir) if console_dir else "",
        "name": "",
        "display": "",
        "theme_color": "",
        "icons_ok": False,
        "missing": [],
    }
    if console_dir is None:
        result["missing"].append("找不到正在服务的 console 目录")
        return result
    manifest_path = console_dir / "manifest.webmanifest"
    if not manifest_path.is_file():
        result["missing"].append("console 目录里没有 manifest.webmanifest")
        return result
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - 解析失败如实报，不编
        result["missing"].append(f"manifest.webmanifest 解析失败：{exc}")
        return result
    result["name"] = str(manifest.get("name") or "")
    result["display"] = str(manifest.get("display") or "")
    result["theme_color"] = str(manifest.get("theme_color") or "")
    if result["display"] != "standalone":
        result["missing"].append("manifest 的 display 不是 standalone")
    icons = manifest.get("icons") or []
    icons_ok = True
    for sizes, purpose in _REQUIRED_ICONS:
        entry = next(
            (
                icon
                for icon in icons
                if icon.get("sizes") == sizes
                and purpose in str(icon.get("purpose") or "any")
            ),
            None,
        )
        if entry is None:
            icons_ok = False
            result["missing"].append(f"manifest 缺 {sizes} {purpose} 图标条目")
            continue
        icon_file = console_dir / str(entry.get("src") or "").lstrip("/")
        if not icon_file.is_file():
            icons_ok = False
            result["missing"].append(f"图标文件不在位：{entry.get('src')}")
    result["icons_ok"] = icons_ok
    result["ready"] = icons_ok and not result["missing"]
    return result


def inspect_kit(kit_dir: Path) -> Dict[str, Any]:
    """检查 packaging/twa 打包套件：文件在位、host/指纹是否还是占位。"""
    result: Dict[str, Any] = {
        "present": kit_dir.is_dir(),
        "kit_dir": str(kit_dir),
        "package_id": "",
        "host": "",
        "host_configured": False,
        "assetlinks_filled": False,
        "build_script_present": False,
        "guide_present": False,
        "keystore_present": False,
    }
    if not result["present"]:
        return result
    result["build_script_present"] = (kit_dir / "build-twa.ps1").is_file()
    result["guide_present"] = (kit_dir / "README.md").is_file()
    #: keystore 正常情况下只该出现在 Hughes 的 Windows 打包机上；
    #: 源码树里出现 = 有误入库风险，状态里如实标红（见 next_steps）。
    result["keystore_present"] = any(kit_dir.glob("*.keystore")) or any(
        kit_dir.glob("*.jks")
    )
    twa_manifest = kit_dir / "twa-manifest.json"
    if twa_manifest.is_file():
        try:
            config = json.loads(twa_manifest.read_text(encoding="utf-8"))
            result["package_id"] = str(config.get("packageId") or "")
            result["host"] = str(config.get("host") or "")
            result["host_configured"] = bool(result["host"]) and not _is_placeholder(
                result["host"]
            )
        except Exception:  # noqa: BLE001 - 坏配置按没配好处理
            pass
    assetlinks = kit_dir / "assetlinks.json"
    if assetlinks.is_file():
        try:
            links = json.loads(assetlinks.read_text(encoding="utf-8"))
            fingerprints: List[str] = []
            for item in links if isinstance(links, list) else []:
                target = item.get("target") or {}
                fingerprints.extend(target.get("sha256_cert_fingerprints") or [])
            result["assetlinks_filled"] = bool(fingerprints) and all(
                not _is_placeholder(str(fp)) for fp in fingerprints
            )
        except Exception:  # noqa: BLE001
            pass
    return result


def twa_status() -> Dict[str, Any]:
    """汇总状态：PWA 就绪度 + 套件进度 + 人话下一步。与聊天工具同口径。"""
    from src.gateway.console_static import resolve_console_dist

    pwa = inspect_pwa(resolve_console_dist())
    app_root = Path(__file__).resolve().parents[2]
    kit = inspect_kit(app_root / "packaging" / "twa")

    steps: List[str] = []
    if not pwa["ready"]:
        steps.append(
            "先修 PWA 底子：" + "；".join(pwa["missing"] or ["console 未就绪"])
        )
    if not kit["present"]:
        steps.append(
            "打包套件在源码仓库的 packaging/twa/ 里（安装包不带它）："
            "从源码里把整个 twa 文件夹复制到 Windows 电脑再往下走"
        )
    if kit["present"] and not kit["host_configured"]:
        steps.append(
            "给贾维斯准备一个 HTTPS 入口（推荐 Tailscale Serve），"
            "把 twa-manifest.json 里的 host 与 webManifestUrl 改成真实地址"
        )
    if kit["keystore_present"]:
        steps.append(
            "警告：套件目录里发现了签名密钥文件——它不该出现在源码树里，"
            "请立刻移走单独保管，并确认没有提交进仓库"
        )
    if kit["present"] and not kit["assetlinks_filled"]:
        steps.append(
            "在 Windows 上跑 build-twa.ps1 打包后，按 README 第 5 步取签名"
            "SHA-256 指纹、回填 assetlinks.json 并部署到站点 /.well-known/ 下"
        )
    if not steps:
        steps.append("配置已齐：在 Windows 上跑 build-twa.ps1 即可出安装包")

    ready_to_build = bool(
        pwa["ready"] and kit["present"] and kit["host_configured"]
    )
    return {
        "pwa": pwa,
        "kit": kit,
        "ready_to_build": ready_to_build,
        "next_steps": steps,
        "guide": (
            "完整步骤在源码 packaging/twa/README.md：HTTPS 入口 → 改两处地址 → "
            "一键打包 → 备份签名密钥 → 回填指纹部署 assetlinks → 真机验收。"
            "真机出包与安装属真机待验，本状态只报配置与文件事实。"
        ),
    }
