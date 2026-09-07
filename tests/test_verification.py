"""Phase 18 Verification & Experience Engine tests (MASTER-SPEC S78-79).

Covers:
  * S78 four-state verdict mapping (success/partial/fail/unknown boundary)
  * criteria match-ratio correctness
  * S79 ExperienceEngine store -> retrieve roundtrip against the REAL memory
    kernel (a fresh MemoryKernel instance is injected for isolation, but it is
    the genuine kernel implementation, not an in-memory fake)
  * owner/tags persistence
  * tag-filtered retrieval
"""
from src.kernels.memory import MemoryKernel, MemoryScope

from src.ai.verification import (
    Verdict,
    VerificationEngine,
    ExperienceEngine,
    ExperienceEntry,
)


# ---------------------------------------------------------------------------
# S78 VerificationEngine
# ---------------------------------------------------------------------------
class TestVerdictEnum:
    def test_four_states(self):
        assert Verdict.VERIFIED.value == "verified"
        assert Verdict.PARTIAL.value == "partial"
        assert Verdict.FAILED.value == "failed"
        assert Verdict.UNKNOWN.value == "unknown"
        assert len(list(Verdict)) == 4


class TestVerificationEngineNoCriteria:
    def test_success_result_verified(self):
        out = VerificationEngine().verify({"success": True})
        assert out["verdict"] == Verdict.VERIFIED
        assert out["score"] == 1.0

    def test_failed_result_failed(self):
        out = VerificationEngine().verify({"success": False, "error": "boom"})
        assert out["verdict"] == Verdict.FAILED
        assert out["score"] == 0.0

    def test_ambiguous_result_unknown(self):
        # No criteria AND no clear success signal -> cannot determine.
        out = VerificationEngine().verify({"output": {"note": "done"}})
        assert out["verdict"] == Verdict.UNKNOWN
        assert out["score"] == 0.0

    def test_explicit_failure_overrides_no_criteria(self):
        out = VerificationEngine().verify({"success": False})
        assert out["verdict"] == Verdict.FAILED


class TestVerificationEngineWithCriteria:
    def test_full_match_verified(self):
        result = {"success": True, "output": {"a": 1, "b": 2}}
        out = VerificationEngine().verify(result, criteria={"a": 1, "b": 2})
        assert out["verdict"] == Verdict.VERIFIED
        assert out["score"] == 1.0

    def test_partial_match_partial(self):
        # 8/10 matched -> 0.8 -> PARTIAL
        criteria = {f"k{i}": i for i in range(10)}
        output = {f"k{i}": i for i in range(8)}
        out = VerificationEngine().verify({"success": True, "output": output}, criteria=criteria)
        assert out["verdict"] == Verdict.PARTIAL
        assert 0.7 <= out["score"] < 1.0

    def test_low_match_failed(self):
        # 5/10 matched -> 0.5 -> FAILED
        criteria = {f"k{i}": i for i in range(10)}
        output = {f"k{i}": i for i in range(5)}
        out = VerificationEngine().verify({"success": True, "output": output}, criteria=criteria)
        assert out["verdict"] == Verdict.FAILED
        assert out["score"] < 0.7

    def test_explicit_failure_with_criteria_is_failed(self):
        out = VerificationEngine().verify(
            {"success": False}, criteria={"a": 1}
        )
        assert out["verdict"] == Verdict.FAILED

    def test_reasons_explain_match(self):
        result = {"success": True, "output": {"a": 1, "b": 99}}
        out = VerificationEngine().verify(result, criteria={"a": 1, "b": 2})
        assert any("criteria matched" in r for r in out["reasons"])
        # The mismatching key is called out.
        assert any("b:" in r and "mismatch" in r for r in out["reasons"])


# ---------------------------------------------------------------------------
# S79 ExperienceEngine (real memory kernel)
# ---------------------------------------------------------------------------
def _fresh_engine():
    """Inject a fresh, isolated MemoryKernel so tests don't pollute state."""
    return ExperienceEngine(memory_kernel=MemoryKernel())


class TestExperienceEntry:
    def test_payload_roundtrip(self):
        e = ExperienceEntry(
            id="e1",
            summary="did a thing",
            verdict=Verdict.VERIFIED.value,
            context={"goal": "x"},
            tags=["alpha"],
            owner="alice",
        )
        restored = ExperienceEntry.from_payload(e.to_payload())
        assert restored.id == e.id
        assert restored.summary == e.summary
        assert restored.verdict == e.verdict
        assert restored.context == e.context
        assert restored.tags == e.tags
        assert restored.owner == "alice"


class TestExperienceStoreRetrieve:
    def test_store_then_retrieve_roundtrip(self):
        engine = _fresh_engine()
        verdict = {"verdict": Verdict.VERIFIED.value, "score": 1.0, "reasons": []}
        entry = engine.extract(verdict, {"summary": "task ok", "tags": ["alpha"], "owner": "alice"})
        assert engine.store(entry) is True

        got = engine.retrieve()
        assert len(got) == 1
        assert got[0].id == entry.id
        assert got[0].summary == "task ok"
        assert got[0].verdict == Verdict.VERIFIED.value

    def test_experience_carries_owner_and_tags(self):
        engine = _fresh_engine()
        verdict = {"verdict": Verdict.PARTIAL.value, "score": 0.8, "reasons": []}
        entry = engine.extract(verdict, {"summary": "half done", "tags": ["beta"], "owner": "bob"})
        engine.store(entry)

        got = engine.retrieve()
        assert len(got) == 1
        stored = got[0]
        # owner must be persisted (both in payload and as an owner: tag).
        assert stored.owner == "bob"
        assert "beta" in stored.tags
        assert "owner:bob" in stored.tags
        assert "experience" in stored.tags

    def test_retrieve_filters_by_tag(self):
        engine = _fresh_engine()
        a = engine.extract(
            {"verdict": Verdict.VERIFIED.value}, {"summary": "A", "tags": ["alpha"], "owner": "alice"}
        )
        b = engine.extract(
            {"verdict": Verdict.FAILED.value}, {"summary": "B", "tags": ["beta"], "owner": "bob"}
        )
        engine.store(a)
        engine.store(b)

        only_alpha = engine.retrieve(tags=["alpha"])
        assert len(only_alpha) == 1
        assert only_alpha[0].id == a.id

        only_beta = engine.retrieve(tags=["beta"])
        assert len(only_beta) == 1
        assert only_beta[0].id == b.id

    def test_retrieve_by_owner_tag(self):
        engine = _fresh_engine()
        a = engine.extract(
            {"verdict": Verdict.VERIFIED.value}, {"summary": "A", "tags": [], "owner": "alice"}
        )
        b = engine.extract(
            {"verdict": Verdict.VERIFIED.value}, {"summary": "B", "tags": [], "owner": "bob"}
        )
        engine.store(a)
        engine.store(b)

        alice_only = engine.retrieve(tags=["owner:alice"])
        assert len(alice_only) == 1
        assert alice_only[0].owner == "alice"

    def test_empty_retrieve_when_nothing_stored(self):
        engine = _fresh_engine()
        assert engine.retrieve() == []
