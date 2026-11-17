"""首次启动成功后，在 Windows 桌面上建一个名叫「贾维斯」的快捷方式。

为什么要这个模块
----------------
一键安装包是 zip 解压即用，没有安装器，所以快捷方式不能靠安装器建，
只能由程序自己在第一次启动成功时建。图标用小铁桌宠本人的样子
（``src/desktop/assets/xiaotie.ico``，由桌宠原图帧转来，见
``scripts/make_xiaotie_ico.py``），不是品牌线那张扁平机器人头
（``assets/liuhao.ico``，那是安装器/应用图标）。

触发点（两个真实启动入口都接，缺一不可）：
  * 桌面壳：``src/desktop/app.py`` —— ``_apply_boot_result`` 在启动门
    ``console_ok`` 之后调用（后台线程，不卡界面）；
  * 命令行启动器：``scripts/start_liuhao.py`` —— 网关 ``/v1/startup``
    就绪且自己的网关子进程还活着之后调用（``LiuHao.bat`` 排障入口也
    走这条路）。

只建一次：桌面上已有同名 ``贾维斯.lnk`` 就跳过；用户删掉后下次启动
成功会重新建回来。启动失败不建（钩子只在成功分支调用）。

实现：PowerShell + ``WScript.Shell`` COM（验收脚本
``scripts/acceptance/install_smoke_e2e.ps1`` 的 ``Get-ShortcutInfo``
用的就是同一路子）。路径以参数传，不拼进脚本字符串；脚本文件以
UTF-8 + BOM 落临时文件，避免中文用户名/中文路径在 PowerShell 5.1
下乱码。本模块在任何情况下都不抛异常、不拦启动：失败只记日志，
返回 ``status="failed"``。
"""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

from . import REPO_ROOT

logger = logging.getLogger(__name__)

#: 桌面快捷方式的名字（Joseph 定：就叫“贾维斯”）。
SHORTCUT_NAME = "贾维斯"
SHORTCUT_FILENAME = f"{SHORTCUT_NAME}.lnk"

#: 一键包的双击入口（快捷方式的目标）。
TARGET_BAT = "start-jarvis.bat"

#: 小铁桌宠图标（随 ``src`` 一起进包，见 ``packaging/stage_payload.py`` 的
#: SOURCE_DIRS）；找不到时退回品牌 ICO，再没有就建无图标快捷方式。
XIAOTIE_ICON = Path("src") / "desktop" / "assets" / "xiaotie.ico"
FALLBACK_ICON = Path("assets") / "liuhao.ico"

#: 建快捷方式的 PowerShell 超时（秒）：建一个 .lnk 是毫秒级操作，
#: 超时只防 PowerShell 被安全软件挂住拖垮启动。
_POWERSHELL_TIMEOUT = 15.0


def _is_windows() -> bool:
    """当前平台是不是 Windows（可 monkeypatch，供 Linux 下测 Windows 分支）。"""
    return os.name == "nt"


def _no_window_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if _is_windows() else 0


def supported() -> bool:
    """当前平台能不能建 Windows 桌面快捷方式。"""
    return _is_windows()


def desktop_dir() -> Path | None:
    """当前用户的桌面目录（OneDrive 重定向的桌面也能正确拿到）。

    先走 SHGetFolderPathW（CSIDL_DESKTOPDIRECTORY，会跟随 OneDrive 的
    桌面重定向），拿不到再退回 ``%USERPROFILE%\\Desktop``；都拿不到
    返回 None。
    """
    if not _is_windows():
        return None
    try:
        buf = ctypes.create_unicode_buffer(260)
        hr = ctypes.windll.shell32.SHGetFolderPathW(None, 0x0000, None, 0, buf)
        if hr == 0 and buf.value:
            return Path(buf.value)
    except Exception:  # noqa: BLE001 - 拿不到就走退路，不拦启动
        logger.debug("SHGetFolderPathW 拿桌面目录失败，走 USERPROFILE 退路", exc_info=True)
    profile = (os.environ.get("USERPROFILE") or "").strip()
    if profile:
        candidate = Path(profile) / "Desktop"
        if candidate.is_dir():
            return candidate
    return None


def shortcut_path(desktop: Path | None = None) -> Path | None:
    """桌面快捷方式的完整路径（``<桌面>\\贾维斯.lnk``）。"""
    folder = desktop if desktop is not None else desktop_dir()
    if folder is None:
        return None
    return folder / SHORTCUT_FILENAME


def target_bat(app_root: Path | None = None) -> Path | None:
    """快捷方式的目标：一键包双击入口 ``start-jarvis.bat``（必须真实存在）。"""
    root = Path(app_root) if app_root is not None else REPO_ROOT
    target = root / TARGET_BAT
    return target if target.is_file() else None


def icon_path(app_root: Path | None = None) -> Path | None:
    """快捷方式的图标：小铁桌宠 ICO，缺失退回品牌 ICO，再缺失返回 None。"""
    root = Path(app_root) if app_root is not None else REPO_ROOT
    xiaotie = root / XIAOTIE_ICON
    if xiaotie.is_file():
        return xiaotie
    fallback = root / FALLBACK_ICON
    if fallback.is_file():
        logger.warning("小铁图标 %s 缺失，快捷方式退回品牌图标 %s", xiaotie, fallback)
        return fallback
    logger.warning("小铁图标与品牌图标都不在，快捷方式将没有自定义图标")
    return None


def build_powershell_script() -> str:
    """建 .lnk 的 PowerShell 脚本（路径全是参数，不拼进脚本，不怕中文/空格）。

    ``$lnk.IconLocation`` 只在给了图标时才写：不写就是 Windows 默认图标，
    不会留下一个指向不存在文件的坏图标引用。
    """
    return (
        "param([string]$Target, [string]$Shortcut, [string]$Icon, [string]$WorkingDir)\n"
        "$shell = New-Object -ComObject WScript.Shell\n"
        "$lnk = $shell.CreateShortcut($Shortcut)\n"
        "$lnk.TargetPath = $Target\n"
        "$lnk.WorkingDirectory = $WorkingDir\n"
        "$lnk.Description = '贾维斯'\n"
        "if ($Icon) { $lnk.IconLocation = $Icon }\n"
        "$lnk.Save()\n"
    )


def _run_powershell(script: str, target: Path, shortcut: Path, icon: Path | None, working_dir: Path) -> tuple[str | None, str | None]:
    """把脚本写进 UTF-8 + BOM 的临时 .ps1 再跑，返回 ``(stdout, error)``。"""
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".ps1", encoding="utf-8-sig", delete=False
    )
    tmp_path = Path(tmp.name)
    try:
        tmp.write(script)
        tmp.close()
        args = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(tmp_path),
            "-Target",
            str(target),
            "-Shortcut",
            str(shortcut),
            "-Icon",
            str(icon) if icon is not None else "",
            "-WorkingDir",
            str(working_dir),
        ]
        try:
            completed = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=_POWERSHELL_TIMEOUT,
                creationflags=_no_window_flags(),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return None, f"{type(exc).__name__}: {exc}"
        if completed.returncode != 0:
            return None, (completed.stderr or "").strip()[:300] or f"powershell 退出码 {completed.returncode}"
        return completed.stdout.strip(), None
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            logger.debug("删除临时 PowerShell 脚本失败：%s", tmp_path, exc_info=True)


Runner = Callable[[str, Path, Path, Path | None, Path], tuple[str | None, str | None]]


def ensure_desktop_shortcut(
    app_root: Path | None = None,
    *,
    desktop: Path | None = None,
    runner: Runner | None = None,
) -> dict[str, Any]:
    """确保桌面上有「贾维斯」快捷方式；只建一次，永不抛异常。

    返回 ``{"status": ..., "path": ..., "reason": ...}``：

    * ``created`` —— 本次新建；
    * ``already_exists`` —— 桌面上已经有同名快捷方式，没动它；
    * ``skipped`` —— 没建（``reason`` 是 ``not_windows`` / ``no_desktop`` /
      ``no_target`` 之一：非 Windows、拿不到桌面目录、一键包入口不存在）；
    * ``failed`` —— PowerShell 建失败（``reason`` 是报错，启动不受影响）。
    """
    root = Path(app_root) if app_root is not None else REPO_ROOT
    if not _is_windows():
        return {"status": "skipped", "path": None, "reason": "not_windows"}
    link = shortcut_path(desktop)
    if link is None:
        return {"status": "skipped", "path": None, "reason": "no_desktop"}
    if link.is_file():
        return {"status": "already_exists", "path": str(link), "reason": None}
    target = target_bat(root)
    if target is None:
        return {"status": "skipped", "path": str(link), "reason": "no_target"}
    icon = icon_path(root)
    run = runner if runner is not None else _run_powershell
    try:
        _, error = run(build_powershell_script(), target, link, icon, root)
    except Exception as exc:  # noqa: BLE001 - 建快捷方式失败绝不能拦启动
        logger.warning("创建桌面快捷方式失败：%s", exc, exc_info=True)
        return {"status": "failed", "path": str(link), "reason": f"{type(exc).__name__}: {exc}"}
    if error:
        logger.warning("创建桌面快捷方式失败：%s", error)
        return {"status": "failed", "path": str(link), "reason": error}
    if not link.is_file():
        logger.warning("PowerShell 退出码正常，但桌面上没出现 %s", link)
        return {"status": "failed", "path": str(link), "reason": "建完没出现快捷方式文件"}
    logger.info("已在桌面创建快捷方式：%s -> %s", link, target)
    return {"status": "created", "path": str(link), "reason": None}


def ensure_on_first_success(app_root: Path | None = None) -> dict[str, Any]:
    """供两个启动入口调用的薄壳：只记日志，永不抛异常。"""
    try:
        result = ensure_desktop_shortcut(app_root)
    except Exception as exc:  # noqa: BLE001 - 最后一道防线
        logger.warning("创建桌面快捷方式失败：%s", exc, exc_info=True)
        return {"status": "failed", "path": None, "reason": f"{type(exc).__name__}: {exc}"}
    if result["status"] == "created":
        logger.info("首次启动成功，已在桌面创建「贾维斯」快捷方式")
    elif result["status"] == "failed":
        logger.warning("首次启动成功，但桌面快捷方式没建成：%s", result.get("reason"))
    return result
