#!/usr/bin/env python3
"""多账号八项硬要求的可重复验收（接口层 + 界面层）。

一条命令跑完：
    .venv\\Scripts\\python.exe scripts\\acceptance\\account_matrix_acceptance.py

它会：
1. 在固定端口 19008、固定数据目录 ``D:\\_audit\\account-matrix-2`` 起一个干净实例；
2. 构造主账号 + 两个子账号 + 一个孙账号 + 自助注册账号；
3. 用真实 HTTP 请求逐项打八项要求的正反例；
4. 用 Playwright 打开真实控制台，以子账号登录，扫描可见文本 / DOM / 本地存储 /
   网络响应里是否出现上级、路径、深度、创建者；
5. 走一遍控制台的自助注册完整流程（点注册 → 填表 → 提交 → 能登录）；
6. 补两条八项之外的边界：B1 降权后旧会话立即失效；B2 导出包在新数据目录恢复后
   账号树 / Key 归属 / 记忆归属仍然成立；
7. 输出「8 行通过表 + 2 行附加边界 + 失败明细 + 每项证据」，并写一份 JSON 证据文件。

防误跑保护：
运行前扫描 ``src/`` 最近 120 秒内是否有 ``.py`` 写入。命中即拒绝运行并退出码 2，
提示「账号核心正在改动，避免把中间态当回归」；确认改动停止后重试，或显式加
``--force`` 跳过。这个坑来自 2026-11-08：两次运行之间 ``account_backup.py``
被并行修改，凭据导出一度为 0，验收把中间态误判成了产品回归。

约束：
* 只读：不修改 ``src/``、``apps/``、``installer/``、``packaging/``；
* 不触碰 ``D:\\LiuHaoData``，不运行 ``setup.exe``；
* 所有数据落在 ``D:\\_audit\\account-matrix-2`` 下的本脚本自建子目录。

退出码：0 全部通过；1 有失败；2 环境不满足（端口占用 / Playwright 缺失 /
``src/`` 近期有写入且未加 ``--force`` 等）。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

PY = sys.executable
DEFAULT_PORT = 19008
DEFAULT_DATA_ROOT = Path(r"D:\_audit\account-matrix-2")
BUNDLE_PASSPHRASE = "audit-bundle-2026"

REQUIREMENTS: Dict[str, str] = {
    "1": "主账号能看全部",
    "2": "子账号只能控自己的下级、不能越级",
    "3": "主账号能用自己的 Key 也能用子账号的 Key，子账号只能用自己那把",
    "4": "子账号的记忆归自己，主账号能看全部",
    "5": "子账号不能知道自己是被创建出来的（界面/接口不暴露）",
    "6": "主账号换机器忘密码有解（恢复码路径）",
    "7": "主账号能把全部打包带走（导出）",
    "8": "成员免邀请码自助注册",
}

#: 八项之外的附加边界，单独两行，不挤占正式八项判定表。
EXTRAS: Dict[str, str] = {
    "B1": "子账号被降权后旧会话立即失效（不能继续做原来能做的事）",
    "B2": "导出包在新数据目录恢复后，账号树 / Key 归属 / 记忆归属仍成立",
}

# 界面层禁用词：只要出现在可见文本 / DOM / 本地存储，就算子账号能感知到层级。
UI_FORBIDDEN_TERMS = (
    "主账号", "子账号", "上级", "下级", "层级", "越级",
    "创建者", "创建人", "由谁创建", "你由", "创建了你", "被创建",
    "父账号", "根账号",
)
# 结构字段：只扫 auth 响应与本地存储，避免把文件路径的 path 误判。
AUTH_STRUCT_KEYS = ("parent_account_id", "parentAccountId", "depth")
ACCOUNT_PATH_RE = re.compile(r'"(?:path|account_path)"\s*:\s*"(/[^"]*/[^"]*)"')

TEST_ACCOUNTS = {
    "boss": "Boss-Pass-2026",
    "workerA": "WorkerA-Pass-2026",
    "workerB": "WorkerB-Pass-2026",
    "workerA1": "WorkerA1-Pass-2026",
    "newbie-api": "Newbie-Api-Pass-2026",
    "newbie-ui": "Newbie-Ui-Pass-2026",
}
RECOVERED_BOSS_PASSWORD = "Boss-Recovered-2026"


# --------------------------------------------------------------------------- #
# HTTP 基础
# --------------------------------------------------------------------------- #

def http(
    method: str,
    url: str,
    payload: Optional[Dict[str, Any]] = None,
    *,
    token: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 90.0,
) -> Tuple[int, Any, str]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    merged = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        merged["Authorization"] = f"Bearer {token}"
    if headers:
        merged.update(headers)
    req = urllib.request.Request(url, data=data, headers=merged, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = int(resp.status)
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        raw = exc.read().decode("utf-8", "replace")
    except Exception as exc:  # 连接层错误也作为证据，不中断整轮
        return 0, {"transport_error": repr(exc)}, repr(exc)
    try:
        body = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        body = {"raw": raw}
    return status, body, raw


def error_code(body: Any) -> str:
    if not isinstance(body, dict):
        return ""
    if body.get("code"):
        return str(body["code"])
    detail = body.get("detail")
    if isinstance(detail, dict):
        return str(detail.get("code") or "")
    return ""


def tree_node(payload: Any) -> Dict[str, Any]:
    """``GET /v1/accounts/tree`` 的根节点在 ``{"tree": {...}}`` 里。"""
    if isinstance(payload, dict) and isinstance(payload.get("tree"), dict):
        return payload["tree"]
    return payload if isinstance(payload, dict) else {}


def count_tree(payload: Any) -> int:
    def walk(node: Dict[str, Any]) -> int:
        return 1 + sum(walk(child) for child in node.get("children", []) if isinstance(child, dict))

    try:
        return walk(tree_node(payload))
    except Exception:
        return -1


def brief(value: Any, limit: int = 600) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        text = repr(value)
    return text if len(text) <= limit else text[:limit] + "…"


def port_is_free(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


#: 防误跑窗口：账号核心写 ``src/`` 的中途跑验收，会把半成品当成产品回归。
GUARD_WINDOW_SECONDS = 120


def recent_src_writes(window: int = GUARD_WINDOW_SECONDS) -> List[Tuple[Path, float]]:
    """返回 ``src/`` 下最近 ``window`` 秒内被写入的 ``.py``（按时间倒序）。"""
    now = time.time()
    hits: List[Tuple[Path, float]] = []
    src = REPO / "src"
    if not src.is_dir():
        return hits
    for path in src.rglob("*.py"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if now - mtime <= window:
            hits.append((path, mtime))
    return sorted(hits, key=lambda item: item[1], reverse=True)


# --------------------------------------------------------------------------- #
# 证据收集
# --------------------------------------------------------------------------- #

class Evidence:
    def __init__(self) -> None:
        self.rows: List[Dict[str, Any]] = []
        self.notes: List[str] = []
        self.interrupted = False

    def check(
        self,
        item: str,
        name: str,
        request: str,
        expected: str,
        actual: str,
        *,
        ok: Optional[bool] = None,
        response: Any = None,
        note: str = "",
    ) -> bool:
        passed = (actual == expected) if ok is None else bool(ok)
        self.rows.append(
            {
                "item": str(item),
                "name": name,
                "request": request,
                "expected": expected,
                "actual": actual,
                "ok": passed,
                "response": brief(response) if response is not None else "",
                "note": note,
            }
        )
        return passed

    def note(self, text: str) -> None:
        self.notes.append(text)

    def rows_for(self, item: str) -> List[Dict[str, Any]]:
        return [row for row in self.rows if row["item"] == item]

    def failures(self) -> List[Dict[str, Any]]:
        return [row for row in self.rows if not row["ok"]]

    def dump(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "requirements": REQUIREMENTS,
                    "extras": EXTRAS,
                    "summary": self.summary(),
                    "notes": self.notes,
                    "rows": self.rows,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def summary(self) -> Dict[str, Any]:
        items: Dict[str, Any] = {}
        for key, title in {**REQUIREMENTS, **EXTRAS}.items():
            rows = self.rows_for(key)
            failed = [row for row in rows if not row["ok"]]
            items[key] = {
                "title": title,
                "checks": len(rows),
                "passed": len(rows) - len(failed),
                "failed": len(failed),
                "verdict": "未执行" if not rows else ("通过" if not failed else "不通过"),
            }
        total_failed = len(self.failures())
        complete = all(item["checks"] > 0 for item in items.values())
        return {
            "items": items,
            "checks": len(self.rows),
            "failed": total_failed,
            "interrupted": self.interrupted,
            "verdict": "通过" if not total_failed and complete and not self.interrupted else "不通过",
        }

    def print_table(self) -> None:
        summary = self.summary()
        print("\n" + "=" * 78)
        print("八项判定表")
        print("=" * 78)
        print(f"{'#':<3}{'要求':<44}{'检查':>5}{'通过':>6}{'失败':>6}  结论")
        print("-" * 78)
        for key in REQUIREMENTS:
            self._print_row(key, summary["items"][key])
        print("-" * 78)
        print("附加边界（八项之外）")
        print("-" * 78)
        for key in EXTRAS:
            self._print_row(key, summary["items"][key])
        print("-" * 78)
        print(f"合计 {summary['checks']} 项检查，失败 {summary['failed']} 项：{summary['verdict']}")

    @staticmethod
    def _print_row(key: str, row: Dict[str, Any]) -> None:
        mark = "🟢" if row["verdict"] == "通过" else ("🔴" if row["verdict"] == "不通过" else "⚪")
        title = row["title"]
        if len(title) > 42:
            title = title[:41] + "…"
        print(
            f"{key:<3}{title:<44}{row['checks']:>5}{row['passed']:>6}{row['failed']:>6}  {mark}"
        )

    def print_failures(self) -> None:
        failures = self.failures()
        if not failures:
            return
        print("\n" + "=" * 78)
        print("失败明细（复现请求 / 期望 / 实际）")
        print("=" * 78)
        for index, row in enumerate(failures, 1):
            label = f"附加 {row['item']}" if row["item"] in EXTRAS else f"第 {row['item']} 项"
            print(f"\n[{index}] {label} · {row['name']}")
            print(f"    请求: {row['request']}")
            print(f"    期望: {row['expected']}")
            print(f"    实际: {row['actual']}")
            if row["response"]:
                print(f"    响应: {row['response']}")
            if row["note"]:
                print(f"    说明: {row['note']}")


# --------------------------------------------------------------------------- #
# 实例生命周期
# --------------------------------------------------------------------------- #

class Instance:
    """隔离实例：固定端口 + 独立数据目录 + 独立身份库/审计库/工作区。"""

    def __init__(self, label: str, root: Path, port: int) -> None:
        self.label = label
        self.root = root
        self.port = port
        self.base = f"http://127.0.0.1:{port}"
        self.data_dir = root / "data"
        self.workspace = root / "workspace"
        self.log_path = root / "server.log"
        self.proc: Optional[subprocess.Popen] = None
        self._log = None

        self.jwt_secret = f"isolated-jwt-{secrets.token_hex(16)}"
        self.integrity_key = f"isolated-integrity-{secrets.token_hex(16)}"
        env = os.environ.copy()
        for name in ("LIUHAO_HUMAN_IDENTITIES_DB", "LIUHAO_DATA_DIR", "PORT"):
            env.pop(name, None)
        env.update(
            {
                "HOST": "127.0.0.1",
                "PORT": str(port),
                "APP_ENV": "development",
                "LIUHAO_HUMAN_IDENTITIES_BACKEND": "file",
                "LIUHAO_HUMAN_IDENTITIES_FILE": str(root / "identities.json"),
                "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY": self.integrity_key,
                "LIUHAO_AUTH_SECRETS_FILE": str(root / "auth_secrets.json"),
                "LIUHAO_JWT_SECRET": self.jwt_secret,
                "AUDIT_DB_PATH": str(root / "audit_store.db"),
                "LIUHAO_MODEL_CONFIG_DB": str(root / "models.sqlite3"),
                "LIUHAO_MODEL_CONFIG_KEY": "isolated-model-key",
                "LIUHAO_WORKSPACE_ROOT": str(self.workspace),
                "PERSONAL_CONTEXT_DB_PATH": str(root / "personal_context.sqlite3"),
                "CONVERSATION_DB_PATH": str(root / "conversations.sqlite3"),
                "LIUHAO_LOG_DIR": str(root / "logs"),
                "LIUHAO_ACCOUNT_USAGE_DB": str(root / "usage.sqlite3"),
                "LIUHAO_ACCOUNT_MEMORY_DB": str(root / "memory.sqlite3"),
                "LIUHAO_ACCOUNT_BACKUP_DB": str(root / "backup.sqlite3"),
                "LIUHAO_ACCOUNT_SETTINGS_FILE": str(root / "account_settings.json"),
                "LIUHAO_DATA_DIR": str(self.data_dir),
                "LIUHAO_ALLOW_REMOTE_RECOVERY": "0",
                "LIUHAO_RECOVERY_HANDOFF_TTL_SECONDS": "600",
                "LIUHAO_INSTANCE_DIR": str(root),
                "AI_PROVIDER_TYPE": "mock",
                "AI_PROVIDER_MODEL": "mock-model",
            }
        )
        self.env = env
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)

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
                raise RuntimeError(f"{self.label} 启动失败：\n{self.tail()}")
            try:
                status, _, _ = http("GET", f"{self.base}/v1/health", timeout=5)
                if status == 200:
                    return
            except Exception:
                pass
            time.sleep(1)
        raise RuntimeError(f"{self.label} 未在 120 秒内就绪：\n{self.tail()}")

    def tail(self, limit: int = 6000) -> str:
        try:
            return self.log_path.read_text(encoding="utf-8", errors="replace")[-limit:]
        except Exception:
            return ""

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                capture_output=True,
            )
        try:
            if self.proc is not None:
                self.proc.wait(timeout=10)
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


# --------------------------------------------------------------------------- #
# 本机证明（首次注册 / 导入 / 恢复码重置）
# --------------------------------------------------------------------------- #

def proof_headers(instance: Instance, *, reset: bool = False) -> Dict[str, str]:
    from src.common import local_proof_ipc

    bundle = local_proof_ipc.request_token_bundle(data_dir=instance.data_dir, timeout=10)
    header = "X-LiuHao-Reset-Proof" if reset else "X-LiuHao-Local-Proof"
    return {header: bundle["reset_token" if reset else "handoff_token"]}


# --------------------------------------------------------------------------- #
# 接口层验收
# --------------------------------------------------------------------------- #

class ApiError(RuntimeError):
    pass


def login(instance: Instance, principal: str, password: str) -> Tuple[str, Any]:
    status, body, _ = http(
        "POST",
        f"{instance.base}/v1/auth/login",
        {"principal": principal, "secret": password, "client": "web"},
    )
    if status != 200:
        raise ApiError(f"{principal} 登录失败 HTTP {status}: {brief(body)}")
    return str(body.get("token") or body.get("access_token") or ""), body


def call(
    instance: Instance,
    method: str,
    path: str,
    *,
    token: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[int, Any, str]:
    return http(method, f"{instance.base}{path}", payload, token=token, headers=headers)


def create_member(
    instance: Instance,
    boss_token: str,
    principal: str,
    role: str,
    *,
    allow_key_sharing: bool = False,
) -> str:
    status, body, _ = call(
        instance, "POST", "/v1/accounts",
        token=boss_token,
        payload={
            "principal": principal,
            "display_name": principal,
            "role": role,
            "allow_key_sharing": allow_key_sharing,
        },
    )
    if status != 200:
        raise ApiError(f"创建 {principal} 失败 HTTP {status}: {brief(body)}")
    return str(body.get("activation_code") or "")


def activate_member(instance: Instance, principal: str, code: str, password: str) -> Any:
    status, body, _ = call(
        instance, "POST", "/v1/accounts/activate",
        payload={"principal": principal, "code": code, "password": password},
    )
    if status != 200:
        raise ApiError(f"激活 {principal} 失败 HTTP {status}: {brief(body)}")
    return body


def create_mock_key(instance: Instance, token: str, name: str) -> str:
    status, body, _ = call(
        instance, "POST", "/v1/models",
        token=token,
        payload={
            "name": name,
            "provider": "mock",
            "model": "mock-model",
            "enabled": True,
            "is_default": False,
        },
    )
    if status != 200:
        raise ApiError(f"创建模型 {name} 失败 HTTP {status}: {brief(body)}")
    return str(body.get("id") or "")


def chat(instance: Instance, token: str, model_id: str, message: str, session_id: str) -> Tuple[int, Any]:
    status, body, _ = call(
        instance, "POST", "/v1/chat",
        token=token,
        payload={
            "message": message,
            "model_id": model_id,
            "session_id": session_id,
            "use_knowledge": False,
        },
    )
    return status, body


def write_memory(instance: Instance, token: str, text: str) -> Tuple[int, Any]:
    return call(
        instance, "POST", "/v1/memory/entries",
        token=token,
        payload={"text": text, "source": "acceptance", "tags": ["acceptance"]},
    )


def usage_calls(instance: Instance, boss_token: str, principal: str) -> Tuple[int, Any, int]:
    status, body, _ = call(instance, "GET", f"/v1/accounts/{principal}/usage", token=boss_token)
    total = 0
    try:
        for owner in body.get("by_key_owner", []):
            total += int(owner.get("calls") or 0)
    except Exception:
        total = -1
    return status, body, total


def setup_tree(ev: Evidence, instance: Instance) -> Dict[str, Any]:
    """构造 boss → workerA/workerB，workerA → workerA1；返回令牌与注册回执。"""
    state: Dict[str, Any] = {"instance": instance}

    status, body, _ = call(
        instance, "POST", "/v1/accounts/register",
        payload={"principal": "boss", "password": TEST_ACCOUNTS["boss"], "display_name": "Boss"},
        headers=proof_headers(instance),
    )
    ev.check(
        "1", "注册主账号", "POST /v1/accounts/register (boss)", "HTTP 200",
        f"HTTP {status}", ok=status == 200, response=body,
    )
    if status != 200:
        raise ApiError(f"无法注册主账号：{brief(body)}")
    state["recovery_code"] = str(body.get("recovery_code") or "")
    ev.check(
        "1", "注册主账号返回恢复码", "POST /v1/accounts/register 响应含 recovery_code",
        "存在且非空", "存在" if state["recovery_code"] else "缺失",
        ok=bool(state["recovery_code"]), response={"recovery_code": "***"},
    )

    boss_token, boss_login = login(instance, "boss", TEST_ACCOUNTS["boss"])
    state["boss_token"] = boss_token
    ev.check(
        "1", "主账号登录", "POST /v1/auth/login (boss)", "HTTP 200", "HTTP 200",
        ok=bool(boss_token), response={"token": "***"},
    )

    # workerA：manager，允许上级借用 Key
    code = create_member(instance, boss_token, "workerA", "manager", allow_key_sharing=True)
    ev.check(
        "1", "创建 workerA", "POST /v1/accounts (boss → manager, allow_key_sharing=true)",
        "返回激活码", "返回激活码" if code else "无激活码", ok=bool(code),
    )
    activate_member(instance, "workerA", code, TEST_ACCOUNTS["workerA"])
    ev.check("1", "激活 workerA", "POST /v1/accounts/activate (workerA)", "HTTP 200", "HTTP 200", ok=True)
    state["workerA_token"], _ = login(instance, "workerA", TEST_ACCOUNTS["workerA"])
    ev.check("1", "登录 workerA", "POST /v1/auth/login (workerA)", "HTTP 200", "HTTP 200", ok=True)

    # workerB：operator，禁止上级借用 Key
    code = create_member(instance, boss_token, "workerB", "operator", allow_key_sharing=False)
    ev.check(
        "1", "创建 workerB", "POST /v1/accounts (boss → operator, allow_key_sharing=false)",
        "返回激活码", "返回激活码" if code else "无激活码", ok=bool(code),
    )
    activate_member(instance, "workerB", code, TEST_ACCOUNTS["workerB"])
    ev.check("1", "激活 workerB", "POST /v1/accounts/activate (workerB)", "HTTP 200", "HTTP 200", ok=True)
    state["workerB_token"], _ = login(instance, "workerB", TEST_ACCOUNTS["workerB"])
    ev.check("1", "登录 workerB", "POST /v1/auth/login (workerB)", "HTTP 200", "HTTP 200", ok=True)

    # workerA1：workerA 的下级
    code = create_member(instance, state["workerA_token"], "workerA1", "viewer")
    ev.check(
        "1", "workerA 创建 workerA1", "POST /v1/accounts (workerA → viewer)",
        "返回激活码", "返回激活码" if code else "无激活码", ok=bool(code),
    )
    activate_member(instance, "workerA1", code, TEST_ACCOUNTS["workerA1"])
    ev.check("1", "激活 workerA1", "POST /v1/accounts/activate (workerA1)", "HTTP 200", "HTTP 200", ok=True)
    state["workerA1_token"], _ = login(instance, "workerA1", TEST_ACCOUNTS["workerA1"])
    ev.check("1", "登录 workerA1", "POST /v1/auth/login (workerA1)", "HTTP 200", "HTTP 200", ok=True)

    # 主账号视图
    status, tree, _ = call(instance, "GET", "/v1/accounts/tree", token=boss_token)
    tree_count = count_tree(tree)
    ev.check(
        "1", "主账号读整棵树", "GET /v1/accounts/tree (boss)", "HTTP 200 且 4 个节点",
        f"HTTP {status} 且 {tree_count} 个节点",
        ok=status == 200 and tree_count == 4, response=tree,
    )

    for principal in ("workerA", "workerB", "workerA1"):
        status, detail, _ = call(instance, "GET", f"/v1/accounts/{principal}", token=boss_token)
        ev.check(
            "1", f"主账号读 {principal} 资料", f"GET /v1/accounts/{principal} (boss)",
            "HTTP 200", f"HTTP {status}", ok=status == 200, response=detail,
        )
        status, usage, _ = call(instance, "GET", f"/v1/accounts/{principal}/usage", token=boss_token)
        ev.check(
            "1", f"主账号读 {principal} 用量", f"GET /v1/accounts/{principal}/usage (boss)",
            "HTTP 200", f"HTTP {status}", ok=status == 200, response=usage,
        )

    status, members, _ = call(instance, "GET", "/v1/accounts", token=boss_token)
    member_count = len(members.get("members", [])) if isinstance(members, dict) else -1
    ev.check(
        "1", "主账号成员列表", "GET /v1/accounts (boss)", "HTTP 200 且 2 个直接成员",
        f"HTTP {status} 且 {member_count} 个",
        ok=status == 200 and member_count == 2, response=members,
    )

    status, events, _ = call(instance, "GET", "/v1/audit/events?limit=20", token=boss_token)
    ev.check(
        "1", "主账号读审计事件", "GET /v1/audit/events?limit=20 (boss)",
        "HTTP 200", f"HTTP {status}", ok=status == 200, response=events,
    )

    state["workerA_login"] = login(instance, "workerA", TEST_ACCOUNTS["workerA"])[1]
    state["budget_tree_count"] = tree_count
    return state


def run_item2(ev: Evidence, instance: Instance, state: Dict[str, Any]) -> None:
    boss_token = state["boss_token"]
    worker_a = state["workerA_token"]
    worker_b = state["workerB_token"]
    worker_a1 = state["workerA1_token"]

    status, body, _ = call(instance, "GET", "/v1/accounts", token=worker_a)
    names = [item.get("principal") for item in body.get("members", [])] if isinstance(body, dict) else []
    ev.check(
        "2", "workerA 成员列表只含直接下级", "GET /v1/accounts (workerA)",
        "HTTP 200 且只有 workerA1", f"HTTP {status} 且 {names}",
        ok=status == 200 and names == ["workerA1"], response=body,
    )

    denied_cases = [
        ("读主账号资料", "GET", "/v1/accounts/boss", worker_a, None),
        ("改主账号权限", "PATCH", "/v1/accounts/boss/permissions", worker_a, {"grant": ["account:audit_read"], "revoke": []}),
        ("读同级 workerB", "GET", "/v1/accounts/workerB", worker_a, None),
        ("改同级 workerB 权限", "PATCH", "/v1/accounts/workerB/permissions", worker_a, {"grant": ["account:audit_read"], "revoke": []}),
        ("workerB 读他人下级 workerA1", "GET", "/v1/accounts/workerA1", worker_b, None),
        ("workerA 读整棵树", "GET", "/v1/accounts/tree", worker_a, None),
        ("workerA1 读父账号", "GET", "/v1/accounts/workerA", worker_a1, None),
    ]
    for name, method, path, token, payload in denied_cases:
        status, body, _ = call(instance, method, path, token=token, payload=payload)
        code = error_code(body)
        ev.check(
            "2", name, f"{method} {path}", "HTTP 404 ACCOUNT_SUBTREE_DENIED",
            f"HTTP {status} {code}",
            ok=status == 404 and code == "ACCOUNT_SUBTREE_DENIED", response=body,
        )

    status, body, _ = call(
        instance, "PATCH", "/v1/accounts/workerA1/permissions",
        token=worker_a,
        payload={"grant": ["account:grant"], "revoke": []},
    )
    ev.check(
        "2", "workerA 改直接下级权限", "PATCH /v1/accounts/workerA1/permissions (workerA)",
        "HTTP 200", f"HTTP {status}", ok=status == 200, response=body,
    )

    status, body, _ = call(instance, "GET", "/v1/auth/me", token=worker_a1)
    code = error_code(body)
    ev.check(
        "2", "权限变更使旧令牌立即失效", "GET /v1/auth/me (workerA1 旧令牌)",
        "HTTP 401 CREDENTIAL_STALE", f"HTTP {status} {code}",
        ok=status == 401 and code == "CREDENTIAL_STALE", response=body,
    )
    new_token, _ = login(instance, "workerA1", TEST_ACCOUNTS["workerA1"])
    state["workerA1_token"] = new_token
    ev.check(
        "2", "失效后重新登录可用", "POST /v1/auth/login (workerA1 新会话)",
        "HTTP 200", "HTTP 200", ok=bool(new_token), response={"token": "***"},
    )


def run_extra_b1(ev: Evidence, instance: Instance, state: Dict[str, Any]) -> None:
    """B1：成员被降权后，旧会话必须在下一次请求就失效。"""
    boss_token = state["boss_token"]
    worker_a = state.get("workerA_token") or ""
    if not worker_a:
        return

    status, body, _ = call(instance, "GET", "/v1/accounts/workerA1", token=worker_a)
    ev.check(
        "B1", "降权前旧令牌可读自己的下级", "GET /v1/accounts/workerA1 (workerA)",
        "HTTP 200", f"HTTP {status}", ok=status == 200, response=body,
    )

    status, body, _ = call(
        instance, "PATCH", "/v1/accounts/workerA/permissions",
        token=boss_token,
        payload={"grant": [], "revoke": ["account:manage_children"]},
    )
    ev.check(
        "B1", "主账号收回 workerA 的子账号管理权限",
        "PATCH /v1/accounts/workerA/permissions {revoke:[account:manage_children]} (boss)",
        "HTTP 200", f"HTTP {status}", ok=status == 200, response=body,
    )

    status, body, _ = call(instance, "GET", "/v1/accounts/workerA1", token=worker_a)
    code = error_code(body)
    ev.check(
        "B1", "降权后旧会话立即失效（下一次请求就被拒）",
        "GET /v1/accounts/workerA1 (降权前签发的 workerA 旧令牌)",
        "HTTP 401 CREDENTIAL_STALE", f"HTTP {status} {code}",
        ok=status == 401 and code == "CREDENTIAL_STALE", response=body,
    )

    new_token, _ = login(instance, "workerA", TEST_ACCOUNTS["workerA"])
    ev.check(
        "B1", "降权后重新登录可拿到新会话", "POST /v1/auth/login (workerA)",
        "HTTP 200", "HTTP 200", ok=bool(new_token), response={"token": "***"},
    )
    if new_token:
        status, body, _ = call(
            instance, "POST", "/v1/accounts",
            token=new_token,
            payload={"principal": "workerA2", "display_name": "Worker A2"},
        )
        code = error_code(body)
        ev.check(
            "B1", "降权后新会话也不能再创建下级",
            "POST /v1/accounts (workerA 新令牌)",
            "HTTP 404 ACCOUNT_SUBTREE_DENIED", f"HTTP {status} {code}",
            ok=status == 404 and code == "ACCOUNT_SUBTREE_DENIED", response=body,
        )
        state["workerA_token"] = new_token

    # 还原权限并换回正常会话，避免影响后续导出 / 界面检查
    status, body, _ = call(
        instance, "PATCH", "/v1/accounts/workerA/permissions",
        token=boss_token,
        payload={"grant": ["account:manage_children"], "revoke": []},
    )
    ev.check(
        "B1", "还原 workerA 的子账号管理权限",
        "PATCH /v1/accounts/workerA/permissions {grant:[account:manage_children]} (boss)",
        "HTTP 200", f"HTTP {status}", ok=status == 200, response=body,
    )
    restored, _ = login(instance, "workerA", TEST_ACCOUNTS["workerA"])
    if restored:
        state["workerA_token"] = restored


def run_item3(ev: Evidence, instance: Instance, state: Dict[str, Any]) -> None:
    boss_token = state["boss_token"]
    worker_a = state["workerA_token"]
    worker_b = state["workerB_token"]

    boss_model = create_mock_key(instance, boss_token, "audit-key-boss")
    worker_a_model = create_mock_key(instance, worker_a, "audit-key-workerA")
    worker_b_model = create_mock_key(instance, worker_b, "audit-key-workerB")
    state["boss_model"] = boss_model
    state["worker_a_model"] = worker_a_model
    state["worker_b_model"] = worker_b_model
    ev.check(
        "3", "三个账号各自创建 Key",
        "POST /v1/models ×3（boss / workerA / workerB）",
        "三把 Key 都有 id", "三把 Key 都有 id" if all((boss_model, worker_a_model, worker_b_model)) else "有缺失",
        ok=all((boss_model, worker_a_model, worker_b_model)),
        response={"boss": bool(boss_model), "workerA": bool(worker_a_model), "workerB": bool(worker_b_model)},
    )

    cases = [
        ("主账号用自己 Key", boss_token, boss_model, "workerA-session"),
        ("主账号用 workerA 已共享 Key", boss_token, worker_a_model, "workerA-session"),
        ("workerA 用自己 Key", worker_a, worker_a_model, "workerA-session"),
    ]
    expected_turns = 0
    for name, token, model_id, session_id in cases:
        status, body = chat(instance, token, model_id, f"case:{name}", session_id)
        if status == 200:
            expected_turns += 2  # 一问一答两轮，导出清单按轮次计数
        ev.check(
            "3", name, f"POST /v1/chat (model_id={model_id})",
            "HTTP 200", f"HTTP {status}", ok=status == 200, response=body,
        )
    state["expected_turns"] = expected_turns

    status, body = chat(instance, worker_b, worker_a_model, "cross:workerB→workerA", "workerB-session")
    code = error_code(body)
    ev.check(
        "3", "workerB 不能用 workerA 的 Key",
        "POST /v1/chat (workerB token + workerA model)",
        "HTTP 403 ACCOUNT_KEY_NOT_OWNED", f"HTTP {status} {code}",
        ok=status == 403 and code == "ACCOUNT_KEY_NOT_OWNED", response=body,
    )

    status, body = chat(instance, worker_a, boss_model, "cross:workerA→boss", "workerA-session")
    code = error_code(body)
    ev.check(
        "3", "workerA 不能用主账号的 Key",
        "POST /v1/chat (workerA token + boss model)",
        "HTTP 403 ACCOUNT_KEY_NOT_OWNED", f"HTTP {status} {code}",
        ok=status == 403 and code == "ACCOUNT_KEY_NOT_OWNED", response=body,
    )

    status, detail_a, _ = call(instance, "GET", "/v1/accounts/workerA", token=boss_token)
    status_b, detail_b, _ = call(instance, "GET", "/v1/accounts/workerB", token=boss_token)
    account_a = detail_a.get("account", detail_a) if isinstance(detail_a, dict) else {}
    account_b = detail_b.get("account", detail_b) if isinstance(detail_b, dict) else {}
    ev.check(
        "3", "workerA 的 Key 共享为开启", "GET /v1/accounts/workerA (boss)",
        "allow_superior_key_use=true",
        f"allow_superior_key_use={account_a.get('allow_superior_key_use')}",
        ok=account_a.get("allow_superior_key_use") is True,
        response=detail_a,
    )
    ev.check(
        "3", "workerB 的 Key 共享为关闭", "GET /v1/accounts/workerB (boss)",
        "allow_superior_key_use=false",
        f"allow_superior_key_use={account_b.get('allow_superior_key_use')}",
        ok=account_b.get("allow_superior_key_use") is False,
        response=detail_b,
    )

    status, models, _ = call(instance, "GET", "/v1/models", token=worker_b)
    ids = [item.get("id") for item in models.get("models", [])] if isinstance(models, dict) else []
    ev.check(
        "3", "workerB 的模型列表不含主账号 Key", "GET /v1/models (workerB)",
        "列表不含 boss 的 model_id", "不含" if boss_model not in ids else "包含",
        ok=boss_model not in ids, response={"model_ids": ids},
    )

    # F1：未共享的 workerB Key 不应被主账号借用
    status, body = chat(instance, boss_token, worker_b_model, "cross:boss→workerB(unshared)", "boss-try-b")
    code = error_code(body)
    ev.check(
        "3", "主账号不能用 workerB 未共享的 Key",
        "POST /v1/chat (boss token + workerB model, allow_superior_key_use=false)",
        "HTTP 403 ACCOUNT_KEY_CONSENT_REQUIRED", f"HTTP {status} {code}",
        ok=status == 403, response=body,
        note="这是附加发现 F1：显式 model_id 的解析路径缺少 Key 授权门。",
    )
    status, usage, calls = usage_calls(instance, boss_token, "workerB")
    ev.check(
        "3", "未共享 Key 的用量账本不应被主账号写入",
        "GET /v1/accounts/workerB/usage (boss) 的 by_key_owner",
        "workerB 名下的调用次数为 0",
        f"calls={calls}",
        ok=status == 200 and calls == 0, response=usage,
        note="用量账本可证明 Key 是否真的被使用，比模型回复文本更可靠。",
    )

    status, usage, calls_a = usage_calls(instance, boss_token, "workerA")
    ev.check(
        "3", "共享 Key 的用量记在 workerA 名下",
        "GET /v1/accounts/workerA/usage (boss)",
        "workerA 名下存在调用记录",
        f"calls={calls_a}" if status == 200 else f"HTTP {status}",
        ok=status == 200 and calls_a > 0, response=usage,
    )


def run_item4(ev: Evidence, instance: Instance, state: Dict[str, Any]) -> None:
    boss_token = state["boss_token"]
    worker_a = state["workerA_token"]
    worker_b = state["workerB_token"]
    worker_a1 = state["workerA1_token"]

    texts = {
        "boss": "BOSS-MEM-MARKER",
        "workerA": "A-MEM-MARKER",
        "workerB": "B-MEM-MARKER",
        "workerA1": "A1-MEM-MARKER",
    }
    ids: Dict[str, str] = {}
    for principal, token in (
        ("boss", boss_token), ("workerA", worker_a), ("workerB", worker_b), ("workerA1", worker_a1),
    ):
        status, body, _ = write_memory(instance, token, texts[principal])
        try:
            ids[principal] = str(body.get("entry", {}).get("id") or "")
        except Exception:
            ids[principal] = ""
        ev.check(
            "4", f"{principal} 写自己的记忆", f"POST /v1/memory/entries ({principal})",
            "HTTP 201", f"HTTP {status}",
            ok=status == 201 and bool(ids.get(principal)), response={"entry": {"id": ids.get(principal)}},
        )

    status, body, _ = call(instance, "GET", "/v1/memory/entries?scope=self", token=worker_a)
    entries = body.get("entries", []) if isinstance(body, dict) else []
    ev.check(
        "4", "workerA self 只看到自己的记忆",
        "GET /v1/memory/entries?scope=self (workerA)",
        "HTTP 200 且 count=1 且 owner=workerA",
        f"HTTP {status} 且 count={len(entries)} 且 owners={[e.get('account_id') for e in entries]}",
        ok=status == 200 and len(entries) == 1 and all(e.get("account_id") == "workerA" for e in entries),
        response=body,
    )

    status, body, _ = call(
        instance, "GET", f"/v1/memory/entries/{ids['workerA']}", token=worker_b
    )
    code = error_code(body)
    ev.check(
        "4", "workerB 读 workerA 记忆被拒",
        "GET /v1/memory/entries/{workerA_id} (workerB)",
        "HTTP 404 ACCOUNT_MEMORY_FOREIGN_DENIED", f"HTTP {status} {code}",
        ok=status == 404 and code == "ACCOUNT_MEMORY_FOREIGN_DENIED", response=body,
    )

    status, body, _ = call(instance, "GET", "/v1/memory/entries?scope=subtree", token=worker_a)
    code = error_code(body)
    ev.check(
        "4", "workerA 不能请求整棵子树",
        "GET /v1/memory/entries?scope=subtree (workerA)",
        "HTTP 404 ACCOUNT_SUBTREE_DENIED", f"HTTP {status} {code}",
        ok=status == 404 and code == "ACCOUNT_SUBTREE_DENIED", response=body,
    )

    status, body, _ = call(instance, "GET", "/v1/memory/entries?scope=subtree", token=boss_token)
    entries = body.get("entries", []) if isinstance(body, dict) else []
    owners = sorted({str(entry.get("account_id") or "") for entry in entries})
    ev.check(
        "4", "主账号 subtree 看到四个账号的记忆",
        "GET /v1/memory/entries?scope=subtree (boss)",
        "HTTP 200 且 4 条且 owners 覆盖四个账号",
        f"HTTP {status} 且 {len(entries)} 条且 owners={owners}",
        ok=status == 200 and len(entries) == 4 and owners == ["boss", "workerA", "workerA1", "workerB"],
        response=body,
    )

    status, body, _ = call(
        instance, "GET", f"/v1/memory/entries/{ids['workerA']}", token=boss_token
    )
    ev.check(
        "4", "主账号读子账号单条记忆", "GET /v1/memory/entries/{workerA_id} (boss)",
        "HTTP 200 且 account_id=workerA", f"HTTP {status}",
        ok=status == 200, response=body,
    )

    status, body, _ = call(
        instance, "GET", "/v1/memory/entries?scope=subtree&q=A-MEM-MARKER", token=boss_token
    )
    count = body.get("count") if isinstance(body, dict) else 0
    ev.check(
        "4", "主账号按关键词检索子账号记忆",
        "GET /v1/memory/entries?scope=subtree&q=A-MEM-MARKER (boss)",
        "HTTP 200 且命中 ≥1", f"HTTP {status} 且 count={count}",
        ok=status == 200 and int(count or 0) >= 1, response=body,
    )


def run_item5(ev: Evidence, instance: Instance, state: Dict[str, Any]) -> None:
    worker_a = state["workerA_token"]

    status, me, _ = call(instance, "GET", "/v1/auth/me", token=worker_a)
    missing = [key for key in AUTH_STRUCT_KEYS if key not in (me or {})]
    leaked = {key: me.get(key) for key in AUTH_STRUCT_KEYS if key in (me or {})}
    ev.check(
        "5", "workerA /auth/me 不暴露创建者与层级",
        "GET /v1/auth/me (workerA)",
        "响应不含 parent_account_id / depth",
        f"缺失 {missing}；泄露 {leaked}" if leaked else "字段均不存在",
        ok=status == 200 and not leaked, response=me,
        note="浏览器登录后本来就会调用 /v1/auth/me，所以这等于把创建者交给子账号。",
    )

    login_body = state.get("workerA_login") or {}
    leaked_login = {key: login_body.get(key) for key in AUTH_STRUCT_KEYS if key in login_body}
    ev.check(
        "5", "workerA 登录响应不暴露创建者与层级",
        "POST /v1/auth/login (workerA)",
        "响应不含 parent_account_id / depth",
        f"泄露 {leaked_login}" if leaked_login else "字段均不存在",
        ok=not leaked_login, response={"leaked": leaked_login},
    )

    status, members, _ = call(instance, "GET", "/v1/accounts", token=worker_a)
    text = brief(members)
    hit = [term for term in ("parent_account_id", "depth", "/boss/") if term in text]
    ev.check(
        "5", "workerA 的成员列表不含结构字段",
        "GET /v1/accounts (workerA)",
        "响应不含 parent_account_id / depth / /boss/",
        f"命中 {hit}" if hit else "无命中",
        ok=status == 200 and not hit, response=members,
    )

    dist = REPO / "apps" / "console" / "console" / "dist"
    hits: List[str] = []
    if dist.exists():
        for asset in dist.rglob("*.js"):
            try:
                source = asset.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for term in ("由谁创建", "创建了你", "你由", "创建者", "创建人"):
                if term in source:
                    hits.append(f"{asset.name}:{term}")
    ev.check(
        "5", "控制台构建包不含“由谁创建”类文案",
        f"扫描 {dist}/*.js",
        "0 命中", f"{len(hits)} 命中 {hits[:5]}",
        ok=not hits,
        note="只证明文案未打包，不证明接口不泄露。",
    )


def run_export(ev: Evidence, instance: Instance, state: Dict[str, Any]) -> Dict[str, Any]:
    boss_token = state["boss_token"]
    worker_a = state["workerA_token"]

    note = instance.workspace / "accounts" / "boss" / "audit-note.txt"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("AUDIT-FILE-MARKER-20261108\n", encoding="utf-8")

    status, files, _ = call(instance, "GET", "/v1/files?account=boss", token=boss_token)
    names = [item.get("name") for item in files.get("entries", [])] if isinstance(files, dict) else []
    ev.check(
        "7", "主账号能看到自己的文件", "GET /v1/files?account=boss (boss)",
        "HTTP 200 且包含 audit-note.txt", f"HTTP {status} 且 {names}",
        ok=status == 200 and "audit-note.txt" in names, response=files,
    )

    status, exported, _ = call(
        instance, "POST", "/v1/accounts/backup/export",
        token=boss_token,
        payload={"scope": "all", "passphrase": BUNDLE_PASSPHRASE},
        headers=proof_headers(instance),
    )
    manifest = exported.get("manifest", {}) if isinstance(exported, dict) else {}
    stores = manifest.get("stores", {}) if isinstance(manifest, dict) else {}
    memory_entries = int((stores.get("account_memory") or {}).get("entries") or 0)
    conversation_turns = int((stores.get("conversation") or {}).get("turns") or 0)
    members = int(manifest.get("member_count") or 0)
    file_count = int(manifest.get("files") or 0)
    byte_count = int(manifest.get("bytes") or 0)
    expected_turns = int(state.get("expected_turns") or 0)
    ev.check(
        "7", "主账号全量导出", "POST /v1/accounts/backup/export (scope=all)",
        f"HTTP 200 且 4 个成员 / 4 条记忆 / {expected_turns} 轮会话 / ≥1 文件 / 字节数>0",
        f"HTTP {status} members={members} memory={memory_entries} turns={conversation_turns} "
        f"files={file_count} bytes={byte_count}",
        ok=status == 200 and members == 4 and memory_entries == 4
        and conversation_turns == expected_turns and file_count >= 1 and byte_count > 0,
        response={"manifest": manifest},
    )
    ev.check(
        "7", "导出包携带成员 Key 密文、不携带主密钥",
        "POST /v1/accounts/backup/export 响应",
        "member_keys_included=true 且 key_material_included=false",
        f"member_keys_included={exported.get('member_keys_included')} "
        f"key_material_included={exported.get('key_material_included')}",
        ok=exported.get("member_keys_included") is True and exported.get("key_material_included") is False,
        response={
            "member_keys_included": exported.get("member_keys_included"),
            "key_material_included": exported.get("key_material_included"),
            "key_scope": manifest.get("key_scope"),
        },
        note="按设计：成员 Key 只以口令保险箱密文随包，换机后可用；主密钥不出机，口令只以哈希随加密包迁移。",
    )

    status, body, _ = call(
        instance, "POST", "/v1/accounts/backup/export",
        token=worker_a,
        payload={"scope": "all", "passphrase": BUNDLE_PASSPHRASE},
        headers=proof_headers(instance),
    )
    code = error_code(body)
    ev.check(
        "7", "子账号不能做全量导出", "POST /v1/accounts/backup/export (workerA)",
        "HTTP 403 ACCOUNT_SCOPE_DENIED", f"HTTP {status} {code}",
        ok=status == 403, response=body,
    )

    state["exported"] = exported
    return exported


def run_item6_and_import(
    ev: Evidence,
    instance_b: Instance,
    state: Dict[str, Any],
) -> None:
    exported = state.get("exported") or {}
    boss_token_a: str = state["boss_token"]

    status, imported, _ = call(
        instance_b, "POST", "/v1/accounts/backup/import",
        payload={"bundle": exported.get("bundle"), "passphrase": BUNDLE_PASSPHRASE},
        headers=proof_headers(instance_b),
    )
    ev.check(
        "7", "新机导入全量包", "POST /v1/accounts/backup/import (fresh instance B)",
        "HTTP 200", f"HTTP {status}", ok=status == 200, response=imported,
    )
    if status != 200:
        raise ApiError(f"备份导入失败：{brief(imported)}")

    accounts = int(imported.get("accounts") or 0)
    memory = int((imported.get("memory") or {}).get("imported") or 0)
    conversations = int((imported.get("conversations") or {}).get("imported") or 0)
    expected_turns = int(state.get("expected_turns") or 0)
    ev.check(
        "7", "导入后账号/记忆/会话数量正确", "导入响应",
        f"accounts=4 且 memory=4 且 conversations={expected_turns}",
        f"accounts={accounts} memory={memory} conversations={conversations}",
        ok=accounts == 4 and memory == 4 and conversations == expected_turns, response=imported,
    )

    # 凭据记录（PBKDF2 哈希）随加密包迁移：新机导入后原密码即可登录。
    status, body, _ = call(
        instance_b, "POST", "/v1/auth/login",
        payload={"principal": "boss", "secret": TEST_ACCOUNTS["boss"], "client": "web"},
    )
    boss_b = str(body.get("token") or body.get("access_token") or "") if isinstance(body, dict) else ""
    ev.check(
        "6", "导入后主账号原密码可登录", "POST /v1/auth/login (boss 原密码 @ B)",
        "HTTP 200", f"HTTP {status}", ok=status == 200 and bool(boss_b), response={"token": "***"},
        note="凭据哈希随加密包迁移，导入后无需重置即可登录。",
    )

    status, body, _ = call(instance_b, "GET", "/v1/auth/me", token=boss_token_a)
    ev.check(
        "6", "A 机令牌不能在 B 机使用", "GET /v1/auth/me (A 机 boss 令牌 @ B)",
        "HTTP 401", f"HTTP {status} {error_code(body)}", ok=status == 401, response=body,
    )

    # 恢复码：B 机用 A 机注册时拿到的恢复码重置主账号
    recovery_code = state.get("recovery_code") or ""
    status, reset, _ = call(
        instance_b, "POST", "/v1/accounts/recovery/reset",
        payload={"recovery_code": recovery_code, "password": RECOVERED_BOSS_PASSWORD},
        headers=proof_headers(instance_b, reset=True),
    )
    ev.check(
        "6", "B 机用 A 机恢复码重置主账号密码",
        "POST /v1/accounts/recovery/reset (recovery_code from A)",
        "HTTP 200", f"HTTP {status}", ok=status == 200, response=reset,
    )

    status, body, _ = call(
        instance_b, "POST", "/v1/auth/login",
        payload={"principal": "boss", "secret": RECOVERED_BOSS_PASSWORD, "client": "web"},
    )
    new_token = str(body.get("token") or body.get("access_token") or "") if isinstance(body, dict) else ""
    ev.check(
        "6", "重置后新密码可登录", "POST /v1/auth/login (boss 新密码 @ B)",
        "HTTP 200", f"HTTP {status}", ok=status == 200 and bool(new_token), response={"token": "***"},
    )

    status, body, _ = call(
        instance_b, "POST", "/v1/auth/login",
        payload={"principal": "boss", "secret": TEST_ACCOUNTS["boss"], "client": "web"},
    )
    ev.check(
        "6", "重置后旧密码失效", "POST /v1/auth/login (boss 旧密码 @ B)",
        "HTTP 401", f"HTTP {status}", ok=status == 401, response=body,
    )

    if boss_b:
        status, body, _ = call(instance_b, "GET", "/v1/auth/me", token=boss_b)
        ev.check(
            "6", "重置后旧令牌失效", "GET /v1/auth/me (boss 重置前令牌 @ B)",
            "HTTP 401 CREDENTIAL_STALE", f"HTTP {status} {error_code(body)}",
            ok=status == 401 and error_code(body) == "CREDENTIAL_STALE", response=body,
        )

    if new_token:
        status, tree_b2, _ = call(instance_b, "GET", "/v1/accounts/tree", token=new_token)
        count2 = count_tree(tree_b2)
        ev.check(
            "6", "重置后账号树仍完整", "GET /v1/accounts/tree (boss 新令牌 @ B)",
            "HTTP 200 且 4 个节点", f"HTTP {status} 且 {count2} 个节点",
            ok=status == 200 and count2 == 4, response=tree_b2,
        )

    # 凭据随包迁移：成员也应能用原密码直接登录，再验证数据隔离。
    worker_a_b = worker_b_b = ""
    for principal, password in (
        ("workerA", TEST_ACCOUNTS["workerA"]),
        ("workerB", TEST_ACCOUNTS["workerB"]),
    ):
        login_status, login_body, _ = call(
            instance_b, "POST", "/v1/auth/login",
            payload={"principal": principal, "secret": password, "client": "web"},
        )
        member_token = (
            str(login_body.get("token") or login_body.get("access_token") or "")
            if isinstance(login_body, dict) else ""
        )
        if principal == "workerA":
            worker_a_b = member_token
        else:
            worker_b_b = member_token
        ev.check(
            "7", f"导入后 {principal} 原密码可登录",
            f"POST /v1/auth/login ({principal} @ B)",
            "HTTP 200 且有令牌", f"HTTP {login_status}",
            ok=login_status == 200 and bool(member_token), response={"token": "***"},
        )

    status, files, _ = call(instance_b, "GET", "/v1/files?account=boss", token=new_token)
    names = [item.get("name") for item in files.get("entries", [])] if isinstance(files, dict) else []
    ev.check(
        "7", "导入后文件仍在", "GET /v1/files?account=boss (boss @ B)",
        "包含 audit-note.txt", f"{names}",
        ok=status == 200 and "audit-note.txt" in names, response=files,
    )

    status, content, _ = call(
        instance_b, "GET", "/v1/files/content?account=boss&path=accounts/boss/audit-note.txt",
        token=new_token,
    )
    text = json.dumps(content, ensure_ascii=False) if isinstance(content, dict) else str(content)
    ev.check(
        "7", "导入后文件内容可读", "GET /v1/files/content?path=accounts/boss/audit-note.txt (boss @ B)",
        "HTTP 200 且包含 AUDIT-FILE-MARKER-20261108",
        f"HTTP {status} 且 {'包含' if 'AUDIT-FILE-MARKER-20261108' in text else '不包含'}",
        ok=status == 200 and "AUDIT-FILE-MARKER-20261108" in text, response=content,
    )

    status, memory_list, _ = call(
        instance_b, "GET", "/v1/memory/entries?scope=subtree&q=A-MEM-MARKER", token=new_token
    )
    count = memory_list.get("count") if isinstance(memory_list, dict) else 0
    ev.check(
        "7", "导入后记忆可检索", "GET /v1/memory/entries?scope=subtree&q=A-MEM-MARKER (boss @ B)",
        "HTTP 200 且命中 ≥1", f"HTTP {status} 且 count={count}",
        ok=status == 200 and int(count or 0) >= 1, response=memory_list,
    )

    memory_id = state.get("worker_a_memory_id") or ""
    if memory_id and worker_b_b:
        status, body, _ = call(
            instance_b, "GET", f"/v1/memory/entries/{memory_id}", token=worker_b_b
        )
        ev.check(
            "7", "导入后 workerB 仍看不到 workerA 记忆",
            "GET /v1/memory/entries/{workerA_id} (workerB @ B)",
            "HTTP 404 ACCOUNT_MEMORY_FOREIGN_DENIED", f"HTTP {status} {error_code(body)}",
            ok=status == 404 and error_code(body) == "ACCOUNT_MEMORY_FOREIGN_DENIED", response=body,
        )

    if worker_a_b:
        status, sessions, _ = call(instance_b, "GET", "/v1/chat/sessions", token=worker_a_b)
        session_names = sessions.get("sessions", []) if isinstance(sessions, dict) else []
        ev.check(
            "7", "导入后会话可被发现", "GET /v1/chat/sessions (workerA @ B)",
            "HTTP 200 且包含 workerA-session", f"HTTP {status} 且 {session_names}",
            ok=status == 200 and "workerA-session" in session_names, response=sessions,
        )

        status, history, _ = call(
            instance_b, "GET", "/v1/chat/history?session_id=workerA-session", token=worker_a_b
        )
        turns = (
            history.get("history") or history.get("turns") or history.get("messages") or []
            if isinstance(history, dict)
            else []
        )
        ev.check(
            "7", "导入后会话内容可读", "GET /v1/chat/history?session_id=workerA-session (workerA @ B)",
            "HTTP 200 且有会话内容", f"HTTP {status} 且有 {len(turns)} 条",
            ok=status == 200 and len(turns) > 0, response=history,
        )

    # ---------------- B2：新数据目录恢复后的八项边界 ----------------
    if new_token:
        status, tree_b3, _ = call(instance_b, "GET", "/v1/accounts/tree", token=new_token)
        count3 = count_tree(tree_b3)
        ev.check(
            "B2", "新数据目录恢复后账号树仍是 4 个节点",
            "GET /v1/accounts/tree (boss @ B)",
            "HTTP 200 且 4 个节点", f"HTTP {status} 且 {count3} 个节点",
            ok=status == 200 and count3 == 4, response=tree_b3,
        )

        boss_model = state.get("boss_model") or ""
        worker_a_model = state.get("worker_a_model") or ""
        worker_b_model = state.get("worker_b_model") or ""
        status, models, _ = call(instance_b, "GET", "/v1/models", token=new_token)
        ids = [item.get("id") for item in models.get("models", [])] if isinstance(models, dict) else []
        ev.check(
            "B2", "Key 归属随包迁移：主账号能看到三把 Key 的归属行",
            "GET /v1/models (boss @ B)",
            "HTTP 200 且三把 Key 归属行都在（boss / workerA / workerB）",
            f"HTTP {status} 且 {ids}",
            ok=status == 200 and boss_model in ids
            and worker_a_model in ids and worker_b_model in ids,
            response={"model_ids": ids},
            note="主账号能看全部（第 1 项），所以整棵子树的 Key 归属行都在；归属是否还成立由下一条"
                 "（workerB 列表不含主账号 Key）与越级拒绝共同证明。",
        )

        if worker_b_b:
            status, models_b, _ = call(instance_b, "GET", "/v1/models", token=worker_b_b)
            ids_b = (
                [item.get("id") for item in models_b.get("models", [])]
                if isinstance(models_b, dict) else []
            )
            ev.check(
                "B2", "Key 归属随包迁移：workerB 的列表仍看不到主账号 Key",
                "GET /v1/models (workerB @ B)",
                "不含主账号 Key", f"{ids_b}",
                ok=status == 200 and boss_model not in ids_b,
                response={"model_ids": ids_b},
            )

        if worker_b_b and boss_model:
            status, body = chat(
                instance_b, worker_b_b, boss_model, "cross:workerB→boss@B", "workerB-session"
            )
            code = error_code(body)
            ev.check(
                "B2", "Key 归属随包迁移：workerB 仍不能用主账号 Key",
                "POST /v1/chat (workerB @ B + boss model)",
                "HTTP 403 ACCOUNT_KEY_NOT_OWNED", f"HTTP {status} {code}",
                ok=status == 403 and code == "ACCOUNT_KEY_NOT_OWNED", response=body,
            )

        status, memory_b, _ = call(
            instance_b, "GET", "/v1/memory/entries?scope=subtree", token=new_token
        )
        entries_b = memory_b.get("entries", []) if isinstance(memory_b, dict) else []
        owners_b = sorted({str(entry.get("account_id") or "") for entry in entries_b})
        ev.check(
            "B2", "记忆归属随包迁移：主账号仍看到四个账号的记忆",
            "GET /v1/memory/entries?scope=subtree (boss @ B)",
            "HTTP 200 且 4 条且 owners 覆盖四个账号",
            f"HTTP {status} 且 {len(entries_b)} 条且 owners={owners_b}",
            ok=status == 200
            and len(entries_b) == 4
            and owners_b == ["boss", "workerA", "workerA1", "workerB"],
            response=memory_b,
        )

    status, body, _ = call(
        instance_b, "POST", "/v1/accounts/recovery/reset",
        payload={"recovery_code": recovery_code, "password": "Reuse-Pass-2026"},
        headers=proof_headers(instance_b, reset=True),
    )
    ev.check(
        "6", "恢复码不能重复使用",
        "POST /v1/accounts/recovery/reset（同一个恢复码第二次）",
        "HTTP 400 ACCOUNT_RECOVERY_CODE_INVALID", f"HTTP {status} {error_code(body)}",
        ok=status == 400 and error_code(body) == "ACCOUNT_RECOVERY_CODE_INVALID", response=body,
    )


def run_item8_api(ev: Evidence, instance: Instance, state: Dict[str, Any]) -> None:
    boss_token = state["boss_token"]

    status, config, _ = call(instance, "GET", "/v1/accounts/self-registration", token=boss_token)
    ev.check(
        "8", "主账号读自助注册开关", "GET /v1/accounts/self-registration (boss)",
        "HTTP 200", f"HTTP {status}", ok=status == 200, response=config,
    )

    status, body, _ = call(
        instance, "POST", "/v1/accounts/self-register",
        payload={"principal": "newbie-api", "password": TEST_ACCOUNTS["newbie-api"], "client": "web"},
    )
    newbie_token = str(body.get("token") or body.get("access_token") or "") if isinstance(body, dict) else ""
    ev.check(
        "8", "自助注册接口免邀请码可用", "POST /v1/accounts/self-register (password 字段)",
        "HTTP 200 且返回令牌", f"HTTP {status} 且 {'有令牌' if newbie_token else '无令牌'}",
        ok=status == 200 and bool(newbie_token), response={"account": (body or {}).get("account")},
    )

    if newbie_token:
        status, me, _ = call(instance, "GET", "/v1/auth/me", token=newbie_token)
        is_newbie = isinstance(me, dict) and me.get("principal") == "newbie-api"
        ev.check(
            "8", "自助账号可立即登录", "GET /v1/auth/me (newbie-api 新令牌)",
            "HTTP 200 且 principal=newbie-api",
            f"HTTP {status} 且 principal={me.get('principal') if isinstance(me, dict) else '?'}",
            ok=status == 200 and is_newbie, response=me,
        )

    if newbie_token:
        status, body, _ = call(instance, "GET", "/v1/accounts", token=newbie_token)
        code = error_code(body)
        members = body.get("members", []) if isinstance(body, dict) else []
        ev.check(
            "8", "自助账号默认不能建下级", "GET /v1/accounts (newbie-api)",
            "HTTP 200 且 0 个成员（或 HTTP 404 拒绝）",
            f"HTTP {status} {code} 且 {len(members)} 个成员",
            ok=(status == 200 and not members) or status == 404, response=body,
        )

    status, body, _ = call(
        instance, "PATCH", "/v1/accounts/self-registration",
        token=boss_token, payload={"allow": False},
    )
    ev.check(
        "8", "主账号可关闭自助注册", "PATCH /v1/accounts/self-registration {allow:false}",
        "HTTP 200", f"HTTP {status}", ok=status == 200, response=body,
    )

    status, body, _ = call(
        instance, "POST", "/v1/accounts/self-register",
        payload={"principal": "newbie-closed", "password": "Closed-Pass-2026", "client": "web"},
    )
    ev.check(
        "8", "关闭后自助注册被拒", "POST /v1/accounts/self-register（开关关闭）",
        "HTTP 403 ACCOUNT_SELF_REGISTER_DISABLED", f"HTTP {status} {error_code(body)}",
        ok=status == 403 and error_code(body) == "ACCOUNT_SELF_REGISTER_DISABLED", response=body,
    )

    status, body, _ = call(
        instance, "PATCH", "/v1/accounts/self-registration",
        token=boss_token, payload={"allow": True},
    )
    ev.check(
        "8", "主账号可重新开启自助注册", "PATCH /v1/accounts/self-registration {allow:true}",
        "HTTP 200", f"HTTP {status}", ok=status == 200, response=body,
    )

    status, body, _ = call(
        instance, "POST", "/v1/accounts/self-register",
        payload={"principal": "newbie-ui-api-shape", "password": "Shape-Pass-2026", "client": "web"},
    )
    ev.check(
        "8", "控制台当前载荷（password）能注册成功",
        "POST /v1/accounts/self-register {principal, password, client}",
        "HTTP 200", f"HTTP {status} {error_code(body)}",
        ok=status == 200, response=body,
        note="控制台 registerSelf() 与后端 SelfRegisterRequest 都使用 password；422 即字段不匹配回归。",
    )

    status, tree, _ = call(instance, "GET", "/v1/accounts/tree", token=boss_token)
    text = json.dumps(tree, ensure_ascii=False) if isinstance(tree, dict) else ""
    ev.check(
        "8", "自助注册账号进入主账号树", "GET /v1/accounts/tree (boss)",
        "包含 newbie-api", "包含" if "newbie-api" in text else "不包含",
        ok="newbie-api" in text, response={"tree_principals": sorted(set(re.findall(r'"principal":\s*"([^"]+)"', text)))},
    )


# --------------------------------------------------------------------------- #
# 界面层验收
# --------------------------------------------------------------------------- #

def _storage_snapshot(page) -> Dict[str, Any]:
    return page.evaluate(
        """() => {
            const local = {}; const session = {};
            for (let i = 0; i < localStorage.length; i++) {
                const k = localStorage.key(i); local[k] = localStorage.getItem(k);
            }
            for (let i = 0; i < sessionStorage.length; i++) {
                const k = sessionStorage.key(i); session[k] = sessionStorage.getItem(k);
            }
            return {local, session};
        }"""
    )


def _decode_jwt(token: str) -> Dict[str, Any]:
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        segment = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(segment))
    except Exception:
        return {}


def _scan_network(entries: List[Dict[str, Any]]) -> List[str]:
    hits: List[str] = []
    for entry in entries:
        url = entry["url"]
        body = entry.get("body")
        text = json.dumps(body, ensure_ascii=False) if body is not None else ""
        if "/v1/auth/" in url:
            for key in AUTH_STRUCT_KEYS:
                if f'"{key}"' in text:
                    hits.append(f"{url}::{key}")
            if ACCOUNT_PATH_RE.search(text):
                hits.append(f"{url}::account-path")
        elif re.search(r'"[^"]*"\s*:\s*"/boss/[^"]*"', text):
            hits.append(f"{url}::/boss/")
    return sorted(set(hits))


def _login_through_ui(page, base: str, principal: str, password: str, timeout: int = 30000) -> None:
    page.goto(base + "/", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_selector("#os-principal", timeout=timeout)
    page.fill("#os-principal", principal)
    page.fill("#os-secret", password)
    page.click("form button[type='submit']")
    page.wait_for_function(
        "() => !!window.localStorage.getItem('liuhao.session') "
        "|| !!window.sessionStorage.getItem('liuhao.session.tab')",
        timeout=timeout,
    )


def run_ui_suite(ev: Evidence, data_root: Path, state: Dict[str, Any], port: int) -> None:
    base = f"http://127.0.0.1:{port}"
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        ev.check(
            "5", "界面探针可运行", "import playwright",
            "Playwright 可用", f"不可用：{exc}", ok=False,
            note="请用 .venv\\Scripts\\python.exe 运行本脚本。",
        )
        return

    shots = data_root / "ui"
    shots.mkdir(parents=True, exist_ok=True)
    captured: List[Dict[str, Any]] = []

    with sync_playwright() as pw:
        launch_kwargs: Dict[str, Any] = {"headless": True, "args": ["--no-sandbox"]}
        try:
            browser = pw.chromium.launch(**launch_kwargs)
        except Exception:
            chrome = Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe"
            browser = pw.chromium.launch(executable_path=str(chrome), **launch_kwargs)

        context = browser.new_context(viewport={"width": 1366, "height": 900})
        page = context.new_page()

        def on_response(response) -> None:
            try:
                url = response.url
                if not ("/v1/auth/" in url or "/v1/accounts" in url):
                    return
                content_type = response.headers.get("content-type") or ""
                body = response.json() if "json" in content_type else None
                captured.append({"url": url.replace("http://127.0.0.1:19008", ""), "status": response.status, "body": body})
            except Exception:
                pass

        page.on("response", on_response)

        try:
            _login_through_ui(page, base, "workerA", TEST_ACCOUNTS["workerA"])
            page.wait_for_timeout(3000)
            page.screenshot(path=str(shots / "workerA-dashboard.png"), full_page=True)
        except Exception as exc:
            ev.check(
                "5", "子账号可通过界面登录", "浏览器打开 http://127.0.0.1:19008 并以 workerA 登录",
                "登录成功并进入主界面", f"失败：{exc}", ok=False,
                note="界面层失败会阻断后续 DOM/存储扫描。",
            )
            context.close()
            browser.close()
            return

        visible = page.locator("body").inner_text()
        dom = page.content()
        storage = _storage_snapshot(page)
        storage_text = json.dumps(storage, ensure_ascii=False)

        term_hits = [term for term in UI_FORBIDDEN_TERMS if term in visible]
        ev.check(
            "5", "界面可见文本不含层级/创建者字眼",
            "浏览器 workerA 登录后读取 body.innerText",
            "0 命中", f"{len(term_hits)} 命中 {term_hits}", ok=not term_hits,
        )

        dom_hits = [term for term in UI_FORBIDDEN_TERMS if term in dom]
        ev.check(
            "5", "界面 DOM 不含层级/创建者字眼",
            "浏览器 workerA 登录后读取 document HTML",
            "0 命中", f"{len(dom_hits)} 命中 {dom_hits}", ok=not dom_hits,
        )

        def storage_struct_hits(snapshot: Dict[str, Any]) -> List[str]:
            hits: List[str] = []

            def walk(value: Any, where: str) -> None:
                if isinstance(value, dict):
                    for key, item in value.items():
                        if key in AUTH_STRUCT_KEYS and item not in (None, 0, "", False):
                            hits.append(f"{where}.{key}={item}")
                        walk(item, f"{where}.{key}")
                elif isinstance(value, list):
                    for index, item in enumerate(value):
                        walk(item, f"{where}[{index}]")

            for bucket_name in ("local", "session"):
                for key, raw in (snapshot.get(bucket_name) or {}).items():
                    if not isinstance(raw, str):
                        continue
                    try:
                        parsed_value = json.loads(raw)
                    except Exception:
                        continue
                    walk(parsed_value, f"{bucket_name}.{key}")
            return hits

        storage_keys = storage_struct_hits(storage)
        storage_preview = {}
        try:
            session_blob = storage["local"].get("liuhao.session") or storage["session"].get("liuhao.session.tab") or ""
            parsed = json.loads(session_blob) if session_blob else {}
            storage_preview = {
                "parentAccountId": parsed.get("parentAccountId"),
                "depth": parsed.get("depth"),
            }
        except Exception:
            parsed = {}
        ev.check(
            "5", "界面本地存储不含创建者与层级",
            "浏览器读取 localStorage/sessionStorage 中的会话对象",
            "不含 parentAccountId / depth 等结构字段",
            f"命中 {storage_keys}" if storage_keys else f"无命中；实际 {storage_preview}",
            ok=not storage_keys, response=storage_preview,
        )

        jwt = _decode_jwt(str((parsed or {}).get("token") or ""))
        jwt_keys = [key for key in ("parent_account_id", "depth", "path") if key in jwt]
        ev.check(
            "5", "会话令牌载荷不含层级字段",
            "解析 localStorage 中会话的 JWT payload",
            "不含 parent_account_id / depth / path",
            f"命中 {jwt_keys}" if jwt_keys else "无命中",
            ok=not jwt_keys, response={"jwt_keys": list(jwt.keys())},
        )

        net_hits = _scan_network(captured)
        ev.check(
            "5", "界面实际网络响应不含创建者/层级",
            "浏览器捕获 /v1/auth/* 与 /v1/accounts* 响应",
            "0 命中 parent_account_id / depth / /boss/ 路径",
            f"{len(net_hits)} 命中 {net_hits[:6]}", ok=not net_hits,
            response={"captured": [item["url"] for item in captured if "/v1/auth/" in item["url"]]},
        )

        # ---- 自助注册完整界面流程 ---------------------------------------- #
        page.evaluate("() => { localStorage.clear(); sessionStorage.clear(); }")
        page.goto(base + "/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector("#os-login-tab-register", timeout=30000)
        page.click("#os-login-tab-register")
        page.wait_for_selector("#reg-principal", timeout=15000)
        page.fill("#reg-principal", "newbie-ui")
        page.fill("#reg-secret", TEST_ACCOUNTS["newbie-ui"])

        register_requests: List[Dict[str, Any]] = []
        register_responses: List[Dict[str, Any]] = []

        def on_request(request) -> None:
            if "/v1/accounts/self-register" in request.url:
                register_requests.append({"url": request.url, "method": request.method, "post_data": request.post_data})

        def on_register_response(response) -> None:
            if "/v1/accounts/self-register" in response.url:
                try:
                    body = response.json()
                except Exception:
                    body = None
                register_responses.append({"status": response.status, "body": body})

        page.on("request", on_request)
        page.on("response", on_register_response)

        page.locator("#os-login-panel form button[type='submit']").last.click()
        page.wait_for_timeout(4000)

        payload_shape = register_requests[-1]["post_data"] if register_requests else ""
        register_status = register_responses[-1]["status"] if register_responses else 0
        register_body = register_responses[-1]["body"] if register_responses else None
        ev.check(
            "8", "控制台自助注册：提交字段与后端一致",
            "点击“创建并进入”后捕获 POST /v1/accounts/self-register 请求体",
            "请求体含 password 字段", f"请求体 {payload_shape}",
            ok='"password"' in (payload_shape or ""), response={"post_data": payload_shape},
        )
        ev.check(
            "8", "控制台自助注册：提交成功",
            "浏览器完成注册表单并提交",
            "HTTP 200 且无字段错误", f"HTTP {register_status}",
            ok=register_status == 200, response=register_body,
            note="422 表示前端与后端字段不匹配。",
        )

        registered = False
        try:
            page.wait_for_function(
                "() => !!window.localStorage.getItem('liuhao.session') "
                "|| !!window.sessionStorage.getItem('liuhao.session.tab')",
                timeout=15000,
            )
            registered = True
        except Exception:
            registered = False
        page.screenshot(path=str(shots / "self-register-result.png"), full_page=True)
        ev.check(
            "8", "控制台自助注册：提交后进入主界面",
            "浏览器注册提交后检查会话存储",
            "存在 liuhao.session", "存在" if registered else "不存在",
            ok=registered, response=register_body,
        )

        if registered:
            page.evaluate("() => { localStorage.clear(); sessionStorage.clear(); }")
            try:
                _login_through_ui(page, base, "newbie-ui", TEST_ACCOUNTS["newbie-ui"])
                can_login = True
            except Exception:
                can_login = False
            ev.check(
                "8", "控制台自助注册：注册后可用同一凭据登录",
                "清空会话后回到登录页，用 newbie-ui 重新登录",
                "登录成功并进入主界面", "成功" if can_login else "失败",
                ok=can_login,
            )
        else:
            ev.check(
                "8", "控制台自助注册：注册后可用同一凭据登录",
                "前置注册失败，未能验证二次登录",
                "登录成功", "未执行", ok=False,
                note="先修复注册字段不匹配，才能验证这一项。",
            )

        context.close()
        browser.close()


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #

def main() -> int:
    parser = argparse.ArgumentParser(description="多账号八项硬要求验收（接口 + 界面）")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--no-ui", action="store_true", help="只跑接口层，跳过浏览器")
    parser.add_argument("--keep", action="store_true", help="保留实例数据目录，便于事后取证")
    parser.add_argument(
        "--force",
        action="store_true",
        help=f"跳过 src/ 最近 {GUARD_WINDOW_SECONDS} 秒写入的保护（账号核心正在改动时不要用）",
    )
    args = parser.parse_args()

    if not args.force:
        recent = recent_src_writes()
        if recent:
            print(
                "[x] 拒绝运行：src/ 最近 "
                f"{GUARD_WINDOW_SECONDS} 秒内有写入 —— 账号核心正在改动，"
                "避免把中间态当回归。",
                file=sys.stderr,
            )
            for path, mtime in recent[:8]:
                stamp = time.strftime("%H:%M:%S", time.localtime(mtime))
                print(f"    {path.relative_to(REPO)}  {stamp}", file=sys.stderr)
            print("    确认改动停止后重试；确需现在跑请加 --force。", file=sys.stderr)
            return 2
    else:
        recent = recent_src_writes()
        if recent:
            print(
                "[!] --force：已跳过防误跑保护，但 src/ 最近 "
                f"{GUARD_WINDOW_SECONDS} 秒内仍有写入 —— 本次结果可能是中间态，红项先别当回归。"
            )
            for path, mtime in recent[:8]:
                stamp = time.strftime("%H:%M:%S", time.localtime(mtime))
                print(f"    {path.relative_to(REPO)}  {stamp}")
            print("    复核方式：等这批改动停 2 分钟后去掉 --force 再跑一次，两次一致才算数。")

    data_root: Path = args.data_root
    data_root.mkdir(parents=True, exist_ok=True)
    json_out = args.json_out or (data_root / "report.json")

    if not port_is_free(args.port):
        print(f"[x] 端口 {args.port} 已被占用；请先停止占用进程再跑验收。", file=sys.stderr)
        return 2

    for name in ("instance-a", "instance-b", "ui"):
        shutil.rmtree(data_root / name, ignore_errors=True)

    b_port = next(
        (candidate for candidate in range(args.port + 1, args.port + 20) if port_is_free(candidate)),
        None,
    )
    if b_port is None:
        print(f"[x] 找不到可用于实例 B 的空闲端口（从 {args.port + 1} 起）。", file=sys.stderr)
        return 2

    ev = Evidence()
    inst_a = Instance("实例 A", data_root / "instance-a", args.port)
    inst_b = Instance("实例 B", data_root / "instance-b", b_port)
    started_a = started_b = False
    state: Dict[str, Any] = {}

    try:
        print(f"[*] 启动实例 A（API + 控制台）：{inst_a.base}，数据目录 {inst_a.root}")
        inst_a.start()
        started_a = True
        state = setup_tree(ev, inst_a)

        print("[*] 第 2 项：子树边界与越级")
        run_item2(ev, inst_a, state)

        print("[*] 第 3 项：Key 归属与借用授权")
        run_item3(ev, inst_a, state)

        print("[*] 第 4 项：记忆归属与聚合")
        run_item4(ev, inst_a, state)

        print("[*] 第 5 项：接口层是否泄露创建者/层级")
        run_item5(ev, inst_a, state)

        print("[*] 附加边界 B1：降权后旧会话立即失效")
        run_extra_b1(ev, inst_a, state)

        # 记忆 id 供 B 机复用
        status, self_mem, _ = call(inst_a, "GET", "/v1/memory/entries?scope=self", token=state["workerA_token"])
        entries = self_mem.get("entries", []) if isinstance(self_mem, dict) else []
        state["worker_a_memory_id"] = entries[0]["id"] if entries else ""

        print("[*] 第 7 项：全量导出")
        run_export(ev, inst_a, state)

        print(f"[*] 启动实例 B（换机恢复）：{inst_b.base}，数据目录 {inst_b.root}")
        inst_b.start()
        started_b = True
        print("[*] 第 6/7 项：新机导入、文件/记忆/会话、恢复码")
        run_item6_and_import(ev, inst_b, state)

        print("[*] 第 8 项：自助注册（接口层）")
        run_item8_api(ev, inst_a, state)

        if args.no_ui:
            ev.note("按 --no-ui 跳过界面层探针。")
        else:
            print("[*] 第 5/8 项：界面层（Playwright 真实浏览器）")
            run_ui_suite(ev, data_root, state, args.port)

    except Exception as exc:
        ev.interrupted = True
        ev.note(f"验收中断：{exc!r}")
        print(f"[x] 验收中断：{exc}", file=sys.stderr)
    finally:
        inst_b.stop()
        inst_a.stop()
        if started_a and started_b and not args.keep:
            ev.note("实例数据目录已保留在数据根下（instance-a / instance-b），便于取证。")

    ev.dump(json_out)
    ev.print_table()
    ev.print_failures()

    print("\n证据文件：", json_out)
    print("实例日志：", data_root / "instance-a" / "server.log", "|", data_root / "instance-b" / "server.log")
    if not args.no_ui:
        print("界面截图：", data_root / "ui")

    summary = ev.summary()
    return 0 if summary["verdict"] == "通过" else 1


if __name__ == "__main__":
    sys.exit(main())
