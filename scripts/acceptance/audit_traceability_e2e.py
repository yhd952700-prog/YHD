"""审计留痕与"谁做了什么"可追溯 —— 真实 HTTP 验收。

五件事，逐条对着真服务做，不看代码"理论上可以"：

1. 三个账号各做：登录 / 改设置（模型配置）/ 加员工 / 派活 / 删文档 /
   改成员权限 / 导出备份，检查审计是否都有记录、字段能否定位（谁、何时、
   对什么、结果）。
2. 权限边界：子账号只能看到自己子树内的审计，越级查询得到中性 404。
3. 篡改实验：停机后直接 UPDATE audit_store.db，再启动跑全量校验。
4. 200 次操作后的条数 / 库大小，以及裁剪与空间回收是否真的发生。
5. 面向普通人的标题是否说人话（无 snake_case / 点号命名空间 / 原始 JSON）。

运行：``.venv\\Scripts\\python.exe scripts/acceptance/audit_traceability_e2e.py``
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
import time
from collections import Counter
from typing import Any, Dict, List

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "scripts", "acceptance"))

from account_tree_e2e import PASSWORDS, Instance, Report, request  # noqa: E402

PASSPHRASE = "bundle-passphrase-123"
RAW_MACHINE_RE = re.compile(r"[a-z]+_[a-z]+|[a-z]+\.[a-z]+")
OPS = (
    ("登录", "login"),
    ("改设置", "ai.model_config.create"),
    ("改设置", "ai.model_config.update"),
    ("加员工", "employee.hired"),
    ("派活", "assign_task"),
    ("删文档", "knowledge.document.deleted"),
    ("导出备份", "account.backup.export"),
    ("改设置·自助注册开关", "account.self_registration.toggle"),
)
PERMISSION_ACTIONS = (
    "account.permission.grant",
    "account.permission.granted",
    "account.permission.revoke",
    "account.permission.revoked",
)


def bootstrap(inst: Instance) -> Dict[str, str]:
    """三个真实账号：root1 → member1 → grand1（都具备 manager 权限）。"""
    status, _ = request(
        "POST", inst.base + "/v1/accounts/register",
        {"principal": "root1", "password": PASSWORDS[0], "display_name": "主账号"},
    )
    if status != 200:
        raise RuntimeError(f"register root failed: {status} {_}")
    tokens = {"root1": _login(inst, "root1", PASSWORDS[0])}

    status, body = request(
        "POST", inst.base + "/v1/accounts",
        {"principal": "member1", "role": "manager", "allow_key_sharing": True},
        token=tokens["root1"],
    )
    if status != 200:
        raise RuntimeError(f"create member1 failed: {status} {body}")
    status, activated = request(
        "POST", inst.base + "/v1/accounts/activate",
        {"principal": "member1", "code": body.get("activation_code") or "",
         "password": PASSWORDS[1]},
    )
    if status != 200:
        raise RuntimeError(f"activate member1 failed: {status} {activated}")
    tokens["member1"] = str(activated.get("token") or "") or _login(
        inst, "member1", PASSWORDS[1])

    status, body = request(
        "POST", inst.base + "/v1/accounts",
        {"principal": "grand1", "role": "manager"},
        token=tokens["member1"],
    )
    if status != 200:
        raise RuntimeError(f"create grand1 failed: {status} {body}")
    status, activated = request(
        "POST", inst.base + "/v1/accounts/activate",
        {"principal": "grand1", "code": body.get("activation_code") or "",
         "password": PASSWORDS[2]},
    )
    if status != 200:
        raise RuntimeError(f"activate grand1 failed: {status} {activated}")
    tokens["grand1"] = str(activated.get("token") or "") or _login(
        inst, "grand1", PASSWORDS[2])
    return tokens


def _login(inst: Instance, principal: str, password: str) -> str:
    status, body = request(
        "POST", inst.base + "/v1/auth/login",
        {"principal": principal, "secret": password},
    )
    if status != 200:
        raise RuntimeError(f"login {principal} failed: {status} {body}")
    return str(body.get("access_token") or "")


def do_operations(report: Report, inst: Instance, tokens: Dict[str, str]) -> Dict[str, Any]:
    item = "覆盖"
    employees: Dict[str, str] = {}
    for principal, token in tokens.items():
        status, body = request(
            "POST", inst.base + "/v1/models",
            {"name": f"审计模型-{principal}", "provider": "mock", "model": "mock-1"},
            token=token,
        )
        model_id = str(body.get("id") or "")
        report.check(item, f"{principal} 改设置·创建模型", status, 200)
        status, _ = request(
            "PUT", f"{inst.base}/v1/models/{model_id}",
            {"model": "mock-2"}, token=token,
        )
        report.check(item, f"{principal} 改设置·更新模型", status, 200)

        employee = f"员工-{principal}"
        status, body = request(
            "POST", inst.base + "/v1/employees",
            {"name": employee, "agent_count": 1, "agent_types": ["executor"],
             "role_description": "审计验收专用"},
            token=token,
        )
        employees[principal] = employee
        report.check(item, f"{principal} 加员工", (status, body.get("name")), (201, employee))

        status, body = request(
            "POST", inst.base + "/v1/goals/assign",
            {"employee_name": employee, "instruction": "整理一份审计验收清单",
             "background": True},
            token=token,
        )
        report.check(item, f"{principal} 派活", status, 201, f"goal={body.get('goal_id')}")

        doc_id = f"doc-{principal}"
        status, body = request(
            "POST", inst.base + "/v1/knowledge/documents",
            {"text": f"{principal} 的审计验收文档，内容很短。", "doc_id": doc_id,
             "metadata": {"title": f"审计文档 {principal}"}},
            token=token,
        )
        report.check(item, f"{principal} 上传文档", status, 200)
        # 文档 id 会按账号命名空间重写，删除必须用服务端返回的真实 id。
        stored_doc_id = str(body.get("document_id") or doc_id)
        status, body = request(
            "DELETE", f"{inst.base}/v1/knowledge/documents/{stored_doc_id}", token=token,
        )
        report.check(
            item, f"{principal} 删文档",
            (status, body.get("deleted")), (200, True), f"chunks={body.get('chunks_removed')}",
        )

        status, body = request(
            "POST", inst.base + "/v1/accounts/backup/export",
            {"scope": "self", "passphrase": PASSPHRASE}, token=token,
        )
        report.check(item, f"{principal} 导出备份", status, 200)

    # 改成员权限：根改直属成员、成员改直属下级都成功；下级越级改上级被拒。
    status, body = request(
        "PATCH", inst.base + "/v1/accounts/member1/permissions",
        {"revoke": ["model:use"], "grant": []}, token=tokens["root1"],
    )
    report.check(item, "root1 改 member1 权限", status, 200)
    request(
        "PATCH", inst.base + "/v1/accounts/member1/permissions",
        {"revoke": [], "grant": ["model:use"]}, token=tokens["root1"],
    )
    # 权限变更会让目标账号的旧令牌立即失效（CREDENTIAL_STALE），这是
    # 有意的安全语义；验收必须像真实用户一样重新登录，而不是复用旧令牌。
    tokens["member1"] = _login(inst, "member1", PASSWORDS[1])

    status, body = request(
        "PATCH", inst.base + "/v1/accounts/grand1/permissions",
        {"revoke": ["model:use"], "grant": []}, token=tokens["member1"],
    )
    report.check(item, "member1 改 grand1 权限", status, 200)
    request(
        "PATCH", inst.base + "/v1/accounts/grand1/permissions",
        {"revoke": [], "grant": ["model:use"]}, token=tokens["member1"],
    )
    tokens["grand1"] = _login(inst, "grand1", PASSWORDS[2])
    status, body = request(
        "PATCH", inst.base + "/v1/accounts/member1/permissions",
        {"revoke": [], "grant": ["model:use"]}, token=tokens["grand1"],
    )
    report.check(item, "grand1 越级改上级权限被拒", status, 404)

    # 设置页最典型的开关：自助注册。关 / 开各留一条记录，最后恢复开启。
    for allow in (False, True, False, True):
        status, body = request(
            "PATCH", inst.base + "/v1/accounts/self-registration",
            {"allow": allow}, token=tokens["root1"],
        )
        report.check(item, f"root1 改设置·自助注册={allow}", status, 200,
                     f"allow={body.get('allow')}")
    return {"employees": employees}


def fetch_events(inst: Instance, token: str, query: str = "") -> Dict[str, Any]:
    status, body = request(
        "GET", f"{inst.base}/v1/audit/events?limit=1000&reverse=true{query}",
        token=token,
    )
    if status != 200:
        raise RuntimeError(f"audit events failed: {status} {body}")
    return body


def check_coverage(report: Report, events: List[Dict[str, Any]]) -> None:
    item = "覆盖表"
    by_action: Dict[str, List[Dict[str, Any]]] = {}
    for event in events:
        by_action.setdefault(str(event.get("action") or ""), []).append(event)

    for label, action in OPS:
        rows = by_action.get(action, [])
        report.check(item, f"{label}（{action}）有记录", len(rows) >= 3, True,
                     f"count={len(rows)}")

    permission_rows = [
        event for event in events
        if str(event.get("action") or "") in PERMISSION_ACTIONS
    ]
    allowed = [
        e for e in permission_rows
        if e.get("outcome") in ("allowed", "allow", "granted", "revoked")
    ]
    denied = [
        e for e in events
        if e.get("principal_id") == "grand1"
        and e.get("outcome") in ("denied", "deny")
    ]
    report.check(item, "改成员权限有允许记录", len(allowed) >= 2, True,
                 f"allowed={len(allowed)} outcomes={[e.get('outcome') for e in permission_rows]}")
    report.check(item, "越级改权限有拒绝记录", len(denied) >= 1, True,
                 f"denied={len(denied)}")

    required_target = {
        "ai.model_config.create", "ai.model_config.update", "employee.hired",
        "assign_task", "knowledge.document.deleted", "account.backup.export",
        "account.self_registration.toggle",
    }
    missing: List[str] = []
    for action in required_target:
        for event in by_action.get(action, []):
            if not str(event.get("target") or "").strip():
                missing.append(f"{action}@seq{event.get('seq')}")
    report.check(item, "对什么（target）字段齐全", missing, [])

    missing_fields: List[str] = []
    for event in events:
        if str(event.get("action") or "") not in {a for _, a in OPS} | set(PERMISSION_ACTIONS):
            continue
        for field in ("principal_id", "timestamp", "event_type", "outcome"):
            if not event.get(field):
                missing_fields.append(f"seq={event.get('seq')} missing {field}")
    report.check(item, "谁/何时/结果字段齐全", missing_fields, [])

    print("[DEBUG] event_type:", dict(Counter(str(e.get("event_type")) for e in events)))
    print("[DEBUG] action:", dict(Counter(str(e.get("action")) for e in events)))
    print("[DEBUG] outcome:", dict(Counter(str(e.get("outcome")) for e in events)))

    # 三个账号都留下过自己的动作（不是只有根在记）。
    actors = Counter(str(e.get("principal_id") or "") for e in events)
    report.check(
        item, "三个账号都有记录",
        all(actors.get(p, 0) >= 7 for p in ("root1", "member1", "grand1")),
        True, dict(actors),
    )


def check_boundary(report: Report, inst: Instance, tokens: Dict[str, str]) -> None:
    item = "权限边界"
    member_events = fetch_events(inst, tokens["member1"])["events"]
    member_actors = {str(e.get("principal_id") or "") for e in member_events}
    report.check(item, "member1 看不到 root1 的记录", "root1" not in member_actors, True,
                 str(sorted(member_actors)))
    report.check(
        item, "member1 只看到自己子树",
        member_actors <= {"member1", "grand1"}, True, str(sorted(member_actors)),
    )

    grand_events = fetch_events(inst, tokens["grand1"])["events"]
    grand_actors = {str(e.get("principal_id") or "") for e in grand_events}
    report.check(item, "grand1 只看得到自己", grand_actors, {"grand1"})

    status, body = request(
        "GET", inst.base + "/v1/audit/events?principal=root1", token=tokens["member1"],
    )
    report.check(item, "member1 查 root1 得到中性 404", status, 404,
                 str(body.get("code") or body.get("message") or ""))
    status, body = request(
        "GET", inst.base + "/v1/audit/events?principal=member1", token=tokens["grand1"],
    )
    report.check(item, "grand1 查上级得到中性 404", status, 404)


def check_readability(report: Report, events: List[Dict[str, Any]]) -> None:
    item = "可读性"
    bad: List[str] = []
    # 界面上展示的是**全部**记录，不只是本轮关心的七种操作；任何一条露出
    # snake_case / 点号命名空间 / 原始 JSON 都会被普通人看到。
    for event in events:
        action = str(event.get("action") or "")
        if not action:
            continue
        title = str(event.get("display_title") or "")
        if not title:
            bad.append(f"seq={event.get('seq')} 空标题")
            print(f"[DEBUG] 空标题原始事件: {event}")
        elif RAW_MACHINE_RE.search(title) or "{" in title or "None" in title:
            bad.append(f"seq={event.get('seq')} {title}")
    report.check(
        item, "操作记录标题为中文且无内部术语", bad, [],
        f"共检查 {sum(1 for e in events if e.get('action'))} 条",
    )

    samples = []
    for _, action in OPS[:4]:
        for event in events:
            if event.get("action") == action:
                samples.append(str(event.get("display_title") or ""))
                break
    report.check(item, "标题包含对象与结果", all(samples), True, " / ".join(samples))


def audit_db_bytes(inst: Instance) -> int:
    path = str(inst.env["AUDIT_DB_PATH"])
    total = 0
    for suffix in ("", "-wal", "-shm"):
        try:
            total += os.path.getsize(path + suffix)
        except OSError:
            pass
    return total


def tamper_experiment(
    report: Report, inst: Instance, tokens: Dict[str, str], events: List[Dict[str, Any]]
) -> None:
    item = "篡改实验"
    target = next(
        (e for e in events if e.get("action") == "employee.hired"), None
    )
    if target is None:
        report.check(item, "找到可篡改样本", False, True)
        return
    seq = int(target["seq"])
    name = str(target.get("target") or "")
    db_path = str(inst.env["AUDIT_DB_PATH"])

    before = fetch_events(inst, tokens["root1"])["events"]
    report.check(item, "篡改前全量校验通过", _verify(inst, tokens["root1"], full=True)["ok"], True)

    inst.stop(cleanup=False)
    def _json_inner(value: str) -> str:
        # details 是 json.dumps 落库的，中文被转义成 \uXXXX；直接 REPLACE
        # 原字面量根本改不到，必须用同一套 JSON 转义片段。
        import json as _json

        return _json.dumps(value, ensure_ascii=True)[1:-1]

    changed = ""
    for attempt in range(10):
        try:
            with sqlite3.connect(db_path, timeout=5) as conn:
                conn.execute(
                    "UPDATE audit_events SET details = REPLACE(details, ?, ?) WHERE seq = ?",
                    (_json_inner(name), _json_inner("被篡改"), seq),
                )
                conn.commit()
                changed = conn.execute(
                    "SELECT details FROM audit_events WHERE seq = ?", (seq,)
                ).fetchone()[0]
            break
        except sqlite3.OperationalError:
            if attempt == 9:
                raise
            # Windows 上进程刚被杀掉时 -wal/-shm 句柄可能还没完全释放。
            time.sleep(0.5)
    inst.start()

    after = fetch_events(inst, tokens["root1"])["events"]
    after_row = next((e for e in after if int(e.get("seq") or 0) == seq), None)
    report.check(
        item, "直接改库确实改变了展示内容",
        bool(after_row and after_row.get("target") == "被篡改"), True,
        str(changed)[:80],
    )
    incremental = _verify(inst, tokens["root1"], full=False)
    full = _verify(inst, tokens["root1"], full=True)
    report.check(item, "全量校验发现篡改", full.get("ok"), False,
                 str(full.get("first_failure")))
    print(f"[INFO] 增量校验 ok={incremental.get('ok')} mode="
          f"{incremental.get('details', {}).get('verification_mode')}")


def _verify(inst: Instance, token: str, *, full: bool) -> Dict[str, Any]:
    status, body = request(
        "GET", f"{inst.base}/v1/audit/verify?full={'true' if full else 'false'}",
        token=token,
    )
    if status != 200:
        raise RuntimeError(f"verify failed: {status} {body}")
    return body


def growth_and_retention(report: Report) -> None:
    item = "200 次操作"
    import tempfile

    root = tempfile.mkdtemp(prefix="liuhao-audit-retention-")
    inst = Instance(
        "liuhao-audit-retention-",
        root=root,
        env_extra={
            "LIUHAO_AUDIT_RETENTION_ENABLED": "1",
            "LIUHAO_AUDIT_RETENTION_DAYS": "30",
            "LIUHAO_AUDIT_RETENTION_MAX_ROWS": "30",
            "LIUHAO_AUDIT_RETENTION_MIN_KEEP": "20",
            "LIUHAO_AUDIT_RETENTION_INTERVAL_SEC": "1",
            "LIUHAO_AUDIT_RETENTION_MAX_DELETE": "10000",
        },
    )
    try:
        request(
            "POST", inst.base + "/v1/accounts/register",
            {"principal": "root1", "password": PASSWORDS[0]},
        )
        token = _login(inst, "root1", PASSWORDS[0])
        size_0 = audit_db_bytes(inst)

        for index in range(1, 201):
            status, _ = request(
                "POST", inst.base + "/v1/auth/login",
                {"principal": "root1", "secret": PASSWORDS[0]},
            )
            if status != 200:
                raise RuntimeError(f"login #{index} failed: {status}")
            if index == 100:
                size_100 = audit_db_bytes(inst)
        time.sleep(2.0)
        _login(inst, "root1", PASSWORDS[0])  # 触发一次裁剪
        time.sleep(1.5)
        size_200 = audit_db_bytes(inst)

        body = fetch_events(inst, token)
        rows = body["events"]
        count = len(rows)
        report.check(item, "200 次操作均成功", True, True)
        report.check(item, "裁剪后条数 < 200", count < 200, True, f"count={count}")
        report.check(item, "至少保留最新 20 条", count >= 20, True, f"count={count}")
        report.check(item, "裁剪后全量校验仍通过", _verify(inst, token, full=True)["ok"], True)
        report.check(item, "库大小受控（<5MB）", size_200 < 5 * 1024 * 1024, True,
                     f"{size_0}→{size_100}→{size_200} bytes")
        print(f"[INFO] 审计库大小：初始 {size_0} B / 100 次 {size_100} B / 200 次后 {size_200} B")
        print(f"[INFO] 裁剪后条数={count}（共跑了 200 次登录）")
    except BaseException:
        print("[ERROR] 保留实例日志用于排查：\n" + inst.tail()[-4000:])
        inst.stop(cleanup=False)
        raise
    else:
        inst.stop()


def main() -> int:
    import tempfile

    report = Report()
    root = tempfile.mkdtemp(prefix="liuhao-audit-trace-")
    inst = Instance(
        "liuhao-audit-trace-",
        root=root,
        env_extra={
            "LIUHAO_KNOWLEDGE_DB": os.path.join(root, "knowledge.sqlite3"),
            "LIUHAO_KNOWLEDGE_ALLOW_MOCK": "1",
        },
    )
    try:
        tokens = bootstrap(inst)
        do_operations(report, inst, tokens)
        events = fetch_events(inst, tokens["root1"])["events"]
        check_coverage(report, events)
        check_boundary(report, inst, tokens)
        check_readability(report, events)
        tamper_experiment(report, inst, tokens, events)
    finally:
        inst.stop()

    growth_and_retention(report)

    print("\n=== 覆盖表（真实审计事件计数） ===")
    print("操作 | 是否有记录 | 条数 | 字段是否够定位")
    by_action: Dict[str, List[Dict[str, Any]]] = {}
    for event in events:
        by_action.setdefault(str(event.get("action") or ""), []).append(event)

    def _field_state(rows: List[Dict[str, Any]], need_target: bool) -> str:
        if not rows:
            return "无记录"
        missing_who = sum(1 for e in rows if not e.get("principal_id"))
        missing_when = sum(1 for e in rows if not e.get("timestamp"))
        missing_result = sum(1 for e in rows if not e.get("outcome"))
        missing_target = (
            sum(1 for e in rows if not str(e.get("target") or "").strip())
            if need_target else 0
        )
        parts = []
        if missing_who:
            parts.append(f"缺谁×{missing_who}")
        if missing_when:
            parts.append(f"缺何时×{missing_when}")
        if missing_target:
            parts.append(f"缺对象×{missing_target}")
        if missing_result:
            parts.append(f"缺结果×{missing_result}")
        if parts:
            return "、".join(parts)
        return "齐全（谁/何时/对象/结果）" if need_target else "齐全（谁/何时/结果）"

    for label, action in (
        ("登录", "login"),
        ("改设置·创建模型", "ai.model_config.create"),
        ("改设置·更新模型", "ai.model_config.update"),
        ("加员工", "employee.hired"),
        ("派活", "assign_task"),
        ("删文档", "knowledge.document.deleted"),
        ("导出备份", "account.backup.export"),
        ("改设置·自助注册开关", "account.self_registration.toggle"),
    ):
        rows = by_action.get(action, [])
        print(f"{label} | {'有' if rows else '无'} | {len(rows)} | "
              f"{_field_state(rows, need_target=action != 'login')}")

    permission_rows = [
        event for event in events
        if str(event.get("action") or "") in PERMISSION_ACTIONS
    ]
    denied_rows = [
        event for event in events
        if str(event.get("action") or "") == "account.manage.denied"
    ]
    print(f"改成员权限 | {'有' if permission_rows else '无'} | {len(permission_rows)} | "
          f"{_field_state(permission_rows, need_target=True)}")
    print(f"越级改权限被拒 | {'有' if denied_rows else '无'} | {len(denied_rows)} | "
          f"{_field_state(denied_rows, need_target=True)}")

    failed = report.failed()
    print(f"\n结果：{len(report.rows) - len(failed)}/{len(report.rows)} 通过")
    for row in failed:
        print(f"  FAIL {row['item']} · {row['check']}: got={row['got']!r} "
              f"expected={row['expected']!r} {row['evidence']}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
