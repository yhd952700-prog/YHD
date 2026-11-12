#!/usr/bin/env python
"""可复跑基线：双击启动器 → 登录框可见。

为什么需要它
------------
``startup_baseline.py`` 量的是"网关可用"，回答不了用户最直接的体感：
**双击之后多久能看见登录框**。这条链上还有启动器自身、就绪探测的轮询粒度、
浏览器进程冷启动、前端 JS 下载与 React 挂载——任何一段变慢都会让用户觉得
"点了没反应"。本脚本把这条链拆成带时间戳的分段，连续跑多次取中位数。

口径
----
* T0 = 本脚本 spawn ``scripts/launch_installed.py`` 的那一刻（模拟双击）；
* 启动器 stdout 的里程碑行（``正在启动…`` / ``[1/2]`` / ``网关就绪`` /
  ``[2/2]`` / ``贾维斯已就绪``）按到达时间打点，分段由相邻打点相减得到；
* 网关内部阶段（包导入 / lifespan 各段）来自 ``LIUHAO_STARTUP_PROFILE_OUT``
  落盘的 ``startup_profile`` JSON，T0' 是进程内"进入 src.gateway 包"；
* 浏览器段用 Playwright 真实启动 Chromium、导航到 ``/``，等到 ``#os-principal``
  可见；这代理的是生产里"启动器调 webbrowser.open + 默认浏览器冷启动"。
  真实默认浏览器可能比 headless Chromium 更慢，脚本会如实标注。
* 最终总计 = 启动器 T0 → ``贾维斯已就绪`` + Chromium 冷启动 + 页面可见。

拒绝脏数据
----------
机器 CPU 占用超过 ``--cpu-max`` 时**不出数**（退出码 5），除非显式 ``--force``；
``--force`` 出的结果会被标成"非基线"，只能用于同一窗口内的 A/B 相对比较。

用法
----
    .venv\\Scripts\\python.exe scripts\\acceptance\\startup_reach_login.py
    # 机器忙时先看窗口是否干净；确认只是短时抖动可加 --force 做 A/B：
    ... .py --force --runs 3 --warmups 0

产物
----
默认写到 ``D:\\_audit\\startup-reach-login\\<时间戳>\\``：每轮的启动器日志、
startup_profile JSON，以及汇总 ``summary.json``。
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VENV_PYTHON = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
LAUNCHER = REPO_ROOT / "scripts" / "launch_installed.py"
DEV_CONSOLE_DIST = REPO_ROOT / "apps" / "console" / "console" / "dist"

DEFAULT_OUT = Path(r"D:\_audit\startup-reach-login")
DEFAULT_DATA = DEFAULT_OUT / "data"

TARGET_MS = 5000.0

#: 启动器 stdout 里程碑 → 分段名。子串匹配，容忍打印措辞的小改动。
MILESTONES = (
    ("launcher_env", "正在启动"),
    ("wizard_done", "跳过首启检查"),
    ("gateway_spawn", "[1/2]"),
    ("gateway_wait", "网关启动中"),
    ("gateway_ready", "网关就绪（"),
    ("gateway_ready_warn", "核心可用，但后台组件未就绪"),
    ("console_check", "[2/2]"),
    ("banner", "贾维斯已就绪"),
)

SEED_CODE = r"""
import os, sys
ROOT = {root!r}
DATA = {data!r}
sys.path.insert(0, ROOT)
os.environ["LIUHAO_DATA_DIR"] = DATA
from scripts.launch_installed import prepare_environment
prepare_environment()
sys.argv = [
    "register_human_identity.py",
    "--principal", {principal!r},
    "--display-name", "Startup Probe",
    "--password-stdin",
]
import runpy
try:
    runpy.run_path(os.path.join(ROOT, "scripts", "register_human_identity.py"), run_name="__main__")
except SystemExit as exc:
    raise SystemExit(exc.code if isinstance(exc.code, int) else 0)
"""


def _python() -> Path:
    return VENV_PYTHON if VENV_PYTHON.exists() else Path(sys.executable)


def _free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _port_open(port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _load_module():
    try:
        import psutil  # noqa: F401
    except ImportError:  # pragma: no cover - 只在没有 psutil 的环境出现
        return None
    return psutil


def _machine_snapshot(samples: int, interval: float) -> dict:
    psutil = _load_module()
    if psutil is None:
        return {
            "cpu_percent": None,
            "cpu_samples": [],
            "python_processes": None,
            "note": "psutil 不可用，无法采样 CPU；请用仓库 .venv 的 python 运行",
        }
    values = [round(psutil.cpu_percent(interval=interval), 1) for _ in range(max(1, samples))]
    count = 0
    for proc in psutil.process_iter(["name"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if name.startswith("python"):
                count += 1
        except Exception:  # noqa: BLE001 - 进程可能随时退出
            continue
    return {
        "cpu_percent": round(statistics.mean(values), 1),
        "cpu_samples": values,
        "python_processes": count,
        "note": "",
    }


def _seed_identity(data_dir: Path, principal: str, password: str) -> None:
    identities = data_dir / "config" / "human_identities.json"
    if identities.exists() and identities.stat().st_size > 2:
        return
    data_dir.mkdir(parents=True, exist_ok=True)
    code = SEED_CODE.format(root=str(REPO_ROOT), data=str(data_dir), principal=principal)
    env = dict(os.environ)
    env.pop("LIUHAO_ALLOW_ENV_OVERRIDE", None)
    proc = subprocess.run(
        [str(_python()), "-c", code],
        input=password + "\n",
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        timeout=300,
    )
    if proc.returncode != 0:
        raise SystemExit(
            "种子账号创建失败（用于让登录框停在 login 阶段而不是首启向导）：\n"
            f"stdout:\n{proc.stdout[-1200:]}\nstderr:\n{proc.stderr[-1200:]}"
        )


def _default_browser_exe() -> Path | None:
    """Playwright 版本与已下载浏览器版本不一致时的兜底。

    ``playwright install`` 下载的是固定版本目录；仓库里的 playwright 包有时
    比本机已下载的浏览器新一号。这里直接找已存在的 Chromium，避免为了跑一条
    基线去下载一个几百 MB 的浏览器。
    """
    root = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    candidates: list[Path] = []
    for pattern in (
        "chromium-*/chrome-win/chrome.exe",
        "chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe",
    ):
        candidates.extend(root.glob(pattern))

    def _version(path: Path) -> int:
        for part in path.parts[::-1]:
            if part.startswith("chromium-"):
                tail = part.split("-", 1)[1]
                if tail.isdigit():
                    return int(tail)
        return 0

    candidates.sort(key=_version, reverse=True)
    return candidates[0] if candidates else None


def _kill_tree(pid: int) -> None:
    psutil = _load_module()
    if psutil is not None:
        try:
            parent = psutil.Process(pid)
            for child in parent.children(recursive=True):
                try:
                    child.kill()
                except Exception:  # noqa: BLE001
                    pass
            parent.kill()
        except Exception:  # noqa: BLE001
            pass
        return
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)


def _run_once(
    *,
    run_dir: Path,
    data_dir: Path,
    console_dist: Path,
    port: int,
    timeout_s: float,
    playwright,
    browser_exe: Path | None = None,
    url_scheme: str = "http",
) -> dict:
    run_dir.mkdir(parents=True, exist_ok=True)
    profile_out = run_dir / "startup_profile.json"
    if profile_out.exists():
        profile_out.unlink()

    env = dict(os.environ)
    env.pop("LIUHAO_ALLOW_ENV_OVERRIDE", None)
    env.pop("LIUHAO_STARTUP_PROFILE", None)
    env["PYTHONUNBUFFERED"] = "1"
    env["LIUHAO_DATA_DIR"] = str(data_dir)
    env["LIUHAO_CONSOLE_DIST"] = str(console_dist)
    env["LIUHAO_STARTUP_PROFILE_OUT"] = str(profile_out)

    cmd = [
        str(_python()),
        str(LAUNCHER),
        "--skip-wizard",
        "--api-port",
        str(port),
        "--no-browser",
    ]
    t0 = time.perf_counter()
    proc = subprocess.Popen(
        cmd,
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    marks: dict[str, float] = {}
    lines: list[tuple[float, str]] = []
    banner = threading.Event()
    ready = threading.Event()

    def _reader() -> None:
        assert proc.stdout is not None
        for raw in proc.stdout:
            elapsed = (time.perf_counter() - t0) * 1000.0
            line = raw.rstrip("\r\n")
            lines.append((elapsed, line))
            # 里程碑只记第一次命中，后续进度行不覆盖。
            for name, needle in MILESTONES:
                if name not in marks and needle in line:
                    marks[name] = elapsed
                    if name in ("gateway_ready", "gateway_ready_warn"):
                        ready.set()
                    if name == "banner":
                        banner.set()
            if "贾维斯已就绪" in line:
                banner.set()
            if "[FAIL]" in line:
                ready.set()

    reader = threading.Thread(target=_reader, daemon=True)
    reader.start()

    try:
        if not banner.wait(timeout=timeout_s):
            tail = "\n".join(line for _, line in lines[-25:])
            raise RuntimeError(f"启动器在 {timeout_s:.0f}s 内没有打印「贾维斯已就绪」。最后输出：\n{tail}")
        if "gateway_ready" not in marks and "gateway_ready_warn" not in marks:
            raise RuntimeError("就绪里程碑缺失（启动器没有报告网关就绪）")
        t_banner = marks["banner"]

        # 生产里 webbrowser.open 就在这段之后；--no-browser 下它被跳过。
        url = f"{url_scheme}://127.0.0.1:{port}/"
        t_browser = time.perf_counter()
        launch_kwargs = {"headless": True}
        if browser_exe is not None:
            launch_kwargs["executable_path"] = str(browser_exe)
        browser = playwright.chromium.launch(**launch_kwargs)
        browser_launch_ms = (time.perf_counter() - t_browser) * 1000.0
        try:
            page = browser.new_page()
            t_nav = time.perf_counter()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_s * 1000.0)
            page.wait_for_selector("#os-principal", state="visible", timeout=timeout_s * 1000.0)
            page_visible_ms = (time.perf_counter() - t_nav) * 1000.0
        finally:
            browser.close()

        result = {
            "marks_ms": {name: round(value, 1) for name, value in marks.items()},
            "segments_ms": {},
            "profile": None,
            "url": url,
            "launcher_log_tail": [line for _, line in lines[-12:]],
        }
        segs = result["segments_ms"]
        if "launcher_env" in marks:
            segs["launcher_python_env"] = round(marks["launcher_env"], 1)
        if "launcher_env" in marks and "wizard_done" in marks:
            segs["launcher_wizard_check"] = round(marks["wizard_done"] - marks["launcher_env"], 1)
        if "wizard_done" in marks and "gateway_spawn" in marks:
            segs["launcher_arg_setup"] = round(marks["gateway_spawn"] - marks["wizard_done"], 1)
        ready_mark = marks.get("gateway_ready", marks.get("gateway_ready_warn"))
        if ready_mark is not None and "gateway_spawn" in marks:
            segs["gateway_process_to_ready"] = round(ready_mark - marks["gateway_spawn"], 1)
        if ready_mark is not None and "console_check" in marks:
            segs["console_probe"] = round(marks["console_check"] - ready_mark, 1)
        if "console_check" in marks:
            segs["post_ready_banner"] = round(t_banner - marks["console_check"], 1)
        segs["launcher_total"] = round(t_banner, 1)
        segs["browser_launch"] = round(browser_launch_ms, 1)
        segs["page_to_login_visible"] = round(page_visible_ms, 1)
        segs["total"] = round(t_banner + browser_launch_ms + page_visible_ms, 1)
        if profile_out.exists():
            try:
                result["profile"] = json.loads(profile_out.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                result["profile"] = {"error": str(exc)}
        (run_dir / "launcher.log").write_text(
            "\n".join(f"{elapsed:8.1f}ms {line}" for elapsed, line in lines),
            encoding="utf-8",
        )
        return result
    finally:
        _kill_tree(proc.pid)
        deadline = time.perf_counter() + 6.0
        while _port_open(port) and time.perf_counter() < deadline:
            time.sleep(0.05)


def _gateway_internal(profile: dict | None) -> dict:
    """从 startup_profile JSON 里抽出可比的分段（ms）。"""
    if not profile:
        return {}
    stages = profile.get("stages") or []
    total_ms = float(profile.get("elapsed_ms") or 0.0)
    by_name = {row.get("name"): float(row.get("delta_ms") or 0.0) for row in stages}
    import_ms = 0.0
    for row in stages:
        name = str(row.get("name") or "")
        if name.startswith("就绪"):
            break
        import_ms += float(row.get("delta_ms") or 0.0)
    return {
        "profile_total_ms": round(total_ms, 1),
        "profile_import_ms": round(import_ms, 1),
        "profile_stages": [
            {"name": row.get("name"), "elapsed_ms": row.get("elapsed_ms"), "delta_ms": row.get("delta_ms")}
            for row in stages
        ],
        "background": profile.get("background"),
        "by_name": by_name,
    }


def _median(values: list[float]) -> float:
    return round(statistics.median(values), 1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="双击启动器 → 登录框可见的可复跑基线")
    parser.add_argument("--runs", type=int, default=3, help="测量的轮数（默认 3，取中位数）")
    parser.add_argument("--warmups", type=int, default=1, help="不计入结果的预热轮数")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="产物根目录")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA), help="安装版数据目录（跨轮复用）")
    parser.add_argument("--console-dist", default=str(DEV_CONSOLE_DIST), help="控制台构建产物目录（只读）")
    parser.add_argument("--browser-exe", default="", help="Chromium 可执行文件；留空则自动找本机已下载的版本")
    parser.add_argument("--principal", default="startup.probe", help="种子账号名")
    parser.add_argument("--password", default="StartupProbe123!", help="种子账号口令")
    parser.add_argument("--timeout", type=float, default=120.0, help="单轮启动与页面等待上限（秒）")
    parser.add_argument("--cpu-max", type=float, default=25.0, help="CPU 占用高于该值拒绝出数")
    parser.add_argument("--cpu-samples", type=int, default=3, help="CPU 采样次数")
    parser.add_argument("--cpu-interval", type=float, default=1.0, help="CPU 采样间隔（秒）")
    parser.add_argument("--max-wait", type=float, default=300.0, help="等干净窗口的最长秒数；0=立即拒绝")
    parser.add_argument("--force", action="store_true", help="负载不达标也出数（标为非基线）")
    args = parser.parse_args(argv)

    if not LAUNCHER.exists():
        print(f"[FAIL] 找不到启动器：{LAUNCHER}", file=sys.stderr)
        return 1
    if not (Path(args.console_dist) / "index.html").exists():
        print(f"[FAIL] 控制台产物不存在：{args.console_dist}/index.html", file=sys.stderr)
        return 1

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "[FAIL] 缺少 playwright。请用仓库 .venv 运行，或先安装：\n"
            "       .venv\\Scripts\\python.exe -m pip install playwright && "
            ".venv\\Scripts\\python.exe -m playwright install chromium",
            file=sys.stderr,
        )
        return 2

    # ---- 负载窗口：脏窗口不出数 ----------------------------------------
    deadline = time.perf_counter() + max(0.0, args.max_wait)
    while True:
        snap = _machine_snapshot(args.cpu_samples, args.cpu_interval)
        cpu = snap.get("cpu_percent")
        clean = cpu is not None and cpu <= args.cpu_max
        if clean or args.force:
            break
        if time.perf_counter() >= deadline:
            print(
                f"[REFUSE] 机器负载过高（CPU {cpu}% > {args.cpu_max}%，"
                f"python 进程 {snap.get('python_processes')} 个），拒绝出基线数据。\n"
                "         这份数字会被别人当基线，宁可不出。等负载降下来再跑，\n"
                "         或用 --force 只做同一窗口的 A/B（结果会标成非基线）。",
                file=sys.stderr,
            )
            return 5
        print(f"[wait] CPU {cpu}% > {args.cpu_max}%，20s 后再采样……", flush=True)
        time.sleep(20.0)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_root = Path(args.out) / stamp
    out_root.mkdir(parents=True, exist_ok=True)
    data_dir = Path(args.data_dir)
    _seed_identity(data_dir, args.principal, args.password)

    if not snap.get("cpu_percent"):
        print(f"[warn] {snap.get('note')}", file=sys.stderr)
    if args.force and (snap.get("cpu_percent") or 0) > args.cpu_max:
        print(
            f"[NON-BASELINE] --force：CPU {snap.get('cpu_percent')}% > {args.cpu_max}%。"
            "本轮数字只能用于同窗口 A/B，不可作为基线。",
            flush=True,
        )

    runs: list[dict] = []
    browser_exe = Path(args.browser_exe) if args.browser_exe else _default_browser_exe()
    with sync_playwright() as pw:
        total_rounds = max(0, args.warmups) + max(1, args.runs)
        for index in range(total_rounds):
            port = _free_port()
            label = f"run{index + 1}"
            print(f"[{label}] 启动器 + Chromium（端口 {port}）……", flush=True)
            result = _run_once(
                run_dir=out_root / label,
                data_dir=data_dir,
                console_dist=Path(args.console_dist),
                port=port,
                timeout_s=args.timeout,
                playwright=pw,
                browser_exe=browser_exe,
            )
            result["warmup"] = index < args.warmups
            result["round"] = index + 1
            if result["warmup"]:
                print(f"[{label}] 预热完成：{result['segments_ms']['total']} ms（不计入中位数）", flush=True)
            else:
                runs.append(result)
                print(f"[{label}] 总计 {result['segments_ms']['total']} ms "
                      f"（启动器 {result['segments_ms']['launcher_total']} + "
                      f"浏览器 {result['segments_ms']['browser_launch']} + "
                      f"页面 {result['segments_ms']['page_to_login_visible']}）", flush=True)

    if not runs:
        print("[FAIL] 没有完成任何测量轮", file=sys.stderr)
        return 1

    seg_keys = sorted({key for run in runs for key in run["segments_ms"]})
    median_segments = {key: _median([run["segments_ms"][key] for run in runs]) for key in seg_keys}
    total_median = median_segments["total"]

    # 取"总耗时最接近中位"的那一轮，展示网关内部阶段，避免拿极端轮误导。
    representative = min(runs, key=lambda run: abs(run["segments_ms"]["total"] - total_median))
    internal = _gateway_internal(representative.get("profile"))
    if internal and internal.get("profile_total_ms"):
        gw_total = median_segments.get("gateway_process_to_ready")
        if gw_total is not None:
            interpreter_ms = max(0.0, gw_total - internal["profile_total_ms"])
            median_segments["gateway_interpreter_and_pkg"] = round(interpreter_ms, 1)

    summary = {
        "generated_at": stamp,
        "clean": bool(snap.get("cpu_percent") is not None and (snap.get("cpu_percent") or 0) <= args.cpu_max),
        "forced": bool(args.force),
        "machine": snap,
        "runs": runs,
        "median_segments_ms": median_segments,
        "representative_profile": internal,
        "target_ms": TARGET_MS,
        "met_target": total_median <= TARGET_MS,
        "notes": [
            "总计时不含启动器调用 webbrowser.open 的几十毫秒（--no-browser 跳过），"
            "浏览器冷启动以 headless Chromium 代理；系统默认浏览器首次冷启动可能更慢。",
            "T0 为 spawn launch_installed.py 的时刻；网关进程内部 T0' 晚于它约等于解释器启动+包导入之前的部分。",
        ],
    }
    (out_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print()
    print("=" * 78)
    print(f"双击 → 登录框可见：{total_median:.0f} ms（目标 {TARGET_MS:.0f} ms，"
          f"{'达标' if total_median <= TARGET_MS else '未达标'}）"
          + ("   [非基线：--force]" if summary["forced"] and not summary["clean"] else ""))
    print("=" * 78)
    print(f"{'分段':<30}{'中位(ms)':>12}  说明")
    labels = {
        "launcher_python_env": "启动器 Python 启动 + 环境准备",
        "launcher_wizard_check": "首启检查（gui 导入 + 判定）",
        "launcher_arg_setup": "参数/端口解析 + 拉起网关",
        "gateway_process_to_ready": "网关进程启动 → /v1/startup ready",
        "gateway_interpreter_and_pkg": "  其中解释器 + 包导入前",
        "console_probe": "控制台就绪探测（/ 请求）",
        "post_ready_banner": "写当前地址 + 打印就绪横幅",
        "launcher_total": "启动器合计（T0 → 贾维斯已就绪）",
        "browser_launch": "Chromium 冷启动（代理默认浏览器）",
        "page_to_login_visible": "导航 / → #os-principal 可见",
        "total": "总计",
    }
    for key in sorted(median_segments):
        print(f"{labels.get(key, key):<30}{median_segments[key]:>12.1f}  {'' if key in labels else key}")

    if internal and internal.get("profile_stages"):
        print()
        print("网关内部阶段（取自最接近中位的一轮，T0' = 进入 src.gateway 包）：")
        print(f"{'阶段':<46}{'累计(ms)':>10}{'本段(ms)':>10}")
        for row in internal["profile_stages"]:
            name = str(row.get("name") or "")
            elapsed = float(row.get("elapsed_ms") or 0.0)
            delta = float(row.get("delta_ms") or 0.0)
            if delta < 15.0 and not name.startswith("就绪"):
                continue
            print(f"{name:<46}{elapsed:>10.1f}{delta:>10.1f}")

    print()
    print(f"产物：{out_root}\\summary.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
