#!/usr/bin/env python
"""对组装好的 payload 做真实冒烟：起进程、开页面、登录、发消息。

验证链路（全部走 staged payload 自带的离线运行时，不依赖构建机 venv）：

    启动 → /v1/health → 首页（驾驶舱）→ 注册人类身份 → 重启
         → /v1/auth/login → 携带令牌访问 /v1/dashboard/summary
         → POST /v1/chat（未配置 provider 时如实记录结果）

结果写入 ``<out>/smoke-report.json``；核心链路失败时返回非零。

用法：
    .venv\\Scripts\\python.exe packaging\\smoke_payload.py --payload dist\\payload\\app
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PAYLOAD = REPO_ROOT / "dist" / "payload" / "app"
DEFAULT_REPORT = REPO_ROOT / "dist" / "smoke-report.json"

SMOKE_PRINCIPAL = "smoke"
SMOKE_PASSWORD = "Smoke-Only-Passw0rd!"

#: 冒烟环境严禁把构建机的仓库配置带进来，否则测的就不是 payload 自己了。
#: 不逐个枚举：任何 ``LIUHAO_*`` 与常见的直接路径变量一律先从子进程环境里剥掉，
#: 再由冒烟显式设置它需要的少数几个（数据目录、完整性密钥）。逐个枚举曾经漏掉
#: ``LIUHAO_HUMAN_IDENTITIES_DB``，导致"注册写进仓库、启动读数据目录"→ 登录 403。
_ISOLATED_PREFIXES = ("LIUHAO_",)
_ISOLATED_EXACT = frozenset({
    "AUDIT_DB_PATH", "AUDIT_DB_BACKUP_PATH",
    "CONVERSATION_DB_PATH", "MEMORY_DB_PATH", "PERSONAL_CONTEXT_DB_PATH",
    "WORKSPACE_ROOT",
})


def _is_isolated(name: str) -> bool:
    upper = name.upper()
    return upper.startswith(_ISOLATED_PREFIXES) or upper in _ISOLATED_EXACT


def _isolated_env(data_dir: Path) -> Dict[str, str]:
    """构造子进程环境：剥掉所有可疑变量，只由冒烟显式注入需要的值。"""
    env = {key: value for key, value in os.environ.items() if not _is_isolated(key)}
    env.update({
        "LIUHAO_DATA_DIR": str(data_dir),
        "NO_PROXY": "localhost,127.0.0.1",
        "no_proxy": "localhost,127.0.0.1",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    return env


def stripped_names() -> list:
    return sorted(name for name in os.environ if _is_isolated(name))


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _opener() -> urllib.request.OpenerDirector:
    """本机探测必须绕代理，否则 127.0.0.1 会被 http_proxy 拦掉。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _http(
    url: str,
    method: str = "GET",
    body: Optional[Dict[str, Any]] = None,
    token: Optional[str] = None,
    timeout: float = 10.0,
) -> Tuple[int, bytes]:
    data = None
    headers: Dict[str, str] = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _opener().open(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
        # 进程还没起来时属于正常轮询状态，返回 0 让调用方继续等。
        return 0, b""


def _wait_ready(base: str, child: subprocess.Popen, log_path: Path, timeout: float = 180.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if child.poll() is not None:
            return False
        status, _ = _http(f"{base}/v1/health", timeout=3.0)
        if status == 200:
            return True
        time.sleep(1.0)
    return False


def _kill_tree(process: subprocess.Popen) -> None:
    """Windows 下必须杀进程树：启动器会再拉一个 uvicorn 子进程。"""
    if process.poll() is not None:
        return
    subprocess.run(
        ["taskkill", "/T", "/F", "/PID", str(process.pid)],
        capture_output=True,
    )


def _start(payload: Path, data_dir: Path, port: int, log_path: Path) -> subprocess.Popen:
    env = _isolated_env(data_dir)
    log = open(log_path, "a", encoding="utf-8")
    return subprocess.Popen(
        [str(payload / "runtime" / "python.exe"),
         str(payload / "scripts" / "launch_installed.py"),
         "--skip-wizard", "--no-browser",
         "--host", "127.0.0.1", "--api-port", str(port)],
        cwd=str(payload), env=env, stdout=log, stderr=subprocess.STDOUT,
    )


def _register(payload: Path, data_dir: Path, log_path: Path) -> int:
    env = _isolated_env(data_dir)
    integrity_path = data_dir / "config" / "integrity.key"
    if not integrity_path.is_file():
        raise SystemExit(
            f"首次启动没有生成 {integrity_path} —— 启动器的数据目录初始化有缺陷"
        )
    # integrity.key 落盘形态是机器绑定密文（LIUHAO-DPAPI-1:...），**不是**明文密钥。
    # 直接把文件内容塞进 LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY 会让注册脚本拿密文
    # 当签名密钥写行，而网关启动时 ensure_integrity_key 解封出明文密钥来校验 →
    # 行上的 integrity 标签对不上 → 登录 403。这里按产品同一条路径解封：
    # read_machine_bound_text 与 ensure_integrity_key 用的是同一个 DPAPI 熵。
    # 本脚本以 packaging\smoke_payload.py 直接执行时 sys.path[0] 是 packaging\，
    # 仓库根不在搜索路径里，import src.* 会 ModuleNotFoundError。显式补仓库根。
    repo_root = Path(__file__).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from src.common.integrity_key import (  # 延迟导入：只在本步骤用
        INTEGRITY_DPAPI_PURPOSE,
        INTEGRITY_KEY_ENV,
    )
    from src.security.encryption import read_machine_bound_text

    integrity, state = read_machine_bound_text(
        integrity_path, purpose=INTEGRITY_DPAPI_PURPOSE
    )
    if state not in ("ok", "legacy") or not integrity:
        raise SystemExit(
            f"无法解封 {integrity_path}（状态={state}）：冒烟不能拿密文当签名密钥，"
            "否则注册的行在网关侧必然验签失败（登录 403）"
        )
    env[INTEGRITY_KEY_ENV] = integrity
    command = [
        str(payload / "runtime" / "python.exe"),
        str(payload / "scripts" / "register_human_identity.py"),
        "--principal", SMOKE_PRINCIPAL,
        "--display-name", "Smoke Test",
        "--permissions", "admin",
        "--password-stdin",
        "--file", str(data_dir / "config" / "human_identities.json"),
        "--secrets-file", str(data_dir / "config" / "auth_secrets.json"),
    ]
    with open(log_path, "a", encoding="utf-8") as log:
        return subprocess.run(
            command, cwd=str(payload), env=env,
            input=SMOKE_PASSWORD + "\n", text=True,
            stdout=log, stderr=subprocess.STDOUT,
        ).returncode


def _registered_in_data_dir(data_dir: Path) -> bool:
    """注册必须落在启动器会读的那份身份文件里，否则登录必然 403。"""
    path = data_dir / "config" / "human_identities.json"
    if not path.is_file():
        return False
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    humans = document.get("humans") if isinstance(document, dict) else None
    if not isinstance(humans, list):
        return False
    return any(
        isinstance(item, dict)
        and str(item.get("principal") or item.get("name") or "") == SMOKE_PRINCIPAL
        for item in humans
    )


_LOCAL_PROOF_CODE = r"""
import json

from src.common.local_proof_ipc import request_token_bundle
from src.gateway import local_proof

local_proof.start_broker()
ready = local_proof.wait_for_broker(timeout=10.0)
acl_ok = local_proof.pipe_acl_is_current_user_only() if ready else False
used = replay = False
bundle_keys = []
try:
    bundle = request_token_bundle(timeout=10.0)
    bundle_keys = sorted(bundle)
    used = local_proof.redeem_token(
        bundle["handoff_token"], scope=local_proof.SCOPE_HANDOFF
    )
    replay = local_proof.redeem_token(
        bundle["handoff_token"], scope=local_proof.SCOPE_HANDOFF
    )
finally:
    local_proof.stop_all_brokers()
print("LOCAL_PROOF_JSON " + json.dumps({
    "ready": ready,
    "acl": acl_ok,
    "bundle_keys": bundle_keys,
    "used": used,
    "replay": replay,
}))
"""


def _local_proof_check(payload: Path, data_dir: Path) -> Dict[str, Any]:
    """在安装包的运行时里真实启动命名管道并走完令牌生命周期。

    只验证 ``import`` 会漏掉 pywin32 运行时缺失、DACL 设置失败、管道线程
    起不来、令牌不可用这类问题；这里用安装包自己的 python.exe 起一个隔离
    进程，输出 ready/acl/used/replay 四项硬证据。
    """
    env = _isolated_env(data_dir)
    try:
        completed = subprocess.run(
            [
                str(payload / "runtime" / "python.exe"),
                "-c",
                _LOCAL_PROOF_CODE,
            ],
            cwd=str(payload),
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"error": str(exc)}
    payload_line = ""
    for line in (completed.stdout or "").splitlines():
        if line.startswith("LOCAL_PROOF_JSON "):
            payload_line = line[len("LOCAL_PROOF_JSON "):]
    if not payload_line:
        return {
            "error": f"exit={completed.returncode}",
            "stdout": (completed.stdout or "")[-500:],
            "stderr": (completed.stderr or "")[-500:],
        }
    try:
        return json.loads(payload_line)
    except json.JSONDecodeError as exc:
        return {"error": f"bad json: {exc}", "raw": payload_line[-500:]}


def _tail(path: Path, lines: int = 25) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(content[-lines:])


def smoke(payload: Path) -> Tuple[Dict[str, Any], int]:
    report: Dict[str, Any] = {"payload": str(payload), "steps": {}}
    if not (payload / "runtime" / "python.exe").is_file():
        report["steps"]["runtime_present"] = False
        return report, 1

    data_dir = Path(tempfile.mkdtemp(prefix="liuhao-smoke-"))
    log_path = data_dir / "smoke.log"
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    report["data_dir"] = str(data_dir)
    report["base_url"] = base
    report["log"] = str(log_path)
    # 记录被剥离的继承变量，证明冒烟在干净子进程里跑（不再靠人工核对清单）。
    report["stripped_env"] = stripped_names()

    try:
        # ---- 第一次启动：健康探针 + 驾驶舱首页 ----
        child = _start(payload, data_dir, port, log_path)
        ready = _wait_ready(base, child, log_path)
        report["steps"]["health"] = "PASS" if ready else "FAIL"
        if not ready:
            report["steps"]["first_start_log_tail"] = _tail(log_path)
            _kill_tree(child)
            return report, 1

        status, body = _http(base + "/", timeout=10)
        console_ok = status == 200 and b"<" in body and b"root" in body
        report["steps"]["console_index"] = "PASS" if console_ok else f"FAIL(http {status})"

        _kill_tree(child)
        time.sleep(2.0)

        # ---- 本机证明通道：真实起管道、读 DACL、取令牌、取用即焚 ----
        proof = _local_proof_check(payload, data_dir)
        report["steps"]["local_proof_pipe"] = (
            "PASS" if proof.get("ready") is True else f"FAIL({proof.get('error') or proof})"
        )
        report["steps"]["local_proof_dacl"] = (
            "PASS" if proof.get("acl") is True else f"FAIL({proof.get('error') or proof})"
        )
        report["steps"]["local_proof_token"] = (
            "PASS"
            if proof.get("used") is True
            and proof.get("bundle_keys") == ["expires_in", "handoff_token", "reset_token"]
            else f"FAIL({proof.get('error') or proof})"
        )
        report["steps"]["local_proof_single_use"] = (
            "PASS" if proof.get("used") is True and proof.get("replay") is False
            else f"FAIL({proof.get('error') or proof})"
        )
        proof_core = (
            report["steps"]["local_proof_pipe"],
            report["steps"]["local_proof_dacl"],
            report["steps"]["local_proof_token"],
            report["steps"]["local_proof_single_use"],
        )
        if any(item != "PASS" for item in proof_core):
            report["steps"]["local_proof_detail"] = proof
            return report, 1

        # ---- 注册人类身份（与向导等价的最小路径） ----
        code = _register(payload, data_dir, log_path)
        report["steps"]["register_identity"] = "PASS" if code == 0 else f"FAIL(exit {code})"
        if code != 0:
            report["steps"]["register_log_tail"] = _tail(log_path)
            return report, 1
        # 注册与启动必须命中同一个库：注册脚本写进数据目录的身份文件，
        # 启动器才读得到。写偏了（历史故障形态）登录必然 403，这里提前失败。
        registered = _registered_in_data_dir(data_dir)
        report["steps"]["identity_store_in_data_dir"] = "PASS" if registered else "FAIL"
        if not registered:
            report["steps"]["register_log_tail"] = _tail(log_path)
            return report, 1

        # ---- 第二次启动：登录 + 鉴权端点 + 发消息 ----
        port = _free_port()
        base = f"http://127.0.0.1:{port}"
        report["base_url"] = base
        child = _start(payload, data_dir, port, log_path)
        ready = _wait_ready(base, child, log_path)
        report["steps"]["restart_health"] = "PASS" if ready else "FAIL"
        if not ready:
            report["steps"]["restart_log_tail"] = _tail(log_path)
            _kill_tree(child)
            return report, 1

        status, body = _http(
            base + "/v1/auth/login", "POST",
            {"principal": SMOKE_PRINCIPAL, "secret": SMOKE_PASSWORD},
        )
        token = ""
        if status == 200:
            try:
                token = json.loads(body).get("access_token", "")
            except json.JSONDecodeError:
                token = ""
        report["steps"]["login"] = "PASS" if token else f"FAIL(http {status})"

        if token:
            status, _ = _http(base + "/v1/dashboard/summary", token=token)
            report["steps"]["authenticated_dashboard"] = "PASS" if status == 200 else f"FAIL(http {status})"

            status, body = _http(
                base + "/v1/chat", "POST",
                {"message": "你好，用一句话介绍你自己", "session_id": "smoke"},
                token=token, timeout=120.0,
            )
            detail = ""
            try:
                parsed = json.loads(body)
                detail = str(parsed.get("reply") or parsed.get("detail") or "")[:160]
                provider = parsed.get("provider") or parsed.get("model")
                if provider:
                    detail = f"[{provider}] {detail}"
            except json.JSONDecodeError:
                detail = body[:160].decode("utf-8", "replace")
            report["steps"]["chat"] = "PASS" if status == 200 else f"FAIL(http {status})"
            report["steps"]["chat_detail"] = detail
            # 未配置 provider 时这里必然失败；不算安装包问题，只如实记录。
            report["steps"]["chat_note"] = (
                "干净安装默认没有 provider；PASS 表示网关返回了回复（可能是 mock），"
                "FAIL 属预期，需在向导里配置模型后再验真实 LLM。"
            )

        _kill_tree(child)
        time.sleep(1.0)

        core = [report["steps"].get(k) for k in
                ("health", "console_index",
                 "local_proof_pipe", "local_proof_dacl",
                 "local_proof_token", "local_proof_single_use",
                 "register_identity",
                 "identity_store_in_data_dir", "restart_health", "login")]
        return report, 0 if all(item == "PASS" for item in core) else 1
    finally:
        try:
            import shutil

            (REPO_ROOT / "dist").mkdir(parents=True, exist_ok=True)
            shutil.copy2(log_path, REPO_ROOT / "dist" / "smoke.log")
        except OSError:
            pass


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="payload 冒烟验证")
    parser.add_argument("--payload", default=str(DEFAULT_PAYLOAD))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    args = parser.parse_args(argv)

    report, code = smoke(Path(args.payload).expanduser().resolve())
    target = Path(args.report)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")

    print("=" * 66)
    print("payload 冒烟结果")
    print("=" * 66)
    for name, value in report["steps"].items():
        if name.endswith("_tail"):
            continue
        print(f"  {name:24s} {value}")
    print(f"  报告：{target}")
    if code != 0:
        for key in ("first_start_log_tail", "restart_log_tail", "register_log_tail"):
            if key in report["steps"]:
                print(f"\n--- {key} ---\n{report['steps'][key]}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
