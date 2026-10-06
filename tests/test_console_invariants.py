"""控制台前端不变量 —— 锁死「每一条规格/门禁都依赖的事实」。

为什么需要这个文件
------------------
浏览器端到端在本机跑不起来（无浏览器），EAP 的 Playwright 规格
（``eap/01-browser-click-through/specs/*.spec.ts``）虽然把这些边界写成了真实
断言，但它们**只在有浏览器时**才提供证据。CI 与本地默认路径里，这些事实实际上
无人看守。

这里用**静态源码断言**把同一批不变量钉住：它是可复现的二值事实（字段/字面量在
不在源码里），不依赖"我声称自己跑过浏览器"，因此能在无浏览器环境下充当守门人。
它与 Playwright 规格是互补而非重复：Playwright 证明**运行时**行为，本文件证明
**契约字面量**没有被悄悄改掉。

四组不变量，各自防一件事
------------------------
1. **错误可读 + 可追溯**：``api.ts`` 必须真的走 ``errorMessage.readError``（而不是
   各自内联一份），且 ``trace_id`` 必须被保留 —— 它是去服务端日志捞完整堆栈的唯一
   钥匙，历史上被内联旧逻辑丢掉了。
2. **P5 审批是"记录"不是"放行"**：按钮文案必须是「记录授权」。把它改成「放行」等于
   对用户宣称"点一下就解封了"，而内核拦截并未解除 —— 这正是主理人要求规格必须断言
   的那条边界。
3. **P9 Identity 严格只读**：页面上不得出现任何写动作按钮（授权/创建/删除/保存…）。
   赋权面尚无人类复核闸门，一个能点的写按钮就是越权入口。
4. **路由无 ``/console/`` 前缀**：控制台是单端口应用，根路径返回 index.html，页面靠
   ``?p=<key>`` 切换。任何硬编码 ``/console/`` 都会 404 并悄悄回落 overview。

这些断言只**读**他人源码，不修改它们；本文件只对我自己新增/认领的前端事实负责。
"""

from __future__ import annotations

import pathlib
import re

import pytest

#: 独立于产品代码推导的路径：``tests/`` → 仓库根 → 前端src。
_CONSOLE_SRC = pathlib.Path(__file__).resolve().parents[1] / (
    "apps/console/console/src"
)


def _files() -> list[pathlib.Path]:
    if not _CONSOLE_SRC.is_dir():  # pragma: no cover - 仅检出不全时
        pytest.skip(f"console source not present at {_CONSOLE_SRC}")
    return sorted(
        p
        for p in _CONSOLE_SRC.rglob("*")
        if p.suffix in {".ts", ".tsx"} and "node_modules" not in p.parts
    )


def _read(rel: str) -> str:
    path = _CONSOLE_SRC / rel
    if not path.is_file():  # pragma: no cover
        pytest.skip(f"{rel} not present")
    return path.read_text(encoding="utf-8")


def _blob() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in _files())


def _code_only(text: str) -> str:
    """剥掉块注释与行注释，只留下真正会执行/显示的代码。

    为什么需要：本文件自己也踩过一次 —— ``errorMessage.ts`` 的 docstring 里为了
    说明"旧逻辑用 JSON.stringify 会糊一脸"，**故意**写了 ``JSON.stringify`` 字样。
    一个不剥注释的"不该出现 JSON.stringify"断言，会把这段**必须保留的缺陷说明**
    判成违规。于是守卫自己产生了假红 —— 那比没有守卫更糟，因为它会诱导人把正确的
    注释删掉来"修复"测试。所有"不该出现 X"的断言都必须走这里。
    """
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"^\s*//.*$", "", text, flags=re.M)


class TestErrorMessageIsWiredAndTraceable:
    """错误必须能被看懂，且能照着 ``trace_id`` 查到服务端堆栈。"""

    def test_api_uses_the_shared_error_reader(self):
        api = _read("lib/api.ts")
        assert "from './errorMessage'" in api, (
            "api.ts 不再从 errorMessage 导入 readError —— 若各处内联一份，"
            "可读化/trace_id 的修复就会在某处分叉回去"
        )
        assert re.search(r"\breadError\b", api), "api.ts 里找不到 readError 的使用点"

    def test_trace_id_is_preserved(self):
        em = _read("lib/errorMessage.ts")
        assert "trace_id" in em, (
            "错误处理丢了 trace_id —— 它是去服务端日志检索完整堆栈的唯一钥匙"
        )
        assert "X-Trace-ID" in em, "缺少 X-Trace-ID 响应头回退（跨域部署也拿得到）"

    def test_object_detail_is_flattened_not_a_json_blob(self):
        # 只查可执行代码（剥注释）：docstring 里为了说明旧缺陷会正当出现
        # "JSON.stringify" 字样，那段说明必须保留，不能被当成违规删掉。
        em_code = _code_only(_read("lib/errorMessage.ts"))
        assert "JSON.stringify" not in em_code, (
            "又用 JSON.stringify 渲染对象 detail 了 —— 会把原始 JSON 糊给用户"
        )
        assert "describeDetail" in em_code, (
            "缺少 describeDetail：对象/数组 detail 没有被可读化"
        )


class TestApprovalIsRecordNotRelease:
    """P5：审批中心记录一次**审计化授权**，不是"放行"。内核拦截不因此解除。"""

    def test_button_is_labelled_record_authorisation(self):
        ac = _read("components/ApprovalCenter.tsx")
        assert "记录授权" in ac, (
            "审批按钮文案必须是「记录授权」；改成「放行」等于宣称点一下就解封，"
            "而内核拦截并未解除"
        )

    def test_honest_disclaimer_is_present(self):
        ac = _read("components/ApprovalCenter.tsx")
        assert "不是通行证" in ac or "不是「放行」" in ac, (
            "缺少「凭据是审计化授权记录、不是通行证」的诚实说明 —— "
            "P5 的边界必须对人明说"
        )


class TestIdentityIsStrictlyReadOnly:
    """P9：Identity 严格只读，页面上不得有任何写动作控件。"""

    def test_readonly_declaration_present(self):
        identity = _read("pages/Identity.tsx")
        assert "严格只读" in identity, "Identity 页缺少「严格只读」的不变量声明"

    def test_no_write_action_buttons(self):
        identity = _read("pages/Identity.tsx")
        # 抽取所有 <button ...>文本</button> 的可见文案，逐一确认没有写动词。
        labels = re.findall(r"<button[^>]*>([^<]*)</button>", identity)
        write_verbs = ("授权", "创建", "删除", "保存", "新增", "grant")
        offenders = [
            label.strip()
            for label in labels
            if any(v in label for v in write_verbs)
        ]
        assert not offenders, (
            f"Identity 页出现了写动作按钮 {offenders} —— 赋权面无人类复核闸门，"
            f"能点的写按钮就是越权入口（P9 必须严格只读）"
        )


class TestRoutingHasNoConsolePrefix:
    """控制台是单端口应用：根路径返回 index.html，页面靠 ?p=<key> 切换。"""

    def test_no_hardcoded_console_prefix(self):
        offenders = [
            p.name
            for p in _files()
            if "/console/" in p.read_text(encoding="utf-8")
        ]
        assert not offenders, (
            f"{offenders} 硬编码了 /console/ 前缀 —— 单端口下会 404 并悄悄回落 overview"
        )
