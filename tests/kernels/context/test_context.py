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

    def test_uniform_discards_everything(self):
        # UNIFORM weight = 1/12 < 0.5 -> nothing retained
        k = ContextKernel(mechanism=AttentionMechanism.UNIFORM)
        data = inputs([(ContextInputType.GOAL, 3), (ContextInputType.TASK, 2)])
        res = k.compress(data)
        assert res.retained_keys == []
        assert set(res.discarded_keys) == {"goal", "task"}
        assert res.compression_ratio == 0.0

    def test_recency_retains_all_present(self):
        # RECENCY weight = 1.0 > 0.5 -> every present type retained
        k = ContextKernel(mechanism=AttentionMechanism.RECENCY)
        data = inputs([(ContextInputType.GOAL, 1), (ContextInputType.TASK, 1)])
        res = k.compress(data)
        assert set(res.retained_keys) == {"goal", "task"}
        assert res.compression_ratio == 2 / 12

    def test_importance_retains_only_high_count(self):
        # IMPORTANCE weight = count/12 > 0.5 only when count >= 7
        k = ContextKernel(mechanism=AttentionMechanism.IMPORTANCE)
        data = inputs([
            (ContextInputType.GOAL, 8),     # 8/12 retained
            (ContextInputType.TASK, 3),     # 3/12 discarded
        ])
        res = k.compress(data)
        assert "goal" in res.retained_keys
        assert "task" in res.discarded_keys
        assert res.compression_ratio == 1 / 12

    def test_hybrid_retains_when_count_ge_twelve(self):
        # HYBRID weight = (count+1)/24 > 0.5 only when count >= 12
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
