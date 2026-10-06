"""`/v1/production/preflight` 的产品面：后端真的发，前端真的显示，且不撒谎。

这个测试防住的三类缺陷
--------------------
1. **后端发了、前端不读**（``declared_test_cases`` / ``errors[]`` 同类）。契约里
   声明了 ``PreflightPayload``，但如果没有任何页面真的调用那个端点并渲染它，
   这份契约就又是一次"看起来有、其实没人用"。所以这里静态断言**接线**存在。
2. **渲染了假绿灯**。``ok: false`` 意味着"此配置不该承载生产流量"。如果页面把它
   渲染成平静的绿色清单，就是在宣称一份它并不拥有的安全性。断言页面的语气与
   分色跟随真实 ``critical_count``。
3. **越界显示配置值**。后端**只下发键名 / 代码 / 严重级别**，绝不下发配置值。
   页面若开始显示密钥，那既是安全事故也是对上游契约的误读。

为什么用静态断言而不是"渲染一次看看"
------------------------------------
本机没有浏览器，任何"渲染后截图 / DOM 断言"都只能是我编的。静态断言至少是
**可复现的真相**：端点路径、字段名、字面量在不在源码里是二值事实。
运行时呈现因此标注为 NOT VERIFIED，而不是假装验过。
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest
from fastapi.testclient import TestClient

from src.gateway.main import get_app

_CONSOLE_SRC = pathlib.Path(__file__).resolve().parents[1] / (
    "apps/console/console/src"
)
_PAGE = _CONSOLE_SRC / "pages" / "Preflight.tsx"


def _console_files() -> list[pathlib.Path]:
    if not _CONSOLE_SRC.is_dir():  # pragma: no cover
        pytest.skip(f"console source not present at {_CONSOLE_SRC}")
    return sorted(
        p
        for p in _CONSOLE_SRC.rglob("*")
        if p.suffix in {".ts", ".tsx"} and "node_modules" not in p.parts
    )


def _code_only(text: str) -> str:
    """剥掉块注释与行注释，只留下真正会执行/显示的代码。

    "不该出现 X"的断言必须走这里：docstring 里为了说明缺陷而故意引用的字面量
    是**必须保留的证据**，一并禁掉等于逼着后人删掉缺陷记录。

    **顺序必须是「先行注释、后块注释」** —— 这是本函数第二次栽在同一处。
    仓库里存在这样的行注释::

        // 审计链：后端 `/v1/audit/*` 一直有真实数据

    那个 ``/*`` 是**行注释里的孤立片段**。若先剥块注释，正则会从它一路配到
    940 字符之后某个**真正的** ``*/``，把中间的活代码（整个 GOVERNANCE_NAV）
    一起吞掉 —— 于是断言"nav 里有 preflight"会读到一段被吃空的源码而假红，
    且报错信息看起来像"功能没写"，极具误导性。先剥行注释就切断了这个配对。
    """
    text = re.sub(r"^\s*//.*$", "", text, flags=re.M)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return text


@pytest.fixture
def client(auth_headers):
    with TestClient(get_app(), headers=auth_headers) as c:
        yield c


class TestBackendActuallySendsThePreflightShape:
    """后端真的发这个形状 —— 契约不是照着想象写的。"""

    def test_endpoint_returns_the_declared_top_level_fields(self, client):
        response = client.get("/v1/production/preflight")
        assert response.status_code == 200, response.text
        body = response.json()
        for key in ("ok", "app_env", "critical_count", "warning_count", "findings"):
            assert key in body, (
                f"preflight 响应缺少 {key!r}：前端契约声明了它，"
                f"后端不发就会渲染成 undefined。实际键={sorted(body)}"
            )

    def test_ok_agrees_with_critical_count(self, client):
        """``ok`` 不是独立判据 —— 它必须**恒等于** critical_count == 0。

        两者若能各说各话，界面就会出现"显示通过但下面列着严重问题"的自相矛盾
        （这正是 /v1/ready 曾经踩过的坑）。
        """
        body = client.get("/v1/production/preflight").json()
        assert body["ok"] is (body["critical_count"] == 0), (
            f"ok={body['ok']!r} 与 critical_count={body['critical_count']} 矛盾"
        )

    def test_counts_match_the_findings_actually_present(self, client):
        """计数必须数的是**真实列表**，不是另一套算出来的数字。"""
        body = client.get("/v1/production/preflight").json()
        findings = body["findings"]
        critical = sum(1 for f in findings if f["severity"] == "critical")
        warning = sum(1 for f in findings if f["severity"] == "warning")
        assert body["critical_count"] == critical, (
            f"critical_count={body['critical_count']} 但列表里真有 {critical} 条"
        )
        assert body["warning_count"] == warning, (
            f"warning_count={body['warning_count']} 但列表里真有 {warning} 条"
        )

    def test_findings_carry_key_severity_code_message(self, client):
        """每条发现必须能让人"照着做"：变量名 + 代码 + 说明，缺一不可。"""
        body = client.get("/v1/production/preflight").json()
        for finding in body["findings"]:
            for key in ("key", "severity", "code", "message"):
                assert key in finding, (
                    f"finding 缺少 {key!r}：没有它运维无法定位。实际={finding}"
                )
            assert finding["severity"] in {"critical", "warning"}, (
                f"severity 只应是 critical/warning，实际={finding['severity']!r}"
            )

    def test_endpoint_requires_a_principal(self):
        """无令牌必须 401 —— 这是"预检信息只给人类主体"的边界证据。"""
        with TestClient(get_app()) as anon:
            assert anon.get("/v1/production/preflight").status_code == 401


class TestConsoleActuallyRendersIt:
    """前端真的接了线并渲染 —— 本文件的主要价值。"""

    def test_page_exists_and_calls_the_endpoint(self):
        if not _PAGE.is_file():  # pragma: no cover
            pytest.skip("Preflight.tsx not present")
        src = _CONSOLE_SRC / "pages" / "Preflight.tsx"
        code = _code_only(src.read_text(encoding="utf-8"))
        assert "'/v1/production/preflight'" in code, (
            "Preflight 页面没有调用 /v1/production/preflight —— "
            "后端真实可用的端点又变成一个无产品视图的死端点"
        )

    def test_page_is_registered_and_reachable(self):
        """nav + PAGE_META + 路由三处都要有，否则页面写出来也点不到。"""
        nav = (_CONSOLE_SRC / "lib" / "nav.ts").read_text(encoding="utf-8")
        app = (_CONSOLE_SRC / "App.tsx").read_text(encoding="utf-8")
        assert "preflight" in _code_only(nav), "nav.ts 没有 preflight 入口"
        assert "preflight:" in _code_only(app), "App.tsx 的 PAGE_META 没有 preflight"
        assert re.search(r"active\s*===\s*'preflight'", _code_only(app)), (
            "App.tsx 路由没有渲染 Preflight —— 菜单里有、点进去是空白"
        )

    def test_contracts_declare_the_shape(self):
        contracts = (_CONSOLE_SRC / "lib" / "contracts.ts").read_text(encoding="utf-8")
        for field in ("ok", "app_env", "critical_count", "warning_count", "findings"):
            assert re.search(rf"\b{field}\??\s*:", contracts), (
                f"contracts.ts 未声明 {field!r} —— TS 层面就挡住了显示"
            )

    def test_honesty_counts_are_rendered_not_assumed(self):
        """页面必须读**后端的** critical_count，而不是自己 if(ok) 猜一个。"""
        code = _code_only(_PAGE.read_text(encoding="utf-8"))
        assert re.search(r"\.critical_count", code), (
            "页面没有引用后端的 critical_count —— 若只用 ok 决定严重项数，"
            "计数就与后端脱钩"
        )
        assert re.search(r"\.warning_count", code), "页面没有引用后端的 warning_count"

    def test_unresolved_severity_is_not_green(self):
        """未知 severity 必须按严重处理，不能默认绿。

        这是"未知即不可信"的直接编码：把没识别出来的取值显示成绿色，
        等于凭猜测放行一份生产配置。
        """
        code = _code_only(_PAGE.read_text(encoding="utf-8"))
        fn = re.search(r"function severityTone\([^)]*\)[^{]*\{(.*?)\n\}", code, re.S)
        assert fn, "找不到 severityTone —— 严重级别的分色逻辑应集中在一处"
        body = fn.group(1)
        assert "warning" in body and "danger" in body, (
            "severityTone 必须同时处理 warning 与 danger"
        )
        # 只有显式的 warning 才降级；默认分支必须是 danger。
        assert re.search(r"===\s*'warning'\s*\?\s*'warn'\s*:\s*'danger'", body), (
            "severityTone 的默认分支不是 danger —— 未知 severity 会被显示成非严重"
        )

    def test_page_does_not_display_configuration_values(self):
        """页面绝不能显示配置值 —— 后端根本不下发，展示了就是编造。"""
        code = _code_only(_PAGE.read_text(encoding="utf-8"))
        for forbidden in ("secret_key", "SECRET_KEY", "api_key", "token"):
            assert forbidden not in code, (
                f"页面引用了 {forbidden!r} —— 本端点只下发键名/代码/严重级别，"
                f"任何配置值都是编造或泄漏"
            )

    def test_no_write_call_in_the_page(self):
        """预检是只读体检报告 —— 页面上不得出现写请求。"""
        code = _code_only(_PAGE.read_text(encoding="utf-8"))
        assert not re.search(r"apiFetch<[^>]*>\(\s*['\"`][^'\"`]*,\s*\{", code), (
            "Preflight 页面出现了带请求选项的调用 —— 本页必须严格只读"
        )
        assert "useApi<PreflightPayload>('/v1/production/preflight'" in code, (
            "Preflight 页面应当只通过 useApi 发这一个 GET"
        )


def test_live_response_and_contract_agree_on_field_names(client):
    """三方交叉验证的一个最小实例：真实响应 vs 契约声明的键**逐字一致**。

    这一条把「后端发什么」与「前端声明什么」放在同一份证据上，
    任何一侧单独改字段名都会红。
    """
    body = client.get("/v1/production/preflight").json()
    contracts = (_CONSOLE_SRC / "lib" / "contracts.ts").read_text(encoding="utf-8")
    block = re.search(
        r"export interface PreflightPayload \{(.*?)\n\}", contracts, re.S
    )
    assert block, "contracts.ts 里找不到 PreflightPayload"
    declared = set(re.findall(r"^\s*(\w+)\??\s*:", block.group(1), re.M))
    sent = set(body.keys())
    assert sent <= declared, (
        f"后端多发了契约未声明的键：{sorted(sent - declared)}"
    )
    assert declared <= sent, (
        f"契约声明了后端不发的键：{sorted(declared - sent)}"
    )
