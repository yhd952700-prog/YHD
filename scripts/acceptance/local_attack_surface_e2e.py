"""本机攻击面回归探针：把四条修复固化成可重复跑的验收。

覆盖（对应 ``docs/audit-local-attack-surface-20261108.md``）：

① 改 ``base_url`` 且不重输 Key → 409 ``MODEL_KEY_TARGET_CHANGED``；假攻击者
   服务器收不到任何请求，更收不到 ``Authorization``。
② 重输 Key / ``confirm_key_resend=true`` → 放行，并写入 ``key.base_url_changed``
   审计（含前后 target、``key_reentered`` / ``stored_key_resent``）。
③ 同 host、仅换端口也算新目标 → 409。
④ 回环多端口（LM Studio / vLLM 形态）仍可保存并测试成功。
⑤ 安装副本 ``config/app.env``、``config/auth_secrets.json``、
   ``data/model_config.key`` 读回 DACL：``AreAccessRulesProtected=True``、
   仅当前用户、不含 ``Users`` / ``CodexSandboxUsers``。
⑥ 本机身份证明管道：命名管道 DACL 只授当前用户 SID；受限令牌（把当前用户
   SID 设为 deny-only，等价于“同机另一个无授权主体”）连管道返回拒绝；一次性
   handoff token 由别的进程取得后失效（403）、属主重新取得可用、取用即焚。
⑦ 备份/导出不含密钥：``scope=all`` / ``scope=self`` 的凭据包真解包后，
   不得出现 ``model_config.key`` / ``integrity.key`` / ``auth_secrets.json`` /
   ``human_identities.json`` / ``model_registry.sqlite3``，也不得出现明文
   API Key；同时确认 Key 确实以口令密封的保险箱形式随包（不是“整包为空”）。
⑧ 卸载残留：在临时目录搭一套“安装完成”足迹（程序目录、数据目录、开始菜单/
   桌面快捷方式、HKCU 卸载键、自启项、托盘进程与监听端口），用受控的注册表/
   目录操作模拟卸载，再检查「程序目录 / data-dir.txt / 快捷方式 / 卸载键 /
   自启项 / 托盘进程 / 端口」是否全部清掉；默认保留数据与明确删除数据两种
   卸载选项都验。

⑥⑦⑧ 各自带负向对照：⑥ 故意造一条只授 ``BUILTIN\\Users`` 的管道，检查器必须
判“非仅当前用户”；⑦ 故意造一份含密钥文件名与明文 Key 的 payload，扫描器必须
全部命中；⑧ 故意留下自启项与托盘进程，残留检查器必须判红。负向对照全绿即
证明探针不是“永远绿”。

脚本自己起一台隔离网关（临时数据目录 + 随机回环端口），以及若干本地假上游
（只用于验证，会记录收到的 ``Authorization``），跑完全部回收；任何 FAIL
都会让退出码非零。

运行（仓库根）::

    .\\.venv\\Scripts\\python.exe scripts/acceptance/local_attack_surface_e2e.py

安装副本不在默认 ``%LOCALAPPDATA%\\LiuHao\\data`` 时::

    .\\.venv\\Scripts\\python.exe scripts/acceptance/local_attack_surface_e2e.py \\
        --installed-data-dir "D:\\somewhere\\LiuHao\\data"

打包前置门禁（安装副本可能尚未存在）::

    .\\.venv\\Scripts\\python.exe scripts/acceptance/local_attack_surface_e2e.py \\
        --allow-missing-installed
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "scripts", "acceptance"))

from account_tree_e2e import (  # noqa: E402
    PASSWORDS,
    PY,
    Instance,
    Report,
    error_code,
    request,
)

MODEL_NAME = "gpt-4o-mini"
STORED_KEY = "sk-audit-stored-0123456789abcdef"
REENTERED_KEY = "sk-audit-reenter-fedcba9876543210"
LOCAL_KEY_A = "sk-local-lmstudio-0001"
LOCAL_KEY_B = "sk-local-vllm-0002"

#: 安装副本里必须私有的文件（相对数据目录）。
PRIVATE_FILES = (
    "config/app.env",
    "config/auth_secrets.json",
    "data/model_config.key",
)

#: 明确不允许出现在 ACE 里的主体：普通 Users / Authenticated Users。
FOREIGN_SIDS = {
    "S-1-5-32-545",  # BUILTIN\Users
    "S-1-5-11",      # NT AUTHORITY\Authenticated Users
}

AUDIT_ACTION = "key.base_url_changed"

#: 备份/「打包带走」产物里绝不允许出现的文件名（相对路径任意位置）。
FORBIDDEN_BACKUP_NAMES = (
    "model_config.key",
    "integrity.key",
    "auth_secrets.json",
    "human_identities.json",
    "model_registry.sqlite3",
)

#: 明文 API Key 形态；已知 Key 都以此开头，能被同一条规则抓到。
PLAINTEXT_KEY_RE = re.compile(r"sk-[A-Za-z0-9_-]{8,}")

#: 恢复码交接相关稳定错误码。
CODE_PROOF_REQUIRED = "ACCOUNT_RECOVERY_PROOF_REQUIRED"


# --------------------------------------------------------------------------- #
# 本地假上游：记录请求头，返回一个合法的最小 OpenAI 响应
# --------------------------------------------------------------------------- #

class _UpstreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:  # 静默，避免污染验收输出
        pass

    def _handle(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        record = {
            "method": self.command,
            "path": self.path,
            "authorization": self.headers.get("Authorization"),
            "x_api_key": self.headers.get("X-API-Key"),
            "body": body[:400].decode("utf-8", "replace"),
        }
        server = self.server
        with server.lock:  # type: ignore[attr-defined]
            server.records.append(record)  # type: ignore[attr-defined]

        if self.command == "GET":
            payload: Dict[str, Any] = {
                "object": "list",
                "data": [{"id": server.model, "object": "model"}],  # type: ignore[attr-defined]
            }
        else:
            payload = {
                "id": "chatcmpl-acceptance",
                "object": "chat.completion",
                "created": 0,
                "model": server.model,  # type: ignore[attr-defined]
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "pong"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = _handle
    do_POST = _handle


class MockUpstream:
    """回环上的假模型服务；``records`` 保留每次请求（含 Authorization）。"""

    def __init__(self, label: str, model: str = MODEL_NAME) -> None:
        self.label = label
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _UpstreamHandler)
        self._httpd.daemon_threads = True
        self._httpd.records = []  # type: ignore[attr-defined]
        self._httpd.lock = threading.Lock()  # type: ignore[attr-defined]
        self._httpd.model = model  # type: ignore[attr-defined]
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name=f"mock-upstream-{label}",
            daemon=True,
        )

    def __enter__(self) -> "MockUpstream":
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @property
    def port(self) -> int:
        return int(self._httpd.server_address[1])

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def api_base(self) -> str:
        return f"{self.base_url}/v1"

    @property
    def records(self) -> List[Dict[str, Any]]:
        with self._httpd.lock:  # type: ignore[attr-defined]
            return list(self._httpd.records)  # type: ignore[attr-defined]

    def auth_headers(self) -> List[str]:
        return [str(r["authorization"]) for r in self.records if r.get("authorization")]

    def clear(self) -> None:
        with self._httpd.lock:  # type: ignore[attr-defined]
            self._httpd.records.clear()  # type: ignore[attr-defined]

    def close(self) -> None:
        try:
            self._httpd.shutdown()
        finally:
            self._httpd.server_close()


# --------------------------------------------------------------------------- #
# 网关侧小工具
# --------------------------------------------------------------------------- #

def bootstrap(inst: Instance) -> str:
    """注册根账号并登录，返回访问令牌。"""
    status, body = request(
        "POST",
        inst.base + "/v1/accounts/register",
        {"principal": "root1", "password": PASSWORDS[0], "display_name": "攻击面验收"},
    )
    if status != 200:
        raise RuntimeError(f"register root failed: {status} {body}")
    status, body = request(
        "POST",
        inst.base + "/v1/auth/login",
        {"principal": "root1", "secret": PASSWORDS[0]},
    )
    if status != 200:
        raise RuntimeError(f"login root failed: {status} {body}")
    token = str(body.get("access_token") or "")
    if not token:
        raise RuntimeError(f"login returned no token: {body}")
    return token


def create_model(
    inst: Instance,
    token: str,
    *,
    name: str,
    base_url: str,
    api_key: Optional[str],
) -> Tuple[int, Any]:
    return request(
        "POST",
        inst.base + "/v1/models",
        {
            "name": name,
            "provider": "openai",
            "model": MODEL_NAME,
            "base_url": base_url,
            "api_key": api_key,
        },
        token=token,
    )


def update_model(
    inst: Instance, token: str, model_id: str, payload: Dict[str, Any]
) -> Tuple[int, Any]:
    return request(
        "PUT", f"{inst.base}/v1/models/{model_id}", payload, token=token
    )


def get_model(inst: Instance, token: str, model_id: str) -> Tuple[int, Any]:
    return request("GET", f"{inst.base}/v1/models/{model_id}", token=token)


def probe(inst: Instance, token: str, payload: Dict[str, Any]) -> Tuple[int, Any]:
    return request("POST", inst.base + "/v1/models/test", payload, token=token)


def probe_saved(inst: Instance, token: str, model_id: str) -> Tuple[int, Any]:
    return request(
        "POST", f"{inst.base}/v1/models/{model_id}/test", {}, token=token
    )


def read_key_audit(db_path: str, *, timeout: float = 10.0) -> List[Dict[str, Any]]:
    """从隔离实例的审计库读出 ``key.base_url_changed`` 事件（带重试）。"""
    deadline = time.time() + timeout
    rows: List[Tuple[Any, ...]] = []
    while time.time() < deadline:
        try:
            with sqlite3.connect(db_path, timeout=5.0) as conn:
                rows = conn.execute(
                    "SELECT seq, principal_id, outcome, details FROM audit_events "
                    "WHERE details LIKE ? ORDER BY seq",
                    (f"%{AUDIT_ACTION}%",),
                ).fetchall()
            if rows:
                break
        except sqlite3.Error:
            pass
        time.sleep(0.3)
    events: List[Dict[str, Any]] = []
    for seq, principal, outcome, details in rows:
        try:
            parsed = json.loads(details or "{}")
        except (TypeError, ValueError):
            parsed = {}
        if parsed.get("action") != AUDIT_ACTION:
            continue
        events.append(
            {
                "seq": seq,
                "principal_id": principal,
                "outcome": outcome,
                "previous_target": parsed.get("previous_target"),
                "new_target": parsed.get("new_target"),
                "stored_key_resent": parsed.get("stored_key_resent"),
                "key_reentered": parsed.get("key_reentered"),
            }
        )
    return events


# --------------------------------------------------------------------------- #
# ① 改地址不重输 Key：409 + 攻击者拿不到 Authorization
# --------------------------------------------------------------------------- #

def case_egress(
    report: Report, inst: Instance, token: str, legit: MockUpstream, attacker: MockUpstream
) -> str:
    item = "①改地址外带"
    status, created = create_model(
        inst,
        token,
        name="audit-egress",
        base_url=legit.api_base,
        api_key=STORED_KEY,
    )
    report.check(item, "带 Key 的模型能保存", status, 200, f"id={created.get('id') if isinstance(created, dict) else created}")
    model_id = str(created.get("id") or "")
    if not model_id:
        report.check(item, "拿到模型 id", False, True, str(created))
        return ""

    # 基线：已保存的目标仍然能用已存 Key 测试通过（修复不能误伤正常用法）。
    status, body = probe_saved(inst, token, model_id)
    report.check(
        item,
        "已保存目标测试通过（基线）",
        (status, body.get("ok") if isinstance(body, dict) else None),
        (200, True),
    )
    report.check(
        item,
        "基线确实发送了已存 Key",
        legit.auth_headers()[:1],
        [f"Bearer {STORED_KEY}"],
    )
    legit.clear()

    # 攻击：只改 base_url，不重输 Key。
    status, body = update_model(inst, token, model_id, {"base_url": attacker.api_base})
    report.check(
        item,
        "改地址不重输 Key 被拒绝",
        (status, error_code(body)),
        (409, "MODEL_KEY_TARGET_CHANGED"),
        str(body)[:160],
    )
    status, stored = get_model(inst, token, model_id)
    stored_url = stored.get("base_url") if isinstance(stored, dict) else None
    report.check(item, "被拒后保存地址未变", stored_url, legit.api_base)

    # 攻击：测试连接把已存 Key 指向新地址。
    status, body = probe(
        inst,
        token,
        {
            "provider": "openai",
            "model": MODEL_NAME,
            "base_url": attacker.api_base,
            "model_id": model_id,
        },
    )
    report.check(
        item,
        "测试连接跨目标被拒绝",
        (status, error_code(body)),
        (409, "MODEL_KEY_TARGET_CHANGED"),
        str(body)[:160],
    )
    report.check(
        item,
        "攻击者服务器没收到任何请求",
        attacker.records,
        [],
        f"port={attacker.port}",
    )
    report.check(item, "攻击者没收到 Authorization", attacker.auth_headers(), [])
    return model_id


# --------------------------------------------------------------------------- #
# ③ 同 host 仅换端口也算新目标
# --------------------------------------------------------------------------- #

def case_port_is_target(
    report: Report, inst: Instance, token: str, model_id: str, other: MockUpstream
) -> None:
    item = "③同host换端口"
    if not model_id:
        report.check(item, "前置模型存在", False, True)
        return
    status, stored = get_model(inst, token, model_id)
    saved = str(stored.get("base_url") if isinstance(stored, dict) else "")
    status, body = update_model(inst, token, model_id, {"base_url": other.api_base})
    report.check(
        item,
        "同 host 换端口保存被拒绝",
        (status, error_code(body)),
        (409, "MODEL_KEY_TARGET_CHANGED"),
        f"saved={saved} requested={other.api_base}",
    )
    status, body = probe(
        inst,
        token,
        {
            "provider": "openai",
            "model": MODEL_NAME,
            "base_url": other.api_base,
            "model_id": model_id,
        },
    )
    report.check(
        item,
        "同 host 换端口测试被拒绝",
        (status, error_code(body)),
        (409, "MODEL_KEY_TARGET_CHANGED"),
        f"saved={saved} requested={other.api_base}",
    )
    report.check(item, "该端口没收到请求", other.records, [])


# --------------------------------------------------------------------------- #
# ② 重输 Key / 显式确认：放行并写审计
# --------------------------------------------------------------------------- #

def case_rekey_and_confirm(
    report: Report, inst: Instance, token: str, model_id: str, attacker: MockUpstream, other: MockUpstream
) -> None:
    item = "②重输/确认"
    if not model_id:
        report.check(item, "前置模型存在", False, True)
        return

    status, body = update_model(
        inst,
        token,
        model_id,
        {"base_url": attacker.api_base, "api_key": REENTERED_KEY},
    )
    report.check(item, "重输 Key 后改地址放行", status, 200, str(body)[:160])
    report.check(
        item,
        "重输 Key 未落明文",
        REENTERED_KEY in json.dumps(body, ensure_ascii=False),
        False,
    )

    status, body = update_model(
        inst,
        token,
        model_id,
        {"base_url": other.api_base, "confirm_key_resend": True},
    )
    report.check(item, "显式确认后改地址放行", status, 200, str(body)[:160])
    report.check(
        item,
        "confirm_key_resend 不落库",
        "confirm_key_resend" in json.dumps(body, ensure_ascii=False),
        False,
    )

    # 确认之后，已存 Key 才允许去新目标；这也是“确认”与“静默外带”的区别。
    status, body = probe_saved(inst, token, model_id)
    report.check(
        item,
        "确认后的新目标测试通过",
        (status, body.get("ok") if isinstance(body, dict) else None),
        (200, True),
    )
    report.check(
        item,
        "新目标收到的是重输的 Key",
        other.auth_headers()[-1:],
        [f"Bearer {REENTERED_KEY}"],
    )

    events = read_key_audit(str(inst.env["AUDIT_DB_PATH"]))
    report.check(item, "审计有 key.base_url_changed", bool(events), True, f"count={len(events)}")
    report.check(
        item,
        "审计记录了重输 Key",
        any(e.get("key_reentered") is True for e in events),
        True,
        str(events),
    )
    resent = [e for e in events if e.get("stored_key_resent") is True]
    report.check(item, "审计记录了已存 Key 外发确认", bool(resent), True, str(resent))
    if resent:
        event = resent[-1]
        report.check(
            item,
            "审计含前后 target",
            (event.get("previous_target"), event.get("new_target")),
            (attacker.base_url, other.base_url),
        )


# --------------------------------------------------------------------------- #
# ④ 回环多端口：本地推理仍可保存与测试
# --------------------------------------------------------------------------- #

def case_loopback_ports(report: Report, inst: Instance, token: str) -> None:
    item = "④回环多端口"
    cases = (
        ("LM Studio", LOCAL_KEY_A),
        ("vLLM", LOCAL_KEY_B),
    )
    ports: List[int] = []
    for index, (label, key) in enumerate(cases, start=1):
        with MockUpstream(f"local-{index}") as local:
            ports.append(local.port)
            status, created = create_model(
                inst,
                token,
                name=f"local-{index}",
                base_url=local.api_base,
                api_key=key,
            )
            report.check(item, f"{label} 回环地址可保存", status, 200, local.api_base)
            model_id = str(created.get("id") or "") if isinstance(created, dict) else ""
            status, body = probe_saved(inst, token, model_id)
            report.check(
                item,
                f"{label} 回环地址测试通过",
                (status, body.get("ok") if isinstance(body, dict) else None),
                (200, True),
                local.api_base,
            )
            report.check(
                item,
                f"{label} 收到自己的 Key",
                local.auth_headers()[:1],
                [f"Bearer {key}"],
            )
    report.check(item, "两个本地端口都被接受", len(set(ports)), 2, str(ports))


# --------------------------------------------------------------------------- #
# ⑤ 安装副本 ACL：读回 DACL
# --------------------------------------------------------------------------- #

def _powershell_json(script: str) -> Any:
    proc = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;" + script,
        ],
        capture_output=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "powershell failed: "
            + proc.stderr.decode("utf-8", "replace")[:400]
        )
    raw = proc.stdout
    for encoding in ("utf-8-sig", locale_encoding()):
        try:
            text = raw.decode(encoding).strip()
            if text:
                return json.loads(text)
        except (UnicodeDecodeError, ValueError):
            continue
    raise RuntimeError(f"cannot parse ACL output: {raw[:200]!r}")


def locale_encoding() -> str:
    import locale

    return locale.getpreferredencoding(False) or "utf-8"


def read_acl(path: Path) -> Dict[str, Any]:
    """读回文件 DACL：AreAccessRulesProtected + 每条 ACE 的 SID/名称。"""
    quoted = "'" + str(path).replace("'", "''") + "'"
    script = (
        f"$p = {quoted};"
        "$a = Get-Acl -LiteralPath $p;"
        "$me = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value;"
        "$e = @($a.Access | ForEach-Object {"
        " [pscustomobject]@{"
        "  Sid = $_.IdentityReference.Translate("
        "[System.Security.Principal.SecurityIdentifier]).Value;"
        "  Name = $_.IdentityReference.Value;"
        "  Inherited = [bool]$_.IsInherited"
        " }"
        "});"
        "[pscustomobject]@{ Me = $me; Protected = [bool]$a.AreAccessRulesProtected;"
        " Entries = $e } | ConvertTo-Json -Depth 5 -Compress"
    )
    return _powershell_json(script)


def _foreign_entries(entries: List[Dict[str, Any]], me: str) -> List[Dict[str, Any]]:
    """不在“仅当前用户”范围内的 ACE。"""
    return [
        entry
        for entry in entries
        if entry.get("Sid") != me
        or entry.get("Sid") in FOREIGN_SIDS
        or "CodexSandboxUsers" in str(entry.get("Name") or "")
        or str(entry.get("Name") or "").upper().endswith("\\USERS")
    ]


def case_installed_acl(report: Report, data_dir: Path, *, required: bool = True) -> None:
    item = "⑤安装副本ACL"
    if os.name != "nt":
        print("[SKIP] ⑤安装副本ACL：非 Windows 平台不适用")
        return
    print(f"[INFO] 安装副本数据目录：{data_dir}")
    if not data_dir.is_dir():
        if required:
            report.check(item, "安装副本存在", False, True, f"not a directory: {data_dir}")
        else:
            print(
                "[SKIP] ⑤安装副本ACL：目录不存在（--allow-missing-installed；"
                "安装后复跑必须去掉该参数）"
            )
        return

    me = ""
    for relative in PRIVATE_FILES:
        path = data_dir / relative
        if not path.is_file():
            report.check(item, f"{relative} 存在", False, True, f"missing: {path}")
            continue
        try:
            info = read_acl(path)
        except Exception as exc:  # noqa: BLE001 - 读回失败必须算失败
            report.check(item, f"{relative} 读回 DACL", False, True, str(exc)[:200])
            continue
        me = str(info.get("Me") or me)
        entries = list(info.get("Entries") or [])
        summary = ", ".join(
            f"{e.get('Name')}({e.get('Sid')})" + ("[inherited]" if e.get("Inherited") else "")
            for e in entries
        )
        print(f"[证据] ACL {relative}: protected={info.get('Protected')} aces=[{summary}]")
        report.check(item, f"{relative} AreAccessRulesProtected", info.get("Protected"), True)
        report.check(
            item,
            f"{relative} 不含 Users/CodexSandboxUsers",
            [e for e in entries if e.get("Sid") in FOREIGN_SIDS
             or "CodexSandboxUsers" in str(e.get("Name") or "")
             or str(e.get("Name") or "").upper().endswith("\\USERS")],
            [],
            summary,
        )
        report.check(item, f"{relative} 仅当前用户", _foreign_entries(entries, me), [], summary)

    if me:
        print(f"[INFO] 当前用户 SID：{me}")


# --------------------------------------------------------------------------- #
# ⑥ 本机身份证明：命名管道 DACL + 一次性 handoff token
# --------------------------------------------------------------------------- #

def _dacl_of_handle(handle: Any) -> Tuple[Any, bool]:
    """对句柄读回 DACL 与“是否受保护（无继承）”。"""
    import win32security

    descriptor = win32security.GetSecurityInfo(
        handle,
        win32security.SE_KERNEL_OBJECT,
        win32security.DACL_SECURITY_INFORMATION,
    )
    control, _revision = descriptor.GetSecurityDescriptorControl()
    return descriptor.GetSecurityDescriptorDacl(), bool(
        control & win32security.SE_DACL_PROTECTED
    )


def _open_pipe_client(name: str, access: int, *, timeout: float = 5.0) -> Any:
    """连接命名管道；实例忙（ERROR_PIPE_BUSY=231）时重试。"""
    import pywintypes
    import win32file
    import win32pipe

    deadline = time.time() + timeout
    last: Optional[BaseException] = None
    while time.time() < deadline:
        try:
            return win32file.CreateFile(
                name, access, 0, None, win32file.OPEN_EXISTING, 0, None
            )
        except pywintypes.error as exc:  # noqa: PERF203 - 仅在忙时重试
            last = exc
            if getattr(exc, "winerror", None) == 231:
                try:
                    win32pipe.WaitNamedPipe(name, 500)
                except Exception:  # noqa: BLE001
                    time.sleep(0.05)
                continue
            raise
    raise RuntimeError(f"无法连接命名管道 {name}: {last}")


def _read_pipe_dacl(name: str) -> Tuple[Any, bool]:
    """跨进程读回命名管道真实 DACL（只有客户端句柄能取安全描述符）。"""
    import win32con

    handle = _open_pipe_client(name, win32con.READ_CONTROL)
    try:
        return _dacl_of_handle(handle)
    finally:
        handle.Close()


def _restricted_connect(name: str) -> Tuple[str, Any]:
    """用“当前用户 SID 被禁用”的受限令牌连接管道（模拟同机无授权主体）。

    返回 ``("denied", winerror)`` / ``("connected", None)`` / ``("error", 文本)``。
    受限令牌仍保留 ``BUILTIN\\Users`` 等组，但 DACL 只授当前用户时，被设为
    deny-only 的用户 SID 不参与授权，访问检查必须返回拒绝。
    """
    import pywintypes
    import win32api
    import win32con
    import win32security

    token = win32security.OpenProcessToken(
        win32api.GetCurrentProcess(), win32security.TOKEN_ALL_ACCESS
    )
    try:
        user_sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        restricted = win32security.CreateRestrictedToken(
            token, 0, [(user_sid, 0)], None, None
        )
    finally:
        token.Close()

    try:
        try:
            win32security.ImpersonateLoggedOnUser(restricted)
        except Exception as exc:  # noqa: BLE001 - 缺 SeImpersonatePrivilege 等
            return "error", f"无法启用受限令牌：{exc}"
        try:
            try:
                handle = _open_pipe_client(name, win32con.GENERIC_READ)
            except pywintypes.error as exc:
                return "denied", getattr(exc, "winerror", None)
            handle.Close()
            return "connected", None
        finally:
            win32security.RevertToSelf()
    finally:
        restricted.Close()


def _make_test_pipe(sid_string: str) -> Tuple[str, Any]:
    """负向对照：造一条只授指定 SID 的管道（默认 BUILTIN\\Users）。

    注意：句柄上的 DACL 必须在 ``ConnectNamedPipe`` **之前**读取，挂起连接后
    再取安全描述符会阻塞（与 ``local_proof._create_pipe`` 的约束一致）。
    """
    import ntsecuritycon
    import win32pipe
    import win32security

    name = rf"\\.\pipe\liuhao-attack-surface-neg-{os.getpid()}-{int(time.time() * 1000)}"
    sid = win32security.ConvertStringSidToSid(sid_string)
    dacl = win32security.ACL()
    dacl.AddAccessAllowedAce(
        win32security.ACL_REVISION, ntsecuritycon.FILE_ALL_ACCESS, sid
    )
    descriptor = win32security.SECURITY_DESCRIPTOR()
    descriptor.SetSecurityDescriptorDacl(1, dacl, 0)
    attributes = win32security.SECURITY_ATTRIBUTES()
    attributes.SECURITY_DESCRIPTOR = descriptor
    handle = win32pipe.CreateNamedPipe(
        name,
        win32pipe.PIPE_ACCESS_DUPLEX,
        win32pipe.PIPE_TYPE_MESSAGE
        | win32pipe.PIPE_READMODE_MESSAGE
        | win32pipe.PIPE_WAIT,
        win32pipe.PIPE_UNLIMITED_INSTANCES,
        4096,
        4096,
        0,
        attributes,
    )
    return name, handle


def _connect_quietly(handle: Any) -> None:
    import win32pipe

    try:
        win32pipe.ConnectNamedPipe(handle, None)
    except Exception:  # noqa: BLE001 - 关闭句柄会打断，属正常收尾
        pass


@contextlib.contextmanager
def _data_dir_env(data_dir: Path):
    """让本进程的 ``recovery_handoff`` 指向隔离实例的数据目录。"""
    old = os.environ.get("LIUHAO_DATA_DIR")
    os.environ["LIUHAO_DATA_DIR"] = str(data_dir)
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("LIUHAO_DATA_DIR", None)
        else:
            os.environ["LIUHAO_DATA_DIR"] = old


def _write_handoff(data_dir: Path, principal: str, code: str) -> Path:
    from src.gateway import recovery_handoff

    with _data_dir_env(data_dir):
        recovery_handoff.write_handoff(principal, code)
        return recovery_handoff.handoff_path()


def _helper_token(data_dir: Path) -> Tuple[str, int]:
    """让一个**独立子进程**取 handoff token，然后退出（令牌随进程失效）。"""
    script = (
        "import json, os, sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from pathlib import Path\n"
        "from src.common.local_proof_ipc import _transact\n"
        "raw = json.loads(_transact(data_dir=Path(sys.argv[2]), timeout=5.0).decode('utf-8'))\n"
        "raw['_child_pid'] = os.getpid()\n"
        "print(json.dumps(raw))\n"
    )
    proc = subprocess.run(
        [PY, "-c", script, REPO, str(data_dir)],
        capture_output=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "helper 取令牌失败："
            + proc.stderr.decode("utf-8", "replace")[:400]
        )
    info = json.loads(proc.stdout.decode("utf-8").strip())
    # Windows 上 Popen 的进程句柄要等对象被回收才关闭；未关闭的“已退出进程”
    # 仍能被 OpenProcess 打开，会让 bound_pid 存活判定误判。显式回收，
    # 确保断言验证的是“进程真的没了”，而不是句柄还没关。
    cleanup = getattr(subprocess, "_cleanup", None)
    if cleanup is not None:
        cleanup()
    bound = int(info.get("bound_pid") or 0)
    # 等 PID 真正不可打开（进程对象已释放），再拿它的令牌去用；否则 Windows
    # 上“已退出但句柄未关”的进程会被 OpenProcess 打开，测的就不是“换人”。
    import win32api
    import win32con

    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            probe_handle = win32api.OpenProcess(
                win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, bound
            )
            win32api.CloseHandle(probe_handle)
            time.sleep(0.05)
        except Exception:  # noqa: BLE001 - 打不开即为真正退出
            break
    else:
        raise RuntimeError(f"helper 进程 {bound} 退出后仍可打开，无法验证令牌绑定性")
    print(
        f"[证据] helper 取令牌: child_pid={info.get('_child_pid')} "
        f"bound_pid={bound}（已确认退出）"
    )
    return str(info["handoff_token"]), bound


def _scan_tree_for(root: Path, needle: str) -> List[str]:
    """在实例目录里找明文令牌；命中即说明令牌落盘。"""
    hits: List[str] = []
    raw_needle = needle.encode("utf-8")
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            path = Path(dirpath) / name
            try:
                if path.stat().st_size > 8 * 1024 * 1024:
                    continue
                if raw_needle in path.read_bytes():
                    hits.append(str(path.relative_to(root)))
            except OSError:
                continue
    return hits


def case_local_proof(report: Report, inst: Instance, token: str) -> None:
    item = "⑥本机身份证明"
    if os.name != "nt":
        print("[SKIP] ⑥本机身份证明：非 Windows 平台不适用（POSIX 为 0600 socket）")
        return
    try:
        import win32security
    except Exception as exc:  # noqa: BLE001 - 缺 pywin32 必须算失败
        report.check(item, "pywin32 可用", False, True, str(exc)[:200])
        return

    from src.common.local_proof_ipc import channel_name, request_token_bundle
    from src.common.windows_acl import (
        current_user_sid_string,
        dacl_entries,
        dacl_is_current_user_only,
    )

    me = current_user_sid_string()
    name = channel_name(Path(inst.data_dir))
    print(f"[INFO] 本机证明通道：{name}")
    print(f"[INFO] 当前用户 SID：{me}")

    # -- 1. 管道 DACL：只授当前用户、无继承 ---------------------------------
    try:
        dacl, protected = _read_pipe_dacl(name)
    except Exception as exc:  # noqa: BLE001 - 读不到 DACL 就是失败
        report.check(item, "读回管道 DACL", False, True, str(exc)[:240])
        return
    entries = dacl_entries(dacl)
    summary = ", ".join(
        f"{e['sid']}(mask={e['mask']},flags={e['flags']})" for e in entries
    )
    print(f"[证据] 管道 DACL: protected={protected} aces=[{summary}]")
    report.check(item, "管道 DACL 已去继承", protected, True)
    report.check(item, "管道 DACL 只有当前用户 SID", [e["sid"] for e in entries], [me])
    report.check(
        item,
        "管道 DACL 判定为仅当前用户",
        dacl_is_current_user_only(dacl, me),
        True,
        summary,
    )
    report.check(
        item,
        "管道 DACL 不含 Users/Authenticated Users",
        [e for e in entries if e["sid"] in FOREIGN_SIDS],
        [],
        summary,
    )

    # -- 2. 非授权主体连管道：拒绝 -------------------------------------------
    verdict, detail = _restricted_connect(name)
    report.check(item, "受限令牌连真实管道被拒绝", (verdict, detail), ("denied", 5))

    # -- 3. 负向对照：只授 Users 的管道必须被判“非仅当前用户”，且受限令牌能连 --
    neg_name, neg_handle = _make_test_pipe("S-1-5-32-545")
    try:
        neg_dacl, _neg_protected = _dacl_of_handle(neg_handle)
        report.check(
            item,
            "负向对照：仅授 Users 的管道被判 FAIL",
            dacl_is_current_user_only(neg_dacl, me),
            False,
            str(dacl_entries(neg_dacl)),
        )
        threading.Thread(
            target=lambda: _connect_quietly(neg_handle), name="neg-pipe", daemon=True
        ).start()
        time.sleep(0.05)
        neg_verdict, _neg_detail = _restricted_connect(neg_name)
        report.check(
            item,
            "负向对照：受限令牌能连上仅授 Users 的管道",
            neg_verdict,
            "connected",
            "证明“被拒”来自 DACL，而不是受限令牌本身失效",
        )
    finally:
        try:
            neg_handle.Close()
        except Exception:  # noqa: BLE001
            pass

    # -- 4. 一次性 handoff token：换人 403 / 属主可用 / 取用即焚 --------------
    from src.common.local_proof_ipc import HANDOFF_HEADER

    status, body = request(
        "POST", inst.base + "/v1/accounts/recovery/rotate", {}, token=token
    )
    code = str(body.get("recovery_code") or "") if isinstance(body, dict) else ""
    report.check(item, "取到恢复码（rotate）", (status, bool(code)), (200, True))
    if not code:
        return

    handoff_file = _write_handoff(Path(inst.data_dir), "root1", code)
    report.check(item, "交接文件已写入", handoff_file.is_file(), True, str(handoff_file))

    # “换人使用”：另一个进程取得令牌后退出（绑定进程已死），再拿来用必须 403。
    other_token, _other_pid = _helper_token(Path(inst.data_dir))
    status, body = request(
        "POST",
        inst.base + "/v1/accounts/recovery/local",
        {},
        headers={HANDOFF_HEADER: other_token},
    )
    report.check(
        item,
        "别的进程取得的令牌 → 403",
        (status, error_code(body)),
        (403, CODE_PROOF_REQUIRED),
    )
    report.check(item, "被拒后交接文件仍在（未被消耗）", handoff_file.is_file(), True)

    bundle = request_token_bundle(data_dir=Path(inst.data_dir))
    owner_token = str(bundle.get("handoff_token") or "")
    report.check(item, "令牌不落盘（实例目录无明文）", _scan_tree_for(Path(inst.root), owner_token), [])

    status, body = request(
        "POST",
        inst.base + "/v1/accounts/recovery/local",
        {},
        headers={HANDOFF_HEADER: owner_token},
    )
    report.check(item, "换回属主令牌可用", status, 200, str(body)[:200])
    report.check(
        item,
        "取回的就是刚生成的码",
        body.get("recovery_code") if isinstance(body, dict) else None,
        code,
    )
    report.check(item, "取用即焚（交接文件已删除）", handoff_file.exists(), False)

    status, body = request(
        "POST",
        inst.base + "/v1/accounts/recovery/local",
        {},
        headers={HANDOFF_HEADER: owner_token},
    )
    report.check(
        item,
        "令牌一次性（重放）→ 403",
        (status, error_code(body)),
        (403, CODE_PROOF_REQUIRED),
    )


# --------------------------------------------------------------------------- #
# ⑦ 备份/导出不含密钥：真解包扫描
# --------------------------------------------------------------------------- #

def _scan_secret_text(text: str) -> List[str]:
    """返回命中的禁用文件名 + 明文 API Key（去重、稳定排序）。"""
    hits = [name for name in FORBIDDEN_BACKUP_NAMES if name in text]
    hits.extend(sorted(set(PLAINTEXT_KEY_RE.findall(text))))
    return hits


def _backup_negative_control(report: Report, item: str) -> None:
    """故意造一份含密钥的 payload，扫描器必须全部命中。"""
    bad = {
        "format": "liuhao.account.backup.v1",
        "files": [{"path": "data/model_config.key", "content": ""}],
        "identity": "config/human_identities.json",
        "integrity": "data/integrity.key",
        "auth": "config/auth_secrets.json",
        "registry": "data/model_registry.sqlite3",
        "models": {"rows": [{"api_key_enc": "sk-live-negative-control-abcdef"}]},
    }
    hits = _scan_secret_text(json.dumps(bad, ensure_ascii=False))
    missed = [name for name in FORBIDDEN_BACKUP_NAMES if name not in hits]
    report.check(item, "负向对照：扫描器认出全部密钥文件名", missed, [], str(hits))
    report.check(
        item,
        "负向对照：扫描器认出明文 Key",
        "sk-live-negative-control-abcdef" in hits,
        True,
        str(hits),
    )


def case_backup_no_secrets(report: Report, inst: Instance, token: str) -> None:
    item = "⑦备份不含密钥"
    from src.gateway.account_backup import _open_model_secrets
    from src.kernels.identity import _backup

    _backup_negative_control(report, item)

    passphrase = "audit-backup-passphrase-123"
    with MockUpstream("backup-key") as upstream:
        status, created = create_model(
            inst,
            token,
            name="audit-backup-key",
            base_url=upstream.api_base,
            api_key=STORED_KEY,
        )
    report.check(
        item,
        "备份前确有可导出的模型 Key",
        status,
        200,
        str(created)[:160],
    )

    for scope in ("all", "self"):
        status, body = request(
            "POST",
            inst.base + "/v1/accounts/backup/export",
            {"scope": scope, "passphrase": passphrase},
            token=token,
        )
        if status != 200 or not isinstance(body, dict) or not isinstance(
            body.get("bundle"), dict
        ):
            report.check(
                item, f"scope={scope} 导出成功", (status, error_code(body)), (200, None)
            )
            continue
        bundle = body["bundle"]

        sealed_text = json.dumps(bundle, ensure_ascii=False)
        report.check(
            item,
            f"scope={scope} 密文包不含禁用名/明文 Key",
            _scan_secret_text(sealed_text),
            [],
        )

        payload = _backup.decrypt_bundle(bundle, passphrase)
        payload_text = json.dumps(payload, ensure_ascii=False)
        report.check(
            item,
            f"scope={scope} 真解包后不含禁用名/明文 Key",
            _scan_secret_text(payload_text),
            [],
        )

        paths = [
            str(entry.get("path") or "").replace("\\", "/")
            for entry in (payload.get("files") or [])
            if isinstance(entry, dict)
        ]
        bad_paths = [
            path
            for path in paths
            if any(name in path for name in FORBIDDEN_BACKUP_NAMES)
        ]
        report.check(item, f"scope={scope} 文件清单无密钥文件", bad_paths, [])

        models = payload.get("models") if isinstance(payload.get("models"), dict) else {}
        report.check(
            item, f"scope={scope} 主密钥不随包", models.get("key_material"), None
        )
        rows = models.get("rows") or []
        report.check(
            item,
            f"scope={scope} 确有模型 Key 密文（扫描样本非空）",
            len(rows) >= 1,
            True,
            f"rows={len(rows)}",
        )
        secrets = _open_model_secrets(models.get("key_vault"), passphrase)
        known = {STORED_KEY, REENTERED_KEY, LOCAL_KEY_A, LOCAL_KEY_B}
        report.check(
            item,
            f"scope={scope} Key 以口令密封携带",
            bool(known & set(secrets.values())),
            True,
            f"vault_keys={len(secrets)}",
        )


# --------------------------------------------------------------------------- #
# ⑧ 卸载残留：受控模拟卸载（不跑 setup.exe）
# --------------------------------------------------------------------------- #

AUTOSTART_VALUE_NAME = "LiuHaoAIOS"


def _reg_set(path: str, name: str, value: str) -> None:
    import winreg

    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE
    ) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)


def _reg_value(path: str, name: str) -> Optional[str]:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ) as key:
            value, _kind = winreg.QueryValueEx(key, name)
            return str(value)
    except FileNotFoundError:
        return None
    except OSError:
        return None


def _reg_key_exists(path: str) -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ):
            return True
    except OSError:
        return False


def _reg_delete_value(path: str, name: str) -> None:
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, name)
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _reg_delete_tree(path: str) -> None:
    """递归删除探针自建的注册表根；只用于 ``LiuHaoAcceptance`` 测试键。"""
    import winreg

    subs: List[str] = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ) as key:
            index = 0
            while True:
                try:
                    subs.append(winreg.EnumKey(key, index))
                except OSError:
                    break
                index += 1
    except FileNotFoundError:
        return
    except OSError:
        return
    for sub in subs:
        _reg_delete_tree(path + "\\" + sub)
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    try:
        import win32api
        import win32con
    except Exception:  # noqa: BLE001 - 缺 pywin32 时按“无法确认存活”处理
        return False
    try:
        handle = win32api.OpenProcess(
            win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        win32api.CloseHandle(handle)
        return True
    except Exception:  # noqa: BLE001 - 打不开即已退出
        return False


def _port_free(port: int) -> bool:
    import socket

    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _wait_port_free(port: int, *, timeout: float = 5.0) -> bool:
    """等端口释放：进程退出后套接字回收不是瞬时的，立刻判定会假红。"""
    deadline = time.time() + timeout
    while True:
        if _port_free(port):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(0.1)


class _UninstallFixture:
    """在临时目录里搭一套“安装完成”的足迹，用受控操作模拟卸载。

    方法边界（重要）：

    * **不运行** ``setup.exe`` / ``unins000.exe``，也不碰用户真实的
      ``HKCU\\...\\CurrentVersion\\Run``、真实开始菜单/桌面；
    * 自启项写在探针专属的 ``HKCU\\Software\\LiuHaoAcceptance\\<token>\\Run``，
      快捷方式写在临时“桌面/开始菜单”目录，进程是探针自起的回环监听进程；
    * 检查逻辑与真实卸载相同：看目录、``data-dir.txt``、快捷方式、卸载键、
      自启值、进程存活、端口占用。真实安装器是否具备等价清理步骤，另由
      ``[INFO]`` 静态审计提示，最终仍需装机实测。
    """

    def __init__(self) -> None:
        token = os.urandom(6).hex()
        self.root = Path(tempfile.mkdtemp(prefix="liuhao-uninstall-"))
        self.install = self.root / "LiuHao"
        self.data = self.root / "LiuHaoData"
        self.desktop = self.root / "Desktop"
        self.start_menu = self.root / "StartMenu"
        self.reg_root = rf"Software\LiuHaoAcceptance\{token}"
        self.run_key = self.reg_root + r"\Run"
        self.uninstall_key = self.reg_root + r"\Uninstall"
        self.value_name = AUTOSTART_VALUE_NAME
        self.proc: Optional[subprocess.Popen] = None
        self.port: Optional[int] = None

    @property
    def shortcuts(self) -> List[Path]:
        return [
            self.desktop / "LiuHao.lnk",
            self.start_menu / "LiuHao.lnk",
            self.start_menu / "卸载 LiuHao.lnk",
        ]

    def setup(self) -> None:
        (self.install / "runtime").mkdir(parents=True)
        (self.install / "scripts").mkdir()
        (self.install / "assets").mkdir()
        for rel in ("runtime/python.exe", "runtime/pythonw.exe",
                    "scripts/liuhao_desktop.py", "assets/liuhao.ico"):
            (self.install / rel).write_text("stub", encoding="utf-8")
        (self.install / "data-dir.txt").write_text(str(self.data), encoding="utf-8")

        (self.data / "config").mkdir(parents=True)
        (self.data / "data").mkdir()
        (self.data / "config" / "app.env").write_text(
            "APP_ENV=production\n", encoding="utf-8"
        )
        (self.data / "data" / "model_registry.sqlite3").write_bytes(
            b"SQLite format 3\x00"
        )

        self.desktop.mkdir()
        self.start_menu.mkdir()
        for shortcut in self.shortcuts:
            shortcut.write_text("stub-lnk", encoding="utf-8")

        command = (
            f'"{self.install / "runtime" / "pythonw.exe"}" '
            f'"{self.install / "scripts" / "liuhao_desktop.py"}" app --open-browser'
        )
        _reg_set(self.run_key, self.value_name, command)
        _reg_set(self.uninstall_key, "InstallLocation", str(self.install))
        _reg_set(self.uninstall_key, "DataDir", str(self.data))

        self._start_tray()

    def _start_tray(self) -> None:
        script = (
            "import socket, time\n"
            "s = socket.socket()\n"
            "s.bind(('127.0.0.1', 0))\n"
            "s.listen(1)\n"
            "print(s.getsockname()[1], flush=True)\n"
            "time.sleep(300)\n"
        )
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.proc = subprocess.Popen(
            [PY, "-u", "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        line = ""
        if self.proc.stdout is not None:
            line = self.proc.stdout.readline().decode("utf-8", "replace").strip()
        self.port = int(line) if line.isdigit() else None

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
        if self.proc is not None:
            try:
                self.proc.wait(timeout=10)
            except Exception:  # noqa: BLE001
                pass
        _reg_delete_tree(self.reg_root)
        for _ in range(10):
            if not self.root.exists():
                break
            shutil.rmtree(self.root, ignore_errors=True)
            time.sleep(0.1)


def _simulate_uninstall(fixture: _UninstallFixture, *, delete_data: bool) -> None:
    """按正确的卸载契约执行清理（用于验证残留检查器）。

    顺序与真实卸载一致：先停托盘/网关 → 清自启项 → 删快捷方式/程序目录 →
    删 HKCU 卸载键 → 按用户选择保留或删除数据目录。
    """
    if fixture.proc is not None and fixture.proc.poll() is None:
        fixture.proc.kill()
        try:
            fixture.proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            pass
    cleanup = getattr(subprocess, "_cleanup", None)
    if cleanup is not None:
        cleanup()

    _reg_delete_value(fixture.run_key, fixture.value_name)

    for shortcut in fixture.shortcuts:
        try:
            shortcut.unlink()
        except FileNotFoundError:
            pass

    (fixture.install / "data-dir.txt").unlink(missing_ok=True)
    shutil.rmtree(fixture.install, ignore_errors=True)
    _reg_delete_tree(fixture.uninstall_key)

    if delete_data:
        shutil.rmtree(fixture.data, ignore_errors=True)


def _residue_problems(
    fixture: _UninstallFixture, *, expect_data: bool
) -> List[str]:
    """卸载后残留清单；空列表 = 干净。"""
    problems: List[str] = []
    if fixture.install.exists():
        problems.append("install_dir")
    if (fixture.install / "data-dir.txt").exists():
        problems.append("data-dir.txt")
    for shortcut in fixture.shortcuts:
        if shortcut.exists():
            problems.append(f"shortcut:{shortcut.name}")
    if _reg_key_exists(fixture.uninstall_key):
        problems.append("uninstall_key")
    if _reg_value(fixture.run_key, fixture.value_name) is not None:
        problems.append("autostart")
    if fixture.proc is not None:
        # Popen 自身持有进程句柄时 OpenProcess 仍能打开已退出进程（Windows
        # 进程对象语义），因此对自己的子进程以 poll() 是否已回收为准。
        if fixture.proc.poll() is None:
            problems.append(f"process:{fixture.proc.pid}")
    if fixture.port is not None and not _wait_port_free(fixture.port):
        problems.append(f"port:{fixture.port}")
    if expect_data:
        if not (fixture.data / "data" / "model_registry.sqlite3").is_file():
            problems.append("data_lost")
    elif fixture.data.exists():
        problems.append("data_not_deleted")
    return problems


def _installer_spec_text() -> str:
    """读取安装器脚本原文；读不到返回空串（随后所有规格断言判 FAIL）。"""
    iss = Path(REPO) / "installer" / "liuhao.iss"
    try:
        return iss.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _strip_iss_comments(text: str) -> str:
    """去掉 Inno 的整行注释（首个非空字符是 ``;``）。"""
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith(";")
    )


def _iss_section(text: str, name: str) -> str:
    """取出 ``[name]`` 段内容（到下一个段头为止）。"""
    wanted = name.lower()
    lines: List[str] = []
    active = False
    for line in text.splitlines():
        header = re.match(r"^\s*\[([^\]]+)\]\s*$", line)
        if header:
            active = header.group(1).strip().lower() == wanted
            continue
        if active:
            lines.append(line)
    return "\n".join(lines)


def _installer_spec_audit(text: str) -> Dict[str, bool]:
    """审计卸载契约是否写进安装器（不依赖人工读注释）。

    * ``stop``：``[UninstallRun]`` 在删文件前调用 ``-m src.desktop stop``；
    * ``autostart``：卸载时走产品命令 ``autostart disable``，不手写注册表；
    * ``close``：``[Setup]`` 里 ``CloseApplications=yes``；
    * ``no_manual_reg``：全文不出现自启值的注册表路径，避免“删键假装关自启”。
    """
    clean = _strip_iss_comments(text).lower()
    uninstall_run = _iss_section(clean, "UninstallRun")
    setup = _iss_section(clean, "Setup")
    return {
        "stop": "-m src.desktop stop" in uninstall_run,
        "autostart": "autostart disable" in uninstall_run,
        "close": re.search(
            r"(?im)^\s*closeapplications\s*=\s*yes\s*$", setup
        )
        is not None,
        "no_manual_reg": "currentversion\\run" not in clean
        and "liuhaoaios" not in clean,
    }


def _comment_out_uninstall_actions(text: str) -> str:
    """反向验证用：把 ``[UninstallRun]`` 里的停进程/关自启两行注释掉。"""
    out: List[str] = []
    in_section = False
    for line in text.splitlines():
        header = re.match(r"^\s*\[([^\]]+)\]\s*$", line)
        if header:
            in_section = header.group(1).strip().lower() == "uninstallrun"
        if (
            in_section
            and line.lstrip().lower().startswith("filename:")
            and ("src.desktop stop" in line or "autostart disable" in line)
        ):
            out.append("; " + line)
        else:
            out.append(line)
    return "\n".join(out)


def case_uninstall_residue(report: Report) -> None:
    item = "⑧卸载残留"
    if os.name != "nt":
        print("[SKIP] ⑧卸载残留：非 Windows 平台不适用")
        return

    # 只读：当前机器真实自启登记（不改）
    try:
        from src.desktop.autostart import read as read_autostart

        info = read_autostart()
        print(
            f"[INFO] 真实 HKCU 自启项：enabled={info.get('enabled')} "
            f"value={info.get('value_name')}"
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[INFO] 读取真实自启项失败：{exc}")

    # 安装器规格必须是硬断言：读不到声明就 FAIL，不再降级成 INFO。
    audit = _installer_spec_audit(_installer_spec_text())
    print(f"[INFO] 安装器规格审计：{audit}")
    report.check(
        item, "安装器规格：卸载前先停托盘/网关（[UninstallRun]）", audit["stop"], True
    )
    report.check(
        item, "安装器规格：卸载时走产品命令关自启（不手写注册表）", audit["autostart"], True
    )
    report.check(item, "安装器规格：CloseApplications=yes", audit["close"], True)
    report.check(
        item, "安装器规格：全文不手写自启注册表删除", audit["no_manual_reg"], True
    )

    # -- 场景 A：默认卸载（保留数据） ---------------------------------------
    fixture = _UninstallFixture()
    try:
        fixture.setup()
        pid = fixture.proc.pid if fixture.proc is not None else 0
        report.check(
            item,
            "夹具：自启/快捷方式/托盘进程/端口均已就位",
            (
                _reg_value(fixture.run_key, fixture.value_name) is not None,
                all(path.exists() for path in fixture.shortcuts),
                pid > 0 and _pid_alive(pid),
                fixture.port is not None and not _port_free(fixture.port),
            ),
            (True, True, True, True),
        )
        _simulate_uninstall(fixture, delete_data=False)
        report.check(
            item,
            "默认卸载后无残留（目录/自启/快捷方式/卸载键/进程/端口）",
            _residue_problems(fixture, expect_data=True),
            [],
        )
        report.check(
            item,
            "默认卸载后数据目录保留且内容完好",
            (fixture.data / "data" / "model_registry.sqlite3").is_file(),
            True,
        )
    finally:
        fixture.stop()

    # -- 场景 B：明确删除数据 -----------------------------------------------
    fixture = _UninstallFixture()
    try:
        fixture.setup()
        _simulate_uninstall(fixture, delete_data=True)
        report.check(
            item,
            "明确删除数据后无残留且数据目录已删除",
            _residue_problems(fixture, expect_data=False),
            [],
        )
    finally:
        fixture.stop()

    # -- 负向对照：故意留自启项与托盘进程，同一检查器必须判红 ----------------
    fixture = _UninstallFixture()
    try:
        fixture.setup()
        for shortcut in fixture.shortcuts:
            shortcut.unlink(missing_ok=True)
        shutil.rmtree(fixture.install, ignore_errors=True)
        _reg_delete_tree(fixture.uninstall_key)
        problems = _residue_problems(fixture, expect_data=True)
        print(f"[证据] 负向卸载残留判定：{problems}")
        report.check(
            item,
            "负向对照：残留自启项被判红",
            "autostart" in problems,
            True,
            str(problems),
        )
        report.check(
            item,
            "负向对照：残留托盘进程/端口被判红",
            any(p.startswith(("process:", "port:")) for p in problems),
            True,
            str(problems),
        )
    finally:
        fixture.stop()


# --------------------------------------------------------------------------- #
# 负向对照：故意把坏输入喂给 ⑥⑦⑧ 的同一条断言，脚本必须报 FAIL
# --------------------------------------------------------------------------- #

def run_negative_controls() -> int:
    """不启网关，只验证“断言真的会红”。

    用与正式运行**完全相同**的断言函数：
    * ⑥ 把只授 ``BUILTIN\\Users`` 的管道当成真管道 → 必须 FAIL；
    * ⑦ 把含 ``model_config.key`` + 明文 Key 的 payload 当成真备份 → 必须 FAIL；
    * ⑧ 故意留下自启项与托盘进程 → 残留检查器必须报非空。
    """
    report = Report()
    item = "负向对照"
    if os.name != "nt":
        print("[SKIP] 负向对照 ⑥：非 Windows 平台不适用")
    else:
        from src.common.windows_acl import (
            current_user_sid_string,
            dacl_entries,
        )

        me = current_user_sid_string()
        name, handle = _make_test_pipe("S-1-5-32-545")
        try:
            dacl, _protected = _dacl_of_handle(handle)
            entries = dacl_entries(dacl)
            print(f"[证据] 负向管道 {name} aces={entries}")
            report.check(
                item + "⑥",
                "只授 Users 的管道被判为仅当前用户",
                [entry["sid"] for entry in entries],
                [me],
            )
        finally:
            handle.Close()

    leaky = {
        "format": "liuhao.account.backup.v1",
        "files": [{"path": "data/model_config.key", "content": "MIIB..."}],
        "identity": "config/human_identities.json",
        "integrity": "data/integrity.key",
        "auth": "config/auth_secrets.json",
        "registry": "data/model_registry.sqlite3",
        "models": {"rows": [{"api_key_enc": "sk-live-negative-control-abcdef"}]},
    }
    hits = _scan_secret_text(json.dumps(leaky, ensure_ascii=False))
    print(f"[证据] 负向备份包命中: {hits}")
    report.check(item + "⑦", "含密钥的备份包被判为不含密钥", hits, [])

    if os.name == "nt":
        fixture = _UninstallFixture()
        try:
            fixture.setup()
            for shortcut in fixture.shortcuts:
                shortcut.unlink(missing_ok=True)
            shutil.rmtree(fixture.install, ignore_errors=True)
            _reg_delete_tree(fixture.uninstall_key)
            problems = _residue_problems(fixture, expect_data=True)
            print(f"[证据] 负向卸载残留判定: {problems}")
            report.check(item + "⑧", "残留自启/进程被判为无残留", problems, [])
        finally:
            fixture.stop()

    # 反向验证：把 [UninstallRun] 的停进程/关自启两行注释掉，规格闸必须报红。
    commented = _comment_out_uninstall_actions(_installer_spec_text())
    commented_audit = _installer_spec_audit(commented)
    print(f"[证据] 注释掉 [UninstallRun] 卸载动作后的规格审计: {commented_audit}")
    report.check(
        item + "⑨",
        "注释掉卸载动作后规格闸必须判红",
        commented_audit,
        {"stop": True, "autostart": True, "close": True, "no_manual_reg": True},
    )

    failed = report.failed()
    print(f"结果：{len(report.rows) - len(failed)}/{len(report.rows)} 通过")
    return 1 if failed else 0


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #

def default_installed_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "LiuHao" / "data"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="本机攻击面回归探针")
    parser.add_argument(
        "--installed-data-dir",
        default=os.environ.get("LIUHAO_INSTALLED_DATA_DIR") or str(default_installed_data_dir()),
        help="安装副本数据目录（默认 %%LOCALAPPDATA%%\\LiuHao\\data）",
    )
    parser.add_argument(
        "--allow-missing-installed",
        action="store_true",
        help="安装副本不存在时跳过 ⑤（打包前置门禁用；安装后复跑不要带此参数）",
    )
    parser.add_argument(
        "--negative-control",
        action="store_true",
        help="只跑负向对照：故意把含密钥/只授 Users 的输入喂给同一条断言，必须 FAIL 退出 1",
    )
    args = parser.parse_args(argv)

    if args.negative_control:
        return run_negative_controls()

    report = Report()
    inst = Instance("liuhao-attack-surface-")
    legit: Optional[MockUpstream] = None
    attacker: Optional[MockUpstream] = None
    other: Optional[MockUpstream] = None
    try:
        token = bootstrap(inst)
        legit = MockUpstream("legit")
        attacker = MockUpstream("attacker")
        other = MockUpstream("other-port")
        for mock in (legit, attacker, other):
            mock.__enter__()

        model_id = case_egress(report, inst, token, legit, attacker)
        case_port_is_target(report, inst, token, model_id, other)
        case_rekey_and_confirm(report, inst, token, model_id, attacker, other)
        case_loopback_ports(report, inst, token)
        case_local_proof(report, inst, token)
        case_backup_no_secrets(report, inst, token)
    finally:
        for mock in (legit, attacker, other):
            if mock is not None:
                mock.close()
        inst.stop()
        # Windows 上网关子进程可能比启动器晚一拍退出；补几次回收，避免 %TEMP%
        # 里残留带 SQLite -shm 的实例目录（ignore_errors 会吞掉首次失败）。
        for _ in range(20):
            if not Path(inst.root).exists():
                break
            shutil.rmtree(inst.root, ignore_errors=True)
            time.sleep(0.1)

    case_uninstall_residue(report)

    case_installed_acl(
        report,
        Path(args.installed_data_dir),
        required=not args.allow_missing_installed,
    )

    failed = report.failed()
    print(f"\n结果：{len(report.rows) - len(failed)}/{len(report.rows)} 通过")
    for row in failed:
        print(
            f"  FAIL {row['item']} · {row['check']}: got={row['got']!r} "
            f"expected={row['expected']!r} {row['evidence']}"
        )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
