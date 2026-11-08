"""用量账本与成本核对（真实 HTTP + 真实 SQLite + 真实审计链）。

场景：根账号 + 两个子账号，各自建自己的 Key（Ollama qwen2.5:3b），
分别跑成功轮、根借用子账号 Key 的轮次、以及三类失败轮（超时 / 凭据 401 /
配额 429）和本地预算拒绝轮。随后核对：

1. 每轮成功是否恰好记一行（calls/tokens）；
2. 子账号用量是否只算自己、根借用后子账号自己的数字不变、根侧可查；
3. 失败轮是否被错误计入用量；
4. 未定价模型是否被算成 0 元 / 预算超限是否真的拦住；
5. 账本行与审计事件能否用 ``correlation_id`` 对上。

Provider 说明（必须如实写清）:
* 成功轮 = 本机 Ollama ``qwen2.5:3b``（http://127.0.0.1:11434），
  provider 在响应里返回真实 usage，账本 ``estimated=0``；
* 失败轮 = 本脚本内的本机 stub HTTP server（100% 本地，不出网），
  按模型名分别模拟超时 / 401 / 429；
* 预算轮 = ``mock`` provider + 价目表里的 ``mock-priced`` 模型。

运行：``.venv\\Scripts\\python.exe scripts/acceptance/usage_ledger_reconciliation.py``
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from account_tree_e2e import Instance, Report, error_code, request  # noqa: E402

PASSWORDS = {"root1": "rootpass123", "child_a": "childpass123", "child_b": "childpass456"}
OLLAMA_BASE = "http://127.0.0.1:11434"
OLLAMA_MODEL = "qwen2.5:3b"
SUCCESS_ROUNDS = {"child_a": 2, "child_b": 2, "root1": 1, "root_borrow_a": 2, "root_borrow_b": 1}


# --------------------------------------------------------------------------- #
# 本机 stub provider（模拟失败，不出网）
# --------------------------------------------------------------------------- #
class _StubHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D401 - 静默
        return

    def do_POST(self):  # noqa: N802
        try:
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            payload = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            payload = {}
        model = str(payload.get("model") or "")
        if "stub-timeout" in model:
            # provider 侧 timeout=2s，这里睡 8s 触发真实读超时。
            time.sleep(8)
            self._send(200, {"message": {"content": "late"}, "done": True})
            return
        if "stub-401" in model:
            self._send(401, {"error": "invalid api key"})
            return
        if "stub-429" in model:
            self._send(429, {"error": "quota exceeded"})
            return
        self._send(200, {"message": {"content": "stub-ok"}, "done": True})

    def _send(self, code: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class StubServer:
    def __init__(self) -> None:
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
        self.port = int(self.httpd.server_address[1])
        self.base = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self) -> "StubServer":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


# --------------------------------------------------------------------------- #
# 辅助
# --------------------------------------------------------------------------- #
def ollama_ready() -> bool:
    try:
        with urllib.request.urlopen(OLLAMA_BASE + "/api/tags", timeout=4) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return any(item.get("name") == OLLAMA_MODEL for item in data.get("models") or [])
    except Exception:
        return False


def create_model(
    inst: Instance,
    token: str,
    *,
    name: str,
    provider: str,
    model: str,
    owner: str,
    base_url: Optional[str] = None,
    timeout: float = 30.0,
) -> Dict[str, Any]:
    payload = {
        "name": name,
        "provider": provider,
        "model": model,
        "owner_principal": owner,
        "is_default": False,
        "timeout": timeout,
        "enabled": True,
    }
    if base_url:
        payload["base_url"] = base_url
    status, body = request("POST", inst.base + "/v1/models", payload, token=token)
    if status != 200:
        raise RuntimeError(f"create model {name} failed: {status} {body}")
    return body


def chat(
    inst: Instance, token: str, *, model_id: str, session: str, text: str
) -> Tuple[int, Dict[str, Any]]:
    return request(
        "POST",
        inst.base + "/v1/chat",
        {
            "message": text,
            "session_id": session,
            "model_id": model_id,
            "use_knowledge": False,
        },
        token=token,
    )


def usage_db(inst: Instance) -> sqlite3.Connection:
    path = inst.env["LIUHAO_ACCOUNT_USAGE_DB"]
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def db_rows(inst: Instance, where: str = "", params: tuple = ()) -> List[sqlite3.Row]:
    sql = (
        "SELECT id, actor, key_owner, model, tokens_in, tokens_out, estimated, "
        "session_id, correlation_id FROM account_usage_events "
    )
    if where:
        sql += "WHERE " + where + " "
    sql += "ORDER BY id"
    with usage_db(inst) as conn:
        return conn.execute(sql, params).fetchall()


def audit_for(inst: Instance, token: str, correlation_id: str) -> List[Dict[str, Any]]:
    status, body = request(
        "GET",
        inst.base + f"/v1/audit/events?correlation_id={correlation_id}&limit=100",
        token=token,
    )
    if status != 200:
        raise RuntimeError(f"audit query failed: {status} {body}")
    return body.get("events") or []


def bootstrap(report: Report, inst: Instance) -> Dict[str, str]:
    item = "账本准备"
    status, body = request(
        "POST",
        inst.base + "/v1/accounts/register",
        {"principal": "root1", "password": PASSWORDS["root1"], "display_name": "主账号"},
    )
    report.check(item, "注册根账号", status, 200, f"recovery={bool(body.get('recovery_code'))}")
    status, body = request(
        "POST", inst.base + "/v1/auth/login",
        {"principal": "root1", "secret": PASSWORDS["root1"]},
    )
    root = body.get("access_token") or ""
    report.check(item, "根登录", status, 200)

    tokens = {"root1": root}
    for child in ("child_a", "child_b"):
        status, body = request(
            "POST",
            inst.base + "/v1/accounts",
            {"principal": child, "role": "manager", "allow_key_sharing": True},
            token=root,
        )
        report.check(item, f"根建 {child}", status, 200, f"code={bool(body.get('activation_code'))}")
        status, activated = request(
            "POST",
            inst.base + "/v1/accounts/activate",
            {
                "principal": child,
                "code": body.get("activation_code") or "",
                "password": PASSWORDS[child],
            },
        )
        report.check(item, f"{child} 激活", status, 200)
        status, login = request(
            "POST", inst.base + "/v1/auth/login",
            {"principal": child, "secret": PASSWORDS[child]},
        )
        tokens[child] = login.get("access_token") or ""
        report.check(item, f"{child} 登录", status, 200, f"depth={login.get('depth')}")
    return tokens


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main() -> int:
    report = Report()
    recon: List[Dict[str, Any]] = []
    api_views: List[Dict[str, Any]] = []
    stub = StubServer().start()
    pricing = os.path.join(REPO, ".tmp_usage_recon_pricing.json")
    with open(pricing, "w", encoding="utf-8") as handle:
        json.dump({"mock-priced": 0.5}, handle)

    if not ollama_ready():
        print(f"[FATAL] 本机 Ollama 不可用或缺少 {OLLAMA_MODEL}，无法跑真实 token 轮次。")
        stub.stop()
        os.unlink(pricing)
        return 2

    inst = (
        Instance(
            "usage-recon-",
            env_extra={
                # 失败的 stub 也是"本机 ollama 端点"，仅用于通过私网端点校验。
                "OLLAMA_BASE_URL": stub.base,
                "LIUHAO_MODEL_PRICING_FILE": pricing,
                # 极小预算：未定价模型不受影响（成本未知 ⇒ 规则不生效）；
                # 定价模型必须被拦住。
                "LIUHAO_DEFAULT_COST_QUOTA": "0.000001",
            },
            start=False,
        )
    )
    try:
        inst.start()
        print(f"[info] 实例 {inst.base} 数据目录 {inst.root}")
        tokens = bootstrap(report, inst)

        # ---- 建模 --------------------------------------------------------
        item = "Key 与模型"
        models: Dict[str, str] = {}
        for owner in ("root1", "child_a", "child_b"):
            body = create_model(
                inst, tokens[owner],
                name=f"{owner}-own", provider="ollama", model=OLLAMA_MODEL,
                owner=owner, base_url=OLLAMA_BASE, timeout=60,
            )
            models[owner] = body.get("id") or ""
            pricing_info = body.get("pricing") or {}
            report.check(item, f"{owner} 模型创建", bool(models[owner]), True, models[owner])
            report.check(
                item, f"{owner} qwen 未定价提示",
                (pricing_info.get("priced"), "未定价" in str(pricing_info.get("note"))),
                (False, True),
            )
        for name, model_name in (
            ("stub-timeout", "stub-timeout"),
            ("stub-401", "stub-401"),
            ("stub-429", "stub-429"),
        ):
            body = create_model(
                inst, tokens["root1"],
                name=f"root-{name}", provider="ollama", model=model_name,
                owner="root1", base_url=stub.base, timeout=2.0,
            )
            models[name] = body.get("id") or ""
        priced = create_model(
            inst, tokens["root1"],
            name="root-priced", provider="mock", model="mock-priced", owner="root1",
        )
        models["priced"] = priced.get("id") or ""
        report.check(
            item, "定价模型标注 priced",
            ((priced.get("pricing") or {}).get("priced"), (priced.get("pricing") or {}).get("price_per_1k_tokens")),
            (True, 0.5),
        )

        # ---- 成功轮（真实 Ollama usage） ---------------------------------
        item = "成功轮记账"
        success: List[Dict[str, Any]] = []
        def run_success(actor: str, model_owner: str, label: str) -> None:
            for idx in range(SUCCESS_ROUNDS[label]):
                last: Tuple[int, Dict[str, Any]] = (0, {})
                for attempt in range(3):
                    status, body = chat(
                        inst, tokens[actor],
                        model_id=models[model_owner],
                        session=f"{actor}-{label}-{idx}-a{attempt}",
                        text="只回复两个字：收到",
                    )
                    last = (status, body)
                    if status == 200 and body.get("status") == "completed":
                        break
                    report.check(
                        item, f"{label} 第{idx + 1}轮第{attempt + 1}次尝试失败（不计账）",
                        body.get("status"), "error",
                        f"corr={body.get('correlation_id')}",
                    )
                status, body = last
                ok = status == 200 and body.get("status") == "completed"
                report.check(
                    item, f"{label} 第{idx + 1}轮 {actor}→{model_owner}",
                    (status, body.get("status")), (200, "completed"),
                    f"corr={body.get('correlation_id')} reply={str(body.get('reply'))[:20]}",
                )
                if ok:
                    success.append(
                        {
                            "label": label, "actor": actor, "model_owner": model_owner,
                            "correlation_id": str(body.get("correlation_id") or ""),
                            "session": f"{actor}-{label}-{idx}",
                        }
                    )

        run_success("child_a", "child_a", "child_a")
        run_success("child_b", "child_b", "child_b")
        run_success("root1", "root1", "root1")
        run_success("root1", "child_a", "root_borrow_a")
        run_success("root1", "child_b", "root_borrow_b")
        report.check(item, "成功轮总数", len(success), 8)

        # ---- 失败轮 ------------------------------------------------------
        item = "失败轮不计用量"
        failures: List[Dict[str, Any]] = []
        for label, model_key, expect_error in (
            ("超时", "stub-timeout", None),
            ("provider 401（Key 无效）", "stub-401", None),
            ("provider 429（额度耗尽）", "stub-429", None),
        ):
            before = len(db_rows(inst))
            status, body = chat(
                inst, tokens["root1"], model_id=models[model_key],
                session=f"fail-{uuid.uuid4().hex[:6]}", text="失败轮",
            )
            after = len(db_rows(inst))
            report.check(
                item, f"{label} 返回非 completed",
                (status, body.get("status")), (200, "error"),
                f"corr={body.get('correlation_id')}",
            )
            report.check(item, f"{label} 不新增用量行", after - before, 0)
            failures.append(
                {"label": label, "status": status, "body": body, "rows_delta": after - before}
            )

        before = len(db_rows(inst))
        status, body = chat(
            inst, tokens["child_b"], model_id=models["root1"],
            session="fail-key-owner", text="越权用根 Key",
        )
        after = len(db_rows(inst))
        report.check(
            item, "子账号不能借用上级 Key",
            (status, error_code(body)), (403, "ACCOUNT_KEY_NOT_OWNED"),
        )
        report.check(item, "越权 Key 不新增用量行", after - before, 0)
        fail_key_corr = str(body.get("correlation_id") or "")
        report.check(item, "越权 403 带回 correlation_id", bool(fail_key_corr), True)
        key_events = [
            e for e in audit_for(inst, tokens["root1"], fail_key_corr)
            if (e.get("details") or {}).get("action") == "key.use"
        ] if fail_key_corr else []
        report.check(
            item, "403 key.use 审计可按 correlation_id 对上",
            (
                len(key_events),
                key_events[0].get("outcome") if key_events else None,
            ),
            (1, "denied"),
            f"corr={fail_key_corr}",
        )
        failures.append({"label": "越权 Key", "status": status, "body": body, "rows_delta": after - before})

        before = len(db_rows(inst))
        status, body = chat(
            inst, tokens["root1"], model_id=models["priced"],
            session="budget-denied", text="预算超限轮",
        )
        after = len(db_rows(inst))
        # 预算拒绝现在是 402（与权限拒绝 403 区分），正文给机器码与 correlation_id。
        report.check(item, "预算超限返回 402（非 500/非 200 伪成功）", status, 402, str(body)[:160])
        report.check(item, "预算超限 code=budget_exceeded", body.get("code"), "budget_exceeded", str(body)[:160])
        budget_message = str(body.get("message") or "")
        report.check(
            item, "预算文案说预算、不提权限",
            (budget_message.count("预算"), "权限" in budget_message), (1, False),
            budget_message[:120],
        )
        report.check(item, "预算拒绝带 correlation_id", bool(body.get("correlation_id")), True)
        report.check(item, "预算超限不新增用量行", after - before, 0)
        corr = str(body.get("correlation_id") or "")
        budget_events = audit_for(inst, tokens["root1"], corr) if corr else []
        report.check(
            item, "预算拒绝可按 correlation_id 在审计页对上",
            (
                len(budget_events),
                (budget_events[0].get("details") or {}).get("reason") if budget_events else None,
            ),
            (1, "budget_exceeded"),
            f"corr={corr}",
        )
        failures.append({"label": "本地预算超限", "status": status, "body": body, "rows_delta": after - before})

        # ---- 账本对账 ----------------------------------------------------
        item = "账本对账"
        rows = db_rows(inst)
        counts: Dict[Tuple[str, str], int] = {}
        for row in rows:
            counts[(row["actor"], row["key_owner"])] = counts.get((row["actor"], row["key_owner"]), 0) + 1
        # 期望只来自**真实 completed 的轮次**：上游失败轮按契约返回
        # 200 + status=error 且不产生用量行（见 test_p1_8_non_stream_failure_skips_usage），
        # 不能把它们算进账本分母；轮次不足由上面的「成功轮总数」如实报失败。
        expected_counts: Dict[Tuple[str, str], int] = {}
        for item_success in success:
            key = (item_success["actor"], item_success["model_owner"])
            expected_counts[key] = expected_counts.get(key, 0) + 1
        report.check(item, "账本行数按 actor×key_owner", counts, expected_counts)
        report.check(item, "账本总行数", len(rows), len(success))
        for key, expected_calls in expected_counts.items():
            actual_rows = [r for r in rows if (r["actor"], r["key_owner"]) == key]
            recon.append(
                {
                    "actor": key[0],
                    "key_owner": key[1],
                    "expected_calls": expected_calls,
                    "actual_calls": len(actual_rows),
                    "tokens_in": sum(int(r["tokens_in"]) for r in actual_rows),
                    "tokens_out": sum(int(r["tokens_out"]) for r in actual_rows),
                    "estimated": sorted({int(r["estimated"]) for r in actual_rows}),
                    "ok": len(actual_rows) == expected_calls,
                }
            )
        report.check(item, "每行非空 correlation_id", all(r["correlation_id"] for r in rows), True)
        report.check(item, "每行 token 非 0", all(r["tokens_in"] > 0 and r["tokens_out"] > 0 for r in rows), True)
        report.check(
            item, "Ollama 轮次均为真实 usage（estimated=0）",
            sorted({int(r["estimated"]) for r in rows}), [0],
        )

        status, child_a_self = request("GET", inst.base + "/v1/accounts/child_a/usage", token=tokens["child_a"])
        status2, child_b_self = request("GET", inst.base + "/v1/accounts/child_b/usage", token=tokens["child_b"])
        status3, root_self = request("GET", inst.base + "/v1/accounts/root1/usage", token=tokens["root1"])
        status4, root_a = request("GET", inst.base + "/v1/accounts/child_a/usage", token=tokens["root1"])
        status5, root_b = request("GET", inst.base + "/v1/accounts/child_b/usage", token=tokens["root1"])
        self_a = expected_counts.get(("child_a", "child_a"), 0)
        self_b = expected_counts.get(("child_b", "child_b"), 0)
        borrow_a = expected_counts.get(("root1", "child_a"), 0)
        borrow_b = expected_counts.get(("root1", "child_b"), 0)
        report.check(item, "子账号 A 自己视角 calls", (status, child_a_self.get("calls")), (200, self_a))
        report.check(item, "子账号 B 自己视角 calls", (status2, child_b_self.get("calls")), (200, self_b))
        report.check(item, "子账号视角无 by_actor", "by_actor" in child_a_self, False)
        report.check(item, "根自己视角 calls（整树）", (status3, root_self.get("calls")), (200, len(success)))
        report.check(item, "根查 A calls", (status4, root_a.get("calls")), (200, self_a + borrow_a))
        report.check(item, "根查 B calls", (status5, root_b.get("calls")), (200, self_b + borrow_b))
        actors_a = {x["actor"]: x["calls"] for x in root_a.get("by_actor") or []}
        owners_a = {x["key_owner"]: x["calls"] for x in root_a.get("by_key_owner") or []}
        expected_actors_a = {"child_a": self_a, "root1": borrow_a}
        expected_owners_a = {"child_a": self_a + borrow_a}
        report.check(
            item, "根查 A 的 by_actor", actors_a,
            {k: v for k, v in expected_actors_a.items() if v},
        )
        report.check(
            item, "根查 A 的 by_key_owner", owners_a,
            {k: v for k, v in expected_owners_a.items() if v},
        )

        # token 数字与账本逐项一致
        def db_sum(where: str, params: tuple) -> Tuple[int, int]:
            with usage_db(inst) as conn:
                row = conn.execute(
                    "SELECT COALESCE(SUM(tokens_in),0) i, COALESCE(SUM(tokens_out),0) o "
                    "FROM account_usage_events WHERE " + where,
                    params,
                ).fetchone()
                return int(row["i"]), int(row["o"])

        a_self_expect = db_sum("actor=? AND key_owner=?", ("child_a", "child_a"))
        report.check(item, "子账号 A tokens 与账本一致", (child_a_self.get("tokens_in"), child_a_self.get("tokens_out")), a_self_expect)
        a_root_expect = db_sum("key_owner=?", ("child_a",))
        report.check(item, "根侧 A tokens 含借用", (root_a.get("tokens_in"), root_a.get("tokens_out")), a_root_expect)
        api_views.extend(
            [
                {
                    "view": "child_a 自己看自己",
                    "calls": child_a_self.get("calls"),
                    "tokens_in": child_a_self.get("tokens_in"),
                    "tokens_out": child_a_self.get("tokens_out"),
                    "by_actor": "—（子账号不显示调用者）",
                },
                {
                    "view": "root 看 child_a",
                    "calls": root_a.get("calls"),
                    "tokens_in": root_a.get("tokens_in"),
                    "tokens_out": root_a.get("tokens_out"),
                    "by_actor": json.dumps(actors_a, ensure_ascii=False),
                },
                {
                    "view": "root 看 child_b",
                    "calls": root_b.get("calls"),
                    "tokens_in": root_b.get("tokens_in"),
                    "tokens_out": root_b.get("tokens_out"),
                    "by_actor": "—",
                },
                {
                    "view": "root 看整树",
                    "calls": root_self.get("calls"),
                    "tokens_in": root_self.get("tokens_in"),
                    "tokens_out": root_self.get("tokens_out"),
                    "by_actor": json.dumps(
                        {x["actor"]: x["calls"] for x in root_self.get("by_actor") or []},
                        ensure_ascii=False,
                    ),
                },
            ]
        )

        # ---- 逐笔明细端点 + 成本展示 -------------------------------------
        item = "逐笔明细与成本"
        status, detail = request(
            "GET", inst.base + "/v1/accounts/root1/usage/events?limit=200", token=tokens["root1"],
        )
        report.check(item, "根查整树逐笔明细", status, 200, str(detail)[:120])
        report.check(item, "根明细条数=账本行数", detail.get("total"), len(rows))
        detail_events = detail.get("events") or []
        report.check(
            item, "每条明细都带 actor/key_owner/correlation_id",
            (
                all(e.get("actor") and e.get("key_owner") for e in detail_events),
                all(e.get("correlation_id") for e in detail_events),
            ),
            (True, True),
        )
        report.check(
            item, "逐笔成本与总成本同口径（qwen 未定价 ⇒ 未计入，不是 0 元）",
            (
                detail.get("cost_complete"),
                sorted(detail.get("unknown_models") or []),
                "未计入成本" in str(detail.get("cost_message") or ""),
                {e.get("cost") for e in detail_events},
            ),
            (False, [OLLAMA_MODEL], True, {None}),
        )
        report.check(
            item, "未定价模型明细 pricing_status=unknown",
            sorted({e.get("pricing_status") for e in detail_events}), ["unknown"],
        )

        status, by_actor = request(
            "GET", inst.base + "/v1/accounts/root1/usage/events?actor=root1", token=tokens["root1"],
        )
        report.check(item, "根按 actor 过滤", (status, by_actor.get("total")), (200, 4))

        status, member = request(
            "GET", inst.base + "/v1/accounts/child_a/usage/events?limit=200", token=tokens["child_a"],
        )
        report.check(item, "成员明细只看自己", (status, member.get("total")), (200, 2))
        report.check(
            item, "成员明细无他人 actor",
            sorted({e.get("actor") for e in member.get("events") or []}), ["child_a"],
        )
        status, cross = request(
            "GET", inst.base + "/v1/accounts/child_a/usage/events?actor=root1", token=tokens["child_a"],
        )
        # 成员传别人的 actor 时，过滤条件被"actor=本人"覆盖：既不会泄漏 root1
        # 的行，也不会伪装成 0 条（仍然是自己的 2 条）。
        report.check(
            item, "成员传别人的 actor 也不泄漏他人行",
            (status, cross.get("total"), sorted({e.get("actor") for e in cross.get("events") or []})),
            (200, 2, ["child_a"]),
        )

        status, by_corr = request(
            "GET",
            inst.base
            + f"/v1/accounts/root1/usage/events?correlation_id={success[0]['correlation_id']}",
            token=tokens["root1"],
        )
        report.check(
            item, "按 correlation_id 定位到唯一一笔",
            (
                status,
                by_corr.get("total"),
                (by_corr.get("events") or [{}])[0].get("correlation_id"),
            ),
            (200, 1, success[0]["correlation_id"]),
        )

        status, usage_page = request(
            "GET", inst.base + "/v1/accounts/root1/usage", token=tokens["root1"],
        )
        report.check(
            item, "用量页带成本字段（未定价如实提示）",
            (
                status,
                usage_page.get("cost_complete"),
                OLLAMA_MODEL in (usage_page.get("unknown_models") or []),
                "不参与预算拦截" in str(usage_page.get("budget_note") or ""),
            ),
            (200, False, True, True),
        )

        # ---- 账本 ↔ 审计 -------------------------------------------------
        item = "账本与审计一致"
        audit_ok = 0
        for item_success in success:
            events = audit_for(inst, tokens[item_success["actor"]], item_success["correlation_id"])
            chat_events = [e for e in events if (e.get("details") or {}).get("action") == "chat"]
            row = next(
                (r for r in rows if r["correlation_id"] == item_success["correlation_id"]), None
            )
            report.check(
                item,
                f"{item_success['label']} 账本+审计同 correlation_id",
                (bool(row), len(chat_events), chat_events[0].get("outcome") if chat_events else None),
                (True, 1, "allow"),
                f"corr={item_success['correlation_id']}",
            )
            if row and len(chat_events) == 1:
                audit_ok += 1
        report.check(item, "成功轮全部可一一对应", audit_ok, len(success))

        # 根借用轮的审计只在根名下可见；子账号看不到根的审计事件。
        borrow = next(item for item in success if item["label"] == "root_borrow_a")
        root_events = audit_for(inst, tokens["root1"], borrow["correlation_id"])
        child_events = audit_for(inst, tokens["child_a"], borrow["correlation_id"])
        report.check(
            item, "根借用：根审计可见 1 条",
            [e for e in root_events if (e.get("details") or {}).get("action") == "chat"].__len__(),
            1,
        )
        report.check(item, "根借用：子账号审计不可见", len(child_events), 0)

        # 失败轮：审计有 error 事件、账本 0 行。
        timeout_fail = failures[0]
        corr = str(timeout_fail["body"].get("correlation_id") or "")
        events = audit_for(inst, tokens["root1"], corr)
        report.check(
            item, "超时轮审计记 error",
            (len(events), events[0].get("outcome") if events else None, (events[0].get("details") or {}).get("action") if events else None),
            (1, "error", "chat"),
            f"corr={corr}",
        )
        report.check(
            item, "超时轮账本 0 行",
            len([r for r in rows if r["correlation_id"] == corr]), 0,
        )

        # 预算拒绝轮：审计 ACCESS_DENIED + 账本 0 行。
        budget_fail = failures[-1]
        budget_corr = str(budget_fail["body"].get("correlation_id") or "")
        denied_events = [
            e for e in audit_for(inst, tokens["root1"], budget_corr)
            if e.get("event_type") == "access_denied"
        ]
        report.check(
            item, "预算拒绝在审计里可见（按 correlation_id）",
            (len(denied_events), (denied_events[0].get("details") or {}).get("action") if denied_events else None),
            (1, "chat"),
            f"corr={budget_corr}",
        )
        if denied_events:
            report.check(
                item, "预算拒绝审计 outcome=deny",
                (denied_events[0].get("outcome"), (denied_events[0].get("details") or {}).get("action")),
                ("deny", "chat"),
            )
        report.check(item, "预算拒绝账本 0 行", budget_fail["rows_delta"], 0)

        # 审计链完整性
        status, body = request("GET", inst.base + "/v1/audit/verify", token=tokens["root1"])
        report.check(item, "审计链完整", (status, body.get("ok")), (200, True), str(body)[:120])
    except Exception:
        print("\n[server log tail]\n" + inst.tail())
        raise
    finally:
        try:
            inst.stop()
        except Exception:
            pass
        stub.stop()
        try:
            os.unlink(pricing)
        except FileNotFoundError:
            pass

    # ---- 输出 -----------------------------------------------------------
    print("\n## 账本对账表（逐（actor, key_owner））\n")
    print("| actor | key_owner | 预期 calls | 实际 calls | tokens_in | tokens_out | estimated | 结果 |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for row in recon:
        print(
            f"| {row['actor']} | {row['key_owner']} | {row['expected_calls']} | {row['actual_calls']} | "
            f"{row['tokens_in']} | {row['tokens_out']} | {','.join(str(x) for x in row['estimated']) or '—'} | "
            f"{'PASS' if row['ok'] else 'FAIL'} |"
        )

    print("\n## 用量页视角对账\n")
    print("| 视角 | calls | tokens_in | tokens_out | by_actor |")
    print("| --- | --- | --- | --- | --- |")
    for row in api_views:
        print(f"| {row['view']} | {row['calls']} | {row['tokens_in']} | {row['tokens_out']} | {row['by_actor']} |")

    print("\n## 检查明细\n")
    for row in report.rows:
        print(
            f"- [{'PASS' if row['ok'] else 'FAIL'}] [{row['item']}] {row['check']}: "
            f"got={row['got']!r} expected={row['expected']!r} {row['evidence']}"
        )

    failed = report.failed()
    print("\n## 问题清单\n")
    if not failed:
        print("- 无失败项。")
    else:
        for row in failed:
            print(f"- [FAIL] [{row['item']}] {row['check']}: got={row['got']!r} expected={row['expected']!r} {row['evidence']}")
    print(f"\n结果：{len(report.rows) - len(failed)}/{len(report.rows)} 通过")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
