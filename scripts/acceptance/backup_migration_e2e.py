"""备份 / 迁移端到端验收（真实网关进程 + 真实临时数据目录）。

覆盖：

1. 造一个有内容的账号树（两层、员工、目标、记忆、会话、知识库、两条模型 Key），
   导出加密包 → 在**全新数据目录**导入 → 逐项核对。
2. 权限边界：成员 ``scope=self`` 只含本人子树；根 ``scope=all`` 含全部，
   成员 Key 只以口令保险箱密文随包，换机后可直接使用。
3. 加密：错误口令、篡改一个字节、损坏包都必须被拒。
4. 换机：整目录拷到新路径，按安装器 ``data-dir.txt`` marker 解析后启动，
   账号可登录、记忆/知识库/员工/目标可读。
5. 失败路径：导入到已有数据的实例、导入损坏包、导入时磁盘满（SQLite 层注入
   ``database or disk is full``）。

运行：
    .venv\\Scripts\\python.exe scripts/acceptance/backup_migration_e2e.py
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[2]
PY = REPO / ".venv" / "Scripts" / "python.exe"
sys.path.insert(0, str(REPO))

PASSPHRASE = "migration-passphrase-123"
WRONG = "wrong-passphrase-456"
ROOT_KEY = "sk-root-migration-0001"
MEMBER_KEY = "sk-member-migration-0002"

PASSWORDS = {"root1": "rootpass123", "member1": "memberpass123"}
ROOT_DOC = "根账号知识库：贾维斯备份迁移验证，包含唯一的检索锚点 ALPHA-ROOT-9527。"
MEMBER_DOC = "成员知识库：换机迁移验证，包含唯一的检索锚点 BETA-MEMBER-8642。"


# --------------------------------------------------------------------------- #
# HTTP / 进程基础设施
# --------------------------------------------------------------------------- #

def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request(
    method: str, url: str, payload: Any = None, token: str = "",
    timeout: float = 120.0,
) -> Tuple[int, Any]:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw)
        except ValueError:
            body = {"raw": raw}
        return exc.code, body
    except Exception as exc:  # noqa: BLE001
        return 0, {"error": f"{type(exc).__name__}: {exc}"}


def data_path_env(data_dir: Path) -> Dict[str, str]:
    """与 ``scripts/launch_installed.data_path_env`` 保持同构。"""
    return {
        "LIUHAO_DATA_DIR": str(data_dir),
        "LIUHAO_WORKSPACE_ROOT": str(data_dir / "workspace"),
        "LIUHAO_HUMAN_IDENTITIES_FILE": str(data_dir / "config" / "human_identities.json"),
        "LIUHAO_AUTH_SECRETS_FILE": str(data_dir / "config" / "auth_secrets.json"),
        "LIUHAO_MODEL_CONFIG_DB": str(data_dir / "data" / "model_registry.sqlite3"),
        "LIUHAO_MODEL_CONFIG_KEY_FILE": str(data_dir / "data" / "model_config.key"),
        "CONVERSATION_DB_PATH": str(data_dir / "conversation_store.db"),
        "AUDIT_DB_PATH": str(data_dir / "audit_store.db"),
        "AUDIT_DB_BACKUP_PATH": str(data_dir / "audit_store.db.backup"),
        "LIUHAO_JWT_REVOCATION_DB": str(data_dir / "liuhao_jwt_revocation.db"),
        "MEMORY_DB_PATH": str(data_dir / "memory_store.db"),
        "PERSONAL_CONTEXT_DB_PATH": str(data_dir / "personal_context.db"),
        "LIUHAO_KNOWLEDGE_DB": str(data_dir / "data" / "knowledge_vectors.sqlite3"),
        "LIUHAO_ALERTS_STORE_PATH": str(data_dir / "data" / "observability" / "alerts.json"),
    }


def clean_env_base() -> Dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("LIUHAO_")
        and key
        not in {
            "CONVERSATION_DB_PATH", "AUDIT_DB_PATH", "AUDIT_DB_BACKUP_PATH",
            "MEMORY_DB_PATH", "PERSONAL_CONTEXT_DB_PATH", "PORT",
        }
    }
    env.update({
        "LIUHAO_ENV": "development",
        "LIUHAO_EMBEDDING_PROVIDER": "mock",
        "LIUHAO_KNOWLEDGE_ALLOW_MOCK": "1",
        "LIUHAO_ALLOW_REMOTE_RECOVERY": "0",
        "LIUHAO_SECRET_KEY": "e2e-fixed-secret-key-for-backup-migration",
        "PYTHONUTF8": "1",
    })
    return env


class Instance:
    def __init__(
        self,
        root: Path,
        *,
        data_dir: Optional[Path] = None,
        pythonpath_extra: str = "",
        cwd: Optional[Path] = None,
    ) -> None:
        self.root = root
        self.data_dir = data_dir or (root / "data")
        self.cwd = cwd or REPO
        self.env = clean_env_base()
        self.env.update(data_path_env(self.data_dir))
        self.env["LIUHAO_INSTANCE_DIR"] = str(root)
        self.env["LIUHAO_LOG_DIR"] = str(root / "logs")
        if pythonpath_extra:
            existing = self.env.get("PYTHONPATH") or ""
            self.env["PYTHONPATH"] = os.pathsep.join(
                part for part in (pythonpath_extra, existing) if part
            )
        for path in (
            self.data_dir,
            self.data_dir / "workspace",
            self.data_dir / "config",
            self.data_dir / "data",
            root / "logs",
        ):
            path.mkdir(parents=True, exist_ok=True)
        # 与安装器一致：每个数据目录绑定自己的身份注册表签名密钥。
        # 不设置它，账号树/人类注册表会 fail-closed，重启后谁都登不进来。
        from src.common.integrity_key import ensure_integrity_key

        self.integrity_key = ensure_integrity_key(data_dir=self.data_dir)
        self.env["LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY"] = self.integrity_key
        self.log_path = root / "server.log"
        self.port = 0
        self.base = ""
        self.proc: Optional[subprocess.Popen] = None
        self._log = None

    def start(self) -> None:
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.env["PORT"] = str(self.port)
        self._log = open(self.log_path, "a", encoding="utf-8")
        self.proc = subprocess.Popen(
            [str(PY), "-m", "src.gateway.__main__"],
            cwd=str(self.cwd),
            env=self.env,
            stdout=self._log,
            stderr=subprocess.STDOUT,
        )
        deadline = time.time() + 150
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("gateway exited:\n" + self.tail())
            status, _ = request("GET", self.base + "/v1/health")
            if status == 200:
                return
            time.sleep(1)
        raise RuntimeError("gateway not ready:\n" + self.tail())

    def stop(self, *, cleanup: bool = False) -> None:
        if self.proc is not None and self.proc.poll() is None:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                capture_output=True,
            )
        try:
            if self.proc is not None:
                self.proc.wait(timeout=15)
        except Exception:  # noqa: BLE001
            if self.proc is not None:
                self.proc.kill()
        if self._log is not None:
            self._log.close()
        self.proc = None
        self._log = None
        if cleanup:
            shutil.rmtree(self.root, ignore_errors=True)

    def tail(self) -> str:
        try:
            return self.log_path.read_text(encoding="utf-8", errors="replace")[-8000:]
        except OSError:
            return ""


def login(inst: Instance, principal: str) -> str:
    status, body = request(
        "POST",
        inst.base + "/v1/auth/login",
        {"principal": principal, "secret": PASSWORDS[principal]},
    )
    if status != 200 or not body.get("access_token"):
        raise RuntimeError(f"login {principal} failed: {status} {body}")
    return str(body["access_token"])


# --------------------------------------------------------------------------- #
# 造数 / 核对
# --------------------------------------------------------------------------- #

def seed(inst: Instance) -> Dict[str, Any]:
    status, body = request(
        "POST",
        inst.base + "/v1/accounts/register",
        {"principal": "root1", "password": PASSWORDS["root1"], "display_name": "主账号"},
    )
    assert status == 200, (status, body)
    root = login(inst, "root1")

    status, created = request(
        "POST",
        inst.base + "/v1/accounts",
        {"principal": "member1", "role": "manager"},
        token=root,
    )
    assert status == 200, (status, created)
    status, activated = request(
        "POST",
        inst.base + "/v1/accounts/activate",
        {
            "principal": "member1",
            "code": created.get("activation_code") or "",
            "password": PASSWORDS["member1"],
        },
    )
    assert status == 200, (status, activated)
    member = login(inst, "member1")

    for token, name in ((root, "root-staff"), (member, "member-staff")):
        status, body = request(
            "POST",
            inst.base + "/v1/employees",
            {"name": name, "agent_count": 1, "agent_types": ["planner"]},
            token=token,
        )
        assert status == 201, (status, body)

    status, root_model = request(
        "POST",
        inst.base + "/v1/models",
        {
            # 当前产品口径：新建模型未「测试连接」前不能直接占默认位；
            # 第一条模型会自动成为默认，所以这里不再传 is_default。
            "name": "root-migration-key", "provider": "mock", "model": "mock-1",
            "api_key": ROOT_KEY,
        },
        token=root,
    )
    assert status == 200, (status, root_model)
    status, member_model = request(
        "POST",
        inst.base + "/v1/models",
        {
            "name": "member-migration-key", "provider": "mock", "model": "mock-1",
            "api_key": MEMBER_KEY,
        },
        token=member,
    )
    assert status == 200, (status, member_model)

    for token, session, model_id in (
        (root, "root-session", root_model.get("id")),
        (member, "member-session", member_model.get("id")),
    ):
        status, body = request(
            "POST",
            inst.base + "/v1/chat",
            {
                "message": f"{session} 的迁移前消息",
                "session_id": session,
                "model_id": model_id,
                "use_knowledge": False,
            },
            token=token,
        )
        assert status == 200, (status, body)

    for token, text in (
        (root, "根账号记忆：偏好深色主题。"),
        (root, "根账号记忆：项目代号贾维斯。"),
        (member, "成员记忆：每周五提交周报。"),
    ):
        status, body = request(
            "POST", inst.base + "/v1/memory/entries", {"text": text}, token=token
        )
        assert status == 201, (status, body)

    for token, text in ((root, ROOT_DOC), (member, MEMBER_DOC)):
        status, body = request(
            "POST", inst.base + "/v1/knowledge/documents", {"text": text}, token=token
        )
        assert status == 200, (status, body)

    goal_ids: Dict[str, str] = {}
    for token, principal, text in (
        (root, "root1", "整理根账号迁移验证清单"),
        (member, "member1", "整理成员迁移验证清单"),
    ):
        status, body = request(
            "POST",
            inst.base + "/v1/goals",
            {"natural_language": text, "background": True},
            token=token,
        )
        assert status == 200, (status, body)
        goal = body.get("goal") or body
        goal_ids[principal] = str(goal.get("id") or goal.get("goal_id") or "")
    _wait_kernel_memory(inst)
    time.sleep(2)
    return {
        "root": root,
        "member": member,
        "root_model_id": root_model.get("id"),
        "member_model_id": member_model.get("id"),
        "goal_ids": goal_ids,
    }


def _wait_kernel_memory(inst: Instance, timeout: float = 30.0) -> int:
    """等内核记忆写完（会话/目标收尾可能异步），否则快照会少算后到的记忆。"""
    path = inst.data_dir / "memory_store.db"
    deadline = time.time() + timeout
    last = -1
    stable = 0.0
    while time.time() < deadline:
        try:
            connection = sqlite3.connect(str(path), timeout=5)
            try:
                count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM memory_entries"
                    ).fetchone()[0]
                )
            finally:
                connection.close()
        except sqlite3.Error:
            count = -1
        if count > 0 and count == last:
            stable += 0.4
            if stable >= 1.6:
                return count
        else:
            stable = 0.0
        last = count
        time.sleep(0.4)
    return last


def _flatten_tree(node: Any) -> List[str]:
    principals: List[str] = []
    if isinstance(node, dict):
        principal = str(node.get("principal") or "")
        if principal:
            principals.append(principal)
        for child in node.get("children") or []:
            principals.extend(_flatten_tree(child))
    elif isinstance(node, list):
        for child in node:
            principals.extend(_flatten_tree(child))
    return principals


def snapshot(inst: Instance, token: str) -> Dict[str, Any]:
    statuses: Dict[str, Any] = {}
    status, body = request("GET", inst.base + "/v1/accounts/tree", token=token)
    if status == 200:
        statuses["accounts"] = sorted(_flatten_tree(body.get("tree")))
    else:
        _, members = request("GET", inst.base + "/v1/accounts", token=token)
        statuses["accounts"] = sorted(
            str(item.get("account_id") or item.get("principal"))
            for item in members.get("members") or []
        )
    status, body = request("GET", inst.base + "/v1/employees", token=token)
    statuses["employees"] = sorted(
        item.get("name") for item in body.get("employees") or []
    )
    status, body = request("GET", inst.base + "/v1/goals", token=token)
    statuses["goals"] = sorted(
        str(item.get("id")) for item in body.get("goals") or []
    )
    status, body = request("GET", inst.base + "/v1/memory/entries?scope=self", token=token)
    statuses["memory"] = sorted(
        str(item.get("text")) for item in body.get("entries") or []
    )
    status, body = request("GET", inst.base + "/v1/chat/sessions", token=token)
    statuses["sessions"] = sorted(body.get("sessions") or [])
    status, body = request("GET", inst.base + "/v1/knowledge/documents", token=token)
    statuses["knowledge"] = sorted(
        str(item.get("document_id")) for item in body.get("documents") or []
    )
    status, body = request("GET", inst.base + "/v1/models", token=token)
    statuses["models"] = sorted(
        f"{item.get('name')}:{item.get('api_key_status')}"
        for item in body.get("models") or []
    )
    return statuses


def tree_principals(inst: Instance, token: str) -> List[str]:
    status, body = request("GET", inst.base + "/v1/accounts/tree", token=token)
    if status != 200:
        return []
    return sorted(_flatten_tree(body.get("tree")))


# --------------------------------------------------------------------------- #
# 结果表
# --------------------------------------------------------------------------- #

class Table:
    def __init__(self) -> None:
        self.rows: List[Tuple[str, str, str, str]] = []

    def add(self, item: str, before: Any, after: Any, consistent: bool) -> None:
        self.rows.append(
            (item, str(before), str(after), "✅ 一致" if consistent else "❌ 不一致")
        )

    def render(self) -> str:
        widths = [max(len(row[i]) for row in self.rows + [("项目", "导出前", "导入后", "一致否")])
                  for i in range(4)]
        line = " | ".join(
            label.ljust(widths[i]) for i, label in enumerate(("项目", "导出前", "导入后", "一致否"))
        )
        sep = "-+-".join("-" * width for width in widths)
        body = "\n".join(
            " | ".join(value.ljust(widths[i]) for i, value in enumerate(row))
            for row in self.rows
        )
        return f"{line}\n{sep}\n{body}"


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #

def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="liuhao-backup-migration-"))
    table = Table()
    failures: List[str] = []
    checks: Dict[str, bool] = {}

    def check(name: str, condition: bool, detail: str = "") -> None:
        checks[name] = bool(condition)
        if not condition:
            failures.append(name + (f" ({detail})" if detail else ""))
            print(f"[FAIL] {name} {detail}")

    a = b = c = d = e = None
    try:
        # ---------------- A: 造数 ----------------
        a = Instance(tmp / "a")
        a.start()
        tokens_a = seed(a)
        before_root = snapshot(a, tokens_a["root"])
        before_member = snapshot(a, tokens_a["member"])
        check("A 账号树两层", tree_principals(a, tokens_a["root"]) == ["member1", "root1"],
              str(tree_principals(a, tokens_a["root"])))

        status, exported_all = request(
            "POST", a.base + "/v1/accounts/backup/export",
            {"scope": "all", "passphrase": PASSPHRASE}, token=tokens_a["root"],
        )
        check("根 scope=all 导出 200", status == 200, str(exported_all)[:300])
        status, exported_self = request(
            "POST", a.base + "/v1/accounts/backup/export",
            {"scope": "self", "passphrase": PASSPHRASE}, token=tokens_a["member"],
        )
        check("成员 scope=self 导出 200", status == 200, str(exported_self)[:300])
        bundle_all = exported_all.get("bundle") or {}
        bundle_self = exported_self.get("bundle") or {}

        # ---------------- 2. 权限边界（解包核对） ----------------
        from src.kernels.identity._backup import decrypt_bundle

        payload_all = decrypt_bundle(bundle_all, PASSPHRASE)
        payload_self = decrypt_bundle(bundle_self, PASSPHRASE)
        text_self = json.dumps(payload_self, ensure_ascii=False)
        self_accounts = sorted(
            str(item.get("principal")) for item in payload_self.get("identity", {}).get("accounts", [])
        )
        check("成员包只含本人账号", self_accounts == ["member1"], str(self_accounts))
        check("成员包不含根 Key 明文", ROOT_KEY not in text_self)
        check("成员包只含本人 Key 密文", MEMBER_KEY not in text_self and "api_key_enc" in text_self)
        self_employee_owners = {
            str(item.get("owner_account_id") or "") for item in payload_self.get("employees") or []
        }
        check("成员包员工只含本人", self_employee_owners <= {"member1"},
              str(self_employee_owners))
        self_goal_owners = {
            str(item.get("owner_account_id") or "") for item in (payload_self.get("goals") or {}).values()
        }
        check("成员包目标只含本人", self_goal_owners <= {"member1"}, str(self_goal_owners))
        self_knowledge_owners = set()
        for row in (payload_self.get("knowledge") or {}).get("vectors") or []:
            metadata = json.loads(row.get("metadata") or "{}")
            self_knowledge_owners.add(str(metadata.get("owner_account_id") or ""))
        check("成员包知识库只含本人", self_knowledge_owners <= {"member1"},
              str(self_knowledge_owners))

        text_all = json.dumps(payload_all, ensure_ascii=False)
        check("根包含全部成员", sorted(
            str(item.get("principal")) for item in payload_all.get("identity", {}).get("accounts", [])
        ) == ["member1", "root1"])
        check("根包含整棵子树 Key 密文（主密钥不随包）",
              "api_key_enc" in text_all
              and bool((payload_all.get("models") or {}).get("key_vault")))
        check("根包不含任何 Key 明文（根/成员都只在保险箱密文里）",
              ROOT_KEY not in text_all and MEMBER_KEY not in text_all)
        check("回执：成员 Key 随包（密文）", exported_all.get("member_keys_included") is True)
        check("回执：模型主密钥未导出", exported_all.get("key_material_included") is False)

        # ---------------- 失败路径：已有数据 ----------------
        status, body = request(
            "POST", a.base + "/v1/accounts/backup/import",
            {"bundle": bundle_all, "passphrase": PASSPHRASE},
        )
        check("导入已有数据实例被拒 409", status == 409, f"{status} {body}")
        check("已有数据实例状态未变", tree_principals(a, tokens_a["root"]) == ["member1", "root1"])

        # ---------------- B: 全新目录导入 ----------------
        b = Instance(tmp / "b")
        b.start()
        status, body = request(
            "POST", b.base + "/v1/accounts/backup/import",
            {"bundle": bundle_all, "passphrase": WRONG},
        )
        check("错误口令被拒 400", status == 400, f"{status} {body}")

        tampered = json.loads(json.dumps(bundle_all))
        raw = bytearray(base64.b64decode(tampered["payload"]))
        raw[len(raw) // 2] ^= 0x01
        tampered["payload"] = base64.b64encode(bytes(raw)).decode("ascii")
        status, body = request(
            "POST", b.base + "/v1/accounts/backup/import",
            {"bundle": tampered, "passphrase": PASSPHRASE},
        )
        check("篡改一个字节被拒 400", status == 400, f"{status} {body}")

        corrupt = json.loads(json.dumps(bundle_all))
        corrupt["payload"] = "!!!not-base64!!!"
        status, body = request(
            "POST", b.base + "/v1/accounts/backup/import",
            {"bundle": corrupt, "passphrase": PASSPHRASE},
        )
        check("损坏包被拒 400", status == 400, f"{status} {body}")

        status, imported = request(
            "POST", b.base + "/v1/accounts/backup/import",
            {"bundle": bundle_all, "passphrase": PASSPHRASE},
        )
        check("全新目录导入 200", status == 200, f"{status} {imported}")
        check("导入回执知识库文档数=2",
              ((imported.get("manifest") or {}).get("knowledge") or {}).get("documents") == 2,
              str((imported.get("manifest") or {}).get("knowledge")))
        check("导入回执员工数=源端",
              (imported.get("manifest") or {}).get("employees")
              == (exported_all.get("manifest") or {}).get("employees"),
              f"{(imported.get('manifest') or {}).get('employees')} vs "
              f"{(exported_all.get('manifest') or {}).get('employees')}")
        check("导入回执目标数=2", (imported.get("manifest") or {}).get("goals") == 2,
              str((imported.get("manifest") or {}).get("goals")))

        b.stop()
        b.start()  # 重启：证明不是内存态
        tokens_b_root = login(b, "root1")
        tokens_b_member = login(b, "member1")  # 凭据跨机可用
        after_root = snapshot(b, tokens_b_root)
        after_member = snapshot(b, tokens_b_member)

        table.add("账号（根视角树）", before_root["accounts"], after_root["accounts"],
                  before_root["accounts"] == after_root["accounts"])
        table.add("员工（根视角）", before_root["employees"], after_root["employees"],
                  before_root["employees"] == after_root["employees"])
        table.add("目标（根视角）", len(before_root["goals"]), len(after_root["goals"]),
                  len(after_root["goals"]) == 2)
        table.add("记忆（根 self）", len(before_root["memory"]), len(after_root["memory"]),
                  before_root["memory"] == after_root["memory"])
        table.add("会话（根）", before_root["sessions"], after_root["sessions"],
                  before_root["sessions"] == after_root["sessions"])
        table.add("知识库（根）", len(before_root["knowledge"]), len(after_root["knowledge"]),
                  len(after_root["knowledge"]) == 1)
        table.add("知识库（成员）", len(before_member["knowledge"]),
                  len(after_member["knowledge"]), len(after_member["knowledge"]) == 1)
        table.add("员工（成员）", before_member["employees"], after_member["employees"],
                  before_member["employees"] == after_member["employees"])
        table.add("目标（成员）", len(before_member["goals"]), len(after_member["goals"]),
                  len(after_member["goals"]) == 1)
        table.add("记忆（成员）", len(before_member["memory"]),
                  len(after_member["memory"]), len(after_member["memory"]) == 1)
        table.add("会话（成员）", before_member["sessions"], after_member["sessions"],
                  before_member["sessions"] == after_member["sessions"])

        status, root_models = request("GET", b.base + "/v1/models", token=tokens_b_root)
        status2, member_models = request("GET", b.base + "/v1/models", token=tokens_b_member)
        root_model_count = len(root_models.get("models") or [])
        member_model_count = len(member_models.get("models") or [])
        table.add("模型 Key（根视角，含子树）", len(before_root["models"]),
                  root_model_count, before_root["models"] == after_root["models"])
        table.add("模型 Key（成员视角）", len(before_member["models"]),
                  member_model_count, before_member["models"] == after_member["models"])
        member_model_rows = member_models.get("models") or []
        check("成员 Key 随包迁移且开箱可用",
              member_model_count == 1
              and member_model_rows[0].get("name") == "member-migration-key"
              and member_model_rows[0].get("api_key_status") == "ok"
              and str(member_model_rows[0].get("api_key_masked") or "").endswith("0002"),
              str(member_model_rows[:1]))
        root_model_rows = root_models.get("models") or []
        check("根视角同时看到根与成员 Key",
              {row.get("name") for row in root_model_rows}
              == {"root-migration-key", "member-migration-key"},
              str(root_model_rows))

        b_root_key_path = Path(data_path_env(b.data_dir)["LIUHAO_MODEL_CONFIG_KEY_FILE"])
        a_root_key_path = Path(data_path_env(a.data_dir)["LIUHAO_MODEL_CONFIG_KEY_FILE"])
        check("新机使用自己的模型主密钥",
              b_root_key_path.read_bytes() != a_root_key_path.read_bytes())
        # 根自己的 Key：包内是「口令保险箱」里的密文，导入时用同一口令解封、
        # 再用新机主密钥重加密 → 新机可直接使用；包内没有明文 Key。
        root_model_rows = root_models.get("models") or []
        root_row = next(
            (row for row in root_model_rows if row.get("name") == "root-migration-key"), {}
        )
        check("导入后根 Key 配置行保留", bool(root_row), str(root_model_rows[:2]))
        check("导入后根 Key 可直接使用（新机重加密成功）",
              bool(root_row.get("api_key_set")) and root_row.get("api_key_status") == "ok",
              str(root_row))
        check("导入后根 Key 尾号与源端一致",
              str(root_row.get("api_key_masked") or "").endswith("0001"),
              str(root_row))
        status, health_emps = request("GET", b.base + "/v1/employees", token=tokens_b_root)
        check("导入后员工页不再 500", status == 200, f"{status} {str(health_emps)[:120]}")
        # 会话历史确实可见（不是只有会话名）
        status, history = request(
            "GET", b.base + "/v1/chat/history?session_id=root-session", token=tokens_b_root
        )
        check("导入后根会话历史可读", status == 200 and len(history.get("history") or []) >= 2,
              f"{status} count={len(history.get('history') or [])}")

        # 知识库检索真的命中（向量 + FTS 都恢复）
        status, search = request(
            "POST", b.base + "/v1/knowledge/search",
            {"query": "ALPHA-ROOT-9527", "top_k": 3}, token=tokens_b_root,
        )
        hits = search.get("hits") or (search.get("retrieval") or {}).get("hits") or []
        check("导入后根知识库可检索", status == 200 and bool(hits),
              f"{status} hits={len(hits)}")

        for item, before, after in (
            ("账号", before_root["accounts"], after_root["accounts"]),
            ("员工", before_root["employees"], after_root["employees"]),
            ("记忆", before_root["memory"], after_root["memory"]),
            ("会话", before_root["sessions"], after_root["sessions"]),
        ):
            check(f"B 核对 {item}", before == after, f"{before} -> {after}")

        # ---------------- C: 磁盘满注入 + 重试 ----------------
        fault_dir = tmp / "fault"
        fault_dir.mkdir()
        (fault_dir / "active").write_text("1", encoding="utf-8")
        (fault_dir / "sitecustomize.py").write_text(
            '''
import sqlite3 as _sqlite3
_orig_connect = _sqlite3.connect
import os as _os

class _DiskFullProxy:
    def __init__(self, conn):
        self._conn = conn
    def execute(self, sql, *args, **kwargs):
        lower = str(sql).lstrip().lower()
        if _os.path.exists(_FLAG) and lower.startswith("insert"):
            raise _sqlite3.OperationalError("database or disk is full")
        return self._conn.execute(sql, *args, **kwargs)
    def __getattr__(self, name):
        return getattr(self._conn, name)
    def __enter__(self):
        self._conn.__enter__()
        return self
    def __exit__(self, *exc):
        return self._conn.__exit__(*exc)

def connect(*args, **kwargs):
    conn = _orig_connect(*args, **kwargs)
    path = str(args[0]) if args else str(kwargs.get("database") or "")
    if path.endswith("account_memory.sqlite3"):
        return _DiskFullProxy(conn)
    return conn

_FLAG = r"%s"
_sqlite3.connect = connect
''' % str(fault_dir / "active"),
            encoding="utf-8",
        )
        d = Instance(tmp / "d", pythonpath_extra=str(fault_dir))
        d.start()
        status, body = request(
            "POST", d.base + "/v1/accounts/backup/import",
            {"bundle": bundle_all, "passphrase": PASSPHRASE},
        )
        check("磁盘满导入返回 507", status == 507, f"{status} {body}")
        status, _ = request("GET", d.base + "/v1/accounts/tree", token="")
        root_after_fault = request(
            "POST", d.base + "/v1/auth/login",
            {"principal": "root1", "secret": PASSWORDS["root1"]},
        )[0]
        check("磁盘满后未认领账号树（可重试）", root_after_fault != 200,
              f"login={root_after_fault}")
        (fault_dir / "active").unlink()
        status, retried = request(
            "POST", d.base + "/v1/accounts/backup/import",
            {"bundle": bundle_all, "passphrase": PASSPHRASE},
        )
        check("清理磁盘后同一包可重试成功", status == 200, f"{status} {retried}")
        d.stop()

        # ---------------- 4. 换机：整目录拷贝 + marker ----------------
        a.stop()
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "launch_installed", REPO / "scripts" / "launch_installed.py"
        )
        launcher = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(launcher)

        original_marker = launcher.DATA_DIR_MARKER
        migrated_root = tmp / "installed" / "migrated-data"
        shutil.copytree(a.data_dir, migrated_root)
        marker = tmp / "installed" / "data-dir.txt"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_bytes(b"\xef\xbb\xbf" + str(migrated_root).encode("utf-8") + b"\r\n")
        launcher.DATA_DIR_MARKER = marker
        data_dir_env_names = {launcher.DATA_DIR_ENV}
        old_env = {key: os.environ.get(key) for key in data_dir_env_names}
        for key in data_dir_env_names:
            os.environ.pop(key, None)
        try:
            resolved = launcher._data_dir()  # noqa: SLF001 - 验收真实解析路径
        finally:
            launcher.DATA_DIR_MARKER = original_marker
            for key, value in old_env.items():
                if value is not None:
                    os.environ[key] = value
        check("marker 指向新数据目录", resolved == migrated_root.resolve(),
              f"{resolved} != {migrated_root.resolve()}")

        e = Instance(tmp / "e", data_dir=resolved)
        e.start()
        tokens_e_root = login(e, "root1")
        tokens_e_member = login(e, "member1")
        tree_e = tree_principals(e, tokens_e_root)
        mem_e = request("GET", e.base + "/v1/memory/entries?scope=self", token=tokens_e_root)[1]
        know_e = request("GET", e.base + "/v1/knowledge/documents", token=tokens_e_root)[1]
        goals_e = request("GET", e.base + "/v1/goals", token=tokens_e_root)[1]
        emp_e = request("GET", e.base + "/v1/employees", token=tokens_e_root)[1]
        table.add("换机：账号登录", "root1/member1", f"{bool(tokens_e_root)}/{bool(tokens_e_member)}",
                  bool(tokens_e_root) and bool(tokens_e_member))
        table.add("换机：账号树", before_root["accounts"], tree_e,
                  tree_e == before_root["accounts"])
        table.add("换机：记忆", len(before_root["memory"]),
                  len(mem_e.get("entries") or []),
                  len(mem_e.get("entries") or []) == len(before_root["memory"]))
        table.add("换机：知识库", 1, len(know_e.get("documents") or []),
                  len(know_e.get("documents") or []) == 1)
        table.add("换机：目标", 2, len(goals_e.get("goals") or []),
                  len(goals_e.get("goals") or []) == 2)
        table.add("换机：员工", len(before_root["employees"]),
                  len(emp_e.get("employees") or []),
                  len(emp_e.get("employees") or []) == len(before_root["employees"]))

        print("\n" + table.render() + "\n")
        if failures:
            print("未通过项：")
            for failure in failures:
                print(" - " + failure)
            return 1
        print("全部通过")
        return 0
    finally:
        for inst in (a, b, c, d, e):
            if inst is not None:
                inst.stop(cleanup=False)
        if os.environ.get("LIUHAO_KEEP_E2E_DIR") != "1":
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            print(f"保留临时目录：{tmp}")


if __name__ == "__main__":
    raise SystemExit(main())
