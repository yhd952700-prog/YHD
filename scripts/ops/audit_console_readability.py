#!/usr/bin/env python
"""三端界面可读性审计：对比度 + 运行时异常。

为什么需要它：HTTP 200 与 MIME 校验只能证明「文件被发出来了」。一个主题变量配错，
桌面端可能完好而网页版整页发白 —— 接口测试全绿也发现不了。本脚本用真实 Chromium
渲染三端 × 全部页面，逐条测文字的实际前景色 / 有效背景色，按 WCAG 判对比度。

判据（WCAG 2.1 AA）：
  正文   < 4.5  → 违规
  大字   < 3.0  → 违规   （大字 = ≥24px，或 ≥18.66px 且 bold≥700）

背景含渐变/图片时无法精确求解，这类样本单独归类为「近似」，只报告不判违规，
避免用估算值误伤。

用法（需要有一个可访问的已发布实例）：
  LIUHAO_AUDIT_BASE=http://127.0.0.1:8099/ python scripts/ops/audit_console_readability.py
退出码 0 = 无违规；1 = 有违规或渲染异常；2 = 环境不可用（找不到浏览器 / 连不上服务）。
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

try:
    import websockets
except ImportError:  # pragma: no cover
    print("缺少 websockets：pip install websockets", file=sys.stderr)
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parents[2]
NAV_TS = ROOT / "apps" / "console" / "console" / "src" / "lib" / "nav.ts"

BASE = os.environ.get("LIUHAO_AUDIT_BASE", "http://127.0.0.1:8099/")
PRINCIPAL = os.environ.get("LIUHAO_AUDIT_PRINCIPAL", "ceo")
SECRET = os.environ.get("LIUHAO_AUDIT_SECRET", "Boss-Console-Demo-2026")
CDP_PORT = int(os.environ.get("LIUHAO_AUDIT_CDP_PORT", "9344"))
OUT = Path(os.environ.get("LIUHAO_AUDIT_OUT", r"D:\WorkBuddyFiles\screenshots\audit"))
PROFILE = Path(os.environ.get("LIUHAO_AUDIT_PROFILE", r"D:\cache\temp\cdp-audit-profile"))

MODES: list[tuple[str, int, int, int, bool]] = [
    ("desktop", 1440, 900, 1, False),
    ("web", 1100, 800, 1, False),
    ("mobile", 390, 844, 2, True),
]

seq = 0
EVENTS: list[dict] = []


# --------------------------------------------------------------------------- #
# 页面清单从 nav.ts 解析，避免与前端分叉
# --------------------------------------------------------------------------- #
def page_keys() -> list[str]:
    if not NAV_TS.exists():
        raise SystemExit(f"找不到 {NAV_TS}")
    source = NAV_TS.read_text(encoding="utf-8")
    keys = re.findall(r"key:\s*'([a-z][a-z0-9-]*)'", source)
    seen: list[str] = []
    for key in keys:
        if key not in seen:
            seen.append(key)
    if not seen:
        raise SystemExit("nav.ts 里没有解析到任何页面 key —— 护栏本身失效，先修护栏")
    return seen


# --------------------------------------------------------------------------- #
# CDP 最小客户端
# --------------------------------------------------------------------------- #
async def pump(ws) -> None:
    try:
        async for raw in ws:
            EVENTS.append(json.loads(raw))
    except Exception:  # noqa: BLE001
        pass


async def cmd(ws, method, params=None, timeout=45.0):
    global seq
    seq += 1
    mid = seq
    await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
    deadline = time.time() + timeout
    while time.time() < deadline:
        for event in list(EVENTS):
            if event.get("id") == mid:
                EVENTS.remove(event)
                if "error" in event:
                    raise RuntimeError(f"{method} -> {event['error']}")
                return event.get("result", {})
        await asyncio.sleep(0.04)
    raise TimeoutError(method)


async def evaluate(ws, expression):
    res = await cmd(ws, "Runtime.evaluate", {
        "expression": expression, "returnByValue": True, "awaitPromise": True})
    if "exceptionDetails" in res:
        detail = res["exceptionDetails"]
        raise RuntimeError(detail.get("exception", {}).get("description") or detail.get("text"))
    return res.get("result", {}).get("value")


async def shot(ws, path: Path) -> None:
    res = await cmd(ws, "Page.captureScreenshot", {"format": "png"})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(res["data"]))


def drain_problems() -> list[str]:
    """取出并清空累计的运行时异常 / console.error。"""
    problems: list[str] = []
    for event in list(EVENTS):
        method = event.get("method")
        params = event.get("params") or {}
        if method == "Runtime.exceptionThrown":
            detail = params.get("exceptionDetails") or {}
            text = detail.get("exception", {}).get("description") or detail.get("text")
            problems.append(f"异常: {str(text).splitlines()[0][:200]}")
        elif method == "Runtime.consoleAPICalled" and params.get("type") in ("error", "assert"):
            args = params.get("args") or []
            text = " ".join(str(a.get("value", a.get("description", ""))) for a in args)
            problems.append(f"console.error: {text[:200]}")
    return problems


# --------------------------------------------------------------------------- #
# 页面内探针：用 canvas 解析任意 CSS 颜色，再逐层合成出有效背景
# --------------------------------------------------------------------------- #
PROBE = r"""
(() => {
  const cv = document.createElement('canvas');
  cv.width = 1; cv.height = 1;
  const cx = cv.getContext('2d', { willReadFrequently: true });
  const cache = new Map();
  const parseColor = (str) => {
    if (cache.has(str)) return cache.get(str);
    cx.clearRect(0, 0, 1, 1);
    cx.fillStyle = '#000';
    cx.fillStyle = str;                    // 非法值会被忽略，落到上一个值
    cx.fillRect(0, 0, 1, 1);
    const d = cx.getImageData(0, 0, 1, 1).data;
    const out = [d[0], d[1], d[2], d[3] / 255];
    cache.set(str, out);
    return out;
  };
  const over = (top, bottom) => [                 // top 叠在 bottom 上
    top[0] * top[3] + bottom[0] * (1 - top[3]),
    top[1] * top[3] + bottom[1] * (1 - top[3]),
    top[2] * top[3] + bottom[2] * (1 - top[3]),
    1,
  ];
  const lum = (rgb) => {
    const f = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(rgb[0]) + 0.7152 * f(rgb[1]) + 0.0722 * f(rgb[2]);
  };
  const ratio = (a, b) => {
    const l1 = lum(a), l2 = lum(b);
    return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
  };
  const theme = document.documentElement.dataset.theme;
  const fallback = theme === 'light' ? [255, 255, 255, 1] : [5, 9, 18, 1];

  const effectiveBg = (el) => {
    const layers = [];
    let approx = false;
    let node = el;
    while (node && node.nodeType === 1) {
      const cs = getComputedStyle(node);
      if (cs.backgroundImage && cs.backgroundImage !== 'none') approx = true;
      const c = parseColor(cs.backgroundColor);
      if (c[3] > 0.001) layers.push(c);
      if (c[3] >= 0.999) break;
      node = node.parentElement;
    }
    let bg = fallback;
    if (!layers.length) approx = true;
    for (let i = layers.length - 1; i >= 0; i -= 1) bg = over(layers[i], bg);
    return { bg, approx };
  };

  const label = (el) => {
    const parts = [];
    let node = el;
    for (let i = 0; i < 4 && node && node.nodeType === 1; i += 1) {
      let s = node.tagName.toLowerCase();
      if (node.id) s += '#' + node.id;
      else if (node.classList.length) s = '.' + [...node.classList].slice(0, 2).join('.');
      parts.unshift(s);
      node = node.parentElement;
    }
    return parts.join(' > ');
  };

  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const seen = new Set();
  const samples = [];
  let node;
  while ((node = walker.nextNode())) {
    const text = (node.nodeValue || '').trim();
    if (!text) continue;
    const el = node.parentElement;
    if (!el || seen.has(el)) continue;
    seen.add(el);
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) < 0.5) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    if (r.bottom < 0 || r.top > 24000) continue;
    const fontSize = parseFloat(cs.fontSize);
    if (!(fontSize >= 8)) continue;
    if (el.closest('.os-chart, svg')) continue;              // 图表内文字由 SVG 另管
    const fg = parseColor(cs.color);
    if (fg[3] < 0.5) continue;
    const { bg, approx } = effectiveBg(el);
    samples.push({
      path: label(el),
      text: text.slice(0, 30),
      fg: [Math.round(fg[0]), Math.round(fg[1]), Math.round(fg[2])],
      bg: [Math.round(bg[0]), Math.round(bg[1]), Math.round(bg[2])],
      fontSize, fontWeight: Number(cs.fontWeight) || 400,
      contrast: Math.round(ratio(fg, bg) * 1000) / 1000,
      approx,
      scrollY: Math.round(window.scrollY),
      pageY: Math.round(r.top + window.scrollY),
    });
  }
  return {
    mode: document.documentElement.dataset.mode,
    theme,
    page: new URLSearchParams(location.search).get('p') || 'overview',
    samples,
  };
})()
"""


def classify(sample: dict) -> str:
    """返回 '' 表示合格，否则返回违规原因。"""
    size = sample["fontSize"]
    bold = sample["fontWeight"] >= 700
    large = size >= 24 or (size >= 18.66 and bold)
    need = 3.0 if large else 4.5
    if sample["approx"]:
        return ""                      # 背景不确定，不判违规
    return f"对比度 {sample['contrast']} < {need}（{'大字' if large else '正文'}）" \
        if sample["contrast"] < need else ""


async def run() -> int:
    keys = page_keys()
    browser = find_chromium()
    if not browser:
        print("找不到 Chromium/Chrome。设 LIUHAO_AUDIT_BROWSER 或安装 playwright chromium。", file=sys.stderr)
        return 2
    if not reachable(BASE):
        print(f"服务不可达：{BASE}", file=sys.stderr)
        return 2
    OUT.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(PROFILE, ignore_errors=True)
    PROFILE.mkdir(parents=True, exist_ok=True)

    print(f"审计目标 {BASE}")
    print(f"页面清单（自 nav.ts 解析，{len(keys)} 页）：{' '.join(keys)}")
    print(f"形态：{'、'.join(m[0] for m in MODES)}")

    chrome = subprocess.Popen(
        [str(browser), "--headless=new", f"--remote-debugging-port={CDP_PORT}",
         f"--user-data-dir={PROFILE}", "--no-first-run", "--no-default-browser-check",
         "--disable-extensions", "--disable-background-networking",
         "--hide-scrollbars", "--force-device-scale-factor=1", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    violations: list[tuple[str, str, dict]] = []
    runtime: list[tuple[str, str, str]] = []
    coverage = 0
    try:
        ws_url = await wait_for_target()
        if not ws_url:
            print("无法连接 CDP", file=sys.stderr)
            return 2
        async with websockets.connect(ws_url, max_size=256 * 1024 * 1024) as ws:
            asyncio.create_task(pump(ws))
            await cmd(ws, "Page.enable")
            await cmd(ws, "Runtime.enable")
            await cmd(ws, "Page.navigate", {"url": BASE})
            await asyncio.sleep(2.5)
            if await evaluate(ws, "!!document.querySelector('#os-principal')"):
                await evaluate(ws, LOGIN % (json.dumps(PRINCIPAL), json.dumps(SECRET)))
                await asyncio.sleep(3.0)
            logged_in = await evaluate(ws, "!!document.querySelector('.os-app')")
            print(f"登录状态：{'已登录' if logged_in else '未登录（将按未登录渲染审计）'}")
            # 清掉手动覆盖，保证审计的是各形态的默认主题
            await evaluate(ws, "localStorage.removeItem('liuhao.mode'); localStorage.removeItem('liuhao.theme')")

            for mode, w, h, dpr, mobile in MODES:
                await cmd(ws, "Emulation.setDeviceMetricsOverride", {
                    "width": w, "height": h, "deviceScaleFactor": dpr, "mobile": mobile})
                theme_seen: set[str] = set()
                print(f"\n===== {mode}  {w}x{h} @{dpr}x")
                for key in keys:
                    await cmd(ws, "Page.navigate", {"url": f"{BASE}?p={key}"})
                    await asyncio.sleep(1.9)
                    await evaluate(ws, "window.dispatchEvent(new Event('resize'))")
                    await asyncio.sleep(0.5)
                    data = await evaluate(ws, PROBE)
                    problems = drain_problems()
                    for message in problems:
                        runtime.append((mode, key, message))
                    bad = [s for s in data["samples"] if classify(s)]
                    coverage += len(data["samples"])
                    approx = sum(1 for s in data["samples"] if s["approx"])
                    theme_seen.add(data["theme"])
                    flag = "OK" if not bad else f"{len(bad)} 处低对比"
                    print(f"  [{'PASS' if not bad and not problems else 'FAIL'}] "
                          f"{mode}/{key:<11} [{data['theme']:<5}] 样本 {len(data['samples']):>3}"
                          f"（近似背景 {approx:>2}）  {flag}"
                          + (f"  运行时问题 {len(problems)}" if problems else ""))
                    for s in bad:
                        violations.append((f"{mode}/{key}", classify(s), s))
                    if bad or problems:
                        await shot(ws, OUT / f"{mode}-{key}.png")

            await evaluate(ws, "localStorage.removeItem('liuhao.mode'); localStorage.removeItem('liuhao.theme')")
    finally:
        chrome.terminate()
        try:
            chrome.wait(timeout=10)
        except subprocess.TimeoutExpired:
            chrome.kill()
        shutil.rmtree(PROFILE, ignore_errors=True)

    print("\n" + "=" * 74)
    print(f"覆盖文本样本 {coverage} 条；低对比违规 {len(violations)} 处；运行时问题 {len(runtime)} 条")
    if violations:
        print(f"\n低对比明细（按 前景×背景 聚类，共 {len(violations)} 处）：")
        agg: dict[tuple, dict] = {}
        for where, why, s in violations:
            key = (tuple(s["fg"]), tuple(s["bg"]))
            a = agg.setdefault(key, {"n": 0, "sizes": set(), "where": set(), "samples": []})
            a["n"] += 1
            a["sizes"].add(f"{s['fontSize']:.0f}px/{s['fontWeight']}")
            a["where"].add(where)
            if len(a["samples"]) < 4:
                a["samples"].append((s["text"], s["path"]))
        for (fg, bg), a in sorted(agg.items(), key=lambda kv: kv[1]["n"], reverse=True):
            print(f"  前景 rgb{fg} 背景 rgb{bg}  次数 {a['n']}  字号 {sorted(a['sizes'])}")
            for txt, p in a["samples"]:
                print(f"      {txt!r}  {p}")
    if runtime:
        print("\n运行时问题：")
        for mode, key, message in runtime[:30]:
            print(f"  {mode}/{key}: {message}")
    print("=" * 74)
    return 1 if (violations or runtime) else 0


LOGIN = """
(() => {
  const set = (el, v) => {
    const d = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
    d.set.call(el, v);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  };
  const p = document.querySelector('#os-principal');
  const s = document.querySelector('#os-secret');
  if (!p || !s) return 'inputs-missing';
  set(p, %s); set(s, %s);
  const btn = document.querySelector('.os-login-card button[type=submit]');
  if (btn) btn.click();
  return 'submitted';
})()
"""


def find_chromium() -> Path | None:
    override = os.environ.get("LIUHAO_AUDIT_BROWSER")
    if override and Path(override).exists():
        return Path(override)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        base = Path(local) / "ms-playwright"
        if base.exists():
            for pattern in ("chromium-*/chrome-win64/chrome.exe",
                            "chromium_headless_shell-*/chrome-win64/headless_shell.exe"):
                hits = sorted(base.glob(pattern))
                if hits:
                    return hits[-1]
    for candidate in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                      r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                      "/usr/bin/chromium", "/usr/bin/google-chrome"):
        if Path(candidate).exists():
            return Path(candidate)
    return None


def reachable(url: str) -> bool:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/v1/health", timeout=4) as r:
            return r.status < 500
    except Exception:  # noqa: BLE001
        return False


async def wait_for_target():
    deadline = time.time() + 40
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/list", timeout=2) as r:
                targets = json.load(r)
            for target in targets:
                if target.get("type") == "page":
                    return target["webSocketDebuggerUrl"]
        except Exception:  # noqa: BLE001
            await asyncio.sleep(0.3)
    return None


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
