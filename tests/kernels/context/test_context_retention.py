"""Guardrail tests for Context Kernel retention.

These tests exist to PREVENT the regression where the attention/compression
block discarded every present input type under the default (and most) mechanisms,
making the kernel a silent runtime no-op.

The fix makes retention RELATIVE (normalized weights, keep above mean, never
empty) instead of an absolute `weight > 0.5` threshold that is not scale-invariant.

Construction pattern mirrors tests/kernels/context/test_context.py.
"""
import pytest

from src.kernels.context import (
    AttentionMechanism,
    ContextInput,
    ContextInputType,
    ContextKernel,
    ContextCompression,
)


def inputs(types):
    """Build a list of ContextInput, one per (type, count) entry."""
    out = []
    for t, count in types:
        out.extend(ContextInput(type=t, source="s") for _ in range(count))
    return out


# Representative, realistic input mix: GOAL is the dominant stream.
REPRESENTATIVE = [
    (ContextInputType.GOAL, 6),
    (ContextInputType.TASK, 3),
    (ContextInputType.MEMORY, 2),
    (ContextInputType.TRUST, 1),
]


# =====================================================================
# Core guardrail: retention is never empty (the regression we fixed)
# =====================================================================

class TestRetentionNeverEmpty:
    @pytest.mark.parametrize("mechanism", [
        AttentionMechanism.UNIFORM,
        AttentionMechanism.IMPORTANCE,
        AttentionMechanism.RECENCY,
        AttentionMechanism.HYBRID,
    ])
    def test_retained_nonempty_for_all_mechanisms(self, mechanism):
        k = ContextKernel(mechanism=mechanism)
        res = k.compress(inputs(REPRESENTATIVE))
        assert res.retained_keys, (
            f"{mechanism.value}: retained must never be empty for non-empty input"
        )

    def test_single_present_type_is_retained(self):
        # The highest-weight fallback must keep at least one type.
        k = ContextKernel(mechanism=AttentionMechanism.UNIFORM)
        res = k.compress(inputs([(ContextInputType.GOAL, 1)]))
        assert res.retained_keys == ["goal"]
        assert res.compression_ratio > 0

    def test_empty_input_still_yields_empty_retained(self):
        # No input => nothing to retain; this is correct, not the bug.
        res = ContextKernel().compress([])
        assert res.retained_keys == []
        assert res.compression_ratio == 0.0


# =====================================================================
# Return-shape contract preserved (consumed by lcore.py / gateway)
# =====================================================================

class TestReturnShape:
    def test_four_keys_present_with_correct_types(self):
        res = ContextKernel(mechanism=AttentionMechanism.HYBRID).compress(inputs(REPRESENTATIVE))
        assert isinstance(res, ContextCompression)
        assert isinstance(res.retained_keys, list)
        assert isinstance(res.discarded_keys, list)
        assert isinstance(res.attention_weights, dict)
        assert isinstance(res.compressed, dict)
        # union of retained + discarded covers exactly the present types
        present = {t.value for t, _ in REPRESENTATIVE}
        assert set(res.retained_keys) | set(res.discarded_keys) == present
        assert set(res.retained_keys) & set(res.discarded_keys) == set()

    def test_normalized_weights_sum_to_one(self):
        res = ContextKernel(mechanism=AttentionMechanism.IMPORTANCE).compress(inputs(REPRESENTATIVE))
        assert abs(sum(res.attention_weights.values()) - 1.0) < 1e-9


# =====================================================================
# Monotonicity: a type with 4x the count must outrank the other
# =====================================================================

class TestMonotonicity:
    def test_importance_prefers_higher_count(self):
        # A (GOAL) count = 4x B (TASK)
        k = ContextKernel(mechanism=AttentionMechanism.IMPORTANCE)
        res = k.compress(inputs([(ContextInputType.GOAL, 8), (ContextInputType.TASK, 2)]))
        assert "goal" in res.retained_keys
        assert "task" not in res.retained_keys
        assert res.attention_weights["goal"] > res.attention_weights["task"]

    def test_hybrid_prefers_higher_count(self):
        k = ContextKernel(mechanism=AttentionMechanism.HYBRID)
        res = k.compress(inputs([(ContextInputType.GOAL, 8), (ContextInputType.TASK, 2)]))
        assert "goal" in res.retained_keys
        assert "task" not in res.retained_keys
        assert res.attention_weights["goal"] > res.attention_weights["task"]


# =====================================================================
# Deterministic tie handling (UNIFORM / RECENCY: all weights equal)
# =====================================================================

class TestTieHandling:
    def test_uniform_weights_equal_and_all_retained(self):
        k = ContextKernel(mechanism=AttentionMechanism.UNIFORM)
        res = k.compress(inputs(REPRESENTATIVE))
        weights = list(res.attention_weights.values())
        assert all(w == weights[0] for w in weights)  # all equal
        present = {t.value for t, _ in REPRESENTATIVE}
        assert set(res.retained_keys) == present       # all retained, none discarded
        assert res.discarded_keys == []

    def test_recency_all_equal_and_deterministic(self):
        # All weights tied at 1.0 -> every present type retained, deterministically.
        order_a = [(ContextInputType.GOAL, 1), (ContextInputType.TASK, 1),
                   (ContextInputType.MEMORY, 1), (ContextInputType.TRUST, 1)]
        order_b = [(ContextInputType.TRUST, 1), (ContextInputType.MEMORY, 1),
                   (ContextInputType.TASK, 1), (ContextInputType.GOAL, 1)]
        ra = ContextKernel(mechanism=AttentionMechanism.RECENCY).compress(inputs(order_a))
        rb = ContextKernel(mechanism=AttentionMechanism.RECENCY).compress(inputs(order_b))
        present = {t.value for t, _ in REPRESENTATIVE}
        assert set(ra.retained_keys) == present
        assert set(rb.retained_keys) == present
        # order-independent / deterministic: same retained set regardless of input order
        assert set(ra.retained_keys) == set(rb.retained_keys)
        # and the normalized weights are all equal (no hidden random tie-break)
        assert len(set(ra.attention_weights.values())) == 1


# =====================================================================
# Anti-fake guardrail: prove the "retained non-empty" assertion is real.
#
# We reconstruct the PRE-FIX absolute-threshold selection (weight > 0.5) using
# the original weight formulas, and show it WOULD yield an empty retained set for
# the same representative input. That is exactly the regression the guardrail above
# now catches: the old code fails the assertion, the fixed code passes it.
# =====================================================================

def _prefix_absolute_threshold_retained(mechanism_value, counts, n_inputs=12):
    """Verbatim pre-fix branch: absolute 0.5 threshold, original weight formulas."""
    weights = {}
    for t, c in counts.items():
        if c <= 0:
            continue
        if mechanism_value == "uniform":
            w = 1.0 / n_inputs
        elif mechanism_value == "importance":
            w = c / n_inputs
        elif mechanism_value == "recency":
            w = 1.0
        elif mechanism_value == "hybrid":
            w = (c + 1) / (n_inputs + n_inputs)
        else:  # pragma: no cover
            w = 1.0
        weights[t] = w
    return [t for t, w in weights.items() if w > 0.5]


class TestGuardrailNotRubberStamp:
    def test_prefix_absolute_threshold_would_discard_everything(self):
        counts = {t.value: c for t, c in REPRESENTATIVE}
        # Under the OLD absolute 0.5 rule the default/most mechanisms kept NOTHING.
        for mech in ["uniform", "importance", "hybrid"]:
            old = _prefix_absolute_threshold_retained(mech, counts)
            assert old == [], (
                f"pre-fix {mech} must have produced an empty retained set "
                f"(this is the bug the guardrail guards against); got {old}"
            )
        # RECENCY was the only one that survived pre-fix; assert the guardrail
        # would still flag a regression if RECENCY also collapsed:
        # (sanity: pre-fix recency keeps everything, so it is NOT empty)
        assert _prefix_absolute_threshold_retained("recency", counts) != []

    def test_fixed_code_is_nonempty_where_prefix_was_empty(self):
        # Symmetric proof: the real, fixed kernel returns non-empty for the same
        # inputs that the pre-fix rule emptied.
        for mech in [AttentionMechanism.UNIFORM, AttentionMechanism.IMPORTANCE,
                     AttentionMechanism.HYBRID]:
            res = ContextKernel(mechanism=mech).compress(inputs(REPRESENTATIVE))
            assert res.retained_keys, f"fixed {mech.value} must retain something"
