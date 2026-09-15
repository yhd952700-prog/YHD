"""Context Kernel unit tests.

Covers: 12-input model, scope validation, typed input counting,
attention-mechanism-driven compression (which keys are retained vs
discarded, and the resulting compression ratio), and the full pipeline.
"""
import pytest

from src.kernels.context import (
    AttentionMechanism,
    ContextInput,
    ContextInputType,
    ContextKernel,
    ContextCompression,
    create_context_kernel,
)


def inputs(types):
    """Build a list of ContextInput, one per (type, count) entry."""
    out = []
    for t, count in types:
        out.extend(ContextInput(type=t, source="s") for _ in range(count))
    return out


# =====================================================================
# Construction & validation
# =====================================================================

class TestConstruction:
    def test_n_inputs_is_twelve(self):
        assert ContextKernel.N_INPUTS == 12

    def test_default_mechanism_is_hybrid(self):
        assert ContextKernel().mechanism is AttentionMechanism.HYBRID

    def test_set_scope_valid(self):
        k = ContextKernel()
        k.set_scope("L5")
        assert k.scope == "L5"

    def test_set_scope_invalid_raises(self):
        k = ContextKernel()
        with pytest.raises(ValueError):
            k.set_scope("L9")


# =====================================================================
# Input counting
# =====================================================================

class TestInputCounting:
    def test_add_input_unknown_type_raises(self):
        k = ContextKernel()
        bad = ContextInput(type="not_a_type", source="s")  # type: ignore
        with pytest.raises(ValueError):
            k.add_input(bad)

    def test_add_input_increments_count(self):
        k = ContextKernel()
        k.add_input(ContextInput(type=ContextInputType.GOAL, source="s"))
        k.add_input(ContextInput(type=ContextInputType.GOAL, source="s"))
        assert k._input_counts[ContextInputType.GOAL] == 2


# =====================================================================
# Compression per mechanism
# =====================================================================

class TestCompression:
    def test_compress_empty(self):
        res = ContextKernel().compress([])
        assert isinstance(res, ContextCompression)
        assert res.retained_keys == []
        assert res.compression_ratio == 0.0

    def test_uniform_retains_all_present(self):
        # Retention is RELATIVE now (normalized weights vs. their mean), not the
        # old absolute `weight > 0.5` rule. Under UNIFORM every present type has
        # the same normalized weight (= the mean), so nothing strictly beats the
        # mean; the kernel's "never emit an empty retained set" fallback keeps
        # every tied max-weight type -> all present types are retained.
        #
        # Pre-fix, the absolute rule (weight = 1/12 < 0.5) retained NOTHING for
        # the real N_INPUTS=12 -- a silent runtime no-op. See
        # tests/kernels/context/test_context_retention.py for the guardrail.
        k = ContextKernel(mechanism=AttentionMechanism.UNIFORM)
        data = inputs([(ContextInputType.GOAL, 3), (ContextInputType.TASK, 2)])
        res = k.compress(data)
        assert set(res.retained_keys) == {"goal", "task"}
        assert res.discarded_keys == []
        assert res.compression_ratio == 2 / 12

    def test_recency_retains_all_present(self):
        # RECENCY gives every present type an equal (tied) weight, so the same
        # tie fallback as UNIFORM keeps them all.
        k = ContextKernel(mechanism=AttentionMechanism.RECENCY)
        data = inputs([(ContextInputType.GOAL, 1), (ContextInputType.TASK, 1)])
        res = k.compress(data)
        assert set(res.retained_keys) == {"goal", "task"}
        assert res.compression_ratio == 2 / 12

    def test_importance_retains_only_above_average_count(self):
        # IMPORTANCE weight ∝ count; normalized, GOAL (8) sits above the mean
        # (0.5) while TASK (3) sits below it -> only the above-average type is
        # retained.
        k = ContextKernel(mechanism=AttentionMechanism.IMPORTANCE)
        data = inputs([
            (ContextInputType.GOAL, 8),     # 8/11 > 1/2 -> retained
            (ContextInputType.TASK, 3),     # 3/11 < 1/2 -> discarded
        ])
        res = k.compress(data)
        assert "goal" in res.retained_keys
        assert "task" in res.discarded_keys
        assert res.compression_ratio == 1 / 12

    def test_hybrid_retains_the_higher_count(self):
        # HYBRID weight ∝ count + 1; the higher-count type wins outright.
        k = ContextKernel(mechanism=AttentionMechanism.HYBRID)
        data = inputs([
            (ContextInputType.GOAL, 12),    # retained
            (ContextInputType.TASK, 5),     # discarded
        ])
        res = k.compress(data)
        assert "goal" in res.retained_keys
        assert "task" in res.discarded_keys

    def test_compressed_bundle_carries_scope_and_mechanism(self):
        k = ContextKernel(mechanism=AttentionMechanism.RECENCY, scope="L4")
        data = inputs([(ContextInputType.GOAL, 1)])
        res = k.compress(data)
        assert res.scope == "L4"
        assert res.compressed["goal"]["mechanism"] == "recency"
        assert res.compressed["goal"]["scope"] == "L4"


# =====================================================================
# Full pipeline
# =====================================================================

class TestPipeline:
    def test_process_full_pipeline(self):
        k = ContextKernel(mechanism=AttentionMechanism.RECENCY)
        data = inputs([(ContextInputType.GOAL, 1), (ContextInputType.MEMORY, 1)])
        res = k.process(data)
        assert set(res.retained_keys) == {"goal", "memory"}

    def test_create_context_kernel_factory(self):
        k = create_context_kernel(mechanism=AttentionMechanism.IMPORTANCE, scope="L2")
        assert k.mechanism is AttentionMechanism.IMPORTANCE
        assert k.scope == "L2"


# =====================================================================
# Bundle content: payloads + correlation traceability
# =====================================================================

class TestBundleCarriesPayloadAndCorrelation:
    """The bundle is meant to be model-ready and traceable; both used to be lost.

    Before the fix ``compressed[type]`` held only ``count``/``scope``/
    ``mechanism`` -- ``inp.data`` was never read -- and ``ContextCompression``
    had no place to put ``inp.correlation_id``, so traceability ended at the
    input boundary.
    """

    def test_compressed_entry_carries_input_payload(self):
        k = ContextKernel(mechanism=AttentionMechanism.UNIFORM)
        data = [
            ContextInput(type=ContextInputType.GOAL, source="s", data={"goal": "ship"}),
            ContextInput(type=ContextInputType.TASK, source="s", data="t1"),
        ]
        res = k.compress(data)
        assert res.compressed["goal"]["data"] == [{"goal": "ship"}]
        assert res.compressed["task"]["data"] == ["t1"]

    def test_all_payloads_of_a_type_are_kept_in_order(self):
        k = ContextKernel(mechanism=AttentionMechanism.UNIFORM)
        data = [
            ContextInput(type=ContextInputType.EVENT, source="s", data="e1"),
            ContextInput(type=ContextInputType.EVENT, source="s", data="e2"),
        ]
        res = k.compress(data)
        assert res.compressed["event"]["data"] == ["e1", "e2"]
        assert res.compressed["event"]["count"] == 2

    def test_correlation_ids_propagate_deduped_in_input_order(self):
        k = ContextKernel(mechanism=AttentionMechanism.UNIFORM)
        data = [
            ContextInput(type=ContextInputType.GOAL, source="s", correlation_id="c1"),
            ContextInput(type=ContextInputType.TASK, source="s", correlation_id="c1"),
            ContextInput(type=ContextInputType.MEMORY, source="s", correlation_id="c2"),
            ContextInput(type=ContextInputType.TRUST, source="s", correlation_id=None),
        ]
        res = k.compress(data)
        assert res.correlation_ids == ["c1", "c2"]

    def test_empty_compress_has_no_correlation_ids(self):
        assert ContextKernel().compress([]).correlation_ids == []
