"""契约漂移门禁 —— `contracts.ts` 声明的**必填**字段，后端必须真的发出来。

为什么这个-test 存在
--------------------
``declared_test_cases`` 那次事故的形状是这样的：``contracts.ts`` 声明了
``totals.declared_test_cases``、某个 ``.tsx`` 渲染它，而 ``build_roster_payload()``
**从未产出过那个键**。于是界面把 ``formatCompact(undefined)`` 画给用户看。
``npx tsc -b`` 全程是绿的 —— 它只校验前端自己的类型声明，无从知道服务端不说话。

所以这里的断言是**两个互不相关的真相交叉验证**：

    contracts.ts 里的 TS 接口        ← 从磁盘读原文，解析成点号路径
    /v1/... 的真实 HTTP 响应          ← TestClient 打真的 app

两边谁也不是从谁推出来的。任一侧单独漂移，这里就变红。

解析器为什么必须递归（血泪版本）
--------------------------------
    第一版解析器用正则把**嵌套对象字面量整块删掉**再找顶层字段 —— 于是它对
    ``totals`` 内部的 `declared_test_cases` **完全失明**。

那正是原缺陷藏身的层级。变异测试（临时把这个字段塞回 contractsts.ts）实测：
门禁全绿、16 passed，什么都抓不到。一个对本类缺陷恰恰在事发层级失明的门禁，
比没有门禁更危险 —— 它会让人以为这里有人看着。

现在的做法是递归下降，把 ``totals.kernels`` 这样的**点号路径**全部抽出来，逐条
拿到真实响应里比对。

边界与取舍（说清楚，免得后人误用）
---------------------------------
- **只查必填字段**（声明里没有 ``?`` 的那些）。可选字段本职就是"可能不在"，比如
  ``error`` 只在失败时出现；查它们会在正确实现上假红，然后被人加 skip 抹掉，
  那才是真的失去价值。
- 列表类路径在列表非空时才查元素形状。空列表没法证明形状错，硬查等于要求"必须
  先有数据"。
"""

from __future__ import annotations

import pathlib
import re

import pytest
from fastapi.testclient import TestClient

from src.gateway.main import get_app

#: 独立推导的 contracts.ts 路径 —— 从**本文件**位置往上走，不用任何产品侧常量。
_CONTRACTS = pathlib.Path(__file__).resolve().parents[1] / (
    "apps/console/console/src/lib/contracts.ts"
)


def _interface_bodies(text: str) -> dict[str, str]:
    """每个 ``export interface X { ... }`` 的花括号内容（保嵌套，不删任何层级）。"""
    bodies: dict[str, str] = {}
    for match in re.finditer(r"export interface (\w+)\s*\{", text):
        name = match.group(1)
        index, depth, chars = match.end(), 1, []
        while index < len(text) and depth > 0:
            char = text[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
            if depth > 0:
                chars.append(char)
            index += 1
        bodies[name] = "".join(chars)
    return bodies


def _strip_comments(body: str) -> str:
    """去掉行注释与块注释：注释里举例用的字段名不是契约。"""
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    return re.sub(r"^\s*//.*$", "", body, flags=re.M)


def _required_paths(body: str, prefix: str = "") -> list[str]:
    """递归抽出所有**必填**字段的点号路径。

    遇到 ``{`` 就往下钻而不是跳过嵌套 —— 那是上一版失明的地方。``records`` /
    ``arrays`` 的元素形状单独处理（见 :data:`LIST_PATHS`）。
    """
    body = _strip_comments(body)
    paths: list[str] = []
    index = 0
    # 逐个消费本层的 `name? : <value>` 声明。
    field_re = re.compile(r"(?:^|[;{}\s])(\w+)(\?)?\s*:")
    while index < len(body):
        match = field_re.search(body, index)
        if not match:
            break
        name, optional = match.group(1), match.group(2)
        rest = body[match.end():].lstrip()
        path = f"{prefix}{name}"
        if rest.startswith("{"):
            # 嵌套对象：递归进去拿 `prefix.name.sub`。上层必填 ⇒ 子树全部可查。
            open_at = match.end() + (len(body[match.end():]) - len(rest))
            depth, cursor = 0, open_at
            while cursor < len(body):
                if body[cursor] == "{":
                    depth += 1
                elif body[cursor] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                cursor += 1
            inner = body[open_at + 1 : cursor]
            if not optional:
                # `{...}[]` 是对象数组（`bands: {key,label}[]`），它的 sub 路径在
                # 真实响应里要走 `bands[].key` —— 少了 `[]` 就会把数组当字典查，
                # 于是每条都"缺失"。这一个字符的差别能让整份契约假红。
                tail = body[cursor + 1 : cursor + 3]
                child_prefix = f"{path}[]." if tail.startswith("[]") else f"{path}."
                paths.extend(_required_paths(inner, child_prefix))
            # 跳过整块，避免把子串再扫一遍
            index = cursor + 1
            continue
        if not optional:
            paths.append(path)
        # 跳到本条声明的结尾（分号或换行），避免重复匹配
        semi = body.find(";", match.end())
        newline = body.find("\n", match.end())
        stops = [p for p in (semi, newline) if p != -1]
        index = min(stops) + 1 if stops else len(body)
    return sorted(set(paths))


def declared_required_fields() -> dict[str, list[str]]:
    """每个导出接口 → 它的必填字段点号路径列表。"""
    if not _CONTRACTS.is_file():  # pragma: no cover - 只在检出不全时触发
        pytest.skip(f"contracts.ts not found at {_CONTRACTS}")
    text = _CONTRACTS.read_text(encoding="utf-8")
    return {
        name: _required_paths(body)
        for name, body in _interface_bodies(text).items()
    }


#: 「TS 类型 ↔ 真实端点」对照表。
#:
#: 这张表本身是**声明**，所以它的价值取决于每一行是否真实防锈；若哪天某个类型只被
#: 没人用的端点引用，这张表不会发现。它发现的是另一件事：**声明存在的前提下，
#: 服务端有没有兑现**。这正是 declared_test_cases 漏掉的那一层。
TYPE_TO_ENDPOINT: dict[str, tuple[str, str | None]] = {
    # type: (endpoint, 该类型的实体在响应里的路径；None = 根)
    "RosterPayload": ("/v1/dashboard/roster", None),
    "DashboardSummary": ("/v1/dashboard/summary", None),
    "ActivityItem": ("/v1/dashboard/activity", "activity[]"),
    "AnalyticsPayload": ("/v1/dashboard/analytics", None),
    "EmployeesPayload": ("/v1/employees", None),
    "EmployeeSummary": ("/v1/employees", "employees[]"),
    "AgentInfo": ("/v1/employees", "agents[]"),
    "EmployeeStats": ("/v1/employees", "stats"),
    "GoalsPayload": ("/v1/goals", None),
    "GoalSummary": ("/v1/goals", "goals[]"),
    "WorkflowsPayload": ("/v1/workflows", None),
    "WorkflowSummary": ("/v1/workflows", "workflows[]"),
    "TimeseriesPoint": ("/v1/dashboard/analytics", "timeseries.days[]"),
    "BreakdownItem": ("/v1/dashboard/analytics", "breakdown.items[]"),
    "RegistryKernel": ("/v1/dashboard/roster", "kernels[]"),
    "RegistryLayer": ("/v1/dashboard/roster", "layers[]"),
    "ProviderItem": ("/v1/dashboard/roster", "providers.items[]"),
}


def _resolve(payload: dict, path: str | None):
    """按 "a.b[].c" 取到目标 dict；空集合返回 None（表示不可比对）。"""
    node: object = payload
    if path is None:
        return node
    for step in path.split("."):
        if not step:
            continue
        if step.endswith("[]"):
            key = step[:-2]
            node = node[key]  # type: ignore[index]
            if not isinstance(node, list) or not node:
                return None  # 空列表：无法证明形状，跳过而不是硬查
            return node[0]
        node = node[step]  # type: ignore[index]
    return node


def _get_at(node, dotted: str) -> tuple[bool, object]:
    """在点号路径上取值；(是否存在, 值)。

    支持 ``bands[].key`` 这种带数组下标的路径：`[]` 段取列表首元素继续钻。列表
    为空时按"不可证明"处理并返回 False —— 与 `_resolve` 对空集合的态度一致：
    不能查就不假装查过了。
    """
    cur = node
    for part in dotted.split("."):
        if part.endswith("[]"):
            part = part[:-2]
            if not isinstance(cur, dict) or part not in cur:
                return False, None
            cur = cur[part]
            if not isinstance(cur, list) or not cur:
                return False, None
            cur = cur[0]
            continue
        if not isinstance(cur, dict) or part not in cur:
            return False, None
        cur = cur[part]
    return True, cur


@pytest.fixture
def client(auth_headers):
    with TestClient(get_app(), headers=auth_headers) as c:
        yield c


@pytest.fixture(scope="module")
def responses():
    """一次性取回所有相关端点的真实响应（module 级，避免每个用例重启 app）。

    这里刻意**不**复用 ``get_app()`` 之外的任何取数封装：契约的一手证据只能是
    真实的 HTTP 响应，中间多加一层，等于给自己留一个漂移的位置。
    """
    from src.security import get_jwt_handler

    token, _ = get_jwt_handler().create_token(subject="contract-gate")
    seen: dict[str, dict] = {}
    with TestClient(get_app(), headers={"Authorization": f"Bearer {token}"}) as c:
        for endpoint in {ep for ep, _ in TYPE_TO_ENDPOINT.values()}:
            response = c.get(endpoint)
            assert response.status_code == 200, (
                f"{endpoint} 返回 {response.status_code}，无法作为契约证据"
            )
            seen[endpoint] = response.json()
    return seen


@pytest.mark.parametrize("type_name", sorted(TYPE_TO_ENDPOINT))
def test_declared_required_fields_are_actually_sent(type_name, responses):
    """contracts.ts 里每个必填字段（含嵌套路径），服务端必须真的发。

    这就是那类缺陷的直接拦截网：解构出来的 `undefined` 从此跑不到界面上。
    """
    endpoint, path = TYPE_TO_ENDPOINT[type_name]
    declared = declared_required_fields().get(type_name, [])
    if not declared:
        pytest.skip(f"{type_name} 没有必填字段 —— 无可比对的契约")

    node = _resolve(responses[endpoint], path)
    if node is None:
        pytest.skip(f"{endpoint} 的 {path or '(根)'} 为空，无法比对形状")

    missing = [p for p in declared if not _get_at(node, p)[0]]
    assert not missing, (
        f"{type_name} 声明了 {missing}，但 {endpoint} 的 "
        f"{path or '(根)'} 没有这些键 —— 前端会渲染 undefined。\n"
        f"实际键：{sorted(node) if isinstance(node, dict) else node}"
    )


def test_the_gate_is_not_vacuous():
    """自检：这个门禁必须真的有内容可查，不然它就是个装饰。

    曾经坏掉的安全/诚实类测试，九成不是"判错"而是"判了个空集"。这里把那份
    自我怀疑固化成断言：表必须非空、解析器必须能读出字段。
    """
    assert TYPE_TO_ENDPOINT, "对照表是空的 —— 门禁什么也没在守"
    fields = declared_required_fields()
    assert fields, "contracts.ts 里没解析到任何接口 —— 解析器已经失效"
    covered = {p for paths in fields.values() for p in paths}
    assert len(covered) >= 40, f"覆盖范围退化到 {len(covered)} 个字段"


def test_nested_fields_are_visible_to_the_parser():
    """**针对性自检**：嵌套层级必须被解析到。

    这条是本门禁用血换来的教训固化的产物。上一版解析器为了"只看顶层"而把嵌套
    对象整块删掉，于是对 ``totals.declared_test_cases`` 这种层级完全失明 —— 而那
    恰好就是原缺陷所在的位置。变异实测：添加该字段后门禁依然 16 passed。

    所以这里把踩过的坑钉成断言：解析器必须能看到超过一层的路径，否则这个门禁
    在最需要它的地方什么也查不到。
    """
    fields = declared_required_fields()
    roster = fields.get("RosterPayload", [])
    nested = [p for p in roster if "." in p]
    assert nested, (
        f"RosterPayload 没有解析出任何嵌套路径 —— 解析器又失明了。"
        f"实际解析结果：{roster}"
    )
    # 原缺陷所在的层级，必须从此不再漏。
    assert any(p.startswith("totals.") for p in nested), (
        f"totals.* 没被解析到 —— 那正是 declared_test_cases 曾经藏身的地方。{roster}"
    )
    # providers / providers.active 这类多层结构同样要能看到。
    assert any(p.startswith("providers.") for p in nested), f"{roster}"


def test_resolver_handles_empty_lists_without_pretending():
    """空列表必须跳过而不是判定通过，否则空响应会让门禁假绿。"""
    assert _resolve({"activity": []}, "activity[]") is None
    assert _resolve({"totals": {"kernels": 3}}, "totals")["kernels"] == 3
    assert _resolve({"a": [{"x": 1}]}, "a[]") == {"x": 1}


def test_nested_comparison_catches_a_missing_key():
    """同一个比较器在非空数据上必须真的逐字段比对（含嵌套）。"""
    declared = declared_required_fields().get("GoalSummary", [])
    assert declared, "GoalSummary 应当有必填字段"

    populated = {"goals": [{f.split(".")[-1]: 1 for f in declared}]}
    node = _resolve(populated, "goals[]")
    assert node is not None, "非空集合不该返回 None"
    assert [f for f in declared if not _get_at(node, f)[0]] == [], (
        "完整数据应全部命中"
    )

    broken = {"goals": [{f.split(".")[-1]: 1 for f in declared[:-1]}]}
    missing = [f for f in declared if not _get_at(_resolve(broken, "goals[]"), f)[0]]
    assert missing == [declared[-1]], f"漏发字段应被查出，实际={missing}"