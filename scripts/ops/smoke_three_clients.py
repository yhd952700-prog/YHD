#!/usr/bin/env python
"""冒烟：发布包 + 三端登录 + PWA 资源（对着真实 HTTP 端口跑）。

为什么单独跑一遍而不用 pytest
------------------------------
`tests/test_gateway_auth.py` 覆盖 ASGI 层面的鉴权语义，`tests/test_console_single_port.py`
覆盖单端口挂载与 PWA 资源的类型。这里要证明的是**另一件事**：真正启动
``deploy/cloud/serve.py`` 之后，浏览器会收到的那一组响应 —— 静态资源的
Content-Type、真实签发的令牌，以及"同一个账号在三个端都能拿到可用会话"。

**刻意不叫 `verify_*.py`。** 那个命名空间是 CI 门控（见
``tests/test_guardrail_scripts.py`` 的元护栏），而本脚本需要先构建发布包
（``npm run build`` + ``build_cloud_bundle.py``），CI 里跑不了 —— 挂进 CI 只会得到
一个永远 skip 的"假护栏"。

全部用标准库（urllib / subprocess），不依赖测试环境里装了什么。

用法::

    .venv\\Scripts\\python.exe scripts\\ops\\smoke_three_clients.py
    .venv\\Scripts\\python.exe scripts\\ops\\smoke_three_clients.py --port 8123
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PY = Path(sys.executable)
BUNDLE = REPO / "deploy" / "cloud"
PASSWORD = "smoke-Only-Test-Password-9271"

RESULTS: list[tuple[bool, str, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((bool(ok), label, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  -- {detail}" if detail else ""),
          flush=True)


class Client:
    """对被测服务的最小 HTTP 客户端。"""

    def __init__(self, base: str) -> None:
        self.base = base

    def request(self, path: str, *, token: str | None = None, method: str = "GET",
                payload: dict | None = None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                return (response.status, response.headers.get("content-type", ""),
                        response.read())
        except urllib.error.HTTPError as error:
            return error.code, error.headers.get("content-type", ""), error.read()

    def json(self, path: str, **kwargs):
        status, ctype, body = self.request(path, **kwargs)
        try:
            return status, ctype, json.loads(body.decode("utf-8"))
        except Exception:  # noqa: BLE001
            return status, ctype, None


def jwt_payload(token: str) -> dict:
    part = token.split(".")[1]
    part += "=" * (-len(part) % 4)
    return json.loads(base64.urlsafe_b64decode(part).decode("utf-8"))


def wait_ready(client: Client, timeout: float = 90.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{client.base}/v1/health", timeout=3):
                return True
        except Exception:  # noqa: BLE001
            time.sleep(0.4)
    return False


def run(port: int) -> int:
    if not (BUNDLE / "serve.py").is_file():
        print(f"!! 发布包不存在：{BUNDLE}/serve.py\n"
              "   先执行：python scripts/build_cloud_bundle.py", file=sys.stderr)
        return 2

    work = Path(tempfile.mkdtemp(prefix="liuhao-smoke-"))
    identities = work / "human_identities.json"
    secrets = work / "auth_secrets.json"
    log_path = work / "serve.log"
    base = f"http://127.0.0.1:{port}"

    env = os.environ.copy()
    env["PORT"] = str(port)
    env["LIUHAO_HUMAN_IDENTITIES_FILE"] = str(identities)
    env["LIUHAO_AUTH_SECRETS_FILE"] = str(secrets)
    env["PYTHONIOENCODING"] = "utf-8"

    print("=" * 70)
    print("1. 注册人类身份（真实脚本；密码走 stdin，不进 argv）")
    print("=" * 70)
    registered = subprocess.run(
        [str(PY), "scripts/register_human_identity.py",
         "--principal", "ceo", "--display-name", "老板", "--password-stdin"],
        cwd=str(REPO), env=env, input=PASSWORD + "\n",
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    check("注册脚本退出码 0", registered.returncode == 0, registered.stderr.strip()[:200])
    check("凭据文件已生成", secrets.is_file(), str(secrets))

    listing = subprocess.run(
        [str(PY), "scripts/register_human_identity.py", "--list"],
        cwd=str(REPO), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    check("--list 报出该主体且标记为可登录",
          "[OK ] ceo" in listing.stdout and "login credential set" in listing.stdout
          and "Can sign in: 1/1" in listing.stdout,
          " / ".join(listing.stdout.strip().splitlines()[2:5]))
    check("--list 不回显明文密码", PASSWORD not in listing.stdout + listing.stderr)

    print()
    print("=" * 70)
    print("2. 启动发布包 deploy/cloud/serve.py")
    print("=" * 70)
    with log_path.open("w", encoding="utf-8") as handle:
        server = subprocess.Popen([str(PY), "serve.py"], cwd=str(BUNDLE), env=env,
                                  stdout=handle, stderr=subprocess.STDOUT)
    client = Client(base)
    try:
        if not wait_ready(client):
            check("服务在 /v1/health 上就绪", False)
            print(log_path.read_text(encoding="utf-8", errors="replace")[-2000:])
            return 1
        check("服务在 /v1/health 上就绪", True, base)

        print()
        print("=" * 70)
        print("3. PWA 资源（浏览器实际会收到的响应）")
        print("=" * 70)
        status, ctype, body = client.request("/")
        check("GET / → 200 text/html", status == 200 and ctype.startswith("text/html"),
              f"{status} {ctype}")
        check("外壳含 root 挂载点与 manifest 声明",
              b'id="root"' in body and b"manifest.webmanifest" in body)

        status, ctype, body = client.request("/sw.js")
        check("GET /sw.js → JavaScript MIME（类型错了就无法注册）",
              status == 200 and "javascript" in ctype, f"{status} {ctype}")
        check("SW 明确不缓存鉴权接口", b"isLiveApi" in body and b"/v1/" in body)

        status, ctype, body = client.json("/manifest.webmanifest")
        manifest = body or {}
        check("GET /manifest.webmanifest → application/manifest+json",
              status == 200 and ctype.startswith("application/manifest+json"),
              f"{status} {ctype}")
        check("display=standalone（装成应用，而不是标签页）",
              manifest.get("display") == "standalone", str(manifest.get("display")))
        purposes = {p for icon in manifest.get("icons", [])
                    for p in str(icon.get("purpose") or "").split()}
        check("声明了 maskable 图标", "maskable" in purposes, str(sorted(purposes)))
        check("声明了至少三个 shortcuts", len(manifest.get("shortcuts", [])) >= 3)

        for name in ("icon-192.png", "icon-512.png", "icon-512-maskable.png",
                     "apple-touch-icon.png"):
            status, ctype, body = client.request("/" + name)
            check(f"GET /{name} → 200 image/png",
                  status == 200 and ctype == "image/png"
                  and body[:8] == b"\x89PNG\r\n\x1a\n",
                  f"{status} {ctype} {len(body)}B")
        status, ctype, _ = client.request("/favicon.svg")
        check("GET /favicon.svg → image/svg+xml", status == 200 and "svg" in ctype,
              f"{status} {ctype}")

        status, ctype, _ = client.request("/?p=approval")
        check("深链 /?p=approval → 200 text/html", status == 200 and ctype.startswith("text/html"))
        status, ctype, _ = client.request("/v1/does-not-exist")
        check("未知 API 路径仍是 404 JSON（没被 SPA 外壳吞掉）",
              status == 404 and ctype.startswith("application/json"), f"{status} {ctype}")

        print()
        print("=" * 70)
        print("4. 三端登录")
        print("=" * 70)
        status, _, config = client.json("/v1/auth/config")
        config = config or {}
        check("GET /v1/auth/config 无需令牌即可读（登录页要用）", status == 200, str(status))
        check("凭据存储 configured=true",
              config.get("secret_store", {}).get("configured") is True)
        check("至少一个可登录人类", (config.get("login_eligible_humans") or 0) >= 1,
              str(config.get("login_eligible_humans")))
        check("配置声明的三端与实现一致",
              config.get("client_modes") == ["desktop", "web", "mobile"],
              str(config.get("client_modes")))

        tokens: dict[str, str] = {}
        for mode in ("desktop", "web", "mobile"):
            status, _, data = client.json(
                "/v1/auth/login", method="POST",
                payload={"principal": "ceo", "secret": PASSWORD, "client": mode},
            )
            data = data or {}
            token = data.get("access_token", "")
            if token:
                tokens[mode] = token
            payload = jwt_payload(token) if token else {}
            check(f"client={mode} 登录成功并签发令牌", status == 200 and bool(token), str(status))
            check(f"client={mode} 令牌记录了来源端",
                  payload.get("metadata", {}).get("client") == mode,
                  str(payload.get("metadata")))
            check(f"client={mode} 令牌主体是 ceo", payload.get("sub") == "ceo",
                  str(payload.get("sub")))

        check("三端拿到的是三个不同会话（jti 互不相同）",
              len({jwt_payload(t).get("jti") for t in tokens.values()}) == len(tokens))

        print()
        print("=" * 70)
        print("5. 令牌真的能用（端到端，不只是「能签发」）")
        print("=" * 70)
        any_token = next(iter(tokens.values()))
        status, _, me = client.json("/v1/auth/me", token=any_token)
        check("GET /v1/auth/me → still_human=true",
              status == 200 and (me or {}).get("still_human") is True, f"{status}")

        status, _, _ = client.request("/v1/policy/enforcement")
        check("无令牌读策略端点 → 401", status == 401, str(status))
        status, _, enforcement = client.json("/v1/policy/enforcement", token=any_token)
        check("带令牌读策略端点 → 200（令牌确实解锁了受管面）", status == 200, str(status))
        check("策略快照来自真实内核配置",
              isinstance(enforcement, dict)
              and {"enabled", "enforced_actions"} <= set(enforcement),
              f"enabled={(enforcement or {}).get('enabled')} "
              f"count={(enforcement or {}).get('count')}")

        status, _, analytics = client.json("/v1/dashboard/analytics?days=14", token=any_token)
        check("带令牌读审计分析 → 200",
              status == 200 and (analytics or {}).get("timeseries", {}).get("available") is True,
              str(status))

        print()
        print("=" * 70)
        print("6. 负向：拒绝路径不能靠「看起来对」")
        print("=" * 70)
        status, _, wrong = client.json(
            "/v1/auth/login", method="POST",
            payload={"principal": "ceo", "secret": "definitely-wrong", "client": "web"})
        check("错误密码 → 401", status == 401, str(status))

        status, _, unknown = client.json(
            "/v1/auth/login", method="POST",
            payload={"principal": "no-such-person", "secret": "definitely-wrong",
                     "client": "web"})
        check("未知主体 → 401", status == 401, str(status))
        check("未知主体与错误密码返回同一句话（防主体枚举）",
              (unknown or {}).get("detail") == (wrong or {}).get("detail"),
              f"{(unknown or {}).get('detail')!r} vs {(wrong or {}).get('detail')!r}")

        status, _, _ = client.request("/v1/auth/me", token="not-a-jwt")
        check("伪造令牌 → 401", status == 401, str(status))
        status, _, _ = client.request("/v1/policy/approvals", token="not-a-jwt")
        check("伪造令牌读审批列表 → 401", status == 401, str(status))

        print()
        print("=" * 70)
        print("7. 登出真的吊销（后端撤销 + 令牌立刻失效）")
        print("=" * 70)
        web_token = tokens["web"]
        status, _, _ = client.request("/v1/auth/logout", method="POST", token=web_token)
        check("POST /v1/auth/logout → 200", status == 200, str(status))
        status, _, _ = client.request("/v1/auth/me", token=web_token)
        check("登出后同一令牌 → 401（撤销真实生效）", status == 401, str(status))
        status, _, _ = client.request("/v1/auth/me", token=tokens["mobile"])
        check("登出只影响自己那个会话，另一个端仍有效", status == 200, str(status))

    finally:
        server.terminate()
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()
        shutil.rmtree(work, ignore_errors=True)

    failed = [item for item in RESULTS if not item[0]]
    print()
    print("=" * 70)
    print(f"结果：{len(RESULTS) - len(failed)}/{len(RESULTS)} 通过")
    for _, label, detail in failed:
        print(f"  FAIL  {label}  {detail}")
    print("=" * 70)
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="发布包三端登录冒烟")
    parser.add_argument("--port", type=int, default=8099, help="被测端口（默认 8099）")
    args = parser.parse_args()
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    return run(args.port)


if __name__ == "__main__":
    raise SystemExit(main())
