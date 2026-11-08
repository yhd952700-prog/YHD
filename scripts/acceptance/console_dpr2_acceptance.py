#!/usr/bin/env python
"""网页控制台在 200% 系统缩放（devicePixelRatio=2）下的首屏可用性验收。

为什么需要它
------------
Windows 200% 缩放下，浏览器把 ``devicePixelRatio`` 报成 2：CSS 像素与物理
像素是 1:2。首屏（登录 / 首次设置卡片）最容易出现的问题就是卡片或主按钮
超出视口、或者和左侧说明区重叠——用户看到的是"按钮点不到"。

本脚本用 Playwright 在 ``device_scale_factor=2`` 下打开控制台，量取真实
布局盒子，逐条判定"登录入口 / 主按钮不重叠、不溢出"，并把 dPR=2 的截图
存进 ``docs/dpi-evidence/``。

只读验证
--------
* 不改前端源码、不写 ``dist\\``；
* 优先复用已经在跑的控制台 dev server（``LIUHAO_CONSOLE_URL`` 或 5173），
  没有才临时起一个，跑完关掉自己起的那一个。

用法
----
    python scripts/acceptance/console_dpr2_acceptance.py
    python scripts/acceptance/console_dpr2_acceptance.py --url http://127.0.0.1:5173/

退出码 0 表示全部 PASS。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CONSOLE_DIR = REPO / "apps" / "console" / "console"
EVIDENCE_DIR = REPO / "docs" / "dpi-evidence"
REPORT_PATH = EVIDENCE_DIR / "console-dpr2-report.json"

DEFAULT_PORT = 5173
#: 视口组合（CSS 像素）。dPR=2 时就相当于 200% 系统缩放下的窗口尺寸。
VIEWPORTS = [
    ("1366x768", 1366, 768),
    ("1280x720", 1280, 720),
]


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
    # 显式绑 127.0.0.1：vite 默认只监听 localhost（Node 下常解析到 ::1），
    # 不写 --host 时用 127.0.0.1 探测会一直失败。
    command = f"npm run dev -- --host 127.0.0.1 --port {port} --strictPort"
    return subprocess.Popen(
        ["cmd.exe", "/c", command],
        cwd=str(CONSOLE_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )


def _stop_dev_server(proc: subprocess.Popen[str]) -> None:
    with_context = ["taskkill", "/F", "/T", "/PID", str(proc.pid)]
    try:
        subprocess.run(with_context, capture_output=True, timeout=20)
    except Exception:  # noqa: BLE001 - 清理失败不影响验收结论
        pass


# ---------------------------------------------------------------------------
# 页面测量
# ---------------------------------------------------------------------------

_MEASURE_JS = """
() => {
  const isVisible = (el) => {
    if (!el) return false;
    const style = getComputedStyle(el);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const box = (el) => {
    const r = el.getBoundingClientRect();
    return { x: r.x, y: r.y, w: r.width, h: r.height,
             text: (el.innerText || el.value || '').trim().slice(0, 30) };
  };
  const card = document.querySelector('.os-login-card');
  const aside = document.querySelector('.os-login-aside');
  const visibleButtons = Array.from(document.querySelectorAll('.os-login-card button'))
      .filter(isVisible);
  const inputs = Array.from(document.querySelectorAll('.os-login-card input'))
      .filter(isVisible);
  const cta = visibleButtons.find(
      (el) => el.type === 'submit' || el.classList.contains('os-btn')) || null;
  return {
    dpr: window.devicePixelRatio,
    viewport: [window.innerWidth, window.innerHeight],
    scroll: [document.documentElement.scrollWidth, document.documentElement.scrollHeight],
    card: card && isVisible(card) ? box(card) : null,
    aside: aside && isVisible(aside) ? box(aside) : null,
    cta: cta ? box(cta) : null,
    buttons: visibleButtons.map(box),
    inputs: inputs.map(box),
    tabs: Array.from(document.querySelectorAll('.os-login-tabs button'))
        .filter(isVisible).map(box),
    title: document.title,
  };
}
"""


def _overlap(a: dict[str, float], b: dict[str, float]) -> bool:
    return not (
        a["x"] + a["w"] <= b["x"]
        or b["x"] + b["w"] <= a["x"]
        or a["y"] + a["h"] <= b["y"]
        or b["y"] + b["h"] <= a["y"]
    )


def _inside_viewport(box: dict[str, float], width: int, height: int, tol: float = 0.5) -> bool:
    return (
        box["x"] >= -tol
        and box["y"] >= -tol
        and box["x"] + box["w"] <= width + tol
        and box["y"] + box["h"] <= height + tol
    )


def _inside_box(box: dict[str, float], outer: dict[str, float], tol: float = 0.5) -> bool:
    return (
        box["x"] >= outer["x"] - tol
        and box["y"] >= outer["y"] - tol
        and box["x"] + box["w"] <= outer["x"] + outer["w"] + tol
        and box["y"] + box["h"] <= outer["y"] + outer["h"] + tol
    )


def _measure_view(page, name: str, width: int, height: int) -> dict[str, object]:
    data = page.evaluate(_MEASURE_JS)
    checks: list[dict[str, object]] = []

    def add(label: str, ok: bool, detail: str) -> None:
        checks.append({"name": label, "ok": bool(ok), "detail": detail})

    add(
        f"{name} devicePixelRatio=2",
        abs(float(data["dpr"]) - 2.0) < 0.01,
        f"devicePixelRatio={data['dpr']} viewport={data['viewport']}",
    )
    card = data["card"]
    add(f"{name} 首屏卡片已渲染", card is not None, f"card={card}")
    if card:
        add(
            f"{name} 卡片不溢出视口",
            _inside_viewport(card, width, height),
            f"card={_fmt(card)} viewport={width}x{height}",
        )
    cta = data["cta"]
    add(f"{name} 主按钮存在且可见", cta is not None, f"cta={cta}")
    if cta:
        add(
            f"{name} 主按钮不溢出视口",
            _inside_viewport(cta, width, height),
            f"cta={_fmt(cta)} viewport={width}x{height}",
        )
        if card:
            add(
                f"{name} 主按钮不超出卡片",
                _inside_box(cta, card),
                f"cta={_fmt(cta)} card={_fmt(card)}",
            )
    aside = data["aside"]
    if card and aside:
        add(
            f"{name} 说明区与卡片不重叠",
            not _overlap(aside, card),
            f"aside={_fmt(aside)} card={_fmt(card)}",
        )
    if card:
        for index, item in enumerate(data["buttons"]):
            add(
                f"{name} 按钮{index + 1}「{item['text']}」在卡片内",
                _inside_box(item, card) and _inside_viewport(item, width, height),
                f"box={_fmt(item)} card={_fmt(card)}",
            )
        for index, item in enumerate(data["inputs"]):
            add(
                f"{name} 输入框{index + 1}在卡片内",
                _inside_box(item, card) and _inside_viewport(item, width, height),
                f"box={_fmt(item)} card={_fmt(card)}",
            )
    if cta and data["inputs"]:
        overlapping = [item for item in data["inputs"] if _overlap(cta, item)]
        add(
            f"{name} 主按钮与输入框不重叠",
            not overlapping,
            f"cta={_fmt(cta)} 重叠输入框={[_fmt(i) for i in overlapping]}",
        )
    scroll = data["scroll"]
    add(
        f"{name} 无横向溢出",
        float(scroll[0]) <= width + 1,
        f"scrollWidth={scroll[0]} viewport宽={width}",
    )
    add(
        f"{name} 首屏不需要纵向滚动即可看到主按钮",
        bool(cta) and float(scroll[1]) <= height + 1,
        f"scrollHeight={scroll[1]} viewport高={height}"
        + (f" cta底={round(cta['y'] + cta['h'], 1)}" if cta else ""),
    )
    return {"viewport": name, "size": [width, height], "checks": checks, "measure": data}


def _fmt(box: dict[str, float]) -> str:
    return f"({round(box['x'], 1)},{round(box['y'], 1)},{round(box['w'], 1)},{round(box['h'], 1)})"


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def run(url: str) -> dict[str, object]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {
            "passed": False,
            "checks": [
                {
                    "name": "Playwright 可用",
                    "ok": False,
                    "detail": "当前解释器没有安装 playwright；请用 .venv\\Scripts\\python.exe 运行",
                }
            ],
            "screenshots": [],
        }

    checks: list[dict[str, object]] = []
    screenshots: list[dict[str, object]] = []
    measurements: list[dict[str, object]] = []

    with sync_playwright() as playwright:
        browser = _launch(playwright)
        if browser is None:
            return {
                "passed": False,
                "checks": [
                    {
                        "name": "浏览器可用",
                        "ok": False,
                        "detail": "既没有 msedge 渠道，也没有 Playwright 自带的 Chromium",
                    }
                ],
                "screenshots": [],
            }
        try:
            for name, width, height in VIEWPORTS:
                context = browser.new_context(
                    viewport={"width": width, "height": height},
                    device_scale_factor=2,
                )
                page = context.new_page()
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_selector(".os-login-card", timeout=30000)
                    page.wait_for_timeout(1200)  # 等字体与首帧动画稳定
                    result = _measure_view(page, name, width, height)
                    measurements.append(result)
                    checks.extend(result["checks"])  # type: ignore[arg-type]
                    out = EVIDENCE_DIR / f"console-dpr2-{name}.png"
                    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(out))
                    screenshots.append(
                        {
                            "path": str(out.relative_to(REPO)),
                            "css_viewport": [width, height],
                            "device_pixels": [width * 2, height * 2],
                            "bytes": out.stat().st_size,
                        }
                    )
                except Exception as exc:  # noqa: BLE001 - 单视口失败不影响另一个
                    checks.append(
                        {
                            "name": f"{name} 页面可打开",
                            "ok": False,
                            "detail": f"{type(exc).__name__}: {exc}",
                        }
                    )
                finally:
                    context.close()
        finally:
            browser.close()

    passed = all(bool(c["ok"]) for c in checks)
    return {
        "url": url,
        "passed": passed,
        "checks": checks,
        "measurements": measurements,
        "screenshots": screenshots,
    }


def _launch(playwright):
    """优先用系统 Edge（无需下载浏览器），退化到 Playwright 自带 Chromium。"""
    for kwargs in ({"channel": "msedge"}, {}):
        try:
            return playwright.chromium.launch(headless=True, **kwargs)
        except Exception:  # noqa: BLE001 - 换下一种启动方式
            continue
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="控制台 dPR=2 首屏可用性验收")
    parser.add_argument("--url", help="控制台地址（默认复用 5173，没有则临时起 dev server）")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="临时 dev server 端口")
    args = parser.parse_args(argv)

    proc: subprocess.Popen[str] | None = None
    port = args.port
    url = (args.url or os.environ.get("LIUHAO_CONSOLE_URL") or "").strip()
    if not url:
        url = f"http://127.0.0.1:{port}/"
        if not _reachable(url):
            if not shutil.which("node"):
                print(f"[FAIL] 控制台不可达且本机没有 node：{url}")
                return 1
            proc = _start_dev_server(port)
            if proc is None:
                print(f"[FAIL] 控制台不可达且缺少依赖目录：{CONSOLE_DIR / 'node_modules'}")
                return 1
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline and not _reachable(url):
                time.sleep(1.0)
    if not _reachable(url):
        if proc is not None:
            _stop_dev_server(proc)
        print(f"[FAIL] 控制台在 120 秒内没有起来：{url}")
        return 1

    try:
        result = run(url)
    finally:
        if proc is not None:
            _stop_dev_server(proc)

    print()
    for check in result["checks"]:  # type: ignore[index]
        status = "PASS" if check["ok"] else "FAIL"
        print(f"[{status}] {check['name']}：{check['detail']}")
    for shot in result.get("screenshots", []):  # type: ignore[union-attr]
        print(f"       证据：{shot['path']} 物理像素={shot['device_pixels']}")

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(
            {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), **result},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    total = len(result["checks"])  # type: ignore[arg-type]
    failed = sum(1 for c in result["checks"] if not c["ok"])  # type: ignore[index]
    print(f"\n汇总：{total - failed}/{total} 检查 PASS")
    print(f"报告：{REPORT_PATH.relative_to(REPO)}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
