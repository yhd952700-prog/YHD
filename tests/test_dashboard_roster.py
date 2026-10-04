"""AI 员工名册端点测试 —— 核心是**防伪造**。

驾驶舱的「我AI员工」面板读 ``GET /v1/dashboard/roster``。这个测试文件要挡住的最坏
情况不是 500，而是**端点在没人注意的时候开始返回编造的员工**：一旦如此，界面会
看起来"更完整"，而实际上在撒谎。

因此这里的断言全部是「与权威来源逐条对账」而不是「数量看起来对」：

- 名册里的每一条 kernel / 能力层，都必须能在 ``capability-registry.yaml`` 里找到
  同 ``id`` 的条目，且 ``name`` / ``status`` 逐字相同；
- provider 列表必须与 ``ProviderFactory._providers`` 完全一致；
- 注册表不可用时必须**降级为空 + 报错**，而不是退回占位员工。

为什么"权威来源"必须由测试**独立**推导（这是本文件最容易被改坏的地方）
--------------------------------------------------------------------
本文件早期版本的 ``registry_block`` fixture 是这样写的::

    raw = yaml.safe_load(roster_module.REGISTRY_PATH.read_text(...))  # ← 自比自

也就是说 **fixture 和被测代码读同一个符号、走同一条路径解析**。这样的对账在结构上
**不可能**发现三类真实缺陷：

1. 代码读错了文件（路径推导写错、装错位置、漏 COPY 进镜像）；
2. 代码用错了字段（把``legacy_id`` 当 ``id`` 读）；
3. 代码漏读了某个段落（只解析 ``capabilities`` 却漏了 ``capability-layers``）。

这不是假想缺陷：本轮就发现 ``Dockerfile`` **从未**把 ``capability-registry.yaml``
拷进镜像（commit 95924123 修复），而当时**全部测试依然全绿** —— 恰恰因为它们自比自：
产品读到"文件不存在"和 fixture 读到"同一个不存在"，两边一致地对账通过了。

因此这里的 fixture 一律从**测试文件自身位置**推导权威路径
（:data:`_INDEPENDENT_REGISTRY_PATH`），绝不经过 ``roster_module.REGISTRY_PATH``
或 ``roster_module._REPO_ROOT``。路径解析本身则由
:class:`TestAuthorityIsIndependent` 单独锚定。
"""

from __future__ import annotations

import pathlib

import pytest
import yaml
from fastapi.testclient import TestClient

from src.gateway.main import get_app

# The data-building half of the roster lives in ``src.ai.roster_payload`` (fastapi
# free) since commit 20415d5c; ``src.gateway.roster`` is now only the thin
# ``@router.get`` wrapper. These tests assert against the DATA module, which is
# where ``REGISTRY_PATH`` / ``load_registry`` / ``provider_status`` /
# ``BAND_BY_PHASE`` actually live.
#
# Why this matters beyond a tidy import: until this was fixed the module failed
# to import at all, so pytest aborted collection for the WHOLE ``tests/`` tree
# (3592 tests never ran). A green-looking narrow run is not evidence that a
# collection error is harmless.
from src.ai import roster_payload as roster_module
from src.ai.roster_payload import (
    BAND_BY_PHASE,
    DOMAIN_BY_KERNEL,
    DOMAIN_LABELS,
    PROVIDER_KEY_ENVS,
    load_registry,
)

#: 权威注册表的**独立**路径推导 —— 见模块 docstring 的"为什么自比自是缺陷"。
#:
#: 只用 ``pathlib`` 从**本测试文件自身**位置向上走一级（``tests/`` → 仓库根），
#: 既不引用 ``roster_module.REGISTRY_PATH``，也不引用 ``roster_module._REPO_ROOT``，
#: 更不import 任何产品侧的路径解析代码。这样本文件对权威来源的认识就与产品代码
#: 的路径推导**在结构上无关**：任何一方单独改错路径，双方不再一致，测试立刻变红。
_INDEPENDENT_REGISTRY_PATH = pathlib.Path(__file__).resolve().parents[1] / (
    "capability-registry.yaml"
)


@pytest.fixture
def client(auth_headers):
    # `with` 才会跑 lifespan；与 tests/test_console_single_port.py 同一约定。
    # 名册端点在控制台鉴权闸门之后，因此带一个真实签发的令牌。
    with TestClient(get_app(), headers=auth_headers) as test_client:
        yield test_client


@pytest.fixture
def registry_block():
    """从**独立推导**的路径读出的权威 yaml 原始块，用于逐条对账。

    与产品代码的路径解析完全解耦：这里用 pytest 进程内的 ``yaml.safe_load``
    直接读 :data:`_INDEPENDENT_REGISTRY_PATH`。若这里改为读
    ``roster_module.REGISTRY_PATH``，本文件就退回到"自比自"，从而在结构上
    失去发现「代码读错文件 / 用错字段 / 漏读段落」的能力 —— 模块 docstring
    记录了这条纪律被违反时已经付出过的实际代价。
    """
    raw = yaml.safe_load(_INDEPENDENT_REGISTRY_PATH.read_text(encoding="utf-8"))
    return raw["capability-registry"]


def _get_roster(client):
    response = client.get("/v1/dashboard/roster")
    assert response.status_code == 200
    return response.json()


class TestRosterIsRouted:
    def test_endpoint_exists_and_is_json(self, client):
        response = client.get("/v1/dashboard/roster")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")

    def test_source_names_the_authority(self, client):
        # 界面要能告诉用户"这份名册出自哪个文件"，否则无法核对。
        #
        # 期望值取自**独立推导**的权威文件名，而不是 ``REGISTRY_PATH.name``：
        # 后者只会证明"``source`` 和产品自己算出的文件名一致"，无法发现产品
        # 整体切换到了另一个文件。
        body = _get_roster(client)
        assert body["source"] == _INDEPENDENT_REGISTRY_PATH.name
        assert body["available"] is True


class TestEveryEmployeeIsTraceable:
    """名册不是自说自话：每条都必须能回到注册表。"""

    def test_kernel_count_matches_the_registry(self, client, registry_block):
        body = _get_roster(client)
        assert len(body["kernels"]) == len(registry_block["capabilities"])

    def test_layer_count_matches_the_registry(self, client, registry_block):
        body = _get_roster(client)
        assert len(body["layers"]) == len(registry_block["capability-layers"])

    def test_no_kernel_is_invented(self, client, registry_block):
        truth = {entry["id"]: entry for entry in registry_block["capabilities"]}
        items = _get_roster(client)["kernels"]
        # 非空前置：否则下面的循环在"名册为空"时**空转通过** —— 那正是把
        # REGISTRY_PATH 指向错误文件后的实际表现（实测该用例仍绿）。
        # 没有这条，"没有伪造任何 kernel"就能被"一条都没有"满足。
        assert items, "roster returned zero kernels; per-row reconciliation would vacuously pass"
        for item in items:
            assert item["id"] in truth, f"roster invented kernel {item['id']!r}"
            source = truth[item["id"]]
            assert item["name"] == source["name"]
            assert item["status"] == source["status"]
            assert item["kernel"] == source["kernel"]

    def test_no_layer_is_invented(self, client, registry_block):
        truth = {entry["id"]: entry for entry in registry_block["capability-layers"]}
        items = _get_roster(client)["layers"]
        # 同上：非空前置，防止空转通过。
        assert items, "roster returned zero layers; per-row reconciliation would vacuously pass"
        for item in items:
            assert item["id"] in truth, f"roster invented layer {item['id']!r}"
            source = truth[item["id"]]
            assert item["name"] == source["name"]
            assert item["module"] == source["module"]
            assert item["status"] == source["status"]

    def test_capability_registry_says_nothing_is_missing(self, client, registry_block):
        # 反向对账：注册表里的条目也必须一条不漏地出现在名册里。
        body = _get_roster(client)
        assert {i["id"] for i in body["kernels"]} == {
            entry["id"] for entry in registry_block["capabilities"]
        }
        assert {i["id"] for i in body["layers"]} == {
            entry["id"] for entry in registry_block["capability-layers"]
        }


class TestPresentationGroupingIsTotal:
    """分组是呈现层唯一的"加工"，必须覆盖全部条目且不留 `other` 兜底。"""

    def test_every_kernel_has_a_known_domain(self, client):
        for item in _get_roster(client)["kernels"]:
            assert item["domain"] in DOMAIN_LABELS, item
            assert item["domain_label"] == DOMAIN_LABELS[item["domain"]]

    def test_every_layer_has_a_known_band(self, client):
        for item in _get_roster(client)["layers"]:
            assert item["band"] in {"loop", "org", "world", "evo"}, item

    def test_grouping_tables_cover_all_fourteen_kernels(self):
        # 少写一个 kernel ⇒ 它会被静默塞进 "other" 域，界面上看起来"少了一位员工"。
        assert len(DOMAIN_BY_KERNEL) == 14

    def test_band_table_covers_phase_9_through_21(self):
        assert sorted(BAND_BY_PHASE) == list(range(9, 22))


class TestProviderFace:
    def test_providers_are_exactly_the_registered_classes(self, client):
        from src.ai.providers import ProviderFactory

        body = _get_roster(client)
        listed = {item["type"] for item in body["providers"]["items"]}
        assert listed == set(ProviderFactory._providers.keys())

    def test_mock_is_always_configured_and_active_by_default(self, client):
        body = _get_roster(client)["providers"]
        mock = next(i for i in body["items"] if i["type"] == "mock")
        # mock provider 无需密钥；未配置 AI_PROVIDER_TYPE 时它就是生效的那一个。
        assert mock["configured"] is True
        assert mock["required_env"] == []

    def test_missing_cloud_key_is_reported_as_not_configured(self, client, monkeypatch):
        # 诚实性关键：没有密钥就说没有，不能因为"类已实现"就报成可用。
        #
        # 注意 patch 的是 **来源模块** src.ai.providers 的 _provider_env：
        # roster.provider_status 是在函数体内 ``from ..ai.providers import ...``
        # 拿到的局部名，不是 roster 模块属性，patch roster 上同名名字无效。
        for name in ("DEEPSEEK_API_KEY", "AI_PROVIDER_KEY"):
            monkeypatch.delenv(name, raising=False)

        import src.ai.providers as providers_module

        monkeypatch.setattr(
            providers_module, "_provider_env", lambda key, default="": ""
        )
        body = roster_module.provider_status()
        deepseek = next(i for i in body["items"] if i["type"] == "deepseek")
        assert deepseek["registered"] is True
        assert deepseek["configured"] is False
        assert "DEEPSEEK_API_KEY" in deepseek["required_env"]

    def test_key_table_covers_every_registered_provider(self):
        from src.ai.providers import ProviderFactory

        assert set(PROVIDER_KEY_ENVS) >= set(ProviderFactory._providers.keys())


class TestDegradesHonestly:
    def test_missing_registry_reports_unavailable_not_a_fabricated_roster(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(roster_module, "_cache", None)
        monkeypatch.setattr(
            roster_module, "REGISTRY_PATH", tmp_path / "does-not-exist.yaml"
        )
        payload = load_registry()
        assert payload["available"] is False
        assert payload["kernels"] == []
        assert payload["layers"] == []
        assert payload["error"]

    def test_unparsable_registry_reports_error(self, monkeypatch, tmp_path):
        broken = tmp_path / "broken.yaml"
        broken.write_text("capability-registry: [this is: not a mapping\n", encoding="utf-8")
        monkeypatch.setattr(roster_module, "_cache", None)
        monkeypatch.setattr(roster_module, "REGISTRY_PATH", broken)
        payload = load_registry()
        assert payload["available"] is False
        assert payload["error"]
        assert payload["kernels"] == [] and payload["layers"] == []

    def test_totals_are_derived_not_hardcoded(self, client):
        body = _get_roster(client)
        assert body["totals"]["kernels"] == len(body["kernels"])
        assert body["totals"]["layers"] == len(body["layers"])
        # Since 20415d5c the module count is reported as ``registry_entries``,
        # NOT as ``employees``. The old assertion here
        # (``employees == len(kernels) + len(layers)``) encoded the very
        # conflation that commit removed: it called a kernel a "member of
        # staff". ``employees`` must equal the number of REAL persisted
        # employees, which is the number that can be held accountable.
        assert body["totals"]["registry_entries"] == len(body["kernels"]) + len(body["layers"])
        assert body["totals"]["employees"] == len(body["real_employees"])

    def test_employees_are_not_modules(self, client):
        """A registry module is not an employee -- and the count must not imply it."""
        body = _get_roster(client)
        assert body["totals"]["employees"] == len(body["real_employees"])
        # Every counted employee must be traceable to the real store, i.e. have
        # an id -- a bare count of registry rows could never satisfy this.
        for employee in body["real_employees"]:
            assert employee.get("id")
            assert employee.get("name")

    def test_declared_totals_agree_with_the_registry_rows(self, client, registry_block):
        """The registry's own declared totals must match the rows it ships.

        ``declared_totals`` carries ``capabilities`` / ``layers`` (the registry's
        self-declared counts). If those drift from the actual row counts, the
        dashboard would quote a total nobody can reconcile against the file it
        names as its source. Note there is deliberately NO ``test_cases`` key
        here -- the payload never claimed one.
        """
        body = _get_roster(client)
        declared = body["declared_totals"]
        assert declared["capabilities"] == len(registry_block["capabilities"])
        assert declared["layers"] == len(registry_block["capability-layers"])
        # And the derived counts must agree with the declared ones.
        assert body["totals"]["kernels"] == declared["capabilities"]
        assert body["totals"]["layers"] == declared["layers"]


class TestRegistryFileIsPresent:
    def test_registry_path_points_inside_the_repo(self):
        path = pathlib.Path(roster_module.REGISTRY_PATH)
        assert path.name == "capability-registry.yaml"
        assert path.parent == roster_module._REPO_ROOT


class TestAuthorityIsIndependent:
    """哨兵：证明"权威来源"这件事本身没被悄悄改掉。

    这一类里没有 ``client`` —— 它们刻意只碰数据层与常量，因为它们要回答的是
    "产品声明的来源是不是那个权威文件"，而不是"端点在运行期返回了什么"。
    """

    def test_product_registry_path_is_the_authoritative_file(self):
        """产品解析出的 ``REGISTRY_PATH`` 必须等于测试独立推导出的路径。

        这是 ``Dockerfile`` 那类缺陷的哨兵（commit 95924123：镜像从未把
        ``capability-registry.yaml`` 拷进去，而当时测试全绿）。若有人改动产品
        侧的路径推导 —— 指向别的目录、别的文件名、或依赖某个 ``_REPO_ROOT``
        推导假设 —— 这条立刻红，而既有那些"与产品同源"的fixture 用例仍然绿。
        """
        assert pathlib.Path(roster_module.REGISTRY_PATH) == _INDEPENDENT_REGISTRY_PATH

    def test_independent_registry_file_is_readable(self):
        """反向：独立路径本身必须真的存在且含权威段落。

        没有这条，上面那条可能因为"两边同时指向同一个不存在的路径"而假绿；
        也没有 ``registry_block`` 里那个 ``KeyError`` 能提供更清晰的诊断。
        """
        assert _INDEPENDENT_REGISTRY_PATH.is_file(), (
            f"authority registry missing at {_INDEPENDENT_REGISTRY_PATH}"
        )
        raw = yaml.safe_load(_INDEPENDENT_REGISTRY_PATH.read_text(encoding="utf-8"))
        block = raw["capability-registry"]
        assert block["capabilities"] and block["capability-layers"]

    def test_production_posture_is_independent_of_the_roster_source(
        self, client, monkeypatch
    ):
        """产品姿态与名册来源是两个**本应独立**的事实，谁都不能被改成另一个的派生。

        ``src.security.posture.is_production()`` 读的是 ``LIUHAO_ENV`` /
        ``ENVIRONMENT`` / ``APP_ENV``，与 ``REGISTRY_PATH`` 没有任何依赖关系 ——
        它是本仓库现成的独立参照点。在 ``ENVIRONMENT=production`` 下：

        - 姿态必须判定为生产（证明这个参照点确实能独立解析出production）；
        - 名册的 ``source`` 必须**仍然**是权威注册表（证明名册没有反过来
          依赖/派生于姿态，也不会因为处于生产姿态就切到别的数据源）。

        任何"把名册数据源挂到姿态判断上"或"把姿态挂到名册数据源上"的改动都会红。
        """
        from src.security.posture import is_production

        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.delenv("LIUHAO_ENV", raising=False)
        monkeypatch.delenv("APP_ENV", raising=False)

        # 参照点本身：与环境变量、与 REGISTRY_PATH 无关。
        assert is_production() is True

        body = _get_roster(client)
        assert body["source"] == _INDEPENDENT_REGISTRY_PATH.name
        assert body["available"] is True
        # 姿态可变，但注册表读取的事实不受姿态影响。
        assert len(body["kernels"]) == 14
        assert len(body["layers"]) == 14
