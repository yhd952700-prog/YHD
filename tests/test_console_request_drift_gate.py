"""控制台**写请求体**漂移门禁 —— 前端真的发出去的字段，必须与后端接受的字段一致。

为什么这个测试存在（与 ``test_contracts_drift_gate.py`` 互为镜像）
------------------------------------------------------------------
``test_contracts_drift_gate.py`` 守的是**响应**方向：``contracts.ts`` 声明了某字段，
后端就真的得发出来（否则界面渲染 ``undefined``）。

本文件守的是**请求**方向，它拦的是一类**更隐蔽**的缺陷 —— 前端类型声明里**有**
这个字段、``npx tsc -b`` 也全绿，但 ``JSON.stringify`` 的请求体里**根本没有**它：

    operator.ts   createGoal(body) {
      body: JSON.stringify({
        natural_language, scope, plan_mode, verification_criteria, background,
        // ← project_id 在此丢失
      })
    }

TypeScript 只校验「``CreateGoalBody`` 允许传 ``project_id``」，**不校验**
``project_id`` 有没有被序列化进请求体。于是调用方传了 ``project_id``，后端收到的
却永远是默认 ``None``：目标落成"独立目标"，归属关系被**静默丢弃**，没有任何报错。

真身缺陷（本门禁发布时抓到的那一条）：``operator.ts`` 的 ``createGoal`` 声明了
``project_id``，但请求体从未带上它 —— 整条「目标 ↔ 项目」归属链在控制台上**不可达**，
而 ``Projects.tsx`` 还在对用户宣称"创建目标时可选归属项目"。类型声明 ≠ 已接线。

两个互不相关的真相交叉验证
--------------------------
    前端 ``JSON.stringify({...})`` 的键   ← 从 operator.ts 原文解析
    后端 Pydantic 请求模型的字段          ← 直接 import 真模型

两侧谁也不是从谁推出来的。任一侧单独漂移，这里就变红。

判据为什么用**集合相等**而不是"包含"
------------------------------------
请求体是"客户端填、服务端收"的东西，所以后端模型里的每一个字段**按定义**都是
客户端可写字段。若后端多出一个字段而控制台从没发过它 —— 那就是又一门
"wire 上存在、产品面上不存在"的能力，正是本门禁要拦的形状。集合相等会把这件事
变成红灯，强制人做一次显式裁决（接线，或说明为何不接线），而不是让缺口悄悄躺着。

边界与取舍
----------
- 只覆盖**有 JSON 请求体的写端点**（创建类）。路径参数 / 删除类没有请求体可查，
  不在此表里。
- 解析前先剥注释：本文件自己就在说明里写了 ``project_id`` 等字样，若不剥注释，
  会把说明文字当成"已接线的键"，门禁反而被自己的注释骗绿。
"""

from __future__ import annotations

import pathlib
import re

import pytest

from src.gateway.ai_management import GoalCreateRequest, HireEmployeeRequest
from src.gateway.projects import ProjectCreateRequest

#: 独立推导路径：本文件在 ``tests/`` → 仓库根 → operator.ts。不用产品侧常量。
_OPERATOR = pathlib.Path(__file__).resolve().parents[1] / (
    "apps/console/console/src/lib/operator.ts"
)

#: 「前端写函数 ↔ 后端请求模型」对照表。
#:
#: 这张表本身是**声明**。它的价值在于：只要某个写动作被登记在这里，前端就必须
#: 真的把后端能收的字段都发出去 —— 而不是"类型里写了、wire 上没有"。
WRITE_TO_MODEL = {
    "createGoal": GoalCreateRequest,
    "hireEmployee": HireEmployeeRequest,
    "createProject": ProjectCreateRequest,
}


def _strip_comments(text: str) -> str:
    """先剥行注释、再剥块注释（顺序与 test_console_invariants 同因，勿调换）。"""
    text = re.sub(r"^\s*//.*$", "", text, flags=re.M)
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def _stringify_body_keys(text: str, func_name: str) -> set[str]:
    """取 ``func_name`` 里 ``JSON.stringify({...})`` 的**顶层**键集合。

    顶层 = 深度 1 的 ``name:`` 声明。值里的嵌套对象不展开（请求体不会把嵌套
    当成后端字段）。
    """
    marker = f"function {func_name}("
    start = text.find(marker)
    if start == -1:
        raise AssertionError(f"operator.ts 里找不到写函数 {func_name}()")
    call = text.find("JSON.stringify({", start)
    if call == -1:
        raise AssertionError(f"{func_name}() 里没有 JSON.stringify({{...}}) 请求体")
    open_brace = text.index("{", call)
    # 括号配平，取到与它匹配的闭括号。
    depth, cursor = 0, open_brace
    while cursor < len(text):
        char = text[cursor]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                break
        cursor += 1
    body = text[open_brace + 1 : cursor]

    keys: set[str] = set()
    local_depth = 0
    index = 0
    key_re = re.compile(r"(\w+)\s*:")
    while index < len(body):
        char = body[index]
        if char in "{[":
            local_depth += 1
            index += 1
            continue
        if char in "}]":
            local_depth -= 1
            index += 1
            continue
        if local_depth == 0:
            match = key_re.match(body, index)
            if match:
                keys.add(match.group(1))
                index = match.end()
                continue
        index += 1
    return keys


def _backend_fields(model) -> set[str]:
    """Pydantic 请求模型的字段名（v2 ``model_fields``，兼容 v1 ``__fields__``）。"""
    fields = getattr(model, "model_fields", None)
    if fields is None:  # pragma: no cover - 仅 pydantic v1
        fields = model.__fields__
    return set(fields.keys())


@pytest.mark.parametrize("func_name", sorted(WRITE_TO_MODEL))
def test_frontend_sends_exactly_what_backend_accepts(func_name):
    """前端请求体键集 == 后端请求模型字段集。

    * 多出来的键 → 后端要么 422、要么静默忽略（都要暴露）；
    * 少发出去的键 → 该能力在**产品面上不可达**（就是 ``project_id`` 那条缺陷的形状）。
    """
    if not _OPERATOR.is_file():  # pragma: no cover - 仅检出不全时
        pytest.skip(f"operator.ts not found at {_OPERATOR}")
    keys = _stringify_body_keys(_strip_comments(_OPERATOR.read_text(encoding="utf-8")), func_name)
    model = WRITE_TO_MODEL[func_name]
    fields = _backend_fields(model)

    assert keys, f"{func_name}() 没解析出任何请求体键 —— 解析器已失效"
    assert fields, f"{model.__name__} 没有任何字段 —— 后端模型异常"

    dropped = fields - keys  # 后端能收、前端从没发 → 产品面不可达
    unknown = keys - fields  # 前端发了、后端不收 → 422/静默忽略

    assert not dropped, (
        f"{func_name}() 的请求体缺少 {sorted(dropped)} —— 后端 {model.__name__} 接受这些字段，"
        f"但控制台从没发出去过：声明在类型里 ≠ 已接线（project_id 缺陷形状）。\n"
        f"前端实际发送：{sorted(keys)}\n后端接受：{sorted(fields)}"
    )
    assert not unknown, (
        f"{func_name}() 发送了后端 {model.__name__} 不认识的字段 {sorted(unknown)} —— "
        f"会被 422 或静默忽略。"
    )


def test_the_gate_is_not_vacuous():
    """自检：门禁必须有内容可查，否则它就只是个装饰。

    与 contracts 门禁同源 —— 一个守空集的守卫比没有守卫更危险，因为它让人以为
    这里有人看着。
    """
    assert WRITE_TO_MODEL, "对照表是空的 —— 门禁什么也没在守"
    assert _OPERATOR.is_file(), f"找不到 operator.ts：{_OPERATOR}"
    text = _strip_comments(_OPERATOR.read_text(encoding="utf-8"))
    for func_name in WRITE_TO_MODEL:
        assert _stringify_body_keys(text, func_name), (
            f"{func_name}() 解析出空键集 —— 解析器失明或函数被改签名"
        )


def test_goal_create_body_type_and_wire_agree():
    """``CreateGoalBody`` 类型声明的字段必须都真的进请求体。

    这是本门禁存在的**直接理由**：``project_id`` 曾经只活在类型里。这里把
    "类型声明" 与 "wire 序列化" 两个真相直接对撞，钉死这条缺陷不再回来。
    """
    if not _OPERATOR.is_file():  # pragma: no cover
        pytest.skip("operator.ts missing")
    text = _strip_comments(_OPERATOR.read_text(encoding="utf-8"))
    wire_keys = _stringify_body_keys(text, "createGoal")

    # 从 CreateGoalBody 接口声明里抽字段名。
    iface = text.find("interface CreateGoalBody")
    assert iface != -1, "找不到 CreateGoalBody 接口声明"
    open_brace = text.index("{", iface)
    close_brace = text.index("}", open_brace)
    declared = set(re.findall(r"(\w+)\s*[?]?\s*:", text[open_brace + 1 : close_brace]))
    assert declared, "CreateGoalBody 没解析出字段 —— 解析器失明"

    missing = declared - wire_keys
    assert not missing, (
        f"CreateGoalBody 声明了 {sorted(missing)}，但 createGoal 的请求体里没有它们 —— "
        f"类型里有、wire 上没有，正是 project_id 缺陷的形状。"
    )
