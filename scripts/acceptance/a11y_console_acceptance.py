#!/usr/bin/env python3
"""控制台可访问性验收 —— 真浏览器跑一遍关键路径。

覆盖三条真实用户才会踩到的线：
1. 屏幕阅读器等价面：未命名控件必须为 0，页面切换必须有 aria-live 播报；
2. 焦点管理：登录后焦点进主区，建员工 / 派活 / 切页过程中焦点不得掉到 body；
3. Windows 高对比度：``forced-colors: active`` 下按钮、边框、当前页仍然可辨。

流程：登录 → 切 6 个页面 → 建员工（先空名必填失败，再成功）→ 派活。

退出码（与 scripts/acceptance 其余脚本一致）
-------------------------------------------
0  全部 PASS
1  有检查失败
2  环境不具备（Playwright 未安装 / 浏览器起不来 / dev server 起不来）
    —— 跑不起来永远不算通过。

用法
----
    python scripts/acceptance/a11y_console_acceptance.py
    python scripts/acceptance/a11y_console_acceptance.py --url http://127.0.0.1:5173/

只读：不改源码、不写 ``dist\\``；优先复用已在跑的控制台，没有才临时起一个，
跑完只关自己起的那一个。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
CONSOLE_DIR = REPO / "apps" / "console" / "console"
DEFAULT_PORT = 18305

#: 登录后的 6 个页面（侧栏文案）。覆盖新增的「目标与任务」与账户面。
#: 注意根账号的 my-keys 会显示成「全部 Key」，这里用对身份稳定的「工作区文件」。
PAGES = ["总览", "贾维斯", "我的员工", "目标与任务", "工作区文件", "系统设置"]

EMPLOYEE_NAME = "验收员工"
ASSIGN_INSTRUCTION = "把验收材料整理成一页"

AUTH_CONFIG: dict[str, Any] = {
    "auth_required": True,
    "methods": ["password"],
    "oauth": {},
    "secret_store": {"configured": True},
    "login_eligible_humans": 1,
    "root_account_exists": True,
    "registration_open": False,
    "token": {"ttl_seconds": 3600},
    "initialization": {"recoverable": False},
}

SESSION: dict[str, Any] = {
    "access_token": "acceptance-token",
    "principal": "root",
    "display_name": "根",
    "scope": "human",
    "permissions": ["*"],
    "expires_at": 4102444800,
    "client": "desktop",
    "is_root": True,
}

ROSTER: dict[str, Any] = {
    "totals": {"employees": 1, "kernels": 0, "layers": 0, "registry_entries": 0},
    # App.tsx 与 Overview 都会读 providers.items / registered_count，缺一项就崩。
    "providers": {"items": [], "registered_count": 0},
    "layers": [],
    "kernels": [],
    "employees": [],
    "registry_entries": 0,
}

SUMMARY: dict[str, Any] = {
    "sessions": {"count": 0},
    "audit": {"total_events": 0},
    "goals": {"count": 0},
}

ANALYTICS: dict[str, Any] = {
    "timeseries": {"available": False, "days": [], "error": ""},
    "breakdown": {
        "available": False,
        "items": [],
        "error": "",
        "excluded_noise_events": 0,
    },
}

#: 每个按钮 / 链接 / 输入的可访问名。留空即"未命名控件"。
UNNAMED_JS = """
() => {
  const nameOf = (el) => {
    const aria = (el.getAttribute('aria-label') || '').trim();
    if (aria) return aria;
    const by = el.getAttribute('aria-labelledby');
    if (by) {
      const text = by.split(/\\s+/)
        .map((id) => (document.getElementById(id)?.textContent || '').trim())
        .join(' ').trim();
      if (text) return text;
    }
    const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
    if (text) return text;
    const title = (el.getAttribute('title') || '').trim();
    if (title) return title;
    if (el.tagName === 'INPUT' || el.tagName === 'SELECT' || el.tagName === 'TEXTAREA') {
      const placeholder = (el.getAttribute('placeholder') || '').trim();
      if (placeholder) return placeholder;
      if (['submit', 'button', 'reset'].includes(el.type)) return (el.value || '').trim();
      if (el.id) {
        const label = document.querySelector(`label[for="${el.id}"]`);
        if (label && label.textContent.trim()) return label.textContent.trim();
      }
      const wrap = el.closest('label');
      if (wrap && wrap.textContent.trim()) return wrap.textContent.trim();
    }
    return '';
  };
  const nodes = Array.from(document.querySelectorAll(
    'button, a[href], input, select, textarea, [role="button"]'
  ));
  return nodes
    .filter((el) => !el.disabled && el.getAttribute('aria-hidden') !== 'true')
    .filter((el) => !nameOf(el))
    .map((el) => el.outerHTML.slice(0, 160));
}
"""

ACTIVE_JS = """
() => {
  const el = document.activeElement;
  if (!el) return { tag: 'NONE', id: '', text: '', label: '' };
  return {
    tag: el.tagName,
    id: el.id || '',
    text: (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 80),
    label: (el.getAttribute('aria-label') || '').trim(),
  };
}
"""

HC_JS = """
() => {
  const box = (selector) => {
    const el = document.querySelector(selector);
    if (!el) return null;
    const style = getComputedStyle(el);
    return {
      borderWidth: parseFloat(style.borderTopWidth) || 0,
      borderStyle: style.borderTopStyle,
      color: style.color,
      background: style.backgroundColor,
    };
  };
  const text = document.querySelector('.os-page-title');
  const navText = document.querySelector('.os-nav-text');
  return {
    forced: window.matchMedia('(forced-colors: active)').matches,
    btn: box('.os-btn'),
    currentNav: box('.os-nav-item[aria-current="page"]'),
    card: box('.os-card'),
    title: text ? getComputedStyle(text).color : '',
    titleBg: text ? getComputedStyle(document.body).backgroundColor : '',
    navText: navText ? getComputedStyle(navText).color : '',
    navBg: navText ? getComputedStyle(document.body).backgroundColor : '',
  };
}
"""


# ---------------------------------------------------------------------------
# 控制台服务
# ---------------------------------------------------------------------------


def _reachable(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= response.status < 400
    except (urllib.error.URLError, OSError):
        return False


def _start_dev_server(port: int) -> subprocess.Popen[str] | None:
    if not (CONSOLE_DIR / "node_modules").is_dir():
        return None
    # 显式绑 127.0.0.1：vite 默认只监听 localhost（Node 下常解析到 ::1）。
    command = f"npm run dev -- --host 127.0.0.1 --port {port} --strictPort"
    proc = subprocess.Popen(
        ["cmd.exe", "/c", command],
        cwd=str(CONSOLE_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    deadline = time.time() + 90
    while time.time() < deadline:
        if _reachable(f"http://127.0.0.1:{port}/"):
            return proc
        if proc.poll() is not None:
            return None
        time.sleep(1)
    return proc


def _stop_dev_server(proc: subprocess.Popen[str]) -> None:
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            timeout=20,
        )
    except Exception:  # noqa: BLE001 - 清理失败不影响验收结论
        pass


# ---------------------------------------------------------------------------
# 网络假后端（真实浏览器 + 状态化 mock）
# ---------------------------------------------------------------------------


class MockBackend:
    """最小状态化后端：建员工/派活会改变后续列表，流程才走得下去。"""

    def __init__(self) -> None:
        self.employees: list[dict[str, Any]] = []
        self.goals: list[dict[str, Any]] = []

    def respond(self, method: str, path: str, body: str | None) -> tuple[int, Any]:
        if path == "/v1/auth/config":
            return 200, AUTH_CONFIG
        if path in ("/v1/auth/login", "/v1/auth/me"):
            if path == "/v1/auth/me":
                return 200, {
                    "still_human": True,
                    "principal": "root",
                    "display_name": "根",
                    "scope": "human",
                    "expires_at": 4102444800,
                    "is_root": True,
                }
            return 200, SESSION
        if path == "/v1/accounts":
            return 200, {
                "members": [
                    {
                        "principal": "root",
                        "display_name": "根",
                        "permissions": ["*"],
                        "status": "active",
                    }
                ],
                "count": 1,
            }
        if path == "/v1/notifications":
            return 200, {"notifications": [], "items": [], "count": 0, "unread": 0}
        if path == "/v1/dashboard/roster":
            return 200, ROSTER
        if path == "/v1/dashboard/summary":
            return 200, SUMMARY
        if path == "/v1/dashboard/activity":
            return 200, {"activity": [], "count": 0}
        if path == "/v1/dashboard/analytics":
            return 200, ANALYTICS
        if path == "/v1/employees/capabilities":
            return 200, {"capabilities": [], "count": 0}
        if path == "/v1/employees":
            if method == "POST":
                try:
                    payload = json.loads(body or "{}")
                except json.JSONDecodeError:
                    payload = {}
                employee = {
                    "name": str(payload.get("name") or EMPLOYEE_NAME),
                    "agent_count": 1,
                    # 至少一个未暂停的 agent，员工才可接活（employeeAssignable）。
                    "agents": [
                        {"id": "agent-acceptance-1", "name": "验收执行体", "status": "active"}
                    ],
                    "role_description": str(payload.get("role_description") or ""),
                    "default_capabilities": [],
                    "employment_status": "active",
                    "read_only": False,
                }
                self.employees.append(employee)
                return 201, employee
            return 200, {
                "agents": [],
                "count": len(self.employees),
                "employee_count": len(self.employees),
                "stats": {
                    "total_employees": len(self.employees),
                    "active_employees": len(self.employees),
                },
                "employees": self.employees,
            }
        if path == "/v1/goals/assign":
            goal = {
                "goal_id": "goal-acceptance-1",
                "state": "running",
                "natural_language": ASSIGN_INSTRUCTION,
            }
            self.goals.append(goal)
            return 200, goal
        if path == "/v1/goals":
            return 200, {"goals": self.goals, "count": len(self.goals)}
        if path.startswith("/v1/goals/"):
            return 200, {
                "goal_id": path.rsplit("/", 1)[-1],
                "state": "running",
                "natural_language": ASSIGN_INSTRUCTION,
                "scope": "employee",
                "correlation_id": "corr-acceptance",
                "replan_count": 0,
                "replan_suggested": False,
                "trace": [],
                "tasks": [],
            }
        if path == "/v1/files":
            # Files 页会读 listing.data.entries.length，缺 entries 会直接崩。
            return 200, {"entries": [], "count": 0, "path": "", "root": ""}
        if path == "/v1/files/content":
            return 200, {"content": "", "rel_path": "", "encoding": "utf-8"}
        if path == "/v1/projects":
            return 200, {"projects": [], "count": 0}
        if path == "/v1/policy/approvals/pending":
            return 200, {"pending": [], "count": 0}
        if path == "/v1/models":
            return 200, {"models": [], "default_id": None}
        if path == "/v1/models/presets":
            return 200, {"presets": []}
        if path == "/v1/settings/logs":
            return 200, {
                "settings": {"retention_days": 14, "max_bytes": 0},
                "usage": {"bytes": 0, "files": 0},
            }
        if path == "/v1/network/research":
            return 200, {"enabled": False, "notice": "联网检索默认关闭。"}
        if path.startswith("/v1/usage"):
            return 200, {"totals": {}, "series": []}
        return 200, {}


def _mount_mocks(context: Any, backend: MockBackend) -> None:
    def handler(route: Any, request: Any) -> None:
        parsed = urllib.parse.urlparse(request.url)
        status, payload = backend.respond(request.method, parsed.path, request.post_data)
        route.fulfill(
            status=status,
            content_type="application/json",
            body=json.dumps(payload, ensure_ascii=False),
        )

    context.route("**/v1/**", handler)


# ---------------------------------------------------------------------------
# 验收
# ---------------------------------------------------------------------------


class Results:
    def __init__(self) -> None:
        self.items: list[tuple[str, bool, str]] = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.items.append((name, ok, detail))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"：{detail}" if detail else ""))
        return ok

    @property
    def failed(self) -> int:
        return sum(1 for _, ok, _ in self.items if not ok)


def _active(page: Any) -> dict[str, str]:
    return page.evaluate(ACTIVE_JS)


def _wait_announcement(page: Any, expected: str, timeout: int = 5000) -> str:
    deadline = time.time() + timeout / 1000
    last = ""
    while time.time() < deadline:
        node = page.locator('[aria-live][role="status"]').first
        if node.count() > 0:
            last = (node.inner_text() or "").strip()
            if expected in last:
                return last
        page.wait_for_timeout(100)
    return last


#: 登录页/首屏/侧栏的等待预算。dev server 冷启动 + Vite 依赖预构建会慢一拍，
#: 30s 在低配机器上不够，于是把「慢」误报成「坏了」。
LOGIN_TIMEOUT = 60_000
NAV_TIMEOUT = 60_000

#: 桌面侧栏与手机底栏是两套 DOM（<1024px 只渲染底栏）。等哪一个都得算成功，
#: 否则窄视口 / 高 DPI 下永远等不到 .os-nav-item。
NAV_SELECTOR = ".os-nav-item, .os-bottom-item"


def _login_diagnostics(page: Any) -> str:
    """登录卡住时把现场带出来：是编译错误、登录被拒，还是首屏没渲染完。"""
    parts: list[str] = []
    try:
        parts.append(f"url={page.url}")
    except Exception:  # noqa: BLE001
        pass
    try:
        overlay = page.locator("vite-error-overlay")
        if overlay.count():
            parts.append("Vite 编译错误：" + (overlay.first.inner_text() or "")[:200])
    except Exception:  # noqa: BLE001
        pass
    try:
        alerts = page.locator('[role="alert"]')
        texts = [
            (alerts.nth(index).inner_text() or "").strip()
            for index in range(min(alerts.count(), 3))
        ]
        texts = [text for text in texts if text]
        if texts:
            parts.append("页面错误提示：" + " | ".join(texts)[:200])
    except Exception:  # noqa: BLE001
        pass
    try:
        parts.append(f".os-app={page.locator('.os-app').count()}")
        parts.append(f"侧栏项={page.locator('.os-nav-item').count()}")
        parts.append(f"底栏项={page.locator('.os-bottom-item').count()}")
    except Exception:  # noqa: BLE001
        pass
    return "；".join(parts) if parts else "页面无可用状态"


def _login(page: Any, base: str, attempts: int = 2, require_nav: bool = True) -> None:
    """登录并等首屏导航出现。

    修过的三处脆弱点：
    1. 只等 `.os-nav-item`：<1024px 时侧栏 display:none，底栏才是真实入口；
    2. 30s 预算：dev server 冷启动会超过它；
    3. 失败只抛 TimeoutError，看不出是编译错误还是登录被拒 —— 现在带现场信息。
    """
    last: Exception | None = None
    for attempt in range(attempts):
        page.goto(base, wait_until="domcontentloaded")
        try:
            page.wait_for_selector("#os-secret", state="visible", timeout=LOGIN_TIMEOUT)
            # 有的构建默认停在注册页签；显式切回登录页签再填。
            tab = page.locator("#os-login-tab-login")
            if tab.count() and tab.first.get_attribute("aria-selected") != "true":
                tab.first.click()
                page.wait_for_selector("#os-secret", state="visible", timeout=10_000)
            page.fill("#os-principal", "root")
            page.fill("#os-secret", "acceptance-pass")
            page.locator('button[type="submit"]').first.click()
            page.wait_for_selector(".os-app", state="visible", timeout=LOGIN_TIMEOUT)
            if require_nav:
                page.wait_for_selector(NAV_SELECTOR, state="visible", timeout=NAV_TIMEOUT)
            page.wait_for_timeout(400)
            return
        except Exception as error:  # noqa: BLE001 - 重试一次，仍失败时带现场信息抛出
            last = error
            if attempt + 1 < attempts:
                page.wait_for_timeout(1200)
                continue
    raise RuntimeError(f"登录后等不到首屏导航（{NAV_SELECTOR}）：{_login_diagnostics(page)}") from last


def _nav_item(page: Any, label: str) -> Any:
    """侧栏入口。用 has_text 过滤而不是 :text-is()，后者嵌在 :has() 里不被支持。"""
    return page.locator(".os-nav-item").filter(has_text=label).first


def run_main_flow(browser: Any, base: str, results: Results) -> None:
    backend = MockBackend()
    context = browser.new_context(viewport={"width": 1280, "height": 720})
    _mount_mocks(context, backend)
    page = context.new_page()

    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))

    def on_console(message: Any) -> None:
        if message.type == "error":
            page_errors.append(message.text)

    page.on("console", on_console)
    _login(page, base)

    # 1) 登录后焦点必须落在主区，而不是 body。
    active = _active(page)
    results.check(
        "登录后焦点进入主内容（main#os-main）",
        active.get("id") == "os-main",
        f"activeElement=<{active.get('tag')}#{active.get('id')}>",
    )

    # 2) 六个页面：切页播报、焦点不落 body、未命名控件为 0。
    total_unnamed: list[str] = []
    body_drops: list[str] = []
    announce_failures: list[str] = []
    for label in PAGES:
        item = _nav_item(page, label)
        if item.count() == 0:
            results.check(f"页面「{label}」存在导航入口", False, "侧栏找不到该入口")
            continue
        item.click()
        page.wait_for_timeout(450)

        # 页面崩溃要记成 FAIL，而不是让整支脚本抛异常退出（那样报告里什么都看不到）。
        try:
            title = (
                page.locator(".os-page-title").first.inner_text(timeout=5000) or ""
            ).strip()
        except Exception:  # noqa: BLE001
            crash = page.locator(".os-crash-message")
            reason = (
                crash.first.inner_text()
                if crash.count()
                else (page_errors[-1] if page_errors else "页面没有渲染出标题")
            )
            results.check(f"页面「{label}」渲染", False, reason[:300])
            continue

        announcement = _wait_announcement(page, "已进入", timeout=3000)
        if title and f"已进入「{title}」" == announcement:
            pass
        else:
            announce_failures.append(f"{label}: 标题={title!r} 播报={announcement!r}")

        active = _active(page)
        if active.get("tag") == "BODY":
            body_drops.append(label)

        unnamed = page.evaluate(UNNAMED_JS)
        total_unnamed.extend(f"{label}: {entry}" for entry in unnamed)

    results.check(
        "六个页面都有 aria-live 切页播报",
        not announce_failures,
        "全部匹配" if not announce_failures else "; ".join(announce_failures),
    )
    results.check(
        "切页过程中焦点不掉到 body",
        not body_drops,
        "0 次" if not body_drops else f"{len(body_drops)} 次：{', '.join(body_drops)}",
    )
    results.check(
        "未命名控件为 0",
        not total_unnamed,
        f"{len(total_unnamed)} 个" if total_unnamed else "0 个",
    )

    # 3) 建员工：先空名提交（必填失败），再成功。
    _nav_item(page, "我的员工").click()
    page.wait_for_timeout(400)

    page.get_by_role("button", name="创建员工").click()
    page.wait_for_timeout(250)
    error = page.locator("#emp-create-error")
    error_text = (error.inner_text() or "").strip() if error.count() else ""
    active = _active(page)
    results.check(
        "空名提交：错误被 role=alert 播报且焦点回到名字输入框",
        error.count() > 0
        and "请先填写员工名字" in error_text
        and error.get_attribute("role") == "alert"
        and active.get("id") == "emp-name",
        f"错误={error_text!r} activeElement=<{active.get('tag')}#{active.get('id')}>",
    )

    page.fill("#emp-name", EMPLOYEE_NAME)
    page.get_by_role("button", name="创建员工").click()
    page.wait_for_timeout(700)
    notice_text = (page.locator("text=已创建员工").first.inner_text() or "").strip()
    active = _active(page)
    results.check(
        f"创建员工「{EMPLOYEE_NAME}」成功，焦点落在结果提示而非 body",
        f"已创建员工「{EMPLOYEE_NAME}」" in notice_text and active.get("tag") != "BODY",
        f"提示={notice_text!r} activeElement=<{active.get('tag')}>",
    )

    # 4) 派活：展开 → 焦点进入输入框 → 提交 → 焦点不落 body。
    page.get_by_role("button", name="单独指派一个任务").first.click()
    page.wait_for_timeout(250)
    active = _active(page)
    results.check(
        "展开指派后焦点进入任务输入框",
        active.get("tag") == "INPUT" and "派给" in active.get("label", ""),
        f"activeElement=<{active.get('tag')}> label={active.get('label')!r}",
    )

    page.fill(f'input[aria-label="派给「{EMPLOYEE_NAME}」的任务"]', ASSIGN_INSTRUCTION)
    page.get_by_role("button", name="派给它").click()
    page.wait_for_timeout(700)
    assign_notice = (page.locator("text=已派给").first.inner_text() or "").strip()
    active = _active(page)
    results.check(
        "派活成功，回执含目标入口提示且焦点不落 body",
        "已派给" in assign_notice
        and "目标与任务" in assign_notice
        and active.get("tag") != "BODY",
        f"回执={assign_notice[:60]!r} activeElement=<{active.get('tag')}>",
    )

    page.screenshot(path=str(REPO / "docs" / "a11y-sr-hc-keyboard-20261108" / "acceptance-main.png"))
    context.close()


def run_forced_colors(browser: Any, base: str, results: Results) -> None:
    backend = MockBackend()
    context = browser.new_context(
        viewport={"width": 1280, "height": 720},
        forced_colors="active",
    )
    _mount_mocks(context, backend)
    page = context.new_page()
    _login(page, base)
    # 总览页只有链接按钮；到「我的员工」才有真正的 .os-btn，检查按钮可见性更贴题。
    _nav_item(page, "我的员工").click()
    page.wait_for_timeout(450)

    styles = page.evaluate(HC_JS)
    results.check(
        "浏览器实际处于 forced-colors: active",
        bool(styles.get("forced")),
        f"matchMedia={styles.get('forced')}",
    )
    btn = styles.get("btn") or {}
    results.check(
        "强制色下按钮有可见边框（不是无边色块）",
        btn.get("borderWidth", 0) >= 1 and btn.get("borderStyle") != "none",
        f"border={btn.get('borderWidth')}px {btn.get('borderStyle')}",
    )
    current = styles.get("currentNav") or {}
    results.check(
        "强制色下当前页用 2px 系统高亮描边",
        current.get("borderWidth", 0) >= 2 and current.get("borderStyle") != "none",
        f"border={current.get('borderWidth')}px {current.get('borderStyle')}",
    )
    card = styles.get("card") or {}
    results.check(
        "强制色下卡片边框仍可见",
        card.get("borderWidth", 0) >= 1 and card.get("borderStyle") != "none",
        f"border={card.get('borderWidth')}px {card.get('borderStyle')}",
    )
    results.check(
        "强制色下正文与导航文字没有消失（前景≠背景）",
        bool(styles.get("title"))
        and styles.get("title") != styles.get("titleBg")
        and styles.get("navText") != styles.get("navBg"),
        f"title={styles.get('title')} / bg={styles.get('titleBg')}",
    )

    page.screenshot(
        path=str(REPO / "docs" / "a11y-sr-hc-keyboard-20261108" / "acceptance-forced-colors.png")
    )
    context.close()


#: 焦点掉到 body 的位置（目标选择器 + 第几次 Tab），用于报告"卡在哪"。
_DROP_TRACE: list[str] = []


def _tab_until(
    page: Any, selector: str, text: str = "", limit: int = 160, check_before: bool = True
) -> tuple[int, int]:
    """只按 Tab 走到目标，返回 (第几次 Tab 命中, 途中焦点停在 body 的次数)。

    0 表示在 limit 次内走不到 —— 那就是"焦点卡住"的位置，调用方把当前 activeElement 报出来。
    掉 body 只统计"按键前焦点已经停在 body"的真实丢失：DOM 重渲染瞬时的 body 不算，
    那会让报告充满噪声。
    """
    drops = 0
    prev: list[str] = []
    for step in range(1, limit + 1):
        if check_before:
            state = page.evaluate(
                """() => {
                    const el = document.activeElement;
                    if (!el || el === document.body || el === document.documentElement) {
                        return { body: true, tag: '', text: '' };
                    }
                    const text = (el.getAttribute('aria-label') || el.innerText || el.textContent || '')
                        .replace(/\\s+/g, ' ').trim().slice(0, 24);
                    return { body: false, tag: el.tagName, text: text };
                }"""
            )
            if state["body"]:
                drops += 1
                trail = " → ".join(prev[-2:]) or "起点"
                _DROP_TRACE.append(
                    f"去找 {selector}{('/' + text) if text else ''} 前（第 {step} 次 Tab），"
                    f"此前焦点：{trail}"
                )
            else:
                prev.append(f"{state['tag']}:{state['text']}")
        page.keyboard.press("Tab")
        state = page.evaluate(
            """([sel, needle]) => {
                const el = document.activeElement;
                if (!el || el === document.body || el === document.documentElement) return 'body';
                if (!el.matches(sel) && !el.closest(sel)) return 'miss';
                if (needle) {
                    const label = (el.getAttribute('aria-label') || el.innerText || el.textContent || '')
                        .replace(/\\s+/g, ' ').trim();
                    return label.includes(needle) ? 'hit' : 'miss';
                }
                return 'hit';
            }""",
            [selector, text],
        )
        if state == "hit":
            return step, drops
    return 0, drops


def _focus_hint(page: Any) -> str:
    active = _active(page)
    return f"focus=<{active.get('tag')}#{active.get('id')}> {active.get('label') or active.get('text')!r}"


def run_keyboard_journey(browser: Any, base: str, results: Results) -> None:
    """纯键盘走完：登录 → 建员工 → 派活。全程只按 Tab / Enter / Esc。"""
    backend = MockBackend()
    context = browser.new_context(viewport={"width": 1280, "height": 720})
    _mount_mocks(context, backend)
    page = context.new_page()
    page.goto(base, wait_until="domcontentloaded")
    page.wait_for_selector("#os-secret", state="visible", timeout=LOGIN_TIMEOUT)

    drops = 0
    action_drops = 0
    action_trace: list[str] = []
    stuck: list[str] = []
    _DROP_TRACE.clear()

    def check_action_focus(label: str) -> None:
        nonlocal action_drops
        if _active(page).get("tag") == "BODY":
            action_drops += 1
            action_trace.append(label)

    steps, hit_drops = _tab_until(page, "#os-principal", check_before=False)
    drops += hit_drops
    results.check("键盘旅程：Tab 可达账号输入框", steps > 0, f"{steps} 次 Tab；{_focus_hint(page)}")
    if steps == 0:
        stuck.append("账号输入框")
        context.close()
        return

    page.keyboard.type("root")
    steps, hit_drops = _tab_until(page, "#os-secret")
    drops += hit_drops
    results.check("键盘旅程：Tab 可达密码输入框", steps > 0, f"{steps} 次 Tab；{_focus_hint(page)}")
    if steps == 0:
        stuck.append("密码输入框")
        context.close()
        return

    page.keyboard.type("acceptance-pass")
    steps, hit_drops = _tab_until(page, 'button[type="submit"]')
    drops += hit_drops
    results.check("键盘旅程：Tab 可达登录按钮", steps > 0, f"{steps} 次 Tab；{_focus_hint(page)}")
    if steps == 0:
        stuck.append("登录按钮")
        context.close()
        return

    page.keyboard.press("Enter")
    page.wait_for_selector(".os-app", state="visible", timeout=LOGIN_TIMEOUT)
    page.wait_for_selector(NAV_SELECTOR, state="visible", timeout=NAV_TIMEOUT)
    page.wait_for_timeout(450)
    results.check(
        "键盘旅程：Enter 提交后进入控制台，焦点不落 body",
        _active(page).get("tag") != "BODY",
        _focus_hint(page),
    )

    steps, hit_drops = _tab_until(page, ".os-nav-item", "我的员工")
    drops += hit_drops
    results.check("键盘旅程：Tab 可达「我的员工」入口", steps > 0, f"{steps} 次 Tab；{_focus_hint(page)}")
    if steps == 0:
        stuck.append("我的员工入口")
        context.close()
        return

    page.keyboard.press("Enter")
    page.wait_for_timeout(450)
    title = (page.locator(".os-page-title").first.inner_text() or "").strip()
    results.check("键盘旅程：Enter 切页进入「我的员工」", "我的员工" in title, f"页面标题={title!r}")
    check_action_focus("Enter 切页后")

    steps, hit_drops = _tab_until(page, "#emp-name")
    drops += hit_drops
    results.check("键盘旅程：Tab 可达员工名字输入框", steps > 0, f"{steps} 次 Tab；{_focus_hint(page)}")
    if steps == 0:
        stuck.append("员工名字输入框")
        context.close()
        return

    page.keyboard.type("键盘员工")
    steps, hit_drops = _tab_until(page, "button", "创建员工")
    drops += hit_drops
    results.check("键盘旅程：Tab 可达「创建员工」按钮", steps > 0, f"{steps} 次 Tab；{_focus_hint(page)}")
    if steps == 0:
        stuck.append("创建员工按钮")
        context.close()
        return

    page.keyboard.press("Enter")
    page.wait_for_timeout(700)
    created = page.locator("text=已创建员工").first
    results.check(
        "键盘旅程：Enter 建员工成功",
        created.count() > 0 and _active(page).get("tag") != "BODY",
        _focus_hint(page),
    )

    steps, hit_drops = _tab_until(page, "button", "单独指派一个任务")
    drops += hit_drops
    results.check("键盘旅程：Tab 可达「单独指派一个任务」", steps > 0, f"{steps} 次 Tab；{_focus_hint(page)}")
    if steps == 0:
        stuck.append("单独指派一个任务")
        context.close()
        return

    page.keyboard.press("Enter")
    page.wait_for_timeout(300)
    check_action_focus("Enter 展开指派后")
    target = 'input[aria-label="派给「键盘员工」的任务"]'
    steps, hit_drops = _tab_until(page, target)
    drops += hit_drops
    results.check("键盘旅程：Enter 展开指派后 Tab 进入任务输入框", steps > 0, f"{steps} 次 Tab；{_focus_hint(page)}")
    if steps == 0:
        stuck.append("任务输入框")
        context.close()
        return

    page.keyboard.type(ASSIGN_INSTRUCTION)
    steps, hit_drops = _tab_until(page, "button", "派给它")
    drops += hit_drops
    results.check("键盘旅程：Tab 可达「派给它」按钮", steps > 0, f"{steps} 次 Tab；{_focus_hint(page)}")
    if steps == 0:
        stuck.append("派给它按钮")
        context.close()
        return

    page.keyboard.press("Enter")
    page.wait_for_timeout(700)
    receipt = page.locator("text=已派给").first
    receipt_text = (receipt.inner_text() or "").strip() if receipt.count() else ""
    results.check(
        "键盘旅程：Enter 派活成功，回执含「目标与任务」",
        "已派给" in receipt_text and "目标与任务" in receipt_text,
        f"回执={receipt_text[:80]!r}",
    )
    results.check(
        "键盘旅程：每个关键动作后焦点都不落 body",
        action_drops == 0,
        "全部通过" if action_drops == 0 else "落 body：" + "、".join(action_trace),
    )
    if drops:
        print("[INFO] Tab 扫掠中焦点曾短暂落到 body（随后仍能继续 Tab，不阻断）：" + "；".join(_DROP_TRACE[:4]))
    results.check(
        "键盘旅程：没有卡住的步骤",
        not stuck,
        "全部通过" if not stuck else "卡在：" + "、".join(stuck),
    )
    page.screenshot(path=str(REPO / "docs" / "a11y-sr-hc-keyboard-20261108" / "acceptance-keyboard.png"))
    context.close()


def run_zoom_200(browser: Any, base: str, results: Results) -> None:
    """Windows 缩放 200%、窗口 1366×768：等效 CSS 视口 683×384、DPR=2。

    断言的是"看得见、点得到"：元素完整落在视口内，且中心点命中测试仍是它自己
    （没被浮层盖住 / 没被裁掉），不是只断言 DOM 里存在。
    """
    width, height = 683, 384
    backend = MockBackend()
    context = browser.new_context(
        viewport={"width": width, "height": height},
        device_scale_factor=2,
    )
    _mount_mocks(context, backend)
    page = context.new_page()
    page.goto(base, wait_until="domcontentloaded")
    page.wait_for_selector("#os-secret", state="visible", timeout=LOGIN_TIMEOUT)

    def visible_and_hittable(selector: str, scroll: bool = False) -> tuple[bool, str]:
        locator = page.locator(selector).first
        if locator.count() == 0:
            return False, "元素不存在"
        if scroll:
            # 页面纵向滚动是正常操作；要排除的是"滚到底也点不到"的裁切/遮挡。
            locator.scroll_into_view_if_needed()
        box = locator.bounding_box()
        if box is None:
            return False, "元素没有布局盒（不可见）"
        inside = (
            box["x"] >= -0.5
            and box["y"] >= -0.5
            and box["x"] + box["width"] <= width + 0.5
            and box["y"] + box["height"] <= height + 0.5
        )
        hit = locator.evaluate(
            """(el) => {
                const r = el.getBoundingClientRect();
                const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
                return !!top && (top === el || el.contains(top));
            }"""
        )
        detail = (
            f"box=({box['x']:.0f},{box['y']:.0f},{box['width']:.0f}x{box['height']:.0f})"
            f" 视口内={inside} 命中={hit}"
        )
        return inside and hit, detail

    ok, detail = visible_and_hittable('button[type="submit"]')
    results.check("200% 缩放：登录按钮完整可见且可点（未被裁掉）", ok, detail)

    _login(page, base, require_nav=False)
    # 683px 已是手机形态，入口在底栏；顺便验证新补的「目标」入口没被挤掉。
    goals_ok, goals_detail = visible_and_hittable('.os-bottom-item[data-key="goals"]')
    results.check("200% 缩放：底栏「目标」入口完整可见且可点", goals_ok, goals_detail)

    nav = page.locator('.os-bottom-item[data-key="my-employees"]')
    if nav.count():
        nav.first.click()
    page.wait_for_timeout(450)
    ok, detail = visible_and_hittable("#emp-name", scroll=True)
    results.check("200% 缩放：员工名字输入框完整可见且可点", ok, detail)
    ok, detail = visible_and_hittable("button:has-text('创建员工')", scroll=True)
    results.check("200% 缩放：「创建员工」按钮完整可见且可点", ok, detail)
    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    results.check("200% 缩放：页面没有横向溢出（不靠左右拖动才能操作）", overflow <= 2, f"溢出={overflow}px")

    page.screenshot(
        path=str(REPO / "docs" / "a11y-sr-hc-keyboard-20261108" / "acceptance-zoom-200.png")
    )
    context.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="控制台可访问性真浏览器验收")
    parser.add_argument("--url", default=os.environ.get("LIUHAO_CONSOLE_URL", ""))
    parser.add_argument("--port", type=int, default=int(os.environ.get("A11Y_CONSOLE_PORT", DEFAULT_PORT)))
    args = parser.parse_args(argv)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[SKIP] 环境不具备：未安装 playwright（pip install playwright && playwright install msedge）")
        return 2

    server: subprocess.Popen[str] | None = None
    base = args.url.rstrip("/") + "/" if args.url else f"http://127.0.0.1:{args.port}/"
    if args.url and not _reachable(base):
        # 显式给了地址就不再自作主张起服务：连不上按"环境不具备"处理。
        print(f"[SKIP] 环境不具备：指定地址不可达（{base}）")
        return 2
    if not _reachable(base):
        server = _start_dev_server(args.port)
        if server is None or not _reachable(base):
            if server is not None:
                _stop_dev_server(server)
            print(f"[SKIP] 环境不具备：控制台 dev server 起不来（{base}）")
            return 2

    results = Results()
    exit_code = 1
    try:
        with sync_playwright() as playwright:
            browser = None
            for launcher, kwargs in (
                (playwright.chromium, {"channel": "msedge"}),
                (playwright.chromium, {}),
            ):
                try:
                    browser = launcher.launch(headless=True, **kwargs)
                    break
                except Exception:  # noqa: BLE001 - 换下一个浏览器再试
                    continue
            if browser is None:
                print("[SKIP] 环境不具备：Edge / Chromium 都起不来")
                return 2
            try:
                runners = (
                    ("主流程", run_main_flow),
                    ("强制色", run_forced_colors),
                    ("键盘旅程", run_keyboard_journey),
                    ("200% 缩放", run_zoom_200),
                )
                for name, runner in runners:
                    started = time.time()
                    try:
                        runner(browser, base, results)
                    except Exception as error:  # noqa: BLE001 - 一条旅程坏掉不该吞掉其余检查
                        if not results.items:
                            raise
                        results.check(f"{name} 执行中断", False, str(error)[:300])
                    print(f"[TIME] {name}：{time.time() - started:.1f}s")
            except Exception as error:  # noqa: BLE001
                if not results.items:
                    # 一条检查都没跑起来（页面打不开 / 登录页不出现）→ 环境问题，不是通过。
                    print(f"[SKIP] 环境不具备：页面无法完成加载（{error}）")
                    return 2
                results.check("执行中断", False, str(error)[:300])
            finally:
                browser.close()
        exit_code = 0 if results.failed == 0 else 1
    finally:
        if server is not None:
            _stop_dev_server(server)

    total = len(results.items)
    print(f"\n汇总：{total - results.failed}/{total} 检查 PASS")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
