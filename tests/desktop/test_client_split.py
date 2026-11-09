"""三端分流回归：桌面壳身份标记、浏览器/手机端差异、桌面端不自降级。

本轮验收的四个硬约束：
  1. 桌面壳用自定义 User-Agent 标明身份（前端读 UA，不靠窗口尺寸猜）；
  2. 原生窗口的最小尺寸存在，窗口再被拖小也不会退回"网页版"；
  3. 后端未就绪时的错误页有重试与等待说明，且**没有**「在浏览器中打开」；
  4. 网页端的「下载桌面版」入口只由身份决定：``data-client=browser`` 且
     非 ``data-mode=mobile``；桌面壳与手机端都不会出现，浏览器拉宽也不会消失。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.desktop import native_window, tray  # noqa: E402

CONSOLE_SRC = REPO_ROOT / "apps" / "console" / "console" / "src"


def test_desktop_ua_token_is_declared_and_appended() -> None:
    assert native_window.DESKTOP_UA_TOKEN.startswith("LiuHaoDesktopShell/")
    assert native_window.DESKTOP_UA_TOKEN in native_window.DESKTOP_USER_AGENT
    # 保留常规 Windows UA 外形，避免被当成手机/爬虫。
    assert "Windows NT" in native_window.DESKTOP_USER_AGENT


def test_native_window_passes_user_agent_to_webview() -> None:
    captured: dict[str, object] = {}

    class FakeWebview:
        @staticmethod
        def start(**kwargs: object) -> None:
            captured.update(kwargs)

    window = native_window.NativeWindow(
        title="鎏灏",
        storage_dir=Path("/tmp/liuhao-test-webview"),
        js_api=None,
    )
    window._webview = FakeWebview()
    window._window = object()
    window.start()

    assert captured["user_agent"] == native_window.DESKTOP_USER_AGENT
    assert captured["gui"] == "edgechromium"


def test_native_window_has_minimum_size() -> None:
    assert native_window.MIN_WINDOW_SIZE == (960, 640)


def test_error_page_waits_with_eta_and_retry_but_no_browser_fallback() -> None:
    page = native_window.error_page("鎏灏启动超时", "等待 60s 仍未就绪（最后阶段：启动网关）。")
    assert "重试检查" in page
    assert "10–30 秒" in page and "60 秒" in page
    assert "本地服务（网关）就绪" in page
    assert "在浏览器中打开" not in page


def test_native_tray_menu_excludes_browser_entry() -> None:
    native_items = tray.menu_items_for(include_browser=False)
    browser_items = tray.menu_items_for(include_browser=True)

    assert all(action != tray.ACTION_OPEN_BROWSER for _id, action, _label in native_items)
    assert any(action == tray.ACTION_OPEN_BROWSER for _id, action, _label in browser_items)
    # 除浏览器入口外，其余菜单项一个不少（顺序也不变）。
    assert [item for item in browser_items if item[1] != tray.ACTION_OPEN_BROWSER] == list(
        native_items
    )


def test_native_status_window_has_no_browser_button() -> None:
    source = (REPO_ROOT / "src" / "desktop" / "app.py").read_text(encoding="utf-8")
    assert "text=LABEL_OPEN_BROWSER" not in source


def test_frontend_identity_token_matches_desktop_shell() -> None:
    mode_ts = (CONSOLE_SRC / "lib" / "mode.ts").read_text(encoding="utf-8")
    match = re.search(r"DESKTOP_SHELL_UA_TOKEN\s*=\s*'([^']+)'", mode_ts)
    assert match, "前端 mode.ts 必须声明 DESKTOP_SHELL_UA_TOKEN"
    token = match.group(1)
    assert native_window.DESKTOP_UA_TOKEN.startswith(token)
    assert "navigator.userAgent" in mode_ts
    assert "dataset.client" in mode_ts


def test_browser_identity_never_reports_desktop() -> None:
    """浏览器即使宽度 >= 1280 也只是 web 身份，不能自称桌面端。"""
    mode_ts = (CONSOLE_SRC / "lib" / "mode.ts").read_text(encoding="utf-8")
    assert "export function detectClient" in mode_ts
    assert "return w < MOBILE_MAX_WIDTH ? 'mobile' : 'web'" in mode_ts

    login = (CONSOLE_SRC / "components" / "Login.tsx").read_text(encoding="utf-8")
    assert "CLIENT_LABEL[detectClient()]" in login


def test_download_entry_is_web_only() -> None:
    css = (CONSOLE_SRC / "os.css").read_text(encoding="utf-8")
    # 按身份判定：浏览器拉宽到 >=1280（data-mode=desktop）仍要有下载入口。
    assert "html[data-client='browser']:not([data-mode='mobile']) .os-download-link" in css
    assert "html[data-mode='web'][data-client='browser'] .os-download-link" not in css
    # 默认隐藏：桌面壳与手机端不会渲染出入口。
    block = css.split(".os-download-link {", 1)[1].split("}", 1)[0]
    assert "display: none" in block

    login = (CONSOLE_SRC / "components" / "Login.tsx").read_text(encoding="utf-8")
    shell = (CONSOLE_SRC / "components" / "Shell.tsx").read_text(encoding="utf-8")
    assert "DesktopDownloadLink" in login
    assert "DesktopDownloadLink" in shell


def test_download_link_points_to_release_page() -> None:
    component = (CONSOLE_SRC / "components" / "DesktopDownloadLink.tsx").read_text(
        encoding="utf-8"
    )
    assert "https://github.com/yhd952700-prog/YHD/releases" in component
