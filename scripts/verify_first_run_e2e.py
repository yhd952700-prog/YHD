#!/usr/bin/env python3
"""首次安装端到端自检：模拟“刚装好的机器”，只用界面/公开 HTTP 接口完成闭环。

一条命令重跑（真实 Key 从参数或环境变量取，不写进仓库）::

    python scripts/verify_first_run_e2e.py --api-key sk-...
    LIUHAO_E2E_API_KEY=sk-... python scripts/verify_first_run_e2e.py

覆盖的步骤（与用户的实际顺序一致）：

1. 全新空数据目录 + 不预置任何模型配置启动服务；
2. 记录控制台首页是否可打开、构建产物里是否有“去配置模型”的指引；
3. 登录（管理员身份由“安装阶段”创建，和 ``start-liuhao.bat`` 走同一函数）；
4. 通过 ``POST /v1/models`` 填 Key（选 deepseek 预设）并保存；
5. ``POST /v1/models/{id}/test`` 真实测试连接；
6. ``POST /v1/chat`` 立刻发起真实对话（不重启）；
7. 杀掉进程、用同一数据目录重启，再对话一次（证明 Key 仍在、无需重填）；
8. 用错误 Key 测试连接，确认提示能说清是“Key 无效”而不是笼统失败。

被吊销 / 额度耗尽的 Key 无法凭空取得，本脚本会明确打印“未用真实 Key 验证”，
这两类由 ``tests/test_gateway_models_api.py`` 的归类单测覆盖（402→额度耗尽，
401+revoked/deactivated→已吊销）。

全程不手工改配置、不手工写数据库；数据目录由脚本用环境变量指向临时目录，
测试结束后删除（``--keep`` 可保留以便排查）。
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]

_ADMIN = "first-run-admin"
_PASSWORD = "first-run-check-passphrase"


class Checks:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.skips: list[str] = []

    def ok(self, name: str, detail: str, elapsed_ms: Optional[float] = None) -> None:
        suffix = f"  ({elapsed_ms:.0f} ms)" if elapsed_ms is not None else ""
        print(f"  [PASS] {name}{suffix}\n         {detail}")

    def fail(self, name: str, detail: str) -> None:
        self.failures.append(name)
        print(f"  [FAIL] {name}\n         {detail}")

    def skip(self, name: str, detail: str) -> None:
        self.skips.append(name)
        print(f"  [SKIP] {name}\n         {detail}")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request(
    base: str,
    path: str,
    method: str = "GET",
    body: Optional[Dict[str, Any]] = None,
    token: Optional[str] = None,
    timeout: float = 40.0,
) -> Tuple[int, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(base + path, data=data, method=method)
    request.add_header("Accept", "application/json")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            try:
                return response.status, json.loads(raw or b"{}")
            except json.JSONDecodeError:
                return response.status, raw.decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return exc.code, raw.decode("utf-8", "replace")


def _fresh_env(data_dir: Path, port: int) -> Dict[str, str]:
    """把所有持久化状态指到临时目录——等价于一台刚装好、还没有任何数据的机器。"""
    env = dict(os.environ)
    env.update(
        {
            "APP_ENV": "development",
            "HOST": "127.0.0.1",
            "PORT": str(port),
            "LOG_LEVEL": "warning",
            "LIUHAO_HUMAN_IDENTITIES_BACKEND": "file",
            "LIUHAO_HUMAN_IDENTITIES_FILE": str(data_dir / "human_identities.json"),
            "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY": secrets.token_hex(32),
            "LIUHAO_AUTH_SECRETS_FILE": str(data_dir / "auth_secrets.json"),
            "LIUHAO_MODEL_CONFIG_DB": str(data_dir / "model_registry.sqlite3"),
            "LIUHAO_MODEL_CONFIG_KEY_FILE": str(data_dir / "model_key.bin"),
            "CONVERSATION_DB_PATH": str(data_dir / "conversation_store.sqlite3"),
            "PERSONAL_CONTEXT_DB_PATH": str(data_dir / "personal_context.sqlite3"),
            "LIUHAO_KNOWLEDGE_DB": str(data_dir / "knowledge.sqlite3"),
            "AUDIT_DB_PATH": str(data_dir / "audit.sqlite3"),
            "LIUHAO_JWT_REVOCATION_DB": str(data_dir / "revocations.sqlite3"),
        }
    )
    return env


def _bootstrap_admin(env: Dict[str, str], checks: Checks) -> bool:
    """安装阶段：和 ``start-liuhao.bat`` 调用的同一个 register_admin。"""
    os.environ.update(env)
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    started = time.monotonic()
    try:
        from scripts.first_run_wizard import register_admin

        register_admin(_ADMIN, "首次安装自检", _PASSWORD)
    except Exception as exc:  # pragma: no cover - 真实安装路径失败时给出原文
        checks.fail("安装阶段创建管理员", f"{type(exc).__name__}: {exc}")
        return False
    checks.ok(
        "安装阶段创建管理员（与 start-liuhao.bat 同一路径）",
        f"principal={_ADMIN}",
        (time.monotonic() - started) * 1000,
    )
    return True


class Gateway:
    def __init__(self, env: Dict[str, str], log_path: Path) -> None:
        self._env = env
        self._log_path = log_path
        self._proc: Optional[subprocess.Popen] = None
        self._log = None

    def start(self, base: str, checks: Checks) -> bool:
        self._log = open(self._log_path, "ab")
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "src.gateway"],
            cwd=str(REPO_ROOT),
            env=self._env,
            stdout=self._log,
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                checks.fail(
                    "服务启动",
                    f"进程提前退出（exit={self._proc.returncode}），日志：{self._tail()}",
                )
                return False
            try:
                status, _ = _request(base, "/v1/health", timeout=3.0)
                if status == 200:
                    checks.ok("服务启动并健康", base)
                    return True
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                pass
            time.sleep(0.4)
        checks.fail("服务启动", f"60s 内 /v1/health 未就绪，日志：{self._tail()}")
        return False

    def stop(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=10)
        if self._log is not None:
            self._log.close()
            self._log = None

    def _tail(self, lines: int = 12) -> str:
        try:
            text = self._log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return "<无日志>"
        return "\n".join(text.splitlines()[-lines:]) or "<无日志>"


def _find_console_asset(html: str) -> Optional[str]:
    marker = "<script"
    for chunk in html.split(marker)[1:]:
        if 'src="' not in chunk:
            continue
        src = chunk.split('src="', 1)[1].split('"', 1)[0]
        if src.endswith(".js"):
            return src
    return None


def _console_guidance(base: str, checks: Checks) -> None:
    status, html = _request(base, "/", timeout=10.0)
    if status != 200 or not isinstance(html, str) or "<div id=\"root\"" not in html:
        checks.fail("控制台可打开", f"GET / -> HTTP {status}，未返回 console 首页")
        return
    checks.ok("控制台可打开（未登录先到登录墙）", "GET / -> 200, 返回 console index.html")

    asset = _find_console_asset(html)
    if not asset:
        checks.skip("控制台有“去填 Key”指引", "index.html 中未找到 JS 资源，无法检查构建产物")
        return
    asset_status, script = _request(base, asset, timeout=15.0)
    if asset_status != 200 or not isinstance(script, str):
        checks.fail("控制台有“去填 Key”指引", f"GET {asset} -> HTTP {asset_status}")
        return
    hints = [text for text in ("去配置模型", "添加模型", "API Key") if text in script]
    if hints:
        checks.ok("控制台有“去填 Key”指引", f"构建产物包含：{'、'.join(hints)}")
    else:
        checks.fail(
            "控制台有“去填 Key”指引",
            "构建产物里没有找到“去配置模型/添加模型/API Key”文案；"
            "用户会看到一个不知道怎么开始的界面",
        )


def _login(base: str, checks: Checks) -> Optional[str]:
    started = time.monotonic()
    status, payload = _request(
        base, "/v1/auth/login", "POST", {"principal": _ADMIN, "secret": _PASSWORD}
    )
    elapsed = (time.monotonic() - started) * 1000
    if status == 200 and isinstance(payload, dict) and payload.get("access_token"):
        checks.ok("登录", f"scope={payload.get('scope')} 权限={payload.get('permissions')}", elapsed)
        return str(payload["access_token"])
    checks.fail("登录", f"HTTP {status}: {json.dumps(payload, ensure_ascii=False)[:300]}")
    return None


def _add_model(
    base: str,
    token: str,
    preset: Dict[str, Any],
    api_key: str,
    model_override: Optional[str],
    checks: Checks,
) -> Optional[str]:
    body = {
        "name": "DeepSeek（首次自检）",
        "provider": preset.get("provider") or "deepseek",
        "base_url": preset.get("base_url") or "https://api.deepseek.com",
        "model": model_override or preset.get("model") or "deepseek-chat",
        "api_key": api_key,
        "timeout": 30,
        "is_default": True,
    }
    started = time.monotonic()
    status, payload = _request(base, "/v1/models", "POST", body, token)
    elapsed = (time.monotonic() - started) * 1000
    if status not in (200, 201) or not isinstance(payload, dict):
        checks.fail("填写 Key 并保存", f"HTTP {status}: {json.dumps(payload, ensure_ascii=False)[:300]}")
        return None
    model_id = str(payload.get("id") or payload.get("model_id") or "")
    serialized = json.dumps(payload, ensure_ascii=False)
    if api_key in serialized:
        checks.fail("填写 Key 并保存", "响应里出现了明文 API Key")
    elif not model_id:
        checks.fail("填写 Key 并保存", f"响应没有模型 id：{serialized[:300]}")
    else:
        checks.ok("填写 Key 并保存（无需重启）", f"model_id={model_id} provider={body['provider']}", elapsed)
    return model_id or None


def _probe_saved(base: str, token: str, model_id: str, checks: Checks) -> bool:
    started = time.monotonic()
    status, payload = _request(base, f"/v1/models/{model_id}/test", "POST", None, token)
    elapsed = (time.monotonic() - started) * 1000
    ok = status == 200 and isinstance(payload, dict) and payload.get("ok") is True
    if ok:
        checks.ok(
            "真实测试连接",
            f"provider={payload.get('provider')} model={payload.get('model')} "
            f"上游延迟={payload.get('latency_ms')} ms",
            elapsed,
        )
    else:
        checks.fail(
            "真实测试连接",
            f"HTTP {status}: {json.dumps(payload, ensure_ascii=False)[:300]}",
        )
    return ok


def _chat(base: str, token: str, session: str, checks: Checks, label: str) -> Optional[str]:
    started = time.monotonic()
    status, payload = _request(
        base,
        "/v1/chat",
        "POST",
        {"message": "请用一句话回答：1+1 等于几？", "session_id": session},
        token,
        timeout=60.0,
    )
    elapsed = (time.monotonic() - started) * 1000
    if status != 200 or not isinstance(payload, dict):
        checks.fail(label, f"HTTP {status}: {json.dumps(payload, ensure_ascii=False)[:300]}")
        return None
    reply = str(payload.get("reply") or "").strip()
    knowledge = payload.get("knowledge") if isinstance(payload.get("knowledge"), dict) else {}
    if not reply:
        checks.fail(label, f"回复为空：{json.dumps(payload, ensure_ascii=False)[:300]}")
        return None
    checks.ok(
        label,
        f"reply={reply[:60]!r} 端到端={elapsed:.0f} ms "
        f"knowledge.used={knowledge.get('used')} reason={knowledge.get('reason')}",
        elapsed,
    )
    return reply


def _wrong_key_probe(
    base: str, token: str, preset: Dict[str, Any], checks: Checks
) -> None:
    body = {
        "provider": preset.get("provider") or "deepseek",
        "base_url": preset.get("base_url") or "https://api.deepseek.com",
        "model": preset.get("model") or "deepseek-chat",
        "api_key": "sk-definitely-not-a-real-key-000000",
        "timeout": 20,
    }
    started = time.monotonic()
    status, payload = _request(base, "/v1/models/test", "POST", body, token)
    elapsed = (time.monotonic() - started) * 1000
    if status != 200 or not isinstance(payload, dict):
        checks.fail("错误 Key 的提示", f"HTTP {status}: {json.dumps(payload, ensure_ascii=False)[:300]}")
        return
    if payload.get("ok") is True:
        checks.fail("错误 Key 的提示", "错误 Key 竟然测试成功了")
        return
    reason = payload.get("reason")
    detail = str(payload.get("detail") or "")
    if reason == "invalid_key" and "无效" in detail:
        checks.ok("错误 Key 的提示能说清类别", f"reason={reason} detail={detail[:80]!r}", elapsed)
    else:
        checks.fail(
            "错误 Key 的提示能说清类别",
            f"reason={reason!r} detail={detail[:120]!r}（期望 invalid_key + 中文提示）",
        )


def _plain_key_absent(data_dir: Path, api_key: str, checks: Checks) -> None:
    db_path = data_dir / "model_registry.sqlite3"
    try:
        blob = db_path.read_bytes()
    except OSError as exc:
        checks.fail("Key 加密落库", f"读不到 {db_path}: {exc}")
        return
    if api_key.encode("utf-8") in blob:
        checks.fail("Key 加密落库", "数据库文件里出现了明文 Key")
    else:
        checks.ok("Key 加密落库", "SQLite 文件中不含明文 Key（仅读取校验，未写入）")


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--api-key", default=os.environ.get("LIUHAO_E2E_API_KEY", ""),
        help="真实提供方 API Key（也可用环境变量 LIUHAO_E2E_API_KEY）",
    )
    parser.add_argument("--model", default="", help="覆盖预设模型名")
    parser.add_argument("--keep", action="store_true", help="保留临时数据目录用于排查")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if not args.api_key.strip():
        print("需要 --api-key 或 LIUHAO_E2E_API_KEY", file=sys.stderr)
        return 2

    checks = Checks()
    data_dir = Path(tempfile.mkdtemp(prefix="liuhao-first-run-"))
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    print(f"临时数据目录: {data_dir}\n服务地址: {base}\n")

    gateway: Optional[Gateway] = None
    try:
        print("[1] 全新空数据目录 + 不预置模型配置启动")
        env = _fresh_env(data_dir, port)
        if not _bootstrap_admin(env, checks):
            return 1
        gateway = Gateway(env, data_dir / "gateway.log")
        if not gateway.start(base, checks):
            return 1

        print("\n[2] 控制台首屏与指引")
        _console_guidance(base, checks)
        status, health = _request(base, "/v1/health")
        print(f"  /v1/health -> HTTP {status}: {json.dumps(health, ensure_ascii=False)[:220]}")

        print("\n[3] 登录并确认“还没有任何模型”")
        token = _login(base, checks)
        if not token:
            return 1
        status, models = _request(base, "/v1/models", token=token)
        count = len(models.get("models") or []) if isinstance(models, dict) else -1
        if status == 200 and count == 0:
            checks.ok("空模型状态可查询", "GET /v1/models -> 0 个模型（未预置）")
        else:
            checks.fail("空模型状态可查询", f"HTTP {status}, models={count}")

        status, presets = _request(base, "/v1/models/presets", token=token)
        preset = {}
        if status == 200 and isinstance(presets, dict):
            preset = next(
                (item for item in presets.get("presets") or [] if item.get("provider") == "deepseek"),
                {},
            )
        if not preset:
            checks.fail("提供方预设可用", f"HTTP {status}: 未找到 deepseek 预设")
            preset = {"provider": "deepseek", "base_url": "https://api.deepseek.com", "model": "deepseek-chat"}

        print("\n[4] 填 Key → 保存（不重启）")
        model_id = _add_model(base, token, preset, args.api_key.strip(), args.model, checks)
        if not model_id:
            return 1

        print("\n[5] 立刻测试连接")
        _probe_saved(base, token, model_id, checks)

        print("\n[6] 不重启，立刻发起真实对话")
        _chat(base, token, "first-run-session", checks, "真实对话（首次安装）")
        _plain_key_absent(data_dir, args.api_key.strip(), checks)

        print("\n[7] 重启后 Key 仍在、无需重填")
        gateway.stop()
        gateway = Gateway(env, data_dir / "gateway-restart.log")
        if not gateway.start(base, checks):
            return 1
        token2 = _login(base, checks)
        if not token2:
            return 1
        status, models2 = _request(base, "/v1/models", token=token2)
        kept = (
            status == 200
            and isinstance(models2, dict)
            and len(models2.get("models") or []) >= 1
        )
        (checks.ok if kept else checks.fail)(
            "重启后模型配置仍在",
            "GET /v1/models -> 1 个模型" if kept else f"HTTP {status}: {json.dumps(models2, ensure_ascii=False)[:200]}",
        )
        _chat(base, token2, "first-run-session-after-restart", checks, "真实对话（重启后）")

        print("\n[8] 错误 Key 的提示")
        _wrong_key_probe(base, token2, preset, checks)
        checks.skip(
            "被吊销的 Key",
            "无法凭空获得真实被吊销的 Key；归类逻辑（401+revoked/deactivated -> key_revoked）"
            "由 tests/test_gateway_models_api.py 覆盖",
        )
        checks.skip(
            "额度耗尽的 Key",
            "无法凭空耗尽真实账户额度；归类逻辑（402 / insufficient balance -> quota_exhausted）"
            "由 tests/test_gateway_models_api.py 覆盖",
        )
    finally:
        if gateway is not None:
            gateway.stop()
        if args.keep:
            print(f"\n保留数据目录: {data_dir}")
        else:
            import shutil

            shutil.rmtree(data_dir, ignore_errors=True)

    print("\n" + "=" * 60)
    if checks.failures:
        print(f"结果：FAIL（{len(checks.failures)} 项失败，{len(checks.skips)} 项跳过）")
        for name in checks.failures:
            print(f"  - {name}")
        return 1
    print(f"结果：PASS（0 项失败，{len(checks.skips)} 项跳过/无法真实验证）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
