"""账号树 E2E：隔离实例 + 真实 HTTP（不碰仓库数据）。

覆盖七项验收：

1. 建树 / 层级上限
2. 越级防枚举（404 + 中性文案）
3. Key 借用双记（actor 与 key_owner 分开记账）
4. 记忆可见性（本人可读写、根只读聚合、越权 404）
5. 导出 / 导入（口令加密、防重放、限次）
6. 删除（有下级拒绝、级联后令牌失效）
7. 明文 Key 扫描（导出包 / 日志 / SQLite）

另加本轮前端缺口的两项真实 HTTP 验证：
* ``/v1/accounts`` 只返回**直接成员**，全树只走 ``/tree``（仅根）；
* ``/v1/memory/*`` 真实存在且按 owner 过滤。

运行：``.venv\\Scripts\\python.exe scripts/acceptance/account_tree_e2e.py``
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PY = os.path.join(REPO, ".venv", "Scripts", "python.exe")
sys.path.insert(0, REPO)

from src.gateway.display import ACCOUNT_HIERARCHY_WORDS, machine_fragments  # noqa: E402

PASSWORDS = ["rootpass123", "memberpass123", "grandpass123"]
MODEL_KEYS = ["sk-root-plain-key-123456", "sk-member-plain-key-abcdef"]
BUNDLE_PASSPHRASE = "bundle-passphrase-123"
NEUTRAL_WORDS = tuple(ACCOUNT_HIERARCHY_WORDS) + ("depth", "parent_id")


# --------------------------------------------------------------------------- #
# 基础设施
# --------------------------------------------------------------------------- #

def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def local_proof_headers(data_dir: str) -> Dict[str, str]:
    """向网关的 DACL 通道申请一次性证明（取码 / 重置各一枚）。

    回环只证明“同一台机器”，不证明“同一个 Windows 账户”；真实的桌面端
    也是这样拿令牌的，所以验收必须走同一条通道，不能直接调内部函数。
    """
    from src.common.local_proof_ipc import request_token_bundle

    bundle = request_token_bundle(data_dir=Path(data_dir))
    return {
        "X-LiuHao-Local-Proof": str(bundle["handoff_token"]),
        "X-LiuHao-Reset-Proof": str(bundle["reset_token"]),
    }


def request(
    method: str,
    url: str,
    payload: Any = None,
    token: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[int, Any]:
    data = None
    merged = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    if token:
        merged["Authorization"] = f"Bearer {token}"
    if headers:
        merged.update(headers)
    headers = merged
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            parsed = json.loads(body) if body else {}
        except Exception:
            parsed = {"raw": body}
        return exc.code, parsed


class Instance:
    """一台隔离的"真机"：独立端口 + 临时目录内的独立数据目录。

    数据目录必须显式落进临时根（``LIUHAO_DATA_DIR``），否则恢复码交接文件
    与升级快照会落到仓库 ``data/`` 或 ``%LOCALAPPDATA%\\LiuHao`` —— 验收
    不允许碰真实用户数据。``root=`` / ``start=False`` 支持"停机 → 写入旧版
    明文 → 重新启动"的迁移场景。
    """

    def __init__(
        self,
        prefix: str,
        *,
        root: Optional[str] = None,
        env_extra: Optional[Dict[str, str]] = None,
        start: bool = True,
    ) -> None:
        self.root = root or tempfile.mkdtemp(prefix=prefix)
        # 每台"机器"独立的登录签名密钥：跨机令牌不应被另一实例接受。
        self.jwt_secret = f"isolated-jwt-secret-{secrets.token_hex(16)}"
        # 身份注册表完整性密钥同样是每机独立的（与真实安装一致），
        # 用它检验恢复码/凭据包是否真的能跨机使用。
        self.integrity_key = f"isolated-integrity-{secrets.token_hex(16)}"
        self.data_dir = os.path.join(self.root, "data")
        self.env = os.environ.copy()
        for name in ("LIUHAO_HUMAN_IDENTITIES_DB", "LIUHAO_DATA_DIR"):
            self.env.pop(name, None)
        self.env.update(
            {
                "HOST": "127.0.0.1",
                "APP_ENV": "development",
                "LIUHAO_HUMAN_IDENTITIES_BACKEND": "file",
                "LIUHAO_HUMAN_IDENTITIES_FILE": os.path.join(self.root, "identities.json"),
                "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY": self.integrity_key,
                "LIUHAO_AUTH_SECRETS_FILE": os.path.join(self.root, "auth_secrets.json"),
                "LIUHAO_JWT_SECRET": self.jwt_secret,
                "AUDIT_DB_PATH": os.path.join(self.root, "audit_store.db"),
                "LIUHAO_MODEL_CONFIG_DB": os.path.join(self.root, "models.sqlite3"),
                "LIUHAO_MODEL_CONFIG_KEY": "isolated-model-key",
                "LIUHAO_WORKSPACE_ROOT": os.path.join(self.root, "workspace"),
                "PERSONAL_CONTEXT_DB_PATH": os.path.join(self.root, "personal_context.sqlite3"),
                "CONVERSATION_DB_PATH": os.path.join(self.root, "conversations.sqlite3"),
                "LIUHAO_LOG_DIR": os.path.join(self.root, "logs"),
                "LIUHAO_ACCOUNT_USAGE_DB": os.path.join(self.root, "usage.sqlite3"),
                "LIUHAO_ACCOUNT_MEMORY_DB": os.path.join(self.root, "memory.sqlite3"),
                "LIUHAO_ACCOUNT_BACKUP_DB": os.path.join(self.root, "backup.sqlite3"),
                "LIUHAO_ACCOUNT_SETTINGS_FILE": os.path.join(self.root, "account_settings.json"),
                # 恢复码交接/升级快照都按这个目录走：验收只动临时目录。
                "LIUHAO_DATA_DIR": self.data_dir,
                # 默认关；需要验证远程开关的实例用 env_extra 覆盖为 "1"。
                "LIUHAO_ALLOW_REMOTE_RECOVERY": "0",
                "LIUHAO_RECOVERY_HANDOFF_TTL_SECONDS": "600",
                # 隔离单实例锁：否则第二/第三个实例会被第一实例的锁挡住。
                "LIUHAO_INSTANCE_DIR": self.root,
            }
        )
        if env_extra:
            self.env.update({str(k): str(v) for k, v in env_extra.items()})
        os.makedirs(self.env["LIUHAO_WORKSPACE_ROOT"], exist_ok=True)
        os.makedirs(self.data_dir, exist_ok=True)
        # 必须写文件而不是 PIPE：PIPE 没人读，日志填满 64KB 缓冲后子进程会
        # 阻塞在写日志上，表现为"请求永远不返回"。文件同时供明文扫描。
        self.log_path = os.path.join(self.root, "server.log")
        self.port = 0
        self.base = ""
        self.proc: Optional[subprocess.Popen] = None
        self._log = None
        if start:
            self.start()

    def start(self) -> None:
        """启动（或重启）服务；每次重新选空闲端口，避免 TIME_WAIT 冲突。"""
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.env["PORT"] = str(self.port)
        self._log = open(self.log_path, "a", encoding="utf-8")
        self.proc = subprocess.Popen(
            [PY, "-m", "src.gateway.__main__"],
            cwd=REPO,
            env=self.env,
            stdout=self._log,
            stderr=subprocess.STDOUT,
        )
        deadline = time.time() + 120
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(self.tail())
            try:
                status, _ = request("GET", self.base + "/v1/health")
                if status == 200:
                    return
            except Exception:
                pass
            time.sleep(1)
        raise RuntimeError("server did not become ready:\n" + self.tail())

    def tail(self) -> str:
        try:
            with open(self.log_path, "r", encoding="utf-8", errors="replace") as handle:
                return handle.read()[-6000:]
        except Exception:
            return ""

    def stop(self, *, cleanup: bool = True) -> None:
        """杀进程树：venv 启动器会再拉起一个 uv python 子进程。"""
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
        # 跑完回收：隔离实例的临时目录不留在 %TEMP%。
        # ``cleanup=False`` 留给"停机写旧文件 → 重启"的迁移场景。
        if cleanup:
            shutil.rmtree(self.root, ignore_errors=True)


# --------------------------------------------------------------------------- #
# 结果收集
# --------------------------------------------------------------------------- #

class Report:
    def __init__(self) -> None:
        self.rows: List[Dict[str, Any]] = []

    def check(self, item: str, name: str, got: Any, expected: Any, evidence: str = "") -> bool:
        ok = got == expected
        self.rows.append(
            {
                "item": item,
                "check": name,
                "got": got,
                "expected": expected,
                "ok": ok,
                "evidence": evidence,
            }
        )
        print(f"[{'PASS' if ok else 'FAIL'}] {item} · {name}: got={got!r} expected={expected!r}")
        return ok

    def failed(self) -> List[Dict[str, Any]]:
        return [row for row in self.rows if not row["ok"]]


def neutral(message: Any) -> bool:
    text = str(message or "")
    return not any(word in text for word in NEUTRAL_WORDS)


def error_code(body: Any) -> Any:
    """兼容两种错误封装：扁平 ``code`` 与 FastAPI ``detail={code,...}``。"""
    if isinstance(body, dict):
        if "code" in body:
            return body.get("code")
        detail = body.get("detail")
        if isinstance(detail, dict):
            return detail.get("code")
    return None


def tree_count(node: Any) -> int:
    if not isinstance(node, dict):
        return 0
    return 1 + sum(tree_count(child) for child in node.get("children") or [])


def machine_free(text: Any) -> bool:
    """主界面文案不含 ``key=value`` / 点号链 / snake_case 机器串。"""
    return not machine_fragments(text)


def user_texts(value: Any, key: Optional[str] = None) -> List[str]:
    """递归收集响应里承载"给用户看的话"的字段。"""
    found: List[str] = []
    if isinstance(value, dict):
        for name, item in value.items():
            if name in ("detail", "notice", "message", "user_message", "hint") \
                    and isinstance(item, str):
                found.append(item)
            found.extend(user_texts(item, name))
    elif isinstance(value, list):
        for item in value:
            found.extend(user_texts(item, key))
    return found


# --------------------------------------------------------------------------- #
# 档位等价性探针：同一主体、同一 epoch，只改令牌里的 scope 声明
# --------------------------------------------------------------------------- #
#
# 目的：证明"B5 把成员从 L7 改成 depth 派生"没有放宽任何 HTTP 可达权限。
# 做法：解出登录令牌的 payload，用同一个签名密钥重新签一份只把 metadata.scope
# 换成 L7 的令牌，然后对同一组真实端点各打一遍，比较 (状态码, 错误码)。
# 若两套档位逐条完全一致，则"变宽/变窄"均不存在。

#: 兼容旧调用；真实实例使用 ``Instance.jwt_secret``（每机独立）。
JWT_SECRET = "isolated-jwt-secret-for-acceptance"


def _b64url_decode(segment: str) -> str:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)).decode("utf-8")


def decode_jwt(token: str) -> Dict[str, Any]:
    return json.loads(_b64url_decode(str(token).split(".")[1]))


def _b64url_encode(value: Dict[str, Any]) -> str:
    raw = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def mint_scope_variant(token: str, scope: str, secret: str = JWT_SECRET) -> str:
    """同一主体 / 同一 epoch，只替换 ``metadata.scope`` 的等价令牌。"""
    header_segment, _, _ = str(token).split(".")
    header = json.loads(_b64url_decode(header_segment))
    payload = decode_jwt(token)
    metadata = dict(payload.get("metadata") or {})
    metadata["scope"] = scope
    payload["metadata"] = metadata
    now = int(time.time())
    payload["iat"] = now
    payload["exp"] = now + 900
    head, body = _b64url_encode(header), _b64url_encode(payload)
    signature = hmac.new(
        secret.encode("utf-8"), f"{head}.{body}".encode("utf-8"), hashlib.sha256
    ).digest()
    return f"{head}.{body}.{base64.urlsafe_b64encode(signature).decode().rstrip('=')}"


SCOPE_MATRIX: Tuple[Tuple[str, str, str, Any], ...] = (
    ("GET /v1/auth/me", "GET", "/v1/auth/me", None),
    ("GET /v1/accounts", "GET", "/v1/accounts", None),
    ("GET /v1/accounts/tree", "GET", "/v1/accounts/tree", None),
    ("GET /v1/accounts/member1/usage", "GET", "/v1/accounts/member1/usage", None),
    ("POST /v1/accounts/backup/export all", "POST", "/v1/accounts/backup/export",
     {"scope": "all", "passphrase": BUNDLE_PASSPHRASE}),
    ("POST /v1/accounts/backup/export self", "POST", "/v1/accounts/backup/export",
     {"scope": "self", "passphrase": BUNDLE_PASSPHRASE}),
    ("GET /v1/files", "GET", "/v1/files", None),
    ("GET /v1/chat/sessions", "GET", "/v1/chat/sessions", None),
    ("GET /v1/chat/stats", "GET", "/v1/chat/stats", None),
    ("GET /v1/goals", "GET", "/v1/goals", None),
    ("GET /v1/audit/events", "GET", "/v1/audit/events?limit=5", None),
    ("GET /v1/audit/events?principal=root1", "GET",
     "/v1/audit/events?principal=root1", None),
    ("POST /v1/audit/verify", "POST", "/v1/audit/verify", {}),
    ("GET /v1/memory/entries self", "GET", "/v1/memory/entries?scope=self", None),
    ("GET /v1/memory/entries subtree", "GET", "/v1/memory/entries?scope=subtree", None),
    ("POST /v1/memory/export self", "POST", "/v1/memory/export",
     {"scope": "self", "format": "json"}),
    ("POST /v1/memory/export subtree", "POST", "/v1/memory/export",
     {"scope": "subtree", "format": "json"}),
)


def _observe(inst: Instance, token: str, method: str, path: str, payload: Any) -> Tuple[int, Any]:
    status, body = request(method, inst.base + path, payload, token=token)
    code = body.get("code") if isinstance(body, dict) else None
    if code is None and isinstance(body, dict):
        detail = body.get("detail")
        code = detail if isinstance(detail, str) else (detail or {}).get("code")
    return status, code


def _verdict(old: Tuple[int, Any], now: Tuple[int, Any]) -> str:
    """同一主体、同一 epoch 下，仅 scope 声明不同导致的权限方向判定。"""
    if old == now:
        return "不变"
    old_ok, now_ok = old[0] < 400, now[0] < 400
    if not old_ok and now_ok:
        return "变宽"
    if old_ok and not now_ok:
        return "变窄"
    # 状态码同档但错误码不同：权限边界未移动。
    return "不变（错误码不同）"


def scope_equivalence(report: Report, inst: Instance, tokens: Dict[str, Any]) -> None:
    """需求 1/2：L7 vs depth 派生档位，逐端点比较；并确认根最高、子严格递减。

    ``tokens`` 里还有非 JWT 的辅助字段（如 ``grand_principal``），这里只取三个
    登录令牌，避免把普通字符串当成令牌解析。
    """
    item = "档位等价性"
    scope_of = {
        principal: decode_jwt(tokens[key]).get("metadata", {}).get("scope")
        for key, principal in (("root", "root1"), ("member", "member1"), ("grand", "grand1"))
    }
    report.check(
        item, "层级档位严格递减",
        (scope_of["root1"], scope_of["member1"], scope_of["grand1"]),
        ("L0", "L1", "L2"),
    )

    def compare(subject: str, token: str, legacy_scope: str, matrix) -> List[str]:
        """旧档位（改动前）打一遍，当前档位（改动后）打一遍，逐条判方向。"""
        legacy = mint_scope_variant(token, legacy_scope, secret=inst.jwt_secret)
        verdicts: List[str] = []
        for name, method, path, payload in matrix:
            old_obs = _observe(inst, legacy, method, path, payload)
            now_obs = _observe(inst, token, method, path, payload)
            label = _verdict(old_obs, now_obs)
            verdicts.append(label)
            report.check(
                item,
                f"{subject} · {name} → {label}（{legacy_scope}={old_obs} / "
                f"{scope_of[subject]}={now_obs}）",
                label not in ("变宽",), True,
                f"old={old_obs} now={now_obs}",
            )
        return verdicts

    member_verdicts = compare("member1", tokens["member"], "L7", SCOPE_MATRIX)
    # 孙账号：只挑会触及层级/共享/备份/审计边界的端点，避免重复耗时。
    grand_matrix = tuple(row for row in SCOPE_MATRIX if any(
        key in row[0] for key in ("tree", "backup", "usage", "audit", "subtree", "scope=self")
    ))
    grand_verdicts = compare("grand1", tokens["grand"], "L7", grand_matrix)

    # 需求 2：根必须仍是最高档 —— 子账号能过的，根必须能过；根专属端点根必过。
    hierarchy_matrix = SCOPE_MATRIX
    for name, method, path, payload in hierarchy_matrix:
        root_obs = _observe(inst, tokens["root"], method, path, payload)
        child_obs = _observe(inst, tokens["grand"], method, path, payload)
        child_ok, root_ok = child_obs[0] < 400, root_obs[0] < 400
        report.check(
            item,
            f"root1 ⊇ grand1 · {name} → {'不变' if child_ok == root_ok else ('根更宽' if root_ok else '根更窄(越权!)')}",
            (not child_ok) or root_ok,
            True,
            f"root={root_obs} grand={child_obs}",
        )
        if any(key in name for key in ("tree", "backup/export all", "audit?principal=root1",
                                       "memory/entries subtree", "memory/export subtree")):
            report.check(
                item, f"根专属可用 · {name}", root_ok, True, f"root={root_obs}",
            )

    # 打印紧凑对照表，供报告直接引用（不计入 PASS/FAIL）。
    print("\n--- 档位对照表（old=L7 / new=depth 派生）---")
    for name, label in zip([row[0] for row in SCOPE_MATRIX], member_verdicts):
        print(f"member1\t{label}\t{name}")
    for name, label in zip([row[0] for row in grand_matrix], grand_verdicts):
        print(f"grand1\t{label}\t{name}")
    print("--- 对照表结束 ---\n")



# --------------------------------------------------------------------------- #
# 场景
# --------------------------------------------------------------------------- #

def bootstrap(report: Report, inst: Instance) -> Dict[str, Any]:
    item = "建树/层级"
    status, _ = request("GET", inst.base + "/v1/accounts")
    report.check(item, "未鉴权列表 401", status, 401)
    status, _ = request("GET", inst.base + "/v1/accounts/roles")
    report.check(item, "公开角色列表 200", status, 200)

    status, registed = request(
        "POST", inst.base + "/v1/accounts/register",
        {"principal": "root1", "password": PASSWORDS[0], "display_name": "主账号"},
    )
    report.check(item, "注册根账号", status, 200, f"recovery={len(registed.get('recovery_code') or '')}")

    status, body = request(
        "POST", inst.base + "/v1/auth/login",
        {"principal": "root1", "secret": PASSWORDS[0]},
    )
    root_token = body.get("access_token") or ""
    report.check(item, "根登录 depth/path", (status, body.get("depth"), body.get("path")), (200, 0, "/root1"))

    status, body = request(
        "POST", inst.base + "/v1/accounts",
        {"principal": "member1", "role": "manager", "allow_key_sharing": True},
        token=root_token,
    )
    report.check(item, "根建直接成员", status, 200, f"code={bool(body.get('activation_code'))}")
    status, activated = request(
        "POST", inst.base + "/v1/accounts/activate",
        {"principal": "member1", "code": body.get("activation_code") or "",
         "password": PASSWORDS[1]},
    )
    member_token = activated.get("token") or ""
    report.check(item, "成员激活返回字符串 token", (status, isinstance(activated.get("token"), str)), (200, True))

    status, body = request(
        "POST", inst.base + "/v1/accounts",
        {"principal": "grand1", "role": "operator"}, token=member_token,
    )
    report.check(item, "成员建直接下级", status, 200)
    request(
        "POST", inst.base + "/v1/accounts/activate",
        {"principal": "grand1", "code": body.get("activation_code") or "",
         "password": PASSWORDS[2]},
    )
    status, body = request(
        "POST", inst.base + "/v1/auth/login",
        {"principal": "grand1", "secret": PASSWORDS[2]},
    )
    grand_token = body.get("access_token") or ""
    report.check(item, "第三层登录 depth=2", (status, body.get("depth")), (200, 2))

    status, body = request(
        "POST", inst.base + "/v1/accounts",
        {"principal": "too_deep", "role": "viewer"}, token=grand_token,
    )
    message = (body.get("message") or "") + str(body.get("detail") or "")
    report.check(item, "超过层级上限 409", status, 409, f"code={body.get('code')}")
    report.check(item, "层级错误码", body.get("code"), "ACCOUNT_DEPTH_EXCEEDED")
    report.check(item, "层级文案中性", neutral(message), True, message[:80])

    status, body = request(
        "POST", inst.base + "/v1/accounts/register",
        {"principal": "root2", "password": "rootpass456"},
    )
    report.check(item, "第二个根 409", status, 409, f"code={body.get('code')}")

    return {
        "root": root_token,
        "member": member_token,
        "grand": grand_token,
        "grand_principal": "grand1",
        # 恢复码明文只在注册响应里出现一次；跨机重置密码要用它。
        "recovery": registed.get("recovery_code") or "",
    }


def direct_members(report: Report, inst: Instance, tokens: Dict[str, Any]) -> None:
    item = "直接成员口径"
    status, body = request("GET", inst.base + "/v1/accounts", token=tokens["root"])
    names = sorted(m.get("principal") for m in body.get("members") or [])
    report.check(item, "根列表只有直接成员", (status, names), (200, ["member1"]))
    report.check(item, "成员带 is_direct", all(m.get("is_direct") for m in body.get("members") or []), True)
    report.check(item, "列表无 parent_id", "parent_id" in json.dumps(body), False)

    status, body = request("GET", inst.base + "/v1/accounts", token=tokens["member"])
    names = sorted(m.get("principal") for m in body.get("members") or [])
    report.check(item, "成员列表只有其直接下级", names, ["grand1"])
    report.check(item, "成员列表不含自己/上级", "member1" in names or "root1" in names, False)

    status, body = request("GET", inst.base + "/v1/accounts", token=tokens["grand"])
    report.check(item, "第三层列表为空", (status, body.get("count")), (200, 0))

    status, body = request("GET", inst.base + "/v1/accounts/tree", token=tokens["root"])
    tree = body.get("tree") or {}
    report.check(item, "根可读整树", (status, tree.get("principal")), (200, "root1"))
    status, body = request("GET", inst.base + "/v1/accounts/tree", token=tokens["member"])
    report.check(item, "成员读树 404", (status, body.get("code")), (404, "ACCOUNT_SUBTREE_DENIED"))


def enumeration(report: Report, inst: Instance, tokens: Dict[str, Any]) -> None:
    item = "越级防枚举"
    status, body = request("GET", inst.base + "/v1/accounts/root1", token=tokens["member"])
    report.check(item, "成员读上级 404", (status, body.get("code")), (404, "ACCOUNT_SUBTREE_DENIED"))
    report.check(item, "越级文案中性", neutral(body.get("message")), True)

    status, body = request(
        "PATCH", inst.base + "/v1/accounts/root1/permissions",
        {"grant": ["account:grant"], "revoke": []}, token=tokens["member"],
    )
    report.check(item, "成员改上级权限 404", status, 404)

    status, body = request("GET", inst.base + "/v1/accounts/root1/usage", token=tokens["member"])
    report.check(item, "成员读上级用量 404", (status, body.get("code")), (404, "ACCOUNT_SUBTREE_DENIED"))

    status, body = request("GET", inst.base + "/v1/accounts/member1/usage", token=tokens["grand"])
    report.check(item, "第三层读成员用量 404", status, 404)

    status, body = request("GET", inst.base + "/v1/accounts/member1", token=tokens["grand"])
    report.check(item, "第三层读成员 404", status, 404)


def usage_double_entry(report: Report, inst: Instance, tokens: Dict[str, Any]) -> None:
    item = "Key 借用双记"
    status, body = request(
        "POST", inst.base + "/v1/models",
        {"name": "member-key", "provider": "mock", "model": "mock-1",
         "is_default": True, "owner_principal": "member1"},
        token=tokens["member"],
    )
    model_id = body.get("id") or ""
    report.check(item, "成员创建 mock 模型", status, 200, f"id={model_id}")
    report.check(item, "模型归属成员", body.get("owner_principal"), "member1")

    status, root_chat = request(
        "POST", inst.base + "/v1/chat",
        {"message": "根借成员 Key", "session_id": "root-session",
         "model_id": model_id, "use_knowledge": False},
        token=tokens["root"],
    )
    report.check(item, "根借用成员 Key 对话", status, 200, f"status={root_chat.get('status')}")
    status, member_chat = request(
        "POST", inst.base + "/v1/chat",
        {"message": "成员自己用", "session_id": "member-session",
         "model_id": model_id, "use_knowledge": False},
        token=tokens["member"],
    )
    report.check(item, "成员自己用同一 Key", status, 200)

    # 反向：主账号自己的 Key，子账号不能用（越级借用只允许"上借下"）。
    status, root_model = request(
        "POST", inst.base + "/v1/models",
        {"name": "root-key", "provider": "mock", "model": "mock-1",
         "is_default": False, "owner_principal": "root1"},
        token=tokens["root"],
    )
    root_model_id = root_model.get("id") or ""
    report.check(item, "根创建自己的 mock 模型", status, 200, f"id={root_model_id}")
    status, body = request(
        "POST", inst.base + "/v1/chat",
        {"message": "成员用根 Key", "session_id": "member-session-2",
         "model_id": root_model_id, "use_knowledge": False},
        token=tokens["member"],
    )
    report.check(
        item, "成员不能用根 Key",
        (status, error_code(body)), (403, "ACCOUNT_KEY_NOT_OWNED"), str(body)[:120],
    )
    status, body = request("GET", inst.base + "/v1/models", token=tokens["member"])
    member_ids = [entry.get("id") for entry in body.get("models") or []]
    report.check(item, "成员模型列表不含根 Key", root_model_id in member_ids, False, str(member_ids))

    status, root_view = request("GET", inst.base + "/v1/accounts/member1/usage", token=tokens["root"])
    actors = sorted(entry.get("actor") for entry in root_view.get("by_actor") or [])
    owners = sorted(entry.get("key_owner") for entry in root_view.get("by_key_owner") or [])
    report.check(item, "根可见调用者身份", (status, actors), (200, ["member1", "root1"]))
    report.check(item, "账单归成员 Key", owners, ["member1"])
    report.check(item, "根视角 calls=2", root_view.get("calls"), 2)

    status, member_view = request(
        "GET", inst.base + "/v1/accounts/member1/usage", token=tokens["member"]
    )
    report.check(item, "成员只见自己账单", (status, member_view.get("by_key_owner") and [x.get("key_owner") for x in member_view["by_key_owner"]]), (200, ["member1"]))
    report.check(item, "成员响应无 by_actor", "by_actor" in member_view, False)
    # 成员侧只统计本人发起的调用：根借用 1 次不改变成员的 calls（仍在账本
    # 里、仍归 member1 的 Key），根视角同时看到真实总量 2。
    report.check(item, "成员 calls=1（借用不被计入）", member_view.get("calls"), 1)

    status, root_self = request("GET", inst.base + "/v1/accounts/root1/usage", token=tokens["root"])
    report.check(item, "根查自己=整树账单", (status, root_self.get("calls")), (200, 2))
    status, grand_usage = request("GET", inst.base + "/v1/accounts/grand1/usage", token=tokens["root"])
    report.check(item, "无用量节点 calls=0", (status, grand_usage.get("calls")), (200, 0))

    # 借用方的身份不能以任何形式出现在被借用方的界面里：逐个打真实接口，
    # 在完整响应文本里搜 root1。
    surfaces = (
        ("登录自述", "GET", "/v1/auth/me"),
        ("成员列表", "GET", "/v1/accounts"),
        ("本人资料", "GET", "/v1/accounts/member1"),
        ("用量", "GET", "/v1/accounts/member1/usage"),
        ("文件", "GET", "/v1/files"),
        ("记忆", "GET", "/v1/memory/entries?scope=self"),
        ("会话列表", "GET", "/v1/chat/sessions"),
        ("会话统计", "GET", "/v1/chat/stats?session_id=member-session"),
        ("审计事件", "GET", "/v1/audit/events"),
        ("事件历史", "GET", "/v1/events/history"),
        ("模型配置", "GET", "/v1/models"),
    )
    def without_position(body: Any) -> str:
        """去掉"本人位置"字段后再扫描。

        ``parent_account_id`` / ``path`` 描述的是**成员自己**在目录里的位置，
        是前端裁剪能力所必需的自述信息；它不是"谁用过我的 Key"的痕迹。
        其余任何字段出现借用方都算泄漏。
        """
        if isinstance(body, dict):
            return json.dumps(
                {k: json.loads(without_position(v)) if isinstance(v, (dict, list)) else v
                 for k, v in body.items() if k not in ("parent_account_id", "path")},
                ensure_ascii=False,
            )
        if isinstance(body, list):
            return json.dumps([json.loads(without_position(item)) for item in body],
                              ensure_ascii=False)
        return json.dumps(body, ensure_ascii=False)

    leaks: List[Tuple[str, int, str]] = []
    for name, method, path in surfaces:
        status, body = request(method, inst.base + path, token=tokens["member"])
        raw = without_position(body)
        if "root1" in raw:
            leaks.append((name, status, raw[:120]))
    report.check(item, "成员 11 个界面 0 次出现借用方", leaks, [], f"surfaces={len(surfaces)}")
    report.check(item, "成员用量只算本人调用",
                 (member_view.get("calls"),
                  [entry.get("key_owner") for entry in member_view.get("by_key_owner") or []]),
                 (1, ["member1"]))
    return {"model_id": model_id}


def memory_visibility(report: Report, inst: Instance, tokens: Dict[str, Any]) -> Dict[str, Any]:
    item = "记忆可见性"
    texts = {"root": "root-secret-memory", "member": "member-secret-memory", "grand": "grand-secret-memory"}
    ids: Dict[str, str] = {}
    for who, token in (("root", tokens["root"]), ("member", tokens["member"]), ("grand", tokens["grand"])):
        status, body = request(
            "POST", inst.base + "/v1/memory/entries",
            {"text": texts[who], "source": "acceptance"}, token=token,
        )
        ids[who] = (body.get("entry") or {}).get("id") or ""
        report.check(item, f"{who} 写自己的记忆", status, 201)

    status, body = request("GET", inst.base + "/v1/memory/entries?scope=self", token=tokens["member"])
    texts_got = [entry.get("text") for entry in body.get("entries") or []]
    report.check(item, "成员列表只有自己", (status, texts_got), (200, [texts["member"]]))

    status, body = request("GET", inst.base + "/v1/memory/entries?scope=subtree", token=tokens["member"])
    report.check(item, "成员请求聚合 404", (status, body.get("code")), (404, "ACCOUNT_SUBTREE_DENIED"))

    status, body = request("GET", inst.base + "/v1/memory/entries?scope=subtree", token=tokens["root"])
    accounts = sorted(entry.get("account_id") for entry in body.get("entries") or [])
    report.check(item, "根聚合整棵子树", (status, accounts), (200, ["grand1", "member1", "root1"]))
    report.check(item, "聚合带来源标注", all(e.get("account_id") for e in body.get("entries") or []), True)

    status, body = request(
        "GET", f"{inst.base}/v1/memory/entries/{ids['member']}", token=tokens["root"]
    )
    report.check(item, "根读成员单条（只读聚合）", status, 200)
    status, body = request(
        "GET", f"{inst.base}/v1/memory/entries/{ids['root']}", token=tokens["member"]
    )
    report.check(item, "成员读根条目 404", (status, body.get("code")), (404, "ACCOUNT_MEMORY_FOREIGN_DENIED"))
    status, body = request(
        "GET", f"{inst.base}/v1/memory/entries/{ids['member']}", token=tokens["grand"]
    )
    report.check(item, "第三层读成员条目 404", status, 404)

    status, body = request("GET", inst.base + "/v1/memory/entries?scope=self&q=member-secret", token=tokens["member"])
    report.check(item, "检索命中自己", (status, len(body.get("entries") or [])), (200, 1))
    status, body = request("GET", inst.base + "/v1/memory/entries?scope=subtree&q=root-secret", token=tokens["root"])
    report.check(item, "根检索跨账号命中", (status, len(body.get("entries") or [])), (200, 1))

    status, body = request(
        "POST", inst.base + "/v1/memory/export", {"scope": "self", "format": "json"},
        token=tokens["member"],
    )
    payload = json.loads(body.get("content") or "{}") if isinstance(body.get("content"), str) else {}
    report.check(item, "成员导出仅自己", (status, [e.get("account_id") for e in payload.get("entries") or []]), (200, ["member1"]))
    status, body = request(
        "POST", inst.base + "/v1/memory/export", {"scope": "subtree", "format": "json"},
        token=tokens["root"],
    )
    payload = json.loads(body.get("content") or "{}")
    report.check(item, "根导出整棵子树", (status, len(payload.get("entries") or [])), (200, 3))
    status, body = request(
        "POST", inst.base + "/v1/memory/export", {"scope": "subtree", "format": "json"},
        token=tokens["member"],
    )
    report.check(item, "成员导出聚合 404", status, 404)

    status, body = request(
        "POST", inst.base + "/v1/memory/custody", {"enabled": True}, token=tokens["member"]
    )
    report.check(item, "成员显式开启代管（写审计）", (status, body.get("enabled")), (200, True))
    status, body = request("GET", inst.base + "/v1/memory/custody", token=tokens["member"])
    report.check(item, "代管状态可读", (status, body.get("enabled")), (200, True))

    return {"ids": ids, "texts": texts}


def employee_adoption(report: Report, inst: Instance, tokens: Dict[str, Any]) -> None:
    """§ 根的取用：子账号员工/技能复制到根空间；子账号侧零痕迹。"""
    item = "员工取用"
    status, body = request(
        "POST", inst.base + "/v1/employees",
        {"name": "member-writer", "agent_count": 2,
         "agent_types": ["planner", "executor"],
         "role_description": "成员负责整理日报"},
        token=tokens["member"],
    )
    report.check(item, "成员创建自己的员工", (status, body.get("name")), (201, "member-writer"))

    status, body = request("GET", inst.base + "/v1/employees", token=tokens["root"])
    names = sorted(emp.get("name") for emp in body.get("employees") or [])
    report.check(item, "根可见子账号员工（只读）", (status, "member-writer" in names), (200, True))

    status, body = request(
        "POST", inst.base + "/v1/employees/adopt",
        {"name": "member-writer", "as_name": "adopted-writer"},
        token=tokens["root"],
    )
    adopted = body.get("employee") or {}
    report.check(item, "根取用子账号员工", (status, adopted.get("name")), (201, "adopted-writer"))
    report.check(item, "副本归属根自己", adopted.get("owner_account_id"), "root1")
    report.check(item, "取用保留职责描述", adopted.get("role_description"), "成员负责整理日报")
    report.check(item, "取用保留默认能力",
                 {a.get("agent_type") for a in adopted.get("agents") or []},
                 {"planner", "executor"})
    report.check(item, "取用可追溯来源",
                 adopted.get("adopted_from"),
                 {"account_id": "member1", "employee_name": "member-writer"})

    status, body = request("GET", inst.base + "/v1/employees", token=tokens["member"])
    names = sorted(emp.get("name") for emp in body.get("employees") or [])
    report.check(item, "子账号员工列表无副本", (status, names), (200, ["member-writer"]))

    # 根取用后的副本对子账号必须"与不存在逐字一致"：状态码 + 响应正文
    # 完全相同，不能靠文案/字段差异泄漏"主账号复制过我的员工"。
    adopted_agents = adopted.get("agents") or []
    copy_agent_id = adopted_agents[0].get("id") if adopted_agents else ""
    missing_agent_id = "no-such-agent-00000000"
    status_copy, body_copy = request(
        "GET", inst.base + f"/v1/employees/{copy_agent_id}", token=tokens["member"]
    )
    status_missing, body_missing = request(
        "GET", inst.base + f"/v1/employees/{missing_agent_id}", token=tokens["member"]
    )
    report.check(item, "成员读根副本=读不存在(状态)", status_copy, status_missing, str(status_copy))
    report.check(
        item, "成员读根副本=读不存在(正文逐字)",
        json.dumps(body_copy, ensure_ascii=False, sort_keys=True),
        json.dumps(body_missing, ensure_ascii=False, sort_keys=True),
    )

    # 子账号能看到的界面里不能出现主账号痕迹（员工/目标/审计）。
    # 只剔除"本人位置"自述字段（parent_account_id/path），其余都算泄漏。
    def visible_text(body: Any) -> str:
        if isinstance(body, dict):
            return json.dumps(
                {k: v for k, v in body.items() if k not in ("parent_account_id", "path")},
                ensure_ascii=False, default=str,
            )
        return json.dumps(body, ensure_ascii=False, default=str)

    leaks = []
    for path in ("/v1/employees", "/v1/goals", "/v1/audit/events"):
        status, body = request("GET", inst.base + path, token=tokens["member"])
        raw = visible_text(body)
        if "root1" in raw or "adopted-writer" in raw:
            leaks.append((path, status, raw[:120]))
    report.check(item, "子账号侧无主账号痕迹", leaks, [], str(leaks))

    # 成员不能取用；非根一律 404（与不存在不可区分）。
    status, body = request(
        "POST", inst.base + "/v1/employees/adopt",
        {"name": "adopted-writer"}, token=tokens["member"],
    )
    report.check(item, "成员取用被拒", (status, body.get("code")), (404, "employee_not_found"))


def memory_adoption(report: Report, inst: Instance, tokens: Dict[str, Any]) -> None:
    """§ 记忆取用：根把子树记忆复制进自己的库；源数据与成员侧完全不变。"""
    item = "记忆取用"
    status, body = request(
        "POST", inst.base + "/v1/memory/adopt",
        {"account_id": "member1"}, token=tokens["root"],
    )
    report.check(item, "根取用成员记忆", (status, body.get("imported")), (200, 1), str(body))

    status, body = request(
        "GET", inst.base + "/v1/memory/entries?scope=self&q=member-secret", token=tokens["root"]
    )
    entries = body.get("entries") or []
    report.check(item, "取用后可搜索", (status, len(entries)), (200, 1), str(entries)[:160])
    report.check(item, "取用条目带来源标注",
                 (entries[0].get("source"), entries[0].get("account_id")),
                 ("adopt:member1", "root1"), str(entries)[:160])

    status, body = request("GET", inst.base + "/v1/memory/entries?scope=self", token=tokens["member"])
    texts = [entry.get("text") for entry in body.get("entries") or []]
    report.check(item, "成员记忆原样未动", (status, texts), (200, ["member-secret-memory"]))

    status, body = request(
        "POST", inst.base + "/v1/memory/adopt", {}, token=tokens["member"]
    )
    report.check(item, "成员取用被拒", (status, body.get("code")),
                 (404, "ACCOUNT_SUBTREE_DENIED"))

    # 重复取用幂等：不会产生第二份副本。
    status, body = request(
        "POST", inst.base + "/v1/memory/adopt",
        {"account_id": "member1"}, token=tokens["root"],
    )
    report.check(item, "重复取用不产生副本", (status, body.get("imported")), (200, 0), str(body))


def backup_roundtrip(
    report: Report, inst: Instance, tokens: Dict[str, Any]
) -> Dict[str, Any]:
    item = "导出导入"
    status, exported = request(
        "POST", inst.base + "/v1/accounts/backup/export",
        {"scope": "all", "passphrase": BUNDLE_PASSPHRASE}, token=tokens["root"],
    )
    manifest = exported.get("manifest") or {}
    bundle = exported.get("bundle") or {}
    report.check(item, "根全量导出", status, 200, f"bundle_id={bundle.get('bundle_id')}")
    # 真实导出产物（HTTP 响应原文）里不得出现恢复码、明文密码或明文 Key。
    raw_export = json.dumps(exported, ensure_ascii=False)
    needles = [str(tokens.get("recovery") or ""), *PASSWORDS, *MODEL_KEYS]
    leaks = [needle for needle in needles if needle and needle in raw_export]
    report.check(item, "导出包不含恢复码/明文凭据", leaks, [], str(leaks[:2]))
    report.check(item, "导出包不含恢复码文件名", "recovery_code" in raw_export, False)
    # 新口径：整包不带成员 Key 与模型主密钥，回执如实标注。
    report.check(item, "导出回执：未含成员 Key/主密钥",
                 (exported.get("member_keys_included"), exported.get("key_material_included")),
                 (False, False))
    report.check(item, "导出回执：明确提示未含成员 Key",
                 "未包含成员 Key" in str(exported.get("notice")), True)
    report.check(item, "清单成员数=3", manifest.get("member_count"), 3)
    report.check(item, "清单含成员列表", sorted(manifest.get("members") or []), ["grand1", "member1", "root1"])
    report.check(item, "清单含文件数/字节数", ("files" in manifest and "bytes" in manifest), True, str({k: manifest.get(k) for k in ("files", "bytes")}))
    store_manifest = manifest.get("stores") or {}
    memory_store = store_manifest.get("account_memory") or {}
    conversation_store = store_manifest.get("conversation") or {}
    report.check(item, "清单写明记忆库与条数",
                 (memory_store.get("db"), memory_store.get("entries")),
                 ("account_memory.sqlite3", 3), str(memory_store))
    report.check(item, "清单写明会话库与条数",
                 (conversation_store.get("db"), conversation_store.get("turns", 0) > 0),
                 ("conversation_store.db", True), str(conversation_store))

    status, body = request(
        "POST", inst.base + "/v1/accounts/backup/export",
        {"scope": "all", "passphrase": BUNDLE_PASSPHRASE}, token=tokens["member"],
    )
    report.check(item, "成员越权全量导出 403", (status, body.get("code")), (403, "ACCOUNT_SCOPE_DENIED"))
    report.check(item, "越权文案中性", neutral(body.get("message")), True, str(body.get("message")))

    status, own = request(
        "POST", inst.base + "/v1/accounts/backup/export",
        {"scope": "self", "passphrase": BUNDLE_PASSPHRASE}, token=tokens["member"],
    )
    own_manifest = own.get("manifest") or {}
    report.check(item, "成员 self 只导出本人", (status, own_manifest.get("members")), (200, ["member1"]))

    # ---- 跨机令牌策略：导出只读，不吊销 A 机旧令牌 ----------------------- #
    policy = exported.get("token_policy") or {}
    report.check(
        item, "导出响应写明令牌策略",
        (policy.get("scope"), policy.get("export_revokes_tokens"),
         policy.get("source_tokens_stay_valid_on_source"),
         policy.get("source_tokens_valid_on_target")),
        ("instance", False, True, False), str(policy),
    )
    status, body = request("GET", inst.base + "/v1/accounts/tree", token=tokens["root"])
    report.check(
        item, "导出后 A 机旧令牌仍有效",
        (status, (body.get("tree") or {}).get("principal")), (200, "root1"),
    )

    status, a_me = request("GET", inst.base + "/v1/auth/me", token=tokens["root"])
    a_scope = a_me.get("scope")
    a_permissions = sorted(a_me.get("permissions") or [])
    status, a_root = request("GET", inst.base + "/v1/accounts/root1", token=tokens["root"])
    a_account = a_root.get("account") or {}

    second = Instance("liuhao-acct-import-")
    try:
        bundle = exported.get("bundle") or {}
        status, imported = request(
            "POST", second.base + "/v1/accounts/backup/import",
            {"bundle": bundle, "passphrase": BUNDLE_PASSPHRASE},
        )
        report.check(item, "新机导入成功", (status, imported.get("root_principal")), (200, "root1"))
        report.check(item, "导入恢复账号数", imported.get("accounts"), 3)
        memory_import = imported.get("memory") or {}
        conversation_import = imported.get("conversations") or {}
        report.check(item, "导入带回全部记忆", memory_import.get("imported"),
                     memory_store.get("entries"), str(memory_import))
        report.check(item, "导入带回全部会话", conversation_import.get("imported"),
                     conversation_store.get("turns"), str(conversation_import))
        imported_policy = imported.get("token_policy") or {}
        report.check(item, "导入响应重申令牌策略",
                     imported_policy.get("source_tokens_valid_on_target"), False, str(imported_policy))
        report.check(item, "导入提示中性且可读",
                     (neutral(imported.get("notice")), machine_free(imported.get("notice"))),
                     (True, True), str(imported.get("notice")))

        # A 机三个令牌在 B 机全部无效（B 用自己生成的签名密钥）。
        foreign = []
        for who in ("root", "member", "grand"):
            status, body = request("GET", second.base + "/v1/auth/me", token=tokens[who])
            foreign.append((who, status, body.get("code") or body.get("detail")))
        report.check(item, "A 机旧令牌在 B 机全部被拒",
                     {row[1] for row in foreign}, {401}, str(foreign))
        report.check(item, "跨机令牌拒绝文案中性",
                     all(neutral(row[2]) for row in foreign), True, str(foreign))

        # B 机根账号：原密码可登录，权限/可见范围与 A 机一致。
        status, login = request(
            "POST", second.base + "/v1/auth/login",
            {"principal": "root1", "secret": PASSWORDS[0]},
        )
        report.check(item, "导入后原凭据可登录", status, 200)
        b_token = login.get("access_token") or ""
        status, b_me = request("GET", second.base + "/v1/auth/me", token=b_token)
        report.check(item, "跨机后同一份权限",
                     (b_me.get("scope"), sorted(b_me.get("permissions") or [])),
                     (a_scope, a_permissions), f"scope={b_me.get('scope')}")
        status, b_tree = request("GET", second.base + "/v1/accounts/tree", token=b_token)
        report.check(item, "跨机后整棵树仍完整",
                     (status, tree_count(b_tree.get("tree"))), (200, 3))
        status, b_root = request("GET", second.base + "/v1/accounts/root1", token=b_token)
        b_account = b_root.get("account") or {}
        report.check(item, "跨机后账号属性一致",
                     {k: b_account.get(k) for k in
                      ("permissions", "status", "allow_superior_key_use", "key_auth_source")},
                     {k: a_account.get(k) for k in
                      ("permissions", "status", "allow_superior_key_use", "key_auth_source")})

        # B 机成员：只能看到自己的直接下级，读不到别人的数据。
        status, mlogin = request(
            "POST", second.base + "/v1/auth/login",
            {"principal": "member1", "secret": PASSWORDS[1]},
        )
        m_token = mlogin.get("access_token") or ""
        report.check(item, "成员跨机原凭据可登录", status, 200)
        status, body = request("GET", second.base + "/v1/accounts", token=m_token)
        report.check(item, "成员跨机只看到直接下级",
                     (status, sorted(m.get("principal") for m in body.get("members") or [])),
                     (200, ["grand1"]))
        status, body = request("GET", second.base + "/v1/accounts/tree", token=m_token)
        report.check(item, "成员跨机读树 404", (status, body.get("code")), (404, "ACCOUNT_SUBTREE_DENIED"))
        status, body = request("GET", second.base + "/v1/accounts/root1", token=m_token)
        report.check(item, "成员跨机读上级 404", (status, body.get("code")), (404, "ACCOUNT_SUBTREE_DENIED"))
        status, body = request("GET", second.base + "/v1/memory/entries?scope=self", token=m_token)
        accounts = sorted(entry.get("account_id") for entry in body.get("entries") or [])
        report.check(item, "成员跨机看不到他人记忆",
                     (status, sorted(set(accounts) - {"member1"})), (200, []), str(accounts))
        # 记忆与会话真的落到了新实例：能读、能搜（不是只有账号壳子）。
        status, body = request(
            "GET", second.base + "/v1/memory/entries?scope=self&q=member-secret", token=m_token
        )
        report.check(item, "跨机成员记忆可搜",
                     (status, len(body.get("entries") or [])), (200, 1), str(body.get("entries"))[:120])
        status, body = request(
            "GET", second.base + "/v1/chat/history?session_id=member-session", token=m_token
        )
        report.check(item, "跨机会话历史可读",
                     (status, (body.get("count") or 0) > 0), (200, True), str(body)[:120])
        # D1：列表必须能发现导入的会话（只读历史但列表为空 = 界面找不到会话）。
        status, body = request("GET", second.base + "/v1/chat/sessions", token=m_token)
        names = sorted(body.get("sessions") or [])
        report.check(item, "跨机会话可在列表发现",
                     (status, "member-session" in names, "root-session" in names),
                     (200, True, False), str(names))
        status, body = request("GET", second.base + "/v1/accounts/member1/usage", token=m_token)
        report.check(item, "成员跨机用量无调用者字段", (status, "by_actor" in body), (200, False))

        # ---- 忘记主账号密码：用恢复码在 B 机重置，数据保留 ----------------- #
        old_b_token = b_token
        status, reset = request(
            "POST", second.base + "/v1/accounts/recovery/reset",
            {"recovery_code": tokens.get("recovery") or "", "password": "recovered123"},
            headers=local_proof_headers(second.data_dir),
        )
        new_token = reset.get("access_token") or reset.get("token") or ""
        report.check(item, "B 机用 A 机恢复码重置根密码", status, 200,
                     f"code={reset.get('code')}")
        status, body = request("GET", second.base + "/v1/accounts/tree", token=new_token)
        report.check(item, "重置后数据完整保留",
                     (status, tree_count(body.get("tree"))), (200, 3))
        status, body = request("GET", second.base + "/v1/auth/me", token=old_b_token)
        report.check(item, "重置后旧 B 机令牌立即失效", status, 401, f"code={body.get('code')}")
        status, body = request(
            "POST", second.base + "/v1/auth/login",
            {"principal": "root1", "secret": PASSWORDS[0]},
        )
        report.check(item, "重置后原密码不可登录", status, 401)
        status, body = request(
            "POST", second.base + "/v1/auth/login",
            {"principal": "root1", "secret": "recovered123"},
        )
        report.check(item, "重置后新密码可登录", status, 200)
        status, body = request(
            "POST", second.base + "/v1/accounts/recovery/reset",
            {"recovery_code": tokens.get("recovery") or "", "password": "recovered456"},
            headers=local_proof_headers(second.data_dir),
        )
        report.check(item, "恢复码一次性（重复使用被拒）", (status, body.get("code")),
                     (400, "ACCOUNT_RECOVERY_CODE_INVALID"))

        status, replay = request(
            "POST", second.base + "/v1/accounts/backup/import",
            {"bundle": bundle, "passphrase": BUNDLE_PASSPHRASE},
        )
        report.check(item, "重放被拒 409", (status, replay.get("code")), (409, "ACCOUNT_BACKUP_REPLAYED"))

        third = Instance("liuhao-acct-lock-")
        try:
            statuses = []
            for _ in range(6):
                status, body = request(
                    "POST", third.base + "/v1/accounts/backup/import",
                    {"bundle": bundle, "passphrase": "wrong-passphrase"},
                )
                statuses.append((status, body.get("code")))
            report.check(item, "错误口令 5 次后锁定", statuses[-1], (429, "ACCOUNT_BACKUP_LOCKED"), str(statuses))
            report.check(item, "前 5 次均为中性失败", {s[0] for s in statuses[:5]}, {400}, str(statuses[:5]))
            report.check(item, "失败文案不区分口令/损坏", neutral(statuses[0][1]), True)
        finally:
            third.stop()
    finally:
        second.stop()
    return {"bundle": exported.get("bundle") or {}, "manifest": manifest}


def file_isolation(report: Report, inst: Instance, tokens: Dict[str, Any]) -> None:
    """§3.5 工作区文件按账号分区：HTTP 读面 + 工具落盘。"""
    item = "文件分区"
    ws = os.path.join(inst.root, "workspace")
    planted = {
        "accounts/root1/root-file.txt": "root-own",
        "accounts/member1/member-file.txt": "member-own",
        "accounts/grand1/grand-file.txt": "grand-own",
        "public-file.txt": "public",
    }
    for rel, text in planted.items():
        path = os.path.join(ws, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)

    status, body = request("GET", inst.base + "/v1/files", token=tokens["member"])
    names = sorted(entry.get("name") for entry in body.get("entries") or [])
    report.check(item, "成员只见自己的空间", (status, names), (200, ["member-file.txt"]))
    report.check(item, "成员空间标记 self", (body.get("scope") or {}).get("mode"), "self")

    status, body = request(
        "GET", inst.base + "/v1/files/content?path=member-file.txt", token=tokens["member"]
    )
    report.check(item, "成员读自己的文件", (status, body.get("content")), (200, "member-own"))

    status, body = request(
        "GET", inst.base + "/v1/files/content?path=../root1/root-file.txt",
        token=tokens["member"],
    )
    report.check(item, "成员越界读上级文件 404", status, 404)
    report.check(item, "文件越权文案中性", neutral(body.get("message")), True)

    status, body = request(
        "GET", inst.base + "/v1/files/content?path=public-file.txt", token=tokens["member"]
    )
    report.check(item, "成员读公共区 404", status, 404)

    status, body = request(
        "GET", inst.base + "/v1/files?account=root1", token=tokens["member"]
    )
    report.check(item, "成员指定上级空间 404", status, 404)

    status, body = request(
        "GET", inst.base + "/v1/files?account=grand1", token=tokens["member"]
    )
    report.check(item, "成员浏览下级空间 404", status, 404)

    status, body = request("GET", inst.base + "/v1/files", token=tokens["root"])
    names = sorted(entry.get("name") for entry in body.get("entries") or [])
    report.check(
        item,
        "根可见公共区与账号目录",
        (status, "accounts" in names and "public-file.txt" in names),
        (200, True),
        str(names),
    )
    report.check(item, "根默认视图标记 all", (body.get("scope") or {}).get("mode"), "all")

    status, body = request(
        "GET", inst.base + "/v1/files?account=member1", token=tokens["root"]
    )
    names = sorted(entry.get("name") for entry in body.get("entries") or [])
    report.check(item, "根只读浏览成员空间", (status, names), (200, ["member-file.txt"]))
    report.check(item, "根浏览成员标记 subtree", (body.get("scope") or {}).get("mode"), "subtree")

    status, body = request(
        "GET", inst.base + "/v1/files/content?path=member-file.txt&account=member1",
        token=tokens["root"],
    )
    report.check(item, "根读成员文件", (status, body.get("content")), (200, "member-own"))

    status, body = request(
        "GET", inst.base + "/v1/files?account=nosuch", token=tokens["root"]
    )
    report.check(item, "根访问树外账号 404", status, 404)

    # 工具级落盘：执行侧带 owner_account_id 时必须写进 accounts/<owner>/。
    from src.ai.tools_local import _make_file_write

    writer = _make_file_write(ws)
    result = writer(path="tool-out.txt", content="tool-owned", owner_account_id="member1")
    report.check(item, "file_write 按 owner 落盘", result.get("rel_path"), "accounts/member1/tool-out.txt")
    report.check(
        item,
        "物理文件在成员空间",
        os.path.isfile(os.path.join(ws, "accounts", "member1", "tool-out.txt")),
        True,
    )
    status, body = request("GET", inst.base + "/v1/files", token=tokens["member"])
    names = sorted(entry.get("name") for entry in body.get("entries") or [])
    report.check(item, "成员可见工具产物", names, ["member-file.txt", "tool-out.txt"])


def session_isolation(
    report: Report, inst: Instance, tokens: Dict[str, Any], model_id: str
) -> None:
    """§3.3 会话列表/历史/删除按 owner 过滤，越权 404。"""
    item = "会话隔离"
    status, body = request("GET", inst.base + "/v1/chat/sessions", token=tokens["member"])
    names = sorted(body.get("sessions") or [])
    report.check(item, "成员列表只含自己的会话", (status, names), (200, ["member-session"]))

    status, body = request(
        "GET", inst.base + "/v1/chat/history?session_id=member-session", token=tokens["root"]
    )
    report.check(item, "根读成员会话名 404", status, 404)
    report.check(item, "会话越权文案中性", neutral(body.get("message")), True)

    status, body = request(
        "GET", inst.base + "/v1/chat/history?session_id=root-session", token=tokens["member"]
    )
    report.check(item, "成员读根会话名 404", status, 404)

    status, body = request(
        "DELETE", inst.base + "/v1/chat/sessions/root-session", token=tokens["member"]
    )
    report.check(item, "成员删根会话 404", status, 404)

    # 同机同名会话不能串号：两边都用 "shared"，历史必须各自独立。
    request(
        "POST", inst.base + "/v1/chat",
        {"message": "shared-root-msg", "session_id": "shared",
         "model_id": model_id, "use_knowledge": False},
        token=tokens["root"],
    )
    request(
        "POST", inst.base + "/v1/chat",
        {"message": "shared-member-msg", "session_id": "shared",
         "model_id": model_id, "use_knowledge": False},
        token=tokens["member"],
    )
    status, body = request(
        "GET", inst.base + "/v1/chat/history?session_id=shared", token=tokens["root"]
    )
    text = json.dumps(body.get("history") or [], ensure_ascii=False)
    report.check(item, "同名会话根历史不串号", "shared-root-msg" in text and "shared-member-msg" not in text, True)
    status, body = request(
        "GET", inst.base + "/v1/chat/history?session_id=shared", token=tokens["member"]
    )
    text = json.dumps(body.get("history") or [], ensure_ascii=False)
    report.check(item, "同名会话成员历史不串号", "shared-member-msg" in text and "shared-root-msg" not in text, True)


def audit_isolation(report: Report, inst: Instance, tokens: Dict[str, Any]) -> None:
    """§3.9 审计读隔离：成员只看自己，根看整树且明确标记。"""
    item = "审计读隔离"
    status, body = request("GET", inst.base + "/v1/audit/events", token=tokens["member"])
    principals = {event.get("principal_id") for event in body.get("events") or []}
    report.check(item, "成员只读到自己的事件", (status, principals in ({"member1"}, set())), (200, True), str(sorted(principals)))
    report.check(item, "成员读取范围标记 self", (body.get("read_scope") or {}).get("mode"), "self")

    status, body = request(
        "GET", inst.base + "/v1/audit/events?principal=root1", token=tokens["member"]
    )
    report.check(item, "成员指定他人 404", status, 404)
    report.check(item, "审计越权文案中性", neutral(body.get("message")), True)

    status, body = request("GET", inst.base + "/v1/audit/events", token=tokens["root"])
    report.check(item, "根读取范围标记 subtree", (body.get("read_scope") or {}).get("mode"), "subtree")
    report.check(item, "根视图标记 root_view", (body.get("read_scope") or {}).get("root_view"), True)
    others = [e for e in (body.get("events") or []) if not e.get("is_self")]
    report.check(item, "根能读到跨账号事件", len(others) > 0, True, f"{len(others)} 条非本人事件")

    member_seq = next(
        (e.get("seq") for e in (body.get("events") or [])
         if e.get("principal_id") == "member1"),
        None,
    )
    root_seq = next(
        (e.get("seq") for e in (body.get("events") or []) if e.get("is_self")),
        None,
    )
    status, body = request("GET", f"{inst.base}/v1/audit/events/{root_seq}", token=tokens["member"])
    report.check(item, "成员读根本人事件 404", status, 404)

    status, body = request("GET", f"{inst.base}/v1/audit/events/{member_seq}", token=tokens["root"])
    report.check(item, "根读成员事件 200", status, 200)
    report.check(item, "单条读取标记 subtree", (body.get("read_scope") or {}).get("mode"), "subtree")
    report.check(item, "单条标记 root_view", (body.get("read_scope") or {}).get("root_view"), True)

    status, body = request("GET", inst.base + "/v1/audit/verify", token=tokens["root"])
    report.check(item, "读隔离不破坏哈希链", (status, body.get("ok")), (200, True))


def frontend_feedback(
    report: Report, inst: Instance, tokens: Dict[str, Any]
) -> None:
    """前端联调六项（B1-B6）的真实 HTTP 验证。"""
    item = "前端联调 B1-B6"

    # B4：无令牌 / 坏令牌 → 401 + 业务码 + 中文中性文案
    status, body = request("GET", inst.base + "/v1/auth/me")
    message = str(body.get("message") or body.get("detail") or "")
    report.check(item, "B4 无令牌 401 结构化", (status, body.get("code")), (401, "AUTH_UNAUTHORIZED"))
    report.check(item, "B4 文案中文且无协议细节", "bearer" not in message.lower(), True, message)

    status, body = request(
        "GET", inst.base + "/v1/auth/me", token="not.a.jwt"
    )
    message = str(body.get("message") or body.get("detail") or "")
    report.check(item, "B4 坏令牌 401 结构化", (status, body.get("code")), (401, "AUTH_TOKEN_INVALID"))
    report.check(item, "B4 坏令牌文案不泄漏解析细节", "segment" not in message.lower(), True, message)

    # B5：登录 scope 与 /auth/me 必须同一口径（账号树内按 depth 派生）
    status, login = request(
        "POST", inst.base + "/v1/auth/login",
        {"principal": "member1", "secret": PASSWORDS[1]},
    )
    login_scope = login.get("scope")
    status, me = request("GET", inst.base + "/v1/auth/me", token=login.get("access_token") or "")
    report.check(item, "B5 登录/自述 scope 一致", (login_scope, me.get("scope")), (login_scope, login_scope))
    report.check(item, "B5 成员 scope 按 depth 派生", login_scope, "L1")
    tokens["member"] = login.get("access_token") or tokens["member"]

    # B1：本人可开关自己的 Key 共享，且不吊销自己的令牌
    status, body = request(
        "PATCH", inst.base + "/v1/accounts/member1/key-sharing",
        {"allow": True}, token=tokens["member"],
    )
    report.check(item, "B1 本人开关共享 200", status, 200, f"code={body.get('code')}")
    account = body.get("account") or {}
    report.check(item, "B1 开关已生效", account.get("allow_superior_key_use"), True)
    status, _ = request("GET", inst.base + "/v1/auth/me", token=tokens["member"])
    report.check(item, "B1 本人令牌未被吊销", status, 200)

    # B2：管理者代改共享 → 对方旧令牌失效，响应显式告知需重新登录
    status, body = request(
        "PATCH", inst.base + "/v1/accounts/member1/key-sharing",
        {"allow": False}, token=tokens["root"],
    )
    report.check(item, "B2 代改共享 200 + requires_reauth", (status, body.get("requires_reauth")), (200, True))
    report.check(item, "B2 响应含重新登录提示", "重新登录" in str(body.get("notice") or ""), True, body.get("notice"))
    status, body = request("GET", inst.base + "/v1/auth/me", token=tokens["member"])
    report.check(item, "B2 对方旧令牌即时失效", (status, body.get("code")), (401, "CREDENTIAL_STALE"))

    status, relogin = request(
        "POST", inst.base + "/v1/auth/login",
        {"principal": "member1", "secret": PASSWORDS[1]},
    )
    report.check(item, "B2 对方重新登录成功", status, 200)
    tokens["member"] = relogin.get("access_token") or tokens["member"]

    # B6：根侧树节点补齐 created_at / key_auth_source
    status, body = request("GET", inst.base + "/v1/accounts/tree", token=tokens["root"])
    nodes: List[Dict[str, Any]] = []

    def walk(node: Dict[str, Any]) -> None:
        nodes.append(node)
        for child in node.get("children") or []:
            walk(child)

    walk((body.get("tree") or {}))
    report.check(item, "B6 树节点带 created_at", all(n.get("created_at") for n in nodes), True)
    report.check(item, "B6 树节点带 key_auth_source", all(n.get("key_auth_source") for n in nodes), True)

    # B3：暂停账号登录 403 + 业务码 + 中性文案；恢复后可登录
    status, body = request(
        "PATCH", inst.base + "/v1/accounts/grand1/status",
        {"status": "suspended", "reason": "acceptance"}, token=tokens["root"],
    )
    report.check(item, "B3 暂停账号", status, 200)
    status, body = request(
        "POST", inst.base + "/v1/auth/login",
        {"principal": "grand1", "secret": PASSWORDS[2]},
    )
    message = str(body.get("message") or body.get("detail") or "")
    report.check(item, "B3 暂停登录 403 + 业务码", (status, body.get("code")), (403, "ACCOUNT_UNAVAILABLE"))
    report.check(item, "B3 暂停文案中性", neutral(message), True, message)

    status, _ = request(
        "PATCH", inst.base + "/v1/accounts/grand1/status",
        {"status": "active", "reason": "acceptance"}, token=tokens["root"],
    )
    status, relogin = request(
        "POST", inst.base + "/v1/auth/login",
        {"principal": "grand1", "secret": PASSWORDS[2]},
    )
    report.check(item, "B3 恢复后可登录", (status, relogin.get("principal")), (200, "grand1"))
    tokens["grand"] = relogin.get("access_token") or tokens["grand"]


def root_only_surface(report: Report, inst: Instance, tokens: Dict[str, Any]) -> None:
    """需求 3：子账号在任何档位下都不能通过主账号专属端点。

    "任何情况"覆盖三层：当前签发令牌、伪造更高档位（``scope=L0``）的令牌、
    以及审计整树的按 ``seq`` 直读。判权只认账号树位置，不认令牌里的声明。
    """
    item = "主账号专属端点"

    _, root_events = request(
        "GET", inst.base + "/v1/audit/events?limit=20", token=tokens["root"]
    )
    root_seq = next(
        (e.get("seq") for e in (root_events.get("events") or [])
         if e.get("principal_id") == "root1"),
        None,
    )
    report.check(item, "定位根账号审计 seq", isinstance(root_seq, int), True, f"seq={root_seq}")

    def deny_checks(subject: str, token: str) -> None:
        status, body = request("GET", inst.base + "/v1/accounts/tree", token=token)
        message = str(body.get("message") or body.get("detail") or "")
        report.check(item, f"{subject} 整树读取被拒", (status, body.get("code")),
                     (404, "ACCOUNT_SUBTREE_DENIED"), f"message={message}")
        report.check(item, f"{subject} 整树读取文案中性", neutral(message), True, message)

        status, body = request(
            "POST", inst.base + "/v1/accounts/backup/export",
            {"scope": "all", "passphrase": BUNDLE_PASSPHRASE}, token=token,
        )
        report.check(item, f"{subject} 全量导出被拒", (status, body.get("code")),
                     (403, "ACCOUNT_SCOPE_DENIED"))

        # 成员自备份（新 bundle_id）→ 已有账号树的实例必须拒绝认领
        status, exported = request(
            "POST", inst.base + "/v1/accounts/backup/export",
            {"scope": "self", "passphrase": BUNDLE_PASSPHRASE}, token=token,
        )
        bundle = exported.get("bundle") or {}
        status, body = request(
            "POST", inst.base + "/v1/accounts/backup/import",
            {"bundle": bundle, "passphrase": BUNDLE_PASSPHRASE}, token=token,
        )
        report.check(item, f"{subject} 对已建树实例导入被拒", (status, body.get("code")),
                     (409, "ACCOUNT_ALREADY_INITIALIZED"))

        status, body = request(
            "GET", inst.base + "/v1/memory/entries?scope=subtree", token=token
        )
        report.check(item, f"{subject} 记忆整树被拒", (status, body.get("code")),
                     (404, "ACCOUNT_SUBTREE_DENIED"))
        status, body = request(
            "POST", inst.base + "/v1/memory/export",
            {"scope": "subtree", "format": "json"}, token=token,
        )
        report.check(item, f"{subject} 记忆整树导出被拒", (status, body.get("code")),
                     (404, "ACCOUNT_SUBTREE_DENIED"))

        status, body = request(
            "GET", inst.base + "/v1/audit/events?principal=root1", token=token
        )
        report.check(item, f"{subject} 审计他账号读被拒", (status, body.get("code")),
                     (404, "ACCOUNT_SUBTREE_DENIED"))
        status, body = request("GET", inst.base + "/v1/audit/events", token=token)
        report.check(item, f"{subject} 审计默认范围仅为本人",
                     (body.get("read_scope") or {}).get("mode"), "self")
        if root_seq is not None:
            status, body = request(
                "GET", f"{inst.base}/v1/audit/events/{root_seq}", token=token
            )
            report.check(item, f"{subject} 审计整树按 seq 读被拒",
                         (status, body.get("code")),
                         (404, "ACCOUNT_SUBTREE_DENIED"))

    def escalation_checks(subject: str, token: str) -> None:
        """把令牌里的 ``scope`` 伪造成最高档 L0，仍不得跨过主账号专属端点。"""
        forged = mint_scope_variant(token, "L0", secret=inst.jwt_secret)
        status, body = request("GET", inst.base + "/v1/accounts/tree", token=forged)
        report.check(item, f"{subject}(scope=L0) 整树读取仍被拒",
                     (status, body.get("code")), (404, "ACCOUNT_SUBTREE_DENIED"))
        status, body = request(
            "POST", inst.base + "/v1/accounts/backup/export",
            {"scope": "all", "passphrase": BUNDLE_PASSPHRASE}, token=forged,
        )
        report.check(item, f"{subject}(scope=L0) 全量导出仍被拒",
                     (status, body.get("code")), (403, "ACCOUNT_SCOPE_DENIED"))
        status, body = request(
            "GET", inst.base + "/v1/memory/entries?scope=subtree", token=forged
        )
        report.check(item, f"{subject}(scope=L0) 记忆整树仍被拒",
                     (status, body.get("code")), (404, "ACCOUNT_SUBTREE_DENIED"))
        status, body = request(
            "GET", inst.base + "/v1/audit/events?principal=root1", token=forged
        )
        report.check(item, f"{subject}(scope=L0) 审计他账号读仍被拒",
                     (status, body.get("code")), (404, "ACCOUNT_SUBTREE_DENIED"))
        status, body = request("GET", inst.base + "/v1/audit/events", token=forged)
        report.check(item, f"{subject}(scope=L0) 审计默认范围仍仅本人",
                     (body.get("read_scope") or {}).get("mode"), "self")
        if root_seq is not None:
            status, body = request(
                "GET", f"{inst.base}/v1/audit/events/{root_seq}", token=forged
            )
            report.check(item, f"{subject}(scope=L0) 审计整树 seq 仍被拒",
                         (status, body.get("code")),
                         (404, "ACCOUNT_SUBTREE_DENIED"))

    deny_checks("member1", tokens["member"])
    deny_checks("grand1", tokens["grand"])
    escalation_checks("member1", tokens["member"])
    escalation_checks("grand1", tokens["grand"])

    # 根：以上端点全部可用，且带 root_view 标记
    status, _ = request("GET", inst.base + "/v1/accounts/tree", token=tokens["root"])
    report.check(item, "根读取整树", status, 200)
    status, _ = request(
        "POST", inst.base + "/v1/accounts/backup/export",
        {"scope": "all", "passphrase": BUNDLE_PASSPHRASE}, token=tokens["root"],
    )
    report.check(item, "根全量导出", status, 200)
    status, body = request(
        "GET", inst.base + "/v1/memory/entries?scope=subtree", token=tokens["root"]
    )
    report.check(item, "根读取记忆整树", status, 200)
    status, body = request("GET", inst.base + "/v1/audit/events", token=tokens["root"])
    report.check(item, "根读取审计整树",
                 (status, (body.get("read_scope") or {}).get("mode"),
                  (body.get("read_scope") or {}).get("root_view")),
                 (200, "subtree", True))
    if root_seq is not None:
        status, _ = request(
            "GET", f"{inst.base}/v1/audit/events/{root_seq}", token=tokens["root"]
        )
        report.check(item, "根按 seq 直读整树审计", status, 200)


SELF_PASSWORD = "selfpass123"
MINIMAL_SCOPES = ["account:audit_read", "account:read", "account:usage_read",
                  "model:manage", "model:use"]


def self_registration(
    report: Report, inst: Instance, tokens: Dict[str, Any], model_id: str
) -> None:
    """产品变更：成员自助注册（无邀请码），并实测最小权限与开关。"""
    item = "自助注册"
    base = inst.base

    # 默认开启；成员无权读/改这个开关。
    status, body = request("GET", base + "/v1/accounts/self-registration",
                           token=tokens["root"])
    report.check(item, "默认允许自助注册", (status, body.get("allow")), (200, True))
    status, body = request("GET", base + "/v1/accounts/self-registration",
                           token=tokens["member"])
    report.check(item, "成员读开关被拒", (status, body.get("code")),
                 (404, "ACCOUNT_SUBTREE_DENIED"))
    status, body = request("PATCH", base + "/v1/accounts/self-registration",
                           {"allow": False}, token=tokens["member"])
    report.check(item, "成员改开关被拒", (status, body.get("code")),
                 (404, "ACCOUNT_SUBTREE_DENIED"))

    # 主账号关闭 → 自助注册结构化拒绝；邀请码路径不受影响。
    status, body = request("PATCH", base + "/v1/accounts/self-registration",
                           {"allow": False}, token=tokens["root"])
    report.check(item, "主账号关闭自助注册", (status, body.get("allow")), (200, False))
    status, body = request("POST", base + "/v1/accounts/self-register",
                           {"principal": "self1", "password": SELF_PASSWORD})
    message = str(body.get("message") or "")
    report.check(item, "关闭后自助注册被拒", (status, body.get("code")),
                 (403, "ACCOUNT_SELF_REGISTER_DISABLED"))
    report.check(item, "关闭拒绝文案中性", neutral(message), True, message)

    status, invited = request(
        "POST", base + "/v1/accounts",
        {"principal": "invitee1", "role": "viewer"}, token=tokens["root"],
    )
    report.check(item, "关闭期间主账号建成员仍可用", status, 200,
                 f"code={bool(invited.get('activation_code'))}")
    status, activated = request(
        "POST", base + "/v1/accounts/activate",
        {"principal": "invitee1", "code": invited.get("activation_code") or "",
         "password": "inviteepass123"},
    )
    report.check(item, "邀请码激活仍可用",
                 (status, isinstance(activated.get("access_token"), str)), (200, True))

    status, body = request("PATCH", base + "/v1/accounts/self-registration",
                           {"allow": True}, token=tokens["root"])
    report.check(item, "恢复允许自助注册", (status, body.get("allow")), (200, True))

    # 自助注册成功：请求体故意夹带父子/深度/权限字段，必须被忽略。
    status, created = request(
        "POST", base + "/v1/accounts/self-register",
        {"principal": "self1", "password": SELF_PASSWORD, "display_name": "自助成员",
         "parent_id": "member1", "parent_account_id": "member1",
         "depth": 0, "root_id": "self1",
         "permissions": ["account:grant", "account:manage_children"]},
    )
    self_token = created.get("access_token") or ""
    account = created.get("account") or {}
    report.check(item, "自助注册成功", (status, account.get("principal")), (200, "self1"))
    report.check(item, "自助注册直接返回令牌", isinstance(self_token, str) and bool(self_token), True)
    report.check(item, "自助注册 notice 不含层级词",
                 neutral(created.get("notice")), True, str(created.get("notice")))
    report.check(item, "请求体父子/深度/权限被忽略",
                 sorted(account.get("permissions") or []), MINIMAL_SCOPES)

    # 位置：根的直接成员（一级），不是根。
    status, body = request("GET", base + "/v1/accounts/tree", token=tokens["root"])
    tree = body.get("tree") or {}
    direct = {str(child.get("principal")) for child in tree.get("children") or []}
    report.check(item, "自助账号挂在根的第一层", "self1" in direct, True, str(sorted(direct)))
    report.check(item, "根可见自助账号", tree.get("principal"), "root1")

    # 最小权限实测：跨账号读/写全部 404 或中性拒绝。
    status, body = request("GET", base + "/v1/accounts/tree", token=self_token)
    report.check(item, "自助账号读整树被拒", (status, body.get("code")),
                 (404, "ACCOUNT_SUBTREE_DENIED"))
    status, body = request("POST", base + "/v1/accounts",
                           {"principal": "selfchild", "role": "viewer"}, token=self_token)
    report.check(item, "自助账号不能建下级", (status, body.get("code")),
                 (404, "ACCOUNT_SUBTREE_DENIED"))
    status, body = request("GET", base + "/v1/accounts/member1", token=self_token)
    report.check(item, "自助账号读他人账号被拒", status, 404)
    status, body = request("GET", base + "/v1/accounts/member1/usage", token=self_token)
    report.check(item, "自助账号读他人用量被拒", status, 404)
    status, body = request("GET", base + "/v1/memory/entries?scope=subtree", token=self_token)
    report.check(item, "自助账号读他人记忆被拒", (status, body.get("code")),
                 (404, "ACCOUNT_SUBTREE_DENIED"))
    status, body = request("POST", base + "/v1/memory/export",
                           {"scope": "subtree", "format": "json"}, token=self_token)
    report.check(item, "自助账号导出他人记忆被拒", (status, body.get("code")),
                 (404, "ACCOUNT_SUBTREE_DENIED"))
    status, body = request("GET", base + "/v1/files?account=root1", token=self_token)
    report.check(item, "自助账号读他人文件被拒", status, 404)
    status, body = request("GET", base + "/v1/chat/history?session_id=root-session",
                           token=self_token)
    report.check(item, "自助账号读他人会话被拒", status, 404)
    status, body = request("GET", base + "/v1/audit/events?principal=root1", token=self_token)
    report.check(item, "自助账号读他人审计被拒", (status, body.get("code")),
                 (404, "ACCOUNT_SUBTREE_DENIED"))
    status, body = request("GET", base + "/v1/audit/events", token=self_token)
    report.check(item, "自助账号审计范围仅本人",
                 (body.get("read_scope") or {}).get("mode"), "self")

    # 成员可见文案全量扫描：所有 detail/notice/message 均不得出现层级词。
    wording_calls = [
        ("读整树", "GET", "/v1/accounts/tree", None),
        ("读他人账号", "GET", "/v1/accounts/root1", None),
        ("建号", "POST", "/v1/accounts", {"principal": "wording-child"}),
        ("读开关", "GET", "/v1/accounts/self-registration", None),
        ("改开关", "PATCH", "/v1/accounts/self-registration", {"allow": True}),
        ("跨主体审计", "GET", "/v1/audit/events?principal=root1", None),
        ("子树记忆", "GET", "/v1/memory/entries?scope=subtree", None),
        ("整树备份", "POST", "/v1/accounts/backup/export",
         {"scope": "all", "passphrase": "wording-passphrase"}),
    ]
    wording_hits: List[Dict[str, str]] = []
    for label, method, path, payload in wording_calls:
        status, body = request(method, base + path, payload, token=self_token)
        for text in user_texts(body):
            for word in NEUTRAL_WORDS:
                if word in text:
                    wording_hits.append({"call": label, "word": word, "text": text})
    report.check(item, "成员可见文案禁用词全量扫描", wording_hits, [],
                 json.dumps(wording_hits[:3], ensure_ascii=False))

    # 他人 Key / 他人模型：不可见、不可改。
    status, body = request("GET", base + "/v1/models", token=self_token)
    report.check(item, "自助账号看不到他人模型",
                 (status, body.get("count"), body.get("models")), (200, 0, []))
    status, body = request("PUT", f"{base}/v1/models/{model_id}",
                           {"name": "hijack"}, token=self_token)
    report.check(item, "自助账号改他人 Key 被拒",
                 (status, body.get("code")), (403, "ACCOUNT_KEY_NOT_OWNED"))

    # 主账号可见并可停用；停用后登录中性拒绝。
    status, body = request("GET", base + "/v1/accounts", token=tokens["root"])
    entry = next((m for m in body.get("members") or []
                  if m.get("principal") == "self1"), None)
    report.check(item, "主账号列表可见自助账号", bool(entry), True)
    report.check(item, "主账号可见其 Key 未授权",
                 (entry or {}).get("allow_superior_key_use"), False)
    status, body = request("PATCH", base + "/v1/accounts/self1/status",
                           {"status": "suspended", "reason": "acceptance"},
                           token=tokens["root"])
    report.check(item, "主账号可停用自助账号", status, 200)
    status, body = request("POST", base + "/v1/auth/login",
                           {"principal": "self1", "secret": SELF_PASSWORD})
    message = str(body.get("message") or "")
    report.check(item, "停用后登录被中性拒绝",
                 (status, body.get("code")), (403, "ACCOUNT_UNAVAILABLE"))
    report.check(item, "停用文案中性", neutral(message), True, message)


def display_fields(
    report: Report, inst: Instance, tokens: Dict[str, Any], model_id: str
) -> None:
    """界面可读字段：模型清单 / 事件列表 / 告警与健康（真实 HTTP 形态）。"""
    item = "界面可读字段"
    base = inst.base

    status, body = request("GET", base + "/v1/health")
    provider = body.get("ai_provider") or {}
    report.check(item, "健康：一句人话 + 中文状态",
                 (status, bool(body.get("user_message")), body.get("status_label"),
                  machine_free(body.get("user_message"))),
                 (200, True, "正常", True))
    report.check(item, "健康：服务商展示名与状态",
                 (bool(provider.get("display_name")),
                  "·" in str(provider.get("display_status")),
                  bool(provider.get("status_label")), provider.get("type"),
                  machine_free(provider.get("display_status"))),
                 (True, True, True, provider.get("type"), True),
                 str(provider.get("display_status")))

    status, body = request("GET", base + "/v1/models", token=tokens["root"])
    models = body.get("models") or []
    target = next((m for m in models if m.get("id") == model_id), None)
    report.check(item, "模型清单：新增字段齐全",
                 (status, bool(models)), (200, True))
    report.check(item, "模型：展示名/服务商/状态",
                 (bool((target or {}).get("display_name")),
                  bool((target or {}).get("provider_display_name")),
                  "·" in str((target or {}).get("display_status")),
                  (target or {}).get("status_label") in ("可用", "已启用", "未启用")),
                 (True, True, True, True), str((target or {}).get("display_status")))
    report.check(item, "模型：旧字段保留",
                 bool((target or {}).get("provider"))
                 and (target or {}).get("model") is not None,
                 True, str((target or {}).get("provider")))

    status, body = request("GET", base + "/v1/events/history?limit=20",
                           token=tokens["root"])
    events = body.get("events") or []
    report.check(item, "事件列表：标题与状态为中文",
                 (status, bool(events),
                  all(e.get("display_title") and e.get("status_label") for e in events),
                  all(machine_free(e.get("display_title")) and machine_free(e.get("status_label"))
                      for e in events)),
                 (200, True, True, True))
    sample = events[0] if events else {}
    report.check(item, "事件：标题不是裸英文枚举",
                 ("success" not in str(sample.get("display_title"))
                  and "blocked" not in str(sample.get("display_title"))),
                 True, str(sample.get("display_title")))
    report.check(item, "事件：旧字段保留",
                 bool(sample.get("action") or sample.get("event_type")), True,
                 str(sample.get("action") or sample.get("event_type")))

    status, body = request("GET", base + "/v1/audit/events?limit=5",
                           token=tokens["root"])
    audits = body.get("events") or []
    report.check(item, "审计事件：也有 display_title",
                 (status, bool(audits), all(e.get("display_title") for e in audits)),
                 (200, True, True))

    status, body = request("GET", base + "/v1/alerts/rules", token=tokens["root"])
    rules = body.get("rules") or []
    report.check(item, "告警规则：人话字段齐全",
                 (status, bool(rules),
                  all(r.get("severity_label") and r.get("status_label")
                      and r.get("user_message") and machine_free(r.get("user_message"))
                      for r in rules)),
                 (200, True, True))
    status, body = request("GET", base + "/v1/alerts", token=tokens["root"])
    # 真机上验收自身会把 CPU/延迟顶到阈值上方，"正在告警"是合同内的合法状态
    # （display.py: firing -> 正在告警）；这里只校验"顶层是机器无关的人话"，
    # 同时把 firing_count 带进证据，避免把真实告警当成文案缺陷。
    label = body.get("status_label")
    report.check(
        item, "告警列表：顶层一句人话",
        (status, bool(body.get("user_message")), label in ("正常", "正在告警"),
         machine_free(body.get("user_message"))),
        (200, True, True, True),
        f"label={label} firing={body.get('firing_count')}",
    )

    status, body = request("GET", base + "/v1/ready")
    checks = body.get("checks") or {}
    report.check(item, "就绪检查：逐项 user_message + technical_detail",
                 (all(c.get("user_message") and c.get("technical_detail")
                      and machine_free(c.get("user_message")) for c in checks.values()),
                  bool(body.get("user_message")), machine_free(body.get("user_message"))),
                 (True, True, True))

    status, body = request("GET", base + "/v1/production/preflight", token=tokens["root"])
    findings = body.get("findings") or []
    report.check(item, "预检：人话标题/说明，原始串只在技术细节",
                 (status, bool(body.get("user_message")),
                  all(f.get("display_title") and f.get("user_message")
                      and machine_free(f.get("display_title"))
                      and machine_free(f.get("user_message"))
                      and f.get("message") not in f.get("user_message")
                      for f in findings)),
                 (200, True, True), f"findings={len(findings)}")


def deletion(report: Report, inst: Instance, tokens: Dict[str, Any]) -> None:
    item = "删除"
    status, body = request(
        "PATCH", inst.base + "/v1/accounts/member1/status",
        {"status": "deleted", "reason": "acceptance"}, token=tokens["root"],
    )
    report.check(item, "删除有下级的成员 409", (status, body.get("code")), (409, "ACCOUNT_DELETE_HAS_CHILDREN"))

    status, body = request(
        "PATCH", inst.base + "/v1/accounts/grand1/status",
        {"status": "deleted", "reason": "acceptance"}, token=tokens["root"],
    )
    report.check(item, "删除末级账号", status, 200, f"keys={sorted((body or {}).keys())[:6]}")
    status, body = request("GET", inst.base + "/v1/accounts", token=tokens["grand"])
    report.check(item, "被删账号令牌立即失效", status, 403, f"code={body.get('code')}")

    status, body = request(
        "PATCH", inst.base + "/v1/accounts/root1/status",
        {"status": "deleted", "reason": "acceptance"}, token=tokens["root"],
    )
    report.check(item, "根账号不可删除 409", (status, body.get("code")), (409, "ACCOUNT_DELETE_ROOT_FORBIDDEN"))


def failure_paths(report: Report, inst: Instance, tokens: Dict[str, Any]) -> None:
    """三条失败路径：吊销、停用、删除有下级；文案必须中性。"""
    item = "失败路径"

    # 1) 令牌被吊销（主动登出）→ 401，提示不透露账号关系。
    status, body = request(
        "POST", inst.base + "/v1/auth/login",
        {"principal": "member1", "secret": PASSWORDS[1]},
    )
    revoke_token = body.get("access_token") or ""
    status, _ = request("POST", inst.base + "/v1/auth/logout", token=revoke_token)
    report.check(item, "登出成功", status, 200)
    status, body = request("GET", inst.base + "/v1/auth/me", token=revoke_token)
    report.check(item, "吊销后旧令牌 401", (status, body.get("code")), (401, "AUTH_TOKEN_INVALID"))
    report.check(item, "吊销提示中性",
                 neutral(json.dumps(body, ensure_ascii=False)), True, str(body))

    # 2) 停用账号 → 旧令牌立刻 403；用一次性临时账号，不影响主树。
    status, created = request(
        "POST", inst.base + "/v1/accounts",
        {"principal": "temp1", "role": "viewer"}, token=tokens["root"],
    )
    status, activated = request(
        "POST", inst.base + "/v1/accounts/activate",
        {"principal": "temp1", "code": created.get("activation_code") or "",
         "password": "temppass123"},
    )
    temp_token = activated.get("token") or ""
    status, _ = request("GET", inst.base + "/v1/auth/me", token=temp_token)
    report.check(item, "临时账号初始可用", status, 200)
    status, _ = request(
        "PATCH", inst.base + "/v1/accounts/temp1/status",
        {"status": "suspended", "reason": "acceptance"}, token=tokens["root"],
    )
    report.check(item, "停用账号", status, 200)
    status, body = request("GET", inst.base + "/v1/auth/me", token=temp_token)
    report.check(item, "停用后旧令牌立即失效",
                 (status, body.get("code")), (403, "ACCOUNT_UNAVAILABLE"))
    report.check(item, "停用提示中性",
                 neutral(json.dumps(body, ensure_ascii=False)), True, str(body))
    status, _ = request(
        "PATCH", inst.base + "/v1/accounts/temp1/status",
        {"status": "deleted", "reason": "acceptance"}, token=tokens["root"],
    )
    report.check(item, "临时账号可清理", status, 200)

    # 3) 删除有下级的账号 → 一律 409（不做级联、不猜测意图）。
    status, body = request(
        "PATCH", inst.base + "/v1/accounts/member1/status",
        {"status": "deleted", "reason": "acceptance"}, token=tokens["root"],
    )
    report.check(item, "删除有下级 409",
                 (status, body.get("code")), (409, "ACCOUNT_DELETE_HAS_CHILDREN"))
    report.check(item, "删除拒绝文案中性",
                 neutral(json.dumps(body, ensure_ascii=False)), True, str(body))


def plaintext_scan(report: Report, dirs: List[str], export_files: List[str]) -> None:
    item = "明文扫描"
    needles = PASSWORDS + MODEL_KEYS + [BUNDLE_PASSPHRASE]
    hits: List[Tuple[str, str]] = []
    for base in dirs:
        for current, _dirs, files in os.walk(base):
            for name in files:
                path = os.path.join(current, name)
                try:
                    with open(path, "rb") as handle:
                        raw = handle.read()
                except OSError:
                    continue
                for needle in needles:
                    if needle.encode("utf-8") in raw:
                        hits.append((path, needle))
    for path in export_files:
        try:
            with open(path, "rb") as handle:
                raw = handle.read()
        except OSError:
            continue
        for needle in needles:
            if needle.encode("utf-8") in raw:
                hits.append((path, needle))
    report.check(item, "导出包/日志/库中 0 条明文", hits, [], json.dumps(hits[:5], ensure_ascii=False))


def _walk_files(base: str) -> List[str]:
    found: List[str] = []
    for current, _dirs, files in os.walk(base):
        for name in files:
            found.append(os.path.join(current, name))
    return found


def _read(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def _find_named(base: str, name: str) -> List[str]:
    return [path for path in _walk_files(base) if os.path.basename(path) == name]


def _protected_name(rel: str) -> bool:
    lowered = rel.replace("\\", "/").lower()
    base = lowered.rsplit("/", 1)[-1]
    return (
        base.startswith("recovery_code")
        or base.startswith("recovery_handoff")
        or base in ("auth_secrets.json", "model_config_key.bin")
    )


def upgrade_snapshot_artifact(report: Report, inst: Instance) -> None:
    """真实升级备份/导出产物：打包层过滤 + 备份后物理断言。

    直接调用生产函数（不是单测桩）：``backup_data`` 遍历并跳过受保护文件，
    ``export_data_snapshot`` 在复制层再过滤一次，最后对导出目录逐文件扫描。
    另外解压 ``code.zip`` 断言代码包内没有数据目录里的受保护文件。
    """
    item = "备份导出排除"
    canary = "RC-EXPORT-CANARY-0001"
    env = dict(inst.env)
    out_dir = os.path.join(inst.root, "artifact-check")
    os.makedirs(out_dir, exist_ok=True)

    # 1) 用一个"待取走"的加密交接文件让真实数据目录里确实存在恢复码文件。
    writer = (
        "from src.gateway import recovery_handoff as rh;"
        f"print(rh.write_handoff('root1', '{canary}')['protection']);"
        "print(rh.handoff_path())"
    )
    proc = subprocess.run(
        [PY, "-c", writer], cwd=REPO, env=env, capture_output=True, text=True, timeout=120
    )
    lines = proc.stdout.strip().splitlines()
    protection = lines[0] if lines else ""
    handoff_path = lines[1] if len(lines) > 1 else ""
    report.check(item, "备份前存在加密恢复码文件", bool(handoff_path) and os.path.isfile(handoff_path), True,
                 f"rc={proc.returncode} out={proc.stdout.strip()} err={proc.stderr[-300:]}")
    if os.path.isfile(handoff_path):
        raw = _read(handoff_path)
        report.check(item, "待备份的恢复码文件是密文", canary.encode() in raw, False,
                     protection)
        report.check(item, "待备份文件不含明文前缀 RC-", b"RC-" in raw, False)

    # 2) 真实生产函数：数据备份 → 快照导出 → 代码包（可解压）。
    script = r"""
import json, sys
from pathlib import Path
from src.upgrade.backups import backup_code, backup_data, export_data_snapshot
app_root, data_dir, out, repo = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
backup = backup_data(app_root, data_dir, [app_root / "auth_secrets.json"], out / "backup")
snapshot = export_data_snapshot(backup.backup_dir, out / "snapshot")
code = backup_code(repo, ("config",), out / "code")
manifest = json.loads(backup.manifest_path.read_text(encoding="utf-8"))
snapshot_manifest = json.loads(snapshot.manifest_path.read_text(encoding="utf-8"))
print(json.dumps({
    "backup_dir": str(backup.backup_dir),
    "snapshot_dir": str(snapshot.destination),
    "zip": str(code.zip_path),
    "excluded": manifest.get("excluded_protected"),
    "snapshot_excluded": snapshot_manifest.get("excluded_protected"),
    "entries": [e.get("relpath") for e in manifest.get("entries", [])],
    "snapshot_entries": [e.get("relpath") for e in snapshot_manifest.get("entries", [])],
}, ensure_ascii=False))
"""
    proc = subprocess.run(
        [PY, "-c", script, str(Path(inst.root)), inst.data_dir, out_dir, str(REPO)],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=300,
    )
    report.check(item, "生产备份/导出函数执行成功", proc.returncode, 0, proc.stderr[-400:])
    meta: Dict[str, Any] = {}
    if proc.returncode == 0:
        meta = json.loads(proc.stdout.strip().splitlines()[-1])

    # 3) 备份清单与备份目录：受保护文件被计数跳过，物理目录里也没有。
    report.check(item, "备份清单记录了排除项", (meta.get("excluded") or 0) >= 2, True,
                 str(meta.get("excluded")))
    backup_dir = meta.get("backup_dir") or ""
    leftovers = [path for path in _walk_files(backup_dir) if _protected_name(
        os.path.relpath(path, backup_dir))]
    report.check(item, "备份目录无恢复码/凭据文件", leftovers, [])
    leaked_payloads = [
        needle for needle in (canary, PASSWORDS[0], *MODEL_KEYS)
        if any(needle.encode("utf-8") in _read(path) for path in _walk_files(backup_dir))
    ]
    report.check(item, "备份内容无恢复码/明文凭据", leaked_payloads, [])

    # 4) 真实导出产物：清单过滤 + 打包后物理断言；目录等价于解压后的包。
    report.check(item, "导出清单记录了排除项", (meta.get("snapshot_excluded") or 0) >= 1, True,
                 str(meta.get("snapshot_excluded")))
    snapshot_dir = meta.get("snapshot_dir") or ""
    snapshot_leftovers = [path for path in _walk_files(snapshot_dir) if _protected_name(
        os.path.relpath(path, snapshot_dir))]
    report.check(item, "导出包无恢复码/凭据文件", snapshot_leftovers, [])
    snapshot_payloads = [
        needle for needle in (canary, PASSWORDS[0], *MODEL_KEYS)
        if any(needle.encode("utf-8") in _read(path) for path in _walk_files(snapshot_dir))
    ]
    report.check(item, "导出包内容无恢复码/明文凭据", snapshot_payloads, [])

    # 5) 解压断言：code.zip 的成员里不能出现数据目录的受保护文件。
    zip_path = meta.get("zip") or ""
    members: List[str] = []
    if zip_path and os.path.isfile(zip_path):
        with zipfile.ZipFile(zip_path) as archive:
            members = archive.namelist()
            packed = [name for name in members if _protected_name(name)]
            code_mentions = canary.encode("utf-8") in b"".join(
                archive.read(name) for name in members
            )
        report.check(item, "代码包无数据受保护文件", packed, [])
        report.check(item, "代码包解压内容无恢复码值", code_mentions, False)
    else:
        report.check(item, "代码包可解压", bool(members), True, zip_path)

    if handoff_path and os.path.isfile(handoff_path):
        os.remove(handoff_path)


def legacy_recovery_migration(report: Report) -> None:
    """老用户旧明文文件：启动迁移为密文、旧文件消失、码仍可用。"""
    item = "旧明文迁移"
    legacy_code = ""
    legacy = Instance("liuhao-acct-legacy-")
    try:
        status, registed = request(
            "POST", legacy.base + "/v1/accounts/register",
            {"principal": "legacy-root", "password": "legacypass123", "display_name": "老用户"},
        )
        legacy_code = registed.get("recovery_code") or ""
        report.check(item, "老账号注册并拿到恢复码", (status, bool(legacy_code)), (200, True))

        # 停机写入"旧版明文格式"文件，再重启触发启动迁移。
        legacy.stop(cleanup=False)
        path = os.path.join(legacy.data_dir, "config", "recovery_code.txt")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(
                f"{legacy_code}\n# LiuHao 本机恢复码 · 账号 legacy-root\n# 旧版明文格式\n"
            )
        legacy.start()

        raw = _read(path)
        report.check(item, "迁移后文件仍在", os.path.isfile(path), True)
        report.check(item, "迁移后不含恢复码明文", legacy_code.encode() in raw, False,
                     f"size={len(raw)}")
        report.check(item, "旧明文标记已消失", "旧版明文格式".encode() in raw, False)
        try:
            document = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            document = {}
        report.check(item, "迁移后文件是 DPAPI 密文",
                     document.get("protection"), "dpapi-current-user", str(document)[:120])
        acl = subprocess.run(["icacls", path], capture_output=True, text=True).stdout
        bad_acl = [marker for marker in ("(I)", "Everyone", "BUILTIN\\Users") if marker in acl]
        report.check(item, "迁移文件 ACL 仅当前用户", bad_acl, [], acl.strip()[:120])

        # 回环 + 桌面端一次性证明才允许取码（同机其他账户没有证明）。
        status, body = request(
            "POST", legacy.base + "/v1/accounts/recovery/local",
            headers=local_proof_headers(legacy.data_dir),
        )
        report.check(item, "本机取用旧码成功", (status, body.get("recovered"), body.get("recovery_code")),
                     (200, True, legacy_code))
        report.check(item, "取用后文件消失", os.path.exists(path), False)
        status, body = request(
            "POST", legacy.base + "/v1/accounts/recovery/local",
            headers=local_proof_headers(legacy.data_dir),
        )
        report.check(item, "再取用失败", (status, body.get("code")),
                     (403, "ACCOUNT_RECOVERY_UNAVAILABLE"))
        status, body = request(
            "POST", legacy.base + "/v1/accounts/recovery/reset",
            {"recovery_code": legacy_code, "password": "legacynew123"},
            headers=local_proof_headers(legacy.data_dir),
        )
        report.check(item, "旧码重置密码成功", status, 200)
        status, body = request(
            "POST", legacy.base + "/v1/auth/login",
            {"principal": "legacy-root", "secret": "legacypass123"},
        )
        report.check(item, "迁移后旧密码不可登录", status, 401)
        status, body = request(
            "POST", legacy.base + "/v1/auth/login",
            {"principal": "legacy-root", "secret": "legacynew123"},
        )
        report.check(item, "迁移后新密码可登录", status, 200)
    finally:
        legacy.stop()


def remote_recovery_switch(report: Report) -> None:
    """远程开关：默认关（见 recovery_handoff_chain），打开后行为变化且日志留痕。"""
    item = "远程恢复开关"
    switch = Instance("liuhao-acct-switch-", env_extra={"LIUHAO_ALLOW_REMOTE_RECOVERY": "1"})
    try:
        status, registed = request(
            "POST", switch.base + "/v1/accounts/register",
            {"principal": "switch-root", "password": "switchpass123", "display_name": "远程开关"},
        )
        code = registed.get("recovery_code") or ""
        report.check(item, "开关实例注册根账号", status, 200, f"code_len={len(code)}")

        forwarded = {"X-Forwarded-For": "203.0.113.9"}
        status, body = request(
            "POST", switch.base + "/v1/accounts/recovery/reset",
            {"recovery_code": code, "password": "switchnew123"},
            headers=forwarded,
        )
        report.check(item, "开关打开后非本机重置被接受", status, 200, str(body)[:120])
        status, body = request(
            "POST", switch.base + "/v1/auth/login",
            {"principal": "switch-root", "secret": "switchnew123"},
        )
        report.check(item, "远程重置后的新密码可登录", status, 200)

        status, body = request(
            "POST", switch.base + "/v1/accounts/recovery/local", headers=forwarded
        )
        report.check(item, "开关打开后取码仍限本机", (status, body.get("code")),
                     (403, "ACCOUNT_RECOVERY_LOCAL_ONLY"))

        report.check(item, "启动日志记录已允许非本机恢复",
                     "已允许非本机恢复" in switch.tail(), True, switch.tail()[-300:])
    finally:
        switch.stop()


def recovery_handoff_chain(report: Report, inst: Instance, tokens: Dict[str, Any]) -> None:
    """主实例上的恢复码全链路（真实 HTTP）：拒绝非本机 → 用码重置 → 一次性。"""
    item = "恢复码链路"
    code0 = tokens.get("recovery") or ""
    forwarded = {"X-Forwarded-For": "203.0.113.9"}

    # 默认关闭远程：任何带非回环 XFF 的请求都必须被拒，且不能消费掉有效码。
    status, body = request(
        "POST", inst.base + "/v1/accounts/recovery/local", headers=forwarded
    )
    report.check(item, "默认非本机取码被拒", (status, body.get("code")),
                 (403, "ACCOUNT_RECOVERY_LOCAL_ONLY"))
    status, body = request(
        "POST", inst.base + "/v1/accounts/recovery/reset",
        {"recovery_code": code0, "password": "handoff-first-1"},
        headers=forwarded,
    )
    report.check(item, "默认非本机重置被拒", (status, body.get("code")),
                 (403, "ACCOUNT_RECOVERY_LOCAL_ONLY"))

    # 首次本机重置：成功消费 code0，旧密码/旧令牌失效，重放被拒。
    status, body = request(
        "POST", inst.base + "/v1/accounts/recovery/reset",
        {"recovery_code": code0, "password": "handoff-first-1"},
        headers=local_proof_headers(inst.data_dir),
    )
    report.check(item, "本机用码重置成功", status, 200, str(body)[:120])
    status, body = request("GET", inst.base + "/v1/auth/me", token=tokens["root"])
    report.check(item, "重置后旧令牌失效", status, 401)
    status, body = request(
        "POST", inst.base + "/v1/auth/login",
        {"principal": "root1", "secret": PASSWORDS[0]},
    )
    report.check(item, "重置后旧密码不可登录", status, 401)
    status, body = request(
        "POST", inst.base + "/v1/accounts/recovery/reset",
        {"recovery_code": code0, "password": "handoff-replay-1"},
        headers=local_proof_headers(inst.data_dir),
    )
    report.check(item, "同一码不可重放", (status, body.get("code")),
                 (400, "ACCOUNT_RECOVERY_CODE_INVALID"))

    # 没有有效码时 /recovery/local 生成新码：走密文写入、取用即焚。
    status, body = request(
        "POST", inst.base + "/v1/accounts/recovery/local",
        headers=local_proof_headers(inst.data_dir),
    )
    raw = json.dumps(body, ensure_ascii=False)
    report.check(item, "本机生成本机新码", (status, body.get("written"), body.get("recovered")),
                 (200, True, False), str(body)[:120])
    report.check(item, "生成走 DPAPI 密文路径", body.get("protection"), "dpapi-current-user")
    report.check(item, "响应只给文件名", body.get("file"), "recovery_code.txt")
    report.check(item, "响应不含绝对路径", ("/" in raw or "\\" in raw), False, raw[:160])
    leftovers = _find_named(inst.data_dir, "recovery_code.txt")
    report.check(item, "取用后交接文件消失", leftovers, [])
    code1 = body.get("recovery_code") or ""
    report.check(item, "返回新码可用", bool(code1), True, f"len={len(code1)}")

    status, body = request(
        "POST", inst.base + "/v1/accounts/recovery/local",
        headers=local_proof_headers(inst.data_dir),
    )
    report.check(item, "再取用失败", (status, body.get("code")),
                 (403, "ACCOUNT_RECOVERY_UNAVAILABLE"))

    status, body = request(
        "POST", inst.base + "/v1/accounts/recovery/reset",
        {"recovery_code": code1, "password": "handoff-second-1"},
        headers=local_proof_headers(inst.data_dir),
    )
    report.check(item, "新码重置密码成功", status, 200)
    status, body = request(
        "POST", inst.base + "/v1/auth/login",
        {"principal": "root1", "secret": "handoff-first-1"},
    )
    report.check(item, "第二次重置后前一密码失效", status, 401)
    status, body = request(
        "POST", inst.base + "/v1/auth/login",
        {"principal": "root1", "secret": "handoff-second-1"},
    )
    report.check(item, "第二次重置后新密码可登录", status, 200)
    status, body = request(
        "POST", inst.base + "/v1/accounts/recovery/reset",
        {"recovery_code": code1, "password": "handoff-replay-2"},
        headers=local_proof_headers(inst.data_dir),
    )
    report.check(item, "新码同样不可重放", (status, body.get("code")),
                 (400, "ACCOUNT_RECOVERY_CODE_INVALID"))

    # 回环但无桌面端证明：必须被拒（同机其他 Windows 账户正是这种情形）。
    status, body = request("POST", inst.base + "/v1/accounts/recovery/local")
    report.check(item, "回环但缺少一次性证明被拒", (status, body.get("code")),
                 (403, "ACCOUNT_RECOVERY_PROOF_REQUIRED"))
    # 同一枚交接令牌只能用一次（重放被拒）。
    used = local_proof_headers(inst.data_dir)
    request("POST", inst.base + "/v1/accounts/recovery/local", headers=used)
    status, body = request(
        "POST", inst.base + "/v1/accounts/recovery/local", headers=used
    )
    report.check(item, "交接令牌不可重放", (status, body.get("code")),
                 (403, "ACCOUNT_RECOVERY_PROOF_REQUIRED"))


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #

def main() -> int:
    report = Report()
    inst = Instance("liuhao-acct-main-")
    dirs = [inst.root]
    export_files: List[str] = []
    try:
        tokens = bootstrap(report, inst)
        direct_members(report, inst, tokens)
        enumeration(report, inst, tokens)
        usage = usage_double_entry(report, inst, tokens)
        memory_visibility(report, inst, tokens)
        employee_adoption(report, inst, tokens)
        backup = backup_roundtrip(report, inst, tokens)
        upgrade_snapshot_artifact(report, inst)
        memory_adoption(report, inst, tokens)
        file_isolation(report, inst, tokens)
        session_isolation(report, inst, tokens, usage["model_id"])
        audit_isolation(report, inst, tokens)
        frontend_feedback(report, inst, tokens)
        scope_equivalence(report, inst, tokens)
        root_only_surface(report, inst, tokens)
        self_registration(report, inst, tokens, usage["model_id"])
        display_fields(report, inst, tokens, usage["model_id"])

        export_path = os.path.join(inst.root, "export-all.json")
        with open(export_path, "w", encoding="utf-8") as handle:
            json.dump(
                {"bundle": backup["bundle"], "manifest": backup["manifest"]},
                handle, ensure_ascii=False,
            )
        export_files.append(export_path)

        failure_paths(report, inst, tokens)
        deletion(report, inst, tokens)
        # 恢复码链路最后跑：它会改掉根密码并使既有令牌失效。
        legacy_recovery_migration(report)
        remote_recovery_switch(report)
        recovery_handoff_chain(report, inst, tokens)
        plaintext_scan(report, dirs, export_files)
    finally:
        inst.stop()
        print("\ntemp dir recycled:", inst.root)

    failed = report.failed()
    print("\n=== 汇总 ===")
    print(json.dumps(
        {"total": len(report.rows), "failed": len(failed), "failures": failed},
        ensure_ascii=False, indent=2,
    ))
    print("\n| 检查项 | 结果 | 关键证据 |")
    print("|---|---|---|")
    for row in report.rows:
        print(f"| {row['item']} · {row['check']} | {'🟢' if row['ok'] else '🔴'} | {row['evidence'] or row['got']} |")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
