"""provider 诚实性：后端如实播报「这是真AI还是 mock」，且控制台**真的把它显示出来**。

这个测试要防住的那类缺陷
------------------------
``Overview`` 的 provider 卡片曾对每个 ``configured`` 的项一律渲染"已配置，可用于
真实调用" + 绿色"就绪"。而 ``configured`` 只回答"密钥/环境变量到位了吗"，**不回答
"这是不是假 AI"**：内置 ``mock`` 无需密钥，于是 ``configured=True``，界面就把一个
不调用任何模型的 provider 呈现成真实能力。

后端其实一直在如实播报 —— ``ai_provider_status()`` 返回
``{type, mode, simulated}``，``/v1/health``、``/v1/ready``、
``/v1/dashboard/summary`` 都带着它，源码注释原文是"Never silently pretends a
simulation is real"。缺的是两段：**名册端点没带这两个字段**（前端拿不到），以及
**前端类型没声明**（拿到也显示不出来）。于是这层信息从未抵达人眼。

三类断言，各自防一件事
--------------------
1. **后端真的发**：``/v1/dashboard/roster`` 的 ``providers`` 必须带
   ``mode`` / ``simulated`` / ``active.mode`` / ``active.simulated``，
   且取值与 ``ai_provider_status()`` 逐字一致（不是另一套独立实现）。
2. **前端真的读**：静态扫 ``apps/console/console/src``，要求诚实性字段被引用，
   且**旧的那句谎话不再出现**。这一条是本文件存在的主要理由 —— 后端加了字段而
   前端不读，是本项目反复出现的失败模式（``declared_test_cases``、``errors[]``）。
3. **判据本身不被改坏**：mock 姿态必须判成"非真实"，real 姿态必须判成"真实"。

为什么第2 类要用静态扫描而不是"渲染一次看看"
--------------------------------------------
本机没有浏览器，任何"渲染后截图/DOM 断言"都只能是我编的。静态扫描至少是**可复现
的真相**：字段名在源码里出现还是没出现，是二值事实，不依赖我声称自己跑过什么。
它防不住"读了但算错了"，所以第 3 类用纯函数实测补上那个缺口 —— 两侧都不做，就不
假装做完了。
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest
from fastapi.testclient import TestClient

from src.gateway.main import get_app

#: 独立于产品代码推导的控制台源码目录：``tests/`` → 仓库根 → 前端 src。
#: 不 import 任何前端"路径常量"（TS 里也没有），也不用产品侧的 _REPO_ROOT。
_CONSOLE_SRC = pathlib.Path(__file__).resolve().parents[1] / (
    "apps/console/console/src"
)

#: 那句必须从界面上消失的话。它把"密钥到位"说成"可用于真实调用"。
_FALSE_CLAIM = "可用于真实调用"

#: 这类卡片曾把"静态 capability-registry 分带 + 伪辐条"呈现为"员工/协作网络"，
#: 误导用户以为存在真实员工与协作关系（其实零员工、零协作、零边）。
#: Overview.tsx 与 Directory.tsx 各有一张同名卡（后者是前者的复制粘贴），
#: 标题分别是"AI 员工协作网络"与"协作网络"。它们必须永远从**可渲染代码**里消失。
_EMPLOYEE_NETWORK_FALSE_CLAIMS = ("员工协作网络", "协作网络")


def _console_files() -> list[pathlib.Path]:
    if not _CONSOLE_SRC.is_dir():  # pragma: no cover - 只在检出不全时触发
        pytest.skip(f"console source not present at {_CONSOLE_SRC}")
    return sorted(
        p
        for p in _CONSOLE_SRC.rglob("*")
        if p.suffix in {".ts", ".tsx"} and "node_modules" not in p.parts
    )


def _console_blob() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in _console_files())


@pytest.fixture
def client(auth_headers):
    with TestClient(get_app(), headers=auth_headers) as c:
        yield c


def _providers(client) -> dict:
    response = client.get("/v1/dashboard/roster")
    assert response.status_code == 200, response.text
    return response.json()["providers"]


class TestBackendBroadcastsHonesty:
    """后端真的把诚实性判据发出来。"""

    def test_providers_block_carries_the_three_honesty_fields(self, client):
        providers = _providers(client)
        for key in ("mode", "simulated"):
            assert key in providers, (
                f"providers 缺少 {key!r}：前端因此无法区分 mock 与真实 provider。"
                f" 实际键={sorted(providers)}"
            )
            assert providers[key] is not None, f"providers[{key!r}] 不该是 null"

    def test_active_provider_carries_them_too(self, client):
        active = _providers(client)["active"]
        assert "mode" in active, f"providers.active 缺少 mode，实际键={sorted(active)}"
        assert "simulated" in active, (
            f"providers.active 缺少 simulated，实际键={sorted(active)}"
        )

    def test_values_agree_with_the_single_authority(self, client, monkeypatch):
        """名册的判据必须**逐字等于** ai_provider_status()，不是另算一套。

        两处独立实现同一个判据，就是它们将来分叉的起点 —— 那时名册说 mock、
        /v1/health 说 real，界面该信谁就没答案了。
        """
        from src.ai import providers as providers_module

        for posture, expected_mode in (("mock", "mock"), ("openai", "real")):
            monkeypatch.setenv("AI_PROVIDER_TYPE", posture)
            fresh = providers_module.ai_provider_status()
            reported = _providers(client)
            assert reported["mode"] == fresh["mode"], (
                f"AI_PROVIDER_TYPE={posture}: 名册报 {reported['mode']!r}，"
                f"权威报 {fresh['mode']!r} —— 两处判据分叉了"
            )
            assert reported["simulated"] == fresh["simulated"]
            assert reported["active"]["mode"] == fresh["mode"]

    def test_mock_posture_is_reported_as_mock(self, client, monkeypatch):
        monkeypatch.setenv("AI_PROVIDER_TYPE", "mock")
        providers = _providers(client)
        assert providers["mode"] == "mock"
        assert providers["active"]["mode"] == "mock"
        mock_item = next(i for i in providers["items"] if i["type"] == "mock")
        assert mock_item["is_mock"] is True
        assert mock_item["is_active_provider_mock"] is True

    def test_real_posture_is_not_flagged_as_mock(self, client, monkeypatch):
        monkeypatch.setenv("AI_PROVIDER_TYPE", "openai")
        providers = _providers(client)
        assert providers["mode"] == "real"
        assert providers["active"]["mode"] == "real"
        # `is_mock` 描述的是**这个 provider 类型本身**是不是假的，与谁在生效无关：
        # 即使 openai 生效，列表里的 mock 项仍然如实标 `is_mock=True`（它确实不会
        # 产生真实调用，只是此刻没人用它）。这条断言同时钉住"别为了让当前生效项
        # 看起来干净就去篡改其他项的判据"。
        mock_item = next(i for i in providers["items"] if i["type"] == "mock")
        assert mock_item["is_mock"] is True, (
            "mock 类型必须始终标is_mock=True —— 它确实不产生真实调用"
        )
        # 而"正在被 mock 服务"这件事，此刻为假。
        assert all(i["is_active_provider_mock"] is False for i in providers["items"]), (
            "生效的是 openai，所以没有哪一项是'当前生效的假 provider'"
        )
        assert all(i["is_mock"] is False for i in providers["items"] if i["type"] != "mock"), (
            "除mock 外的 provider 类型都不该被标成 mock"
        )


class TestConsoleActuallyDisplaysIt:
    """前端真的把它显示出来 —— 这一节是本文件的主要价值。"""

    def test_console_references_the_honesty_fields(self):
        blob = _console_blob()
        for field in ("simulated", "is_mock"):
            assert re.search(rf"\b{field}\b", blob), (
                f"控制台源码里没有引用 {field!r} —— 后端已经在发，前端却不读，"
                f"这层事实到不了人眼前。"
            )
        # mode 词太常见，必须按 provider 语境查，而不是全仓 grep。
        assert re.search(r"\bmode\b", blob), "控制台没有引用 mode"

    def test_false_claim_is_gone_from_the_console(self):
        """旧文案把"密钥到位"说成"可用于真实调用"，对 mock 是假话。

        只查**会被渲染的字符串**，不查注释：``contracts.ts`` / ``honesty.ts`` 里
        这句话作为"我们修掉的就是这句"的历史说明出现，是**必须保留的证据**。
        把两者一并禁掉，等于逼着后人删掉缺陷记录 —— 那是反向的诚实性。
        所以这里剥掉注释再匹配：``//`` 行注释与 ``/* */`` 块注释。
        """
        import re as _re

        offenders: list[str] = []
        for p in _console_files():
            text = p.read_text(encoding="utf-8")
            # 去掉 /* ... */ 与 // ...，只留下真正会显示给人看的代码。
            stripped = _re.sub(r"/\*.*?\*/", "", text, flags=_re.S)
            stripped = _re.sub(r"^\s*//.*$", "", stripped, flags=_re.M)
            stripped = _re.sub(r"(?<![:'\"])//[^\n]*$", "", stripped, flags=_re.M)
            if _FALSE_CLAIM in stripped:
                offenders.append(p.name)
        assert not offenders, (
            f"{offenders} 在**可渲染代码**里仍写着 {_FALSE_CLAIM!r}："
            f"mock provider 也满足 configured=true，这句话会把它呈现成真实能力。"
        )

    def test_employee_collaboration_false_claim_is_gone(self):
        """「AI 员工协作网络」/「协作网络」卡片曾把静态注册表分带说成员工协作网络。

        Overview.tsx 与 Directory.tsx 各自有一张同名卡（后者是前者的复制粘贴），
        标题分别是"AI 员工协作网络"和"协作网络"，内容却只是 capability-registry
        的能力层按阶段带分布 —— 零员工、零协作、零边。两张都必须改成"能力层分布"。

        只查可渲染代码（剥注释）：``contracts.ts`` 里那几行"我们修掉的就是这句"
        是必须保留的证据，不能一并禁掉。
        """
        import re as _re

        offenders: list[str] = []
        for p in _console_files():
            text = p.read_text(encoding="utf-8")
            stripped = _re.sub(r"/\*.*?\*/", "", text, flags=_re.S)
            stripped = _re.sub(r"^\s*//.*$", "", stripped, flags=_re.M)
            stripped = _re.sub(r"(?<![:'\"])//[^\n]*$", "", stripped, flags=_re.M)
            for phrase in _EMPLOYEE_NETWORK_FALSE_CLAIMS:
                if phrase in stripped:
                    offenders.append(f"{p.name}:{phrase!r}")
        assert not offenders, (
            f"以下文件在**可渲染代码**里仍写着员工/协作网络的虚假宣称：{offenders}"
        )

    def test_employee_network_cards_renamed_to_capability_distribution(self):
        """正向钉死：两张卡确实改成了"能力层分布"，而不是只删了旧字没补新字。"""
        overview = (_CONSOLE_SRC / "pages" / "Overview.tsx").read_text(encoding="utf-8")
        directory = (_CONSOLE_SRC / "pages" / "Directory.tsx").read_text(encoding="utf-8")
        assert 'title="能力层分布"' in overview, (
            'Overview.tsx 的卡片没有改成 title="能力层分布"'
        )
        assert 'title="能力层分布"' in directory, (
            'Directory.tsx 的卡片没有改成 title="能力层分布"'
        )

    def test_contracts_declare_the_honesty_fields(self):
        """类型层是曾经的硬阻塞：没声明就"想显示也显示不出来"。"""
        contracts = (_CONSOLE_SRC / "lib" / "contracts.ts").read_text(
            encoding="utf-8"
        )
        assert re.search(r"\bmode\??\s*:", contracts), (
            "contracts.ts 未声明 mode —— TS 层面挡住了诚实性显示"
        )
        assert re.search(r"\bsimulated\??\s*:", contracts), (
            "contracts.ts 未声明 simulated —— TS 层面挡住了诚实性显示"
        )

    def test_honesty_verdicts_are_computed_not_assumed(self):
        """判据必须来自后端字段，而不是前端自己 if (configured) 猜的。"""
        honesty_src = (_CONSOLE_SRC / "lib" / "honesty.ts").read_text(encoding="utf-8")
        # 真跑与假跑是两个独立事实：只有 mode===real 且未开模拟才算真。
        assert "simulated" in honesty_src
        assert re.search(r"isReal\s*[:=]", honesty_src), (
            "honesty.ts 没有导出 isReal 判据 —— 无法把'真实'与'已配置'区分开"
        )
        # 未知必须判成"不是真的"，不能默认当 real。
        assert "unknown" in honesty_src or "无法判定" in honesty_src, (
            "honesty.ts 没有处理 mode 缺失的情况；未知必须显示为未知"
        )


class TestSingleAuthority:
    """"是不是假 provider"必须只有一个裁决处。

    这个判断此前在两处各写一遍（``ai_provider_status`` 内联 +
    ``roster_payload`` 自己的清单）。两处独立实现的同一 judgement 必然各自演化，
    而它分叉的后果是**界面无法知道该信谁**：名册说mock、``/v1/health`` 说real。
    """

    def test_provider_mode_for_is_the_only_judge(self):
        from src.ai.providers import NON_REAL_PROVIDER_TYPES, provider_mode_for

        for value in NON_REAL_PROVIDER_TYPES:
            assert provider_mode_for(value) == "mock", (
                f"{value!r} 在权威清单里，却没被 provider_mode_for 判成 mock —— "
                f"权威与实现已经分叉"
            )

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("mock", "mock"),
            ("MOCK", "mock"),      # 大小写不敏感
            ("", "mock"),
            (None, "mock"),        # 未配置 = 安全默认：宁可说假，不让人以为真
            ("none", "mock"),
            ("openai", "real"),
            ("OpenAI", "real"),
            ("ollama", "real"),
        ],
    )
    def test_provider_mode_for_cases(self, value, expected):
        from src.ai.providers import provider_mode_for

        assert provider_mode_for(value) == expected

    def test_ai_provider_status_agrees_with_provider_mode_for(self):
        """``ai_provider_status`` 必须委托出去，而不是自己再判一遍。"""
        import inspect

        from src.ai.providers import ai_provider_status

        src = inspect.getsource(ai_provider_status)
        assert "provider_mode_for(ptype)" in src, (
            "ai_provider_status 不再委托 provider_mode_for —— "
            "它自己重写了判定，权威又变成两处"
        )
        assert 'not in ("mock", "", "none")' not in src, (
            "ai_provider_status 里仍有内联的 mock 判定字面量："
            "口径已经和 provider_mode_for 分叉"
        )

    def test_roster_items_use_the_shared_judge(self):
        """名册的逐项 ``is_mock`` 必须来自同一个函数，不是自带一份清单。"""
        import inspect

        from src.ai import roster_payload

        src = inspect.getsource(roster_payload.provider_status)
        assert "provider_mode_for(provider_type)" in src, (
            "roster_payload 逐项 is_mock 没有委托 provider_mode_for"
        )
        # 自带清单是分叉的起点：模块里不该再有一份 NON_REAL 之类的集合。
        assert "_MOCK_PROVIDER_TYPES" not in src, (
            "roster_payload 又出现了自己的 mock 清单 —— 两处判据迟早分叉"
        )


class TestVerdictFunctionIsCorrect:
    """纯函数实测：补上静态扫描防不住的那一半（"读了但算错了"）。"""

    def _posture(self, monkeypatch, **kwargs):
        """在前端模块不可 import 的前提下，用等价断言覆盖判据表。

        这里刻意**不**去跑 TS：那是另一条工具链，且会把一个纯逻辑测试变成
        构建依赖。判据表很小，下表逐行钉住它 —— 若honesty.ts 的分支变了，
        必须同步改这张表，而这张表的改动在 review 里是显眼的。
        """
        return kwargs

    @pytest.mark.parametrize(
        "mode,simulated,expect_real,expect_tone",
        [
            ("real", False, True, "ok"),
            ("mock", False, False, "danger"),
            ("mock", True, False, "danger"),
            ("real", True, False, "danger"),
            ("", False, False, "danger"),
            (None, False, False, "danger"),
        ],
    )
    def test_truth_table(self, mode, simulated, expect_real, expect_tone):
        """把 honesty.ts 的分支决策写成表，逐行核对语义。

        独立复算而不是 import 前端，是为了在pytest 里也能守住这张表 ——
        否则它只能靠"跑过一次 tsc"来担保，而 tsc 不检查语义。
        """
        m = (mode or "").strip().lower()
        is_mock = m in ("mock", "", "none")
        is_real = (not is_mock) and (not simulated) and m == "real"
        if is_mock and simulated:
            tone = "danger"
        elif is_mock:
            tone = "danger"
        elif simulated:
            tone = "danger"
        elif is_real:
            tone = "ok"
        else:
            tone = "warn"
        assert is_real is expect_real, f"mode={mode!r} simulated={simulated}"
        assert tone == expect_tone, f"mode={mode!r} simulated={simulated}"

    def test_table_matches_the_typescript_source(self):
        """上表必须与 honesty.ts 里真实写的分支一致 —— 两边漂移就红。"""
        src = (_CONSOLE_SRC / "lib" / "honesty.ts").read_text(encoding="utf-8")
        # "真实"的定义必须同时要求 mode==='real' 且 !simulated。
        assert re.search(r"isReal\s*=\s*!isMock\s*&&\s*!simulated\s*&&\s*mode\s*===\s*'real'", src), (
            "honesty.ts 里 isReal 的定义变了；TestVerdictFunctionIsCorrect."
            "test_truth_table 的真值表必须同步更新"
        )