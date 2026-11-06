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

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PY = os.path.join(REPO, ".venv", "Scripts", "python.exe")
sys.path.insert(0, REPO)

PASSWORDS = ["rootpass123", "memberpass123", "grandpass123"]
MODEL_KEYS = ["sk-root-plain-key-123456", "sk-member-plain-key-abcdef"]
BUNDLE_PASSPHRASE = "bundle-passphrase-123"
NEUTRAL_WORDS = ("上级", "子账号", "层级", "越级", "depth", "parent_id")


# --------------------------------------------------------------------------- #
# 基础设施
# --------------------------------------------------------------------------- #

def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request(
    method: str, url: str, payload: Any = None, token: Optional[str] = None
) -> Tuple[int, Any]:
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    if token:
        headers["Authorization"] = f"Bearer {token}"
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
    def __init__(self, prefix: str) -> None:
        self.root = tempfile.mkdtemp(prefix=prefix)
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        env = os.environ.copy()
        for name in ("LIUHAO_HUMAN_IDENTITIES_DB", "LIUHAO_DATA_DIR"):
            env.pop(name, None)
        env.update(
            {
                "PORT": str(self.port),
                "HOST": "127.0.0.1",
                "APP_ENV": "development",
                "LIUHAO_HUMAN_IDENTITIES_BACKEND": "file",
                "LIUHAO_HUMAN_IDENTITIES_FILE": os.path.join(self.root, "identities.json"),
                "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY": "isolated-acceptance-key",
                "LIUHAO_AUTH_SECRETS_FILE": os.path.join(self.root, "auth_secrets.json"),
                "LIUHAO_JWT_SECRET": "isolated-jwt-secret-for-acceptance",
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
                # 隔离单实例锁：否则第二/第三个实例会被第一实例的锁挡住。
                "LIUHAO_INSTANCE_DIR": self.root,
            }
        )
        os.makedirs(env["LIUHAO_WORKSPACE_ROOT"], exist_ok=True)
        # 必须写文件而不是 PIPE：PIPE 没人读，日志填满 64KB 缓冲后子进程会
        # 阻塞在写日志上，表现为"请求永远不返回"。文件同时供明文扫描。
        self.log_path = os.path.join(self.root, "server.log")
        self._log = open(self.log_path, "w", encoding="utf-8")
        self.proc = subprocess.Popen(
            [PY, "-m", "src.gateway.__main__"],
            cwd=REPO,
            env=env,
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

    def stop(self) -> None:
        """杀进程树：venv 启动器会再拉起一个 uv python 子进程。"""
        if self.proc.poll() is None:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                capture_output=True,
            )
        try:
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()
        try:
            self._log.close()
        except Exception:
            pass


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
    report.check(item, "成员 calls=2", member_view.get("calls"), 2)

    status, root_self = request("GET", inst.base + "/v1/accounts/root1/usage", token=tokens["root"])
    report.check(item, "根查自己=整树账单", (status, root_self.get("calls")), (200, 2))
    status, grand_usage = request("GET", inst.base + "/v1/accounts/grand1/usage", token=tokens["root"])
    report.check(item, "无用量节点 calls=0", (status, grand_usage.get("calls")), (200, 0))
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
    report.check(item, "清单成员数=3", manifest.get("member_count"), 3)
    report.check(item, "清单含成员列表", sorted(manifest.get("members") or []), ["grand1", "member1", "root1"])
    report.check(item, "清单含文件数/字节数", ("files" in manifest and "bytes" in manifest), True, str({k: manifest.get(k) for k in ("files", "bytes")}))

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

    second = Instance("liuhao-acct-import-")
    try:
        bundle = exported.get("bundle") or {}
        status, imported = request(
            "POST", second.base + "/v1/accounts/backup/import",
            {"bundle": bundle, "passphrase": BUNDLE_PASSPHRASE},
        )
        report.check(item, "新机导入成功", (status, imported.get("root_principal")), (200, "root1"))
        report.check(item, "导入恢复账号数", imported.get("accounts"), 3)
        status, login = request(
            "POST", second.base + "/v1/auth/login",
            {"principal": "root1", "secret": PASSWORDS[0]},
        )
        report.check(item, "导入后原凭据可登录", status, 200)
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
        backup = backup_roundtrip(report, inst, tokens)
        file_isolation(report, inst, tokens)
        session_isolation(report, inst, tokens, usage["model_id"])
        audit_isolation(report, inst, tokens)

        export_path = os.path.join(inst.root, "export-all.json")
        with open(export_path, "w", encoding="utf-8") as handle:
            json.dump(
                {"bundle": backup["bundle"], "manifest": backup["manifest"]},
                handle, ensure_ascii=False,
            )
        export_files.append(export_path)

        deletion(report, inst, tokens)
        plaintext_scan(report, dirs, export_files)
    finally:
        inst.stop()
        print("\ntemp dir:", inst.root)

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
