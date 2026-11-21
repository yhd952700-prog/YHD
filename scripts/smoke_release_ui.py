#!/usr/bin/env python3
"""发布冒烟（护栏 5：守发布物而非源码）。

以仓库 `console/` dist 冷启网关（全新数据目录），用 Playwright 走：
首启建号 → 未来主题工作台 → 经典主题工作台 → 登录页，逐屏截图，
全程收集 pageerror，任一屏有 pageerror 即失败。

用法：
    python scripts/smoke_release_ui.py [--port 8951] [--out DIR]

需要 Playwright（chromium）。退出码 0 = 三屏齐全且零 pageerror。
截图落 --out（默认 /tmp/smoke-release-ui）：01-future.png / 02-classic.png /
03-login.png。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def wait_ready(port: int, timeout: float = 90.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/ready", timeout=3) as resp:
                if json.loads(resp.read()).get("status") == "ready":
                    return True
        except Exception:
            time.sleep(1.5)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8951)
    ap.add_argument("--out", default="/tmp/smoke-release-ui")
    ap.add_argument(
        "--gateway-python",
        default=str(Path.home() / "workspace/recovery/venv046/bin/python"),
        help="跑网关的解释器（后端 venv）；截图侧解释器需带 Playwright，两者可不同",
    )
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    data_dir = tempfile.mkdtemp(prefix="smoke-data-")
    env = dict(
        os.environ,
        HOST="127.0.0.1",
        PORT=str(args.port),
        LIUHAO_DATA_DIR=data_dir,
        LIUHAO_CONSOLE_DIST=str(ROOT / "console"),
    )
    gateway_python = args.gateway_python if Path(args.gateway_python).exists() else sys.executable
    proc = subprocess.Popen(
        [gateway_python, "-m", "src.gateway"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        if not wait_ready(args.port):
            print("SMOKE FAIL: gateway did not become ready")
            return 1
        from playwright.sync_api import sync_playwright

        base = f"http://127.0.0.1:{args.port}/"
        errors: list[str] = []
        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context(viewport={"width": 1440, "height": 900})
            pg = ctx.new_page()
            pg.on("pageerror", lambda e: errors.append(f"future/login flow: {e}"))
            pg.goto(base, wait_until="networkidle")
            pg.wait_for_timeout(2000)
            # 首启向导建号（全新数据目录）
            ins = pg.query_selector_all("input")
            texts = [i for i in ins if i.get_attribute("type") in ("text", None)]
            pwds = [i for i in ins if i.get_attribute("type") == "password"]
            if len(pwds) >= 2 and texts:
                texts[0].fill("smoke")
                pwds[0].fill("smoke-pass-1234")
                pwds[1].fill("smoke-pass-1234")
                pg.evaluate(
                    """() => { const bs = Array.from(document.querySelectorAll('button.os-btn'));
                      const b = bs.find(x => x.offsetParent !== null) || bs[0]; if (b) b.click(); }"""
                )
                pg.wait_for_timeout(4000)
                for label in ("我已保存", "知道了"):
                    btn = pg.query_selector(f"text={label}")
                    if btn and btn.is_visible():
                        btn.click()
                        pg.wait_for_timeout(800)
            pg.wait_for_timeout(2500)
            pg.screenshot(path=str(out / "01-future.png"))
            # 经典主题
            pg.evaluate("localStorage.setItem('jarvis.theme.mode.v1', 'classic')")
            pg.reload(wait_until="networkidle")
            pg.wait_for_timeout(2500)
            pg.screenshot(path=str(out / "02-classic.png"))
            # 登录页（新上下文 = 未登录）
            ctx2 = browser.new_context(viewport={"width": 1440, "height": 900})
            pg2 = ctx2.new_page()
            pg2.on("pageerror", lambda e: errors.append(f"login: {e}"))
            pg2.goto(base, wait_until="networkidle")
            pg2.wait_for_timeout(2000)
            pg2.screenshot(path=str(out / "03-login.png"))
            browser.close()
        shots = [out / n for n in ("01-future.png", "02-classic.png", "03-login.png")]
        missing = [str(s) for s in shots if not s.exists() or s.stat().st_size == 0]
        if missing:
            print(f"SMOKE FAIL: screenshots missing: {missing}")
            return 1
        if errors:
            print("SMOKE FAIL: pageerrors:")
            for e in errors:
                print(" ", e)
            return 1
        print(f"SMOKE PASS: 3 screenshots in {out}, zero pageerrors")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
