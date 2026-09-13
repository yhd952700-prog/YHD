"""AI 员工名册端点测试 —— 核心是**防伪造**。

驾驶舱的「我AI员工」面板读 ``GET /v1/dashboard/roster``。这个测试文件要挡住的最坏
情况不是 500，而是**端点在没人注意的时候开始返回编造的员工**：一旦如此，界面会
看起来"更完整"，而实际上在撒谎。

因此这里的断言全部是「与权威来源逐条对账」而不是「数量看起来对」：

- 名册里的每一条 kernel / 能力层，都必须能在 ``capability-registry.yaml`` 里找到
  同 ``id`` 的条目，且 ``name`` / ``status`` 逐字相同；
- provider 列表必须与 ``ProviderFactory._providers`` 完全一致；
- 注册表不可用时必须**降级为空 + 报错**，而不是退回占位员工。
"""

from __future__ import annotations

import pathlib

import pytest
import yaml
from fastapi.testclient import TestClient

from src.gateway.main import get_app
from src.gateway import roster as roster_module
from src.gateway.roster import (
    BAND_BY_PHASE,
    DOMAIN_BY_KERNEL,
    DOMAIN_LABELS,
    PROVIDER_KEY_ENVS,
    load_registry,
)


@pytest.fixture
def client():
    # `with` 才会跑 lifespan；与 tests/test_console_single_port.py 同一约定。
    with TestClient(get_app()) as test_client:
        yield test_client


@pytest.fixture
def registry_block():
    """直接从权威 yaml 读出的原始块，用于逐条对账。"""
    raw = yaml.safe_load(roster_module.REGISTRY_PATH.read_text(encoding="utf-8"))
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
        body = _get_roster(client)
        assert body["source"] == roster_module.REGISTRY_PATH.name
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
        for item in _get_roster(client)["kernels"]:
            assert item["id"] in truth, f"roster invented kernel {item['id']!r}"
            source = truth[item["id"]]
            assert item["name"] == source["name"]
            assert item["status"] == source["status"]
            assert item["kernel"] == source["kernel"]

    def test_no_layer_is_invented(self, client, registry_block):
        truth = {entry["id"]: entry for entry in registry_block["capability-layers"]}
        for item in _get_roster(client)["layers"]:
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
        assert body["totals"]["employees"] == len(body["kernels"]) + len(body["layers"])

    def test_declared_tests_are_summed_from_the_registry(self, client, registry_block):
        body = _get_roster(client)
        expected = sum(int(entry.get("test_count") or 0) for entry in registry_block["capability-layers"])
        assert body["totals"]["declared_test_cases"] == expected


class TestRegistryFileIsPresent:
    def test_registry_path_points_inside_the_repo(self):
        path = pathlib.Path(roster_module.REGISTRY_PATH)
        assert path.name == "capability-registry.yaml"
        assert path.parent == roster_module._REPO_ROOT
