"""默认零外连端到端回归：证明"默认状态下，除回环外一条连接都没有"。

为什么需要它
------------
``docs/audit-data-egress-20261108.md`` 已经证明过一次"默认状态零出站"，但
一次性抓包挡不住后续改动。本脚本把那次实验固化成可重复的验收：

    启动 → 首屏（含本地 JS/CSS）→ 登录 → 发消息 → 检索（未开启）
    → 导出诊断包 → 检查更新

全程对**网关进程及其子进程**做 100–250ms 的连接采样，同时把
``HTTP_PROXY/HTTPS_PROXY`` 指向一个"记录后一律 502、绝不转发"的本地代理。
两条证据都必须是 0 条非回环连接/请求。

退出码（三档，绝不把"跑不起来"算通过）
--------------------------------------
* ``0`` = 验证完成，且非回环连接 0 条、代理请求 0 条；
* ``1`` = 验证完成，但发现出站（列出域名/IP/端口），或流程断言失败；
* ``2`` = **环境不具备，未判定通过**：代理无法监听、端口无法分配、
  psutil 无法观测进程连接等。此时不输出"通过"。

用法::

    python scripts/acceptance/egress_default_off_e2e.py
    python scripts/acceptance/egress_default_off_e2e.py --keep

约束：临时数据目录 + 临时端口；不碰 ``D:\\LiuHaoData``，不依赖 ``dist\\``，
不跑 ``setup.exe``；代理只记录目标、不读 body，也不转发任何字节。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import psutil
except Exception:  # noqa: BLE001 - 没有 psutil 就无法证明"零出站"
    psutil = None  # type: ignore[assignment]

REPO = Path(__file__).resolve().parents[2]
PY = sys.executable

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_ENV = 2


class EnvUnavailable(RuntimeError):
    """环境不具备（代理/端口/观测能力），对应退出码 2。"""


class GatewayStartError(RuntimeError):
    """网关自身启动失败或卡死，属于真实缺陷，对应退出码 1。"""


def _is_loopback_ip(ip: str) -> bool:
    value = str(ip or "").strip().strip("[]").lower()
    return (
        value.startswith("127.")
        or value in {"::1", "0:0:0:0:0:0:0:1", "localhost", "0.0.0.0", "::"}
    )


def allocate_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", 0))
        except OSError as exc:
            raise EnvUnavailable(f"无法分配回环端口：{exc}") from exc
        return int(sock.getsockname()[1])


class BlockingProxy(threading.Thread):
    """本地拦截代理：只记录目标，随后返回 502，绝不转发。"""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self._records: List[Tuple[str, str]] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._sock.bind(("127.0.0.1", 0))
            self._sock.listen(50)
        except OSError as exc:
            raise EnvUnavailable(f"拦截代理无法监听：{exc}") from exc
        self.port = int(self._sock.getsockname()[1])
        self._sock.settimeout(0.5)

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                data = conn.recv(4096)
                line = data.split(b"\r\n", 1)[0].decode("latin-1", "replace")
                parts = line.split()
                if len(parts) >= 2:
                    method, target = parts[0], parts[1]
                    with self._lock:
                        self._records.append((method, target))
                conn.sendall(
                    b"HTTP/1.1 502 Bad Gateway\r\n"
                    b"Content-Type: text/plain\r\n"
                    b"Content-Length: 0\r\n"
                    b"Connection: close\r\n\r\n"
                )
            except OSError:
                pass
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    def snapshot(self) -> List[Tuple[str, str]]:
        with self._lock:
            return list(self._records)

    def clear(self) -> None:
        with self._lock:
            self._records.clear()

    def self_test(self) -> None:
        """正向对照：代理必须真的记录一次请求，否则"0 条"不可信。"""
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=3) as sock:
                sock.sendall(
                    b"CONNECT probe.invalid:443 HTTP/1.1\r\n"
                    b"Host: probe.invalid:443\r\n\r\n"
                )
                sock.recv(4096)
        except OSError as exc:
            raise EnvUnavailable(f"拦截代理自检失败：{exc}") from exc
        deadline = time.time() + 2
        while time.time() < deadline:
            if any("probe.invalid" in target for _, target in self.snapshot()):
                self.clear()
                return
            time.sleep(0.05)
        raise EnvUnavailable("拦截代理自检未记录到请求，不能据此判定零出站")

    def stop(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass


def _wanted_pids(pid: int) -> set[int]:
    try:
        root = psutil.Process(pid)
        return {pid} | {child.pid for child in root.children(recursive=True)}
    except psutil.Error:
        return {pid}


def process_connections(pid: int) -> List[Any]:
    """取该进程（含子进程）的连接。

    用全局 TCP 表再按 PID 过滤，而不是 ``Process.net_connections``：
    后者在部分 Windows 环境下对"刚启动、尚未被父进程完全登记"的监听
    socket 会返回空表，导致"看不见 = 判定通过"的假阴性。
    """
    if psutil is None:
        raise EnvUnavailable("缺少 psutil，无法观测进程连接，不能判定通过")
    wanted = _wanted_pids(pid)
    try:
        return [
            conn for conn in psutil.net_connections(kind="inet")
            if getattr(conn, "pid", None) in wanted
        ]
    except psutil.AccessDenied as exc:
        raise EnvUnavailable(f"无权读取系统 TCP 表：{exc}") from exc
    except psutil.Error as exc:
        raise EnvUnavailable(f"无法读取系统 TCP 表：{exc}") from exc


class ConnectionSampler(threading.Thread):
    """按 PID（含子进程）采样 TCP 连接，只记录非回环远端。"""

    def __init__(self, pid: int, interval: float = 0.2) -> None:
        super().__init__(daemon=True)
        if psutil is None:
            raise EnvUnavailable("缺少 psutil，无法观测进程连接，不能判定通过")
        self.pid = pid
        self.interval = interval
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.seen: set[str] = set()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self._sample()
            except Exception:  # noqa: BLE001 - 采样失败不静默跳过
                pass
            self._stop.wait(self.interval)

    def _sample(self) -> None:
        for conn in process_connections(self.pid):
            remote = getattr(conn, "raddr", None)
            if not remote or not getattr(remote, "ip", ""):
                continue
            if _is_loopback_ip(remote.ip):
                continue
            key = f"{remote.ip}:{remote.port}"
            with self._lock:
                self.seen.add(key)

    def snapshot(self) -> List[str]:
        with self._lock:
            return sorted(self.seen)

    def stop(self) -> None:
        self._stop.set()


def sees_listening(pid: int, port: int) -> bool:
    """证明"我们看得到该进程的 socket"；看不到就不能得出"0 条"。"""
    connections = process_connections(pid)
    return any(
        conn.status == psutil.CONN_LISTEN
        and getattr(conn, "laddr", None)
        and conn.laddr.port == port
        for conn in connections
    )


class IsolatedGateway:
    """临时数据目录 + 临时端口 + 全量隔离环境变量的网关实例。"""

    def __init__(self, proxy_port: int) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="liuhao-egress-e2e-"))
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

        # 清掉所有可能把实例拉出默认状态的开关；用空字符串覆盖，防止 .env 重新注入。
        for name in (
            "LIUHAO_HUMAN_IDENTITIES_DB",
            "LIUHAO_DATA_DIR",
            "LIUHAO_INSTANCE_DIR",
            "LIUHAO_LOG_DIR",
            "LIUHAO_LISTEN_PORT",
            "LIUHAO_LISTEN_HOST",
            "PORT",
            "LIUHAO_WEB_RESEARCH",
            "LIUHAO_WEB_RESEARCH_PROVIDERS",
            "LIUHAO_EMBEDDING_PROVIDER",
            "LIUHAO_EMBEDDING_API_KEY",
            "LIUHAO_EMBEDDING_MODEL",
            "OPENAI_API_KEY",
            "LIUHAO_INTEL_ALLOW_NETWORK",
            "LIUHAO_UPGRADE_MANIFEST_URL",
            "LIUHAO_UPGRADE_PUBLISH_DIR",
            "LIUHAO_ALERT_WEBHOOK",
            "OTEL_EXPORTER_OTLP_ENDPOINT",
        ):
            self.env[name] = ""

        proxy_url = f"http://127.0.0.1:{proxy_port}"
        self.env.update(
            {
                "APP_ENV": "production",
                "HOST": "127.0.0.1",
                "PORT": str(self.port),
                # 所有 urllib 出站都会先撞上代理（记录后 502）；回环直连不受影响。
                "HTTP_PROXY": proxy_url,
                "HTTPS_PROXY": proxy_url,
                "ALL_PROXY": proxy_url,
                "http_proxy": proxy_url,
                "https_proxy": proxy_url,
                "all_proxy": proxy_url,
                "NO_PROXY": "127.0.0.1,localhost,::1",
                "no_proxy": "127.0.0.1,localhost,::1",
                # 身份 / 数据 / 日志全部落临时根。
                "LIUHAO_HUMAN_IDENTITIES_BACKEND": "file",
                "LIUHAO_HUMAN_IDENTITIES_FILE": str(self.root / "identities.json"),
                "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY": f"e2e-{os.urandom(8).hex()}",
                "LIUHAO_AUTH_SECRETS_FILE": str(self.root / "auth_secrets.json"),
                "LIUHAO_JWT_SECRET": os.urandom(32).hex(),
                "AUDIT_DB_PATH": str(self.root / "audit_store.db"),
                "LIUHAO_MODEL_CONFIG_DB": str(self.root / "models.sqlite3"),
                "LIUHAO_MODEL_CONFIG_KEY": "e2e-model-config-key",
                "LIUHAO_MODEL_CONFIG_MIGRATE": "0",
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
                # 默认关闭的开关落到临时偏好文件（文件不存在 = 关闭）。
                "LIUHAO_PRIVACY_SETTINGS_FILE": str(self.root / "privacy_settings.json"),
                "LIUHAO_TSA_PROVIDER": "local",
                "OTEL_SDK_DISABLED": "true",
                # 离线可重复：mock provider，不需要真 Key。
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
                raise GatewayStartError("网关启动即退出：\n" + self.tail())
            status, _ = call(self.base + "/v1/health", timeout=3)
            if status == 200:
                return
            time.sleep(1)
        raise GatewayStartError("网关 120 秒内未就绪：\n" + self.tail())

    def tail(self) -> str:
        try:
            return self.log_path.read_text(encoding="utf-8", errors="replace")[-6000:]
        except OSError:
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
        except Exception:  # noqa: BLE001
            if self.proc is not None:
                self.proc.kill()
        try:
            if self._log is not None:
                self._log.close()
        except Exception:  # noqa: BLE001
            pass
        self.proc = None
        self._log = None

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


#: 本脚本自己的请求必须绕过系统代理，否则会把验收客户端的流量算到"出站"里。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(
    url: str,
    *,
    method: str = "GET",
    body: Optional[Dict[str, Any]] = None,
    token: Optional[str] = None,
    timeout: float = 90.0,
) -> Tuple[int, bytes]:
    data = None
    headers = {"Accept": "application/json, text/html, */*"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except Exception as exc:  # noqa: BLE001 - 网络层失败也要成为证据
        return 0, f"{type(exc).__name__}: {exc}".encode("utf-8")


def json_of(payload: bytes) -> Dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return {}
    return value if isinstance(value, dict) else {}


CHECK: List[Tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECK.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" | {detail}" if detail else ""), flush=True)
    return bool(ok)


def run_flow(gateway: IsolatedGateway) -> None:
    base = gateway.base
    origin = base

    status, payload = call(base + "/v1/health")
    check("启动：/v1/health 200", status == 200, f"HTTP {status}")

    # 首屏：HTML + 同源的 JS/CSS（如果本次运行有构建产物）。
    status, payload = call(base + "/")
    check("首屏：GET / 可访问", status in (200, 404), f"HTTP {status}")
    assets = re.findall(rb'(?:src|href)="(/(?:assets|static)/[^"]+\.(?:js|css))"', payload)
    for asset in dict.fromkeys(assets[:6]):
        asset_status, _ = call(base + asset.decode("utf-8", "replace"))
        check(
            f"首屏资源：{asset.decode('utf-8', 'replace')}",
            asset_status in (200, 404),
            f"HTTP {asset_status}",
        )

    owner = f"egress-{os.urandom(4).hex()}"
    password = "Egress-E2E-2026"
    status, payload = call(
        base + "/v1/accounts/register",
        method="POST",
        body={"principal": owner, "password": password},
        token=None,
        timeout=30,
    )
    check("登录：首次创建账户", status in (200, 201), f"HTTP {status}")

    status, payload = call(
        base + "/v1/auth/login",
        method="POST",
        body={"principal": owner, "secret": password},
        timeout=30,
    )
    token = str(json_of(payload).get("access_token") or json_of(payload).get("token") or "")
    check("登录：拿到令牌", status == 200 and bool(token), f"HTTP {status}")

    if token:
        status, payload = call(base + "/v1/about", token=token, timeout=30)
        check("关于页：/v1/about 200", status == 200, f"HTTP {status}")

    status, payload = call(
        base + "/v1/chat",
        method="POST",
        body={"message": "你好，确认默认状态下可以正常对话。"},
        token=token,
        timeout=120,
    )
    check("发消息：/v1/chat 200", status == 200, f"HTTP {status}")

    # 检索：默认必须是关闭的；再发一条"会触发检索"的问题，仍然不允许出网。
    status, payload = call(base + "/v1/network/research", token=token)
    state = json_of(payload)
    check(
        "检索：默认关闭（enabled=false / stored=false）",
        status == 200 and state.get("enabled") is False,
        f"HTTP {status} enabled={state.get('enabled')} stored={state.get('stored_enabled')}",
    )

    status, payload = call(
        base + "/v1/chat",
        method="POST",
        body={"message": "请帮我查一下本周量子计算的最新进展。"},
        token=token,
        timeout=120,
    )
    check("检索：未开启时提问不出网", status == 200, f"HTTP {status}")

    status, payload = call(base + "/v1/diagnostics/bundle", token=token, timeout=90)
    check(
        "诊断包：导出成功",
        status == 200 and payload[:2] == b"PK",
        f"HTTP {status} bytes={len(payload)}",
    )

    status, payload = call(base + "/v1/upgrade/check", method="POST", token=token, timeout=30)
    check(
        "检查更新：无更新源时不发请求",
        status in (200, 400, 403, 409),
        f"HTTP {status} body={payload[:120].decode('utf-8', 'replace')}",
    )

    time.sleep(1.0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true", help="保留临时目录（排障用）")
    args = parser.parse_args()

    proxy: Optional[BlockingProxy] = None
    sampler: Optional[ConnectionSampler] = None
    gateway: Optional[IsolatedGateway] = None
    exit_code = EXIT_PASS

    try:
        proxy = BlockingProxy()
        proxy.start()
        proxy.self_test()
        print(f"拦截代理：127.0.0.1:{proxy.port}（记录后一律 502，不转发）", flush=True)

        gateway = IsolatedGateway(proxy.port)
        print(f"实例根目录：{gateway.root}", flush=True)
        print(f"网关端口：{gateway.port}", flush=True)
        gateway.start()

        if gateway.proc is None or not sees_listening(gateway.proc.pid, gateway.port):
            raise EnvUnavailable("无法观测到网关的监听 socket，不能判定零出站")

        sampler = ConnectionSampler(gateway.proc.pid)
        sampler.start()

        run_flow(gateway)
        time.sleep(1.0)

        nonloop = sampler.snapshot()
        proxied = proxy.snapshot()

        check(
            "证据：网关进程非回环连接 0 条",
            len(nonloop) == 0,
            f"count={len(nonloop)} targets={nonloop}",
        )
        check(
            "证据：拦截代理收到 0 条出站请求",
            len(proxied) == 0,
            f"count={len(proxied)} targets={proxied}",
        )

        if any(not ok for _, ok, _ in CHECK):
            exit_code = EXIT_FAIL
    except EnvUnavailable as exc:
        print(f"[ENV] 环境不具备，未判定通过：{exc}", flush=True)
        exit_code = EXIT_ENV
    except GatewayStartError as exc:
        print(f"[FAIL] 网关启动/流程失败：{exc}", flush=True)
        exit_code = EXIT_FAIL
    except Exception as exc:  # noqa: BLE001 - 任何未预期异常都不能算通过
        print("[FAIL] 未预期异常：", flush=True)
        traceback.print_exc()
        print(f"{type(exc).__name__}: {exc}", flush=True)
        exit_code = EXIT_FAIL
    finally:
        if sampler is not None:
            sampler.stop()
        if gateway is not None:
            gateway.stop()
        if proxy is not None:
            proxy.stop()
        if gateway is not None and not args.keep:
            gateway.cleanup()

    failed = [item for item in CHECK if not item[1]]
    print()
    print(f"合计：{len(CHECK)} 项，通过 {len(CHECK) - len(failed)}，失败 {len(failed)}")
    for name, _, detail in failed:
        print(f"  - {name} | {detail}")
    print(f"退出码：{exit_code}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
