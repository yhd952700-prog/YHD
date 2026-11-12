"""生产模式局域网/手机写请求端到端验收：登录之后能真正写一次。

背景（PROD-002）：安装版显式对外绑定（``HOST=0.0.0.0`` / 指定网卡）后，
手机或局域网里的浏览器从 ``http://<本机 LAN IP>:<实际端口>`` 打开的页面，
读请求正常、写请求一律 ``403 origin_not_allowed`` —— 手机端实际不可用。

修复要求：对外绑定时自动放行「本机 LAN IP + 实际监听端口」作为 Origin，
精确完整来源比对（不退化成 ``*``），跨站来源与错误端口仍然 403。

本脚本用真实进程 + 真实 HTTP 会话：

    回环建根账号 → 手机 UA/LAN Origin 登录 → 建员工 → 发消息
    → 负向：跨站 Origin / https 变体 / 错误端口 必须 403

用法::

    python scripts/acceptance/production_lan_write_e2e.py

退出码 0 = 全部通过，1 = 有失败项。
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
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

CHECKS: List[Tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" | {detail}" if detail else ""), flush=True)
    return bool(ok)


def allocate_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def lan_ipv4() -> str:
    """本机第一个非回环 IPv4（与网关 lan_write_origins 的枚举口径一致）。"""
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from src.gateway.main import _lan_ipv4_addresses

    addresses = _lan_ipv4_addresses()
    if not addresses:
        raise RuntimeError("本机没有可用的非回环 IPv4 地址，无法模拟局域网访问")
    return addresses[0]


def http(
    method: str,
    url: str,
    *,
    body: Optional[Dict[str, Any]] = None,
    origin: Optional[str] = None,
    token: Optional[str] = None,
    user_agent: Optional[str] = None,
    timeout: float = 60.0,
) -> Tuple[int, str]:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if origin:
        headers["Origin"] = origin
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if user_agent:
        headers["User-Agent"] = user_agent
    request = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 - 网络层失败也是证据
        return 0, f"{type(exc).__name__}: {exc}"


def json_of(text: str) -> Dict[str, Any]:
    try:
        value = json.loads(text)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


class ProductionLanInstance:
    """隔离的生产模式实例：临时数据目录 + 随机端口 + 显式对外绑定。"""

    def __init__(self, lan: str) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="liuhao-lan-e2e-"))
        self.port = allocate_port()
        self.lan = lan
        self.loopback_base = f"http://127.0.0.1:{self.port}"
        self.lan_base = f"http://{lan}:{self.port}"
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
            "HOST",
        ):
            self.env.pop(name, None)
        self.env.update(
            {
                "APP_ENV": "production",
                "HOST": "0.0.0.0",
                "PORT": str(self.port),
                "LIUHAO_HUMAN_IDENTITIES_BACKEND": "file",
                "LIUHAO_HUMAN_IDENTITIES_FILE": str(self.root / "identities.json"),
                "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY": f"lan-e2e-{os.urandom(8).hex()}",
                "LIUHAO_AUTH_SECRETS_FILE": str(self.root / "auth_secrets.json"),
                "LIUHAO_JWT_SECRET": os.urandom(32).hex(),
                "AUDIT_DB_PATH": str(self.root / "audit_store.db"),
                "LIUHAO_MODEL_CONFIG_DB": str(self.root / "models.sqlite3"),
                "LIUHAO_MODEL_CONFIG_KEY": "lan-e2e-model-key",
                "LIUHAO_WORKSPACE_ROOT": str(self.root / "workspace"),
                "PERSONAL_CONTEXT_DB_PATH": str(self.root / "personal_context.sqlite3"),
                "CONVERSATION_DB_PATH": str(self.root / "conversations.sqlite3"),
                "LIUHAO_LOG_DIR": str(self.root / "logs"),
                "LIUHAO_ACCOUNT_USAGE_DB": str(self.root / "usage.sqlite3"),
                "LIUHAO_ACCOUNT_MEMORY_DB": str(self.root / "memory.sqlite3"),
                "LIUHAO_ACCOUNT_BACKUP_DB": str(self.root / "backup.sqlite3"),
                "LIUHAO_ACCOUNT_SETTINGS_FILE": str(self.root / "account_settings.json"),
                "LIUHAO_DATA_DIR": str(self.root / "data"),
                "LIUHAO_INSTANCE_DIR": str(self.root),
                "LIUHAO_ALLOW_REMOTE_RECOVERY": "0",
                "AI_PROVIDER_TYPE": "mock",
            }
        )

    def start(self) -> None:
        (self.root / "workspace").mkdir(parents=True, exist_ok=True)
        (self.root / "data").mkdir(parents=True, exist_ok=True)
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
            status, _ = http("GET", self.lan_base + "/v1/health", timeout=3)
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
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)], capture_output=True)
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


def run_flow(instance: ProductionLanInstance) -> None:
    loopback_origin = f"http://127.0.0.1:{instance.port}"
    lan_origin = f"http://{instance.lan}:{instance.port}"
    owner = f"owner-{os.urandom(4).hex()}"
    owner_password = "Lan-Owner-2026"

    status, text = http("GET", instance.lan_base + "/v1/health")
    check("LAN 读请求：GET /v1/health 返回 200", status == 200, f"HTTP {status}")

    status, text = http(
        "POST",
        instance.loopback_base + "/v1/accounts/register",
        body={"principal": owner, "password": owner_password},
        origin=loopback_origin,
    )
    body = json_of(text)
    check(
        "本机首次创建账户（回环）成功",
        status in (200, 201),
        f"HTTP {status} code={body.get('code', '') or body.get('error', '')}",
    )

    # 手机端真实路径：LAN Origin + 手机 UA 登录
    status, text = http(
        "POST",
        instance.lan_base + "/v1/auth/login",
        body={"principal": owner, "secret": owner_password},
        origin=lan_origin,
        user_agent=MOBILE_UA,
    )
    body = json_of(text)
    root_token = str(body.get("access_token") or body.get("token") or "")
    check(
        "手机 UA + LAN Origin 登录未被 403",
        status == 200 and bool(root_token),
        f"HTTP {status} origin={lan_origin}",
    )

    employee = f"局域网验收员工-{os.urandom(3).hex()}"
    status, text = http(
        "POST",
        instance.lan_base + "/v1/employees",
        body={"name": employee, "agent_count": 1},
        origin=lan_origin,
        token=root_token,
        user_agent=MOBILE_UA,
    )
    body = json_of(text)
    check(
        "手机 UA + LAN Origin 写操作（建员工）成功落地",
        status in (200, 201),
        f"HTTP {status} name={employee} code={body.get('code', '')}",
    )

    status, text = http(
        "POST",
        instance.lan_base + "/v1/chat",
        body={"message": "手机端确认：写请求可以正常落地。"},
        origin=lan_origin,
        token=root_token,
        user_agent=MOBILE_UA,
        timeout=90,
    )
    body = json_of(text)
    check(
        "手机 UA + LAN Origin 发消息成功",
        status == 200,
        f"HTTP {status} code={body.get('code', '')}",
    )

    # 负向对照：不能因为对外绑定就把任意来源放开
    for label, origin in (
        ("跨站域名", "http://evil.example"),
        ("同主机 https 变体", f"https://{instance.lan}:{instance.port}"),
    ):
        status, text = http(
            "POST",
            instance.lan_base + "/v1/auth/login",
            body={"principal": owner, "secret": owner_password},
            origin=origin,
        )
        body = json_of(text)
        check(
            f"负向：{label} 写请求 403",
            status == 403 and body.get("error") == "origin_not_allowed",
            f"HTTP {status} origin={origin}",
        )

    wrong_port = instance.port + 1 if instance.port < 65535 else instance.port - 1
    wrong_origin = f"http://{instance.lan}:{wrong_port}"
    status, text = http(
        "POST",
        instance.lan_base + "/v1/auth/login",
        body={"principal": owner, "secret": owner_password},
        origin=wrong_origin,
    )
    body = json_of(text)
    check(
        "负向：LAN 主机但端口不符 写请求 403",
        status == 403 and body.get("error") == "origin_not_allowed",
        f"HTTP {status} origin={wrong_origin}",
    )


def audit_log(instance: ProductionLanInstance) -> None:
    log = instance.log_path.read_text(encoding="utf-8", errors="replace")
    cross_site = [line for line in log.splitlines() if "reason=cross_site_write" in line]
    legit = f"origin='http://{instance.lan}:{instance.port}'"
    legit_hits = [line for line in cross_site if legit in line]
    check(
        "证据：合法 LAN Origin 的 SECURITY_REJECTED(cross_site_write) 计数为 0",
        len(legit_hits) == 0,
        f"count={len(legit_hits)}",
    )
    negative_hits = [
        line
        for line in cross_site
        if "evil.example" in line or "https://" in line or f"http://{instance.lan}:{instance.port + 1}" in line
        or f"http://{instance.lan}:{instance.port - 1}" in line
    ]
    check(
        "证据：负向对照确实产生了 SECURITY_REJECTED(cross_site_write)",
        len(negative_hits) >= 3,
        f"count={len(negative_hits)}",
    )


def main() -> int:
    lan = lan_ipv4()
    instance = ProductionLanInstance(lan)
    print(f"实例目录：{instance.root}", flush=True)
    print(f"LAN 基址：{instance.lan_base}", flush=True)
    try:
        instance.start()
        run_flow(instance)
    except Exception as exc:  # noqa: BLE001
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
    for name, _, detail in failed:
        print(f"  - {name} | {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
