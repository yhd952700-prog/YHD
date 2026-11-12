"""六个页面存在性检查（只在测试目录实例上跑）。

为什么要单独写这一支：仓库现有的 ``scripts/acceptance/a11y_console_acceptance.py``
用 ``root / acceptance-pass`` 这套 dev 夹具登录，并且只读仓库、不在本轮文件域；
自升级测试目录装出来的实例，管理员是冒烟脚本新建的 ``liuhao-smoke``。这里用
Playwright 走同一套登录选择器（``#os-principal`` / ``#os-secret``），只验证
「登录后六个页面都在、点得进、点完不白屏」，不重复做无障碍专项。

退出码沿用全局三态：

* ``0`` PASS：六个页面全部可点且主区仍渲染；
* ``1`` 有差异：缺页 / 点击后白屏；
* ``2`` 参数或环境不具备（未安装 playwright、浏览器起不来）——不是通过。
"""

from __future__ import annotations

import argparse
import os

#: 与 ``scripts/acceptance/a11y_console_acceptance.py`` 保持同一份侧栏文案。
PAGES = ["总览", "贾维斯", "我的员工", "目标与任务", "工作区文件", "系统设置"]

NAV_SELECTOR = ".os-nav-item, .os-bottom-item"
LOGIN_TIMEOUT = 60_000


def _launch(playwright):  # noqa: ANN001 - playwright 类型不便在此处导入
    for kwargs in ({"channel": "msedge"}, {}):
        try:
            return playwright.chromium.launch(headless=True, **kwargs)
        except Exception:  # noqa: BLE001 - 换下一个浏览器再试
            continue
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="六个页面存在性检查")
    parser.add_argument("--base", required=True, help="例如 http://127.0.0.1:18080/")
    parser.add_argument("--principal", default="liuhao-smoke")
    parser.add_argument("--password-env", default="LIUHAO_PAGES_PASSWORD")
    args = parser.parse_args(argv)

    password = os.environ.get(args.password_env, "")
    if not password:
        print(f"[SKIP] 环境不具备：环境变量 {args.password_env} 为空")
        return 2

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[SKIP] 环境不具备：未安装 playwright")
        return 2

    with sync_playwright() as playwright:
        browser = _launch(playwright)
        if browser is None:
            print("[SKIP] 环境不具备：Edge / Chromium 都起不来")
            return 2
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(args.base, wait_until="domcontentloaded", timeout=LOGIN_TIMEOUT)
            page.wait_for_selector("#os-secret", state="visible", timeout=LOGIN_TIMEOUT)
            tab = page.locator("#os-login-tab-login")
            if tab.count() and tab.first.get_attribute("aria-selected") != "true":
                tab.first.click()
                page.wait_for_selector("#os-secret", state="visible", timeout=10_000)
            page.fill("#os-principal", args.principal)
            page.fill("#os-secret", password)
            page.locator('button[type="submit"]').first.click()
            page.wait_for_selector(".os-app", state="visible", timeout=LOGIN_TIMEOUT)
            page.wait_for_selector(NAV_SELECTOR, state="visible", timeout=LOGIN_TIMEOUT)
            page.wait_for_timeout(400)

            missing: list[str] = []
            blank: list[str] = []
            for label in PAGES:
                item = page.locator(NAV_SELECTOR).filter(has_text=label)
                if item.count() == 0:
                    missing.append(label)
                    continue
                item.first.click()
                page.wait_for_timeout(250)
                if page.locator(".os-app").count() == 0:
                    blank.append(label)
            if missing or blank:
                if missing:
                    print(f"[FAIL] 缺页：{'、'.join(missing)}")
                if blank:
                    print(f"[FAIL] 点击后白屏：{'、'.join(blank)}")
                return 1
            print(f"[PASS] 六个页面均可进入：{'、'.join(PAGES)}")
            return 0
        finally:
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
