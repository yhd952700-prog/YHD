#!/usr/bin/env python
"""高 DPI 缩放组合验收（可重跑，输出 PASS/FAIL + 关键尺寸 + 证据图）。

为什么需要它
------------
之前的验收只覆盖 100% / 150% / 200% 三档整档、单屏、固定分辨率——那是"开发
机器"的组合。真实用户会遇到更麻烦的：

* 125% / 175% 这类**非整档**缩放（Tk 的 ``tk scaling`` 变成 1.6667 / 2.3333）；
* 双屏 DPI 不同（1.25 + 1.5），窗口在两边拖动；
* 分辨率 / 远程桌面切换：1920×1080 → 1366×768 → 1280×720，以及断开远程
  桌面后回到本机高 DPI。这条最容易留下"窗口卡在屏幕外拖不回来"。

本脚本在**真实窗口**上验证这些组合：用 ``src.desktop.dpi`` 的两个查询函数
（``window_dpi`` / ``work_area``）模拟显示器环境，其余全部走产品代码路径
（``fit_window`` / ``DpiWatcher`` / ``WM_DPICHANGED`` / ``WM_DISPLAYCHANGE``）。

判定口径
--------
* 窗口外框矩形必须完全落在工作区内；
* 底部按钮既要在工作区内，也要在客户区内（不被窗口边缘裁掉）；
* 内容超出可视区时滚动条必须出现（否则内容够不着）；
* 换到更高 DPI 的显示器后，字号像素高度必须按比例变大（不得变小）。

用法
----
    python scripts/acceptance/dpi_scaling_acceptance.py            # 跑全部用例
    python scripts/acceptance/dpi_scaling_acceptance.py --list     # 列出用例
    python scripts/acceptance/dpi_scaling_acceptance.py --case wizard-125

对安装副本跑同一套用例（确认装出来的 desktop 代码是否落后于源码）：

    set LIUHAO_DPI_TARGET_ROOT=D:\\LiuHao
    python scripts/acceptance/dpi_scaling_acceptance.py

此时只把 ``D:\\LiuHao`` 放进 ``sys.path``（不回退到源码），证据图统一加
``installed-`` 前缀，报告写到 ``docs/dpi-evidence/installed-copy-report.json``。

证据：截图按组合命名写入 ``docs/dpi-evidence/``，机器可读汇总写入
``docs/dpi-evidence/dpi-acceptance-report.json``。

退出码：``0`` 全部 PASS；``1`` 有用例 FAIL；``2`` 参数错误（未知用例）；
``3`` 当前不是交互式桌面会话（无头 / 服务会话），**跳过、不做判定**。

约束：只 monkeypatch 显示器环境，不改产品代码、文案与跳转；不写 ``dist\\``，
不运行安装器。
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EVIDENCE_DIR = REPO / "docs" / "dpi-evidence"

#: 被测代码的根目录。默认源码仓库；设 ``LIUHAO_DPI_TARGET_ROOT=D:\LiuHao``
#: 时改为对**安装副本**跑同一套用例（用于确认装出来的 desktop 代码是否落后）。
TARGET_ROOT = Path(os.environ.get("LIUHAO_DPI_TARGET_ROOT") or REPO).expanduser().resolve()
IS_INSTALLED = TARGET_ROOT != REPO

#: 安装副本的证据单独命名，避免和源码结论混在一起。
_SHOT_PREFIX = "installed-" if IS_INSTALLED else ""
REPORT_PATH = EVIDENCE_DIR / ("installed-copy-report.json" if IS_INSTALLED else "dpi-scaling-report.json")

#: 用例在子进程里跑，直接执行时 ``sys.path[0]`` 是 ``scripts/acceptance``。
#: 只把**被测根**放进 ``sys.path``：否则安装副本缺模块时会静默回退到源码，
#: 得到一个假的绿。
if str(TARGET_ROOT) not in sys.path:
    sys.path.insert(0, str(TARGET_ROOT))

#: 任务栏高度：模拟的工作区高度 = 分辨率高度 - 该值。
TASKBAR = 40

#: 固定出一个 100% 基准：16 磅字体在该 DPI 下的最小可接受像素高度系数。
_FONT_POINT = 16


def _work(width: int, height: int) -> tuple[int, int, int, int]:
    """按"分辨率"生成工作区矩形（扣掉任务栏）。"""
    return (0, 0, int(width), int(height) - TASKBAR)


#: 用例表。``kind`` 决定用哪段流程；每项都对应一条独立的证据图。
CASES: dict[str, dict[str, object]] = {
    # -- 单屏整档 / 非整档 -------------------------------------------------
    "wizard-100": {"kind": "wizard-scale", "scale": 1.0, "screen": (1920, 1080)},
    "wizard-125": {"kind": "wizard-scale", "scale": 1.25, "screen": (1920, 1080)},
    "wizard-150": {"kind": "wizard-scale", "scale": 1.5, "screen": (1920, 1080)},
    "wizard-175": {"kind": "wizard-scale", "scale": 1.75, "screen": (1920, 1080)},
    "wizard-200": {"kind": "wizard-scale", "scale": 2.0, "screen": (1920, 1080)},
    # -- 双屏不同 DPI：125% ↔ 150% ----------------------------------------
    "dual-move-125to150": {
        "kind": "wizard-move",
        "from_scale": 1.25,
        "from_screen": (1920, 1080),
        "to_scale": 1.5,
        "to_screen": (1366, 768),
    },
    "dual-move-150to125": {
        "kind": "wizard-move",
        "from_scale": 1.5,
        "from_screen": (1366, 768),
        "to_scale": 1.25,
        "to_screen": (1920, 1080),
    },
    # -- 分辨率 / 远程桌面切换 --------------------------------------------
    "resolution-sequence": {"kind": "wizard-resolution"},
    # -- 状态窗口（主界面）------------------------------------------------
    "status-175": {"kind": "status-scale", "scale": 1.75, "screen": (1920, 1080)},
    "status-200-1280x720": {"kind": "status-scale", "scale": 2.0, "screen": (1280, 720)},
}


# ---------------------------------------------------------------------------
# 显示器环境模拟
# ---------------------------------------------------------------------------


class _Env:
    """被模拟的显示器环境：当前 DPI + 工作区（物理像素）。"""

    def __init__(self, scale: float, screen: tuple[int, int]) -> None:
        self.dpi = int(round(96 * scale))
        self.work = _work(*screen)
        self.screen = screen

    def move_to(self, scale: float, screen: tuple[int, int]) -> None:
        self.dpi = int(round(96 * scale))
        self.work = _work(*screen)
        self.screen = screen


def _install(env: _Env) -> None:
    """让 ``src.desktop.dpi`` 读到模拟环境（只替换两个只读查询函数）。"""
    if str(TARGET_ROOT) not in sys.path:
        sys.path.insert(0, str(TARGET_ROOT))
    from src.desktop import dpi as dpi_support

    dpi_support.window_dpi = lambda hwnd=None: env.dpi  # type: ignore[assignment]
    dpi_support.work_area = lambda hwnd=None: env.work  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Win32 辅助
# ---------------------------------------------------------------------------


def _user32():
    lib = ctypes.WinDLL("user32", use_last_error=True)
    lib.GetParent.argtypes = [wintypes.HWND]
    lib.GetParent.restype = wintypes.HWND
    lib.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    lib.GetWindowRect.restype = wintypes.BOOL
    lib.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    lib.SetWindowPos.restype = wintypes.BOOL
    lib.SetForegroundWindow.argtypes = [wintypes.HWND]
    lib.SetForegroundWindow.restype = wintypes.BOOL
    lib.SendMessageW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    lib.SendMessageW.restype = ctypes.c_ssize_t
    lib.GetSystemMetrics.argtypes = [ctypes.c_int]
    lib.GetSystemMetrics.restype = ctypes.c_int
    return lib


def _frame_hwnd(root) -> int:
    lib = _user32()
    hwnd = int(root.winfo_id())
    return int(lib.GetParent(wintypes.HWND(hwnd)) or hwnd)


def _frame_rect(hwnd: int) -> tuple[int, int, int, int]:
    rect = wintypes.RECT()
    _user32().GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect))
    return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)


def _visible_rect(hwnd: int) -> tuple[int, int, int, int]:
    """DWM 可见边界（不含透明缩放边框），拿不到时退回 GetWindowRect。"""
    try:
        dwm = ctypes.WinDLL("dwmapi")
        rect = wintypes.RECT()
        if dwm.DwmGetWindowAttribute(
            wintypes.HWND(hwnd), 9, ctypes.byref(rect), ctypes.sizeof(rect)
        ) == 0:
            return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
    except Exception:  # noqa: BLE001 - 旧系统没有该接口
        pass
    return _frame_rect(hwnd)


def _move_window(hwnd: int, x: int, y: int) -> None:
    # NOSIZE | NOZORDER | NOACTIVATE：只移动，不改变尺寸与焦点。
    _user32().SetWindowPos(wintypes.HWND(hwnd), None, int(x), int(y), 0, 0, 0x0001 | 0x0004 | 0x0010)


def _send(hwnd: int, msg: int, wparam: int = 0, lparam: int = 0) -> None:
    _user32().SendMessageW(wintypes.HWND(hwnd), msg, wparam, lparam)


def _pump(root, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            root.update()
        except Exception:  # noqa: BLE001 - 窗口已销毁
            return
        time.sleep(0.02)


def _wait_until(root, predicate, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            root.update()
        except Exception:  # noqa: BLE001
            return False
        if predicate():
            return True
        time.sleep(0.03)
    try:
        root.update()
    except Exception:  # noqa: BLE001
        pass
    return bool(predicate())


def _capture(hwnd: int, out: Path, screen: tuple[int, int]) -> dict[str, object]:
    """截取窗口可见区域；超出物理屏幕的部分裁掉（记录实际截取尺寸）。"""
    x, y, width, height = _visible_rect(hwnd)
    screen_w, screen_h = screen[0], screen[1]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(screen_w, x + width), min(screen_h, y + height)
    meta: dict[str, object] = {
        "path": str(out.relative_to(REPO)),
        "window_rect": [x, y, width, height],
        "captured": [x0, y0, max(0, x1 - x0), max(0, y1 - y0)],
    }
    if x1 - x0 <= 0 or y1 - y0 <= 0:
        meta["error"] = "窗口完全在物理屏幕外，无法截屏"
        return meta
    out.parent.mkdir(parents=True, exist_ok=True)
    cw, ch = x1 - x0, y1 - y0
    script = (
        "Add-Type -AssemblyName System.Drawing;"
        f"$w={cw};$h={ch};$x={x0};$y={y0};"
        "$bmp=New-Object System.Drawing.Bitmap($w,$h);"
        "$g=[System.Drawing.Graphics]::FromImage($bmp);"
        "$g.CopyFromScreen($x,$y,0,0,(New-Object System.Drawing.Size($w,$h)));"
        f"$bmp.Save('{out}',[System.Drawing.Imaging.ImageFormat]::Png);"
        "$g.Dispose();$bmp.Dispose()"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            check=True,
            capture_output=True,
            timeout=60,
        )
        meta["bytes"] = out.stat().st_size
    except Exception as exc:  # noqa: BLE001 - 截图失败不能盖住尺寸结论
        meta["error"] = f"{type(exc).__name__}: {exc}"
    return meta


def _font_linespace(root) -> int:
    """窗口内最大字号的行高（像素）。换屏后必须按比例变大。"""
    import tkinter.font as tkfont

    best = 0
    stack = [root]
    while stack:
        widget = stack.pop()
        try:
            stack.extend(widget.winfo_children())
            spec = str(widget.cget("font"))
            if not spec:
                continue
            best = max(best, int(tkfont.Font(root=root, font=spec).metrics("linespace")))
        except Exception:  # noqa: BLE001 - 控件不支持 font 或已销毁
            continue
    return best


# ---------------------------------------------------------------------------
# 通用测量
# ---------------------------------------------------------------------------


def _box(rect: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """``(x, y, w, h)`` → ``(left, top, right, bottom)``。"""
    return (rect[0], rect[1], rect[0] + rect[2], rect[1] + rect[3])


def _inside(rect: tuple[int, int, int, int], area: tuple[int, int, int, int]) -> bool:
    """两个矩形都按 ``(left, top, right, bottom)`` 比较。"""
    return (
        rect[0] >= area[0]
        and rect[1] >= area[1]
        and rect[2] <= area[2]
        and rect[3] <= area[3]
    )


def _measure(root, env: _Env, buttons: dict[str, object]) -> dict[str, object]:
    """采集窗口位置、按钮位置、字体与滚动状态。"""
    from src.desktop import dpi as dpi_support

    hwnd = _frame_hwnd(root)
    filled: dict[str, dict[str, int]] = {}
    for name, widget in buttons.items():
        filled[name] = {
            "x": int(widget.winfo_rootx()),
            "y": int(widget.winfo_rooty()),
            "w": int(widget.winfo_width()),
            "h": int(widget.winfo_height()),
            "mapped": bool(widget.winfo_ismapped()),
        }
    client = (
        int(root.winfo_rootx()),
        int(root.winfo_rooty()),
        int(root.winfo_width()),
        int(root.winfo_height()),
    )
    work = dpi_support.work_area(hwnd)
    return {
        "hwnd": hwnd,
        "dpi": env.dpi,
        "tk_scaling": round(float(root.tk.call("tk", "scaling")), 4),
        "work_area": list(work),
        "frame_rect": list(_frame_rect(hwnd)),
        "client_rect": list(client),
        "buttons": filled,
        "font_linespace": _font_linespace(root),
    }


def _check_window(
    prefix: str,
    metrics: dict[str, object],
    *,
    require_scrollbar_when_overflow: bool = True,
) -> list[dict[str, object]]:
    """把一次测量变成 PASS/FAIL 列表。"""
    checks: list[dict[str, object]] = []
    work = tuple(metrics["work_area"])  # type: ignore[arg-type]
    frame = _box(tuple(metrics["frame_rect"]))  # type: ignore[arg-type]
    client = _box(tuple(metrics["client_rect"]))  # type: ignore[arg-type]

    checks.append(
        {
            "name": f"{prefix} 窗口在工作区内",
            "ok": _inside(frame, work),
            "detail": f"frame={frame} work={work}",
        }
    )
    buttons = metrics["buttons"]  # type: ignore[assignment]
    for name, rect in buttons.items():  # type: ignore[union-attr]
        item = _box((rect["x"], rect["y"], rect["w"], rect["h"]))
        checks.append(
            {
                "name": f"{prefix} 按钮 {name} 在工作区内",
                "ok": bool(rect["mapped"]) and _inside(item, work),
                "detail": f"rect={item} mapped={rect['mapped']}",
            }
        )
        checks.append(
            {
                "name": f"{prefix} 按钮 {name} 未被窗口裁掉",
                "ok": _inside(item, client),
                "detail": f"rect={item} client={client}",
            }
        )
    return checks


def _check_scaling(prefix: str, metrics: dict[str, object], expected_dpi: int) -> list[dict[str, object]]:
    expected = round(expected_dpi / 72.0, 4)
    linespace = int(metrics["font_linespace"])  # type: ignore[arg-type]
    floor = int(0.9 * _FONT_POINT * expected_dpi / 72.0)
    return [
        {
            "name": f"{prefix} tk scaling = DPI/72",
            "ok": abs(float(metrics["tk_scaling"]) - expected) <= 0.01,  # type: ignore[arg-type]
            "detail": f"tk_scaling={metrics['tk_scaling']} 期望 {expected}（DPI {expected_dpi}）",
        },
        {
            "name": f"{prefix} 字号未按 96 DPI 渲染",
            "ok": linespace >= floor,
            "detail": f"最大行高={linespace}px 下限={floor}px（{_FONT_POINT}pt @ {expected_dpi} DPI）",
        },
    ]


def _scroll_check(root, scroll, prefix: str) -> dict[str, object]:
    try:
        inner = int(scroll.inner.winfo_reqheight())
        canvas = int(scroll.canvas.winfo_height())
        overflow = inner > canvas
        visible = bool(scroll._scrollbar_visible)
        return {
            "name": f"{prefix} 内容超出时可滚动",
            "ok": (not overflow) or visible,
            "detail": f"内容高={inner} 可视高={canvas} 溢出={overflow} 滚动条={visible}",
        }
    except Exception as exc:  # noqa: BLE001
        return {"name": f"{prefix} 内容超出时可滚动", "ok": False, "detail": f"读取失败：{exc}"}


# ---------------------------------------------------------------------------
# 用例：首启向导
# ---------------------------------------------------------------------------


def _open_wizard(env: _Env):
    _install(env)
    from scripts.first_run_gui import _WizardWindow

    log_dir = Path(tempfile.gettempdir()) / "lh-dpi-acceptance"
    log_dir.mkdir(parents=True, exist_ok=True)
    window = _WizardWindow(
        Path("D:/LiuHaoData"),
        lambda values: (False, "预览模式，不提交"),
        log_dir / "first_run.log",
        port=8080,
    )
    # 第 3 页（模型/端口）内容最长，最能暴露"按钮被挤出屏幕"。
    window._show(2)
    _pump(window.root, 0.9)
    return window


def _wizard_case(case: str, spec: dict[str, object]) -> dict[str, object]:
    env = _Env(float(spec["scale"]), tuple(spec["screen"]))  # type: ignore[arg-type]
    window = _open_wizard(env)
    root = window.root
    buttons = {"上一步": window._back, "取消": window._cancel, "下一步": window._next}
    metrics = _measure(root, env, buttons)

    checks: list[dict[str, object]] = []
    checks += _check_scaling(case, metrics, env.dpi)
    checks.append(
        {
            "name": f"{case} 进程 DPI 感知",
            "ok": _awareness() >= 2,
            "detail": f"GetProcessDpiAwareness={_awareness()}（2=Per-Monitor）",
        }
    )
    checks += _check_window(case, metrics)
    checks.append(_scroll_check(root, window._scroll, case))

    hwnd = int(metrics["hwnd"])  # type: ignore[arg-type]
    root.attributes("-topmost", True)
    _user32().SetForegroundWindow(wintypes.HWND(hwnd))
    _pump(root, 0.3)
    shot = _capture(hwnd, EVIDENCE_DIR / f"{_SHOT_PREFIX}{case}.png", env.screen)
    root.attributes("-topmost", False)

    window._stop_dpi_watcher()
    root.destroy()
    return {
        "case": case,
        "kind": "wizard-scale",
        "scale": spec["scale"],
        "screen": list(spec["screen"]),  # type: ignore[arg-type]
        "passed": all(bool(c["ok"]) for c in checks),
        "checks": checks,
        "metrics": metrics,
        "screenshots": [shot],
    }


def _awareness() -> int:
    from src.desktop import dpi as dpi_support

    value = dpi_support.process_dpi_awareness()
    return int(value) if value is not None else -1


def _wizard_move_case(case: str, spec: dict[str, object]) -> dict[str, object]:
    """双屏不同 DPI：在 A 屏开窗 → 拖到 B 屏 → 校验缩放、字体与位置。"""
    from src.desktop import dpi as dpi_support

    env = _Env(float(spec["from_scale"]), tuple(spec["from_screen"]))  # type: ignore[arg-type]
    window = _open_wizard(env)
    root = window.root
    buttons = {"上一步": window._back, "取消": window._cancel, "下一步": window._next}
    before = _measure(root, env, buttons)
    before_font = int(before["font_linespace"])  # type: ignore[arg-type]
    before_scaling = float(before["tk_scaling"])  # type: ignore[arg-type]

    # 切到 B 屏：Windows 会先更新显示器信息，再给窗口发 WM_DPICHANGED。
    env.move_to(float(spec["to_scale"]), tuple(spec["to_screen"]))  # type: ignore[arg-type]
    hwnd = int(before["hwnd"])  # type: ignore[arg-type]
    new_dpi = env.dpi
    _send(hwnd, dpi_support.WM_DPICHANGED, new_dpi | (new_dpi << 16), 0)
    expected_scaling = round(new_dpi / 72.0, 4)
    settled = _wait_until(
        root,
        lambda: abs(float(root.tk.call("tk", "scaling")) - expected_scaling) < 0.01,
        4.0,
    )
    _pump(root, 0.4)
    after = _measure(root, env, buttons)
    after_font = int(after["font_linespace"])  # type: ignore[arg-type]

    checks: list[dict[str, object]] = [
        {
            "name": f"{case} 换屏后 tk scaling 跟随新 DPI",
            "ok": settled,
            "detail": f"{before_scaling} → {after['tk_scaling']}（期望 {expected_scaling}）",
        },
    ]
    ratio = float(spec["to_scale"]) / float(spec["from_scale"])  # type: ignore[arg-type]
    floor = int(before_font * ratio * 0.85)
    if ratio > 1:
        # 换到更高 DPI 的屏幕：字号必须变大，绝不能变小。
        checks.append(
            {
                "name": f"{case} 换屏后字体不缩小",
                "ok": after_font >= before_font,
                "detail": f"行高 {before_font}px → {after_font}px（DPI {int(round(96 * float(spec['from_scale'])))} → {new_dpi}）",  # type: ignore[arg-type]
            }
        )
    checks.append(
        {
            "name": f"{case} 字号随 DPI 成比例（未过度缩小）",
            "ok": after_font >= floor,
            "detail": f"行高 {before_font}px → {after_font}px，期望 ≥ {floor}px（比例 {ratio:.4f}）",
        }
    )
    checks += _check_scaling(case, after, new_dpi)
    checks += _check_window(case, after)
    checks.append(_scroll_check(root, window._scroll, case))

    root.attributes("-topmost", True)
    _user32().SetForegroundWindow(wintypes.HWND(hwnd))
    _pump(root, 0.3)
    shot = _capture(hwnd, EVIDENCE_DIR / f"{_SHOT_PREFIX}{case}.png", env.screen)
    root.attributes("-topmost", False)

    window._stop_dpi_watcher()
    root.destroy()
    return {
        "case": case,
        "kind": "wizard-move",
        "from": [spec["from_scale"], list(spec["from_screen"])],  # type: ignore[arg-type]
        "to": [spec["to_scale"], list(spec["to_screen"])],  # type: ignore[arg-type]
        "passed": all(bool(c["ok"]) for c in checks),
        "checks": checks,
        "metrics": after,
        "screenshots": [shot],
    }


def _wizard_resolution_case(case: str) -> dict[str, object]:
    """分辨率 / 远程桌面切换：每一步都必须自愈回工作区内。"""
    from src.desktop import dpi as dpi_support

    env = _Env(1.5, (1920, 1080))
    window = _open_wizard(env)
    root = window.root
    buttons = {"上一步": window._back, "取消": window._cancel, "下一步": window._next}
    hwnd = int(_measure(root, env, buttons)["hwnd"])  # type: ignore[arg-type]

    steps = [
        # (标签, 缩放, 分辨率, 先把窗口挪到哪里, 证据图)
        ("本机 1920×1080@150%", 1.5, (1920, 1080), (760, 420), None),
        ("远程桌面 1366×768@100%", 1.0, (1366, 768), (760, 420), "res-1366x768.png"),
        ("远程桌面 1280×720@100%", 1.0, (1280, 720), (700, 300), "res-1280x720.png"),
        ("断开远程桌面 → 本机 1920×1080@150%", 1.5, (1920, 1080), (700, 300), "res-back-local-150.png"),
    ]
    checks: list[dict[str, object]] = []
    shots: list[dict[str, object]] = []
    step_metrics: list[dict[str, object]] = []
    previous_font = 0
    previous_dpi = env.dpi

    for index, (label, scale, screen, position, shot_name) in enumerate(steps):
        if index > 0:
            # 模拟"用户把窗口拖到了右下角/边缘，然后分辨率变了"。
            _move_window(hwnd, position[0], position[1])
            _pump(root, 0.15)
            previous_font = int(_font_linespace(root))
            previous_dpi = env.dpi
            env.move_to(scale, screen)
            # Windows 在分辨率变化时广播 WM_DISPLAYCHANGE；DPI 变了再补 WM_DPICHANGED。
            _send(hwnd, dpi_support.WM_DISPLAYCHANGE)
            if env.dpi != previous_dpi:
                _send(hwnd, dpi_support.WM_DPICHANGED, env.dpi | (env.dpi << 16), 0)

        expected = round(env.dpi / 72.0, 4)
        healed = _wait_until(
            root,
            lambda: abs(float(root.tk.call("tk", "scaling")) - expected) < 0.02
            and _inside(
                _box(_frame_rect(hwnd)),
                (0, 0, screen[0], screen[1] - TASKBAR),
            ),
            5.0,
        )
        _pump(root, 0.35)
        metrics = _measure(root, env, buttons)
        step_metrics.append({"step": label, **metrics})
        checks += _check_window(f"{case} · {label}", metrics)
        checks.append(_scroll_check(root, window._scroll, f"{case} · {label}"))
        checks.append(
            {
                "name": f"{case} · {label} 自愈到工作区内",
                "ok": healed,
                "detail": f"frame={tuple(metrics['frame_rect'])} work={tuple(metrics['work_area'])}",  # type: ignore[arg-type]
            }
        )
        if index > 0 and previous_font:
            # 字号按 DPI 比例变化是对的；判据是"不低于按比例的 85%"，
            # 这样分辨率降低时允许字号变小，但缩得过头（例如误按 96 DPI 渲染）会失败。
            expected_ratio = env.dpi / previous_dpi if previous_dpi else 1.0
            floor = int(previous_font * expected_ratio * 0.85)
            checks.append(
                {
                    "name": f"{case} · {label} 字体未异常缩小",
                    "ok": int(metrics["font_linespace"]) >= floor,  # type: ignore[arg-type]
                    "detail": (
                        f"行高 {previous_font}px → {metrics['font_linespace']}px"
                        f"（DPI {previous_dpi} → {env.dpi}，下限 {floor}px）"
                    ),
                }
            )
        if shot_name:
            root.attributes("-topmost", True)
            _user32().SetForegroundWindow(wintypes.HWND(hwnd))
            _pump(root, 0.25)
            shots.append(_capture(hwnd, EVIDENCE_DIR / f"{_SHOT_PREFIX}{shot_name}", screen))
            root.attributes("-topmost", False)

    window._stop_dpi_watcher()
    root.destroy()
    return {
        "case": case,
        "kind": "wizard-resolution",
        "passed": all(bool(c["ok"]) for c in checks),
        "checks": checks,
        "metrics": step_metrics,
        "screenshots": shots,
    }


# ---------------------------------------------------------------------------
# 用例：状态窗口
# ---------------------------------------------------------------------------


def _status_case(case: str, spec: dict[str, object]) -> dict[str, object]:
    env = _Env(float(spec["scale"]), tuple(spec["screen"]))  # type: ignore[arg-type]
    _install(env)
    import tkinter as tk

    from src.desktop import dpi as dpi_support
    from src.desktop.app import DesktopApp

    app = DesktopApp()
    root = tk.Tk()
    dpi_support.apply_tk_scaling(root)
    root.title("status-acceptance")
    app._root = root
    app._build_ui()
    app._apply_geometry(center=True)
    app._dpi_watcher = dpi_support.DpiWatcher(root, app._on_dpi_changed)
    app._dpi_watcher.start()
    _pump(root, 0.9)

    buttons = {}
    stack = [root]
    while stack:
        widget = stack.pop()
        try:
            stack.extend(widget.winfo_children())
        except Exception:  # noqa: BLE001
            continue
        if isinstance(widget, tk.Button) and widget.winfo_ismapped():
            buttons[str(widget.cget("text"))] = widget
    metrics = _measure(root, env, buttons)
    checks: list[dict[str, object]] = []
    checks += _check_scaling(case, metrics, env.dpi)
    checks += _check_window(case, metrics)
    if app._scroll is not None:
        checks.append(_scroll_check(root, app._scroll, case))

    hwnd = int(metrics["hwnd"])  # type: ignore[arg-type]
    root.attributes("-topmost", True)
    _user32().SetForegroundWindow(wintypes.HWND(hwnd))
    _pump(root, 0.3)
    shot = _capture(hwnd, EVIDENCE_DIR / f"{_SHOT_PREFIX}{case}.png", env.screen)
    root.attributes("-topmost", False)

    if app._dpi_watcher is not None:
        app._dpi_watcher.stop()
        app._dpi_watcher = None
    root.destroy()
    return {
        "case": case,
        "kind": "status-scale",
        "scale": spec["scale"],
        "screen": list(spec["screen"]),  # type: ignore[arg-type]
        "passed": all(bool(c["ok"]) for c in checks),
        "checks": checks,
        "metrics": metrics,
        "screenshots": [shot],
    }


# ---------------------------------------------------------------------------
# 调度
# ---------------------------------------------------------------------------


def run_case(case: str) -> dict[str, object]:
    spec = CASES[case]
    kind = spec["kind"]
    if kind == "wizard-scale":
        return _wizard_case(case, spec)
    if kind == "wizard-move":
        return _wizard_move_case(case, spec)
    if kind == "wizard-resolution":
        return _wizard_resolution_case(case)
    if kind == "status-scale":
        return _status_case(case, spec)
    raise SystemExit(f"未知用例类型：{kind}")


def _print_case(result: dict[str, object]) -> None:
    status = "PASS" if result["passed"] else "FAIL"
    metrics = result.get("metrics")
    detail = ""
    if isinstance(metrics, dict) and "frame_rect" in metrics:
        detail = (
            f"frame={tuple(metrics['frame_rect'])} work={tuple(metrics['work_area'])} "  # type: ignore[arg-type]
            f"tk={metrics['tk_scaling']} 行高={metrics['font_linespace']}px"
        )
    elif isinstance(metrics, list) and metrics:
        steps = [m for m in metrics if isinstance(m, dict)]
        if steps:
            last = steps[-1]
            detail = (
                f"{steps[0].get('step')} → {last.get('step')} "
                f"frame={tuple(last.get('frame_rect', []))} "  # type: ignore[arg-type]
                f"tk={last.get('tk_scaling')} 行高={last.get('font_linespace')}px"
            )
    print(f"[{status}] {result['case']:<22} {detail}")
    for check in result["checks"]:  # type: ignore[index]
        if not check["ok"]:
            print(f"         - {check['name']}：{check['detail']}")
    for shot in result.get("screenshots", []):  # type: ignore[union-attr]
        print(f"         证据：{shot.get('path')} {shot.get('captured')}")


def _target_diff() -> list[dict[str, object]]:
    """安装副本相对源码的 DPI 能力差异（只读文件检查，不执行安装代码）。"""
    rows: list[dict[str, object]] = []
    for rel, label in (
        ("src/desktop/dpi.py", "DPI 适配模块 dpi.py"),
        ("scripts/first_run_gui.py", "图形化首启向导 first_run_gui.py"),
    ):
        rows.append({"path": rel, "label": label, "present": (TARGET_ROOT / rel).is_file()})
    app = TARGET_ROOT / "src" / "desktop" / "app.py"
    text = app.read_text(encoding="utf-8", errors="replace") if app.is_file() else ""
    for token, label in (
        ("fit_window", "app.py 调用 fit_window 自适应尺寸"),
        ("minsize(1, 1)", "app.py 解除旧 minsize 下限"),
        ("WM_DISPLAYCHANGE", "app.py 处理分辨率变化"),
    ):
        rows.append({"path": "src/desktop/app.py", "label": label, "present": token in text})
    return rows


def _interactive_session() -> tuple[bool, str]:
    """当前进程是否在可创建真实窗口的交互式桌面会话里。

    Windows 服务会话（session 0）或非 ``WinSta0`` 窗口站没有可见桌面，Tk 窗口
    映射不出来，所有尺寸结论都无意义。这种情况必须**显式跳过**，不能把"窗口
    建不出来"当成"DPI 缩放假失败"，也不能给出一个绿。

    这里只做进程级探测（不创建 Tk 窗口）：提前建 Tk 会锁定进程 DPI 感知，
    导致后续用例无法再显式设置 Per-Monitor V2。
    """
    if os.name != "nt":
        return True, f"非 Windows 平台（{os.name}），按交互式处理"
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.GetProcessWindowStation.restype = wintypes.HANDLE
        user32.GetUserObjectInformationW.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        user32.GetUserObjectInformationW.restype = wintypes.BOOL
        kernel32.ProcessIdToSessionId.argtypes = [
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.ProcessIdToSessionId.restype = wintypes.BOOL

        station = user32.GetProcessWindowStation()
        if not station:
            return False, "拿不到进程窗口站句柄"
        buffer = ctypes.create_unicode_buffer(256)
        needed = wintypes.DWORD(0)
        ok = user32.GetUserObjectInformationW(
            station,
            2,  # UOI_NAME
            ctypes.cast(buffer, ctypes.c_void_p),
            ctypes.sizeof(buffer),
            ctypes.byref(needed),
        )
        name = buffer.value if ok else ""
        if name.lower() != "winsta0":
            return False, f"当前窗口站为 {name or '未知'}，不是交互式 WinSta0"

        session = wintypes.DWORD(0)
        if kernel32.ProcessIdToSessionId(
            kernel32.GetCurrentProcessId(), ctypes.byref(session)
        ) and session.value == 0:
            return False, "运行在 session 0（服务会话），没有可交互桌面"
    except Exception as exc:  # noqa: BLE001 - 探测失败时按不可验证处理
        return False, f"交互性检测失败：{type(exc).__name__}: {exc}"
    return True, f"窗口站 {name}，session {session.value}"


def _write_skip_report(reason: str) -> None:
    """无头/服务会话下用「跳过」覆盖旧报告，避免上一次的绿被当成本次结论。"""
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(
            {
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "target_root": str(TARGET_ROOT),
                "skipped": True,
                "reason": reason,
                "passed": 0,
                "total": 0,
                "cases": [],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _run_all(cases: list[str]) -> int:
    ready, reason = _interactive_session()
    if not ready:
        _write_skip_report(reason)
        print(f"[SKIP] 高 DPI 验收需要交互式桌面会话：{reason}")
        print("       无头 / 服务会话下窗口无法映射，本轮不做判定；请在登录的桌面会话里重跑。")
        print(f"       报告：{REPORT_PATH.relative_to(REPO)}（已标记 skipped）")
        return 3

    if IS_INSTALLED:
        print(f"被测根（安装副本）：{TARGET_ROOT}")
        for row in _target_diff():
            print(f"  {'有' if row['present'] else '缺'}  {row['label']}（{row['path']}）")
        print()

    results: list[dict[str, object]] = []
    for case in cases:
        # 每个用例独立进程：DPI 感知与 Tk 都是进程级状态，混跑会互相污染结论。
        completed = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--case", case],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(REPO),
            timeout=180,
        )
        payload = ""
        for line in reversed(completed.stdout.strip().splitlines()):
            if line.startswith("{"):
                payload = line
                break
        if not payload:
            results.append(
                {
                    "case": case,
                    "passed": False,
                    "checks": [
                        {
                            "name": f"{case} 子进程产出结果",
                            "ok": False,
                            "detail": (completed.stderr or completed.stdout or "无输出")[-800:],
                        }
                    ],
                    "metrics": {},
                    "screenshots": [],
                }
            )
            continue
        results.append(json.loads(payload))

    print()
    for result in results:
        _print_case(result)

    passed = sum(1 for item in results if item["passed"])
    total = len(results)
    target_diff = _target_diff() if IS_INSTALLED else []
    if not IS_INSTALLED:
        verdict = "源码仓库"
    elif passed == total:
        verdict = "安装副本已包含本轮 DPI 修复"
    else:
        verdict = "安装副本为旧版本，本轮修复未进包"
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(
            {
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "target_root": str(TARGET_ROOT),
                "verdict": verdict,
                "install_diff": target_diff,
                "screen": [
                    ctypes.windll.user32.GetSystemMetrics(0),
                    ctypes.windll.user32.GetSystemMetrics(1),
                ],
                "passed": passed,
                "total": total,
                "cases": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n汇总：{passed}/{total} 用例 PASS")
    print(f"结论：{verdict}")
    print(f"报告：{REPORT_PATH.relative_to(REPO)}")
    return 0 if passed == total else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="高 DPI 缩放组合验收")
    parser.add_argument("--list", action="store_true", help="列出全部用例")
    parser.add_argument("--case", help="只跑一个用例（直接输出 JSON，供调度器解析）")
    args = parser.parse_args(argv)

    if args.list:
        for name, spec in CASES.items():
            print(f"{name:<22} {spec}")
        return 0

    ready, reason = _interactive_session()
    if not ready:
        _write_skip_report(reason)
        if args.case:
            print(
                json.dumps(
                    {
                        "case": args.case,
                        "passed": False,
                        "skipped": True,
                        "reason": reason,
                        "checks": [],
                        "metrics": {},
                        "screenshots": [],
                        "target_root": str(TARGET_ROOT),
                    },
                    ensure_ascii=False,
                )
            )
        else:
            print(f"[SKIP] 高 DPI 验收需要交互式桌面会话：{reason}")
            print("       无头 / 服务会话下窗口无法映射，本轮不做判定；请在登录的桌面会话里重跑。")
        return 3

    if args.case:
        if args.case not in CASES:
            print(f"未知用例：{args.case}；可用：{', '.join(CASES)}", file=sys.stderr)
            return 2
        result = run_case(args.case)
        result["target_root"] = str(TARGET_ROOT)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["passed"] else 1
    return _run_all(list(CASES))


if __name__ == "__main__":
    raise SystemExit(main())
