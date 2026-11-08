"""账号树三件边界实测（隔离实例 + 真实 HTTP + 可选真实浏览器）。

覆盖：

1. **删中间层账号**：有下级时是级联删除 / 子账号被提升 / 直接拒绝？
   并给出三种分支的中文提示原文（有下级拒绝、根账号拒绝、末级删除）。
2. **同一浏览器换账号不串数据**：根账号建会话/员工/记忆/用量后，子账号登录，
   四类列表必须只属于子账号；本地缓存由前端单测覆盖
   （``apps/console/console/src/lib/auth.test.ts`` 的"同浏览器换账号"）。
3. **极端形态**：预算 10 层 / 单层 200 下级 / 共 500 成员。
   产品深度的可配置上限是 8 层（``LIUHAO_ACCOUNT_MAX_DEPTH`` 截断到 7），
   因此先实测"第 9 层"的真实拒绝，再按上限构造 500 成员并测：
   树接口耗时、列表耗时、成员页首屏渲染与滚动。

运行：``.venv\\Scripts\\python.exe scripts/acceptance/account_tree_edges_e2e.py``
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from account_tree_e2e import (  # noqa: E402
    Instance,
    Report,
    configure_harness,
    error_code,
    request,
)

ROOT_PASSWORD = "rootpass123"
MEMBER_PASSWORD = "memberpass123"
GRAND_PASSWORD = "grandpass123"


def _login(inst: Instance, principal: str, secret: str) -> str:
    status, body = request(
        "POST", inst.base + "/v1/auth/login",
        {"principal": principal, "secret": secret},
    )
    if status != 200:
        raise RuntimeError(f"login {principal} failed: {status} {body}")
    return str(body.get("access_token") or "")


def _create_member(
    inst: Instance, token: str, principal: str, *, role: str = "manager",
    allow_key_sharing: bool = False,
) -> str:
    status, body = request(
        "POST", inst.base + "/v1/accounts",
        {"principal": principal, "role": role, "allow_key_sharing": allow_key_sharing},
        token=token,
    )
    if status != 200:
        raise RuntimeError(f"create {principal} failed: {status} {body}")
    return str(body.get("activation_code") or "")


def _activate(inst: Instance, principal: str, code: str, password: str) -> str:
    status, body = request(
        "POST", inst.base + "/v1/accounts/activate",
        {"principal": principal, "code": code, "password": password},
    )
    if status != 200:
        raise RuntimeError(f"activate {principal} failed: {status} {body}")
    return str(body.get("token") or "")


def _message(body: Any) -> str:
    if not isinstance(body, dict):
        return str(body)
    message = body.get("message")
    if isinstance(message, str) and message:
        return message
    detail = body.get("detail")
    if isinstance(detail, dict):
        nested = detail.get("message")
        if isinstance(nested, str) and nested:
            return nested
    if isinstance(detail, str):
        return detail
    return ""


def _tree_children(node: Any) -> Dict[str, str]:
    """把 /v1/accounts/tree 摊平成 {principal: parent}，用于断言"没被提升"。"""
    found: Dict[str, str] = {}

    def walk(item: Any, parent: Optional[str]) -> None:
        if not isinstance(item, dict):
            return
        principal = str(item.get("principal") or "")
        if principal:
            found[principal] = parent or ""
        for child in item.get("children") or []:
            walk(child, principal)

    walk(node, None)
    return found


# --------------------------------------------------------------------------- #
# 1. 删除中间层账号
# --------------------------------------------------------------------------- #

def deletion_policy(report: Report, inst: Instance) -> None:
    item = "删除中间层"
    status, _ = request(
        "POST", inst.base + "/v1/accounts/register",
        {"principal": "root1", "password": ROOT_PASSWORD, "display_name": "主账号"},
    )
    report.check(item, "注册根账号", status, 200)
    root = _login(inst, "root1", ROOT_PASSWORD)

    member_code = _create_member(inst, root, "member1", role="manager", allow_key_sharing=True)
    _activate(inst, "member1", member_code, MEMBER_PASSWORD)
    member = _login(inst, "member1", MEMBER_PASSWORD)
    grand_code = _create_member(inst, member, "grand1", role="operator")
    _activate(inst, "grand1", grand_code, GRAND_PASSWORD)
    grand = _login(inst, "grand1", GRAND_PASSWORD)
    report.check(item, "三层树就绪", (bool(root), bool(member), bool(grand)), (True, True, True))

    status, body = request("GET", inst.base + "/v1/accounts/tree", token=root)
    before = _tree_children(body.get("tree") or {})
    report.check(
        item, "删除前树形",
        before,
        {"root1": "", "member1": "root1", "grand1": "member1"},
    )

    # A) 有下级 → 默认拒绝（本次实测的核心）
    status, body = request(
        "PATCH", inst.base + "/v1/accounts/member1/status",
        {"status": "deleted", "reason": "边界验收"}, token=root,
    )
    message_has_children = _message(body)
    report.check(
        item, "有下级删除被拒绝（状态/错误码）",
        (status, error_code(body)),
        (409, "ACCOUNT_DELETE_HAS_CHILDREN"),
        f"message={message_has_children}",
    )
    report.check(
        item, "有下级删除中文提示原文",
        message_has_children,
        "该账号仍有关联的其他账号，请先处理关联账号或选择一并停用",
    )

    # 拒绝之后子树必须原样保留：既没有级联删，也没有把 grand1 提升到 root。
    status, body = request("GET", inst.base + "/v1/accounts/tree", token=root)
    after = _tree_children(body.get("tree") or {})
    report.check(item, "拒绝后子树未被级联删除", set(after.keys()), {"root1", "member1", "grand1"})
    report.check(item, "拒绝后下级未被提升（父子关系不变）", after.get("grand1"), "member1", str(after))

    # B) 根账号自身 → 拒绝（另一种"不能删"的提示原文）
    status, body = request(
        "PATCH", inst.base + "/v1/accounts/root1/status",
        {"status": "deleted", "reason": "边界验收"}, token=root,
    )
    root_message = _message(body)
    report.check(
        item, "根账号删除被拒绝（状态/错误码）",
        (status, error_code(body)),
        (409, "ACCOUNT_DELETE_ROOT_FORBIDDEN"),
        f"message={root_message}",
    )
    report.check(item, "根账号删除中文提示原文", root_message, "根账号不可删除")

    # C) 末级账号 → 允许删除（成功的分支）
    status, body = request(
        "PATCH", inst.base + "/v1/accounts/grand1/status",
        {"status": "deleted", "reason": "边界验收"}, token=root,
    )
    report.check(
        item, "末级账号可删除",
        (status, body.get("status"), body.get("affected")),
        (200, "deleted", ["grand1"]),
        f"audit_retained={body.get('audit_retained')}",
    )
    status, _ = request("GET", inst.base + "/v1/accounts", token=grand)
    report.check(item, "被删末级的旧令牌立即失效", status, 403)

    # D) "明确选择级联"在现产品里只有"一并停用"，没有"级联删除"：
    #    重建一个下级后停用 middleware，必须整棵子树一起停用并可恢复。
    grand2_code = _create_member(inst, member, "grand2", role="operator")
    _activate(inst, "grand2", grand2_code, "grand2pass123")
    grand2 = _login(inst, "grand2", "grand2pass123")
    status, body = request(
        "PATCH", inst.base + "/v1/accounts/member1/status",
        {"status": "suspended", "reason": "一并停用"}, token=root,
    )
    report.check(
        item, "显式级联停用整棵子树",
        (status, sorted(body.get("affected") or [])),
        (200, ["grand2", "member1"]),
        f"notice={body.get('notice')}",
    )
    status, _ = request("GET", inst.base + "/v1/accounts", token=grand2)
    report.check(item, "级联停用后下级令牌失效", status, 403)
    status, _ = request("GET", inst.base + "/v1/accounts", token=member)
    report.check(item, "级联停用后中间层令牌失效", status, 403)
    # 恢复同样整棵子树一起恢复（不会出现"上级活了、下级还停着"）。
    status, body = request(
        "PATCH", inst.base + "/v1/accounts/member1/status",
        {"status": "active", "reason": "恢复"}, token=root,
    )
    report.check(
        item, "恢复也级联整棵子树",
        (status, sorted(body.get("affected") or [])),
        (200, ["grand2", "member1"]),
    )

    # 产品决定记录（打印即报告素材，不计 PASS/FAIL）。
    print("\n--- 删除策略实测结论 ---")
    print(json.dumps({
        "有下级": {"行为": "直接拒绝（409 ACCOUNT_DELETE_HAS_CHILDREN）", "提示": message_has_children},
        "根账号": {"行为": "直接拒绝（409 ACCOUNT_DELETE_ROOT_FORBIDDEN）", "提示": root_message},
        "末级": {"行为": "允许删除（200，affected=[自身]）", "提示": "界面统一显示“状态变更已完成”"},
        "级联": {"行为": "不存在“级联删除”；仅支持“一并停用/恢复”整棵子树", "提示": "无独立确认文案，沿用停用确认"},
        "提升": {"行为": "不存在；拒绝删除后 parent 关系原样保留", "提示": "—"},
    }, ensure_ascii=False, indent=2))
    print("--- 删除策略结论结束 ---\n")


# --------------------------------------------------------------------------- #
# 2. 换账号：后端数据不串（前端本地缓存见 vitest）
# --------------------------------------------------------------------------- #

def switch_isolation(report: Report, inst: Instance, root: str, member: str) -> None:
    item = "换账号不串数据"
    status, body = request(
        "POST", inst.base + "/v1/models",
        {"name": "root-switch-model", "provider": "mock", "model": "mock-1",
         "owner_principal": "root1"},
        token=root,
    )
    root_model = str(body.get("id") or "")
    report.check(item, "根建自己的模型", status, 200, f"id={root_model}")

    status, body = request(
        "POST", inst.base + "/v1/chat",
        {"message": "根账号的会话内容", "session_id": "root-session",
         "model_id": root_model, "use_knowledge": False},
        token=root,
    )
    report.check(item, "根发一条消息（产生会话+用量）", status, 200, f"status={body.get('status')}")

    status, body = request(
        "POST", inst.base + "/v1/memory/entries",
        {"text": "root-switch-memory", "source": "acceptance"}, token=root,
    )
    report.check(item, "根写一条记忆", status, 201)
    status, body = request(
        "POST", inst.base + "/v1/employees",
        {"name": "root-switch-writer", "agent_count": 2,
         "agent_types": ["planner", "executor"],
         "role_description": "根账号的员工"},
        token=root,
    )
    report.check(item, "根建一个员工", (status, body.get("name")), (201, "root-switch-writer"))

    # 子账号：不登出、直接换令牌（同一浏览器换号的最小后端等价路径）。
    child_sessions = request("GET", inst.base + "/v1/chat/sessions", token=member)[1]
    child_session_names = [str(x) for x in child_sessions.get("sessions") or []]
    report.check(item, "切到子账号：会话列表无上一个账号", "root-session" in child_session_names, False, str(child_session_names))

    child_memory = request("GET", inst.base + "/v1/memory/entries?scope=self", token=member)[1]
    child_texts = [e.get("text") for e in child_memory.get("entries") or []]
    report.check(item, "切到子账号：记忆无上一个账号", "root-switch-memory" in child_texts, False, str(child_texts))

    child_employees = request("GET", inst.base + "/v1/employees", token=member)[1]
    child_names = sorted(e.get("name") for e in child_employees.get("employees") or [])
    report.check(item, "切到子账号：员工无上一个账号", "root-switch-writer" in child_names, False, str(child_names))

    child_models = request("GET", inst.base + "/v1/models", token=member)[1]
    child_model_ids = [m.get("id") for m in child_models.get("models") or []]
    report.check(item, "切到子账号：模型配置无上一个账号", root_model in child_model_ids, False, str(child_model_ids))

    child_usage = request("GET", inst.base + "/v1/accounts/member1/usage", token=member)[1]
    report.check(item, "切到子账号：用量只算子账号自己", child_usage.get("calls"), 0)

    # 子账号自己建一条会话/记忆/员工后，列表里只有自己的东西。
    member_model = request(
        "POST", inst.base + "/v1/models",
        {"name": "member-switch-model", "provider": "mock", "model": "mock-1",
         "owner_principal": "member1"},
        token=member,
    )[1]
    member_model_id = str(member_model.get("id") or "")
    request(
        "POST", inst.base + "/v1/chat",
        {"message": "子账号的会话内容", "session_id": "member-session",
         "model_id": member_model_id, "use_knowledge": False},
        token=member,
    )
    request(
        "POST", inst.base + "/v1/memory/entries",
        {"text": "member-switch-memory", "source": "acceptance"}, token=member,
    )
    request(
        "POST", inst.base + "/v1/employees",
        {"name": "member-switch-writer", "agent_count": 2,
         "agent_types": ["planner", "executor"], "role_description": "子账号的员工"},
        token=member,
    )

    sessions = request("GET", inst.base + "/v1/chat/sessions", token=member)[1]
    names = [str(x) for x in sessions.get("sessions") or []]
    report.check(item, "子账号列表只含自己的数据", names, ["member-session"])
    texts = [e.get("text") for e in request("GET", inst.base + "/v1/memory/entries?scope=self", token=member)[1].get("entries") or []]
    report.check(item, "子账号记忆只含自己", texts, ["member-switch-memory"])
    employees = sorted(e.get("name") for e in request("GET", inst.base + "/v1/employees", token=member)[1].get("employees") or [])
    report.check(item, "子账号员工只含自己", employees, ["member-switch-writer"])
    usage = request("GET", inst.base + "/v1/accounts/member1/usage", token=member)[1]
    report.check(item, "子账号用量只算自己", usage.get("calls"), 1)

    # 再切回根：根仍然只看得到自己的（不会把子账号数据带上来）。
    back_sessions = [str(x) for x in request("GET", inst.base + "/v1/chat/sessions", token=root)[1].get("sessions") or []]
    report.check(item, "切回根：列表仍只属根", "member-session" in back_sessions, False, str(back_sessions))
    back_employees = sorted(e.get("name") for e in request("GET", inst.base + "/v1/employees", token=root)[1].get("employees") or [])
    report.check(item, "切回根：员工仍只属根", "member-switch-writer" in back_employees, False, str(back_employees))


# --------------------------------------------------------------------------- #
# 3. 极端形态：10 层 / 单层 200 / 共 500
# --------------------------------------------------------------------------- #

def _timed(report: Report, item: str, name: str, inst: Instance, path: str, token: str,
           runs: int = 3, floor_ms: float = 0.0) -> float:
    """同一路径跑 ``runs`` 次，取 worst（用户最不能接受的那个数）。"""
    samples: List[float] = []
    payload: Any = None
    status = 0
    for _ in range(runs):
        start = time.perf_counter()
        status, payload = request("GET", inst.base + path, token=token)
        samples.append((time.perf_counter() - start) * 1000)
    worst = max(samples)
    median = statistics.median(samples)
    raw = len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) if payload else 0
    report.check(
        item, name,
        (status, worst >= floor_ms),
        (200, True),
        f"median={median:.0f}ms worst={worst:.0f}ms bytes={raw}",
    )
    print(f"[TIME] {name}: median={median:.0f}ms worst={worst:.0f}ms bytes={raw}")
    return worst


def extreme_tree(report: Report, *, run_browser: bool = False) -> None:
    item = "极端形态"
    scale = Instance(
        "liuhao-account-edges-",
        env_extra={"LIUHAO_ACCOUNT_MAX_DEPTH": "7"},
    )
    try:
        status, _ = request(
            "POST", scale.base + "/v1/accounts/register",
            {"principal": "scale-root", "password": ROOT_PASSWORD, "display_name": "规模根"},
        )
        report.check(item, "注册规模实例根账号", status, 200)
        root = _login(scale, "scale-root", ROOT_PASSWORD)

        # 3.1 先试"10 层"：产品把深度截断在 7（8 层），第 9 层必须被拒绝。
        chain = ["scale-root"]
        token = root
        deepest_error = ""
        levels = 0
        for level in range(1, 11):
            name = f"depth{level}"
            status, body = request(
                "POST", scale.base + "/v1/accounts",
                {"principal": name, "role": "manager"}, token=token,
            )
            if status != 200:
                deepest_error = f"{status} {error_code(body)} {_message(body)}"
                break
            levels = level
            code = str(body.get("activation_code") or "")
            _activate(scale, name, code, f"depth{level}pass")
            token = _login(scale, name, f"depth{level}pass")
            chain.append(name)
        report.check(
            item, "10 层不可达：实际在第 9 层被拒（上限 8 层）",
            (levels, deepest_error.split(" ")[0] if deepest_error else "", deepest_error.split(" ")[1] if deepest_error else ""),
            (7, "409", "ACCOUNT_DEPTH_EXCEEDED"),
            f"created={chain} error={deepest_error}",
        )
        print(f"[DEPTH] 实际可建层数={levels + 1}（根 0 层 + 成员 {levels} 层）; 第 9 层提示={deepest_error}")

        # 3.2 构造 500 成员：单层 200 下级（l1），其余分摊在 l2/l3。
        l1 = chain[1]
        l1_token = _login(scale, l1, "depth1pass")
        l2 = chain[2]
        l2_token = _login(scale, l2, "depth2pass")
        l3 = chain[3]
        l3_token = _login(scale, l3, "depth3pass")

        created = len(chain)  # 1 + 7
        for index in range(199):  # l1 已有 l2 一个下级 → 直接下级共 200
            status, _ = request(
                "POST", scale.base + "/v1/accounts",
                {"principal": f"bulk-a{index:03d}", "role": "viewer"}, token=l1_token,
            )
            if status != 200:
                raise RuntimeError(f"bulk-a{index} failed: {status}")
            created += 1
        for index in range(200):
            status, _ = request(
                "POST", scale.base + "/v1/accounts",
                {"principal": f"bulk-b{index:03d}", "role": "viewer"}, token=l2_token,
            )
            if status != 200:
                raise RuntimeError(f"bulk-b{index} failed: {status}")
            created += 1
        for index in range(93):
            status, _ = request(
                "POST", scale.base + "/v1/accounts",
                {"principal": f"bulk-c{index:03d}", "role": "viewer"}, token=l3_token,
            )
            if status != 200:
                raise RuntimeError(f"bulk-c{index} failed: {status}")
            created += 1
        report.check(item, "构造 500 成员", created, 500)
        print(f"[SCALE] 成员总数={created}；{l1} 直接下级=200（含 {l2}）")

        # 3.3 成员管理页真正依赖的两个接口：/tree（根，整树）与 /accounts（直接下级）。
        _timed(report, item, "根读整树 /v1/accounts/tree（500 节点）", scale, "/v1/accounts/tree", root)
        _timed(report, item, "根读直接成员 /v1/accounts（200 条）", scale, "/v1/accounts", root)
        _timed(report, item, "中间层读直接下级 /v1/accounts（200 条）", scale, "/v1/accounts", l1_token)

        status, body = request("GET", scale.base + "/v1/accounts/tree", token=root)
        count = len(_tree_children(body.get("tree") or {}))
        report.check(item, "树接口返回全部 500 节点", count, 500, f"status={status}")

        if run_browser:
            _browser_members(report, scale)
    finally:
        scale.stop()


def _launch(playwright):
    """优先系统 Edge，其次 Playwright 自带 chromium；都没有就跳过。"""
    errors: List[str] = []
    for kwargs in ({"channel": "msedge"}, {}):
        try:
            return playwright.chromium.launch(headless=True, **kwargs)
        except Exception as exc:  # noqa: BLE001 - 环境探测
            errors.append(f"{kwargs}: {exc}")
    raise RuntimeError("浏览器不可用：" + " | ".join(errors))


def _browser_members(report: Report, inst: Instance) -> None:
    item = "极端形态·真实浏览器"
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        report.check(item, "playwright 可用", False, True, "未安装 playwright")
        return
    with sync_playwright() as pw:
        browser = None
        try:
            browser = _launch(pw)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(inst.base + "/?p=members", wait_until="domcontentloaded", timeout=60000)
            page.fill("#os-principal", "scale-root")
            page.fill("#os-secret", ROOT_PASSWORD)
            start = time.perf_counter()
            page.click("button[type=submit]")
            page.wait_for_selector("table.os-table tbody tr", timeout=120000)
            first_row_ms = (time.perf_counter() - start) * 1000
            # 等 React 把整棵树渲染完（行数稳定）。
            deadline = time.time() + 120
            last_count = 0
            while time.time() < deadline:
                count = page.locator("table.os-table tbody tr").count()
                if count >= 500:
                    break
                if count == last_count and count > 0:
                    page.wait_for_timeout(500)
                    if page.locator("table.os-table tbody tr").count() == count:
                        break
                last_count = count
                page.wait_for_timeout(200)
            rows = page.locator("table.os-table tbody tr").count()
            render_ms = (time.perf_counter() - start) * 1000

            # 滚动：表格容器必须能滚到最后一页，且不触发主线程卡死（2 秒内完成）。
            scrolled = page.evaluate(
                """() => {
                    const wrap = document.querySelector('.os-table-wrap');
                    if (!wrap) return {found: false};
                    const before = wrap.scrollTop;
                    wrap.scrollTop = wrap.scrollHeight;
                    return {found: true, before, after: wrap.scrollTop,
                            height: wrap.scrollHeight, client: wrap.clientHeight};
                }"""
            )
            page.wait_for_timeout(300)
            bottom = page.evaluate(
                """() => {
                    const rows = [...document.querySelectorAll('table.os-table tbody tr')];
                    const last = rows[rows.length - 1];
                    if (!last) return {visible: false};
                    const r = last.getBoundingClientRect();
                    return {visible: r.bottom <= window.innerHeight + 1, text: last.textContent};
                }"""
            )

            report.check(item, "成员页首屏出现数据", rows > 0, True, f"rows={rows} first_row={first_row_ms:.0f}ms")
            report.check(item, "500 行在 120 秒内渲染完（未卡死）", rows >= 500, True, f"rows={rows} render={render_ms:.0f}ms")
            report.check(
                item, "表格容器可滚动到底（滚动后最后一行可见）",
                (scrolled.get("found"), scrolled.get("after", 0) > scrolled.get("before", 0), bottom.get("visible")),
                (True, True, True),
                f"scroll={scrolled} last={str(bottom.get('text'))[:40]}",
            )
            print(f"[BROWSER] 首行可见={first_row_ms:.0f}ms 全部渲染={render_ms:.0f}ms rows={rows} scroll={scrolled}")
        finally:
            if browser is not None:
                browser.close()


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #

def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="账号树三件边界实测")
    parser.add_argument("--port-base", type=int, default=None)
    parser.add_argument("--data-root", default=None)
    parser.add_argument("--json-report", default=None)
    parser.add_argument("--no-browser", action="store_true", help="跳过真实浏览器渲染测试")
    return parser.parse_args(argv)


def run(*, run_browser: bool = False) -> Report:
    report = Report()
    main_inst = Instance("liuhao-account-edges-main-")
    try:
        deletion_policy(report, main_inst)
        # 删测把 member1/grand1 状态改来改去；换号隔离用刚恢复的 member1。
        root = _login(main_inst, "root1", ROOT_PASSWORD)
        member = _login(main_inst, "member1", MEMBER_PASSWORD)
        switch_isolation(report, main_inst, root, member)
    finally:
        main_inst.stop()
    extreme_tree(report, run_browser=run_browser)
    return report


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    configure_harness(port_base=args.port_base, data_root=args.data_root)
    report = run(run_browser=not args.no_browser)
    failed = report.failed()
    print("\n=== 汇总 ===")
    print(json.dumps(
        {"total": len(report.rows), "failed": len(failed),
         "failures": [{k: row[k] for k in ("item", "check", "got", "expected", "evidence")} for row in failed]},
        ensure_ascii=False, indent=2,
    ))
    if args.json_report:
        path = os.path.abspath(args.json_report)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"total": len(report.rows), "failed": len(failed), "rows": report.rows},
                      handle, ensure_ascii=False, indent=2, default=str)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
