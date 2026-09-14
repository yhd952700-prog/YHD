"""Model pricing is configuration, and it must be able to reach production.

The cost guard (``quota_enforcement``) is useless without a price. Prices used
to be stuck in two ways at once:

* the registry path was hard-coded to a Windows absolute path, so the Linux
  release bundle could never find it;
* even when set, the registry lives under ``data/``, which is gitignored --
  so any price written there was never committed and never shipped.

This module pins the fix: paths are overridable (``$LIUHAO_MODEL_REGISTRY_FILE``
/ ``$LIUHAO_MODEL_PRICING_FILE``) and default to the **repository root**, and
prices come from ``config/model_pricing.json`` -- version-controlled, and copied
into the release bundle because ``config/`` is in ``SOURCE_DIRS``.

Two honesty assertions run through everything here:

* an unpriced model yields **no** ``estimated_cost`` key (never a fabricated 0);
* a model explicitly priced at 0 yields ``0.0`` -- "free" and "unknown" are
  different facts and must stay distinguishable.
"""
from __future__ import annotations

import json

import pytest

from src.ai.agent_factory import economy_inputs
from src.models import registry as registry_module
from src.models.registry import (
    DEFAULT_MODEL_PRICING_FILE,
    DEFAULT_MODEL_REGISTRY_FILE,
    MODEL_PRICING_FILE_ENV,
    MODEL_REGISTRY_FILE_ENV,
    ModelRegistry,
    get_model_registry,
)

PRICED = "priced-model"
FREE = "free-model"


@pytest.fixture
def isolated_registry(tmp_path, monkeypatch):
    """A registry built from throwaway state + pricing files."""
    state = tmp_path / "model_registry.json"
    pricing = tmp_path / "model_pricing.json"
    monkeypatch.setenv(MODEL_REGISTRY_FILE_ENV, str(state))
    monkeypatch.setenv(MODEL_PRICING_FILE_ENV, str(pricing))
    registry_module._default_registry = None
    yield state, pricing
    registry_module._default_registry = None


def _write_prices(pricing_path, mapping):
    pricing_path.write_text(json.dumps(mapping), encoding="utf-8")


# --------------------------------------------------------------------------
# portable, overridable paths
# --------------------------------------------------------------------------


class TestPathsArePortable:
    def test_defaults_are_anchored_at_the_repository_root(self):
        # The old default was a hard-coded D:\... path that does not exist in
        # the Linux bundle. Both defaults must derive from the repo instead.
        root = registry_module._REPO_ROOT
        assert DEFAULT_MODEL_REGISTRY_FILE == root / "data" / "model_registry.json"
        assert DEFAULT_MODEL_PRICING_FILE == root / "config" / "model_pricing.json"

    def test_pricing_default_is_inside_config_so_the_bundle_ships_it(self):
        # data/ is gitignored; config/ is in build_cloud_bundle's SOURCE_DIRS.
        assert "config" in DEFAULT_MODEL_PRICING_FILE.parts
        assert "data" not in DEFAULT_MODEL_PRICING_FILE.parts

    def test_the_env_overrides_win(self, isolated_registry):
        state, _ = isolated_registry
        registry = ModelRegistry()
        assert registry.storage_path == state

    def test_env_override_is_read_by_the_singleton(self, isolated_registry):
        state, _ = isolated_registry
        assert get_model_registry().storage_path == state


# --------------------------------------------------------------------------
# pricing overlay
# --------------------------------------------------------------------------


class TestPricingIsApplied:
    def test_a_priced_model_supplies_a_cost(self, isolated_registry):
        _, pricing = isolated_registry
        _write_prices(pricing, {PRICED: 0.01})
        ModelRegistry()
        # 1000 in + 500 out = 1500 tokens -> 1.5 * 0.01
        assert economy_inputs("probe", PRICED, 1000, 500)["estimated_cost"] == pytest.approx(0.015)

    def test_pricing_works_without_registering_the_model(self, isolated_registry):
        """The whole point: pricing must not require a registered model.

        ``register_model`` forces ``model_id = "{name}-{version}"``, so a bare
        id like ``gpt-5.6-sol`` cannot be produced by it.
        """
        _, pricing = isolated_registry
        _write_prices(pricing, {"gpt-5.6-sol": 0.002})
        registry = ModelRegistry()
        assert registry.get_model("gpt-5.6-sol") is not None
        assert registry.get_model("gpt-5.6-sol").estimated_cost_per_1k == pytest.approx(0.002)

    def test_missing_pricing_file_is_not_an_error(self, isolated_registry):
        _, pricing = isolated_registry
        assert not pricing.exists()
        registry = ModelRegistry()  # must not raise
        assert registry.get_model(PRICED) is None

    def test_an_unpriced_model_is_never_called_free(self, isolated_registry):
        _, pricing = isolated_registry
        _write_prices(pricing, {PRICED: 0.01})
        ModelRegistry()
        kwargs = economy_inputs("probe", "some-other-model", 1000, 500)
        assert "estimated_cost" not in kwargs

    def test_a_zero_price_is_reported_as_zero_not_as_unknown(self, isolated_registry):
        """A local ollama model really is free -- that is a different fact."""
        _, pricing = isolated_registry
        _write_prices(pricing, {FREE: 0.0})
        ModelRegistry()
        assert economy_inputs("probe", FREE, 1000, 500)["estimated_cost"] == pytest.approx(0.0)

    def test_pricing_overrides_an_existing_entry(self, isolated_registry):
        state, pricing = isolated_registry
        _write_prices(pricing, {PRICED: 0.05})
        first = ModelRegistry()
        first.register_model(name=PRICED, version="1", model_type="llm")
        registered_id = f"{PRICED}-1"
        assert registered_id in first._models

        _write_prices(pricing, {registered_id: 0.09})
        second = ModelRegistry()
        assert second.get_model(registered_id).estimated_cost_per_1k == pytest.approx(0.09)


class TestBadPricingCannotTakeTheProcessDown:
    @pytest.mark.parametrize(
        "content",
        ["{not json", "[1, 2, 3]", '"a string"', ""],
        ids=["malformed", "not-an-object", "bare-string", "empty"],
    )
    def test_a_broken_file_is_skipped_not_fatal(self, isolated_registry, content):
        _, pricing = isolated_registry
        pricing.write_text(content, encoding="utf-8")
        registry = ModelRegistry()  # must not raise
        assert registry.get_model(PRICED) is None

    @pytest.mark.parametrize("bad", ["cheap", -1, True, None, [1]])
    def test_non_numeric_prices_are_skipped(self, isolated_registry, bad, caplog):
        _, pricing = isolated_registry
        _write_prices(pricing, {"bad-model": bad, PRICED: 0.01})
        with caplog.at_level("WARNING"):
            registry = ModelRegistry()
        assert registry.get_model("bad-model") is None
        assert registry.get_model(PRICED) is not None
        assert any("not a number" in r.message or "negative" in r.message for r in caplog.records)

    def test_underscore_keys_are_documentation_not_prices(self, isolated_registry, caplog):
        """A comment key must not spam a 'not a number' warning."""
        _, pricing = isolated_registry
        _write_prices(pricing, {"_comment": "how to use this file", PRICED: 0.01})
        with caplog.at_level("WARNING"):
            registry = ModelRegistry()
        assert registry.get_model("_comment") is None
        assert not [r for r in caplog.records if "_comment" in r.message]

    def test_a_malformed_file_still_leaves_a_usable_registry(self, isolated_registry):
        _, pricing = isolated_registry
        pricing.write_text("{not json", encoding="utf-8")
        registry = ModelRegistry()
        registry.register_model(name="still-works", version="1", model_type="llm")
        assert registry.get_model("still-works-1") is not None
