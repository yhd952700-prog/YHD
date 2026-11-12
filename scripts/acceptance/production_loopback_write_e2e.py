"""生产模式回环写请求端到端验收：打开安装版后"能注册、能登录、能干活"。

背景（发布阻断级）：安装版以 ``APP_ENV=production`` 启动后，浏览器从
``http://127.0.0.1:<实际端口>`` 发出的所有写请求都被
``SECURITY_REJECTED reason=cross_site_write`` 拦成 403 —— 用户装完连注册/登录
都过不去。修复要求放行依据是"请求确实来自本机回环 + 端口等于本次实际监听端口"，
而不是把 ``127.0.0.1`` 永久加进白名单。

这个脚本用真实进程 + 真实 HTTP 会话跑完整链路：

    自助注册 → 登录 → 建员工 → 发消息

并对每一步断言"没有被 403"；随后做两组负向对照（``http://evil.example``、
回环但端口不符）必须 403。最后从服务端日志里抓 ``SECURITY_REJECTED`` 计数：
回环同端口的合法 Origin 必须为 0 条。

约束：临时数据目录 + 临时端口，跑完杀进程并清理；不碰 ``D:\\LiuHaoData``，
不依赖 ``dist\\``，不跑 ``setup.exe``。

用法::

    python scripts/acceptance/production_loopback_write_e2e.py

退出码 0 = 全部通过，1 = 有失败项（失败原因逐条打印）。
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[2]
PY = sys.executable

CHECKS: List[Tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((name, bool(ok), detail))
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" | {detail}" if detail else ""), flush=True)
    return bool(ok)


def allocate_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def http(
    method: str,
    url: str,
    *,
    body: Optional[Dict[str, Any]] = None,
    origin: Optional[str] = None,
    token: Optional[str] = None,
    timeout: float = 60.0,
) -> Tuple[int, str]:
    """发一个真实 HTTP 请求；4xx/5xx 也返回状态码而不是抛异常。"""
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if origin:
        headers["Origin"] = origin
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        url, data=data, headers=headers, method=method.upper()
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 - 网络层失败也要成为证据
        return 0, f"{type(exc).__name__}: {exc}"


def json_of(text: str) -> Dict[str, Any]:
    try:
        value = json.loads(text)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


class ProductionInstance:
    """隔离的生产模式实例：临时数据目录 + 临时端口。"""

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="liuhao-loopback-e2e-"))
        self.data_dir = self.root / "data"
        self.workspace = self.root / "workspace"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.port = allocate_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.log_path = self.root / "server.log"
        self.proc: Optional[subprocess.Popen] = None
        self._log = None
        self.env = os.environ.copy()
        for name in (
            "LIUHAO_HUMAN_IDENTITIES_DB",
            "LIUHAO_DATA_DIR",
            "LIUHAO_INSTANCE_DIR",
            "LIUHAO_LOG_DIR",
            "LIUHAO_LISTEN_PORT",
            "LIUHAO_LISTEN_HOST",
            "PORT",
        ):
            self.env.pop(name, None)
        self.env.update(
            {
                # 安装形态：生产环境 + 只绑本机回环。
                "APP_ENV": "production",
                "HOST": "127.0.0.1",
                "PORT": str(self.port),
                # 身份与密钥全部落进临时根。
                "LIUHAO_HUMAN_IDENTITIES_BACKEND": "file",
                "LIUHAO_HUMAN_IDENTITIES_FILE": str(self.root / "identities.json"),
                "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY": f"e2e-integrity-{os.urandom(8).hex()}",
                "LIUHAO_AUTH_SECRETS_FILE": str(self.root / "auth_secrets.json"),
                "LIUHAO_JWT_SECRET": os.urandom(32).hex(),
                "AUDIT_DB_PATH": str(self.root / "audit_store.db"),
                "LIUHAO_MODEL_CONFIG_DB": str(self.root / "models.sqlite3"),
                "LIUHAO_MODEL_CONFIG_KEY": "e2e-model-config-key",
                "LIUHAO_WORKSPACE_ROOT": str(self.workspace),
                "PERSONAL_CONTEXT_DB_PATH": str(self.root / "personal_context.sqlite3"),
                "CONVERSATION_DB_PATH": str(self.root / "conversations.sqlite3"),
                "LIUHAO_LOG_DIR": str(self.root / "logs"),
                "LIUHAO_ACCOUNT_USAGE_DB": str(self.root / "usage.sqlite3"),
                "LIUHAO_ACCOUNT_MEMORY_DB": str(self.root / "memory.sqlite3"),
                "LIUHAO_ACCOUNT_BACKUP_DB": str(self.root / "backup.sqlite3"),
                "LIUHAO_ACCOUNT_SETTINGS_FILE": str(self.root / "account_settings.json"),
                "LIUHAO_DATA_DIR": str(self.data_dir),
                "LIUHAO_INSTANCE_DIR": str(self.root),
                "LIUHAO_ALLOW_REMOTE_RECOVERY": "0",
                # 离线可重复：默认 mock provider，不联网、不需要真 Key。
                "AI_PROVIDER_TYPE": "mock",
            }
        )

    def start(self) -> None:
        self._log = open(self.log_path, "a", encoding="utf-8")
        self.proc = subprocess.Popen(
            [PY, "-m", "src.gateway.__main__"],
            cwd=str(REPO),
            env=self.env,
            stdout=self._log,
            stderr=subprocess.STDOUT,
        )
        deadline = time.time() + 120
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("网关启动即退出：\n" + self.tail())
            status, _ = http("GET", self.base + "/v1/health", timeout=3)
            if status == 200:
                return
            time.sleep(1)
        raise RuntimeError("网关 120 秒内未就绪：\n" + self.tail())

    def tail(self) -> str:
        try:
            return self.log_path.read_text(encoding="utf-8", errors="replace")[-6000:]
        except Exception:
            return ""

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                    capture_output=True,
                )
            else:
                self.proc.terminate()
        try:
            if self.proc is not None:
                self.proc.wait(timeout=15)
        except Exception:
            if self.proc is not None:
                self.proc.kill()
        try:
            if self._log is not None:
                self._log.close()
        except Exception:
            pass
        self.proc = None
        self._log = None

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


def run_flow(instance: ProductionInstance) -> None:
    loopback_origin = f"http://127.0.0.1:{instance.port}"
    localhost_origin = f"http://localhost:{instance.port}"
    ipv6_origin = f"http://[::1]:{instance.port}"
    principal = f"e2e-{os.urandom(4).hex()}"
    password = "Loopback-E2E-2026"

    status, text = http("GET", instance.base + "/v1/health")
    check("生产模式启动：GET /v1/health 返回 200", status == 200, f"HTTP {status}")

    # ⓪ 全新实例的首次设置：登录页"创建账户"先落一个根账号。
    owner = f"owner-{os.urandom(4).hex()}"
    owner_password = "Loopback-Owner-2026"
    status, text = http(
        "POST",
        instance.base + "/v1/accounts/register",
        body={"principal": owner, "password": owner_password},
        origin=loopback_origin,
    )
    body = json_of(text)
    check(
        "首次创建账户：回环同端口 Origin 未被 403",
        status in (200, 201),
        f"HTTP {status} code={body.get('code', '') or body.get('error', '')}",
    )

    # ⓪b 用刚创建的账号登录（浏览器"打开就能进去"的主路径）
    status, text = http(
        "POST",
        instance.base + "/v1/auth/login",
        body={"principal": owner, "secret": owner_password},
        origin=loopback_origin,
    )
    body = json_of(text)
    root_token = str(body.get("access_token") or body.get("token") or "")
    check(
        "首次登录：回环同端口 Origin 未被 403",
        status == 200 and bool(root_token),
        f"HTTP {status}",
    )

    # ① 自助注册（已初始化实例上，登录页"创建账户"的第二条路径）
    status, text = http(
        "POST",
        instance.base + "/v1/accounts/self-register",
        body={"principal": principal, "password": password, "client": "web"},
        origin=loopback_origin,
    )
    body = json_of(text)
    token = str(body.get("token") or body.get("access_token") or "")
    check(
        "自助注册：回环同端口 Origin 未被 403",
        status in (200, 201) and bool(token),
        f"HTTP {status} code={body.get('code', '') or body.get('error', '')}",
    )

    # ② 登录
    status, text = http(
        "POST",
        instance.base + "/v1/auth/login",
        body={"principal": principal, "secret": password},
        origin=loopback_origin,
    )
    body = json_of(text)
    login_token = str(body.get("access_token") or body.get("token") or "")
    check(
        "登录：回环同端口 Origin 未被 403",
        status == 200 and bool(login_token),
        f"HTTP {status}",
    )
    token = login_token or token

    # ③ 建员工
    employee = f"验收员工-{os.urandom(3).hex()}"
    status, text = http(
        "POST",
        instance.base + "/v1/employees",
        body={"name": employee, "agent_count": 1},
        origin=loopback_origin,
        token=token,
    )
    body = json_of(text)
    check(
        "建员工：回环同端口 Origin 未被 403",
        status in (200, 201),
        f"HTTP {status} name={employee} code={body.get('code', '')}",
    )

    # ④ 发一条消息（根账号可用机器级 mock provider，离线可重复）
    status, text = http(
        "POST",
        instance.base + "/v1/chat",
        body={"message": "你好，确认写请求可以正常落地。"},
        origin=loopback_origin,
        token=root_token or token,
        timeout=90,
    )
    body = json_of(text)
    check(
        "发消息：回环同端口 Origin 未被 403",
        status == 200,
        f"HTTP {status} code={body.get('code', '')}",
    )

    # ⑤ 同一台机器上的另外两个回环写法也应放行（localhost / IPv6 回环）
    for label, origin in (
        ("localhost 同端口", localhost_origin),
        ("[::1] 同端口", ipv6_origin),
    ):
        status, text = http(
            "POST",
            instance.base + "/v1/auth/login",
            body={"principal": principal, "secret": password},
            origin=origin,
        )
        check(
            f"回环变体放行：{label} 登录未被 403",
            status == 200,
            f"HTTP {status} origin={origin}",
        )

    # ⑥ 负向对照 1：非回环跨站来源必须 403
    status, text = http(
        "POST",
        instance.base + "/v1/auth/login",
        body={"principal": principal, "secret": password},
        origin="http://evil.example",
    )
    body = json_of(text)
    check(
        "负向：http://evil.example 写请求 403",
        status == 403 and body.get("error") == "origin_not_allowed",
        f"HTTP {status} error={body.get('error', '')}",
    )

    # ⑦ 负向对照 2：回环但端口不符必须 403
    wrong_port = instance.port + 1 if instance.port < 65535 else instance.port - 1
    wrong_origin = f"http://127.0.0.1:{wrong_port}"
    status, text = http(
        "POST",
        instance.base + "/v1/auth/login",
        body={"principal": principal, "secret": password},
        origin=wrong_origin,
    )
    body = json_of(text)
    check(
        "负向：回环但端口不符写请求 403",
        status == 403 and body.get("error") == "origin_not_allowed",
        f"HTTP {status} origin={wrong_origin}",
    )


def audit_log(instance: ProductionInstance) -> None:
    log = instance.log_path.read_text(encoding="utf-8", errors="replace")
    rejected = [line for line in log.splitlines() if "SECURITY_REJECTED" in line]
    cross_site = [
        line for line in rejected if "reason=cross_site_write" in line
    ]
    legit = f"origin='http://127.0.0.1:{instance.port}'"
    legit_hits = [line for line in cross_site if legit in line]
    negative_hits = [
        line
        for line in cross_site
        if ("evil.example" in line)
        or (f"origin='http://127.0.0.1:{instance.port + 1}'" in line)
        or (f"origin='http://127.0.0.1:{instance.port - 1}'" in line)
    ]

    check(
        "证据：回环同端口 Origin 的 SECURITY_REJECTED(cross_site_write) 计数为 0",
        len(legit_hits) == 0,
        f"count={len(legit_hits)}",
    )
    check(
        "证据：负向对照确实产生了 SECURITY_REJECTED(cross_site_write)",
        len(negative_hits) >= 2,
        f"count={len(negative_hits)}",
    )
    check(
        "证据：SECURITY_REJECTED 只来自负向对照",
        len(cross_site) == len(negative_hits),
        f"cross_site_write={len(cross_site)}",
    )
    for line in negative_hits:
        print(f"    log> {line}", flush=True)


def main() -> int:
    instance = ProductionInstance()
    print(f"实例根目录：{instance.root}", flush=True)
    print(f"生产模式端口：{instance.port}", flush=True)
    try:
        instance.start()
        run_flow(instance)
    except Exception as exc:  # noqa: BLE001 - 启动失败也要给出可读证据
        check("实例启动 / 流程执行", False, f"{type(exc).__name__}: {exc}")
        print(instance.tail(), flush=True)
    finally:
        instance.stop()
        try:
            audit_log(instance)
        except Exception as exc:  # noqa: BLE001
            check("日志审计", False, f"{type(exc).__name__}: {exc}")
        instance.cleanup()

    failed = [item for item in CHECKS if not item[1]]
    print()
    print(f"合计：{len(CHECKS)} 项，通过 {len(CHECKS) - len(failed)}，失败 {len(failed)}")
    if failed:
        for name, _, detail in failed:
            print(f"  - {name} | {detail}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
