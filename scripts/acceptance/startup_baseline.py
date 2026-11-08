"""启动基线采集：自己等「干净窗口」，等到了才测，一次跑完四项。

为什么需要这个脚本
------------------
``docs/audit-size-startup-20261108.md`` §9 把干净基线列成待办，但人工等窗口的
结果就是「永远在等」：这台开发机常年有 30+ 个 python 进程和多条 agent 线并发，
99% CPU 下测出来的绝对值会被同比放大 2-3 倍，没法当锚点。

所以把判定条件写进脚本：CPU 持续低于阈值、python 进程数低于阈值、目标端口空闲，
三条全过才开跑；任何一条不过就打印「窗口不干净」并说明是哪一条，退出码非零。
默认会一直等到 ``--max-wait``（默认 1 小时），以后不用人喊。

四项采集（顺序固定，必须在同一窗口内连续跑完）
--------------------------------------------
1. 冷启动：全新数据目录，进程创建 → ``GET /v1/ready`` 返回 200 的墙钟时间；
2. 热启动 ×3：复用同一数据目录，各跑一次，取中位；
3. 冷 import 图：``python -X importtime -c "import src.gateway.main"``，按累计排序；
4. 体积对账：遍历指定根目录（默认已安装 ``D:\\LiuHao``），给总量与分桶。

产物：``<out>/baseline.json`` + ``<out>/summary.txt``（一页摘要）+ 每次启动的
profile JSON / stdout / stderr。

用法::

    .venv\\Scripts\\python.exe scripts/acceptance/startup_baseline.py
    ... --out-dir D:\\_audit\\startup-baseline --size-root dist\\perf-20261108\\payload\\app

退出码：0 采集完成；1 采集过程失败；2 窗口不干净（未开跑）。

口径提醒：本脚本自己只测「进程创建 → /v1/ready 200」，内部阶段耗时来自
``src/gateway/startup_profile.py`` 写出的 JSON（``LIUHAO_STARTUP_PROFILE``）。
两者分母不同，报告里要分开写，不要混着引用。
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import shutil
import socket
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUNTIME = Path(r"D:\LiuHao\runtime\python.exe")
DEFAULT_SIZE_ROOT = Path(r"D:\LiuHao")
DEFAULT_OUT_ROOT = Path(r"D:\_audit\startup-baseline")

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:  # pragma: no cover - 老解释器/重定向
    pass

try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover - 用 ctypes 兜底，脚本仍能跑
    psutil = None  # type: ignore

#: 采集期间必须干净；阈值刻意保守，宁可多等一个窗口，也不要产出被负载污染的锚点。
DEFAULT_CPU_MAX = 15.0
DEFAULT_PY_MAX = 5
DEFAULT_PORT = 19011
#: `-X importtime` 的行格式：``import time: self_us | cumulative | <缩进>模块名``
IMPORTTIME_RE = re.compile(r"^import time:\s+(\d+)\s+\|\s+(\d+)\s+\|\s+(.*)$")


# ─────────────────────────────────────────────────────────── 窗口判定
def _sample_cpu(samples: int, interval: float) -> List[float]:
    values: List[float] = []
    if psutil is not None:
        for _ in range(samples):
            values.append(float(psutil.cpu_percent(interval=interval)))
        return values
    # 兜底：连续两次 GetSystemTimes 差分，逐次算整机占用率。
    prev = _system_times()
    for _ in range(samples):
        time.sleep(interval)
        curr = _system_times()
        total = (curr[0] - prev[0]) + (curr[1] - prev[1]) + (curr[2] - prev[2])
        busy = total - (curr[0] - prev[0])
        values.append(100.0 * busy / total if total else 0.0)
        prev = curr
    return values


def _system_times() -> Tuple[int, int, int]:
    idle, kernel, user = ctypes.c_ulonglong(), ctypes.c_ulonglong(), ctypes.c_ulonglong()
    ctypes.windll.kernel32.GetSystemTimes(
        ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
    )
    return int(idle.value), int(kernel.value), int(user.value)


def _python_processes() -> List[Dict[str, Any]]:
    """统计本机 python/pythonw 进程（排除本脚本自己与其父链）。"""
    me = os.getpid()
    skip = {me}
    if psutil is not None:
        try:
            parent = psutil.Process(me).parent()
            while parent is not None:
                skip.add(parent.pid)
                parent = parent.parent()
        except Exception:  # noqa: BLE001
            pass
        found = []
        for proc in psutil.process_iter(["pid", "name", "cmdline", "cpu_percent"]):
            info = proc.info
            name = (info.get("name") or "").lower()
            if info.get("pid") in skip:
                continue
            if name in ("python.exe", "pythonw.exe", "python", "pythonw"):
                found.append(
                    {
                        "pid": info.get("pid"),
                        "name": name,
                        "cpu_percent": info.get("cpu_percent"),
                        "cmdline": " ".join(info.get("cmdline") or [])[:160],
                    }
                )
        return found
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq python.exe", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout
    found = []
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[1].isdigit() and int(parts[1]) not in skip:
            found.append({"pid": int(parts[1]), "name": parts[0], "cmdline": ""})
    return found


def _port_free(host: str, port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        sock.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def check_window(args: argparse.Namespace) -> Dict[str, Any]:
    """一次判定；返回可直接写进 JSON 的明细。"""
    cpu = _sample_cpu(args.cpu_samples, args.cpu_interval)
    procs = _python_processes()
    port_free = _port_free("127.0.0.1", args.port)
    cpu_ok = all(value <= args.cpu_max for value in cpu)
    py_ok = len(procs) <= args.py_max
    return {
        "cpu_samples": [round(v, 1) for v in cpu],
        "cpu_max": args.cpu_max,
        "cpu_ok": cpu_ok,
        "python_count": len(procs),
        "python_max": args.py_max,
        "python_ok": py_ok,
        "python_processes": procs[:20],
        "port": args.port,
        "port_free": port_free,
        "clean": cpu_ok and py_ok and port_free,
    }


def wait_for_window(args: argparse.Namespace) -> Dict[str, Any]:
    """反复判定直到干净或超时；每轮打印一行「哪个条件没过」。"""
    deadline = time.monotonic() + args.max_wait
    attempt = 0
    while True:
        attempt += 1
        state = check_window(args)
        state["attempt"] = attempt
        stamp = datetime.now().strftime("%H:%M:%S")
        if state["clean"]:
            print(f"[{stamp}] 窗口干净（第 {attempt} 次判定）：CPU {state['cpu_samples']} "
                  f"python {state['python_count']} port {state['port']} 空闲")
            return state
        failed = []
        if not state["cpu_ok"]:
            failed.append(
                f"CPU 超过阈值（采样 {state['cpu_samples']} > {args.cpu_max}%）"
            )
        if not state["python_ok"]:
            failed.append(
                f"python 进程过多（{state['python_count']} > {args.py_max}）"
            )
        if not state["port_free"]:
            failed.append(f"端口 {state['port']} 被占用")
        print(f"[{stamp}] 窗口不干净（第 {attempt} 次）：" + "；".join(failed))
        if args.max_wait <= 0 or time.monotonic() >= deadline:
            state["failed"] = failed
            return state
        time.sleep(args.poll_interval)


# ─────────────────────────────────────────────────────────── 启动测量
def _base_env(data_dir: Path, port: int, args: argparse.Namespace) -> Dict[str, str]:
    env = dict(os.environ)
    # 与安装版启动器（scripts/start_liuhao.py）一致：所有业务库都落在数据目录。
    # 不设这些变量时，冷启动会在审计库检查上返回 503，readiness 永远等不到 200，
    # 那不是启动慢，而是"没有审计库路径"——属于采集环境缺陷，必须在这里补齐。
    data = data_dir / "data"
    for sub in ("", "observability"):
        (data / sub).mkdir(parents=True, exist_ok=True)
    env.update(
        {
            "LIUHAO_DATA_DIR": str(data_dir),
            "LIUHAO_WORKSPACE_ROOT": str(data_dir / "workspace"),
            "PYTHONPATH": str(getattr(args, "app_root", REPO_ROOT)),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "HOST": "127.0.0.1",
            "PORT": str(port),
            "LIUHAO_STARTUP_PROFILE": "1",
            "CONVERSATION_DB_PATH": str(data_dir / "conversation_store.db"),
            "AUDIT_DB_PATH": str(data_dir / "audit_store.db"),
            "AUDIT_DB_BACKUP_PATH": str(data_dir / "audit_store.db.backup"),
            "LIUHAO_JWT_REVOCATION_DB": str(data_dir / "liuhao_jwt_revocation.db"),
            "MEMORY_DB_PATH": str(data_dir / "memory_store.db"),
            "PERSONAL_CONTEXT_DB_PATH": str(data_dir / "personal_context.db"),
            "LIUHAO_KNOWLEDGE_DB": str(data / "knowledge_vectors.sqlite3"),
            "LIUHAO_ALERTS_STORE_PATH": str(data / "observability" / "alerts.json"),
            "LIUHAO_MODEL_CONFIG_DB": str(data / "model_registry.sqlite3"),
            "LIUHAO_MODEL_CONFIG_KEY_FILE": str(data / "model_config.key"),
            "LIUHAO_HUMAN_IDENTITIES_FILE": str(data_dir / "config" / "human_identities.json"),
            "LIUHAO_AUTH_SECRETS_FILE": str(data_dir / "config" / "auth_secrets.json"),
        }
    )
    if args.console_dist and Path(args.console_dist).is_dir():
        env["LIUHAO_CONSOLE_DIST"] = str(args.console_dist)
    # 数据目录里已生成过配置时沿用；冷启动第一次没有这些文件，交给应用自举。
    app_env = data_dir / "config" / "app.env"
    if app_env.is_file():
        for line in app_env.read_text(encoding="utf-8", errors="replace").splitlines():
            match = re.match(r"^\s*([A-Z_][A-Z0-9_]*)=(.*)$", line)
            if match:
                env[match.group(1)] = match.group(2).strip()
    key_file = data_dir / "config" / "integrity.key"
    if key_file.is_file():
        env["LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY"] = key_file.read_text(
            encoding="utf-8", errors="replace"
        ).strip()
    return env


def _http_status(url: str, timeout: float = 1.0) -> Optional[int]:
    return _http_probe(url, timeout)[0]


def _http_probe(url: str, timeout: float = 1.0) -> Tuple[Optional[int], Optional[Dict[str, Any]]]:
    """返回 ``(状态码, JSON 体)``；连不上时两者都是 None。"""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            code = int(response.status)
            raw = response.read()
    except urllib.error.HTTPError as exc:
        code = int(exc.code)
        try:
            raw = exc.read()
        except Exception:  # noqa: BLE001
            raw = b""
    except Exception:  # noqa: BLE001 - 连接未就绪是常态
        return None, None
    try:
        return code, json.loads(raw.decode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        return code, None


def _degraded_checks(body: Optional[Dict[str, Any]]) -> List[str]:
    """从 ``/v1/ready`` 或 ``/v1/health`` 的响应里挑出非 healthy 的检查名。

    503 只说明"有检查没过"，真正的原因是哪个检查——把名字列出来，报告里才
    能写"是 ai_provider 没配"还是"数据库不可用"，而不是笼统的"没就绪"。
    """
    checks = (body or {}).get("checks") or {}
    return [name for name, detail in checks.items()
            if isinstance(detail, dict) and detail.get("status") != "healthy"]


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
            capture_output=True,
            timeout=20,
        )
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
    try:
        proc.wait(timeout=10)
    except Exception:  # noqa: BLE001
        pass


def run_once(
    label: str,
    data_dir: Path,
    args: argparse.Namespace,
    out_dir: Path,
) -> Dict[str, Any]:
    """启动一次网关，量到 /v1/ready 200 为止，然后杀掉。"""
    profile_path = out_dir / f"profile-{label}.json"
    env = _base_env(data_dir, args.port, args)
    env["LIUHAO_STARTUP_PROFILE_OUT"] = str(profile_path)
    if profile_path.exists():
        profile_path.unlink()
    stdout_path = out_dir / f"{label}.out.log"
    stderr_path = out_dir / f"{label}.err.log"
    started = time.perf_counter()
    with stdout_path.open("w", encoding="utf-8", errors="replace") as out, \
            stderr_path.open("w", encoding="utf-8", errors="replace") as err:
        proc = subprocess.Popen(
            [str(args.interpreter), "-u", "-m", "src.gateway"],
            cwd=str(args.app_root),
            env=env,
            stdout=out,
            stderr=err,
        )
    first_status: Optional[int] = None
    first_ms: Optional[float] = None
    ready_ms: Optional[float] = None
    first_body: Optional[Dict[str, Any]] = None
    last_body: Optional[Dict[str, Any]] = None
    deadline = started + args.startup_timeout
    try:
        while time.perf_counter() < deadline:
            if proc.poll() is not None:
                break
            status, body = _http_probe(f"http://127.0.0.1:{args.port}/v1/ready")
            if status is not None:
                last_body = body
            if status is not None and first_ms is None:
                first_status = status
                first_ms = round((time.perf_counter() - started) * 1000, 0)
                first_body = body
            if status == 200:
                ready_ms = round((time.perf_counter() - started) * 1000, 0)
                break
            time.sleep(0.04)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 0)
    finally:
        healthy = None
        if ready_ms:
            health_code, health_body = _http_probe(
                f"http://127.0.0.1:{args.port}/v1/health"
            )
            healthy = health_code if health_code is not None else None
            result_health_body = health_body
        else:
            result_health_body = None
        _kill_tree(proc)
    profile = None
    if profile_path.is_file():
        try:
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            profile = {"error": str(exc)}
    result = {
        "label": label,
        "data_dir": str(data_dir),
        "ready_ms": ready_ms,
        "first_http_status": first_status,
        "first_http_ms": first_ms,
        "first_http_degraded": _degraded_checks(first_body),
        "last_http_degraded": _degraded_checks(last_body),
        "health_degraded": _degraded_checks(result_health_body),
        "elapsed_ms": elapsed_ms,
        "terminated_by_collector": True,
        "health_status": healthy,
        "profile_present": profile is not None,
        "internal_total_ms": (profile or {}).get("elapsed_ms"),
        "stages": (profile or {}).get("stages"),
        "stdout_log": str(stdout_path),
        "stderr_log": str(stderr_path),
    }
    print(f"  {label:12s} ready={ready_ms} ms  internal={result['internal_total_ms']} ms "
          f"health={healthy}")
    return result


# ─────────────────────────────────────────────────────────── import 图
def import_graph(args: argparse.Namespace, out_dir: Path, data_dir: Path) -> Dict[str, Any]:
    env = _base_env(data_dir, args.port, args)
    env.pop("LIUHAO_STARTUP_PROFILE", None)
    env.pop("LIUHAO_STARTUP_PROFILE_OUT", None)
    log_path = out_dir / "importtime-cold.log"
    code = "import src.gateway.main"
    started = time.perf_counter()
    with log_path.open("w", encoding="utf-8", errors="replace") as handle:
        subprocess.run(
            [str(args.interpreter), "-X", "importtime", "-c", code],
            cwd=str(args.app_root),
            env=env,
            stdout=handle,
            stderr=handle,
        )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 0)
    rows: List[Dict[str, Any]] = []
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = IMPORTTIME_RE.match(line.strip())
        if not match:
            continue
        self_us = int(match.group(1))
        cum_us = int(match.group(2))
        name = match.group(3).strip()
        rows.append(
            {"name": name, "self_ms": round(self_us / 1000, 1),
             "cumulative_ms": round(cum_us / 1000, 1)}
        )
    top_cum = sorted(rows, key=lambda r: -r["cumulative_ms"])[:30]
    top_self = sorted(rows, key=lambda r: -r["self_ms"])[:20]
    print(f"  冷 import 图：{len(rows)} 个模块，墙钟 {elapsed_ms} ms")
    return {
        "command": f"{args.interpreter} -X importtime -c \"{code}\"",
        "wall_ms": elapsed_ms,
        "module_count": len(rows),
        "top_cumulative": top_cum,
        "top_self": top_self,
        "log": str(log_path),
    }


# ─────────────────────────────────────────────────────────── 体积对账
def size_report(root: Path) -> Dict[str, Any]:
    total = 0
    count = 0
    buckets: Dict[str, List[int]] = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            path = os.path.join(dirpath, name)
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            total += size
            count += 1
            rel = os.path.relpath(path, root)
            parts = Path(rel).parts
            key = parts[0] if len(parts) > 1 else "(root files)"
            if key == "runtime" and len(parts) > 2:
                key = f"runtime\\{parts[1]}"
            bucket = buckets.setdefault(key, [0, 0])
            bucket[0] += size
            bucket[1] += 1
    rows = sorted(
        ({"bucket": k, "miB": round(v[0] / 1048576, 2), "files": v[1]}
         for k, v in buckets.items()),
        key=lambda row: -row["miB"],
    )
    print(f"  体积：{count} 文件 / {total / 1048576:.2f} MiB（{root}）")
    return {
        "root": str(root),
        "files": count,
        "bytes": total,
        "miB": round(total / 1048576, 2),
        "buckets": rows,
    }


# ─────────────────────────────────────────────────────────── 摘要
def write_summary(result: Dict[str, Any], out_dir: Path) -> Path:
    cold = result.get("cold") or {}
    warm = result.get("warm") or []
    graph = result.get("import_graph") or {}
    size = result.get("size") or {}
    warm_ready = [r["first_http_ms"] for r in warm if r.get("first_http_ms")]
    warm_internal = [r["internal_total_ms"] for r in warm if r.get("internal_total_ms")]
    lines = [
        "启动基线" + ("（干净窗口）" if result.get("clean") else
                       "（调试采集：窗口不干净，数字不能当锚点）"),
        f"采集时间：{result['collected_at']}",
        f"解释器：{result['interpreter']}",
        f"窗口：CPU {result['window']['cpu_samples']}（阈值 {result['window']['cpu_max']}%），"
        f"python 进程 {result['window']['python_count']}（阈值 {result['window']['python_max']}），"
        f"端口 {result['window']['port']} 空闲",
        "",
        "① 冷启动（全新数据目录）",
        f"   进程创建 → 首个 HTTP 响应：{cold.get('first_http_ms')} ms"
        f"（状态 {cold.get('first_http_status')}）",
        f"   /v1/ready 200：{cold.get('ready_ms')} ms",
        f"   非 healthy 检查：{cold.get('first_http_degraded') or '无'}",
        f"   内部阶段合计（T0 → core ready）：{cold.get('internal_total_ms')} ms",
        "",
        "② 热启动 ×3（同一数据目录）",
        f"   首个 HTTP 响应：[{[r.get('first_http_ms') for r in warm]}] ms，中位 "
        f"{round(statistics.median(warm_ready), 0) if warm_ready else None} ms",
        f"   内部合计：{warm_internal} ms，中位 "
        f"{round(statistics.median(warm_internal), 0) if warm_internal else None} ms",
        "",
        "③ 冷 import 图",
        f"   {graph.get('module_count')} 个模块，墙钟 {graph.get('wall_ms')} ms",
        "   累计 Top 8：",
    ]
    for row in (graph.get("top_cumulative") or [])[:8]:
        lines.append(f"     {row['cumulative_ms']:>9.1f} ms  {row['name']}")
    lines += [
        "",
        "④ 体积对账",
        f"   {size.get('files')} 文件 / {size.get('miB')} MiB（{size.get('root')}）",
        "   分桶 Top 6：",
    ]
    for row in (size.get("buckets") or [])[:6]:
        lines.append(f"     {row['miB']:>8.2f} MiB  {row['files']:>6d}  {row['bucket']}")
    text = "\n".join(lines) + "\n"
    path = out_dir / "summary.txt"
    path.write_text(text, encoding="utf-8")
    print("\n" + text)
    return path


# ─────────────────────────────────────────────────────────── main
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="干净窗口下的启动基线采集")
    parser.add_argument("--out-dir", default="",
                        help=f"产物目录（默认 {DEFAULT_OUT_ROOT}\\<时间戳>）")
    parser.add_argument("--size-root", default=str(DEFAULT_SIZE_ROOT),
                        help=f"体积对账根目录（默认 {DEFAULT_SIZE_ROOT}）")
    parser.add_argument("--interpreter", default="",
                        help="跑网关用的解释器（默认已安装 runtime，缺失则用当前解释器）")
    parser.add_argument("--app-root", default=str(REPO_ROOT),
                        help="被测量的程序根（默认仓库根；测 payload/安装树时指向其 app 目录）")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--cpu-max", type=float, default=DEFAULT_CPU_MAX)
    parser.add_argument("--py-max", type=int, default=DEFAULT_PY_MAX)
    parser.add_argument("--cpu-samples", type=int, default=6)
    parser.add_argument("--cpu-interval", type=float, default=1.0,
                        help="单次 CPU 采样窗口（秒）；6×1s 才能证明「持续」低负载")
    parser.add_argument("--max-wait", type=float, default=3600.0,
                        help="等窗口的最长秒数；0 表示只判定一次")
    parser.add_argument("--poll-interval", type=float, default=60.0)
    parser.add_argument("--startup-timeout", type=float, default=180.0)
    parser.add_argument("--console-dist", default=str(DEFAULT_SIZE_ROOT / "console"))
    parser.add_argument("--warm-runs", type=int, default=3)
    parser.add_argument("--force", action="store_true",
                        help="跳过窗口判定直接采集（仅供调试，产物 clean=false）")
    parser.add_argument("--rm-data", dest="keep_data", action="store_false",
                        help="采集结束后删除数据目录（默认保留，便于复盘）")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.out_dir:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        args.out_dir = str(DEFAULT_OUT_ROOT / stamp)
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    args.interpreter = Path(
        args.interpreter or (str(DEFAULT_RUNTIME) if DEFAULT_RUNTIME.is_file() else sys.executable)
    )
    print(f"产物目录：{out_dir}")
    print(f"网关解释器：{args.interpreter}")
    print(f"体积根目录：{args.size_root}")
    print(f"判定条件：CPU ≤ {args.cpu_max}% × {args.cpu_samples} 次采样，"
          f"python ≤ {args.py_max}，端口 {args.port} 空闲，最长等 {args.max_wait:.0f}s")

    window = wait_for_window(args)
    if not window["clean"] and not args.force:
        reason = "；".join(window.get("failed") or ["未知"])
        print()
        print("窗口不干净，未采集任何数据。")
        print(f"  未通过的条件：{reason}")
        print("  建议：关掉本地压测/构建/agent 会话，或调大 --max-wait 让脚本自己等。")
        (out_dir / "window.json").write_text(
            json.dumps(window, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return 2
    if args.force and not window["clean"]:
        print("  --force：窗口不干净仍继续，产物会标记 clean=false。")

    result: Dict[str, Any] = {
        "collected_at": datetime.now().isoformat(timespec="seconds"),
        "interpreter": str(args.interpreter),
        "interpreter_version": subprocess.run(
            [str(args.interpreter), "-c", "import sys; print(sys.version)"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        ).stdout.strip(),
        "repo": str(REPO_ROOT),
        "window": window,
        "clean": bool(window["clean"]),
        "thresholds": {"cpu_max": args.cpu_max, "py_max": args.py_max,
                       "port": args.port, "samples": args.cpu_samples},
    }

    data_dir = out_dir / "data"
    if data_dir.exists() and not args.keep_data:
        shutil.rmtree(data_dir, ignore_errors=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    failures: List[str] = []
    try:
        print("\n① 冷启动（全新数据目录）")
        if (data_dir / "config" / "app.env").exists():
            print("  注意：数据目录已存在上次的配置，这不是真正的冷启动；"
                  "如需冷启动请换一个新的 --out-dir 或加 --rm-data。")
        result["cold"] = run_once("cold", data_dir, args, out_dir)
        if result["cold"].get("first_http_ms") is None:
            failures.append("冷启动在超时内没有任何 HTTP 响应（进程可能没起来）")

        print("\n② 热启动")
        result["warm"] = []
        for index in range(1, args.warm_runs + 1):
            run = run_once(f"warm{index}", data_dir, args, out_dir)
            result["warm"].append(run)
            if run.get("first_http_ms") is None:
                failures.append(f"热启动第 {index} 次没有任何 HTTP 响应")
            time.sleep(1.0)

        print("\n③ 冷 import 图")
        result["import_graph"] = import_graph(args, out_dir, data_dir)

        print("\n④ 体积对账")
        size_root = Path(args.size_root)
        if not size_root.is_absolute():
            size_root = REPO_ROOT / size_root
        result["size"] = size_report(size_root)
    except Exception as exc:  # noqa: BLE001 - 采集失败要留证据，不能只抛栈
        result["error"] = f"{type(exc).__name__}: {exc}"
        failures.append(f"采集异常：{result['error']}")
        (out_dir / "baseline.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        import traceback
        traceback.print_exc()
        return 1

    summary_path = write_summary(result, out_dir)
    result["summary"] = str(summary_path)
    (out_dir / "baseline.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"JSON：{out_dir / 'baseline.json'}")
    if failures:
        print("未通过的采集项：" + "；".join(failures))
        return 1
    if not result["clean"]:
        print("采集完成，但窗口不干净（--force）；数字不能当锚点。")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
